# Windows nodes

Reached over SSH with `ansible_shell_type: powershell`. The `ansible.windows`
collection is not part of `ansible-core`: it comes from the `ansible` package,
from a copy inside the project, or from the project's requirements file (see
"Collections from Galaxy").

The test nodes accept public keys only, so the run uses a private key stored
as a secret and passed with `--private-key`. Measured from the sandboxed unit
against a Windows 11 VM (4 vCPU, 8 GB), `test/integration/windows.sh`:

| Task | Time |
|---|---|
| fact gathering | 10 to 11 s |
| `win_command` | 4 s |
| `win_shell` (one CIM query) | 5 s |
| whole run | 20 s |

The same run launched through `fleetctl` and from the interface takes 21 to 24
seconds.

Every task on Windows costs 4 to 5 seconds whatever it does, against a fraction
of a second on Linux. A collection cycle must therefore gather everything a
Windows node has to report in one task, with one script, and not run one task
per check.

`become` on Windows is `runas`, with `ansible_become_method: runas` and
`ansible_become_user: SYSTEM` in the inventory. It fails before any module
runs when `ansible_remote_tmp` is set to a directory such as
`C:\Windows\Temp\ansible`, and works with the default (`%TEMP%`): tried with
`win_whoami` and with the checks of the catalog on Windows 11, 16 October.
Windows Update needs no `become` over SSH with a key.
