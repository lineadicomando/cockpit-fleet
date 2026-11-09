"""/etc/cockpit-fleet/config.toml: limits set by the machine's administrator.

A missing file means defaults. An unknown key is an error rather than being
ignored, so that a typo does not silently leave a limit at its default.
"""

import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .errors import Unavailable
from .profiles import DIAGNOSTIC_MODULES, PROFILES
from .validators import MODULE_RE

# An archive reaches fleetctl whole, on its input: this bounds its memory.
MAX_UPLOAD_MB = 50

_PROXY_RE = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?")
_LINK_RE = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?")
_AGENT_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_MAX_AGENTS = 8

# What an ad-hoc command may run when config.toml does not say.
ADHOC_MODULES = (
    "ansible.builtin.ping",
    "ansible.builtin.setup",
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "ansible.builtin.service_facts",
    "ansible.builtin.package_facts",
    "ansible.windows.win_ping",
    "ansible.windows.setup",
    "ansible.windows.win_command",
    "ansible.windows.win_shell",
    "ansible.windows.win_powershell",
)


@dataclass(frozen=True)
class ChatAgent:
    """An agent the chat page may start: a program that speaks the Agent
    Client Protocol on its standard input and output."""

    name: str
    command: tuple[str, ...]
    label: str = ""
    # What a user types in a terminal to log the agent in to its model.
    login: str = ""
    # The agent's own program for a terminal, which fleet assistant runs; in
    # its words {mcp_config} is a file naming fleet-mcp as the only server,
    # {allowed_tools} the tools the profile lets through with no question
    # (comma-separated) and {instructions} what the agent is told of where it
    # is. Empty: none.
    terminal: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    max_concurrent_runs: int = 4
    default_forks: int = 5
    max_forks: int = 50
    # Days the output of a run is kept after it ended; 0: always.
    runs_keep_days: int = 90
    local_project_roots: tuple[str, ...] = ()
    max_upload_mb: int = 20
    proxy: str = ""
    adhoc_modules: tuple[str, ...] = ADHOC_MODULES
    inventory_scripts: bool = False
    mcp_enabled: bool = False
    mcp_max_role: str = "operator"
    # Modules of an ad-hoc command an agent may run in a profile that only
    # diagnoses; always among adhoc_modules.
    mcp_adhoc_modules: tuple[str, ...] = DIAGNOSTIC_MODULES
    chat_agents: tuple[ChatAgent, ...] = ()
    chat_max_sessions: int = 2
    # The profiles of the assistant a user may pick, and the one an agent
    # gets when it names none (profiles.py).
    chat_profiles: tuple[str, ...] = ("observer", "assisted")
    chat_default_profile: str = "assisted"
    # What holds an agent of an autonomous profile: the runs it may launch in
    # one session, and whether they must all be dry runs (autonomy.py).
    chat_autonomous_runs: int = 10
    chat_autonomous_dry_run: bool = False
    # Where the web console is reached, for the links in notifications.
    notify_url: str = ""
    # Check cycles: how many at once (apart from runs), at most so many hosts
    # per cycle and forks within one (fewer when what cycles took says so),
    # the memory they may take together (MB; 0: half the controller's), the
    # time limit of the task on a host (and that of the slow kinds: the CMDB,
    # updates, compliance), and how often a problem nobody acknowledged is
    # told again (0: never).
    checks_max_concurrent: int = 1
    checks_shard_size: int = 50
    checks_forks: int = 20
    checks_memory_mb: int = 0
    checks_timeout: int = 60
    checks_slow_timeout: int = 900
    checks_reminder_hours: int = 24
    checks_late_after: int = 3
    # Days the values of the checks are kept as they came, then by the hour,
    # then by the day.
    metrics_raw_days: int = 14
    metrics_hourly_days: int = 90
    metrics_daily_days: int = 730
    # Hours a run waits for its approval before it expires.
    approvals_expire_hours: int = 24
    # Whether an administrator may approve their own run that waits for
    # another person.
    approvals_admins_exempt: bool = True
    # Gigabytes the files of the store may take together; 0: what the disk has.
    store_max_gb: int = 0
    # Downloads into the store running at the same time, apart from runs.
    store_max_downloads: int = 2

    def as_dict(self) -> dict:
        return asdict(self)


class ConfigError(Unavailable):
    pass


def _section(data: dict, name: str, allowed: set[str]) -> dict:
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ConfigError(f"unknown key in [{name}]: {', '.join(unknown)}")
    return section


def _int(section: dict, table: str, key: str, default: int, low: int, high: int) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{table}.{key} must be an integer between {low} and {high}")
    return value


