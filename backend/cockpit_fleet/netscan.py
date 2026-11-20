"""The scan of a subnet: which subnets may be scanned, the job that scans one
(scanner.py, in the run's sandbox) and what it found.

A subnet may be scanned when it lies within one the controller is attached
to (unless [network] scan_connected is false) or within one of [network]
scan_subnets, and has at most scan_max_addresses addresses: an agent steered
by what a node wrote cannot turn the controller on any network it likes.
What the job leaves is read as untrusted, as everything a run writes.
"""

import ipaddress
import json
import re
import shutil
import sys
import time
from pathlib import Path

from . import jobs, scanner, store
from .cli import Context, Id, Int, Str, Values, command
from .errors import BadRequest, Forbidden
from .runs import run_row

ROUTES = Path("/proc/net/route")
_RTF_UP = 0x1
_RTF_GATEWAY = 0x2
_MAC_RE = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
_SEEN = ("tcp", "icmp", "arp")
_MAX_RESULT = 8 * 1024 * 1024


def _address(field: str) -> ipaddress.IPv4Address:
    # The kernel prints the address as it holds it, in the network's order,
    # read as a number of this machine's.
    return ipaddress.IPv4Address(int(field, 16).to_bytes(4, sys.byteorder))


def connected(text: str) -> list[dict]:
    """The subnets the controller is attached to, from /proc/net/route: the
    routes with no gateway, but those of the loopback and of single hosts."""
    found = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            destination = _address(fields[1])
            gateway = int(fields[2], 16)
            flags = int(fields[3], 16)
            mask = _address(fields[7])
            subnet = ipaddress.IPv4Network(f"{destination}/{mask}")
        except ValueError:
            continue
        if (not flags & _RTF_UP or flags & _RTF_GATEWAY or gateway or subnet.prefixlen == 0
                or subnet.prefixlen > 30 or subnet.is_loopback):
            continue
        entry = {"subnet": str(subnet), "source": "connected", "interface": fields[0]}
        if entry not in found:
            found.append(entry)
    return found


def _connected(config) -> list[dict]:
    if not config.scan_connected:
        return []
    try:
        return connected(ROUTES.read_text())
    except OSError:
        return []


def allowed(config) -> list[dict]:
    """The subnets a scan may cover, each with where it comes from."""
    found = _connected(config)
    taken = {entry["subnet"] for entry in found}
    found += [{"subnet": subnet, "source": "config", "interface": ""}
              for subnet in config.scan_subnets if subnet not in taken]
    return found


def check_subnet(config, value: str) -> ipaddress.IPv4Network:
    """A subnet that may be scanned, or the reason it may not."""
    try:
        subnet = ipaddress.IPv4Network(value)
    except ValueError as error:
        raise BadRequest(f"{value!r} is not an IPv4 subnet written as address/prefix, with no"
                         f" bits of a host: {error}") from error
    if subnet.num_addresses > config.scan_max_addresses:
        raise BadRequest(f"{subnet} has {subnet.num_addresses} addresses, more than"
                         f" {config.scan_max_addresses} ([network] scan_max_addresses): scan a"
                         " part of it")
    if not any(subnet.subnet_of(ipaddress.IPv4Network(entry["subnet"]))
               for entry in allowed(config)):
        raise Forbidden(f"{subnet} is not within a subnet that may be scanned: one the"
                        " controller is attached to, or one of [network] scan_subnets")
    return subnet


@command("netscan.subnets", role="admin")
def netscan_subnets(ctx: Context, params: Values) -> dict:
    """The subnets a scan may cover: those the controller is attached to,
    unless config.toml says otherwise, and those it lists. A scan takes one of
    them or a part of it, of at most max_addresses addresses, and tries the
    ports listed on each address."""
    config = ctx.config
    return {"subnets": allowed(config), "max_addresses": config.scan_max_addresses,
            "ports": list(config.scan_ports)}


@command("netscan.start", role="admin", mutating=True, agent="scan",
         params={
             "subnet": Str("An IPv4 subnet as address/prefix, such as 192.168.1.0/24, within"
                           " one of those netscan.subnets lists", required=True, max_length=18),
         })
def netscan_start(ctx: Context, params: Values) -> dict:
    """Scan a subnet for the hosts on it: each address is tried with an ICMP
    echo and a TCP connection to a fixed list of ports, and each host found is
    named from the reverse DNS and NetBIOS, with its MAC address when it is on
    the controller's own segment. It is a run: netscan.result waits for it and
    gives what it found."""
    subnet = check_subnet(ctx.config, params["subnet"])
    on_link = any(subnet.subnet_of(ipaddress.IPv4Network(entry["subnet"]))
                  for entry in _connected(ctx.config))
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        run = {"id": run_id, "kind": "netscan", "title": str(subnet),
               "options": json.dumps({"subnet": str(subnet), "on_link": on_link})}
        spec = {"kind": "netscan", "subnet": str(subnet), "ports": list(ctx.config.scan_ports)}
        jobs.enqueue(ctx, run, spec, directory)
    except BaseException:
        if not ctx.conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
            shutil.rmtree(directory, ignore_errors=True)
        raise
    return run_row(store.fetch(ctx.conn, "runs", run_id, "run"))


