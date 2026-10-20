"""The inventory of hosts (the CMDB): what each host is made of, the updates
it lacks, and the drift that compliance checks find.

The checks of the kinds cmdb and updates send their data beside their
result; ingest hands it here. It is written by the nodes, so it is read as
untrusted: what is not well formed is left out, and every list is cut to
size. The last snapshot of a host is kept in normalised tables; its history
is the changes only, what came, went or changed between two snapshots.
"""

import json

from . import store
from .catalog import clean_text, finite
from .cli import Context, Id, Int, Str, Values, command

# Changes are kept this long; maintenance.tidy deletes them after.
KEEP_CHANGES = 365 * 86400
# A change of the memory below this share is the kernel keeping more or less
# for itself, not a module added or removed.
_MEMORY_SLACK = 0.05

_MAX_PACKAGES = 20000
_MAX_SERVICES = 5000
_MAX_UPDATES = 5000
_MAX_DEVICES = 64
_MAX_ADDRESSES = 32
_MAX_DIFF = 256 * 1024
_HARDWARE = ("vendor", "model", "serial", "bios", "virtual", "cpu_model")


def _text(value: object, limit: int = 256) -> str:
    return clean_text(value, limit).strip()


def _count(value: object) -> int | None:
    number = finite(value)
    return int(number) if number is not None and number >= 0 else None


def _entries(value: object, limit: int) -> list[dict]:
    return [item for item in value[:limit] if isinstance(item, dict)] \
        if isinstance(value, list) else []


def clean_cmdb(data: object) -> dict:
    """The data of a cmdb check as a node sent it, keeping what is well formed."""
    data = data if isinstance(data, dict) else {}
    found = data.get("hardware") if isinstance(data.get("hardware"), dict) else {}
    hardware = {key: _text(found.get(key)) for key in _HARDWARE}
    hardware.update(cpus=_count(found.get("cpus")), memory=_count(found.get("memory")),
                    boot_at=finite(found.get("boot_time")))
    disks = [{"name": _text(disk.get("name"), 64), "size": _count(disk.get("size")) or 0,
              "model": _text(disk.get("model"), 128)}
             for disk in _entries(data.get("disks"), _MAX_DEVICES) if _text(disk.get("name"))]
    interfaces = []
    for nic in _entries(data.get("interfaces"), _MAX_DEVICES):
        name = _text(nic.get("name"), 64)
        if not name:
            continue
        addresses = nic.get("addresses") if isinstance(nic.get("addresses"), list) else []
        interfaces.append({"name": name, "mac": _text(nic.get("mac"), 64).lower(),
                           "addresses": [_text(address, 64) for address in
                                         addresses[:_MAX_ADDRESSES] if _text(address, 64)]})
    out = {"hardware": hardware, "disks": sorted(disks, key=lambda disk: disk["name"]),
           "interfaces": sorted(interfaces, key=lambda nic: nic["name"]),
           "package_manager": _text(data.get("package_manager"), 32),
           "packages": None, "services": None}
    if isinstance(data.get("packages"), list):
        packages = {}
        for package in _entries(data["packages"], _MAX_PACKAGES):
            name = _text(package.get("name"))
            if name:
                arch = _text(package.get("arch"), 32)
                packages[(name, arch)] = _text(package.get("version"), 128)
        out["packages"] = packages
    if isinstance(data.get("services"), list):
        out["services"] = {
            _text(service.get("name")): (_text(service.get("state"), 32),
                                         _text(service.get("enabled"), 32))
            for service in _entries(data["services"], _MAX_SERVICES)
            if _text(service.get("name"))}
    return out


def _record(conn, host_id: str, now: float, area: str, name: str, before: str,
            after: str) -> None:
    conn.execute("INSERT INTO cmdb_changes (host_id, ts, area, name, before, after)"
                 " VALUES (?, ?, ?, ?, ?, ?)", (host_id, now, area, name[:256], before[:512],
                                                after[:512]))


def _memory_changed(before: int | None, after: int | None) -> bool:
    if before is None or after is None:
        return before != after
    return abs(before - after) > _MEMORY_SLACK * max(before, after)


def _gib(value: int | None) -> str:
    return "" if value is None else f"{value / 2**30:.1f} GiB"


