# Architecture

How cockpit-fleet is put together on the controller, why, and what was measured
before building on it. The plan and the open decisions are in
[TODO.md](../TODO.md); this file describes what exists.

## Overview

```
Browser (React, PatternFly)
   │ cockpit.spawn → sudo -u cockpit-fleet fleetctl COMMAND < params.json
   ▼
service account cockpit-fleet (unprivileged, no login shell)
   │ writes /var/lib/cockpit-fleet/runs/<id>/{spec.json, project/, creds/, known_hosts}
   │ sudo fleet-unit start <id>          cockpit-fleet-finish@<id> records the outcome
   ▼
cockpit-fleet-run@<id>.service        sandboxed, User=cockpit-fleet
   │ PID 1 decrypts creds/ into /run/credentials/<unit>/
   │ fleet-run builds the ansible-playbook command line from spec.json
   ▼
ansible-playbook
   │ lineadicomando.fleet.events → events.jsonl      one JSON object per line
   │ stdout → stdout.log and the journal
```

There is no daemon and no listening socket. Everything that runs is either a
short command started by the web console or a systemd unit started on demand.

## Privilege boundaries

Two programs run as root. Both validate their single argument against
`[a-z0-9][a-z0-9_-]{0,63}` and accept nothing else.

| Helper | Called by | Does |
|---|---|---|
| `fleet-unit start\|stop ID` | `cockpit-fleet`, through one sudoers line | `systemctl start\|stop cockpit-fleet-run@ID.service` |
| `fleet-secret set\|delete NAME`, `list` | administrators (sudoers rule comes with phase 1) | encrypts stdin with `systemd-creds` into `/etc/cockpit-fleet/secrets/NAME.cred` |

### Why not a polkit rule

The first design let the service account manage its units through a polkit rule
matching unit names like `cockpit-fleet-run@*.service`. Testing it showed that
`StartTransientUnit` carries the same `unit` and `verb` details as a normal
start, so the rule also allowed

```
systemd-run --unit=cockpit-fleet-prepare@x.service /usr/bin/id   →  uid=0(root)
```

for any matching name that had no unit file yet. A name that does have a
template is refused by systemd, but that protection depends on a file being
installed, not on the rule. A helper that builds the unit name itself has no
such gap, and it is also out of reach from inside a run, where `NoNewPrivileges`
disables sudo.

## Secrets

- `fleet-secret set` encrypts the value with the host key (and the TPM2, when
  the machine has a usable one). There is no command to read a value back.
- The ciphertext is readable by the service account and useless to it: only
  PID 1 can decrypt.
- When a run is queued, the ciphertext files it needs are copied into its
  `creds/` directory, named after their purpose: `private_key`,
  `connection_password`, `become_password`, `vault_password`.
- The unit has `LoadCredentialEncrypted=fleet:/var/lib/cockpit-fleet/runs/%i/creds`.
  systemd decrypts every file there into `/run/credentials/<unit>/fleet_<purpose>`,
  a directory mounted for that unit only and removed when it stops.
- `fleet-run` passes those files to Ansible with `--private-key`,
  `--connection-password-file`, `--become-password-file` and
  `--vault-password-file`. A secret never appears in argv or in the environment.

Secrets are encrypted without an embedded name, because the same secret is
loaded under a different credential name depending on its purpose.

The service account decides which ciphertext goes into which run, so
compromising that account still exposes every secret through a crafted run.
What the design guarantees is narrower: secrets are safe at rest and in
backups, the web console cannot read them, and a playbook cannot reach the
secrets of another run or project.

## The run sandbox

`system/cockpit-fleet-run@.service`. `systemd-analyze security` rates it 1.5.

Every run executes as the same account, so the sandbox also separates runs
from each other. A first version did not: one run could read the decrypted
secrets of a concurrent one through `/proc/PID/root` of its processes. Each run
now has its own PID namespace (`PrivatePIDs=yes`, systemd 257) and IPC
namespace, and sees no process but its own. `test/integration/sandbox.sh`
tries exactly that path.

| A playbook can | A playbook cannot |
|---|---|
| read and write its own run directory | see the database, other projects, other runs |
| read its own decrypted secrets | read the secret store or another run's credentials |
| open network connections | reach D-Bus or systemd (`/run` is an empty tmpfs) |
| write to a private `/tmp` and a throwaway home | use sudo or setuid binaries, write `/etc` or `/usr`, read `/home` |

