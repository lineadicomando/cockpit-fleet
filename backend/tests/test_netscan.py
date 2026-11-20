"""The scan of a subnet: which subnets may be scanned, the job and what it
leaves, read as untrusted, and the probes themselves on the loopback."""

import ipaddress
import json
import socket
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import netscan, paths, scanner
from cockpit_fleet.config import ConfigError, parse

# A controller on 192.168.10.0/24 (eth0) and 10.88.0.0/16 (a bridge), with a
# default route, a route through a gateway and a route to one host.
ROUTES = """\
Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t010AA8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
eth0\t000AA8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
podman0\t0000580A\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
eth0\t0000100A\t010AA8C0\t0003\t0\t0\t100\t0000FFFF\t0\t0\t0
wg0\t0100000A\t00000000\t0005\t0\t0\t0\tFFFFFFFF\t0\t0\t0
"""

ARP = """\
IP address       HW type     Flags       HW address            Mask     Device
192.168.10.7     0x1         0x2         52:54:00:aa:bb:07     *        eth0
192.168.10.8     0x1         0x0         00:00:00:00:00:00     *        eth0
192.168.10.9     0x1         0x2         52:54:00:AA:BB:09     *        eth0
10.88.0.5        0x1         0x2         52:54:00:aa:bb:05     *        podman0
"""


def netbios_answer(transaction: int, names: list[tuple[str, int, bool]], mac: bytes) -> bytes:
    """A node status response as Windows sends it."""
    header = struct.pack("!HHHHHH", transaction, 0x8400, 0, 1, 0, 0)
    question = b"\x20" + b"CK" + b"A" * 30 + b"\0"
    body = bytes([len(names)])
    for name, suffix, group in names:
        body += name.encode().ljust(15) + bytes([suffix]) + struct.pack("!H", 0x8400 if group else 0x0400)
    body += mac + bytes(40)
    return header + question + struct.pack("!HHIH", 0x21, 1, 0, len(body)) + body


@unittest.skipIf(not hasattr(socket, "if_nameindex"), "no sockets")
class RoutesTest(unittest.TestCase):
    def test_connected_subnets_are_the_routes_with_no_gateway(self):
        with mock.patch("sys.byteorder", "little"):
            found = netscan.connected(ROUTES)
        self.assertEqual(found, [
            {"subnet": "192.168.10.0/24", "source": "connected", "interface": "eth0"},
            {"subnet": "10.88.0.0/16", "source": "connected", "interface": "podman0"},
        ])

    def test_lines_that_do_not_parse_are_left_out(self):
        self.assertEqual(netscan.connected("header\nnonsense\neth0 zz 0 1 0 0 0 zz\n"), [])


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        config = parse("")
        self.assertTrue(config.scan_connected)
        self.assertEqual(config.scan_subnets, ())
        self.assertIn(22, config.scan_ports)
        self.assertEqual(config.scan_max_addresses, 1024)

    def test_values_are_checked(self):
        config = parse('[network]\nscan_subnets = ["10.20.0.0/16", "10.20.0.0/16"]\n'
                       "scan_ports = [445, 22, 22]\nscan_max_addresses = 256\n"
                       "scan_connected = false\n")
        self.assertEqual(config.scan_subnets, ("10.20.0.0/16",))
        self.assertEqual(config.scan_ports, (22, 445))
        self.assertEqual(config.scan_max_addresses, 256)
        self.assertFalse(config.scan_connected)
        for text in ('scan_subnets = ["10.20.0.1/16"]', 'scan_subnets = ["fd00::/64"]',
                     "scan_subnets = [10]", 'scan_subnets = "10.0.0.0/8"', "scan_ports = []",
                     "scan_ports = [0]", "scan_ports = [true]", f"scan_ports = {list(range(1, 40))}",
                     "scan_max_addresses = 8192", "scan_max_addresses = 2",
                     'scan_connected = "yes"', "scan_other = 1"):
            with self.subTest(text), self.assertRaises(ConfigError):
                parse(f"[network]\n{text}\n")


