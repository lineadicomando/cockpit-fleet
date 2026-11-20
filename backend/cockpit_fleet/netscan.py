"""The scan of a subnet: which subnets may be scanned, the job that scans one
(scanner.py, in the run's sandbox) and what it found.

A subnet may be scanned when it lies within one the controller is attached
to (unless [network] scan_connected is false) or within one of [network]
scan_subnets, and has at most scan_max_addresses addresses: an agent steered
by what a node wrote cannot turn the controller on any network it likes.
What the job leaves is read as untrusted, as everything a run writes.

netscan.probe looks at the hosts of an inventory one by one, synchronously:
whether each name resolves, answers, has its connection's port open, and
whether the MAC address seen is the one the CMDB knows for it.

What a scan found becomes an inventory written here (netscan.inventory), or
the MAC addresses of the hosts of one (netscan.macs): the variable
mac_address in host_vars/HOST/fleet_mac.yaml, a file of ours. Ansible reads
no host_vars/HOST.yaml once host_vars/HOST/ exists, so a file of the host's
is moved, unchanged, into that directory in the same commit.
"""

import ipaddress
import json
import re
import shutil
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from . import files, jobs, journal, scanner, store
from .cli import COMMANDS, Bool, Choice, Context, Id, Int, Str, Strs, Values, command
from .errors import BadRequest, Conflict, Forbidden
from .resources import queue_parse
from .runs import run_row
from .validators import check_host

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
    return connected_routes() if config.scan_connected else []


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


VARIABLE = "mac_address"
MAC_FILE = "fleet_mac.yaml"
_GROUP_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# What Ansible looks for in host_vars, in its order: the first found is the
# only one it reads (DataLoader.find_vars_files).
_VARS_EXTENSIONS = ("", ".yml", ".yaml", ".json")
_SETS_MAC = re.compile(rb"^\s*\"?mac_address\"?\s*:", re.MULTILINE)


def _scan_hosts(ctx, run_id: str) -> tuple[dict, dict[str, dict]]:
    """A scan that has ended, and its hosts by address."""
    jobs.reconcile(ctx)
    row = store.fetch(ctx.conn, "runs", run_id, "run")
    if row["kind"] != "netscan":
        raise BadRequest(f"{run_id} is not the run of a scan")
    if row["status"] in jobs.ACTIVE:
        raise Conflict("the scan has not ended yet")
    by_address, by_name = _known(ctx)
    hosts = {host["address"]: _row(host, by_address, by_name) for host in ctx.conn.execute(
        "SELECT * FROM netscan_hosts WHERE run_id = ?", (run_id,))}
    return run_row(row), hosts


def host_name(host: dict, naming: str) -> str:
    """The name a host found gets in an inventory: short (its NetBIOS name, or
    the first label of its DNS name), dns (the whole DNS name, or the NetBIOS
    name) or address; its address when it has no such name that Ansible and
    a path take."""
    netbios = host["netbios_name"].lower()
    dns = host["dns_name"].rstrip(".")
    choices = {"short": (netbios, dns.split(".")[0]), "dns": (dns, netbios),
               "address": ()}[naming]
    for name in choices:
        if not name or _is_address(name):
            continue
        try:
            return check_host(name)
        except BadRequest:
            continue
    return host["address"]


def inventory_text(hosts: list[dict], naming: str, group: str) -> tuple[str, list[dict]]:
    """hosts.yaml for the hosts found, and the name each got. A name taken by
    an earlier host is not given twice: the later one gets its address."""
    taken: set[str] = set()
    named = []
    for host in hosts:
        name = host_name(host, naming)
        if name.lower() in taken:
            name = host["address"]
        taken.add(name.lower())
        named.append({"name": name, "address": host["address"], "mac": host["mac"]})
    lines = ["all:"]
    indent = "  "
    if group:
        lines += ["  children:", f"    {group}:"]
        indent = "      "
    lines.append(f"{indent}hosts:" if named else f"{indent}hosts: {{}}")
    for entry in named:
        lines.append(f"{indent}  {json.dumps(entry['name'])}:")
        if entry["name"] != entry["address"]:
            lines.append(f"{indent}    ansible_host: {json.dumps(entry['address'])}")
        if entry["mac"]:
            lines.append(f"{indent}    {VARIABLE}: {json.dumps(entry['mac'])}")
        if entry["name"] == entry["address"] and not entry["mac"]:
            lines[-1] += " {}"
    return "\n".join(lines) + "\n", named