def _hardware_changes(old, new: dict) -> list[tuple[str, str, str]]:
    """What changed of the hardware, as (what, before, after)."""
    hardware = new["hardware"]
    changes = [(key, old[key], hardware[key]) for key in _HARDWARE
               if key != "virtual" and old[key] != hardware[key]]
    if old["cpus"] != hardware["cpus"]:
        changes.append(("cpus", str(old["cpus"] or ""), str(hardware["cpus"] or "")))
    if _memory_changed(old["memory"], hardware["memory"]):
        changes.append(("memory", _gib(old["memory"]), _gib(hardware["memory"])))
    before = {disk["name"]: disk["size"] for disk in store.loads(old["disks"], [])}
    after = {disk["name"]: disk["size"] for disk in new["disks"]}
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            changes.append((f"disk {name}", _gib(before.get(name)), _gib(after.get(name))))
    before = {nic["name"]: nic["mac"] for nic in store.loads(old["interfaces"], [])}
    after = {nic["name"]: nic["mac"] for nic in new["interfaces"]}
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            changes.append((f"interface {name}", before.get(name, ""), after.get(name, "")))
    return changes


def _sync_rows(conn, host_id: str, now: float, first: bool, table: str, area: str,
               keys: tuple[str, ...], values: tuple[str, ...], new: dict) -> None:
    """Bring the rows of a host in a table in line with new, recording what
    came, went or changed, unless this is the first snapshot of the host."""
    columns = ", ".join((*keys, *values))
    old = {tuple(row[key] for key in keys): tuple(row[value] for value in values)
           for row in conn.execute(f"SELECT {columns} FROM {table} WHERE host_id = ?",  # noqa: S608
                                   (host_id,))}
    marks = ", ".join("?" for _ in range(len(keys) + len(values) + 1))
    for key in old.keys() - new.keys():
        where = " AND ".join(f"{column} = ?" for column in keys)
        conn.execute(f"DELETE FROM {table} WHERE host_id = ? AND {where}",  # noqa: S608
                     (host_id, *key))
        if not first:
            _record(conn, host_id, now, area, " ".join(filter(None, key)), " ".join(old[key]), "")
    for key, value in new.items():
        value = value if isinstance(value, tuple) else (value,)
        if key not in old:
            conn.execute(f"INSERT INTO {table} (host_id, {columns}) VALUES ({marks})",  # noqa: S608
                         (host_id, *key, *value))
            if not first:
                _record(conn, host_id, now, area, " ".join(filter(None, key)), "",
                        " ".join(value))
        elif old[key] != value:
            sets = ", ".join(f"{column} = ?" for column in values)
            where = " AND ".join(f"{column} = ?" for column in keys)
            conn.execute(f"UPDATE {table} SET {sets} WHERE host_id = ? AND {where}",  # noqa: S608
                         (*value, host_id, *key))
            if not first:
                _record(conn, host_id, now, area, " ".join(filter(None, key)),
                        " ".join(old[key]), " ".join(value))


def store_cmdb(ctx: Context, host_id: str, data: object, now: float) -> list[str]:
    """Keep the last snapshot of a host and its changes; returns what changed
    of its hardware, for a person."""
    conn = ctx.conn
    new = clean_cmdb(data)
    old = conn.execute("SELECT * FROM cmdb_hosts WHERE host_id = ?", (host_id,)).fetchone()
    # The updates may have come first: then nothing is known to compare with.
    first = old is None or old["collected_at"] is None
    hardware = new["hardware"]
    changes = [] if first else _hardware_changes(old, new)
    for what, before, after in changes:
        _record(conn, host_id, now, "hardware", what, before or "", after or "")
    if not first:
        before = {nic["name"]: nic["addresses"] for nic in store.loads(old["interfaces"], [])}
        for nic in new["interfaces"]:
            if nic["name"] in before and before[nic["name"]] != nic["addresses"]:
                _record(conn, host_id, now, "network", nic["name"],
                        " ".join(before[nic["name"]]), " ".join(nic["addresses"]))
    values = {**hardware, "disks": json.dumps(new["disks"]),
              "interfaces": json.dumps(new["interfaces"]), "collected_at": now}
    if new["packages"] is not None:
        values["package_manager"] = new["package_manager"]
    if old is None:
        conn.execute("INSERT INTO cmdb_hosts (host_id) VALUES (?)", (host_id,))
    marks = ", ".join(f"{column} = ?" for column in values)
    conn.execute(f"UPDATE cmdb_hosts SET {marks} WHERE host_id = ?",  # noqa: S608
                 (*values.values(), host_id))
    if new["packages"] is not None:
        _sync_rows(conn, host_id, now, first, "cmdb_packages", "package", ("name", "arch"),
                   ("version",), new["packages"])
    if new["services"] is not None:
        # A service that stops or starts is a matter for the checks: only
        # services that come or go, or are enabled or not, are changes.
        _sync_rows(conn, host_id, now, first, "cmdb_services", "service", ("name",),
                   ("enabled",), {(name,): enabled for name, (_state, enabled)
                                  in new["services"].items()})
        conn.executemany("UPDATE cmdb_services SET state = ? WHERE host_id = ? AND name = ?",
                         [(state, host_id, name) for name, (state, _enabled)
                          in new["services"].items()])
    return [f"{what} {before or '-'} → {after or '-'}" for what, before, after in changes]


