# Overview and security model

## Overview

```
Browser (React, PatternFly)
   │ cockpit.spawn → sudo -u cockpit-fleet fleetctl COMMAND < params.json
   ▼
service account cockpit-fleet (unprivileged, no login shell)
   │ writes /var/lib/cockpit-fleet/runs/<id>/{spec.json, project/, creds/, known_hosts}
   │ sudo fleet-unit start <id>          cockpit-fleet-finish@<id> records the outcome
   │                                     cockpit-fleet-scheduler.timer launches what is due
   │                                     cockpit-fleet-notify.path sends what the rules say
   ▼
cockpit-fleet-run@<id>.service        sandboxed, User=cockpit-fleet
   │ PID 1 decrypts creds/ (copied by fleet-unit, as root) into /run/credentials/<unit>/
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
| `fleet-unit start\|stop ID` | `cockpit-fleet`, through one sudoers line | `systemctl start\|stop cockpit-fleet-run@ID.service`; before a start, copies the run's `creds/` into `/run/cockpit-fleet/creds/ID` (below) |
| `fleet-secret set\|delete NAME`, `list` | administrators | encrypts stdin with `systemd-creds` into `/etc/cockpit-fleet/secrets/NAME.cred` (`secrets/notify/` for a name beginning with `n-`, a notification channel's) |
| `fleet-secret answer` | operators and administrators | encrypts stdin into `/run/cockpit-fleet/answers/`, under a name it chooses and prints |

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
- The unit has `LoadCredentialEncrypted=fleet:/run/cockpit-fleet/creds/%i`.
  `fleet-unit`, as root, fills that directory from the run's `creds/` just
  before `systemctl start`, and empties it once the start has returned
  (`Type=exec`: the credentials are read by then). The units that start
  runs through it (`cockpit-fleet-finish@`, the scheduler) have that
  directory among their `ReadWritePaths`: `fleet-unit` runs in their mount
  namespace, where `ProtectSystem=strict` leaves `/run` read-only. systemd
  decrypts every
  file there into `/run/credentials/<unit>/fleet_<purpose>`, a directory
  mounted for that unit only and removed when it stops.
- Why the copy: PID 1 reads those paths as root and, with systemd 257
  (Debian 13), follows symbolic links, so a `creds/` or a run directory that
  the account turned into a link would have it decrypt root's own
  credentials of other services (`/etc/credstore.encrypted`, say) into a run
  (seen on 24 October; Fedora 44's 259 and AlmaLinux 10 refused). The copy
  reaches each part of the path without following a link, and takes only
  regular files of the account with one link, 256 KiB at most
  (`credstage.py`). The run directory itself is still bound into the unit
  by path: an account that swapped it for a link between the check and the
  start could bind another directory, whose files the run reads only as far
  as their own permissions let the account.
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
| read and write its own run directory | see the database, the projects the backend keeps, other runs |
| read its own decrypted secrets | read the secret store or another run's credentials |
| open network connections | reach D-Bus or systemd (`/run` is an empty tmpfs) |
| write to a private `/tmp` and a throwaway home | use sudo or setuid binaries, write `/etc` or `/usr`, read `/home` |

Three settings are less strict than they could be, each for a measured reason:

- **Home is a writable tmpfs**, not read-only: with the local connection,
  Ansible creates its temporary directory under the account's home. That
  directory, `~/.ansible`, is the run's `scratch/remote_tmp` on disk, bound
  there: a module run on the controller keeps its temporary files in it,
  and `get_url` the whole download, which in the tmpfs failed with ENOSPC
  beyond about half the memory (a 3 GB file on a VM of 2 GB, 9 November).
- **`SystemCallErrorNumber=EPERM`**, without a deny list on top of
  `@system-service`: modules executed on the controller call `chown()` even when
  nothing changes, and a seccomp kill made `copy` fail with "Bad system call".
- **No `ProcSubset=pid`**: fact gathering on the controller reads
  `/proc/meminfo` and `/proc/cpuinfo`.

`ANSIBLE_CONFIG` points to a file generated for the run. Ansible rejects
`/dev/null` here (it wants a `.cfg` extension), and setting it also keeps an
`ansible.cfg` inside the project from being loaded.

Runs go in `system-cockpitfleet.slice` (`system/`), whose `CPUWeight=20`
puts them behind the interface and the units that record results when the
processor is contended, and leaves them all of it otherwise. Without a
weight systemd keeps the cpu controller off in `system.slice`, and the
forks of one check cycle made each `fleetctl` call ten times slower
([Checks](checks.md), "At scale"). The weight changes no isolation: the slice holds only
runs, each still in its own unit and namespaces.

SSH uses `StrictHostKeyChecking=yes` with a known-hosts file in the run
directory and no global file. A host with an unknown key is refused, including
when a password is used. `ControlPersist` works inside the sandbox: several
tasks produce one login.
