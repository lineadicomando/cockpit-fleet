"""The catalog of checks: what each one takes, what it measures, and how its
measures become a state.

A check runs on the node, in the module lineadicomando.fleet.checks (a .py
and a .ps1 with one output schema), and reports metrics. Thresholds are
applied here, when the results are taken in, so that changing one needs no
new collection and the node cannot decide whether it is in trouble.
"""

import json
import math
import re

from .errors import BadRequest

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3
STATE_NAMES = ("ok", "warning", "critical", "unknown")
# From the least to the most severe: an unknown is worse than a warning,
# better than a critical.
_SEVERITY = {OK: 0, WARNING: 1, UNKNOWN: 2, CRITICAL: 3}

_SERVICE_RE = re.compile(r"[A-Za-z0-9_@.:][A-Za-z0-9_@.:\\ -]{0,255}")
_METRIC_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")

# name -> parameters (name -> (kind, help)), default thresholds, what it is
CATALOG: dict[str, dict] = {
    "reachability": {
        "description": "Whether Ansible reaches the host. A host it does not reach has its"
                       " other checks unknown, and only this one is told about.",
        "params": {},
        "thresholds": {},
    },
    "disk": {
        "description": "Space and inodes used on each filesystem (each fixed drive on"
                       " Windows).",
        "params": {
            "mounts": ("paths", "Only these mount points (C: on Windows); all by default"),
            "exclude_types": ("names", "Filesystem types left out, beyond the pseudo ones"),
        },
        "thresholds": {"used_pct": {"warning": 85, "critical": 95},
                       "inodes_used_pct": {"warning": 85, "critical": 95}},
    },
    "cpu": {
        "description": "Processor time used, over one second (the load on Windows), and the"
                       " load averages on Linux.",
        "params": {},
        "thresholds": {"cpu_pct": {"warning": 90, "critical": 98}},
    },
    "memory": {
        "description": "Memory and swap in use.",
        "params": {},
        "thresholds": {"memory_used_pct": {"warning": 90, "critical": 97},
                       "swap_used_pct": {"warning": 80, "critical": 95}},
    },
    "services": {
        "description": "Whether the services named are running: systemd units, or Windows"
                       " services.",
        "params": {"names": ("names", "The services")},
        "required": ("names",),
        "thresholds": {"not_running": {"warning": None, "critical": 1}},
    },
    "uptime": {
        "description": "Time since the last boot, and whether a reboot is required.",
        "params": {},
        "thresholds": {"reboot_required": {"warning": 1, "critical": None}},
    },
    "certificates": {
        "description": "Days before certificates expire: files (PEM or DER), directories of"
                       " them, or a Windows store such as Cert:\\LocalMachine\\My.",
        "params": {"paths": ("paths", "Files, directories or stores")},
        "required": ("paths",),
        "thresholds": {"days_left": {"warning": 30, "critical": 7}},
    },
    "clock": {
        "description": "How far the clock of the node is from the controller's, in seconds,"
                       " network delay included.",
        "params": {},
        "thresholds": {"offset": {"warning": 10, "critical": 60}},
    },
    "cmdb": {
        "description": "What the host is made of: hardware, installed packages and services,"
                       " kept in the inventory of hosts with their changes. Warning for one"
                       " cycle when the hardware changed.",
        "params": {},
        "thresholds": {},
        "slow": True,
        "interval": 6 * 3600,
    },
    "updates": {
        "description": "Updates available, security ones apart: apt, dnf, zypper, or Windows"
                       " Update searched without installing anything.",
        "params": {"refresh": ("flag", "Refresh the package metadata first; needs become")},
        "thresholds": {"security_updates": {"warning": 1, "critical": None},
                       "updates": {"warning": None, "critical": None}},
        "slow": True,
        "interval": 6 * 3600,
    },
}

KINDS = tuple(CATALOG)
# Kinds that are not in the catalog: a playbook of the user that ends with
# fleet_result, and one run in --check --diff, whose changes are the drift.
PLAYBOOK = "playbook"
COMPLIANCE = "compliance"
OWN_KINDS = (PLAYBOOK, COMPLIANCE)
# What a compliance check measures: the tasks that would change something.
_COMPLIANCE_THRESHOLDS = {"changed": {"warning": 1, "critical": None}}


def slow(kind: str) -> bool:
    """Whether a kind goes in cycles of its own, with a longer time limit and
    a lane of the dispatch of its own, so as not to hold back the others."""
    return kind == COMPLIANCE or bool(CATALOG.get(kind, {}).get("slow"))


def catalog() -> list[dict]:
    """The catalog as the interface and agents see it."""
    return [{"name": name, "description": entry["description"],
             "params": {key: {"kind": kind, "help": text}
                        for key, (kind, text) in entry["params"].items()},
             "required": list(entry.get("required", ())),
             "thresholds": entry["thresholds"], "slow": bool(entry.get("slow")),
             "interval": entry.get("interval", 300)}
            for name, entry in CATALOG.items()]


