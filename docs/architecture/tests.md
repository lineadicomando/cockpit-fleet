# Tests

| What | Where | Runs on |
|---|---|---|
| Frontend unit tests | `test/*.test.ts` | development host, CI |
| Backend unit tests | `backend/tests/` | development host, CI |
| Negative tests over the whole registry | `backend/tests/test_negative.py` | development host, CI |
| The same on the installed package, through sudo | `test/integration/negative.sh` | controller, as root; wipes the database |
| Privilege boundaries | `test/integration/privileges.sh` | controller, as root |
| Sandbox, secrets, SSH | `test/integration/sandbox.sh` | controller, as root |
| Roles, audit, sudoers | `test/integration/roles.sh` | controller, as root |
| A whole run through fleetctl | `test/integration/runs.sh` | controller, as root; wipes the database |
| Projects written in the interface | `test/integration/files.sh` | controller, as root; wipes the database |
| Projects uploaded as an archive | `test/integration/upload.sh` | controller, as root; wipes the database |
| Collections from a requirements file | `test/integration/galaxy.sh` | controller, as root; wipes the database |
| Ad-hoc commands | `test/integration/adhoc.sh` | controller, as root; wipes the database |
| Inventory plugins, directories, scripts | `test/integration/inventories.sh` | controller, as root; wipes the database |
| Agents: fleet-mcp, ceiling, tools, ssh | `test/integration/mcp.sh` | controller, as root; wipes the database |
| Chat: fleet-chat, limit on sessions, tools | `test/integration/chat.sh` | controller, as root; wipes the database |
| The command fleet for a terminal | `test/integration/cli.sh` | controller, as root; wipes the database |
| Schedules and the timer | `test/integration/schedules.sh` | controller, as root; wipes the database |
| Notifications, with stand-ins of the four channels | `test/integration/notify.sh` | controller, as root; wipes the database |
| Checks of the hosts, a full disk told about, a cycle falling behind | `test/integration/checks.sh` | controller with podman, as root; wipes the database |
| Several Linux nodes | `test/integration/multihost.sh` | controller with podman, as root; wipes the database |
| Callback cost | `test/integration/measure-events.sh` | controller, as root |
| Load: real cycles on many hosts, the interface timed | `test/integration/load.sh` | controller with podman, as root; wipes the database |
| Windows node over SSH | `test/integration/windows.sh` | controller, as root; wipes the database |

The controller is a container or a machine. `make ct-test`
(`scripts/ct-test.sh`, `test/containers/`) builds an image of Debian 13,
Fedora 44 or AlmaLinux 10 with systemd as PID 1, what the package needs and
podman for the nodes, installs the package in it, and runs each script in a
fresh container, so that scripts that wipe the database run side by side.
Two things make a container behave as a machine here. It is privileged,
within the user's namespace: unprivileged, the run unit fails at `NAMESPACE`,
since systemd may neither make the namespaces nor mount a new `/proc` for
`PrivatePIDs`, and it ignores `PrivateIPC` with only a warning. And `/` is
made a shared mount at boot: systemd mounts the credentials of a unit in a
namespace of its own and moves them into `/run/credentials`, which reaches
the unit only through shared propagation; on podman's private `/` a run got
no secret, with no error.

What a container leaves to machines: SELinux, which belongs to the host's
kernel (a container reports it disabled); the kernel itself, which is the
host's; the interface in a browser; Windows nodes. Its nodes, containers in
the container, read the host's `/proc/meminfo` and `/proc/stat`, which is
why `checks.sh` gives the memory and processor checks
no thresholds: the state it expects says that the module answered, whatever
the load of the host.

The containers run in UTC, which the test machines did not, and that found a
bug: `systemd-analyze calendar` prints a time "(in UTC)" only when the local
zone has an offset, and the schedules read only that line, so on a
controller in UTC no schedule was ever due.

The machines are VMs dedicated to the project and built from scratch by
`test/vm/` (`lab.sh`, skill `vm-lab`): a controller of each distribution and
a Windows 11 node, on a libvirt network of their own, each address reserved
for the VM's MAC. A controller starts from the distribution's cloud image,
verified against its checksum file, with cloud-init only for the name, the
time zone and the admin account; the packages come from a script run over
SSH, where its errors show. The controllers run in Europe/Rome, not UTC, so
that between containers and machines both cases are covered. The Windows
node is installed by Windows Setup with an answer file, on a SATA disk and
an e1000e NIC, which need no driver from outside the ISO; its first logon
installs OpenSSH Server. Each VM has one snapshot, `baseline`, internal to
its disk and without the package, so that a test starts from a known
system with the package of the commit under test (`lab.sh deploy`). What
identifies the VMs is tracked (`test/vm/lab.conf`); what belongs to one
workstation, the password and the path of the Windows ISO, is in `.env`.

The negative tests take the registry as a whole, so that a command added
later is covered without a test of its own. A table in the test gives every
command its least role and whether it is audited as a change: a command
added, or a role lowered, fails until the table says so. Every command is
then called below its role, by an approver alone, for an agent above the
ceiling, and with a "via" that would claim more, and must be refused, with
its handler never reached and the refusal audited. A fuzzer gives each
parameter of each command, and each leaf of its objects (the commands with
several forms in each of them), values of the wrong type, out of bounds,
too long, and strings made to become an option, leave a directory, run a
shell or render a template, all aimed at a directory nothing may write to;
the commands run against a controller with one of everything, so that an id
reaches past its lookup. What must not happen: an internal error, a write in
that directory, one of those strings as an argument of its own before `--`
in any command line spawned (git really runs), a redacted value in the
audit trail or the journal. The same is done to `spec.json` for each
builder of `runner.py`, since `fleet-run` reads it back: every value is
checked again, and no command line gets an option or a path outside the
run. Secrets are planted where they live (the store, the channels', the
waiting answers, a run's `creds/`), every command is called by each role
allowed to, and none answers with one, nor does the journal or any file of
the database hold it. `fleet-unit` and `fleet-secret` are loaded from
`backend/libexec` and given every form of their arguments. They found that
nesting deep enough to stop the JSON parser, and objects of the wrong type
in the links of a workflow or in the host keys of an import, gave an
internal error; that a secret typed in clear was refused but recorded with
the call; that `hostkey.add` took a pattern that trusted its key for every
host; and that `SUDO_UID` was read by `int()`. `negative.sh` gives every
command, through sudo as each role, an empty object, one hostile value per
parameter and input that is not an object, and checks what the caller's
environment and working directory cannot bring into fleetctl.

CI runs on Gitea (`.gitea/workflows/ci.yml`): type check, build, both unit test
suites, ruff, and a native package build with lintian, which fails on errors
and warnings, on a Debian 13 runner; and the `.rpm` built natively and
checked with rpmlint on two more runners, Fedora 44 and AlmaLinux 10, one
job each. The runners are containers where jobs run directly, as a user
that is not root; tools with a version (Node.js, ruff) are pinned in the
workflow and installed once per runner by `scripts/ci-tool.sh`.
`.github/workflows/ci.yml` runs the same steps on GitHub, each job in a
container of its distribution (`debian:trixie`, `fedora:44`,
`almalinux:10`) on `ubuntu-latest`, where the job is root and nothing
persists, so the build tools and the pinned ones are installed at each
run; the packages are kept as artifacts of the run. The
integration scripts are not part of CI: they need podman able to start
privileged containers, which the runner, itself a container where jobs run
as a non-root user, was not set up for.
