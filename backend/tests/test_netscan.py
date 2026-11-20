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


class ScanBase(Base):
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



class JobTest(ScanBase):
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


def found(address, **extra):
    host = {"address": address, "mac": "", "mac_source": "", "dns_name": "", "netbios_name": "",
            "workgroup": "", "ports": [], "seen": ["tcp"], "self": False, "known": []}
    return {**host, **extra}


class NamesTest(unittest.TestCase):
    def test_a_host_is_named_as_asked_or_by_its_address(self):
        host = found("10.0.0.7", dns_name="pc101.school.lan.", netbios_name="PC101")
        self.assertEqual(netscan.host_name(host, "short"), "pc101")
        self.assertEqual(netscan.host_name(host, "dns"), "pc101.school.lan")
        self.assertEqual(netscan.host_name(host, "address"), "10.0.0.7")
        dns_only = found("10.0.0.8", dns_name="printer.school.lan")
        self.assertEqual(netscan.host_name(dns_only, "short"), "printer")
        netbios_only = found("10.0.0.9", netbios_name="AULA_3")
        self.assertEqual(netscan.host_name(netbios_only, "short"), "10.0.0.9")
        self.assertEqual(netscan.host_name(found("10.0.0.10"), "dns"), "10.0.0.10")
        self.assertEqual(netscan.host_name(found("10.0.0.11", dns_name="-x"), "dns"),
                         "10.0.0.11")

    def test_the_text_of_an_inventory(self):
        hosts = [found("10.0.0.7", netbios_name="PC101", mac="52:54:00:aa:bb:07"),
                 found("10.0.0.8", dns_name="pc101.other.lan"),
                 found("10.0.0.9")]
        text, named = netscan.inventory_text(hosts, "short", "")
        self.assertEqual([entry["name"] for entry in named], ["pc101", "10.0.0.8", "10.0.0.9"])
        self.assertEqual(text, 'all:\n  hosts:\n    "pc101":\n      ansible_host: "10.0.0.7"\n'
                               '      mac_address: "52:54:00:aa:bb:07"\n    "10.0.0.8": {}\n'
                               '    "10.0.0.9": {}\n')
        grouped, _ = netscan.inventory_text(hosts[:1], "address", "lab")
        self.assertEqual(grouped, 'all:\n  children:\n    lab:\n      hosts:\n'
                                  '        "10.0.0.7":\n          mac_address: "52:54:00:aa:bb:07"\n')


