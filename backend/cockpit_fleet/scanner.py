"""The scan of a subnet, inside the run's sandbox (a job of the kind netscan).

Standard library only, no privilege: an ICMP echo on a datagram socket (the
kernel allows it to the groups of net.ipv4.ping_group_range), a TCP connect
to each port of a fixed list (a refusal says the host is there as much as an
open port does), the name of each host that answers from the reverse DNS and
from NetBIOS, and the MAC address from the controller's neighbour table once
the sweep has filled it. The table holds only the hosts of the controller's
own segment: beyond a router there is no entry at all, never the router's.

What it finds goes to netscan.json in the run's directory, which fleetctl
reads as untrusted data (netscan.ingest).
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RESULT = "netscan.json"
# A scan's addresses at most, whatever the spec says: config.toml's limit
# cannot go beyond it either.
MAX_ADDRESSES = 4096
MAX_PORTS = 32
TIMEOUT = 1.0
# Hosts probed at once; each holds one socket per port while it is probed.
MAX_WORKERS = 64
MAX_SOCKETS = 512
# How long the kernel takes to give up on a neighbour it had seen before:
# 5 s before the first probe, then 3 probes a second apart. Read before
# that, the table could still show a host that has gone since.
SETTLE = 9.0
ARP_TABLE = Path("/proc/net/arp")
_ATF_COM = 0x2
_NETBIOS_PORT = 137


def check_spec(spec: dict) -> tuple[ipaddress.IPv4Network, list[int]]:
    """The subnet and the ports of a spec, checked again inside the run."""
    try:
        subnet = ipaddress.IPv4Network(str(spec.get("subnet", "")))
    except ValueError as error:
        raise ValueError(f"not an IPv4 subnet: {error}") from error
    if subnet.num_addresses > MAX_ADDRESSES:
        raise ValueError(f"more than {MAX_ADDRESSES} addresses")
    ports = spec.get("ports")
    if (not isinstance(ports, list) or not 1 <= len(ports) <= MAX_PORTS
            or not all(type(port) is int and 0 < port < 65536 for port in ports)):
        raise ValueError(f"ports must be 1 to {MAX_PORTS} port numbers")
    return subnet, sorted(set(ports))


def addresses(subnet: ipaddress.IPv4Network) -> list[str]:
    """The addresses to probe: those of hosts, or every one of a /31 or a /32."""
    return [str(address) for address in subnet.hosts()]


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def icmp_available() -> bool:
    """Whether this process may send an ICMP echo without privilege."""
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
    except OSError:
        return False
    return True


def ping(address: str, timeout: float = TIMEOUT) -> bool:
    """One ICMP echo. The kernel sets the identifier of a datagram socket and
    hands it only the replies that carry it."""
    sequence = secrets.randbelow(65535) + 1
    payload = b"cockpit-fleet"
    header = struct.pack("!BBHHH", 8, 0, 0, 0, sequence)
    packet = struct.pack("!BBHHH", 8, 0, _checksum(header + payload), 0, sequence) + payload
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (address, 0))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                sock.settimeout(remaining)
                reply, _source = sock.recvfrom(1024)
                if (len(reply) >= 8 and reply[0] == 0
                        and struct.unpack("!H", reply[6:8])[0] == sequence):
                    return True
    except OSError:
        return False


def connect_all(address: str, ports: list[int], timeout: float = TIMEOUT) -> tuple[list[int], bool]:
    """Connect to every port at once: the ports open, and whether the host
    answered at all (a refusal is an answer)."""
    selector = selectors.DefaultSelector()
    open_ports: list[int] = []
    answered = False
    pending: dict[socket.socket, int] = {}
    try:
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
        deadline = time.monotonic() + timeout
        while pending:
            remaining = deadline - time.monotonic()
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


def _encode_netbios(name: bytes) -> bytes:
    padded = name.ljust(16, b"\0")
    return b"".join(bytes((0x41 + (byte >> 4), 0x41 + (byte & 0xF))) for byte in padded)


def netbios_request(transaction: int) -> bytes:
    """A node status request (NBSTAT) for the wildcard name."""
    header = struct.pack("!HHHHHH", transaction, 0, 1, 0, 0, 0)
    return header + b"\x20" + _encode_netbios(b"*") + b"\0" + struct.pack("!HH", 0x21, 1)


def parse_netbios(data: bytes, transaction: int) -> dict:
    """The name of the machine, its workgroup and the MAC address it gives,
    from a node status response; {} for anything else."""
    if len(data) < 12 or struct.unpack("!H", data[:2])[0] != transaction:
        return {}
    position = 12
    if position < len(data) and data[position] & 0xC0 == 0xC0:
        position += 2
    else:
        while position < len(data) and data[position] != 0:
            position += data[position] + 1
        position += 1
    position += 10  # type, class, time to live, length
    if position >= len(data):
        return {}
    count = data[position]
    position += 1
    found: dict = {}
    for index in range(count):
        entry = data[position + 18 * index: position + 18 * (index + 1)]
        if len(entry) < 18:
            return found
        name = entry[:15].decode("ascii", errors="replace").strip(" \0")
        suffix = entry[15]
        group = bool(struct.unpack("!H", entry[16:18])[0] & 0x8000)
        if suffix == 0 and not group and "name" not in found:
            found["name"] = name
        elif suffix == 0 and group and "workgroup" not in found:
            found["workgroup"] = name
    unit = data[position + 18 * count: position + 18 * count + 6]
    if len(unit) == 6 and any(unit):
        found["mac"] = ":".join(f"{byte:02x}" for byte in unit)
    return found


def netbios(address: str, timeout: float = TIMEOUT) -> dict:
    transaction = secrets.randbelow(65535) + 1
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(netbios_request(transaction), (address, _NETBIOS_PORT))
            data, _source = sock.recvfrom(2048)
    except OSError:
        return {}
    return parse_netbios(data, transaction)


def reverse_name(address: str) -> str:
    try:
        return socket.gethostbyaddr(address)[0]
    except (OSError, UnicodeError):
        return ""


def is_local(address: str) -> bool:
    """Whether the address is the controller's own: the kernel then picks it
    as the source of a datagram sent to it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((address, 9))
            return sock.getsockname()[0] == address
    except OSError:
        return False