def _chosen(hosts: dict[str, dict], addresses: list[str]) -> list[dict]:
    if not addresses:
        raise BadRequest("choose the hosts: addresses")
    missing = [address for address in addresses if address not in hosts]
    if missing:
        raise BadRequest(f"not found by this scan: {', '.join(missing[:10])}")
    chosen = [hosts[address] for address in dict.fromkeys(addresses)]
    return sorted(chosen, key=lambda host: ipaddress.IPv4Address(host["address"]))


@command("netscan.inventory", role="admin", mutating=True, agent="config",
         params={
             "id": Id("The run of the scan."),
             "name": Str("The name of the new inventory.", required=True, max_length=200),
             "addresses": Strs("The hosts found to put in it, by address.",
                               max_items=scanner.MAX_ADDRESSES),
             "naming": Choice(("short", "dns", "address"), "What names the hosts: short (the"
                              " NetBIOS name, or the first label of the DNS name), dns (the"
                              " whole DNS name), or address; a host with no such name gets"
                              " its address", default="short"),
             "group": Str("A group to put every host in; none: they are hosts of all.",
                          default="", max_length=64),
         })
def netscan_inventory(ctx: Context, params: Values) -> dict:
    """Make an inventory written here from hosts a scan found: each with
    ansible_host, its address, and mac_address when the scan saw one. It is
    read once made; an agent's waits in a draft for a person to merge."""
    group = params["group"]
    if group and (not _GROUP_RE.fullmatch(group) or group in ("all", "ungrouped")):
        raise BadRequest("group must be a name of letters, digits and _, not all or ungrouped")
    run, hosts = _scan_hosts(ctx, params["id"])
    chosen = _chosen(hosts, params["addresses"])
    text, named = inventory_text(chosen, params["naming"], group)
    created = COMMANDS["inventory.create"].call(ctx, {
        "name": params["name"], "kind": "managed", "format": "yaml", "content": text})
    parse = None
    if not created.get("draft"):
        inventory = store.fetch(ctx.conn, "inventories", created["id"], "inventory")
        parse = queue_parse(ctx, inventory)
    return {"inventory": created, "hosts": named, "parse_run_id": parse,
            "subnet": run["options"].get("subnet", "")}


def _base(inventory, tree) -> PurePosixPath:
    """Where host_vars goes in the tree of an inventory: beside the source
    Ansible reads, or in it when that is a directory."""
    path = PurePosixPath(inventory["path"] or ".")
    if str(path) != "." and not (tree / path).is_dir():
        path = path.parent
    return path


def _read(tree, path: PurePosixPath) -> bytes | None:
    """A file of the tree that is a file and no link, or None."""
    target = tree / path
    if target.is_symlink() or not target.is_file():
        return None
    return target.read_bytes()


def plan_macs(tree, base: PurePosixPath, wanted: dict[str, str]) -> tuple[list, list[dict]]:
    """The operations that write mac_address for each host of WANTED (name to
    MAC address), and what becomes of each host."""
    operations, outcome = [], []
    for name, mac in sorted(wanted.items()):
        directory = base / "host_vars" / name
        ours = directory / MAC_FILE
        text = f"{VARIABLE}: {json.dumps(mac)}\n".encode()
        entry = {"host": name, "mac": mac, "path": str(ours)}
        if (tree / directory).is_symlink():
            outcome.append({**entry, "result": "skipped", "reason": f"{directory} is a link"})
            continue
        if (tree / directory).is_dir():
            others = [child for child in sorted((tree / directory).iterdir())
                      if child.name != MAC_FILE and child.is_file() and not child.is_symlink()]
            setting = [child.name for child in others if _SETS_MAC.search(child.read_bytes())]
            if setting:
                outcome.append({**entry, "result": "skipped",
                                "reason": f"{VARIABLE} is set in {directory / setting[0]}"})
            elif _read(tree, ours) == text:
                outcome.append({**entry, "result": "unchanged"})
            else:
                operations.append({"op": "write", "path": str(ours), "content": text.decode()})
                outcome.append({**entry, "result": "written"})
            continue
        found = None
        for extension in _VARS_EXTENSIONS:
            candidate = base / "host_vars" / (name + extension)
            if (tree / candidate).is_symlink() or (tree / candidate).exists():
                found = candidate
                break
        if found is not None and _read(tree, found) is None:
            outcome.append({**entry, "result": "skipped", "reason": f"{found} is not a file"})
            continue
        if found is not None and _SETS_MAC.search(_read(tree, found)):
            outcome.append({**entry, "result": "skipped",
                            "reason": f"{VARIABLE} is set in {found}"})
            continue
        if found is not None:
            moved = directory / found.name
            operations.append({"op": "move", "path": str(found), "to": str(moved)})
            entry["moved"] = {"from": str(found), "to": str(moved)}
        operations.append({"op": "write", "path": str(ours), "content": text.decode()})
        outcome.append({**entry, "result": "written"})
    return operations, outcome