def _item(value: object, kind: str, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or value.startswith("-") \
            or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BadRequest(f"{what}: each entry must be a line of at most 512 characters,"
                         " not beginning with -")
    if kind == "names" and not _SERVICE_RE.fullmatch(value):
        raise BadRequest(f"{what}: {value!r} is not a valid name")
    return value


def check_params(kind: str, params: object) -> dict:
    """The parameters of a check of the catalog, checked. They reach the node
    as data, never in a command line of the controller."""
    entry = CATALOG.get(kind)
    if kind in OWN_KINDS:
        # A check of the user takes what its playbook needs from the inventory.
        entry = {"params": {}}
    if entry is None:
        raise BadRequest(f"kind must be one of: {', '.join(KINDS)}")
    if not isinstance(params, dict):
        raise BadRequest("params must be an object")
    unknown = sorted(set(params) - set(entry["params"]))
    if unknown:
        raise BadRequest(f"unknown parameter of a {kind} check: {', '.join(unknown)}")
    out: dict = {}
    for name, (item_kind, _text) in entry["params"].items():
        if item_kind == "flag":
            value = params.get(name, False)
            if not isinstance(value, bool):
                raise BadRequest(f"params.{name} must be true or false")
            if value:
                out[name] = True
            continue
        value = params.get(name, [])
        if not isinstance(value, list) or len(value) > 100:
            raise BadRequest(f"params.{name} must be a list of at most 100 entries")
        items = [_item(item, item_kind, f"params.{name}") for item in value]
        if items:
            out[name] = items
    for name in entry.get("required", ()):
        if name not in out:
            raise BadRequest(f"a {kind} check needs params.{name}")
    return out


def _number(value: object, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BadRequest(f"{what} must be a number or null")
    return value


def check_thresholds(kind: str | None, thresholds: object) -> dict:
    """Thresholds by metric: a warning, a critical, either may be null. With
    both, a critical below the warning means that lower is worse."""
    if not isinstance(thresholds, dict) or len(thresholds) > 50:
        raise BadRequest("thresholds must be an object of at most 50 metrics")
    out = {}
    for metric, limits in thresholds.items():
        if not isinstance(metric, str) or not _METRIC_RE.fullmatch(metric):
            raise BadRequest(f"thresholds: {metric!r} is not the name of a metric")
        if not isinstance(limits, dict) or set(limits) - {"warning", "critical"}:
            raise BadRequest(f"thresholds.{metric} takes warning and critical")
        out[metric] = {"warning": _number(limits.get("warning"), f"thresholds.{metric}.warning"),
                       "critical": _number(limits.get("critical"),
                                           f"thresholds.{metric}.critical")}
    return out


def effective_thresholds(kind: str, own: dict) -> dict:
    """The catalog's defaults, with what the check sets in their place."""
    defaults = _COMPLIANCE_THRESHOLDS if kind == COMPLIANCE \
        else CATALOG.get(kind, {}).get("thresholds", {})
    return {**defaults, **own}


def worst(*states: int) -> int:
    return max(states, key=lambda state: _SEVERITY.get(state, 2))


def _breach(value: float, limit: float | None, lower: bool) -> bool:
    if limit is None:
        return False
    return value <= limit if lower else value >= limit


def evaluate(metrics: list[dict], thresholds: dict) -> tuple[int, list[str]]:
    """The state the metrics are in, and a line for each threshold crossed."""
    state, crossed = OK, []
    for found in metrics:
        limits = thresholds.get(found["name"])
        if not limits:
            continue
        warning, critical = limits.get("warning"), limits.get("critical")
        lower = warning is not None and critical is not None and critical < warning
        value = found["value"]
        where = f" on {found['instance']}" if found.get("instance") else ""
        unit = found.get("unit", "")
        if _breach(value, critical, lower):
            state = worst(state, CRITICAL)
            crossed.append(f"{found['name']}{where} {value}{unit} (critical {critical})")
        elif _breach(value, warning, lower):
            state = worst(state, WARNING)
            crossed.append(f"{found['name']}{where} {value}{unit} (warning {warning})")
    return state, crossed


# --- what a node sends back: read as untrusted --------------------------------------

_MAX_METRICS = 200
_MAX_DETAILS = 16 * 1024


def clean_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value if ord(char) >= 32 or char in "\t")[:limit]


def finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def clean_metrics(metrics: object) -> list[dict]:
    """The metrics of one result as a node sent them, keeping only what is
    well formed."""
    out = []
    for found in metrics if isinstance(metrics, list) else []:
        if len(out) >= _MAX_METRICS:
            break
        if not isinstance(found, dict):
            continue
        name = found.get("name")
        value = finite(found.get("value"))
        if not isinstance(name, str) or not _METRIC_RE.fullmatch(name) or value is None:
            continue
        entry: dict = {"name": name, "value": value, "unit": clean_text(found.get("unit"), 16)}
        instance = clean_text(found.get("instance"), 256)
        if instance:
            entry["instance"] = instance
        for key in ("min", "max"):
            bound = finite(found.get(key))
            if bound is not None:
                entry[key] = bound
        out.append(entry)
    return out


def clean_details(details: object) -> dict:
    if not isinstance(details, dict):
        return {}
    try:
        text = json.dumps(details, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    return details if len(text) <= _MAX_DETAILS else {"note": "details too large, left out"}


def clean_state(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and value in (OK, WARNING, CRITICAL, UNKNOWN) else UNKNOWN
