# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    """Probe the node's network with the module of the node's platform:
    lineadicomando.fleet.win_net_probe where the shell is PowerShell, else
    lineadicomando.fleet.net_probe (see the action checks)."""

    TRANSFERS_FILES = False
    _VALID_ARGS = frozenset(("subnet", "addresses", "ports", "timeout"))

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        powershell = getattr(self._connection._shell, "SHELL_FAMILY", "") == "powershell"
        module = ("lineadicomando.fleet.win_net_probe" if powershell
                  else "lineadicomando.fleet.net_probe")
        result.update(self._execute_module(module_name=module, module_args=self._task.args,
                                           task_vars=task_vars))
        return result
