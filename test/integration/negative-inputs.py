#!/usr/bin/python3 -I
"""Hostile input to every fleetctl command, through sudo, as a user of each
role: what the unit tests give the registry, on the installed package.

    negative-inputs.py CANARY_DIR

Prints PASS and FAIL lines like lib.sh, and exits 1 when something failed.
Run as root, with the accounts itviewer, itoperator and itadmin in place,
and config.toml enabling agents up to admin, in the profiles AGENT_PROFILES.
"""

import concurrent.futures
import json
import os
import subprocess
import sys

sys.path.insert(0, "/usr/lib/cockpit-fleet")

from cockpit_fleet import cli, profiles  # noqa: E402
from cockpit_fleet.auth import ROLES  # noqa: E402

FLEETCTL = "/usr/libexec/cockpit-fleet/fleetctl"
USERS = {"viewer": "itviewer", "operator": "itoperator", "admin": "itadmin"}
CANARY_DIR = sys.argv[1]
CANARY = os.path.join(CANARY_DIR, "PWNED")
# What negative.sh enables in config.toml; the first is the default.
AGENT_PROFILES = ("observer", "diagnostic")
STRINGS = ["../" * 24 + CANARY.lstrip("/"), "-PWNED", f"$(touch {CANARY})", f"x\ntouch {CANARY}",
           "{{ lookup('pipe', 'touch " + CANARY + "') }}"]
WRONG = {"string": 1, "integer": "1", "boolean": "true", "id": "../x", "strings": [1],
         "objects": ["x"], "object": [], "choice": "PWNED"}

failed = False


def report(ok: bool, what: str, problems: list[str]) -> None:
    global failed
    if ok:
        print(f"PASS  {what}")
        return
    failed = True
    print(f"FAIL  {what}: {len(problems)}")
    for problem in problems[:20]:
        print(f"      | {problem}")


def call(user: str, name: str, raw: bytes) -> dict:
    argv = ["/usr/bin/sudo", "-u", user, "/usr/bin/sudo", "-n", "-u", "cockpit-fleet",
            FLEETCTL, name]
    done = subprocess.run(argv, input=raw, capture_output=True, timeout=180, check=False)
    # A command that streams out (store.read) writes bytes, not an answer, and
    # its answer to stderr when it fails.
    streams = cli.COMMANDS[name].stream == "out" if name in cli.COMMANDS else False
    try:
        answer = json.loads(done.stderr if streams and done.returncode == 1 else done.stdout)
        if isinstance(answer, dict) and "ok" in answer:
            return answer
    except ValueError:
        pass
    if streams and done.returncode == 0:
        return {"ok": True, "data": None}
    message = done.stderr.decode(errors="replace").strip()[:200]
    return {"ok": False, "error": {"code": "no answer", "message": message}}


def broken(answer: dict) -> bool:
    return not answer["ok"] and answer["error"]["code"] in ("internal", "no answer")


def run_all(cases: list[tuple[str, str, bytes, str]]) -> list[tuple[str, dict]]:
    """(user, command, input, label) -> (label, answer), six at a time."""
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        futures = [(label, pool.submit(call, user, name, raw))
                   for user, name, raw, label in cases]
        return [(label, future.result()) for label, future in futures]


commands = cli.load_commands()

# --- roles: each command, each role, nothing but an empty object ------------------
cases = [(USERS[role], name, b"{}", f"{name} as {role}")
         for name in sorted(commands) for role in ROLES]
problems = []
for label, answer in run_all(cases):
    name, role = label.split(" as ")
    below = ROLES.index(role) < ROLES.index(commands[name].role)
    if below and (answer["ok"] or answer["error"]["code"] != "forbidden"):
        problems.append(f"{label}: not refused: {answer}")
    elif not below and broken(answer):
        problems.append(f"{label}: {answer['error']}")
report(not problems,
       f"every command refused below its role, and answered at it ({len(cases)} calls)", problems)

# --- hostile values: each parameter of each command, as an administrator ----------
cases = []
for name in sorted(commands):
    for key, field in commands[name].params.items():
        values = [WRONG[field.kind]] + (STRINGS if field.kind in ("string", "id") else [])
        if field.kind in ("strings", "objects", "object"):
            values += [[STRINGS[0]], {STRINGS[1]: STRINGS[2]}]
        for value in values:
            cases.append(("itadmin", name, json.dumps({key: value}).encode(),
                          f"{name} {key}={json.dumps(value)[:50]}"))
problems = [f"{label}: {answer['error']}" for label, answer in run_all(cases) if broken(answer)]
report(not problems, f"no hostile value breaks a command ({len(cases)} calls)", problems)

# --- as an agent: held to its profile, whatever it says it is ----------------------
said = (None, "observer", "diagnostic", "administrator", "Observer", ["administrator"])
cases = []
for name in sorted(commands):
    for profile in said:
        params = {"via": "mcp:it", **({} if profile is None else {"profile": profile})}
        cases.append(("itadmin", name, json.dumps(params).encode(),
                      f"{name} profile={json.dumps(profile)}"))
problems = []
for (_, name, raw, _), (label, answer) in zip(cases, run_all(cases), strict=True):
    spec = commands[name]
    profile = json.loads(raw).get("profile", AGENT_PROFILES[0])
    allowed = ((spec.tool or name == "mcp.tools") and profile in AGENT_PROFILES
               and profiles.allows(profile, profiles.call_class(name, spec.agent, {}, ())))
    if not allowed and (answer["ok"] or answer["error"]["code"] != "forbidden"):
        problems.append(f"{label}: not refused: {answer}")
    elif allowed and (broken(answer) or answer.get("error", {}).get("code") == "forbidden"):
        problems.append(f"{label}: {answer['error']}")
report(not problems, f"an agent gets only what its profile allows ({len(cases)} calls)",
       problems)

# --- input that is not a JSON object, as a viewer ---------------------------------
garbage = [b"[]", b"\xff\xfe", b"[" * 200_000, b'{"a": "' + b"x" * (2 * 1024 * 1024) + b'"}',
           b'{"via": "mcp\\nx"}', b"\x00"]
cases = [("itviewer", name, raw, f"{name} {raw[:12]!r}")
         for name in sorted(commands) for raw in garbage]
problems = [f"{label}: {answer}" for label, answer in run_all(cases)
            if answer["ok"] or broken(answer)]
report(not problems, f"what is not a JSON object is refused ({len(cases)} calls)", problems)

written = os.listdir(CANARY_DIR)
report(not written, "nothing was written where the values pointed", written)
sys.exit(1 if failed else 0)
