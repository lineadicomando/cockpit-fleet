# Windows nodes: history

Archive: what was tried, measured and found while reaching Windows nodes;
not needed to work on it.

**Timings**, from the sandboxed unit against a Windows 11 VM (4 vCPU,
8 GB), `test/integration/windows.sh`:

| Task | Time |
|---|---|
| fact gathering | 10 to 11 s |
| `win_command` | 4 s |
| `win_shell` (one CIM query) | 5 s |
| whole run | 20 s |

The same run launched through `fleetctl` and from the interface took 21 to
24 seconds.

**`become`** (`runas` to `SYSTEM`) tried with `win_whoami` and with the
checks of the catalog on Windows 11: it failed before any module ran with
`ansible_remote_tmp` set to `C:\Windows\Temp\ansible`, and worked with the
default.
