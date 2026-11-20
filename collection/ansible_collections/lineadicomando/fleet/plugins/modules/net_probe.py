# SPDX-License-Identifier: LGPL-2.1-or-later
# It runs on the nodes, with Python 3.6 or later: nothing newer is used.

DOCUMENTATION = r"""
module: net_probe
short_description: Probe addresses of the node's own network from the node
description:
  - Tries each address with an ICMP echo and a TCP connection to each port,
    then reads the node's neighbour table, which gives the MAC address of
    each host on the node's segment: what the controller cannot see beyond
    a router.
  - The same module exists for Windows, M(lineadicomando.fleet.win_net_probe),
    with the same parameters and the same results; the action of the same
    name as this module chooses between the two by the shell of the host.
  - It changes nothing on the node or on the hosts it probes. cockpit-fleet
    lets it probe only subnets it may scan, or the hosts of the inventory,
    and gives it the ports itself.
options:
  subnet:
    description: An IPv4 subnet, address/prefix, of at most 1024 addresses.
    type: str
  addresses:
    description: IPv4 addresses, instead of a subnet; at most 1024.
    type: list
    elements: str
  ports:
    description: TCP ports to try on each address; at most 32.
    type: list
    elements: int
    default: [22, 80, 135, 139, 443, 445, 3389, 5985, 5986]
  timeout:
    description: Seconds to wait for each answer, from 0.2 to 5.
    type: float
    default: 1.0
author:
  - Alessandro Gagliano
"""

EXAMPLES = r"""
- name: The hosts of the segment of pc101, seen from it
  lineadicomando.fleet.net_probe:
    subnet: 192.168.10.0/24
"""

RETURN = r"""
net_probe:
  description: What the node found.
  type: dict
  returned: always
  contains:
    node: {description: "The node's own interfaces: C(name), C(mac), C(addresses).", type: list}
    icmp: {description: Whether the node could send an ICMP echo., type: bool}
    hosts:
      description: "Each address that answered or is in the neighbour table: C(address),
        C(seen) (tcp, icmp, arp), C(ports) open, C(mac)."
      type: list
"""

import errno
import ipaddress
import json
import os
import secrets
import selectors
import socket
import struct
import threading
import time

from ansible.module_utils.basic import AnsibleModule

MAX_ADDRESSES = 1024
MAX_PORTS = 32
WORKERS = 32
MAX_SOCKETS = 512
# How long the kernel takes to give up on a neighbour it had seen before.
SETTLE = 9.0
ARP_TABLE = "/proc/net/arp"


def targets(subnet, addresses):
    if subnet:
        network = ipaddress.IPv4Network(u"%s" % subnet)
        if network.num_addresses > MAX_ADDRESSES:
            raise ValueError("more than %d addresses" % MAX_ADDRESSES)
        return [str(address) for address in network.hosts()] or [str(network.network_address)]
    found = []
    for address in addresses:
        found.append(str(ipaddress.IPv4Address(u"%s" % address)))
    if not found or len(found) > MAX_ADDRESSES:
        raise ValueError("give 1 to %d addresses" % MAX_ADDRESSES)
    return list(dict.fromkeys(found))


def connect_all(address, ports, timeout):
    """The ports open, and whether the host answered at all (a refusal is an answer)."""
    pending = {}
    open_ports = []
    answered = False
    selector = selectors.DefaultSelector()
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        code = sock.connect_ex((address, port))
        if code == 0:
            open_ports.append(port)
            answered = True
            sock.close()
        elif code in (errno.EINPROGRESS, errno.EAGAIN):
            pending[sock] = port
            selector.register(sock, selectors.EVENT_WRITE)
        else:
            answered = answered or code == errno.ECONNREFUSED
            sock.close()
    deadline = time.time() + timeout
    try:
        while pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            for key, _events in selector.select(remaining):
                sock = key.fileobj
                code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if code == 0:
                    open_ports.append(pending[sock])
                    answered = True
                elif code == errno.ECONNREFUSED:
                    answered = True
                selector.unregister(sock)
                sock.close()
                del pending[sock]
    finally:
        for sock in pending:
            sock.close()
        selector.close()
    return sorted(open_ports), answered


def icmp_socket_allowed():
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
    except (OSError, AttributeError):
        return False
    return True


