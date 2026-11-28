# Tests

| What | Where | Runs on |
|---|---|---|
| Frontend unit tests | `test/*.test.ts` | development host, CI |
| Frontend component tests | `test/*.test.tsx` | development host, CI |
| Backend unit tests | `backend/tests/` | development host, CI |
| Negative tests over the whole registry | `backend/tests/test_negative.py` | development host, CI |
| Scenarios in a browser ([browser-tests.md](browser-tests.md)) | `test/browser/scenarios/` | a controller of `make browser-up`, or a VM of the lab |

The component tests draw a component, or a whole page, in a document of
happy-dom (`test/dom.ts`, imported first; English strings) and drive it as
a person would, through Testing Library's roles and labels; the backend is
the double of `cockpit.spawn` (`test/mocks/cockpit.ts`), so a test sees
what the page sent to `fleetctl` or `fleet-secret`, on stdin and in argv,
and gives the session the commands of a role. The loader of the tests
(`test/cockpit-loader.mjs`, `cockpit-resolve.mjs`) turns `.tsx` into
JavaScript with esbuild, as the bundle does, takes JSON imported without
attributes and gives PatternFly's style sheets as nothing. `cleanup()` after
each test: `node:test` does not call it.

Integration scripts run as root on a controller with the package installed
(a container of `make ct-test`, a VM of the lab, or another machine: see
below). The header of each says in detail what it shows and what it
takes. Those that wipe delete the database, runs and projects, and need
`IT_WIPE=yes`; `IT_SSH_USER` and `IT_SSH_PASSWORD` name an account of the
controller that accepts password logins over SSH.

