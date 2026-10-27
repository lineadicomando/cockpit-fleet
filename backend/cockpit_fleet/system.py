"""Commands about the installation itself: identity, self-check, audit trail."""

import grp
import os
import re
import shutil
import stat
import subprocess

from . import __version__, audit, db, jobs, paths
from .auth import allowed
from .cli import COMMANDS, Context, Int, Values, command
from .errors import FleetError

# AlmaLinux 10 has 2.16; Debian 13 has 2.19.
_MIN_ANSIBLE = (2, 16)
_MIN_SYSTEMD = 257


@command("whoami", role="viewer", right="approve")
def whoami(ctx: Context, params: Values) -> dict:
    """Who is calling, with which role and rights, and the commands they may use."""
    commands = sorted(c.name for c in COMMANDS.values()
                      if allowed(ctx.caller, c.role, c.right))
    try:
        chat = bool(ctx.config.chat_agents)
    except FleetError:
        chat = False  # a broken config.toml is reported by selfcheck
    return {
        "user": ctx.caller.user,
        "role": ctx.caller.role,
        "rights": sorted(ctx.caller.rights),
        "version": __version__,
        "commands": commands,
        "chat": chat,
    }


@command("config.show", role="admin")
def config_show(ctx: Context, params: Values) -> dict:
    """The limits set in config.toml."""
    return {"file": str(ctx.config_file), "values": ctx.config.as_dict()}


@command("audit.list", role="admin",
         params={
             "limit": Int(1, 500, default=50),
             "before": Int(1, 2**62),
         })
def audit_list(ctx: Context, params: Values) -> dict:
    """The audit trail, newest first: who did what and what was refused."""
    limit = params["limit"]
    before = params["before"]
    entries = audit.entries(ctx.conn, limit, before)
    return {"entries": entries, "more": len(entries) == limit}


def _output(argv: list[str]) -> str | None:
    """First line a program prints, or None when it is missing or fails."""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=20, check=False,
            stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.splitlines()[0]


def _numbers(text: str) -> tuple[int, ...]:
    match = re.search(r"\d+(\.\d+)*", text)
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def _check_ansible() -> tuple[str, str]:
    line = _output(["/usr/bin/ansible", "--version"])
    if line is None:
        return "fail", "not found"
    return ("ok" if _numbers(line)[:2] >= _MIN_ANSIBLE else "fail"), line


def _check_systemd() -> tuple[str, str]:
    line = _output(["/usr/bin/systemctl", "--version"])
    if line is None:
        return "fail", "not found"
    return ("ok" if _numbers(line)[:1] >= (_MIN_SYSTEMD,) else "fail"), line


def _check_database(ctx: Context) -> tuple[str, str]:
    try:
        return "ok", f"schema {db.schema_version(ctx.conn)}"
    except FleetError as error:
        return "fail", str(error)


def _check_config(ctx: Context) -> tuple[str, str]:
    try:
        ctx.config  # noqa: B018  (loading it is the check)
    except FleetError as error:
        return "fail", str(error)
    return "ok", str(ctx.config_file) if ctx.config_file.exists() else "defaults"


def _check_state_dir() -> tuple[str, str]:
    for directory in (paths.STATE_DIR, paths.RUNS_DIR, paths.PROJECTS_DIR):
        if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
            return "fail", f"{directory} is not writable"
    return "ok", str(paths.STATE_DIR)


def _check_secrets_dir() -> tuple[str, str]:
    if not os.access(paths.SECRETS_DIR, os.R_OK | os.X_OK):
        return "fail", f"{paths.SECRETS_DIR} is not readable"
    if os.access(paths.SECRETS_DIR, os.W_OK):
        return "fail", f"{paths.SECRETS_DIR} is writable by the service account"
    return "ok", str(paths.SECRETS_DIR)