def ping_socket(address, timeout):
    sequence = secrets.randbelow(65535) + 1
    packet = struct.pack("!BBHHH", 8, 0, 0, 0, sequence) + b"cockpit-fleet"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except OSError:
        return False
    try:
        sock.sendto(packet, (address, 0))
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            sock.settimeout(remaining)
            reply = sock.recvfrom(1024)[0]
            if len(reply) >= 8 and reply[0:1] == b"\x00" \
                    and struct.unpack("!H", reply[6:8])[0] == sequence:
                return True
    except (OSError, socket.timeout):
        return False
    finally:
        sock.close()


def read_arp(wanted):
    found = {}
    try:
        with open(ARP_TABLE) as table:
            lines = table.read().splitlines()[1:]
    except (IOError, OSError):
        return found
    for line in lines:
        fields = line.split()
        if len(fields) < 6 or fields[0] not in wanted:
            continue
        try:
            flags = int(fields[2], 16)
        except ValueError:
            continue
        mac = fields[3].lower()
        if flags & 0x2 and mac != "00:00:00:00:00:00":
            found[fields[0]] = mac
    return found


def interfaces(module):
    """The node's interfaces with an IPv4 address, from ip -j address."""
    ip = module.get_bin_path("ip")
    if not ip:
        return []
    rc, out, _err = module.run_command([ip, "-j", "-4", "address"])
    if rc != 0:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    found = []
    for link in data:
        if link.get("ifname") == "lo":
            continue
        addresses = ["%s/%s" % (item.get("local"), item.get("prefixlen"))
                     for item in link.get("addr_info", []) if item.get("local")]
        mac = ""
        try:
            with open("/sys/class/net/%s/address" % link.get("ifname")) as source:
                mac = source.read().strip().lower()
        except (IOError, OSError):
            pass
        if addresses:
            found.append({"name": link.get("ifname", ""), "mac": mac, "addresses": addresses})
    return found


def run(module):
    params = module.params
    if bool(params["subnet"]) == bool(params["addresses"]):
        module.fail_json(msg="give either subnet or addresses")
    try:
        addresses = targets(params["subnet"], params["addresses"] or [])
    except ValueError as error:
        module.fail_json(msg=str(error))
    ports = sorted(set(params["ports"]))
    if not 1 <= len(ports) <= MAX_PORTS or not all(0 < port < 65536 for port in ports):
        module.fail_json(msg="ports must be 1 to %d port numbers" % MAX_PORTS)
    timeout = params["timeout"]
    if not 0.2 <= timeout <= 5:
        module.fail_json(msg="timeout must be between 0.2 and 5 seconds")

    icmp_socket = icmp_socket_allowed()
    ping = None if icmp_socket else module.get_bin_path("ping")
    found = {}
    lock = threading.Lock()
    queue = list(addresses)

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                address = queue.pop()
            seen = []
            open_ports, answered = connect_all(address, ports, timeout)
            if answered:
                seen.append("tcp")
            if icmp_socket and ping_socket(address, timeout):
                seen.append("icmp")
            elif ping:
                rc = module.run_command([ping, "-c", "1", "-W", str(max(1, int(round(timeout)))),
                                         address])[0]
                if rc == 0:
                    seen.append("icmp")
            if seen:
                with lock:
                    found[address] = {"address": address, "seen": seen, "ports": open_ports,
                                      "mac": ""}

    workers = max(1, min(WORKERS, len(addresses), MAX_SOCKETS // len(ports)))
    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    wanted = set(addresses)
    neighbours = read_arp(wanted)
    if any(address not in found for address in neighbours):
        time.sleep(SETTLE)
        neighbours = read_arp(wanted)
    for address, mac in neighbours.items():
        host = found.setdefault(address, {"address": address, "seen": [], "ports": [],
                                          "mac": ""})
        host["mac"] = mac
        host["seen"].append("arp")
    hosts = sorted(found.values(), key=lambda host: ipaddress.IPv4Address(u"%s" % host["address"]))
    module.exit_json(changed=False, net_probe={
        "node": interfaces(module), "icmp": bool(icmp_socket or ping), "hosts": hosts,
        "addresses": len(addresses)})


def main():
    module = AnsibleModule(
        argument_spec=dict(
            subnet=dict(type="str"),
            addresses=dict(type="list", elements="str"),
            ports=dict(type="list", elements="int",
                       default=[22, 80, 135, 139, 443, 445, 3389, 5985, 5986]),
            timeout=dict(type="float", default=1.0),
        ),
        supports_check_mode=True,
    )
    os.environ["LC_ALL"] = "C"
    run(module)


if __name__ == "__main__":
    main()