| Script | What it shows (variables it takes, time) | Wipes |
|---|---|---|
| `privileges.sh` | what the service account can reach |  |
| `sandbox.sh` | run sandbox, secrets, SSH (IT_SSH_USER, IT_SSH_PASSWORD) |  |
| `roles.sh` | roles, audit, sudoers (creates and removes test accounts) |  |
| `negative.sh` | hostile input to every command as each role (`negative-inputs.py`), the caller's environment, secrets typed in clear | yes |
| `runs.sh` | a whole run through fleetctl (IT_SSH_USER, IT_SSH_PASSWORD; IT_GIT_URL, IT_GIT_REF for a git project) | yes |
| `files.sh` | a project written in the interface, syntax checks, starters | yes |
| `upload.sh` | a project uploaded as an archive, hostile archives | yes |
| `store.sh` | the store: put, read, a run's files taken in, downloads, copies, archives, `sftp` through `fleet store sftp` | yes |
| `documents.sh` | the documents: repository, roles, writes as commits, attachments, rendering, `fleet doc`, hidden from runs | yes |
| `galaxy.sh` | collections from a requirements file (IT_GALAXY_NET=yes for git) | yes |
| `adhoc.sh` | ad-hoc commands, the allowlist of modules (IT_SSH_USER, IT_SSH_PASSWORD) | yes |
| `netscan.sh` | the scan of a subnet on a dummy interface (IT_NETSCAN_SUBNET: a lab's subnet instead, without the inventories), inventories from it, a probe, `net_probe` | yes |
| `inventories.sh` | inventory plugins, directories of sources, scripts off and on | yes |
| `mcp.sh` | fleet-mcp: tools by role, ceiling, profiles, autonomy, an agent's ad-hoc confirmed, via in the audit, forced-command key | yes |
| `chat.sh` | fleet-chat with `fake-agent.py`: start, limit on sessions, tools by role; `fleet assistant` with `fake-terminal.py` | yes |
| `cli.sh` | fleet at a terminal: setup, launch, exit statuses, roles, secrets on stdin | yes |
| `workflows.sh` | workflows: an approval at a step, a confirmation by who launched it, set_stats passed on, resume, cancel | yes |
| `schedules.sh` | the timer, ten schedules on one minute, missed times, overlap (about 5 min) | yes |
| `notify.sh` | notifications to stand-ins of the four channels (`notify-sinks.py`), a retry by the tick (about 3 min) | yes |
| `checks.sh` | checks on container nodes (podman), a playbook of the user, a full disk told about, a cycle falling behind (IT_KEEP; about 10 min) | yes |
| `cmdb.sh` | the CMDB, updates and drift of container nodes (podman), changes by hand (IT_KEEP; about 6 min) | yes |
| `measure-events.sh` | callback cost on HOSTS simulated hosts (arguments `[HOSTS] [FORKS]`) |  |
| `load.sh` | real check cycles (podman) on many aliases and a few SSH nodes, the interface timed (`IT_LOAD_*`, IT_KEEP; `load-sample.py`) | yes |
| `multihost.sh` | IT_NODES container nodes (podman; 5): vaults, passphrase key, key changes | yes |
| `windows.sh` | Windows node over SSH, a run and the checks (IT_WIN_HOST, IT_WIN_USER, IT_WIN_KEY, IT_KEEP) | yes |
| `nodes.sh` | the container nodes themselves (podman): `up N KEYS`, `recreate I KEYS`, `down`, `address I` |  |
| `lifecycle.sh` | upgrade, removal, purge (IT_PKG_OLD, IT_PKG_NEW; no package installed), through `make ct-lifecycle` |  |

## Running them

- **Containers**: `make ct-test DISTRO=debian|fedora|alma [TESTS="a b"]`
  (`scripts/ct-test.sh`, `test/containers/`) builds the package of
  `DISTRO` and an image of it with systemd as PID 1 and podman for the
  nodes, and runs each script as root in a fresh container, `CT_JOBS` at a
  time (2), with `IT_WIPE=yes` and an account for `IT_SSH_USER`; other
  `IT_*` variables of the caller are passed on; logs in
  `it-results/DISTRO/`. `windows.sh`, `measure-events.sh` and `load.sh` run
  only when named; `lifecycle.sh` only through `make ct-lifecycle`, which
  builds an older package too, as version 0.0.0 (`CT_OLD_VERSION=0.3.0`
  gives it another, for what an upgrade does only from some versions;
  `CT_KEEP_PACKAGES=DIR` keeps both, to run it on a VM). It needs rootless
  podman on cgroup v2, nothing else of the host. Run the `ct-test` of two
  distributions one after the other, not together.
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
end of the run's output. With `IT_KEEP=yes`, `windows.sh`, `checks.sh`,
`cmdb.sh` and `load.sh` leave their data in place, so that the same run
can be looked at in the interface; the webhook's stand-in of `checks.sh`
then stays up (`pkill -f notify-sinks.py` as root).

Installing by hand: [install.md](../install.md); `dnf reinstall` for the
same version. When `config.toml` was changed on both sides dpkg stops to
ask: put the packaged file back first, or use `--force-confold`. After a
deploy a Cockpit tab runs the old bundle until reloaded, and a copy in
`~/.local/share/cockpit` (left by `make devel-install`) shadows the
package's.

### Containers and machines

Two things make a container behave as a machine. It is privileged, within
the user's namespace: unprivileged, the run unit fails at `NAMESPACE`,
since systemd may neither make the namespaces nor mount a new `/proc` for
`PrivatePIDs`, and it ignores `PrivateIPC` with only a warning. And `/` is
made a shared mount at boot (`make-rshared.service`): systemd mounts the
credentials of a unit in a namespace of its own and moves them into
`/run/credentials`, which reaches the unit only through shared
propagation; on podman's private `/` a run gets no secret, with no error.

What a container leaves to machines: SELinux and the kernel, which are the
host's (a container reports SELinux disabled); the interface in a browser;
Windows nodes. Its nodes, containers in the container, read the host's
`/proc/meminfo` and `/proc/stat`, which is why `checks.sh` gives the memory
and processor checks no thresholds.

The machines are the lab's VMs, built from scratch by `test/vm/`
(`lab.sh`, skill `vm-lab`): a controller of each distribution, in
Europe/Rome so that containers (UTC) and machines cover both cases, and a
Windows 11 node. A test starts from their snapshot `baseline`, without the
package, and installs that of the commit under test (`lab.sh deploy`).

Known failures (27 November; both pass on machines and on the other
distributions): `cmdb.sh` on Fedora in a container (netavark: "invalid
namespace path"); on AlmaLinux in a container on a Fedora 44 host, podman
often cannot build the image of the nodes ("mounting an overlay over build
context directory ... invalid argument"), and every script with nodes
fails (`cmdb`, `multihost`, `checks`, `store`): show those on the VM
`alma10`.

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
`backend/libexec` and given every form of their arguments (what they
found:
[history/tests.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/tests.md)).
`negative.sh` gives every
command, through sudo as each role, an empty object, one hostile value per
parameter and input that is not an object, and checks what the caller's
environment and working directory cannot bring into fleetctl.

## CI

Both workflows run the same steps, and are kept in step: type check,
build, the frontend and backend unit tests, the catalogs of the
translations regenerated and compared with those committed, ruff, and a
native `.deb` build with lintian failing on errors and warnings, on Debian
13; the `.rpm` built natively and checked with rpmlint on Fedora 44 and
AlmaLinux 10, one job each. Tools with a version (Node.js, ruff) are
pinned in the workflow (`NODE_VERSION`, `RUFF_VERSION`). Actions are pinned
by the commit of their release, its version in a comment
(`actions/checkout@<sha> # v7.0.1`): a tag can be moved by whoever holds
the action's repository, and `release` may publish. They are raised by
hand, both workflows together.

- **Gitea** (`.gitea/workflows/ci.yml`): runners labelled `cockpit-fleet`,
  `cockpit-fleet-fedora`, `cockpit-fleet-alma`, arm64 containers where jobs
  run directly as a user that is not root; the pinned tools are installed
  once per runner by `scripts/ci-tool.sh`. No `release` job.
- **GitHub** (`.github/workflows/ci.yml`): each job in a container of its
  distribution (`debian:trixie`, `fedora:44`, `almalinux:10`) on
  `ubuntu-latest`, as root, nothing kept between runs; the packages are
  artifacts of the run. At a tag `vX.Y.Z` (pushed by Gitea's mirror) one
  more job, `release`, after the others pass, checks the tag
  (`release.mjs verify`) and that the three packages have its version, and
  publishes them with `SHA256SUMS` as the GitHub release of the tag, notes
  from `release.mjs notes`; run again, it replaces the assets. A tag made
  before the job existed is published with `gh workflow run ci.yml -f
  tag=vX.Y.Z`: the packages are built at the tag, the notes come from
  main's `CHANGELOG.md`.

The integration scripts are not part of CI: they need podman able to start
privileged containers, which the runners, containers where jobs run as a
non-root user, were not set up for.
