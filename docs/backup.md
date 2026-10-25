# Backup and restore

What a controller keeps, what to copy, and how to put it back. This page is
for the administrator of the machine; every command runs as root.

## What there is

| What | Where | In a backup |
|---|---|---|
| The database: projects, inventories, credentials (without their secrets), templates, schedules, workflows, checks and their states, time series, notifications, the history of runs, the audit log | `/var/lib/cockpit-fleet/fleet.db` (with `-wal` and `-shm` beside it) | yes, copied as below |
| Projects written in the interface (a git repository each, with their history), uploaded archives, collections installed for projects, checkouts of git projects | `/var/lib/cockpit-fleet/projects/` | yes; git projects and their collections can also be fetched again |
| The output and events of runs | `/var/lib/cockpit-fleet/runs/` | if the output of past runs matters; it is deleted after `[runs] keep_days` anyway |
| The configuration | `/etc/cockpit-fleet/config.toml` | yes |
| Local projects | the directories of `[projects] local_roots` | with the rest of the machine's data |
| Secrets, encrypted | `/etc/cockpit-fleet/secrets/` | only for this same machine (below) |

`/var/lib/cockpit-fleet/cache` holds nothing that cannot be computed
again.

## Secrets

Secrets are encrypted with the host key of this machine
(`/var/lib/systemd/credential.secret`) and, when it has one, its TPM. A copy
of `/etc/cockpit-fleet/secrets/` restores them on this machine, as long as
that key and that TPM are the same: after a deleted file, or a disk
restored with the rest of the system. On another machine, or on this one
reinstalled, the copy is useless, and that is the point: a backup that
leaks gives away no secret.

So keep the secrets themselves where you keep passwords, and after a
restore elsewhere type them again: in **Credentials** for keys, passwords
and vault passwords, in **Notifications** for the token of each channel.
The list of credentials says which secrets each one should have and which
are missing.

## Copying the database

The database is in WAL mode and may be written at any time (the scheduler
ticks every minute). Copy it with SQLite's backup, which gives a consistent
copy while the controller works, and do it as the service account: a file
of the database created by root would lock the service out of it.

```sh
install -d -o cockpit-fleet -g cockpit-fleet -m 0700 /var/backups/cockpit-fleet
sudo -u cockpit-fleet python3 -c '
import sqlite3
source = sqlite3.connect("/var/lib/cockpit-fleet/fleet.db")
target = sqlite3.connect("/var/backups/cockpit-fleet/fleet.db")
source.backup(target)
target.close()
'
```

Then copy that file, the projects and the configuration where backups go:

```sh
tar -C / -czf /root/cockpit-fleet-$(date +%F).tar.gz \
    var/backups/cockpit-fleet/fleet.db var/lib/cockpit-fleet/projects \
    etc/cockpit-fleet/config.toml
```

Add `etc/cockpit-fleet/secrets` for a restore on this same machine, and
`var/lib/cockpit-fleet/runs` to keep the output of past runs.

## Restoring

1. Install the package, the same version or a newer one: an older database
   is brought up to date when the backend first opens it, a newer one is
   refused.
2. Stop what may write, and wait for runs to end:

   ```sh
   systemctl stop cockpit-fleet-scheduler.timer cockpit-fleet-notify.path
   systemctl list-units 'cockpit-fleet-run@*'      # until there is none
   ```

3. Put the files back, owned by the service account:

   ```sh
   tar -C / -xzf /root/cockpit-fleet-2026-10-25.tar.gz
   rm -f /var/lib/cockpit-fleet/fleet.db-wal /var/lib/cockpit-fleet/fleet.db-shm
   install -o cockpit-fleet -g cockpit-fleet -m 0600 \
       /var/backups/cockpit-fleet/fleet.db /var/lib/cockpit-fleet/fleet.db
   chown -R cockpit-fleet:cockpit-fleet /var/lib/cockpit-fleet/projects
   ```

   The secrets, if restored on the same machine, stay root's:
   `chown -R root:cockpit-fleet /etc/cockpit-fleet/secrets`, files `0640`.

4. Start again, and look:

   ```sh
   systemctl start cockpit-fleet-scheduler.timer cockpit-fleet-notify.path
   fleet selfcheck            # as a user of cockpit-fleet-admin
   ```

   The System tab shows the same. Runs that were running when the copy was
   made end with an error, "the run ended without a result", at the first
   tick. Runs that were still queued start then, if their directory came
   back with the rest; cancel them in **Runs** first if they should not. A
   run whose directory was not restored keeps its outcome, without output.

5. Elsewhere, type the secrets again (above), and trust the host keys
   again only if the nodes changed: the trusted keys are in the database.

## Moving a configuration without its history

**System**, "Export", writes one JSON document with the credentials (names,
users, vault identifiers, never a secret), projects, inventories, host keys,
templates, workflows, schedules, checks, channels and rules, each named by
its name. "Import" on another controller creates what it does not have yet
and leaves alone what has the same name; it reports each item. It carries
no files (a project written in the interface, an uploaded archive), no run,
no state of a check, no audit log, and no secret: set them afterwards. At a
terminal: `fleet config export --json > fleet.json` (without `--json` it
prints the document for reading), and `fleet config import --document
@fleet.json`.