class SubnetTest(unittest.TestCase):
    def setUp(self):
        self.config = parse('[network]\nscan_subnets = ["172.16.0.0/16"]\n')
        patch = mock.patch.object(netscan, "_connected", lambda config: [
            {"subnet": "192.168.10.0/24", "source": "connected", "interface": "eth0"}]
            if config.scan_connected else [])
        patch.start()
        self.addCleanup(patch.stop)

    def test_allowed_are_the_connected_and_those_listed(self):
        self.assertEqual([entry["subnet"] for entry in netscan.allowed(self.config)],
                         ["192.168.10.0/24", "172.16.0.0/16"])
        off = replace(self.config, scan_connected=False)
        self.assertEqual([entry["subnet"] for entry in netscan.allowed(off)], ["172.16.0.0/16"])

    def test_a_subnet_within_an_allowed_one_of_a_size_allowed(self):
        for subnet in ("192.168.10.0/24", "192.168.10.128/25", "172.16.4.0/22", "172.16.4.9/32"):
            self.assertEqual(str(netscan.check_subnet(self.config, subnet)), subnet)

    def test_others_are_refused(self):
        for subnet, code in (("192.168.11.0/24", "forbidden"), ("192.168.0.0/16", "bad_request"),
                             ("172.16.0.0/21", "bad_request"), ("192.168.10.1/24", "bad_request"),
                             ("fd00::/120", "bad_request"), ("example.org", "bad_request"),
                             ("0.0.0.0/0", "bad_request")):
            with self.subTest(subnet), self.assertRaises(Exception) as caught:
                netscan.check_subnet(self.config, subnet)
            self.assertEqual(caught.exception.code, code, subnet)