def _text(value: object, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _host(entry: object, subnet: ipaddress.IPv4Network) -> dict | None:
    """One host as the job wrote it, checked; None for one malformed."""
    if not isinstance(entry, dict):
        return None
    try:
        address = ipaddress.IPv4Address(str(entry.get("address", "")))
    except ValueError:
        return None
    if address not in subnet:
        return None
    mac = _text(entry.get("mac"), 17).lower()
    ports = entry.get("ports")
    ports = sorted({port for port in ports if type(port) is int and 0 < port < 65536}
                   )[:scanner.MAX_PORTS] if isinstance(ports, list) else []
    seen = entry.get("seen")
    seen = [how for how in _SEEN if isinstance(seen, list) and how in seen]
    source = entry.get("mac_source")
    return {
        "address": str(address),
        "mac": mac if _MAC_RE.fullmatch(mac) else "",
        "mac_source": source if source in ("arp", "netbios") and _MAC_RE.fullmatch(mac) else "",
        "dns_name": _text(entry.get("dns_name"), 253),
        "netbios_name": _text(entry.get("netbios_name"), 15),
        "workgroup": _text(entry.get("workgroup"), 15),
        "ports": ports,
        "seen": seen,
        "self": entry.get("self") is True,
    }


def ingest(ctx, run, directory: Path) -> dict:
    """Take in what a scan found; returns what goes into the run's options."""
    subnet = ipaddress.IPv4Network(store.loads(run["options"], {}).get("subnet", "0.0.0.0/32"))
    try:
        data = json.loads(jobs.read_run_file(directory, scanner.RESULT, _MAX_RESULT))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("hosts"), list):
        return {}
    hosts = {}
    for entry in data["hosts"][:scanner.MAX_ADDRESSES]:
        host = _host(entry, subnet)
        if host is not None:
            hosts[host["address"]] = host
    ctx.conn.execute("DELETE FROM netscan_hosts WHERE run_id = ?", (run["id"],))
    ctx.conn.executemany(
        "INSERT INTO netscan_hosts (run_id, address, mac, mac_source, dns_name, netbios_name,"
        " workgroup, ports, seen, self) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(run["id"], host["address"], host["mac"], host["mac_source"], host["dns_name"],
          host["netbios_name"], host["workgroup"], json.dumps(host["ports"]),
          json.dumps(host["seen"]), int(host["self"])) for host in hosts.values()])
    return {"hosts": len(hosts), "icmp": data.get("icmp") is True,
            "complete": data.get("complete") is True}


def _names(name: str) -> set[str]:
    """A host's name as it may be matched: whole, and its first label."""
    name = name.strip().lower().rstrip(".")
    if not name:
        return set()
    try:
        ipaddress.ip_address(name)
        return {name}
    except ValueError:
        return {name, name.split(".")[0]}


def _known(ctx) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """The hosts of the inventories by address and by name, as their last
    reading lists them."""
    by_address: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for inventory in ctx.conn.execute("SELECT id, name, hosts FROM inventories WHERE NOT starter"
                                      " ORDER BY name COLLATE NOCASE"):
        for host in store.loads(inventory["hosts"], []):
            if not isinstance(host, dict):
                continue
            name = str(host.get("name", ""))
            entry = {"inventory_id": inventory["id"], "inventory": inventory["name"],
                     "host": name}
            for key in _names(str(host.get("address") or "")) | _names(name):
                index = by_address if _is_address(key) else by_name
                if entry not in index.setdefault(key, []):
                    index[key].append(entry)
    return by_address, by_name


def _is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _row(row, by_address: dict, by_name: dict) -> dict:
    host = {key: row[key] for key in ("address", "mac", "mac_source", "dns_name",
                                      "netbios_name", "workgroup")}
    host["ports"] = store.loads(row["ports"], [])
    host["seen"] = store.loads(row["seen"], [])
    host["self"] = bool(row["self"])
    known = [{**entry, "by": "address"} for entry in by_address.get(row["address"], [])]
    for name in _names(row["dns_name"]) | _names(row["netbios_name"]):
        for entry in by_name.get(name, []):
            if not any(item["inventory_id"] == entry["inventory_id"]
                       and item["host"] == entry["host"] for item in known):
                known.append({**entry, "by": "name"})
    host["known"] = known
    return host


@command("netscan.result", role="admin",
         params={
             "id": Id("The run of the scan."),
             "wait": Int(0, 50, "Seconds to wait for the scan to end before answering.",
                         default=0),
         })
def netscan_result(ctx: Context, params: Values) -> dict:
    """What a scan found: each host that answered, how it was seen (tcp: a
    port open or refused, icmp, arp: in the controller's neighbour table), its
    open ports, its names from the reverse DNS and NetBIOS, its MAC address,
    and the hosts of inventories it may be, by address or by name. With wait
    it answers when the scan ends or after that many seconds; "done" tells
    which."""
    run_id = params["id"]
    deadline = time.monotonic() + params["wait"]
    while True:
        jobs.reconcile(ctx)
        row = store.fetch(ctx.conn, "runs", run_id, "run")
        if row["kind"] != "netscan":
            raise BadRequest(f"{run_id} is not the run of a scan")
        if row["status"] not in jobs.ACTIVE or time.monotonic() >= deadline:
            break
        time.sleep(1)
    by_address, by_name = _known(ctx)
    hosts = [_row(host, by_address, by_name) for host in ctx.conn.execute(
        "SELECT * FROM netscan_hosts WHERE run_id = ?", (run_id,))]
    hosts.sort(key=lambda host: ipaddress.IPv4Address(host["address"]))
    run = run_row(row)
    return {"run": run, "done": row["status"] not in jobs.ACTIVE, "hosts": hosts,
            "subnet": run["options"].get("subnet", ""),
            "on_link": run["options"].get("on_link") is True}
