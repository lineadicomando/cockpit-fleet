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

`fleet` at a terminal and the assistant (`fleet-mcp`, `fleet-chat`) reach
`fleetctl` the same way, through the same sudoers line, as the user who
runs them. There is no daemon and no listening socket. Everything that
runs is either a short command started by the web console or a systemd
unit started on demand.

## Privilege boundaries

Two programs run as root. Both validate the name or id they take against
`[a-z0-9][a-z0-9_-]{0,63}` and accept nothing else.

| Helper | Called by | Does |
|---|---|---|
| `fleet-unit start\|stop ID` | `cockpit-fleet`, through one sudoers line | `systemctl start\|stop cockpit-fleet-run@ID.service`; before a start, copies the run's `creds/` into `/run/cockpit-fleet/creds/ID` (below) |
| `fleet-secret set\|delete NAME`, `list` | administrators | encrypts stdin with `systemd-creds` into `/etc/cockpit-fleet/secrets/NAME.cred` (`secrets/notify/` for a name beginning with `n-`, a notification channel's) |
| `fleet-secret answer` | operators and administrators | encrypts stdin into `/run/cockpit-fleet/answers/`, under a name it chooses and prints |

### Why not a polkit rule

A polkit rule matching unit names (`cockpit-fleet-run@*.service`) also
authorises `StartTransientUnit` of that name, which carries the same
details as a start: arbitrary code as root for any matching name with no
unit file. `fleet-unit` builds the unit name itself, and is out of reach
from inside a run, where `NoNewPrivileges` disables sudo.

## Secrets

- `fleet-secret set` encrypts the value with the host key (and the TPM2, when
  the machine has a usable one), without an embedded name, since one secret
  is loaded under a different credential name depending on its purpose.
  There is no command to read a value back.
- The ciphertext is readable by the service account and useless to it: only
  PID 1 can decrypt.
- When a run is queued, the ciphertext files it needs are copied into its
  `creds/` directory, named after their purpose: `private_key`,
  `key_passphrase`, `connection_password`, `become_password`,
  `vault_password_N`, and `survey_N`, `secret_var_N` ([Jobs](jobs.md)).
- The unit has `LoadCredentialEncrypted=fleet:/run/cockpit-fleet/creds/%i`.
  `fleet-unit`, as root, fills that directory from the run's `creds/` just
  before `systemctl start`, and empties it once the start has returned
  (`Type=exec`: the credentials are read by then). The units that start
  runs through it (`cockpit-fleet-finish@`, the scheduler) have that
  directory among their `ReadWritePaths`: `fleet-unit` runs in their mount
  namespace, where `ProtectSystem=strict` leaves `/run` read-only. systemd
  decrypts every file there into `/run/credentials/<unit>/fleet_<purpose>`,
  a directory mounted for that unit only and removed when it stops.
- Why the copy: PID 1 reads those paths as root and (systemd 257, Debian 13)
  follows symbolic links, so a `creds/` that the account turned into a link
  would have it decrypt root's own credentials of other services
  (`/etc/credstore.encrypted`) into a run. The copy (`credstage.py`) reaches
  each part of the path without following a link, and takes only regular
  files of the account with one link, 256 KiB and 128 files at most. The
  run directory itself is still bound into the unit by path: an account
  that swapped it for a link between the check and the start could bind
  another directory, whose files the run reads only as far as their own
  permissions let the account.
- `fleet-run` passes those files to Ansible with `--private-key` (or loads
  it into an `ssh-agent` when it has a passphrase),
  `--connection-password-file`, `--become-password-file` and `--vault-id
  ID@FILE`. A secret never appears in argv or in the environment.

What this guarantees, and what it does not (the service account decides
which ciphertext goes into which run): [security.md](../security.md),
"Secrets".

## The run sandbox

`system/cockpit-fleet-run@.service`. `systemd-analyze security` rates it 1.5.

Every run executes as the same account, so the sandbox also separates runs
from each other: each has its own PID namespace (`PrivatePIDs=yes`, systemd
257) and IPC namespace, and sees no process but its own, so no run reaches
another's decrypted secrets through `/proc/PID/root`
(`test/integration/sandbox.sh` tries that path).

| A playbook can | A playbook cannot |
|---|---|
| read and write its own run directory | see the database, the projects the backend keeps, other runs |
| read its own decrypted secrets | read the secret store or another run's credentials |
| open network connections | reach D-Bus or systemd (`/run` is a read-only tmpfs with only the resolver's directory) |
| write to a private `/tmp` and a throwaway home | use sudo or setuid binaries, write `/etc` or `/usr`, read `/home` |
| leave processes running while it runs | outlive its run, or change its outcome (`jobs.md`) |

Three settings are less strict than they could be, each for a measured reason:

- **Home is a writable tmpfs**, not read-only: with the local connection,
  Ansible creates its temporary directory under the account's home. That
  directory, `~/.ansible`, is the run's `scratch/remote_tmp` on disk, bound
  there: a module run on the controller keeps its temporary files in it,
  and `get_url` the whole download, which in the tmpfs would be bounded by
  memory.
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
([Checks](checks.md), "At scale"). The weight changes no isolation: the
slice holds only runs, each still in its own unit and namespaces.

SSH uses `StrictHostKeyChecking=yes` with a known-hosts file in the run
directory and no global file. A host with an unknown key is refused, including
when a password is used. `ControlPersist` (60 s) works inside the sandbox: several
tasks produce one login.