@command("netscan.macs", role="admin", mutating=True, agent="config",
         params={
             "id": Id("The run of the scan."),
             "inventory_id": Id("An inventory written here."),
             "hosts": Strs("Only these hosts of the inventory, by name; all those found"
                           " otherwise.", max_items=scanner.MAX_ADDRESSES),
             "by_name": Bool("Also the hosts that the scan found by name only, not by"
                             " address."),
             "preview": Bool("Say what would be written, and write nothing."),
         })
def netscan_macs(ctx: Context, params: Values) -> dict:
    """Write mac_address, the MAC address a scan saw, for the hosts of an
    inventory written here that it found, in host_vars/HOST/fleet_mac.yaml
    beside the inventory's source, as one commit (an agent's goes to the
    draft of its session). A file host_vars/HOST.yaml already there is moved
    into host_vars/HOST/, unchanged, since Ansible would no longer read it; a
    host whose host_vars already set mac_address is left alone."""
    inventory = store.fetch(ctx.conn, "inventories", params["inventory_id"], "inventory")
    if inventory["kind"] != "managed":
        raise BadRequest("only an inventory written here: in a project's, write mac_address"
                         " in the project")
    run, hosts = _scan_hosts(ctx, params["id"])
    found: dict[str, set[str]] = {}
    for host in hosts.values():
        for known in host["known"]:
            if known["inventory_id"] == inventory["id"] and host["mac"] \
                    and (known["by"] == "address" or params["by_name"]):
                found.setdefault(known["host"], set()).add(host["mac"])
    asked = set(params["hosts"])
    outcome = [{"host": name, "mac": "", "path": "", "result": "skipped",
                "reason": "not found by this scan with a MAC address"}
               for name in sorted(asked - set(found))]
    wanted = {}
    for name, macs in found.items():
        if asked and name not in asked:
            continue
        if len(macs) > 1:
            outcome.append({"host": name, "mac": "", "path": "", "result": "skipped",
                            "reason": "found with more than one MAC address: "
                            + ", ".join(sorted(macs))})
            continue
        try:
            files.check_file_path(f"host_vars/{name}/{MAC_FILE}", "host")
        except BadRequest:
            outcome.append({"host": name, "mac": "", "path": "", "result": "skipped",
                            "reason": "its name cannot name a file"})
            continue
        wanted[name] = next(iter(macs))
    tree = files.inventory_tree(ctx, inventory)
    # An agent writes to its draft, and previews what it holds once it exists.
    place = files.place(ctx, inventory, "", writing=not params["preview"])
    if place is not None:
        tree = files.draft_tree(place)
    draft = None if params["preview"] else place
    operations, planned = plan_macs(tree, _base(inventory, tree), wanted)
    outcome = sorted(planned + outcome, key=lambda entry: entry["host"])
    answer = {"hosts": outcome, "subnet": run["options"].get("subnet", ""),
              "revision": None, "draft": None}
    if params["preview"] or not operations:
        return answer
    checked = [files.check_operation(index, item) for index, item in enumerate(operations)]
    written = sum(1 for entry in outcome if entry["result"] == "written")
    done = files.change(ctx, inventory, checked,
                        f"Write the MAC addresses of {written} hosts found on"
                        f" {answer['subnet']}", draft=draft)
    return {**answer, "revision": done["revision"], "draft": done["draft"]}