def _root(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ConfigError(f"projects.local_roots entries must be absolute paths: {value!r}")
    path = PurePosixPath(value)
    if ".." in path.parts or len(path.parts) < 2:
        raise ConfigError(f"projects.local_roots entry is not usable: {value!r}")
    return str(path)


def _module(value: object) -> str:
    if not isinstance(value, str) or not MODULE_RE.fullmatch(value):
        raise ConfigError("adhoc.modules entries must be full module names,"
                          f" like ansible.builtin.ping: {value!r}")
    return value


def _text(section: dict, table: str, key: str, limit: int) -> str:
    value = section.get(key, "")
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ConfigError(f"{table}.{key} must be one line of at most {limit} characters")
    return value


def _agent(name: str, section: object) -> ChatAgent:
    table = f"chat.agents.{name}"
    if not _AGENT_RE.fullmatch(name):
        raise ConfigError(f"{table}: the name of an agent is made of a-z, 0-9, - and _")
    if not isinstance(section, dict):
        raise ConfigError(f"[{table}] must be a table")
    unknown = sorted(set(section) - {"command", "label", "login", "terminal"})
    if unknown:
        raise ConfigError(f"unknown key in [{table}]: {', '.join(unknown)}")
    command = _argv(section.get("command"), f"{table}.command")
    terminal = _argv(section["terminal"], f"{table}.terminal") if "terminal" in section else ()
    return ChatAgent(name, command, _text(section, table, "label", 60),
                     _text(section, table, "login", 200), terminal)


def _argv(command: object, what: str) -> tuple[str, ...]:
    # An empty word is an argument too, such as the empty list of tools.
    if (not isinstance(command, list) or not 1 <= len(command) <= 32
            or not all(isinstance(word, str) and "\x00" not in word and len(word) <= 1024
                       for word in command) or not command[0]):
        raise ConfigError(f"{what} must be a list of strings: a program and its arguments")
    if not command[0].startswith("/"):
        raise ConfigError(f"{what} must begin with the absolute path of a program")
    return tuple(command)


def parse(text: str) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"not valid TOML: {error}") from error
    unknown = sorted(set(data) - {"runs", "projects", "network", "adhoc", "inventories", "mcp",
                                     "chat", "notifications", "checks", "metrics",
                                     "approvals", "store"})
    if unknown:
        raise ConfigError(f"unknown section: {', '.join(unknown)}")

    runs = _section(data, "runs", {"max_concurrent", "default_forks", "max_forks", "keep_days"})
    max_forks = _int(runs, "runs", "max_forks", Config.max_forks, 1, 200)
    fallback = min(Config.default_forks, max_forks)
    default_forks = _int(runs, "runs", "default_forks", fallback, 1, 200)
    if default_forks > max_forks:
        raise ConfigError("runs.default_forks cannot exceed runs.max_forks")

    projects = _section(data, "projects", {"local_roots", "max_upload_mb"})
    roots = projects.get("local_roots", [])
    if not isinstance(roots, list):
        raise ConfigError("projects.local_roots must be a list")

    network = _section(data, "network", {"proxy"})
    proxy = network.get("proxy", "")
    if not isinstance(proxy, str) or (proxy and not _PROXY_RE.fullmatch(proxy)):
        raise ConfigError("network.proxy must be empty or an http(s)://host[:port] URL")

    adhoc = _section(data, "adhoc", {"modules"})
    modules = adhoc.get("modules", list(ADHOC_MODULES))
    if not isinstance(modules, list) or len(modules) > 200:
        raise ConfigError("adhoc.modules must be a list of at most 200 module names")

    inventories = _section(data, "inventories", {"scripts"})
    scripts = inventories.get("scripts", False)
    if not isinstance(scripts, bool):
        raise ConfigError("inventories.scripts must be true or false")

    mcp = _section(data, "mcp", {"enabled", "max_role", "adhoc_modules"})
    mcp_enabled = mcp.get("enabled", False)
    if not isinstance(mcp_enabled, bool):
        raise ConfigError("mcp.enabled must be true or false")
    mcp_max_role = mcp.get("max_role", Config.mcp_max_role)
    if mcp_max_role not in ("viewer", "operator", "admin"):
        raise ConfigError("mcp.max_role must be viewer, operator or admin")
    adhoc_modules = tuple(dict.fromkeys(_module(module) for module in modules))
    if "adhoc_modules" in mcp:
        diagnostic = mcp["adhoc_modules"]
        if not isinstance(diagnostic, list) or len(diagnostic) > 200:
            raise ConfigError("mcp.adhoc_modules must be a list of at most 200 module names")
        outside = [module for module in diagnostic if module not in adhoc_modules]
        if outside:
            raise ConfigError("mcp.adhoc_modules must be among adhoc.modules:"
                              f" {', '.join(map(str, outside))}")
        diagnostic = tuple(dict.fromkeys(diagnostic))
    else:
        diagnostic = tuple(module for module in DIAGNOSTIC_MODULES if module in adhoc_modules)

    chat = _section(data, "chat", {"agents", "max_sessions", "profiles", "default_profile",
                                   "autonomous_runs", "autonomous_dry_run"})
    agents = chat.get("agents", {})
    if not isinstance(agents, dict) or len(agents) > _MAX_AGENTS:
        raise ConfigError(f"chat.agents must be at most {_MAX_AGENTS} tables, one per agent")
    profiles = chat.get("profiles", list(Config.chat_profiles))
    if (not isinstance(profiles, list) or not profiles
            or any(profile not in PROFILES for profile in profiles)):
        raise ConfigError(f"chat.profiles must be a list of some of: {', '.join(PROFILES)}")
    default_profile = chat.get("default_profile", Config.chat_default_profile)
    if "default_profile" not in chat and default_profile not in profiles:
        default_profile = profiles[0]
    if default_profile not in profiles:
        raise ConfigError("chat.default_profile must be one of chat.profiles")
    dry_run = chat.get("autonomous_dry_run", False)
    if not isinstance(dry_run, bool):
        raise ConfigError("chat.autonomous_dry_run must be true or false")

    notifications = _section(data, "notifications", {"url"})
    notify_url = notifications.get("url", "")
    if not isinstance(notify_url, str) or (notify_url and not _LINK_RE.fullmatch(notify_url)):
        raise ConfigError("notifications.url must be empty or an http(s)://host[:port][/path]"
                          " URL")

    checks = _section(data, "checks", {"max_concurrent", "shard_size", "forks", "memory_mb",
                                       "timeout", "slow_timeout", "reminder_hours",
                                       "late_after"})

    metrics = _section(data, "metrics", {"raw_days", "hourly_days", "daily_days"})
    raw_days = _int(metrics, "metrics", "raw_days", Config.metrics_raw_days, 1, 366)
    hourly_days = _int(metrics, "metrics", "hourly_days", Config.metrics_hourly_days, 1, 3660)
    daily_days = _int(metrics, "metrics", "daily_days", Config.metrics_daily_days, 1, 36600)
    if not raw_days <= hourly_days <= daily_days:
        raise ConfigError("metrics: raw_days, hourly_days and daily_days must not decrease")

    approvals = _section(data, "approvals", {"expire_hours", "admins_exempt"})
    admins_exempt = approvals.get("admins_exempt", True)
    if not isinstance(admins_exempt, bool):
        raise ConfigError("approvals.admins_exempt must be true or false")

    store = _section(data, "store", {"max_gb", "max_downloads"})

    return Config(
        max_concurrent_runs=_int(runs, "runs", "max_concurrent", Config.max_concurrent_runs, 1, 64),
        default_forks=default_forks,
        max_forks=max_forks,
        runs_keep_days=_int(runs, "runs", "keep_days", Config.runs_keep_days, 0, 36600),
        local_project_roots=tuple(_root(root) for root in roots),
        max_upload_mb=_int(projects, "projects", "max_upload_mb", Config.max_upload_mb,
                           1, MAX_UPLOAD_MB),
        proxy=proxy,
        adhoc_modules=adhoc_modules,
        inventory_scripts=scripts,
        mcp_enabled=mcp_enabled,
        mcp_max_role=mcp_max_role,
        mcp_adhoc_modules=diagnostic,
        chat_agents=tuple(_agent(name, section) for name, section in agents.items()),
        chat_max_sessions=_int(chat, "chat", "max_sessions", Config.chat_max_sessions, 1, 64),
        chat_profiles=tuple(dict.fromkeys(profiles)),
        chat_default_profile=default_profile,
        chat_autonomous_runs=_int(chat, "chat", "autonomous_runs", Config.chat_autonomous_runs,
                                  1, 1000),
        chat_autonomous_dry_run=dry_run,
        notify_url=notify_url.rstrip("/"),
        checks_max_concurrent=_int(checks, "checks", "max_concurrent",
                                   Config.checks_max_concurrent, 1, 16),
        checks_shard_size=_int(checks, "checks", "shard_size", Config.checks_shard_size, 1, 1000),
        checks_forks=_int(checks, "checks", "forks", Config.checks_forks, 1, 200),
        checks_memory_mb=_int(checks, "checks", "memory_mb", Config.checks_memory_mb,
                              0, 1024 * 1024),
        checks_timeout=_int(checks, "checks", "timeout", Config.checks_timeout, 5, 600),
        checks_slow_timeout=_int(checks, "checks", "slow_timeout", Config.checks_slow_timeout,
                                 5, 3600),
        checks_reminder_hours=_int(checks, "checks", "reminder_hours",
                                   Config.checks_reminder_hours, 0, 24 * 30),
        checks_late_after=_int(checks, "checks", "late_after", Config.checks_late_after, 1, 100),
        metrics_raw_days=raw_days,
        metrics_hourly_days=hourly_days,
        metrics_daily_days=daily_days,
        approvals_expire_hours=_int(approvals, "approvals", "expire_hours",
                                    Config.approvals_expire_hours, 1, 24 * 30),
        approvals_admins_exempt=admins_exempt,
        store_max_gb=_int(store, "store", "max_gb", Config.store_max_gb, 0, 1024 * 1024),
        store_max_downloads=_int(store, "store", "max_downloads", Config.store_max_downloads,
                                 1, 16),
    )


def load(path: Path) -> Config:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return Config()
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    try:
        return parse(text)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from error
