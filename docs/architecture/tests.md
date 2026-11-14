# Tests

| What | Where | Runs on |
|---|---|---|
| Frontend unit tests | `test/*.test.ts` | development host, CI |
| Backend unit tests | `backend/tests/` | development host, CI |
| Negative tests over the whole registry | `backend/tests/test_negative.py` | development host, CI |

Integration scripts run as root on a controller with the package installed
(a container of `make ct-test`, a VM of the lab, or another machine: see
below). Those that wipe delete the database, runs and projects, and need
`IT_WIPE=yes`; `IT_SSH_USER` and `IT_SSH_PASSWORD` name an account of the
controller that accepts password logins over SSH.

| Script | What it shows (variables it takes, time) | Wipes |
|---|---|---|
| `test/integration/privileges.sh` | what the service account can reach |  |
| `test/integration/sandbox.sh` | run sandbox, secrets, SSH (IT_SSH_USER, IT_SSH_PASSWORD) |  |
| `test/integration/roles.sh` | roles, audit, sudoers (creates and removes test accounts) |  |
| `test/integration/negative.sh` | hostile input to every command as each role, the caller's environment, secrets typed in clear | yes |
| `test/integration/runs.sh` | a whole run through fleetctl | yes |
| `test/integration/files.sh` | a project written in the interface, syntax checks | yes |
| `test/integration/upload.sh` | a project uploaded as an archive, hostile archives | yes |
| `test/integration/store.sh` | the store: put and read through fleetctl, roles, a run's farm taken in, the tick's digests, many moved and deleted at once, copies (one a job), an archive, downloads from a container node, sources checked again, refused addresses | yes |
| `test/integration/galaxy.sh` | collections from a requirements file (IT_GALAXY_NET=yes for git) | yes |
| `test/integration/adhoc.sh` | ad-hoc commands, the allowlist of modules (IT_SSH_USER, IT_SSH_PASSWORD) | yes |
| `test/integration/inventories.sh` | inventory plugins, directories of sources, scripts off and on | yes |
| `test/integration/mcp.sh` | fleet-mcp: tools by role, ceiling, profiles, autonomy, an agent's ad-hoc confirmed, via in the audit, forced-command key | yes |
| `test/integration/chat.sh` | fleet-chat with fake-agent.py: start, limit on sessions, tools by role | yes |
| `test/integration/cli.sh` | fleet at a terminal: setup, launch, exit statuses, roles, secrets on stdin | yes |
| `test/integration/workflows.sh` | workflows: an approval at a step, a confirmation by who launched it, set_stats passed on, resume, cancel | yes |
| `test/integration/schedules.sh` | the timer, ten schedules on one minute, missed times, overlap (about 5 min) | yes |
| `test/integration/notify.sh` | notifications to stand-ins of the four channels, a retry by the tick (about 3 min) | yes |
| `test/integration/checks.sh` | checks on container nodes (podman), a playbook of the user, a full disk told about, a cycle falling behind (about 10 min) | yes |
| `test/integration/cmdb.sh` | the CMDB, updates and drift of container nodes (podman), changes by hand (about 4 min) | yes |
| `test/integration/measure-events.sh` | callback cost on N simulated hosts |  |
| `test/integration/load.sh` | real check cycles (podman) on IT_LOAD_HOSTS aliases and IT_LOAD_SSH nodes for IT_LOAD_MINUTES, the interface timed | yes |
| `test/integration/multihost.sh` | five container nodes (podman): vaults, passphrase key, key changes | yes |
| `test/integration/windows.sh` | Windows node over SSH, a run and the checks (IT_WIN_HOST, IT_WIN_USER, IT_WIN_KEY) | yes |
| `test/integration/nodes.sh` | the container nodes themselves (podman): `up N KEYS`, `recreate I KEYS`, `down` |  |
| `test/integration/lifecycle.sh` | upgrade, removal, purge (IT_PKG_OLD, IT_PKG_NEW; no package installed), through `make ct-lifecycle` |  |

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