def _check_run_unit() -> tuple[str, str]:
    if not paths.RUN_UNIT_FILE.is_file():
        return "fail", f"{paths.RUN_UNIT_FILE} is missing"
    helper = str(paths.LIBEXEC_DIR / "fleet-unit")
    if _output(["/usr/bin/sudo", "-n", "-l", helper]) is None:
        return "fail", "the service account may not run fleet-unit through sudo"
    return "ok", paths.RUN_UNIT_FILE.name


def _check_scheduler() -> tuple[str, str]:
    result = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", paths.SCHEDULER_TIMER],
                            check=False, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        return "warn", f"{paths.SCHEDULER_TIMER} is not active: schedules launch nothing"
    return "ok", paths.SCHEDULER_TIMER


def _check_notify() -> tuple[str, str]:
    result = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", paths.NOTIFY_PATH_UNIT],
                            check=False, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        return "warn", f"{paths.NOTIFY_PATH_UNIT} is not active: notifications are not sent"
    return "ok", paths.NOTIFY_PATH_UNIT


def _check_collection() -> tuple[str, str]:
    plugin = (paths.COLLECTIONS_DIR / "ansible_collections" / "lineadicomando" / "fleet"
              / "plugins" / "callback" / "events.py")
    return ("ok", "lineadicomando.fleet") if plugin.is_file() else ("fail", f"{plugin} is missing")


# Who writes in a directory of local projects: the administrators of Fleet, as
# docs/architecture.md says ("Projects at a terminal").
_WRITERS = "cockpit-fleet-admin"


def _group(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def check_project_root(root: str) -> tuple[str, str]:
    """A directory of local projects: readable by the service, written only by
    the administrators of Fleet, since what is there is what operators launch."""
    hidden = jobs.hidden_from_units(root)
    if hidden:
        return "fail", (f"{root} is under {hidden}, which the scheduler cannot see: its"
                        " projects work when launched by hand, never when scheduled")
    try:
        status = os.stat(root)
    except OSError as error:
        return "fail", f"{root}: {error.strerror}"
    if not os.access(root, os.R_OK | os.X_OK):
        return "fail", f"{root} is not readable by the service account"
    if status.st_mode & stat.S_IWOTH:
        return "fail", f"{root}: every account may write there"
    if status.st_mode & stat.S_IWGRP and _group(status.st_gid) != _WRITERS:
        return "warn", (f"{root}: the group {_group(status.st_gid)} may write there, and what it"
                        f" writes is what operators launch ({_WRITERS} is the convention)")
    if status.st_mode & stat.S_IROTH:
        return "warn", f"{root}: every account of this machine may read the projects"
    return "ok", root


def _check_project_roots(ctx: Context) -> tuple[str, str]:
    roots = ctx.config.local_project_roots
    if not roots:
        return "ok", "none: local-directory projects are off"
    found = [check_project_root(root) for root in roots]
    order = ("fail", "warn", "ok")
    worst = min((status for status, _detail in found), key=order.index)
    return worst, "; ".join(detail for status, detail in found if status == worst) \
        if worst != "ok" else ", ".join(roots)


def _check_tool(name: str, missing: str) -> tuple[str, str]:
    path = shutil.which(name, path="/usr/bin:/bin")
    return ("ok", path) if path else (missing, "not installed")


@command("selfcheck", role="viewer")
def selfcheck(ctx: Context, params: Values) -> dict:
    """Is everything a run needs in place? One entry per check, worst first in the UI."""
    checks = [
        ("ansible", _check_ansible()),
        ("systemd", _check_systemd()),
        ("database", _check_database(ctx)),
        ("config", _check_config(ctx)),
        ("state_dir", _check_state_dir()),
        ("secrets_dir", _check_secrets_dir()),
        ("run_unit", _check_run_unit()),
        ("scheduler", _check_scheduler()),
        ("notify", _check_notify()),
        ("collection", _check_collection()),
        ("project_roots", _check_project_roots(ctx)),
        ("git", _check_tool("git", "fail")),
        # Only needed for SSH logins with a password.
        ("sshpass", _check_tool("sshpass", "warn")),
    ]
    return {"checks": [{"id": name, "status": status, "detail": detail}
                       for name, (status, detail) in checks]}