def clean_updates(data: object) -> dict:
    data = data if isinstance(data, dict) else {}
    updates = {}
    for update in _entries(data.get("updates"), _MAX_UPDATES):
        name = _text(update.get("name"))
        if name:
            updates[name] = (_text(update.get("current"), 128),
                             _text(update.get("available"), 128),
                             1 if update.get("security") is True else 0,
                             _text(update.get("source"), 256))
    return {"manager": _text(data.get("manager"), 32), "updates": updates}


def store_updates(ctx: Context, host_id: str, data: object, now: float) -> None:
    """Keep the updates a host lacks, replacing those it lacked before."""
    conn = ctx.conn
    new = clean_updates(data)
    if conn.execute("SELECT 1 FROM cmdb_hosts WHERE host_id = ?", (host_id,)).fetchone() is None:
        conn.execute("INSERT INTO cmdb_hosts (host_id) VALUES (?)", (host_id,))
    conn.execute("UPDATE cmdb_hosts SET updates_at = ?, update_manager = ? WHERE host_id = ?",
                 (now, new["manager"], host_id))
    conn.execute("DELETE FROM cmdb_updates WHERE host_id = ?", (host_id,))
    conn.executemany("INSERT INTO cmdb_updates (host_id, name, current, available, security,"
                     " source) VALUES (?, ?, ?, ?, ?, ?)",
                     [(host_id, name, *values) for name, values in new["updates"].items()])


def store_drift(ctx: Context, host_id: str, check_id: str, changes: list[dict],
                now: float) -> None:
    """Keep what the last cycle of a compliance check would have changed on a
    host, cut to size."""
    kept, size = [], 0
    for change in changes:
        size += len(change.get("diff", "")) + len(change.get("task", ""))
        if size > _MAX_DIFF:
            kept.append({"task": "", "diff": "", "note": "more changes, left out"})
            break
        kept.append(change)
    ctx.conn.execute("INSERT INTO drift (host_id, check_id, ts, changes) VALUES (?, ?, ?, ?)"
                     " ON CONFLICT (host_id, check_id) DO UPDATE SET ts = excluded.ts,"
                     " changes = excluded.changes",
                     (host_id, check_id, now, json.dumps(kept, ensure_ascii=False)))


def prune(ctx: Context, now: float) -> int:
    return ctx.conn.execute("DELETE FROM cmdb_changes WHERE ts < ?",
                            (now - KEEP_CHANGES,)).rowcount


# --- commands ---------------------------------------------------------------------------

def _hardware(row) -> dict:
    if row["collected_at"] is None and row["updates_at"] is None:
        return {}
    return {
        **{key: row[key] for key in (*_HARDWARE, "cpus", "memory", "boot_at", "collected_at",
                                     "package_manager", "updates_at", "update_manager")},
        "disks": store.loads(row["disks"], []), "interfaces": store.loads(row["interfaces"], []),
    }


_LIST_QUERY = """
    SELECT h.id, h.name, h.inventory_id, h.platform, h.os, h.facts, h.groups, h.reachable,
           i.name AS inventory, c.*,
           (SELECT count(*) FROM cmdb_packages p WHERE p.host_id = h.id) AS packages,
           (SELECT count(*) FROM cmdb_services s WHERE s.host_id = h.id) AS services,
           (SELECT count(*) FROM cmdb_updates u WHERE u.host_id = h.id) AS updates,
           (SELECT count(*) FROM cmdb_updates u WHERE u.host_id = h.id AND u.security = 1)
               AS security_updates,
           (SELECT max(s.hard_state) FROM check_states s JOIN checks k ON k.id = s.check_id
             WHERE s.host_id = h.id AND k.kind = 'compliance' AND k.enabled = 1
               AND s.state IS NOT NULL) AS compliance
    FROM hosts h JOIN inventories i ON i.id = h.inventory_id
    LEFT JOIN cmdb_hosts c ON c.host_id = h.id
"""


def _summary(row) -> dict:
    facts = store.loads(row["facts"], {})
    compliance = row["compliance"]
    return {
        "id": row["id"], "name": row["name"], "inventory_id": row["inventory_id"],
        "inventory": row["inventory"], "groups": store.loads(row["groups"], []),
        "platform": row["platform"], "os": row["os"], "os_id": facts.get("os_id", ""),
        "os_version": facts.get("os_version", ""), "kernel": facts.get("kernel", ""),
        "arch": facts.get("arch", ""),
        "reachable": None if row["reachable"] is None else bool(row["reachable"]),
        "hardware": _hardware(row) if row["host_id"] else {},
        "packages": row["packages"], "services": row["services"], "updates": row["updates"],
        "security_updates": row["security_updates"],
        # The worst hard state of its compliance checks: 0 compliant.
        "compliant": None if compliance is None else compliance == 0,
    }


