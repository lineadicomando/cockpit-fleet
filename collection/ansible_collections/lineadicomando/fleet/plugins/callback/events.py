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
  - What a task changed, when Ansible reports it, is written as a unified diff.
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
  max_items:
    description: Longer lists and mappings in results are cut to this many entries.
    type: int
    default: 200
    env:
      - name: FLEET_EVENTS_MAX_ITEMS
    ini:
      - section: callback_fleet_events
        key: max_items
  max_diff:
    description: A longer diff of one result is cut to this many characters.
    type: int
    default: 16384
    env:
      - name: FLEET_EVENTS_MAX_DIFF
    ini:
      - section: callback_fleet_events
        key: max_diff
  secrets_dir:
    description:
      - Directory of files whose content must not appear in the events. Every
        line of at least four characters of the files named C(fleet_*) is
        replaced by asterisks wherever it occurs.
    type: path
    env:
      - name: FLEET_EVENTS_SECRETS_DIR
    ini:
      - section: callback_fleet_events
        key: secrets_dir
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

import difflib
import glob
import json
import os
import time

from ansible.plugins.callback import CallbackBase

_MAX_ITEMS = 200
_MAX_DEPTH = 8
# "diff" is dropped because it is written apart, as text: see unified_diff.
_DROPPED_KEYS = frozenset({"invocation", "ansible_facts", "diff"})
_DIFF_NOTES = {
    "dst_binary": "diff skipped: destination file appears to be binary",
    "src_binary": "diff skipped: source file appears to be binary",
    "dst_larger": "diff skipped: destination file size is greater than {}",
    "src_larger": "diff skipped: source file size is greater than {}",
}


def _diff_side(value):
    if value is None:
        return []
    if not isinstance(value, str):
        value = json.dumps(value, indent=4, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    lines = value.splitlines(True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def unified_diff(diff, context=3):
    """The "diff" of a result as text, the way "ansible-playbook --diff" prints it.

    Done here, and not by whoever reads the events, because it needs the whole
    of "before" and "after": in the event they would be cut like any string.
    """
    parts = []
    for entry in diff if isinstance(diff, (list, tuple)) else [diff]:
        if not isinstance(entry, dict):
            continue
        for key, note in _DIFF_NOTES.items():
            if key in entry:
                parts.append(note.format(entry[key]) + "\n")
        if "before" in entry and "after" in entry:
            before = entry.get("before_header")
            after = entry.get("after_header")
            parts.extend(difflib.unified_diff(
                _diff_side(entry["before"]), _diff_side(entry["after"]),
                fromfile=f"before: {before}" if before else "before",
                tofile=f"after: {after}" if after else "after",
                n=context,
            ))
        prepared = entry.get("prepared")
        if isinstance(prepared, str) and prepared:
            parts.append(prepared if prepared.endswith("\n") else prepared + "\n")
    return "".join(parts)


def read_secrets(directory):
    """The values to mask, longest first."""
    found = set()
    for path in sorted(glob.glob(os.path.join(directory, "fleet_*"))) if directory else []:
        try:
            with open(path, encoding="utf-8", errors="surrogateescape") as source:
                lines = source.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if len(line.encode("utf-8", "surrogateescape")) >= 4 and not line.startswith("-----"):
                found.add(line)
    return sorted(found, key=len, reverse=True)


def mask(value, secrets):
    """The same value with every secret replaced, in strings at any depth.

    Done on the values and not on the line of JSON, where a secret that
    happens to read like a field name would break the event.
    """
    if isinstance(value, str):
        for secret in secrets:
            if secret in value:
                value = value.replace(secret, "********")
        return value
    if isinstance(value, dict):
        return {mask(key, secrets) if isinstance(key, str) else key: mask(item, secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(item, secrets) for item in value]
    return value


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
        self._max_items = _MAX_ITEMS
        self._max_diff = 16384
        self._keep_facts = False
        self._secrets = []

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super().set_options(task_keys=task_keys, var_options=var_options, direct=direct)
        self._max_string = self.get_option("max_string")
        self._max_items = self.get_option("max_items")
        self._max_diff = self.get_option("max_diff")
        self._keep_facts = self.get_option("keep_facts")
        self._secrets = read_secrets(self.get_option("secrets_dir"))
        self._out = open(self.get_option("path"), "a", encoding="utf-8")  # noqa: SIM115

    def _emit(self, event, **fields):
        fields.update(event=event, ts=round(time.time(), 3))
        if self._secrets:
            fields = {key: mask(item, self._secrets) for key, item in fields.items()}
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
                for key, item in list(value.items())[: self._max_items]
                if not str(key).startswith("_ansible_")
                and (key not in _DROPPED_KEYS or (key == "ansible_facts" and self._keep_facts))
            }
        if isinstance(value, (list, tuple)):
            return [self._trim(item, depth + 1) for item in value[: self._max_items]]
        return value

    def _diff(self, data):
        """The fields of an event that say what the result changed, if it says so."""
        if not data.get("diff"):
            return {}
        try:
            text = unified_diff(data["diff"])
        except Exception:  # noqa: BLE001 - a module may return anything; the run goes on
            return {}
        if not text:
            return {}
        if len(text) > self._max_diff:
            return {"diff": text[: self._max_diff], "diff_cut": True}
        return {"diff": text}

    def _result(self, status, result, **extra):
        task = _part(result, "task")
        data = _part(result, "result")
        extra.update(self._diff(data))
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

    def _item(self, status, result, **extra):
        # One event per element of a loop; the task's own event still follows.
        task = _part(result, "task")
        data = _part(result, "result")
        label = data.get("_ansible_item_label", data.get("item", ""))
        extra.update(self._diff(data))
        self._emit(
            "item_" + status,
            host=_part(result, "host").get_name(),
            task_uuid=str(task._uuid),
            item=self._trim(label if isinstance(label, str) else json.dumps(label, default=str)),
            changed=bool(data.get("changed", False)),
            msg=self._trim(str(data.get("msg", ""))) if status == "failed" else "",
            **extra,
        )

    def v2_runner_item_on_ok(self, result):
        self._item("ok", result)

    def v2_runner_item_on_failed(self, result):
        self._item("failed", result)

    def v2_runner_item_on_skipped(self, result):
        self._item("skipped", result)

    def v2_playbook_on_stats(self, stats):
        hosts = {host: stats.summarize(host) for host in sorted(stats.processed)}
        # What set_stats set for the whole run (per_host: false), which a
        # step of a workflow passes on to the steps after it.
        custom = getattr(stats, "custom", None) or {}
        artifacts = custom.get("_run", {}) if isinstance(custom, dict) else {}
        if artifacts:
            self._emit("stats", hosts=hosts, artifacts=self._trim(artifacts))
        else:
            self._emit("stats", hosts=hosts)