@unittest.skipUnless(Path("/usr/bin/git").exists(), "needs git")
class InventoryTest(ScanBase):
    HOSTS = [
        {"address": "192.168.10.7", "mac": "52:54:00:aa:bb:07", "mac_source": "arp",
         "netbios_name": "PC101", "seen": ["tcp", "arp"]},
        {"address": "192.168.10.8", "mac": "52:54:00:aa:bb:08", "mac_source": "arp",
         "dns_name": "pc102.school.lan", "seen": ["arp"]},
        {"address": "192.168.10.9", "mac": "52:54:00:aa:bb:09", "mac_source": "netbios",
         "netbios_name": "PC103", "seen": ["tcp"]},
        {"address": "192.168.10.10", "seen": ["icmp"]},
        {"address": "192.168.10.11", "mac": "52:54:00:aa:bb:11", "netbios_name": "PC105",
         "mac_source": "arp", "seen": ["arp"]},
    ]

    def setUp(self):
        super().setUp()
        self.run_id = self.scan()
        self.leave(self.run_id, self.HOSTS)

    def tree(self, inventory_id):
        return paths.INVENTORIES_DIR / inventory_id / "tree"

    def test_an_inventory_from_a_scan(self):
        made = self.ok("netscan.inventory", {
            "id": self.run_id, "name": "aula", "group": "aula3",
            "addresses": ["192.168.10.9", "192.168.10.7", "192.168.10.10"]})
        inventory = made["inventory"]
        self.assertEqual([host["name"] for host in made["hosts"]],
                         ["pc101", "pc103", "192.168.10.10"])
        text = (self.tree(inventory["id"]) / "hosts.yaml").read_text()
        self.assertIn('      hosts:\n        "pc101":\n          ansible_host: "192.168.10.7"\n'
                      '          mac_address: "52:54:00:aa:bb:07"\n', text)
        self.assertIn('        "192.168.10.10": {}\n', text)
        parse = self.ctx().conn.execute("SELECT kind, inventory_id FROM runs WHERE id = ?",
                                        (made["parse_run_id"],)).fetchone()
        self.assertEqual(tuple(parse), ("inventory", inventory["id"]))

    def test_an_inventory_refuses_what_the_scan_did_not_find(self):
        for params, code in (({"addresses": []}, "bad_request"),
                             ({"addresses": ["192.168.10.99"]}, "bad_request"),
                             ({"addresses": ["192.168.10.7"], "group": "all"}, "bad_request"),
                             ({"addresses": ["192.168.10.7"], "group": "a b"}, "bad_request")):
            with self.subTest(params):
                self.assertEqual(self.code("netscan.inventory",
                                           {"id": self.run_id, "name": "x", **params}), code)
        running = self.scan()
        self.assertEqual(self.code("netscan.inventory", {"id": running, "name": "x",
                                                         "addresses": ["192.168.10.7"]}),
                         "conflict")
        self.assertEqual(self.code("netscan.inventory", {"id": self.run_id, "name": "x",
                                                         "addresses": ["192.168.10.7"]},
                                   role="operator"), "forbidden")

    def existing(self):
        inventory = self.ok("inventory.create", {
            "name": "lab", "kind": "managed", "content": "all:\n  hosts: {}\n", "files": [
                {"path": "host_vars/pc101.yaml", "content": "role: lab\n"},
                {"path": "host_vars/pc103.yml", "content": "mac_address: '00:11:22:33:44:55'\n"},
                {"path": "host_vars/pc105/other.yaml", "content": "x: 1\n"},
            ]})
        hosts = [{"name": name, "address": address, "port": None, "connection": "",
                  "groups": []} for name, address in (
                      ("pc101", "192.168.10.7"), ("pc102", "192.168.10.8"),
                      ("pc103", "192.168.10.9"), ("pc104", ""), ("pc105", "192.168.10.11"))]
        self.ctx().conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                                (json.dumps(hosts), inventory["id"]))
        return inventory["id"]

    def test_mac_addresses_written_into_an_inventory(self):
        inventory_id = self.existing()
        tree = self.tree(inventory_id)
        preview = self.ok("netscan.macs", {"id": self.run_id, "inventory_id": inventory_id,
                                            "preview": True})
        self.assertIsNone(preview["revision"])
        self.assertFalse((tree / "host_vars" / "pc102").exists())
        results = {entry["host"]: entry["result"] for entry in preview["hosts"]}
        self.assertEqual(results, {"pc101": "written", "pc102": "written", "pc103": "skipped",
                                   "pc105": "written"})
        done = self.ok("netscan.macs", {"id": self.run_id, "inventory_id": inventory_id})
        self.assertTrue(done["revision"])
        self.assertEqual(done["hosts"], preview["hosts"])
        self.assertEqual((tree / "host_vars/pc101/fleet_mac.yaml").read_text(),
                         'mac_address: "52:54:00:aa:bb:07"\n')
        # Moved into the directory, as it was: Ansible would not read it beside it.
        self.assertEqual((tree / "host_vars/pc101/pc101.yaml").read_text(), "role: lab\n")
        self.assertFalse((tree / "host_vars/pc101.yaml").exists())
        self.assertEqual(done["hosts"][0]["moved"], {"from": "host_vars/pc101.yaml",
                                                     "to": "host_vars/pc101/pc101.yaml"})
        self.assertTrue((tree / "host_vars/pc102/fleet_mac.yaml").is_file())
        self.assertEqual((tree / "host_vars/pc103.yml").read_text(),
                         "mac_address: '00:11:22:33:44:55'\n")
        self.assertIn("mac_address is set in host_vars/pc103.yml", done["hosts"][2]["reason"])
        self.assertEqual((tree / "host_vars/pc105/other.yaml").read_text(), "x: 1\n")
        self.assertTrue((tree / "host_vars/pc105/fleet_mac.yaml").is_file())
        again = self.ok("netscan.macs", {"id": self.run_id, "inventory_id": inventory_id})
        self.assertIsNone(again["revision"])
        self.assertEqual({entry["result"] for entry in again["hosts"] if entry["host"] != "pc103"},
                         {"unchanged"})

    def test_only_the_hosts_asked_for_and_by_name_when_asked(self):
        inventory_id = self.existing()
        hosts = [{"name": "PC105", "address": "", "port": None, "connection": "", "groups": []}]
        self.ctx().conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                                (json.dumps(hosts), inventory_id))
        none = self.ok("netscan.macs", {"id": self.run_id, "inventory_id": inventory_id,
                                         "preview": True})
        self.assertEqual(none["hosts"], [])
        named = self.ok("netscan.macs", {"id": self.run_id, "inventory_id": inventory_id,
                                          "preview": True, "by_name": True,
                                          "hosts": ["PC105", "pc999"]})
        self.assertEqual([(entry["host"], entry["result"]) for entry in named["hosts"]],
                         [("PC105", "written"), ("pc999", "skipped")])

    def test_only_an_inventory_written_here(self):
        project = self.local_project()
        inventory = self.ok("inventory.create", {"name": "f", "kind": "file",
                                                 "project_id": project["id"],
                                                 "path": "inventories/hosts.yml"})
        self.assertEqual(self.code("netscan.macs", {"id": self.run_id,
                                                    "inventory_id": inventory["id"]}),
                         "bad_request")

    def test_an_agent_writes_to_its_draft(self):
        inventory_id = self.existing()
        self.config = replace(self.config, mcp_max_role="admin",
                              chat_profiles=("assisted", "diagnostic", "administrator"))
        answer = self.call("netscan.macs", {"id": self.run_id, "inventory_id": inventory_id,
                                            "via": "mcp:test", "profile": "administrator",
                                            "session": "0123456789abcdef"})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["draft"], "0123456789abcdef")
        self.assertFalse((self.tree(inventory_id) / "host_vars/pc102").exists())
        refused = self.call("netscan.macs", {"id": self.run_id, "inventory_id": inventory_id,
                                             "via": "mcp:test", "profile": "diagnostic"})
        self.assertFalse(refused["ok"])
        self.assertIn("(config) is not allowed", refused["error"]["message"])


