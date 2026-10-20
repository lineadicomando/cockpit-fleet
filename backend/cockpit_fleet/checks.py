"""Checks: the hosts of the inventories, the checks applied to their groups,
and the state of each check on each host.

The tick of the scheduler plans a cycle: what is due, grouped by inventory,
credential and become, in shards of hosts, each shard one job of the kind
"check" whose playbook runs every check of a host in one task. When the job
ends, reconcile() hands its events to ingest(), which turns the results into
states: soft until a problem has been seen max_attempts times, then hard,
and a hard change is what notifications are about.

The slow kinds (the CMDB, updates, compliance) go in cycles of their own,
with a longer time limit; what they collect beyond a state is kept by cmdb.
"""

import json
import shutil
import traceback
from collections import defaultdict

from . import catalog, cmdb, jobs, journal, metrics, notify, store
from .catalog import COMPLIANCE, CRITICAL, OK, OWN_KINDS, PLAYBOOK, STATE_NAMES, UNKNOWN, WARNING
from .cli import Bool, Choice, Context, Id, Int, Obj, Str, Values, command
from .errors import BadRequest, Conflict, FleetError
from .resources import credential_of_kind, place_inventory
from .validators import check_group, check_inventory_host, check_relative_path

# Raw states kept to tell whether a check flaps, how many it takes before
# that is said (a problem and its recovery are not flapping), and the share
# of changes among them that starts and ends it.
_HISTORY = 20
_FLAP_MIN = 10
_FLAP_START = 0.5
_FLAP_END = 0.25
# Inventories with checks are listed again this often, for hosts that came or went.
REFRESH = 3600
# Check cycles are kept this long, then removed with their directory.
KEEP_CYCLES = 24 * 3600
# A check due this little after a tick goes with it: the next time is counted
# from when its cycle was planned, on the tick, and the timer is this precise.
SLACK = 10


# --- the host registry ----------------------------------------------------------------

def sync_hosts(ctx: Context, inventory_id: str | None, listing: list[dict]) -> None:
    """Bring the hosts of an inventory in line with what its job listed."""
    if inventory_id is None:
        return
    conn = ctx.conn
    now = store.now()
    existing = {row["name"]: row["id"] for row in conn.execute(
        "SELECT id, name FROM hosts WHERE inventory_id = ?", (inventory_id,))}
    names = set()
    for host in listing:
        name = host["name"]
        names.add(name)
        values = {"address": host.get("address", ""), "groups": json.dumps(host.get("groups", []))}
        if name in existing:
            store.update(conn, "hosts", existing[name], values, "host")
        else:
            store.insert(conn, "hosts", {"id": store.new_id("h"), "inventory_id": inventory_id,
                                         "name": name, "created_at": now, **values}, "host")
    for name, host_id in existing.items():
        if name not in names:
            conn.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    sync_states(ctx)


def _members(host_group: str, groups: list[str]) -> bool:
    return host_group == "all" or host_group in groups


def sync_states(ctx: Context) -> None:
    """One state for each check on each host of its group, and no other."""
    conn = ctx.conn
    now = store.now()
    hosts = defaultdict(list)
    for row in conn.execute("SELECT id, inventory_id, groups FROM hosts"):
        hosts[row["inventory_id"]].append((row["id"], store.loads(row["groups"], [])))
    for check in conn.execute("SELECT id, inventory_id, host_group FROM checks").fetchall():
        wanted = {host_id for host_id, groups in hosts[check["inventory_id"]]
                  if _members(check["host_group"], groups)}
        have = {row["host_id"] for row in conn.execute(
            "SELECT host_id FROM check_states WHERE check_id = ?", (check["id"],))}
        for host_id in wanted - have:
            conn.execute("INSERT INTO check_states (host_id, check_id, next_at) VALUES (?, ?, ?)",
                         (host_id, check["id"], now))
        for host_id in have - wanted:
            conn.execute("DELETE FROM check_states WHERE host_id = ? AND check_id = ?",
                         (host_id, check["id"]))


# --- the state engine ---------------------------------------------------------------

def _flapping(history: list[int], was: bool) -> bool:
    if len(history) < _FLAP_MIN:
        return False
    changes = sum(1 for before, after in zip(history, history[1:], strict=False)
                  if before != after)
    share = changes / (len(history) - 1)
    if share >= _FLAP_START:
        return True
    return was and share > _FLAP_END


