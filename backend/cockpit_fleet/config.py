"""/etc/cockpit-fleet/config.toml: limits set by the machine's administrator.

A missing file means defaults. An unknown key is an error rather than being
ignored, so that a typo does not silently leave a limit at its default.
"""

import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .errors import Unavailable

# An archive reaches fleetctl whole, on its input: this bounds its memory.
MAX_UPLOAD_MB = 50

_PROXY_RE = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?")


@dataclass(frozen=True)
class Config:
    max_concurrent_runs: int = 4
    default_forks: int = 5
    max_forks: int = 50
    local_project_roots: tuple[str, ...] = ()
    max_upload_mb: int = 20
    proxy: str = ""

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


def parse(text: str) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"not valid TOML: {error}") from error
    unknown = sorted(set(data) - {"runs", "projects", "network"})
    if unknown:
        raise ConfigError(f"unknown section: {', '.join(unknown)}")

    runs = _section(data, "runs", {"max_concurrent", "default_forks", "max_forks"})
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

    return Config(
        max_concurrent_runs=_int(runs, "runs", "max_concurrent", Config.max_concurrent_runs, 1, 64),
        default_forks=default_forks,
        max_forks=max_forks,
        local_project_roots=tuple(_root(root) for root in roots),
        max_upload_mb=_int(projects, "projects", "max_upload_mb", Config.max_upload_mb,
                           1, MAX_UPLOAD_MB),
        proxy=proxy,
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