class ProbeTest(Base):
    """netscan.probe, with the network stood in for: what each host says."""

    ANSWERS = {
        "192.168.10.7": {"seen": ["tcp", "icmp"], "ports": [22, 445]},
        "192.168.10.8": {"seen": ["tcp"], "ports": [3389]},
        "192.168.10.9": {"seen": [], "ports": []},
        "192.168.10.10": {"seen": [], "ports": []},
        "192.168.10.11": {"seen": ["tcp"], "ports": [5985]},
    }
    NEIGHBOURS = {"192.168.10.7": "52:54:00:aa:bb:07", "192.168.10.10": "52:54:00:aa:bb:10",
                  "192.168.10.11": "52:54:00:aa:bb:99"}

    def setUp(self):
        super().setUp()
        self.inventory = self.ok("inventory.create", {"name": "aula", "kind": "file",
                                                      "project_id": self.local_project()["id"],
                                                      "path": "inventories/hosts.yml"})
        hosts = [{"name": name, "address": address, "port": None, "connection": connection,
                  "groups": []} for name, address, connection in (
                      ("pc101", "192.168.10.7", ""), ("pc102", "192.168.10.8", ""),
                      ("pc103", "192.168.10.9", ""), ("pc104", "192.168.10.10", ""),
                      ("pc105", "192.168.10.11", "winrm"), ("gone.school.lan", "", ""),
                      ("v6", "fd00::5", ""), ("localhost", "", "local"))]
        conn = self.ctx().conn
        conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                     (json.dumps(hosts), self.inventory["id"]))
        for index, (name, mac, addresses) in enumerate((
                ("pc101", "52-54-00-AA-BB-07", ["192.168.10.7/24"]),
                ("pc105", "52:54:00:aa:bb:05", ["192.168.10.5/24"]),
                ("pc109", "52:54:00:aa:bb:99", ["192.168.10.19/24"]))):
            conn.execute("INSERT INTO hosts (id, inventory_id, name, created_at)"
                         " VALUES (?, ?, ?, 0)", (f"h{index}", self.inventory["id"], name))
            conn.execute("INSERT INTO cmdb_hosts (host_id, interfaces) VALUES (?, ?)",
                         (f"h{index}", json.dumps([{"name": "eth0", "mac": mac,
                                                    "addresses": addresses}])))
        self.asked = []

        def probe(address, ports, icmp):
            self.asked.append((address, tuple(ports)))
            return {"address": address, **self.ANSWERS[address]}

        def name_host(host):
            host.update(dns_name="", netbios_name="PC999" if host["address"] == "192.168.10.8"
                        else "", workgroup="")

        for patch in (
                mock.patch.object(scanner, "probe", probe),
                mock.patch.object(scanner, "name_host", name_host),
                mock.patch.object(scanner, "is_local", lambda address: False),
                mock.patch.object(scanner, "icmp_available", lambda: True),
                mock.patch.object(scanner, "SETTLE", 0),
                mock.patch.object(netscan, "_resolve", lambda name: []),
                mock.patch.object(netscan, "_neighbours", lambda: dict(self.NEIGHBOURS)),
                mock.patch.object(netscan, "connected_routes", lambda: [
                    {"subnet": "192.168.10.0/24", "source": "connected", "interface": "eth0"}])):
            patch.start()
            self.addCleanup(patch.stop)

    def test_what_each_host_says(self):
        answer = self.ok("netscan.probe", {"inventory_id": self.inventory["id"]}, role="operator")
        hosts = {host["name"]: host for host in answer["hosts"]}
        self.assertNotIn("localhost", hosts)
        self.assertEqual({name: host["findings"] for name, host in hosts.items()}, {
            "pc101": [],
            "pc102": ["port_closed", "netbios_differs"],
            "pc103": ["silent"],
            "pc104": ["segment_only"],
            "pc105": ["mac_differs", "address_moved"],
            "gone.school.lan": ["unresolved"],
            "v6": ["ipv6"],
        })
        self.assertEqual(hosts["pc101"]["cmdb"]["macs"], ["52:54:00:aa:bb:07"])
        self.assertEqual(hosts["pc104"]["seen"], ["arp"])
        self.assertEqual(hosts["pc105"]["mac_of"], ["pc109 (aula)"])
        self.assertEqual(hosts["pc105"]["connection_port"], 5985)
        self.assertTrue(hosts["pc101"]["on_link"])
        self.assertIn(5985, answer["ports"])
        self.assertEqual(len(self.asked), 5)

    def test_only_the_hosts_asked_for(self):
        self.assertEqual(self.code("netscan.probe", {"inventory_id": self.inventory["id"]},
                                   role="viewer"), "forbidden")
        answer = self.ok("netscan.probe", {"inventory_id": self.inventory["id"],
                                            "hosts": ["pc101"]}, role="operator")
        self.assertEqual([host["name"] for host in answer["hosts"]], ["pc101"])
        self.assertEqual(self.code("netscan.probe", {"inventory_id": self.inventory["id"],
                                                     "hosts": ["localhost", "nobody"]},
                                   role="operator"), "bad_request")

    def test_too_many_hosts_at_once(self):
        hosts = [{"name": f"h{index}", "address": f"10.0.{index // 250}.{index % 250 + 1}",
                  "port": None, "connection": "", "groups": []} for index in range(300)]
        self.ctx().conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                                (json.dumps(hosts), self.inventory["id"]))
        self.assertEqual(self.code("netscan.probe", {"inventory_id": self.inventory["id"]},
                                   role="operator"), "bad_request")

    def test_mac_addresses_as_windows_writes_them(self):
        self.assertEqual(netscan.normal_mac("52-54-00-AA-BB-07"), "52:54:00:aa:bb:07")
        for value in ("", None, "00:00:00:00:00:00", "52:54:00:aa:bb", "zz:54:00:aa:bb:07"):
            self.assertEqual(netscan.normal_mac(value), "", value)