def read_arp(text: str, subnet: ipaddress.IPv4Network) -> dict[str, str]:
    """Address to MAC address, from /proc/net/arp: the entries that are
    complete, in the subnet."""
    found = {}
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 6:
            continue
        try:
            address = ipaddress.IPv4Address(fields[0])
            flags = int(fields[2], 16)
        except ValueError:
            continue
        mac = fields[3].lower()
        if address in subnet and flags & _ATF_COM and mac != "00:00:00:00:00:00":
            found[str(address)] = mac
    return found


def probe(address: str, ports: list[int], icmp: bool) -> dict:
    """What one address says: how it was seen, its open ports and its names."""
    seen = []
    open_ports, answered = connect_all(address, ports)
    if answered:
        seen.append("tcp")
    if icmp and ping(address):
        seen.append("icmp")
    return {"address": address, "seen": seen, "ports": open_ports}


def name_host(host: dict) -> None:
    host["dns_name"] = reverse_name(host["address"])
    found = netbios(host["address"])
    host["netbios_name"] = found.get("name", "")
    host["workgroup"] = found.get("workgroup", "")
    if found.get("mac"):
        host["netbios_mac"] = found["mac"]


def scan(spec: dict, run_dir: Path, say, stopped) -> dict:
    """Run the scan of a spec; returns what fleet-run records as the result."""
    subnet, ports = check_spec(spec)
    targets = addresses(subnet)
    icmp = icmp_available()
    say(f"scanning {subnet}: {len(targets)} addresses, TCP ports {', '.join(map(str, ports))}")
    if not icmp:
        say("ICMP echo not allowed to this account (net.ipv4.ping_group_range): TCP and the"
            " neighbour table only")
    workers = max(1, min(MAX_WORKERS, MAX_SOCKETS // len(ports)))
    found: dict[str, dict] = {}
    lock = threading.Lock()

    def one(address: str) -> None:
        if stopped():
            return
        host = probe(address, ports, icmp)
        if host["seen"]:
            with lock:
                found[address] = host

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(one, address) for address in targets]:
            future.result()

    if not stopped():
        deadline = time.monotonic() + SETTLE
        while time.monotonic() < deadline and not stopped():
            time.sleep(0.2)
    try:
        neighbours = read_arp(ARP_TABLE.read_text(), subnet)
    except OSError as error:
        say(f"cannot read the neighbour table: {error}")
        neighbours = {}
    for address, mac in neighbours.items():
        host = found.setdefault(address, {"address": address, "seen": [], "ports": []})
        host["seen"].append("arp")
        host["mac"] = mac

    hosts = sorted(found.values(), key=lambda host: ipaddress.IPv4Address(host["address"]))
    if not stopped():
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(hosts)))) as pool:
            for future in [pool.submit(name_host, host) for host in hosts]:
                future.result()
    for host in hosts:
        host["self"] = is_local(host["address"])
        if not host.get("mac") and host.get("netbios_mac"):
            host["mac"] = host["netbios_mac"]
            host["mac_source"] = "netbios"
        elif host.get("mac"):
            host["mac_source"] = "arp"
        host.pop("netbios_mac", None)
        names = [name for name in (host.get("dns_name"), host.get("netbios_name")) if name]
        say(f"{host['address']:<15} {host.get('mac', '-'):<17} {','.join(host['seen']):<12}"
            f" {','.join(map(str, host['ports'])) or '-':<20} {' '.join(names)}")

    complete = not stopped()
    say(f"{len(hosts)} hosts found in {time.monotonic() - started:.0f} s"
        + ("" if complete else " (stopped before the end)"))
    result = {"subnet": str(subnet), "ports": ports, "icmp": icmp, "complete": complete,
              "addresses": len(targets), "hosts": hosts}
    temporary = run_dir / (RESULT + ".tmp")
    temporary.write_text(json.dumps(result))
    os.replace(temporary, run_dir / RESULT)
    return {"rc": 0 if complete else 1, "hosts": len(hosts)}
