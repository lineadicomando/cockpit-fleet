# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
import re

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase

_STATES = {"ok": 0, "warning": 1, "critical": 2, "unknown": 3}
_METRIC_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _state(value):
    if isinstance(value, str) and value.strip().lower() in _STATES:
        return _STATES[value.strip().lower()]
    if isinstance(value, int) and not isinstance(value, bool) and value in _STATES.values():
        return value
    raise AnsibleActionFail("state must be ok, warning, critical, unknown, or 0 to 3")


def _number(value, what):
    if isinstance(value, str):
        # What a template gives is text: 42 stays an integer, 4.2 a float.
        try:
            value = int(value.strip())
        except ValueError:
            try:
                value = float(value)
            except ValueError:
                raise AnsibleActionFail(f"{what} must be a number") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AnsibleActionFail(f"{what} must be a number")
    return value


def _metric(entry, index):
    if not isinstance(entry, dict):
        raise AnsibleActionFail(f"metrics[{index}] must be a mapping")
    name = entry.get("name")
    if not isinstance(name, str) or not _METRIC_RE.fullmatch(name):
        raise AnsibleActionFail(f"metrics[{index}].name must be lowercase letters, digits and _")
    found = {"name": name, "value": _number(entry.get("value"), f"metrics[{index}].value"),
             "unit": str(entry.get("unit", ""))[:16]}
    if entry.get("instance") not in (None, ""):
        found["instance"] = str(entry["instance"])[:256]
    for key in ("min", "max"):
        if entry.get(key) is not None:
            found[key] = _number(entry[key], f"metrics[{index}].{key}")
    return found


class ActionModule(ActionBase):
    """Return the result of a check of the user in the contract of
    cockpit-fleet. Nothing runs on the node: this is checked and handed back,
    and cockpit-fleet applies its thresholds when it takes it in."""

    TRANSFERS_FILES = False
    _VALID_ARGS = frozenset(("state", "summary", "metrics", "details"))

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        args = self._task.args
        metrics = args.get("metrics") or []
        if not isinstance(metrics, list):
            raise AnsibleActionFail("metrics must be a list")
        details = args.get("details") or {}
        if not isinstance(details, dict):
            raise AnsibleActionFail("details must be a mapping")
        result.update(changed=False, fleet_result={
            "state": _state(args.get("state", "ok")),
            "summary": str(args.get("summary", ""))[:500],
            "metrics": [_metric(entry, index) for index, entry in enumerate(metrics)],
            "details": details,
        })
        return result
