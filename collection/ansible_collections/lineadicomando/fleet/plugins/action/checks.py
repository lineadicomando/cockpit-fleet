# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    """Run the checks of the catalog with the module of the node's platform:
    lineadicomando.fleet.win_checks where the shell is PowerShell, else
    lineadicomando.fleet.checks. Ansible would not choose by itself: without
    a PowerShell shell it looks a module up with no suffix, and finds either."""

    TRANSFERS_FILES = False
    _VALID_ARGS = frozenset(("checks",))

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        powershell = getattr(self._connection._shell, "SHELL_FAMILY", "") == "powershell"
        module = "lineadicomando.fleet.win_checks" if powershell else "lineadicomando.fleet.checks"
        result.update(self._execute_module(module_name=module, module_args=self._task.args,
                                           task_vars=task_vars))
        return result