Three settings are less strict than they could be, each for a measured reason:

- **Home is a writable tmpfs**, not read-only: with the local connection,
  Ansible creates its temporary directory under the account's home.
- **`SystemCallErrorNumber=EPERM`**, without a deny list on top of
  `@system-service`: modules executed on the controller call `chown()` even when
  nothing changes, and a seccomp kill made `copy` fail with "Bad system call".
- **No `ProcSubset=pid`**: fact gathering on the controller reads
  `/proc/meminfo` and `/proc/cpuinfo`.

`ANSIBLE_CONFIG` points to a file generated for the run. Ansible rejects
`/dev/null` here (it wants a `.cfg` extension), and setting it also keeps an
`ansible.cfg` inside the project from being loaded.

SSH uses `StrictHostKeyChecking=yes` with a known-hosts file in the run
directory and no global file. A host with an unknown key is refused, including
when a password is used. `ControlPersist` works inside the sandbox: several
tasks produce one login.

## The backend interface

`fleetctl COMMAND < params.json` is the only way the web console talks to the
backend. The command is a name from a registry; parameters are a JSON object
on stdin; the answer is one JSON object, `{"ok": true, "data": …}` or
`{"ok": false, "error": {"code", "message"}}`. The exit status is 0 whenever an
answer was written, so a failed exit always means the backend was not reached.

- **Who is calling.** fleetctl refuses to run as anything but the service
  account, so it is always reached through sudo, which sets `SUDO_UID` and does
  not let the caller override it.
- **Roles.** `cockpit-fleet-viewer`, `-operator`, `-admin`; the highest group
  wins. Root is an administrator. Every command declares the least role that
  may call it, and the check happens in one place, before the handler runs.
- **Audit.** Commands that change something, and every refusal, are written to
  the `audit` table and to the journal (`journalctl -t cockpit-fleet`, with
  `FLEET_USER`, `FLEET_COMMAND`, `FLEET_OUTCOME` fields). Parameters marked as
  secret are replaced before being stored. `fleet-secret` runs as root and
  cannot write to the database, so it logs to the journal only.
- **Database.** SQLite in WAL mode at `/var/lib/cockpit-fleet/fleet.db`, mode
  0600. Migrations are an append-only list applied under a lock on connect; a
  database from a newer version is refused.
- **Configuration.** `/etc/cockpit-fleet/config.toml`, root-owned. A missing
  file means defaults; an unknown key is an error, so a typo cannot silently
  leave a limit at its default.

An administrator of the machine who is in no role group is refused by sudo.
With Cockpit's administrative access turned on, the frontend retries as root
and gets the admin role; the audit trail then shows `root`, not the person.

To add a command: a function decorated with `@command(name, role=…,
mutating=…, redact=…)` in a module imported by `cli.main`, parameters read
through `Params` (which rejects unknown ones), and a test in `backend/tests`.

## Jobs

A run unit executes one job, of one of three kinds, described by `spec.json`
in its directory: `playbook` (ansible-playbook), `inventory`
(`ansible-inventory --list`, to learn hosts and groups) and `sync` (fetch a git
project). All three need the sandbox, because all three can execute code from
a project: inventory plugins and scripts, or git itself.

When a job is queued, `fleetctl` prepares `/var/lib/cockpit-fleet/runs/<id>/`:

| Entry | Content |
|---|---|
| `spec.json` | what to run; validated again by `fleet-run` |
| `project/` | a copy of the project (symbolic links copied, never followed; `.git` left out) |
| `creds/` | ciphertext of the secrets this job may use, named by purpose |
| `known_hosts` | the host keys an administrator has trusted |
| `inventory/` | the inventory, when it is written in the interface rather than a project file |

The run then sees a self-contained directory and nothing else. `project/` and
`creds/` are removed when the job ends; `stdout.log`, `events.jsonl` and
`result.json` stay.

There is no daemon tracking jobs. The `runs` table holds the state, and
`reconcile` brings it up to date from what a unit left behind: it runs when a
unit ends (`OnSuccess=`/`OnFailure=` start `cockpit-fleet-finish@ID.service`,
which is the backend itself, unsandboxed, as the service account) and whenever
runs are read. It also starts queued runs while fewer than
`runs.max_concurrent` are running. A unit that is gone without a
`result.json` is recorded as an error, or as canceled if that was asked.