MAX_PROBE = 256
_WINRM = ("winrm", "ansible.builtin.winrm", "psrp", "ansible.builtin.psrp")
_LOCAL = ("local", "ansible.builtin.local")


def normal_mac(value: object) -> str:
    """A MAC address as aa:bb:cc:dd:ee:ff, from that or aa-bb-… (Windows); ""
    for anything else."""
    text = str(value or "").strip().lower().replace("-", ":")
    return text if _MAC_RE.fullmatch(text) and text != "00:00:00:00:00:00" else ""


def _connection_port(host: dict) -> int:
    if host.get("port"):
        return host["port"]
    return 5985 if host.get("connection", "") in _WINRM else 22


def _resolve(name: str) -> list[str]:
    try:
        found = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return list(dict.fromkeys(entry[4][0] for entry in found))


def _cmdb(ctx, inventory_id: str) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """What the CMDB last saw of the hosts of an inventory, by name (their MAC
    addresses and addresses), and every host of any inventory by MAC address."""
    of_inventory: dict[str, dict] = {}
    by_mac: dict[str, list[str]] = {}
    for row in ctx.conn.execute(
            "SELECT h.name, h.inventory_id, i.name AS inventory, c.interfaces, c.collected_at"
            " FROM cmdb_hosts c JOIN hosts h ON h.id = c.host_id"
            " JOIN inventories i ON i.id = h.inventory_id"):
        macs, addresses = set(), set()
        for nic in store.loads(row["interfaces"], []):
            if not isinstance(nic, dict):
                continue
            mac = normal_mac(nic.get("mac"))
            if mac:
                macs.add(mac)
            for address in nic.get("addresses") or []:
                addresses.add(str(address).split("/")[0])
        for mac in macs:
            by_mac.setdefault(mac, []).append(f"{row['name']} ({row['inventory']})")
        if row["inventory_id"] == inventory_id:
            of_inventory[row["name"]] = {"macs": sorted(macs), "addresses": sorted(addresses),
                                         "collected_at": row["collected_at"]}
    return of_inventory, by_mac


def findings(host: dict) -> list[str]:
    """What a probe of one host says, from the most telling: unresolved (its
    name has no address), ipv6 (it has only IPv6 addresses, not probed),
    silent (nothing answered, not even on the segment), segment_only (in the
    neighbour table, answering nothing else: on, behind a firewall),
    port_closed (it answers, but not on its connection's port),
    mac_differs (the MAC address seen is none the CMDB knows for it; mac_of
    says whose it is), address_moved (the CMDB saw it with other addresses),
    netbios_differs (it gives another name over NetBIOS)."""
    if host["unresolved"]:
        return ["unresolved"]
    if host["ipv6"]:
        return ["ipv6"]
    said = []
    answered = "tcp" in host["seen"] or "icmp" in host["seen"]
    if not host["seen"]:
        said.append("silent")
    elif not answered:
        said.append("segment_only")
    elif host["connection_port"] not in host["ports"]:
        said.append("port_closed")
    cmdb = host["cmdb"]
    if host["mac"] and cmdb and cmdb["macs"] and host["mac"] not in cmdb["macs"]:
        said.append("mac_differs")
    if cmdb and cmdb["addresses"] and host["address"] not in cmdb["addresses"]:
        said.append("address_moved")
    short = host["name"].split(".")[0].lower()
    if host["netbios_name"] and not _is_address(host["name"]) \
            and host["netbios_name"].lower() != short:
        said.append("netbios_differs")
    return said


@command("netscan.probe", role="operator",
         params={
             "inventory_id": Id("The inventory."),
             "hosts": Strs("Only these hosts of it, by name; all otherwise.", max_items=MAX_PROBE),
         })