class ProbeModuleTest(ScanBase):
    """An ad-hoc lineadicomando.fleet.net_probe: what it may probe from a node."""

    def setUp(self):
        super().setUp()
        self.config = replace(self.config, scan_subnets=("172.16.0.0/16",))
        project = self.local_project()
        self.inventory = self.ok("inventory.create", {"name": "lab", "kind": "file",
                                                      "project_id": project["id"],
                                                      "path": "inventories/hosts.yml"})
        hosts = [{"name": "far", "address": "10.9.9.9", "port": None, "connection": "",
                  "groups": []}]
        self.ctx().conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                                (json.dumps(hosts), self.inventory["id"]))

    def adhoc(self, args, free_form=""):
        return self.call("run.adhoc", {"inventory_id": self.inventory["id"], "limit": "all",
                                       "module": netscan.PROBE_MODULE, "args": args,
                                       "free_form": free_form})

    def test_what_a_node_may_probe(self):
        for args in ({"subnet": "192.168.10.0/24"}, {"subnet": "172.16.5.0/24"},
                     {"addresses": ["10.9.9.9", "172.16.1.1"]},
                     {"subnet": "172.16.5.0/24", "ports": [445, 22], "timeout": 2}):
            with self.subTest(args):
                answer = self.adhoc(args)
                self.assertTrue(answer["ok"], answer)
                spec = json.loads((paths.RUNS_DIR / answer["data"]["id"] / "spec.json")
                                  .read_text())
                self.assertEqual(spec["args"]["ports"], sorted(args.get(
                    "ports", list(self.config.scan_ports))))
        for args, code in (({"subnet": "8.8.8.0/24"}, "forbidden"),
                           ({"addresses": ["8.8.8.8"]}, "forbidden"),
                           ({"subnet": "172.16.0.0/16"}, "bad_request"),
                           ({}, "bad_request"),
                           ({"subnet": "172.16.5.0/24", "addresses": ["10.9.9.9"]}, "bad_request"),
                           ({"subnet": "172.16.5.0/24", "ports": [8080]}, "bad_request"),
                           ({"subnet": "172.16.5.0/24", "timeout": 60}, "bad_request"),
                           ({"subnet": "{{ lookup('pipe', 'id') }}"}, "bad_request"),
                           ({"addresses": ["{{ x }}"]}, "bad_request"),
                           ({"subnet": "172.16.5.0/24", "become": True}, "bad_request")):
            with self.subTest(args):
                answer = self.adhoc(args)
                self.assertFalse(answer["ok"], answer)
                self.assertEqual(answer["error"]["code"], code, answer)
        self.assertEqual(self.adhoc({"subnet": "172.16.5.0/24"}, free_form="x")["error"]["code"],
                         "bad_request")

    def test_an_agent_probes_from_a_node_in_diagnostic(self):
        self.config = replace(self.config, mcp_max_role="admin",
                              chat_profiles=("assisted", "diagnostic"))
        answer = self.call("run.adhoc", {
            "inventory_id": self.inventory["id"], "limit": "all", "module": netscan.PROBE_MODULE,
            "args": {"subnet": "172.16.5.0/24"}, "via": "mcp:test", "profile": "diagnostic"})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["status"], "running")
        refused = self.call("run.adhoc", {
            "inventory_id": self.inventory["id"], "limit": "all", "module": netscan.PROBE_MODULE,
            "args": {"subnet": "8.8.8.0/24"}, "via": "mcp:test", "profile": "diagnostic"})
        self.assertEqual(refused["error"]["code"], "forbidden")