**Git projects.** A sync job makes a fresh shallow fetch of one reference into
its own directory and drops `.git`; the result replaces the project's tree
only if the fetch succeeded. Hooks are disabled, submodules are not followed,
and `GIT_ALLOW_PROTOCOL` leaves only https and ssh. A token is handed to git by
an askpass helper that reads it from the credentials directory, so it is in
neither argv nor the environment.

**A project's `ansible.cfg`** is not used as it is: a run gets a generated one
with only the allowlisted keys of the project's file (`roles_path` and
`collections_path` when they stay inside the project, `gathering`,
`pipelining`, `become` settings and a few more). Anything that would load
plugins from elsewhere, point to a vault password file or relax host key
checking is dropped. The integration test ships a project that tries.

**Host keys.** `hostkey.scan` returns the keys a host offers, with their
fingerprints; nothing is trusted until an administrator confirms with
`hostkey.add`. A run connects only to hosts listed there.

**Limits a launcher can change.** Launching a template, an operator may add a
host limit and ask for a dry run. The limit is combined with the template's
own (`template:&caller`), so it can narrow the target but not widen it.

## Events

The callback `lineadicomando.fleet.events` appends one JSON object per line:
`playbook_start`, `play_start`, `task_start`, `host_ok`, `host_failed`,
`host_skipped`, `host_unreachable`, `stats`. Results are trimmed (long strings
cut, `invocation` and, by default, `ansible_facts` dropped).

Measured on the test controller (2 vCPU, 2 GB), 200 hosts simulated with the
local connection, 10 forks, fact gathering plus three tasks:

| | Time | Output |
|---|---|---|
| without the callback | 113 s | |
| with the callback | 117 s | 389 kB, 1.9 kB per host |
| with the callback, facts kept | 119 s | 2.6 MB, 12.8 kB per host |

The callback costs about 3%. The number that matters for sizing is the first
one: about 0.6 s of wall time per host on two cores, before any network delay.
A collection cycle over 200 hosts takes around two minutes on this hardware, so
intervals of 5 to 15 minutes are realistic and controller CPU, not storage, is
the first limit.

## Windows nodes

Reached over SSH with `ansible_shell_type: powershell`. The `ansible.windows`
collection is not part of `ansible-core`: it comes from the `ansible` package
or from `ansible-galaxy` into the project. Installing it into the run's project
directory works, and the collection is found from there.

The test nodes accept public keys only, so the run uses a private key stored
as a secret and passed with `--private-key`. Measured from the sandboxed unit
against a Windows 11 VM (4 vCPU, 8 GB), `test/integration/windows.sh`:

| Task | Time |
|---|---|
| fact gathering | 10 to 11 s |
| `win_command` | 4 s |
| `win_shell` (one CIM query) | 5 s |
| whole run | 20 s |

Every task on Windows costs 4 to 5 seconds whatever it does, against a fraction
of a second on Linux. A collection cycle must therefore gather everything a
Windows node has to report in one task, with one script, and not run one task
per check.

## Packaging

The `.deb` is built in a Debian 13 container (`make deb`, output in `deb/`),
which also runs lintian; on a Debian 13 system `scripts/build-deb.sh --native`
does the same without a container. The frontend bundle is built beforehand with npm,
because the package build has no network. The package creates the account and
the role groups (`sysusers.d`), the state and secret directories
(`tmpfiles.d`), and installs the unit, the helpers, the sudoers line and the
bundled collection.

## Tests

| What | Where | Runs on |
|---|---|---|
| Frontend unit tests | `test/*.test.ts` | development host, CI |
| Backend unit tests | `backend/tests/` | development host, CI |
| Privilege boundaries | `test/integration/privileges.sh` | controller, as root |
| Sandbox, secrets, SSH | `test/integration/sandbox.sh` | controller, as root |
| Roles, audit, sudoers | `test/integration/roles.sh` | controller, as root |
| A whole run through fleetctl | `test/integration/runs.sh` | controller, as root; wipes the database |
| Callback cost | `test/integration/measure-events.sh` | controller, as root |
| Windows node over SSH | `test/integration/windows.sh` | controller, as root |

CI runs on Gitea (`.gitea/workflows/ci.yml`): type check, build, both unit test
suites, ruff, and a native package build with lintian, which fails on errors
and warnings. The integration scripts need the package installed on a
controller and are not part of CI yet.