def netscan_probe(ctx: Context, params: Values) -> dict:
    """Probe the hosts of an inventory from the controller, to tell why one
    does not answer: whether its name resolves, whether it answers an ICMP
    echo or a TCP connection on a few ports and on its connection's port, its
    names from the reverse DNS and NetBIOS, its MAC address when it is on the
    controller's own segment, and whether that is the MAC address the CMDB
    knows for it or another host's. Each host has its findings: unresolved,
    ipv6, silent, segment_only, port_closed, mac_differs, address_moved,
    netbios_differs. It changes nothing; at most 256 hosts at a time."""
    jobs.reconcile(ctx)
    inventory = store.fetch(ctx.conn, "inventories", params["inventory_id"], "inventory")
    listed = [host for host in store.loads(inventory["hosts"], []) if isinstance(host, dict)
              and host.get("connection", "") not in _LOCAL]
    if params["hosts"]:
        names = {str(host.get("name", "")) for host in listed}
        missing = [name for name in params["hosts"] if name not in names]
        if missing:
            raise BadRequest(f"not hosts of {inventory['name']} reached over the network:"
                             f" {', '.join(missing[:10])}")
        asked = set(params["hosts"])
        listed = [host for host in listed if host.get("name") in asked]
    if len(listed) > MAX_PROBE:
        raise BadRequest(f"{inventory['name']} has {len(listed)} hosts: probe at most"
                         f" {MAX_PROBE} at a time, by name")
    config = ctx.config
    ports = sorted(set(config.scan_ports) | {_connection_port(host) for host in listed})
    ports = ports[:scanner.MAX_PORTS]
    icmp = scanner.icmp_available()
    connected = [ipaddress.IPv4Network(entry["subnet"]) for entry in connected_routes()]
    cmdb, by_mac = _cmdb(ctx, inventory["id"])
    journal.send(f"probe of {len(listed)} hosts of {inventory['name']} by {ctx.caller.user}", 6,
                 FLEET_COMMAND="netscan.probe")

    def one(entry: dict) -> dict:
        name = str(entry.get("name", ""))
        target = str(entry.get("address") or name)
        addresses = [target] if _is_address(target) else _resolve(target)
        address = next((item for item in addresses if ":" not in item), "")
        host = {"name": name, "target": target, "address": address,
                "unresolved": not addresses, "ipv6": bool(addresses) and not address,
                "connection_port": _connection_port(entry),
                "seen": [], "ports": [], "mac": "", "dns_name": "", "netbios_name": "",
                "workgroup": "", "on_link": False, "self": False,
                "cmdb": cmdb.get(name), "mac_of": []}
        if not address:
            return host
        found = scanner.probe(address, ports, icmp)
        host.update(seen=found["seen"], ports=found["ports"],
                    on_link=any(ipaddress.IPv4Address(address) in net for net in connected),
                    self=scanner.is_local(address))
        if found["seen"]:
            scanner.name_host(host)
            if host.get("netbios_mac"):
                host["mac"] = normal_mac(host.pop("netbios_mac"))
        return host

    workers = max(1, min(scanner.MAX_WORKERS, scanner.MAX_SOCKETS // len(ports)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        probed = list(pool.map(one, listed))
    neighbours = _neighbours()
    if any(host["address"] in neighbours and not host["seen"] for host in probed):
        # Seen only in the table: it may be an entry the kernel has not given up yet.
        time.sleep(scanner.SETTLE)
        neighbours = _neighbours()
    for host in probed:
        mac = neighbours.get(host["address"])
        if mac:
            host["mac"] = mac
            if not host["seen"]:
                host["seen"] = ["arp"]
            elif "arp" not in host["seen"]:
                host["seen"].append("arp")
        if host["mac"]:
            host["mac_of"] = [owner for owner in by_mac.get(host["mac"], [])
                              if owner != f"{host['name']} ({inventory['name']})"]
        host["findings"] = findings(host)
    return {"inventory": {"id": inventory["id"], "name": inventory["name"]},
            "parsed_at": inventory["parsed_at"], "icmp": icmp, "ports": ports,
            "hosts": probed}


def connected_routes() -> list[dict]:
    try:
        return connected(ROUTES.read_text())
    except OSError:
        return []


def _neighbours() -> dict[str, str]:
    """Address to MAC address, from the controller's neighbour table."""
    try:
        return scanner.read_arp(scanner.ARP_TABLE.read_text(), ipaddress.IPv4Network("0.0.0.0/0"))
    except OSError:
        return {}