class ScannerTest(unittest.TestCase):
    def test_spec_is_checked_again_in_the_run(self):
        subnet, ports = scanner.check_spec({"subnet": "10.0.0.0/30", "ports": [445, 22, 22]})
        self.assertEqual((str(subnet), ports), ("10.0.0.0/30", [22, 445]))
        for spec in ({"subnet": "10.0.0.0/8", "ports": [22]}, {"subnet": "10.0.0.1/30", "ports": [22]},
                     {"subnet": "10.0.0.0/30", "ports": []}, {"subnet": "10.0.0.0/30", "ports": ["22"]},
                     {"subnet": "10.0.0.0/30", "ports": [70000]}, {"ports": [22]}):
            with self.subTest(spec), self.assertRaises(ValueError):
                scanner.check_spec(spec)

    def test_addresses_of_hosts(self):
        self.assertEqual(scanner.addresses(ipaddress.IPv4Network("10.0.0.0/30")),
                         ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(scanner.addresses(ipaddress.IPv4Network("10.0.0.5/32")), ["10.0.0.5"])

    def test_neighbours_complete_and_in_the_subnet(self):
        self.assertEqual(scanner.read_arp(ARP, ipaddress.IPv4Network("192.168.10.0/24")),
                         {"192.168.10.7": "52:54:00:aa:bb:07", "192.168.10.9": "52:54:00:aa:bb:09"})

    def test_netbios_request_names_the_wildcard(self):
        request = scanner.netbios_request(0x1234)
        self.assertEqual(request[:2], b"\x12\x34")
        self.assertEqual(request[12:14], b"\x20C")
        self.assertEqual(request[13:15], b"CK")
        self.assertEqual(request[-4:], b"\x00\x21\x00\x01")

    def test_netbios_answer(self):
        answer = netbios_answer(7, [("PC101", 0x00, False), ("SCUOLA", 0x00, True),
                                    ("PC101", 0x20, False)], bytes.fromhex("525400aabb07"))
        self.assertEqual(scanner.parse_netbios(answer, 7),
                         {"name": "PC101", "workgroup": "SCUOLA", "mac": "52:54:00:aa:bb:07"})
        # Samba gives no MAC address; another transaction is not ours.
        samba = netbios_answer(7, [("FILES", 0x00, False)], bytes(6))
        self.assertEqual(scanner.parse_netbios(samba, 7), {"name": "FILES"})
        self.assertEqual(scanner.parse_netbios(answer, 8), {})
        for cut in range(0, len(answer), 7):
            scanner.parse_netbios(answer[:cut], 7)

    def test_connect_tells_open_refused_and_silent(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            open_port = listener.getsockname()[1]
            with socket.socket() as other:
                other.bind(("127.0.0.1", 0))
                closed_port = other.getsockname()[1]
            self.assertEqual(scanner.connect_all("127.0.0.1", [open_port, closed_port]),
                             ([open_port], True))
            self.assertEqual(scanner.connect_all("127.0.0.1", [closed_port]), ([], True))
        # Nothing answers there.
        self.assertEqual(scanner.connect_all("192.0.2.1", [22], timeout=0.2), ([], False))

    def test_a_scan_of_the_loopback(self):
        with socket.socket() as listener, tempfile.TemporaryDirectory() as tmp:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            said = []
            with mock.patch.object(scanner, "SETTLE", 0), \
                    mock.patch.object(scanner, "netbios", lambda address: {}), \
                    mock.patch.object(scanner, "reverse_name", lambda address: "localhost"):
                result = scanner.scan({"subnet": "127.0.0.1/32", "ports": [port]}, Path(tmp),
                                      said.append, lambda: False)
            self.assertEqual(result, {"rc": 0, "hosts": 1})
            data = json.loads((Path(tmp) / scanner.RESULT).read_text())
        host = data["hosts"][0]
        self.assertEqual((host["address"], host["ports"], host["self"], host["dns_name"]),
                         ("127.0.0.1", [port], True, "localhost"))
        self.assertIn("tcp", host["seen"])
        self.assertTrue(data["complete"])
        self.assertIn("1 hosts found", said[-1])

    def test_a_scan_stopped_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scanner.scan({"subnet": "192.0.2.0/30", "ports": [22]}, Path(tmp),
                                  lambda text: None, lambda: True)
            data = json.loads((Path(tmp) / scanner.RESULT).read_text())
        self.assertEqual(result["rc"], 1)
        self.assertFalse(data["complete"])


class JobTest(Base):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, mcp_enabled=True)
        patch = mock.patch.object(netscan, "_connected", lambda config: [
            {"subnet": "192.168.10.0/24", "source": "connected", "interface": "eth0"}])
        patch.start()
        self.addCleanup(patch.stop)

    def scan(self, subnet="192.168.10.0/24"):
        run = self.ok("netscan.start", {"subnet": subnet})
        self.assertEqual((run["kind"], run["title"], run["status"]), ("netscan", subnet, "running"))
        spec = json.loads((paths.RUNS_DIR / run["id"] / "spec.json").read_text())
        self.assertEqual(spec, {"kind": "netscan", "subnet": subnet,
                                "ports": list(self.config.scan_ports)})
        return run["id"]

    def leave(self, run_id, hosts, rc=0, complete=True):
        (paths.RUNS_DIR / run_id / scanner.RESULT).write_text(json.dumps(
            {"subnet": "192.168.10.0/24", "icmp": True, "complete": complete, "hosts": hosts}))
        self.finish(run_id, rc)

    def test_subnets_and_roles(self):
        found = self.ok("netscan.subnets")
        self.assertEqual(found["subnets"][0]["subnet"], "192.168.10.0/24")
        self.assertEqual(found["max_addresses"], 1024)
        for role in ("viewer", "operator"):
            self.assertEqual(self.code("netscan.subnets", role=role), "forbidden")
            self.assertEqual(self.code("netscan.start", {"subnet": "192.168.10.0/24"}, role=role),
                             "forbidden")
        self.assertEqual(self.code("netscan.start", {"subnet": "8.8.8.0/24"}), "forbidden")
        self.assertEqual(self.ctx().conn.execute("SELECT count(*) FROM runs").fetchone()[0], 0)
        self.assertEqual(list((paths.RUNS_DIR).iterdir()), [])

    def test_what_a_scan_found_read_as_untrusted(self):
        run_id = self.scan()
        inventory = self.ok("inventory.create", {"name": "lab", "kind": "managed", "content": "x"})
        hosts = [{"name": "pc101", "address": "192.168.10.7", "port": None, "connection": "",
                  "groups": []},
                 {"name": "PC102.school.lan", "address": "", "port": None, "connection": "",
                  "groups": []}]
        self.ctx().conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                                (json.dumps(hosts), inventory["id"]))
        self.leave(run_id, [
            {"address": "192.168.10.7", "mac": "52:54:00:AA:BB:07", "mac_source": "arp",
             "dns_name": "pc101.school.lan", "netbios_name": "PC101", "workgroup": "SCUOLA",
             "ports": [22, 445, 22, "x", 0], "seen": ["tcp", "arp", "magic"], "self": False},
            {"address": "192.168.10.8", "mac": "not a mac", "mac_source": "arp",
             "netbios_name": "PC102" + "X" * 50, "ports": "all", "seen": "all", "self": "yes"},
            {"address": "10.0.0.1", "seen": ["tcp"]},
            {"address": "192.168.10.300"},
            "nonsense",
        ])
        result = self.ok("netscan.result", {"id": run_id})
        self.assertTrue(result["done"])
        self.assertEqual(result["run"]["status"], "successful")
        self.assertEqual(result["run"]["options"],
                         {"subnet": "192.168.10.0/24", "on_link": True, "hosts": 2, "icmp": True,
                          "complete": True})
        first, second = result["hosts"]
        self.assertEqual(first["mac"], "52:54:00:aa:bb:07")
        self.assertEqual(first["ports"], [22, 445])
        self.assertEqual(first["seen"], ["tcp", "arp"])
        self.assertEqual(first["known"], [{"inventory_id": inventory["id"], "inventory": "lab",
                                           "host": "pc101", "by": "address"}])
        self.assertEqual((second["mac"], second["mac_source"], second["ports"], second["seen"],
                          second["self"]), ("", "", [], [], False))
        self.assertEqual(len(second["netbios_name"]), 15)
        self.assertEqual(second["known"], [])
        self.assertFalse((paths.RUNS_DIR / run_id / scanner.RESULT).exists())

    def test_matched_by_name(self):
        run_id = self.scan()
        inventory = self.ok("inventory.create", {"name": "lab", "kind": "managed", "content": "x"})
        hosts = [{"name": "PC102.school.lan", "address": "", "port": None, "connection": "",
                  "groups": []}]
        self.ctx().conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                                (json.dumps(hosts), inventory["id"]))
        self.leave(run_id, [{"address": "192.168.10.8", "netbios_name": "PC102", "seen": ["tcp"]}])
        known = self.ok("netscan.result", {"id": run_id})["hosts"][0]["known"]
        self.assertEqual(known, [{"inventory_id": inventory["id"], "inventory": "lab",
                                  "host": "PC102.school.lan", "by": "name"}])

    def test_a_scan_canceled_keeps_what_it_found(self):
        run_id = self.scan()
        self.ok("run.cancel", {"id": run_id})
        self.leave(run_id, [{"address": "192.168.10.7", "seen": ["icmp"]}], rc=1, complete=False)
        result = self.ok("netscan.result", {"id": run_id})
        self.assertEqual(result["run"]["status"], "canceled")
        self.assertFalse(result["run"]["options"]["complete"])
        self.assertEqual([host["address"] for host in result["hosts"]], ["192.168.10.7"])

    def test_a_link_in_place_of_the_result_is_not_followed(self):
        run_id = self.scan()
        secret = self.root / "secret.json"
        secret.write_text(json.dumps({"hosts": [{"address": "192.168.10.66", "seen": ["tcp"]}]}))
        (paths.RUNS_DIR / run_id / scanner.RESULT).symlink_to(secret)
        self.finish(run_id)
        self.assertEqual(self.ok("netscan.result", {"id": run_id})["hosts"], [])

    def test_only_scans_and_only_for_administrators(self):
        run_id = self.scan()
        self.leave(run_id, [])
        self.assertEqual(self.code("netscan.result", {"id": run_id}, role="operator"), "forbidden")
        self.assertEqual(self.code("run.log", {"id": run_id}, role="operator"), "forbidden")
        self.ok("run.log", {"id": run_id})
        other = self.ok("inventory.create", {"name": "x", "kind": "managed", "content": "x"})
        parse = self.ok("inventory.parse", {"id": other["id"]})
        self.assertEqual(self.code("netscan.result", {"id": parse["run_id"]}), "bad_request")

    def test_an_agent_scans_in_diagnostic_and_administrator_only(self):
        def as_agent(profile):
            ctx = self.ctx()
            ctx.via = "mcp:test"
            return netscan_call(ctx, profile)

        def netscan_call(ctx, profile):
            from cockpit_fleet import cli
            payload = {"subnet": "192.168.10.0/30", "via": "mcp:test", "profile": profile}
            return cli.dispatch("netscan.start", json.dumps(payload).encode(), ctx)

        for profile, allowed in (("observer", False), ("assisted", False), ("autonomous", False),
                                 ("diagnostic", True), ("administrator", True)):
            with self.subTest(profile):
                answer = as_agent(profile)
                self.assertEqual(answer["ok"], allowed, answer)