@command("cmdb.list", role="viewer",
         params={
             "inventory_id": Id(required=False),
         })
def cmdb_list(ctx: Context, params: Values) -> dict:
    """The hosts with what they are made of: system, hardware, how many packages,
    services and updates (security ones apart), and whether they are compliant."""
    rows = ctx.conn.execute(
        _LIST_QUERY + " WHERE (? IS NULL OR h.inventory_id = ?)"
        " ORDER BY h.name COLLATE NOCASE, i.name",
        (params["inventory_id"], params["inventory_id"])).fetchall()
    return {"hosts": [_summary(row) for row in rows]}


@command("cmdb.get", role="viewer",
         params={
             "host_id": Id(),
             "changes": Int(0, 5000, "How many of the last changes", default=500),
         })
def cmdb_get(ctx: Context, params: Values) -> dict:
    """What one host is made of: hardware, packages, services, updates it lacks
    and the last changes."""
    conn = ctx.conn
    row = conn.execute(_LIST_QUERY + " WHERE h.id = ?", (params["host_id"],)).fetchone()
    if row is None:
        store.fetch(conn, "hosts", params["host_id"], "host")
    host_id = row["id"]
    packages = conn.execute("SELECT name, version, arch FROM cmdb_packages WHERE host_id = ?"
                            " ORDER BY name, arch", (host_id,)).fetchall()
    services = conn.execute("SELECT name, state, enabled FROM cmdb_services WHERE host_id = ?"
                            " ORDER BY name", (host_id,)).fetchall()
    updates = conn.execute("SELECT name, current, available, security, source FROM cmdb_updates"
                           " WHERE host_id = ? ORDER BY security DESC, name",
                           (host_id,)).fetchall()
    changes = conn.execute("SELECT ts, area, name, before, after FROM cmdb_changes"
                           " WHERE host_id = ? ORDER BY id DESC LIMIT ?",
                           (host_id, params["changes"])).fetchall()
    return {**_summary(row),
            "package_list": [dict(item) for item in packages],
            "service_list": [dict(item) for item in services],
            "update_list": [{**dict(item), "security": bool(item["security"])}
                            for item in updates],
            "changes": [dict(item) for item in changes]}


@command("cmdb.packages", role="viewer",
         params={
             "name": Str("Part of the name of a package", required=True, max_length=128),
             "inventory_id": Id(required=False),
         })
def cmdb_packages(ctx: Context, params: Values) -> dict:
    """Which hosts have a package, and which version: by part of its name."""
    pattern = params["name"].strip().replace("\\", "\\\\").replace("%", "\\%") \
        .replace("_", "\\_")
    rows = ctx.conn.execute(
        "SELECT p.name, p.version, p.arch, h.id AS host_id, h.name AS host, i.name AS inventory"
        " FROM cmdb_packages p JOIN hosts h ON h.id = p.host_id"
        " JOIN inventories i ON i.id = h.inventory_id"
        " WHERE p.name LIKE ? ESCAPE '\\' AND (? IS NULL OR h.inventory_id = ?)"
        " ORDER BY p.name, h.name COLLATE NOCASE LIMIT 5001",
        (f"%{pattern}%", params["inventory_id"], params["inventory_id"])).fetchall()
    return {"packages": [dict(row) for row in rows[:5000]], "more": len(rows) > 5000}


@command("drift.get", role="operator",
         params={
             "host_id": Id(),
             "check_id": Id("Only this compliance check", required=False),
         })
def drift_get(ctx: Context, params: Values) -> dict:
    """What the compliance checks of a host would change, with the diffs, as of
    their last cycle. They may hold the content of files, so a viewer does not
    see them."""
    store.fetch(ctx.conn, "hosts", params["host_id"], "host")
    rows = ctx.conn.execute(
        "SELECT d.*, c.name AS check_name FROM drift d JOIN checks c ON c.id = d.check_id"
        " WHERE d.host_id = ? AND (? IS NULL OR d.check_id = ?) ORDER BY c.name COLLATE NOCASE",
        (params["host_id"], params["check_id"], params["check_id"])).fetchall()
    return {"drift": [{"check_id": row["check_id"], "check": row["check_name"], "ts": row["ts"],
                       "changes": store.loads(row["changes"], [])} for row in rows]}
