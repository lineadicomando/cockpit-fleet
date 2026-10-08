# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

DOCUMENTATION = """
name: events
type: notification
short_description: Write run events as JSON lines for cockpit-fleet
description:
  - Appends one JSON object per line to a file for every playbook, play, task
    and host result, so that cockpit-fleet can show and store a run without
    parsing the human-readable output.
  - Result values are trimmed, so the file stays small on large fleets.
requirements:
  - Must be enabled, for example with C(ANSIBLE_CALLBACKS_ENABLED).
options:
  path:
    description: File the events are appended to.
    required: true
    type: path
    env:
      - name: FLEET_EVENTS_FILE
    ini:
      - section: callback_fleet_events
        key: path
  max_string:
    description: Longer strings in results are cut to this many characters.
    type: int
    default: 2048
    env:
      - name: FLEET_EVENTS_MAX_STRING
    ini:
      - section: callback_fleet_events
        key: max_string
  keep_facts:
    description: Keep C(ansible_facts) in results instead of dropping them.
    type: bool
    default: false
    env:
      - name: FLEET_EVENTS_KEEP_FACTS
    ini:
      - section: callback_fleet_events
        key: keep_facts
"""

import json
import time

from ansible.plugins.callback import CallbackBase

_MAX_ITEMS = 200
_MAX_DEPTH = 8
_DROPPED_KEYS = frozenset({"invocation", "ansible_facts"})


def _part(result, name):
    # ansible-core 2.19 made these public; older releases only have _name.
    value = getattr(result, name, None)
    return value if value is not None else getattr(result, "_" + name)


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "lineadicomando.fleet.events"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._out = None
        self._max_string = 2048
        self._keep_facts = False

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super().set_options(task_keys=task_keys, var_options=var_options, direct=direct)
        self._max_string = self.get_option("max_string")
        self._keep_facts = self.get_option("keep_facts")
        self._out = open(self.get_option("path"), "a", encoding="utf-8")  # noqa: SIM115

    def _emit(self, event, **fields):
        fields.update(event=event, ts=round(time.time(), 3))
        self._out.write(json.dumps(fields, ensure_ascii=False, default=str) + "\n")
        self._out.flush()

    def _trim(self, value, depth=0):
        if isinstance(value, str):
            if len(value) > self._max_string:
                return value[: self._max_string] + "…"
            return value
        if depth >= _MAX_DEPTH:
            return "…"
        if isinstance(value, dict):
            return {
                str(key): self._trim(item, depth + 1)
                for key, item in list(value.items())[:_MAX_ITEMS]
                if not str(key).startswith("_ansible_")
                and (key not in _DROPPED_KEYS or (key == "ansible_facts" and self._keep_facts))
            }
        if isinstance(value, (list, tuple)):
            return [self._trim(item, depth + 1) for item in value[:_MAX_ITEMS]]
        return value

    def _result(self, status, result, **extra):
        task = _part(result, "task")
        data = _part(result, "result")
        self._emit(
            "host_" + status,
            host=_part(result, "host").get_name(),
            task_uuid=str(task._uuid),
            changed=bool(data.get("changed", False)),
            result=self._trim(data),
            **extra,
        )

    def v2_playbook_on_start(self, playbook):
        self._emit("playbook_start", playbook=playbook._file_name)

    def v2_playbook_on_play_start(self, play):
        self._emit("play_start", play=play.get_name(), play_uuid=str(play._uuid))

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._emit(
            "task_start",
            task=task.get_name(),
            task_uuid=str(task._uuid),
            action=task.action,
            path=task.get_path(),
        )

    def v2_playbook_on_handler_task_start(self, task):
        self.v2_playbook_on_task_start(task, False)

    def v2_runner_on_ok(self, result):
        self._result("ok", result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._result("failed", result, ignored=bool(ignore_errors))

    def v2_runner_on_skipped(self, result):
        self._result("skipped", result)

    def v2_runner_on_unreachable(self, result):
        self._result("unreachable", result)

    def v2_playbook_on_stats(self, stats):
        hosts = {host: stats.summarize(host) for host in sorted(stats.processed)}
        self._emit("stats", hosts=hosts)
