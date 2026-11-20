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
| `test/integration/files.sh` | a project written in the interface, syntax checks, starters | yes |
| `test/integration/upload.sh` | a project uploaded as an archive, hostile archives | yes |
| `test/integration/store.sh` | the store: put and read through fleetctl, roles, a run's farm taken in, the tick's digests, many moved and deleted at once, copies (one a job), an archive, downloads from a container node, sources checked again, refused addresses, `sftp` over SSH through `fleet store sftp` | yes |
| `test/integration/galaxy.sh` | collections from a requirements file (IT_GALAXY_NET=yes for git) | yes |
| `test/integration/adhoc.sh` | ad-hoc commands, the allowlist of modules (IT_SSH_USER, IT_SSH_PASSWORD) | yes |
| `test/integration/netscan.sh` | the scan of a subnet: on a dummy interface by default, nothing leaving the machine (IT_NETSCAN_SUBNET: a subnet of the lab's instead, without the inventories), ICMP from the sandbox, refusals, a scan canceled; an inventory made from it and MAC addresses written into another, read by Ansible | yes |
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

## Running them

- **Containers**: `make ct-test DISTRO=debian|fedora|alma [TESTS="a b"]`
  builds the package of `DISTRO` and runs each script as root in a fresh
  container of it, `CT_JOBS` at a time (2), with `IT_WIPE=yes` and an
  account for `IT_SSH_USER`; other `IT_*` variables of the caller are
  passed on; logs in `it-results/DISTRO/`. `windows.sh`,
  `measure-events.sh` and `load.sh` run only when named; `lifecycle.sh`
  only through `make ct-lifecycle`, which builds an older package too
  (`CT_KEEP_PACKAGES=DIR` keeps both, to run it on a VM). It needs
  rootless podman on cgroup v2, nothing else of the host.
- **The lab**: `test/vm/lab.sh deploy TARGET` installs the package of the
  work tree, `test/vm/lab.sh it TARGET SCRIPT...` runs scripts the same way,
  logs in `it-results/vm-TARGET/` (skill `vm-lab`).
- **Another machine**: copy `test/integration` there and start one as root,
  e.g. `sudo env IT_WIPE=yes IT_SSH_USER=USER IT_SSH_PASSWORD=... sh
  integration/runs.sh`. With `systemd-run` (to outlive the session) on
  Fedora or AlmaLinux, pass `-p
  SELinuxContext=unconfined_u:unconfined_r:unconfined_t:s0` and redirect
  the output in the shell, not with `StandardOutput=`: a unit runs in
  `initrc_t`, where `ssh-keygen` cannot write in `/run`.

A controller is a machine of a target distribution with `cockpit`, the
package, `podman`, `sshpass` and `acl` (for `cli.sh`); `nodes.sh` starts
Linux nodes on podman's default network, at 10.88.0.101 onwards. Windows
nodes are reached over SSH with a key: authorise a key pair made for the
test on the node and store its private half as a credential, or as the
secret `it-win-key` that `windows.sh` expects.

The scripts that go through `fleetctl` share helpers in `lib.sh` (`admin`,
`get`, `expect_run`, `wipe`, `prepare`); a failed `expect_run` prints the
end of the run's output. With `IT_KEEP=yes`, `windows.sh` leaves its data
in place so the same run can be launched from the interface; so does
`checks.sh`, whose stand-in for the webhook then stays up (`pkill -f
notify-sinks.py` as root).

Installing on a test controller: `apt-get install ./cockpit-fleet_*.deb`,
`dnf install ./cockpit-fleet-*.noarch.rpm` (`dnf reinstall` for the same
version, which `install` skips). dpkg stops to ask when
`/etc/cockpit-fleet/config.toml` was changed there and in the package too:
put the packaged file back first, or install with `--force-confold`. After
a deploy a Cockpit tab runs the old bundle until reloaded, and a copy in
`~/.local/share/cockpit` (left by `make devel-install`) shadows the
package's.

## Where they run

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

Known failure: since 1 November `cmdb.sh` fails on Fedora in a container
(podman's netavark: "invalid namespace path" when it starts its nodes); it
passes on the other two and on machines. Run the `ct-test` of two
distributions one after the other, not together.

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

## Negative tests

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

## CI

CI runs on Gitea (`.gitea/workflows/ci.yml`): type check, build, both unit test
suites, the catalogs of the translations regenerated and compared with those
committed, ruff, and a native package build with lintian, which fails on errors
and warnings, on a Debian 13 runner (label `cockpit-fleet`); and the `.rpm`
built natively and checked with rpmlint on two more runners, Fedora 44 and
AlmaLinux 10 (`cockpit-fleet-fedora`, `cockpit-fleet-alma`), one job each.
The runners are arm64 containers where jobs run directly, as a user that
is not root; tools with a version (Node.js, ruff) are pinned in the
workflow and installed once per runner by `scripts/ci-tool.sh`.
`.github/workflows/ci.yml` runs the same steps on GitHub, each job in a
container of its distribution (`debian:trixie`, `fedora:44`,
`almalinux:10`) on `ubuntu-latest`, where the job is root and nothing
persists, so the build tools and the pinned ones are installed at each
run; the packages are kept as artifacts of the run. At a tag `vX.Y.Z`
(pushed to GitHub by Gitea's mirror) one more job, `release`, after the
others pass, checks the tag (`release.mjs verify`) and that the three
packages have its version, and publishes them with `SHA256SUMS` as the
GitHub release of the tag, notes from `release.mjs notes`; run again, it
replaces the assets. A tag made before the job existed is published by
running the workflow by hand with the input `tag` (`gh workflow run ci.yml
-f tag=vX.Y.Z`): the packages are built at the tag, the notes come from
main's `CHANGELOG.md`. The Gitea workflow has no such job. The
integration scripts are not part of CI: they need podman able to start
privileged containers, which the runner, itself a container where jobs run
as a non-root user, was not set up for.