def apply(ctx: Context, row, state: int, summary: str, metrics: list, details: dict,
          now: float, suppressed: bool = False, since: float | None = None,
          at_once: bool = False) -> None:
    """Record one result of a check on a host. row is its state joined with
    the check (interval, retry_interval, max_attempts).

    A problem is soft until it has been seen max_attempts times in a row,
    and is checked again at the retry interval meanwhile; a recovery is hard
    at once. A result suppressed because the host could not be reached
    changes neither the hard state nor the count. A problem at_once is hard
    at once: one that lasts a single cycle, such as a change of hardware.
    """
    hard, kind, attempt = row["hard_state"], row["state_type"], row["attempt"]
    if suppressed:
        pass
    elif state == OK:
        hard, kind, attempt = OK, "hard", 0
    elif (hard != OK and kind == "hard") or at_once:
        # Already a problem: a change between problems is hard at once.
        hard, kind, attempt = state, "hard", row["max_attempts"]
    else:
        attempt = min(attempt + 1, row["max_attempts"])
        if attempt >= row["max_attempts"]:
            hard, kind = state, "hard"
        else:
            kind = "soft"

    history = store.loads(row["history"], [])
    if not suppressed:
        history = [*history, state][-_HISTORY:]
    flapping = _flapping(history, bool(row["flapping"]))
    values = {
        "state": state, "hard_state": hard, "state_type": kind, "attempt": attempt,
        "summary": summary[:1000], "metrics": json.dumps(metrics),
        "details": json.dumps(details, ensure_ascii=False, default=str),
        "checked_at": now, "run_id": None, "history": json.dumps(history),
        "flapping": int(flapping),
        # From when the cycle was planned, so that it keeps to the ticks.
        "next_at": (since or now) + (row["retry_interval"] if kind == "soft"
                                     else row["interval"]),
    }
    if hard != row["hard_state"]:
        values["changed_at"] = now
    if hard == OK:
        values.update(ack_by="", ack_at=None, ack_comment="")
    if state != row["state"] or hard != row["hard_state"] or kind != row["state_type"]:
        ctx.conn.execute(
            "INSERT INTO state_changes (host_id, check_id, ts, state, state_type, summary)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (row["host_id"], row["check_id"], now, state, kind, summary[:500]))
    marks = ", ".join(f"{column} = ?" for column in values)
    ctx.conn.execute(f"UPDATE check_states SET {marks} WHERE host_id = ? AND check_id = ?",  # noqa: S608
                     (*values.values(), row["host_id"], row["check_id"]))


_STATE_ROW = (
    "SELECT s.*, c.interval, c.retry_interval, c.max_attempts, c.kind, c.thresholds,"
    " c.name AS check_name FROM check_states s JOIN checks c ON c.id = s.check_id")


def judge(kind: str, own_thresholds: dict, result: dict, host_time: object,
          received: object) -> tuple[int, str, list, dict]:
    """The state of one result a node sent, with the thresholds applied."""
    state = catalog.clean_state(result.get("state"))
    summary = catalog.clean_text(result.get("summary"), 500)
    metrics = catalog.clean_metrics(result.get("metrics"))
    details = catalog.clean_details(result.get("details"))
    if kind == "clock":
        node = catalog.finite(host_time)
        here = catalog.finite(received)
        if node is None or here is None:
            return UNKNOWN, "the node did not say its time", [], details
        offset = round(abs(node - here), 2)
        metrics = [{"name": "offset", "value": offset, "unit": "s"}]
        details = {"node_time": node, "received": here}
        summary = f"{offset} s from the controller"
    found, crossed = catalog.evaluate(metrics, catalog.effective_thresholds(kind, own_thresholds))
    if crossed:
        summary = "; ".join(crossed) + (f" ({summary})" if summary else "")
    return catalog.worst(state, found), summary, metrics, details


def _events(directory) -> dict[str, dict]:
    """What each host answered in a cycle: its last result event."""
    found: dict[str, dict] = {}
    try:
        with open(directory / "events.jsonl", encoding="utf-8", errors="replace") as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("event") in (
                        "host_ok", "host_failed", "host_unreachable"):
                    found[str(event.get("host"))] = event
    except OSError:
        pass
    return found


def _playbook_events(directory, check_id: str) -> dict[str, dict]:
    """What each host answered to a playbook of the user, in the shape the
    catalog's task gives: the last fleet_result of the host, unless a task
    failed on it or it could not be reached."""
    found: dict[str, dict] = {}
    try:
        with open(directory / "events.jsonl", encoding="utf-8", errors="replace") as source:
            lines = source.readlines()
    except OSError:
        return found
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind, host = event.get("event"), str(event.get("host"))
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if kind == "host_unreachable" or (kind == "host_failed" and not event.get("ignored")):
            if found.get(host, {}).get("event") not in ("host_unreachable", "host_failed"):
                found[host] = {"event": kind, "ts": event.get("ts"), "result": result}
        elif kind == "host_ok" and isinstance(result.get("fleet_result"), dict):
            if found.get(host, {}).get("event") in ("host_unreachable", "host_failed"):
                continue
            answer = {**result["fleet_result"], "id": check_id}
            found[host] = {"event": "host_ok", "ts": event.get("ts"),
                           "result": {"fleet_results": [answer]}}
        elif kind == "host_ok" and host not in found:
            found[host] = {"event": "host_ok", "ts": event.get("ts"), "result": {}}
    return found


def _compliance_events(directory, check_id: str) -> dict[str, dict]:
    """What each host answered to a compliance playbook run in --check --diff:
    the tasks that would change something, with their diffs, unless a task
    failed on it or it could not be reached."""
    found: dict[str, dict] = {}
    tasks: dict[str, str] = {}
    items: dict[tuple, list[str]] = defaultdict(list)
    try:
        with open(directory / "events.jsonl", encoding="utf-8", errors="replace") as source:
            lines = source.readlines()
    except OSError:
        return found
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind, host = event.get("event"), str(event.get("host"))
        uuid = str(event.get("task_uuid", ""))
        if kind == "task_start":
            tasks[uuid] = catalog.clean_text(event.get("task"), 200)
            continue
        if kind not in ("host_ok", "host_skipped", "host_failed", "host_unreachable",
                        "item_ok"):
            continue
        if found.get(host, {}).get("event") in ("host_unreachable", "host_failed"):
            continue
        diff = event.get("diff") if isinstance(event.get("diff"), str) else ""
        diff = "".join(char for char in diff[:64 * 1024] if ord(char) >= 32 or char in "\t\n")
        if kind == "item_ok":
            if event.get("changed") is True and diff:
                items[(host, uuid)].append(diff)
            continue
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if kind == "host_unreachable" or (kind == "host_failed" and not event.get("ignored")):
            found[host] = {"event": kind, "ts": event.get("ts"), "result": result}
            continue
        entry = found.setdefault(host, {"event": "host_ok", "ts": event.get("ts"),
                                        "changes": []})
        if kind == "host_ok" and event.get("changed") is True:
            text = "\n".join([*items.pop((host, uuid), []), diff]).strip("\n")
            entry["changes"].append({"task": tasks.get(uuid, ""), "diff": text})
    for entry in found.values():
        if entry["event"] != "host_ok":
            continue
        changes = entry["changes"]
        count = len(changes)
        summary = (f"{count} task{'s' if count != 1 else ''} would change"
                   if count else "compliant: nothing would change")
        entry["result"] = {"fleet_results": [{
            "id": check_id, "state": OK, "summary": summary,
            "metrics": [{"name": "changed", "value": count, "unit": ""}],
            "details": {"tasks": [change["task"] for change in changes][:100]}}]}
    return found


def ingest(ctx: Context, run, directory, error: str = "") -> None:
    """Turn what a check cycle left into states. Only the checks planned for
    a host in this cycle are taken, and the host is the one Ansible names in
    the event, never one that a node writes."""
    conn = ctx.conn
    now = store.now()
    since = run["created_at"]
    try:
        spec = json.loads((directory / "spec.json").read_text())
    except (OSError, ValueError):
        spec = {}
    plan = spec.get("plan", {}) if isinstance(spec, dict) else {}
    if spec.get("playbook"):
        ids = {entry.get("id") for entries in plan.values() for entry in entries}
        read = _compliance_events if spec.get("compliance") else _playbook_events
        events = read(directory, next(iter(ids))) if len(ids) == 1 else {}
    else:
        events = _events(directory)
    hosts = {row["name"]: row for row in conn.execute(
        "SELECT id, name FROM hosts WHERE inventory_id = ?", (run["inventory_id"],))}
    for name, entries in plan.items():
        host = hosts.get(name)
        if host is None:
            continue
        rows = {row["check_id"]: row for row in conn.execute(
            _STATE_ROW + " WHERE s.host_id = ? AND s.run_id = ?", (host["id"], run["id"]))}
        event = events.get(name)
        status = event["event"] if event else ""
        result = event.get("result", {}) if event else {}
        if not isinstance(result, dict):
            result = {}
        if status == "host_ok":
            info = result.get("fleet_host") if isinstance(result.get("fleet_host"), dict) else {}
            if info:
                facts = {key: catalog.clean_text(info.get(key), 200) for key in
                         ("platform", "os", "os_id", "os_version", "kernel", "arch")}
                conn.execute("UPDATE hosts SET platform = ?, os = ?, facts = ?, facts_at = ?,"
                             " reachable = 1 WHERE id = ?",
                             (facts["platform"], facts["os"], json.dumps(facts), now, host["id"]))
            else:
                # A playbook of the user says nothing of what the host is.
                conn.execute("UPDATE hosts SET reachable = 1 WHERE id = ?", (host["id"],))
            answers = {item.get("id"): item for item in result.get("fleet_results", [])
                       if isinstance(item, dict)}
        else:
            conn.execute("UPDATE hosts SET reachable = ? WHERE id = ?",
                         (0 if status == "host_unreachable" else 1 if status else None,
                          host["id"]))
            answers = {}
        message = catalog.clean_text(result.get("msg"), 500) or error or "no result for this host"
        for entry in entries:
            row = rows.get(entry.get("id"))
            if row is None:
                continue  # changed or removed while the cycle ran
            if status == "host_ok" and entry["id"] in answers:
                answer = answers[entry["id"]]
                state, summary, values, details = judge(
                    row["kind"], store.loads(row["thresholds"], {}), answer, info.get("time"),
                    event.get("ts"))
                changed = _keep(ctx, row, host["id"], answer, event, now)
                if changed:
                    state = catalog.worst(state, WARNING)
                    summary = "hardware changed: " + "; ".join(changed)[:400] + \
                        (f" ({summary})" if summary else "")
                apply(ctx, row, state, summary, values, details, now, since=since,
                      at_once=bool(changed))
                metrics.record(ctx, host["id"], row["check_id"], values,
                               metrics.when(event.get("ts"), since, now))
            elif status == "host_ok":
                missing = ("the playbook gave no result: it ends with"
                           " lineadicomando.fleet.fleet_result" if row["kind"] == PLAYBOOK
                           else "the node sent no result for this check")
                apply(ctx, row, UNKNOWN, missing, [], {}, now, since=since)
            elif status == "host_unreachable":
                if row["kind"] == "reachability":
                    apply(ctx, row, CRITICAL, f"unreachable: {message}", [], {}, now,
                          since=since)
                else:
                    apply(ctx, row, UNKNOWN, "the host is unreachable", [], {}, now,
                          suppressed=True, since=since)
            else:
                apply(ctx, row, UNKNOWN, message, [], {}, now, since=since)
    # Whatever this cycle held and did not answer is free for the next one.
    for row in conn.execute(_STATE_ROW + " WHERE s.run_id = ?", (run["id"],)).fetchall():
        apply(ctx, row, UNKNOWN, error or "the cycle ended without a result", [], {}, now)
    notify_states(ctx, now)


def _keep(ctx: Context, row, host_id: str, answer: dict, event: dict, now: float) -> list[str]:
    """Keep what a slow kind collected beyond its state; returns what changed
    of the hardware, if anything."""
    if row["kind"] == "cmdb" and isinstance(answer.get("cmdb"), dict):
        return cmdb.store_cmdb(ctx, host_id, answer["cmdb"], now)
    if row["kind"] == "updates" and isinstance(answer.get("updates"), dict):
        cmdb.store_updates(ctx, host_id, answer["updates"], now)
    elif row["kind"] == COMPLIANCE:
        cmdb.store_drift(ctx, host_id, row["check_id"], event.get("changes", []), now)
    return []


# --- planning ---------------------------------------------------------------------

def _queue_cycle(ctx: Context, inventory, credential_id: str | None, become: bool,
                 plan: dict[str, list[dict]], check=None) -> str:
    """Prepare and queue one check cycle; returns its run. With check, a
    check of the user: its playbook, from its project. A cycle of a slow
    kind has a longer time limit and a lane of the dispatch of its own."""
    project = None
    if check is not None:
        project = store.fetch(ctx.conn, "projects", check["project_id"], "project")
    elif inventory["kind"] == "file":
        project = store.fetch(ctx.conn, "projects", inventory["project_id"], "project")
    kinds = {entry["check"] for entries in plan.values() for entry in entries}
    slow = any(catalog.slow(kind) for kind in kinds)
    spec: dict = {"kind": "check", "plan": plan,
                  "timeout": ctx.config.checks_slow_timeout if slow
                  else ctx.config.checks_timeout,
                  "forks": min(ctx.config.checks_forks, ctx.config.max_forks), "become": become,
                  "vaults": []}
    if check is not None:
        spec["playbook"] = check["playbook"]
        if check["kind"] == COMPLIANCE:
            spec["compliance"] = True
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        if project:
            jobs.snapshot(jobs.project_tree(project, ctx.config), directory / "project")
            jobs.place_collections(project, directory, required=check is not None)
        else:
            (directory / "project").mkdir(mode=0o700)
        spec.update(place_inventory(ctx, inventory, directory,
                                    project["id"] if check is not None else None))
        if credential_id:
            machine = store.fetch(ctx.conn, "credentials", credential_id, "credential")
            spec["user"] = machine["username"]
            jobs.add_secret(directory, credential_id, "private_key", "private_key")
            jobs.add_secret(directory, credential_id, "key_passphrase", "key_passphrase")
            jobs.add_secret(directory, credential_id, "password", "connection_password")
            jobs.add_secret(directory, credential_id, "become_password", "become_password")
        if inventory["vault_credential_id"]:
            vault = store.fetch(ctx.conn, "credentials", inventory["vault_credential_id"],
                                "credential")
            jobs.add_secret(directory, vault["id"], "vault_password", "vault_password_0")
            spec["vaults"].append({"file": "vault_password_0", "id": vault["vault_id"]})
        jobs.write_known_hosts(ctx.conn, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    count = len(plan)
    what = check["name"] if check is not None else inventory["name"]
    jobs.queue(ctx, {"id": run_id, "kind": "check",
                     "title": f"{what}: {count} host{'s' if count != 1 else ''}",
                     "inventory_id": inventory["id"],
                     "project_id": project["id"] if project else None,
                     "options": json.dumps({"hosts": count, "slow": slow,
                                            "checks": sum(len(e) for e in plan.values())})},
               spec, directory)
    return run_id


def plan(ctx: Context, now: float) -> list[str]:
    """Queue a cycle for what is due; returns the runs queued."""
    conn = ctx.conn
    # A state held by a cycle that is no longer queued or running is free again.
    conn.execute("UPDATE check_states SET run_id = NULL WHERE run_id IS NOT NULL AND run_id NOT IN"
                 " (SELECT id FROM runs WHERE status IN ('queued', 'running'))")
    rows = conn.execute(
        "SELECT s.host_id, s.check_id, h.name AS host, h.inventory_id, c.kind, c.params,"
        " c.machine_credential_id, c.become FROM check_states s"
        " JOIN hosts h ON h.id = s.host_id JOIN checks c ON c.id = s.check_id"
        " WHERE c.enabled = 1 AND s.run_id IS NULL AND (s.next_at IS NULL OR s.next_at <= ?)"
        " ORDER BY s.next_at, h.name", (now + SLACK,)).fetchall()
    groups: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        try:
            check_inventory_host(row["host"])
        except BadRequest:
            state = conn.execute(_STATE_ROW + " WHERE s.host_id = ? AND s.check_id = ?",
                                 (row["host_id"], row["check_id"])).fetchone()
            apply(ctx, state, UNKNOWN, "a host by this name cannot be checked", [], {}, now)
            continue
        # A check of the user is a playbook of its own: it goes alone; a slow
        # kind goes with its own kind only.
        own = row["check_id"] if row["kind"] in OWN_KINDS \
            else row["kind"] if catalog.slow(row["kind"]) else None
        key = (row["inventory_id"], row["machine_credential_id"], bool(row["become"]), own)
        groups[key][row["host"]].append(row)
    queued = []
    size = ctx.config.checks_shard_size
    for (inventory_id, credential_id, become, own), by_host in groups.items():
        inventory = store.fetch(conn, "inventories", inventory_id, "inventory")
        check = store.fetch(conn, "checks", own, "check") \
            if own and own not in catalog.KINDS else None
        names = sorted(by_host)
        for start in range(0, len(names), size):
            shard = {name: by_host[name] for name in names[start:start + size]}
            cycle_plan = {name: [{"id": row["check_id"], "check": row["kind"],
                                  "params": store.loads(row["params"], {})} for row in entries]
                          for name, entries in shard.items()}
            held = [(row["host_id"], row["check_id"]) for entries in shard.values()
                    for row in entries]
            try:
                run_id = _queue_cycle(ctx, inventory, credential_id, become, cycle_plan, check)
            except (FleetError, OSError) as failure:
                for host_id, check_id in held:
                    row = conn.execute(_STATE_ROW + " WHERE s.host_id = ? AND s.check_id = ?",
                                       (host_id, check_id)).fetchone()
                    apply(ctx, row, UNKNOWN, f"the cycle could not be prepared: {failure}", [],
                          {}, now)
                continue
            conn.executemany(
                "UPDATE check_states SET run_id = ? WHERE host_id = ? AND check_id = ?",
                [(run_id, host_id, check_id) for host_id, check_id in held])
            queued.append(run_id)
    return queued


def _refresh_inventories(ctx: Context, now: float) -> None:
    """List again, now and then, the inventories that checks use."""
    rows = ctx.conn.execute(
        "SELECT i.* FROM inventories i WHERE i.id IN (SELECT inventory_id FROM checks"
        " WHERE enabled = 1) AND (i.parsed_at IS NULL OR i.parsed_at < ?) AND NOT EXISTS"
        " (SELECT 1 FROM runs r WHERE r.kind = 'inventory' AND r.inventory_id = i.id"
        " AND r.status IN ('queued', 'running'))", (now - REFRESH,)).fetchall()
    from .resources import queue_parse
    for inventory in rows:
        try:
            queue_parse(ctx, inventory, start=False)
        except (FleetError, OSError) as failure:
            store.update(ctx.conn, "inventories", inventory["id"],
                         {"parse_error": str(failure), "parsed_at": now}, "inventory")


def prune_cycles(ctx: Context, now: float) -> int:
    """Delete the check cycles past a day, with their directory: their
    results are in the states."""
    rows = ctx.conn.execute("SELECT id FROM runs WHERE kind = 'check' AND finished_at < ?",
                            (now - KEEP_CYCLES,)).fetchall()
    for row in rows:
        shutil.rmtree(jobs.run_dir(row["id"]), ignore_errors=True)
        ctx.conn.execute("DELETE FROM runs WHERE id = ?", (row["id"],))
    return len(rows)


def cycle(ctx: Context) -> dict:
    """What the tick does for checks, after the schedules."""
    now = store.now()
    out: dict = {}
    for name, step in (("refresh", _refresh_inventories), ("notify", notify_states)):
        try:
            step(ctx, now)
        except Exception:  # noqa: BLE001  (one step must not keep the others from running)
            journal.send(f"checks: {name} failed", 3, FLEET_TRACEBACK=traceback.format_exc())
    try:
        out["queued"] = plan(ctx, now)
    except Exception:  # noqa: BLE001
        journal.send("checks: planning failed", 3, FLEET_TRACEBACK=traceback.format_exc())
    jobs.dispatch(ctx)
    return out


# --- notifications --------------------------------------------------------------------

def in_maintenance(ctx: Context, host, now: float) -> bool:
    groups = store.loads(host["groups"], [])
    for row in ctx.conn.execute(
            "SELECT host_id, host_group FROM maintenances WHERE inventory_id = ?"
            " AND starts_at <= ? AND ends_at > ?", (host["inventory_id"], now, now)):
        if row["host_id"] == host["id"] or (
                row["host_id"] is None and _members(row["host_group"], groups)):
            return True
    return False


def notify_states(ctx: Context, now: float) -> int:
    """Tell what a hard state changed to, once nothing holds it back: a
    problem, a recovery from a problem told about, a problem nobody has
    acknowledged after the reminder interval."""
    conn = ctx.conn
    reminder = ctx.config.checks_reminder_hours * 3600
    rows = conn.execute(
        "SELECT s.*, c.name AS check_name, c.kind, h.name AS host, h.inventory_id, h.groups,"
        " h.reachable, h.id AS hid, i.name AS inventory FROM check_states s"
        " JOIN checks c ON c.id = s.check_id JOIN hosts h ON h.id = s.host_id"
        " JOIN inventories i ON i.id = h.inventory_id"
        " WHERE c.enabled = 1 AND s.state_type = 'hard' AND (s.hard_state != s.notified_state"
        " OR (s.hard_state != 0 AND ? > 0 AND s.ack_by = '' AND s.notified_at < ?))",
        (reminder, now - reminder)).fetchall()
    told = 0
    for row in rows:
        if row["flapping"] or (row["kind"] != "reachability" and row["reachable"] == 0):
            continue
        if in_maintenance(ctx, {"id": row["hid"], "inventory_id": row["inventory_id"],
                                "groups": row["groups"]}, now):
            continue
        if row["hard_state"] != row["notified_state"]:
            event = "recovery" if row["hard_state"] == OK else "problem"
        else:
            event = "reminder"
        payload = {
            "event": event, "title": f"{row['check_name']} on {row['host']}",
            "state": STATE_NAMES[row["hard_state"]], "summary": row["summary"][:500],
            "host": row["host"], "check": row["check_name"], "inventory": row["inventory"],
            "host_id": row["hid"], "since": row["changed_at"],
        }
        told += notify.check_event(ctx, event, payload, row["hid"], row["check_id"],
                                   row["inventory_id"], store.loads(row["groups"], []))
        conn.execute("UPDATE check_states SET notified_state = ?, notified_at = ?"
                     " WHERE host_id = ? AND check_id = ?",
                     (row["hard_state"], now, row["host_id"], row["check_id"]))
    return told


# --- commands: checks -----------------------------------------------------------------

def _counts(ctx: Context, where: str, args: tuple) -> dict:
    counts = {"ok": 0, "warning": 0, "critical": 0, "unknown": 0, "pending": 0}
    for row in ctx.conn.execute(
            f"SELECT s.state, count(*) AS n FROM check_states s JOIN checks c ON c.id = s.check_id"  # noqa: S608
            f" JOIN hosts h ON h.id = s.host_id WHERE c.enabled = 1 AND {where}"
            " GROUP BY s.state", args):
        counts["pending" if row["state"] is None else STATE_NAMES[row["state"]]] += row["n"]
    return counts


def _check(ctx: Context, row) -> dict:
    data = {key: row[key] for key in ("id", "name", "kind", "inventory_id", "interval",
                                      "retry_interval", "max_attempts", "machine_credential_id",
                                      "project_id", "playbook", "created_by", "created_at",
                                      "updated_at")}
    data.update(group=row["host_group"], params=store.loads(row["params"], {}),
                thresholds=store.loads(row["thresholds"], {}), become=bool(row["become"]),
                enabled=bool(row["enabled"]),
                effective_thresholds=catalog.effective_thresholds(
                    row["kind"], store.loads(row["thresholds"], {})),
                states=_counts(ctx, "s.check_id = ?", (row["id"],)))
    inventory = ctx.conn.execute("SELECT name FROM inventories WHERE id = ?",
                                 (row["inventory_id"],)).fetchone()
    data["inventory"] = inventory["name"] if inventory else ""
    return data


@command("check.catalog", role="viewer")
def check_catalog(ctx: Context, params: Values) -> dict:
    """The kinds of check there are: what each takes, measures and its default thresholds."""
    return {"catalog": catalog.catalog()}


@command("check.list", role="viewer")
def check_list(ctx: Context, params: Values) -> dict:
    """The checks configured, with how many of their hosts are in each state."""
    rows = ctx.conn.execute("SELECT * FROM checks ORDER BY name COLLATE NOCASE").fetchall()
    return {"checks": [_check(ctx, row) for row in rows]}


_CHECK_PARAMS = {
    "name": Str(required=True, max_length=200),
    "inventory_id": Id("The inventory whose hosts are checked"),
    "group": Str("A group of the inventory, or all", default="all", max_length=128),
    "interval": Int(60, 7 * 86400, "Seconds between two checks", default=300),
    "retry_interval": Int(30, 86400, "Seconds between two checks while a problem is soft",
                          default=60),
    "max_attempts": Int(1, 10, "Times a problem is seen before it is hard and told about",
                        default=3),
    "params": Obj("The parameters of the kind, as check.catalog lists them"),
    "thresholds": Obj("By metric, {warning, critical}, either null; in place of the defaults"),
    "machine_credential_id": Id("How to connect, if the inventory does not say",
                                required=False),
    "become": Bool(),
    "enabled": Bool(default=True),
    "project_id": Id("For a check of the kind playbook or compliance: the project of the"
                     " playbook", required=False),
    "playbook": Str("For a check of the kind playbook: its path in the project, a playbook"
                    " that ends with lineadicomando.fleet.fleet_result; for one of the kind"
                    " compliance, a playbook run in --check --diff", max_length=1024),
}


def _playbook_values(ctx: Context, params: Values, kind: str, inventory) -> dict:
    """Where the playbook of a check of the user is; nothing for the catalog."""
    if kind not in OWN_KINDS:
        params.unused("project_id", "playbook")
        return {"project_id": None, "playbook": ""}
    project = store.fetch(ctx.conn, "projects", params.need("project_id"), "project")
    path = str(check_relative_path(params.need("playbook"), "playbook"))
    if inventory["kind"] == "file" and inventory["project_id"] != project["id"]:
        raise BadRequest("the inventory is a file of another project")
    return {"project_id": project["id"], "playbook": path}


def _check_values(ctx: Context, params: Values, kind: str) -> dict:
    inventory = store.fetch(ctx.conn, "inventories", params["inventory_id"], "inventory")
    where = _playbook_values(ctx, params, kind, inventory)
    group = check_group(params["group"].strip())
    if group != "all" and inventory["parsed_at"] is not None and not inventory["parse_error"]:
        known = {name for host in store.loads(inventory["hosts"], []) for name in host["groups"]}
        if group not in known:
            raise Conflict(f"the inventory {inventory['name']} has no group {group}")
    return {
        "name": store.check_name(params["name"]), "inventory_id": inventory["id"],
        "host_group": group, "interval": params["interval"],
        "retry_interval": min(params["retry_interval"], params["interval"]),
        "max_attempts": params["max_attempts"],
        "params": json.dumps(catalog.check_params(kind, params["params"])),
        "thresholds": json.dumps(catalog.check_thresholds(kind, params["thresholds"])),
        "machine_credential_id": credential_of_kind(ctx, params["machine_credential_id"],
                                                    "machine"),
        "become": int(params["become"]), "enabled": int(params["enabled"]),
        "updated_at": store.now(), **where,
    }


def _after_change(ctx: Context, inventory_id: str) -> None:
    """New hosts are listed first; then every host of the group has a state."""
    inventory = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    if inventory["parsed_at"] is None and not ctx.conn.execute(
            "SELECT 1 FROM runs WHERE kind = 'inventory' AND inventory_id = ?"
            " AND status IN ('queued', 'running')", (inventory_id,)).fetchone():
        from .resources import queue_parse
        queue_parse(ctx, inventory)
    sync_states(ctx)


@command("check.create", role="admin", mutating=True,
         params={"kind": Choice((*catalog.KINDS, *OWN_KINDS)), **_CHECK_PARAMS})
def check_create(ctx: Context, params: Values) -> dict:
    """Apply a check to a group of an inventory: one of the catalog, a
    playbook of a project that ends with lineadicomando.fleet.fleet_result,
    or one of the kind compliance, a playbook run in --check --diff whose
    hosts are non-compliant when a task would change something. Its hosts
    are checked from the next tick of the scheduler."""
    values = _check_values(ctx, params, params["kind"])
    check_id = store.new_id("k")
    store.insert(ctx.conn, "checks", {"id": check_id, "kind": params["kind"],
                                      "created_by": ctx.caller.user, "created_at": store.now(),
                                      **values}, "check")
    _after_change(ctx, values["inventory_id"])
    return _check(ctx, store.fetch(ctx.conn, "checks", check_id, "check"))


@command("check.update", role="admin", mutating=True, params={"id": Id(), **_CHECK_PARAMS})
def check_update(ctx: Context, params: Values) -> dict:
    """Change a check; its kind stays. New thresholds apply from the next result."""
    row = store.fetch(ctx.conn, "checks", params["id"], "check")
    values = _check_values(ctx, params, row["kind"])
    store.update(ctx.conn, "checks", row["id"], values, "check")
    if values["interval"] < row["interval"]:
        ctx.conn.execute("UPDATE check_states SET next_at = min(next_at, ?) WHERE check_id = ?",
                         (store.now() + values["interval"], row["id"]))
    _after_change(ctx, values["inventory_id"])
    return _check(ctx, store.fetch(ctx.conn, "checks", row["id"], "check"))


@command("check.delete", role="admin", mutating=True,
         params={
             "id": Id(),
         })
def check_delete(ctx: Context, params: Values) -> dict:
    """Delete a check, with its states and their history."""
    store.delete(ctx.conn, "checks", params["id"], "check")
    return {}


@command("check.run", role="operator", mutating=True,
         params={
             "id": Id("The check"),
             "host_id": Id("Only this host", required=False),
         })
def check_run(ctx: Context, params: Values) -> dict:
    """Check now, without waiting for the interval: on every host of the
    check, or on one."""
    row = store.fetch(ctx.conn, "checks", params["id"], "check")
    if not row["enabled"]:
        raise Conflict("this check is turned off")
    now = store.now()
    cursor = ctx.conn.execute(
        "UPDATE check_states SET next_at = ? WHERE check_id = ? AND (? IS NULL OR host_id = ?)"
        " AND run_id IS NULL", (now, row["id"], params["host_id"], params["host_id"]))
    if params["host_id"] and cursor.rowcount == 0:
        raise Conflict("this host is not checked by this check, or is being checked now")
    queued = plan(ctx, now)
    jobs.dispatch(ctx)
    return {"runs": queued}


# --- commands: hosts and states ---------------------------------------------------------

def _state(row, now: float, maintenance: bool) -> dict:
    return {
        "host_id": row["host_id"], "check_id": row["check_id"], "host": row["host"],
        "check": row["check_name"], "kind": row["kind"], "inventory_id": row["inventory_id"],
        "state": None if row["state"] is None else STATE_NAMES[row["state"]],
        "hard_state": STATE_NAMES[row["hard_state"]], "state_type": row["state_type"],
        "attempt": row["attempt"], "max_attempts": row["max_attempts"],
        "summary": row["summary"], "metrics": store.loads(row["metrics"], []),
        "details": store.loads(row["details"], {}), "checked_at": row["checked_at"],
        "changed_at": row["changed_at"], "next_at": row["next_at"],
        "running": row["run_id"] is not None, "flapping": bool(row["flapping"]),
        "acknowledged": bool(row["ack_by"]), "ack_by": row["ack_by"], "ack_at": row["ack_at"],
        "ack_comment": row["ack_comment"], "in_maintenance": maintenance,
        "suppressed": row["kind"] != "reachability" and row["reachable"] == 0,
    }


_STATES_QUERY = (
    "SELECT s.*, c.name AS check_name, c.kind, c.max_attempts, h.name AS host,"
    " h.inventory_id, h.groups, h.reachable FROM check_states s"
    " JOIN checks c ON c.id = s.check_id JOIN hosts h ON h.id = s.host_id WHERE c.enabled = 1")


def _states(ctx: Context, where: str = "", args: tuple = ()) -> list[dict]:
    now = store.now()
    rows = ctx.conn.execute(f"{_STATES_QUERY} {where}"  # noqa: S608
                            " ORDER BY h.name COLLATE NOCASE, c.name COLLATE NOCASE",
                            args).fetchall()
    windows: dict[str, bool] = {}
    out = []
    for row in rows:
        if row["host_id"] not in windows:
            windows[row["host_id"]] = in_maintenance(
                ctx, {"id": row["host_id"], "inventory_id": row["inventory_id"],
                      "groups": row["groups"]}, now)
        out.append(_state(row, now, windows[row["host_id"]]))
    return out


@command("state.list", role="viewer",
         params={
             "problems": Bool("Only what is not ok"),
             "inventory_id": Id(required=False),
             "check_id": Id(required=False),
             "host_id": Id(required=False),
         })
def state_list(ctx: Context, params: Values) -> dict:
    """The state of each check on each host, with counters by state."""
    jobs.reconcile(ctx)  # a cycle that ended has results to show
    where, args = [], []
    for column, name in (("h.inventory_id", "inventory_id"), ("s.check_id", "check_id"),
                         ("s.host_id", "host_id")):
        if params[name]:
            where.append(f"{column} = ?")
            args.append(params[name])
    # The counters are those of the whole selection, problems or not.
    counts = _counts(ctx, " AND ".join(where) or "1 = 1", tuple(args))
    if params["problems"]:
        where.append("s.state IS NOT NULL AND s.state != 0")
    states = _states(ctx, "".join(f" AND {part}" for part in where), tuple(args))
    return {"states": states, "counts": counts}


def _host(row, counts: dict) -> dict:
    data = {key: row[key] for key in ("id", "inventory_id", "name", "address", "platform", "os",
                                      "facts_at", "created_at")}
    data.update(groups=store.loads(row["groups"], []), facts=store.loads(row["facts"], {}),
                reachable=None if row["reachable"] is None else bool(row["reachable"]),
                inventory=row["inventory"], states=counts)
    return data


@command("host.list", role="viewer",
         params={
             "inventory_id": Id(required=False),
         })
def host_list(ctx: Context, params: Values) -> dict:
    """The hosts of the inventories, with what they are and their checks by state."""
    rows = ctx.conn.execute(
        "SELECT h.*, i.name AS inventory FROM hosts h JOIN inventories i ON i.id = h.inventory_id"
        " WHERE (? IS NULL OR h.inventory_id = ?) ORDER BY h.name COLLATE NOCASE, i.name",
        (params["inventory_id"], params["inventory_id"])).fetchall()
    counts: dict[str, dict] = defaultdict(lambda: {"ok": 0, "warning": 0, "critical": 0,
                                                  "unknown": 0, "pending": 0})
    for found in ctx.conn.execute(
            "SELECT s.host_id, s.state, count(*) AS n FROM check_states s"
            " JOIN checks c ON c.id = s.check_id WHERE c.enabled = 1 GROUP BY s.host_id, s.state"):
        key = "pending" if found["state"] is None else STATE_NAMES[found["state"]]
        counts[found["host_id"]][key] += found["n"]
    return {"hosts": [_host(row, counts[row["id"]]) for row in rows]}


@command("host.get", role="viewer",
         params={
             "id": Id(),
         })
def host_get(ctx: Context, params: Values) -> dict:
    """One host: what it is, the state of each of its checks, the last changes."""
    jobs.reconcile(ctx)
    row = ctx.conn.execute(
        "SELECT h.*, i.name AS inventory FROM hosts h JOIN inventories i ON i.id = h.inventory_id"
        " WHERE h.id = ?", (params["id"],)).fetchone()
    if row is None:
        store.fetch(ctx.conn, "hosts", params["id"], "host")
    states = _states(ctx, " AND s.host_id = ?", (row["id"],))
    counts = {"ok": 0, "warning": 0, "critical": 0, "unknown": 0, "pending": 0}
    for state in states:
        counts[state["state"] or "pending"] += 1
    changes = ctx.conn.execute(
        "SELECT x.*, c.name AS check_name FROM state_changes x JOIN checks c ON c.id = x.check_id"
        " WHERE x.host_id = ? ORDER BY x.id DESC LIMIT 100", (row["id"],)).fetchall()
    return {**_host(row, counts), "checks": states,
            "changes": [{"check_id": change["check_id"], "check": change["check_name"],
                         "ts": change["ts"], "state": STATE_NAMES[change["state"]],
                         "state_type": change["state_type"], "summary": change["summary"]}
                        for change in changes],
            "in_maintenance": in_maintenance(ctx, row, store.now())}


_STATE_KEY = {"host_id": Id(), "check_id": Id()}


def _state_row(ctx: Context, params: Values):
    row = ctx.conn.execute("SELECT * FROM check_states WHERE host_id = ? AND check_id = ?",
                           (params["host_id"], params["check_id"])).fetchone()
    if row is None:
        store.fetch(ctx.conn, "hosts", params["host_id"], "host")
        store.fetch(ctx.conn, "checks", params["check_id"], "check")
        raise Conflict("this host is not checked by this check")
    return row


@command("state.ack", role="operator", mutating=True,
         params={**_STATE_KEY, "comment": Str(default="", max_length=500)})
def state_ack(ctx: Context, params: Values) -> dict:
    """Acknowledge a problem: someone is on it. No reminder is sent while it
    lasts; it ends when the check is ok again."""
    row = _state_row(ctx, params)
    if row["hard_state"] == OK:
        raise Conflict("there is no problem to acknowledge")
    ctx.conn.execute("UPDATE check_states SET ack_by = ?, ack_at = ?, ack_comment = ?"
                     " WHERE host_id = ? AND check_id = ?",
                     (ctx.caller.user, store.now(), params["comment"].strip(), row["host_id"],
                      row["check_id"]))
    return {}


@command("state.unack", role="operator", mutating=True, params=_STATE_KEY)
def state_unack(ctx: Context, params: Values) -> dict:
    """Take back an acknowledgement."""
    row = _state_row(ctx, params)
    ctx.conn.execute("UPDATE check_states SET ack_by = '', ack_at = NULL, ack_comment = ''"
                     " WHERE host_id = ? AND check_id = ?", (row["host_id"], row["check_id"]))
    return {}


# --- commands: maintenance -------------------------------------------------------------

def _maintenance(ctx: Context, row) -> dict:
    data = {key: row[key] for key in ("id", "inventory_id", "host_id", "starts_at", "ends_at",
                                      "comment", "created_by", "created_at")}
    data["group"] = row["host_group"]
    host = ctx.conn.execute("SELECT name FROM hosts WHERE id = ?", (row["host_id"],)).fetchone()
    inventory = ctx.conn.execute("SELECT name FROM inventories WHERE id = ?",
                                 (row["inventory_id"],)).fetchone()
    data["host"] = host["name"] if host else ""
    data["inventory"] = inventory["name"] if inventory else ""
    return data


@command("maintenance.list", role="viewer")
def maintenance_list(ctx: Context, params: Values) -> dict:
    """The maintenance windows that are open or to come, and those ended in the last week."""
    rows = ctx.conn.execute("SELECT * FROM maintenances WHERE ends_at > ? ORDER BY starts_at",
                            (store.now() - 7 * 86400,)).fetchall()
    return {"maintenances": [_maintenance(ctx, row) for row in rows], "now": store.now()}


@command("maintenance.create", role="operator", mutating=True,
         params={
             "inventory_id": Id(),
             "host_id": Id("One host, or else a group", required=False),
             "group": Str("A group of the inventory, or all", default="", max_length=128),
             "starts_at": Int(0, 2**40, "When it begins, in seconds since the epoch; now"
                                        " by default"),
             "ends_at": Int(0, 2**40, "When it ends, in seconds since the epoch"),
             "comment": Str(default="", max_length=500),
         })
def maintenance_create(ctx: Context, params: Values) -> dict:
    """Open a maintenance window: nothing about the hosts in it is told while
    it lasts. Their checks still run; what is still a problem when it ends is
    told then."""
    inventory = store.fetch(ctx.conn, "inventories", params["inventory_id"], "inventory")
    group = params["group"].strip()
    host_id = params["host_id"]
    if (host_id is None) == (not group):
        raise BadRequest("name either a host or a group")
    if host_id is not None:
        host = store.fetch(ctx.conn, "hosts", host_id, "host")
        if host["inventory_id"] != inventory["id"]:
            raise BadRequest("this host is not in this inventory")
    else:
        check_group(group)
    now = store.now()
    starts = params["starts_at"] if params["starts_at"] is not None else int(now)
    ends = params["ends_at"]
    if ends is None or ends <= max(starts, now):
        raise BadRequest("ends_at must be later than now and than starts_at")
    if ends - starts > 90 * 86400:
        raise BadRequest("a maintenance window lasts at most 90 days")
    maintenance_id = store.new_id("m")
    store.insert(ctx.conn, "maintenances", {
        "id": maintenance_id, "inventory_id": inventory["id"], "host_id": host_id,
        "host_group": group if host_id is None else "", "starts_at": starts, "ends_at": ends,
        "comment": params["comment"].strip(), "created_by": ctx.caller.user,
        "created_at": now}, "maintenance")
    return _maintenance(ctx, store.fetch(ctx.conn, "maintenances", maintenance_id,
                                         "maintenance"))


@command("maintenance.delete", role="operator", mutating=True,
         params={
             "id": Id(),
         })
def maintenance_delete(ctx: Context, params: Values) -> dict:
    """Close a maintenance window, or take back one to come."""
    row = store.fetch(ctx.conn, "maintenances", params["id"], "maintenance")
    now = store.now()
    if row["starts_at"] <= now < row["ends_at"]:
        # Ended now rather than forgotten: what it held back is told at the next tick.
        ctx.conn.execute("UPDATE maintenances SET ends_at = ? WHERE id = ?", (now, row["id"]))
    else:
        store.delete(ctx.conn, "maintenances", row["id"], "maintenance")
    return {}
