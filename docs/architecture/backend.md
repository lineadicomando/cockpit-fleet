# The backend interface

`fleetctl COMMAND < params.json` is the only way the web console talks to the
backend. The command is a name from a registry; parameters are a JSON object
on stdin; the answer is one JSON object, `{"ok": true, "data": …}` or
`{"ok": false, "error": {"code", "message"}}`. The exit status is 0 whenever an
answer was written, so a failed exit always means the backend was not reached
(a command that streams writes its error on stderr and exits 1).
The input is at most 1 MB, but for the commands that carry files, which
declare their own limit (`max_input`): `file.write`, `doc.write` and
`doc.render` (about 2 MB), `config.import` (8 MB), and about 67 MB for
`project.upload`, `inventory.create`, `file.change`, `file.import` and
`file.push`, an archive or a bundle of 50 MB in base64. Commands that move
files of any size declare `stream=` (see [The store](store.md)): `store.put`
and `doc.put` read their parameters on the first line and the bytes after
them; `store.read`, `store.archive` and `doc.get` write the bytes instead of
an answer and take their path(s) in argv; `store.sftp` speaks SFTP on its
input and output.

- **Who is calling.** fleetctl refuses to run as anything but the service
  account, so it is always reached through sudo, which sets `SUDO_UID` and does
  not let the caller override it. Only plain decimal digits are read as an
  account: whatever else `int()` would take (spaces, a sign, other scripts'
  digits) gives no role.
- **Roles.** `cockpit-fleet-viewer`, `-operator`, `-admin`; the highest group
  wins. Root is an administrator. Every command declares the least role that
  may call it, and the check happens in one place, before the handler runs.
- **Rights.** A group that adds one thing to whatever role the caller has, or
  stands for it alone: `cockpit-fleet-approver` gives `approve` (see
  [Approvals](approvals.md)). A command may name a right besides its role, and is then
  allowed to either. A call made for an agent has no right at all.
- **Audit.** Commands that change something, and every refusal (by role, by
  the ceiling of agents, or by the command itself, such as approving one's
  own launch), are written to the `audit` table and to the journal
  (`journalctl -t cockpit-fleet`, with `FLEET_USER`, `FLEET_COMMAND`,
  `FLEET_OUTCOME` fields, and `FLEET_VIA` when the call was made for an
  agent); `audit.list` (admin) reads the table, newest first. Parameters
  marked as secret are replaced before being stored, and so is a value
  typed where a secret was expected (an answer to a secret field of a
  survey, given in clear rather than as a ticket, or a default for it): the
  error that refuses it carries the value, and the record masks it wherever
  it appears. Identifiers and the limit are kept whole, other values cut at
  200 characters each, so that a long answer does not push them out. An
  agent's call to a command that is not a tool (`tool=False`) is refused
  here too, not only by `fleet-mcp`. `fleet-secret` runs as root and cannot
  write to the database, so it logs to the journal only.
- **Database.** SQLite in WAL mode at `/var/lib/cockpit-fleet/fleet.db`, mode
  0600. Migrations are an append-only list applied under a lock on connect; a
  database from a newer version is refused. They run with foreign keys off,
  so that one can rebuild a table others refer to. The kinds of projects and
  of runs are checked by the commands, not by the schema.
- **Configuration.** `/etc/cockpit-fleet/config.toml`, root-owned. A missing
  file means defaults; an unknown key is an error, so a typo cannot silently
  leave a limit at its default. `config.show` (admin) gives the values in
  force.

An administrator of the machine who is in no role group is refused by sudo.
With Cockpit's administrative access turned on, the frontend retries as root
and gets the admin role; the audit trail then shows `root`, not the person.

Commands are added as CLAUDE.md says ("Adding things"), in a module that
`cli.load_commands` imports. The declared parameters check the input before
the handler runs (an unknown parameter is refused) and describe the command
as JSON Schema to an agent. A command with several forms, a project of one
kind or another, declares the parameters of all of them and refuses those
that mean nothing in the form asked for.
