# The terminal: history

Archive: what was tried, measured and found while building the terminal;
not needed to work on it. The design is in
[architecture/terminal.md](../architecture/terminal.md).

Checked on Debian 13 (`cli.sh`): a project, an inventory and a template
set up by an administrator, a file written from a file and from stdin, an
operator's launch followed to its end, exit statuses, refusals by role, a
user in no group told why, a secret from stdin; and by hand, at a
terminal, a secret question asked with no echo, which reached the run
masked and is in no record.

A local project under `/home` or `/tmp` worked when launched through sudo
and failed whenever a timer started it, with "is not a directory": the
scheduler and `cockpit-fleet-finish@` have `ProtectHome` and
`PrivateTmp`. Hence `jobs.HIDDEN_FROM_UNITS`.
