# Windows nodes

Reached over SSH with `ansible_shell_type: powershell`, never WinRM (rule
10 of `CLAUDE.md`). The `ansible.windows` collection is not part of
`ansible-core`: it comes from the `ansible` package, from a copy inside the
project, or from the project's requirements file ([Projects](projects.md),
"Collections from Galaxy"); the checks of the catalog and `net_probe` need
none of it.

The test nodes accept public keys only, so the run uses a private key stored
as a secret and passed with `--private-key` (`test/integration/windows.sh`).

Every task on Windows costs 4 to 5 seconds whatever it does, against a
fraction of a second on Linux (fact gathering 10 to 11 s, a whole short run
about 20 s). A collection cycle must therefore gather everything a Windows
node has to report in one task, with one script, and not run one task per
check ([Checks](checks.md), "One task per host"). The measures:
[history](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/windows.md).

`become` on Windows is `runas`, with `ansible_become_method: runas` and
`ansible_become_user: SYSTEM` in the inventory. It fails before any module
runs when `ansible_remote_tmp` is set to a directory such as
`C:\Windows\Temp\ansible`, and works with the default (`%TEMP%`).
Windows Update needs no `become` over SSH with a key.
