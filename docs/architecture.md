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
  Ansible creates its temporary directory under the account's home.
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
(Checks, "At scale"). The weight changes no isolation: the slice holds only
runs, each still in its own unit and namespaces.

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
The input is at most 1 MB, for every command but two, which declare their
own limit: `project.upload` and `config.import`.

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
  "Approvals"). A command may name a right besides its role, and is then
  allowed to either. A call made for an agent has no right at all.
- **Audit.** Commands that change something, and every refusal (by role, by
  the ceiling of agents, or by the command itself, such as approving one's
  own launch), are written to
  the `audit` table and to the journal (`journalctl -t cockpit-fleet`, with
  `FLEET_USER`, `FLEET_COMMAND`, `FLEET_OUTCOME` fields, and `FLEET_VIA` when
  the call was made for an agent). Parameters marked as
  secret are replaced before being stored, and so is a value typed where a
  secret was expected (an answer to a secret field of a survey, given in
  clear rather than as a ticket, or a default for it): the error that
  refuses it carries the value, and the record masks it wherever it
  appears. Identifiers and the limit are
  kept whole, other values cut at 200 characters each, so that a long
  answer does not push them out. An agent's call to a command that is not a
  tool (`tool=False`) is refused here too, not only by `fleet-mcp`. `fleet-secret` runs as root and
  cannot write to the database, so it logs to the journal only.
- **Database.** SQLite in WAL mode at `/var/lib/cockpit-fleet/fleet.db`, mode
  0600. Migrations are an append-only list applied under a lock on connect; a
  database from a newer version is refused. They run with foreign keys off,
  so that one can rebuild a table others refer to. The kinds of projects and
  of runs are checked by the commands, not by the schema.
- **Configuration.** `/etc/cockpit-fleet/config.toml`, root-owned. A missing
  file means defaults; an unknown key is an error, so a typo cannot silently
  leave a limit at its default.

An administrator of the machine who is in no role group is refused by sudo.
With Cockpit's administrative access turned on, the frontend retries as root
and gets the admin role; the audit trail then shows `root`, not the person.

To add a command: a function decorated with `@command(name, role=…,
mutating=…, redact=…, params={…})` in a module imported by `cli.main`, and a
test in `backend/tests`. The parameters are declared, each with its type and
its bounds (`Str`, `Int`, `Bool`, `Id`, `Strs`, `Objs`, `Obj`, `Choice`): the
declaration is what checks the input before the handler runs (an unknown
parameter is refused) and what describes the command as JSON Schema to an
agent. The handler gets the checked values. A command with several forms, a
project of one kind or another, declares the parameters of all of them and
refuses those that mean nothing in the form asked for.

## Jobs

A run unit executes one job, of one of eight kinds, described by `spec.json`
in its directory: `playbook` (ansible-playbook), `adhoc` (one module, as a
playbook written for the job), `inventory` (`ansible-inventory --list`, to
learn hosts and groups; when it fails, Ansible quotes the lines it could not
read, which may hold passwords, so why is shown to administrators only,
and so is its output), `sync` (fetch a git project), `syntax` (check a
playbook without running it), `unpack` (extract an uploaded archive),
`galaxy` (install the collections a project asks for) and `check` (a cycle
of checks, see "Checks"). A run of a workflow is not a job: it has no
unit, and its steps are runs of playbooks (see "Workflows"). All of them need the sandbox, because all of them work on what a project
brings: inventory plugins and scripts, git itself, the rules a project gives
to ansible-lint, an archive made by anyone, or a list of sources to download
from.

When a job is queued, `fleetctl` prepares `/var/lib/cockpit-fleet/runs/<id>/`:

| Entry | Content |
|---|---|
| `spec.json` | what to run; validated again by `fleet-run` |
| `project/` | a copy of the project (symbolic links copied, never followed; `.git` left out) |
| `creds/` | ciphertext of the secrets this job may use, named by purpose |
| `known_hosts` | the host keys an administrator has trusted |
| `inventory/` | a copy of the tree of the inventory, when it is written in the interface rather than a project file |
| `collections/` | a copy of the collections installed for the project, when it asks for some |

The copy of a local project opens each directory and file from the one
that holds it, following no link, so a tree its owner changes while it is
copied cannot lead outside it (an earlier `os.walk` could be raced into
copying the database). What the service account cannot read there (logs or
reports that a tool of the owner writes with mode 0600, in a directory git
ignores) is left out of the copy instead of failing the job, and the paths
left out head the run's output, written to `stdout.log` before the unit
starts: a variables file left out says so there, not silently. Failing
instead made every run of such a project fail on files it never used.

The run can write in its own directory, so what it leaves there is read by
`fleetctl`, outside the sandbox, through `jobs.open_run_file`: without
following a link (the backend could otherwise be led to the database, or to
another run's decrypted credentials through `/proc/PID/root`, both reachable
by the account), without waiting on a FIFO, and with a size limit for the
small files.

The run then sees a self-contained directory and nothing else. `project/`,
`creds/`, `collections/` and an uploaded `archive` are removed when the job
ends; `stdout.log`, `events.jsonl` and `result.json` stay until
`[runs] keep_days` (90; 0 for ever) after it ended, when the tick deletes the
directory (see "Maintenance").

There is no daemon tracking jobs. The `runs` table holds the state, and
`reconcile` brings it up to date from what a unit left behind: it runs when a
unit ends (`OnSuccess=`/`OnFailure=` start `cockpit-fleet-finish@ID.service`,
which is the backend itself, unsandboxed, as the service account) and whenever
runs are read. It also starts queued runs while fewer than
`runs.max_concurrent` are running: those of a higher priority first (a
schedule gives its own to its runs; a launch by hand has 0), then the oldest. Only one `reconcile` works at a time (a
lock on the directory of the runs): the unit that ends and a page that polls
get there together, and what a job left can be moved only once; found with
the galaxy job, whose result takes longer to take in. A unit that is gone without a
`result.json` is recorded as an error, or as canceled if that was asked.

**Git projects.** A sync job makes a fresh shallow fetch of one reference into
its own directory and drops `.git`; the result replaces the project's tree
only if the fetch succeeded. Hooks are disabled, submodules are not followed,
and `GIT_ALLOW_PROTOCOL` leaves only https and ssh. A token is handed to git by
an askpass helper that reads it from the credentials directory, so it is in
neither argv nor the environment.

**Projects written in the interface.** A third kind of project, `managed`,
has no source elsewhere: its files are written in the "Files" page by
administrators. They are the work tree of a git repository, and every
change is one commit whose author is the caller and whose committer is the
service account: one file written (`file.write`), a file or a directory
renamed or deleted, or a list of changes (`file.change`: write, move, copy,
delete, of files and directories) made all together or not at all. A run
records the commit it ran.

```
/var/lib/cockpit-fleet/projects/<id>/tree/       the files, what a run copies
/var/lib/cockpit-fleet/projects/<id>/repo.git/   the repository, beside the tree
/var/lib/cockpit-fleet/projects/<id>/lock        one change at a time (flock)
```

The repository is not inside the tree, so nothing in the tree is git's own and
no path can reach its configuration or hooks; a path with a component called
`.git`, in any case, is refused anyway, as is one that goes through a symbolic
link. Here git runs in `fleetctl`, outside the sandbox, which a fetched project
never gets. What makes the difference: the repository is created by the
backend and holds only what administrators wrote through it, git reads no
configuration but the repository's own, hooks are off, and paths are literal,
never patterns.

A write quotes the SHA-256 of the content it started from, empty for a new
file: one that someone else changed meanwhile is refused instead of
overwritten. A list of changes may also quote the revision it was made on,
and is refused if the project moved on since. The content is not copied
into the audit record, the paths are (`operations[].content` and
`operations[].data` are redacted inside the list). Files are up to 1 MB:
text is edited, a file that is not UTF-8 (an installer a playbook copies to
nodes) is uploaded in base64 and shown as binary, never edited. The same
page shows the files of the other kinds of project, read-only.

The changes of a list are made one after the other in the tree; each is
checked before it touches anything, and when one fails after others were
made, `git reset --hard` and `git clean` put the tree back as the last
commit has it. A directory is moved or copied only if every path it would
then hold is one the editor takes (twelve levels at most), and a copy only
if the project then holds at most 5000 files. Git keeps no empty
directory, so neither does a project: a directory left empty goes.

`file.restore` puts a file or a directory back as it was at a revision, as
a new commit, exactly (files added since are removed). Only a revision in
the history of `main` is taken: a push that was refused leaves its objects
in the repository, links among them, and they must not reach the tree.
`file.download` gives a file in base64, or a directory as a gzipped tar
built by `fleetctl`, opening each directory from the one that holds it and
leaving links out, for any kind of project; like `file.bundle`, it is in
the audit trail, as a copy of the files.

`file.import` brings an archive into a managed project or inventory, into
a directory of it or at its top, as one commit of the caller: it travels
like an upload (base64 on stdin, within `[projects] max_upload_mb`) and is
unpacked by the same `unpack` job, in the sandbox, with the same rules and
the same single top directory left out. What the job leaves is then read
by `fleetctl` from the run's directory one directory from another,
following no link, and taken only if every entry is a regular file or a
directory, of at most 1 MB, with a path the editor takes, 5000 files at
most: a link, which an uploaded project may keep inside itself, fails the
import here, since the editor never writes one. The files are added, and
replace those of the same path; nothing else of the tree goes. What a
write does follows: syntax checks, collections, inventories read again.

An inventory that is a file of a project is read again when a change in
the interface or a push touches what it reads: its own file or directory,
the directory it is in (its `group_vars` and `host_vars`), or a directory
it is in, moved or deleted. A fetch or an upload reads again every
inventory of the project. Before, the hosts were those of the last reading
until someone asked, or the hourly reading for checks.

**Uploaded projects.** A fourth kind of project, `archive`, gets its files
from a tar (plain, gzip, bzip2 or xz) or a zip chosen in the browser. They are
not edited afterwards: another archive replaces the whole tree.

```
browser ── {"id", "filename", "data": base64} on stdin ──▶ fleetctl project.upload
                 │ counts and digests the bytes, writes runs/<id>/archive
                 ▼
cockpit-fleet-run@<id>   unpack: archive → checkout.unpack/ → checkout/
                 ▼
reconcile        checkout/ replaces projects/<id>/tree; revision = SHA-256 of the archive
```

The archive travels like every other parameter, so there is still one way
into the backend. The browser sends the JSON in pieces of 256 kB on the same
standard input, and `fleetctl` holds it in memory whole: that is what bounds
the size, 20 MB by default (`projects.max_upload_mb`, at most 50). `fleetctl`
does nothing with the archive but count and digest its bytes; the format is
read from the content, in the sandbox, never from the name.

The job does not call `extract` of `tarfile` or `zipfile`: `archive.py` reads
the members and writes each one itself.

| In the archive | What happens |
|---|---|
| a regular file, a directory | written, private to the account; only the executable bit is kept |
| a path that is absolute, has `..`, a backslash or a control character, or is deeper than 32 levels | the archive is refused |
| a hard link, a device, a fifo; an encrypted zip | refused |
| a symbolic link | made last, when no file is left to be written through one; refused unless it stays inside the tree, both as written and as the kernel follows it |
| the same path twice; a file where a directory is | refused |
| anything under `.git`, in any case | left out |
| more than 20 000 files, or more than 500 MB once unpacked | refused; the size is counted as it is written, not as declared |

A refused archive leaves nothing: the project keeps the tree and the revision
it had, and shows the reason. A single directory that holds everything else,
as `project-1.0/` in most archives, is not kept: its content is the project.
The revision is the digest `fleetctl` took of what it received, not something
the job reports; and what the job leaves is taken as the tree only if it is a
directory, not a link (the same now holds for a git fetch).

Measured on the test controller, from the browser through Cockpit: an archive
of 19 MB is answered in 1.7 s and unpacked in under a second; 600 MB of zeros
in a gzip of 600 kB are refused at the limit.

**Collections from Galaxy.** A project of any kind may list the collections
it needs in a requirements file: the first one found among
`collections/requirements.yml`, `collections/requirements.yaml`,
`requirements.yml` and `requirements.yaml`. A `galaxy` job runs
`ansible-galaxy collection install` on it, and what it installs is kept beside
the project's tree, never inside it:

```
/var/lib/cockpit-fleet/projects/<id>/collections/<sha256 of the requirements file>/ansible_collections/…
```

Not inside, because the tree of a git project is replaced at every fetch, the
one of a local project is not ours to write, and a managed one would get the
collections in its history. The digest is in the name so that changed
requirements are not answered by what was installed for the old ones: a
project is `installed` only while a directory with the digest of its current
file exists. One directory is kept, the last installed.

Every job that copies the project gets a copy of that directory as
`collections/`, last in `ANSIBLE_COLLECTIONS_PATH`: what the project carries
itself wins. It is a copy, not a link, for the same reason the project is:
runs of one project share nothing they can write. A playbook is refused while
its project's collections are missing; a syntax check or an inventory listing
goes on with what there is.

The installation is queued by an administrator (`project.collections`, which
installs again also when nothing changed: a requirement may name a branch),
and by the backend when the files of a project change and ask for something
that is not there: after a fetch, after an upload, when a requirements file is
saved in the interface. A local project, which nobody tells us about, shows
"not installed" until someone asks.

`fleetctl` reads the requirements file only to digest it, without following a
link; the digest it took names the directory, not something the job reports.
What the job leaves is taken only if it is a directory and within the size
limits of a project. In the job, ansible-galaxy clones a git source itself
and has no option for git, so hooks and local transports are turned off
through the environment (`GIT_CONFIG_COUNT`), next to the same
`GIT_ALLOW_PROTOCOL` a fetch has; ssh sources meet the trusted host keys. No
credential is offered to any source, and the servers a project's `ansible.cfg`
lists are not used (the allowlist drops them): public sources only, for now.
Downloads and clones go to the run's directory on disk, not to its runtime
directory, which is memory.

Checked on Debian 13 with ansible-core 2.19.11 (`galaxy.sh`): ansible-galaxy
skips what the controller or the project already has, so with Debian's
`ansible` package a requirement for `ansible.windows` installs nothing, and an
installation that installs nothing is still one. A collection of 1000 files
from a public git repository is installed in 7 to 17 s and takes 5.7 MB.

**Syntax checks.** Saving a file that looks like a playbook (a YAML list with
a play at its top level) queues a `syntax` job on a copy of the project:
`ansible-playbook --syntax-check`, then `ansible-lint --offline` on the same
file when `/usr/bin/ansible-lint` exists. The job's status is the syntax
check's; what ansible-lint found is in the output and marked on the run
(`options.lint`), and does not fail it. The same check can be asked for any
playbook of any project. It is a job and not a call because it reads a project:
ansible-lint loads the rules a project's `.ansible-lint` points to, which is
Python of the project (`files.sh` ships one and checks where it could write).
No vault password is given to a check.

Checked on Debian 13 with ansible-core 2.19.11 and ansible-lint 25.2.1, which
works inside the unit as it is.

**The editor.** Files, the text of an inventory and a template's variables
are edited with CodeMirror 6: highlighting (YAML with Jinja in it, INI, JSON,
Python), indentation, search. It costs about 650 kB of bundle, which goes from
0.65 to 1.3 MB. CodeMirror writes its styles into the page as a `<style>`
element, which Cockpit's content security policy refuses; the editor is
therefore mounted in a shadow root, where it uses constructed style sheets,
which the policy allows. The policy is not relaxed.

Completion for Ansible files has three sources. Keywords of plays, tasks and
roles are a list in the frontend, and where the cursor is (a play, a task, the
parameters of a module) is read from indentation, not from a parser: enough
for a suggestion, and silent when in doubt. The names of the modules come from
`module.list` and the parameters of one from `module.doc`, which run
`ansible-doc` in `fleetctl`. That is outside the sandbox, so it only looks at
what root installed: a closed environment, a throwaway home, `/` as working
directory, no project. Modules that a project brings in its own collections,
or that were installed for it from its requirements file, are therefore not
suggested. Listing the modules takes seconds (9201 of them
with the `ansible` package, 0.9 MB), so the list is kept in
`/var/lib/cockpit-fleet/cache` until a package or a system collection changes;
the documentation of one module takes 0.4 s and is not kept.

**A project's `ansible.cfg`** is not used as it is: a run gets a generated one
with only the allowlisted keys of the project's file (`roles_path` and
`collections_path` when they stay inside the project, `gathering`,
`pipelining`, `become` settings and a few more). Anything that would load
plugins from elsewhere, point to a vault password file or relax host key
checking is dropped. The integration test ships a project that tries.

**Help on modules.** `module.help` returns the whole of what `ansible-doc`
knows about one module: description, requirements, parameters with their
suboptions, notes, examples and return values. It runs like `module.doc`,
outside the sandbox on what root installed, so it has the same limit: no
module of a project's own collections. The interface shows it in a dialog,
opened from the page of the files and from the ad-hoc command.

**Inventories.** A file inventory is a path of a project: a file of hosts, a
directory of sources (read together, as Ansible does: static files and plugin
configurations side by side), or the YAML file of an inventory plugin. Every
one is read by a job, in the sandbox, so a plugin runs there and nowhere
else. A plugin gets no credential beyond the vault of the inventory.

A file inventory serves templates, ad-hoc commands and checks of any
project, not only its own: playbooks from a public git repository, say,
and the inventory of a school, with its vault, in a project of its own.
When the project of the job is another, the job gets, beside the copy of
its own project, a copy of what the inventory reads as `inventory/`, at
the same path: its directory, or the directory its file is in (its
`group_vars` and `host_vars`), the whole project for a file at its top.
The copy is made like that of a project, from `/` part by part, so a link
on the way is refused. Only the collections of the job's project are
copied: an inventory plugin that comes from the collections of the
inventory's project is not there. Until 0.1.0 such a template was
refused.

**Inventories written in the interface** are a tree of files, like a managed
project, with a git repository beside it:

```
/var/lib/cockpit-fleet/inventories/<id>/tree/       the files, what a run copies
/var/lib/cockpit-fleet/inventories/<id>/repo.git/   the repository
/var/lib/cockpit-fleet/inventories/<id>/lock        one change at a time
```

The `file.*` commands work on it by its id (an inventory's starts with `i`,
a project's with `p`), under the same rules as on a managed project: the
same paths, the same limits, a commit for each change, history, restore,
download, and a clone and push at a terminal; not `file.check`, since an
inventory has no playbook. A change reads the inventory again. Its source
is the whole tree, read as Ansible reads a directory of sources (a file of
hosts with `group_vars/` and `host_vars/` beside it, files of plugins with
the names they want, `*.aws_ec2.yml`), or a file or directory of it: the
inventory's `path`. A run gets a copy of the tree as `inventory/`, made as
the copy of a project is; an executable file in it is never run as a
script, whatever `[inventories] scripts` says, as before.

`inventory.create` and `inventory.update` still take `content`: the text of
its main file, `hosts.yml` (or `hosts` for INI), written as a commit;
`inventory.create` also takes `files`, every file of the tree, which is how
an export brings an inventory back. An inventory written before it was a
tree had its text in the database (`content`): the first time its tree is
needed, the text is made `hosts.yml` (or `hosts`) of a new repository, in a
commit of the service account, and dropped from the database. That needs no
step of the upgrade and no migration of files, and a controller that never
opens an inventory keeps its text where it was. The revision of the tree is
in the database (schema 20) and in what `inventory.get` answers.

This replaces one text field: `group_vars` per group, `host_vars`, a vault
file beside the others, several sources and a plugin with the name it wants
could not be written in the interface before, only in a project. A hidden
managed project behind each inventory was considered and left: every list,
export and check of projects would have had to leave it out.

A job enables Ansible's default inventory plugins but `script`: an executable
file of a project is then read as text and never run. `[inventories] scripts
= true` in `config.toml` puts it back, for the whole installation; the script
runs in the sandbox like the rest of the project. And
`ANSIBLE_INVENTORY_UNPARSED_FAILED` is set: an inventory that nothing can read
fails the job. Without it a playbook went on with no host and ended as a
successful run of nothing.

Checked on Debian 13 with ansible-core 2.19.11 (`inventories.sh`):
`ansible.builtin.generator`, a directory with a static file and
`ansible.builtin.constructed`, a script with the setting off and on.

**Host keys.** `hostkey.scan` returns the keys offered by one host, or by every
SSH host of an inventory, with their fingerprints; nothing is trusted until an
administrator confirms with `hostkey.add`. A run connects only to hosts listed
there. Each scanned key is marked new, already trusted, or changed: a key that
differs from the trusted one is refused unless the replacement is asked for
explicitly, because that is also what an impersonated host looks like. A
host is a name or an address, alone or in brackets with a port, and a key
type a plain name: each key is one line of `known_hosts`, and nothing in it
may read as a pattern, a list of hosts, a marker such as `@cert-authority`
or a second line, any of which would trust the key for other hosts. An
import takes host keys through the same checks, and a row stored before
them that fails one is left out of the `known_hosts` of a run.

**SSH keys with a passphrase.** Ansible has no option to read a passphrase from
a file, so `fleet-run` starts an `ssh-agent` inside the unit and loads the key
with `ssh-add`, which gets the passphrase from `fleet-askpass`. The agent lives
in the run's PID namespace and ends with it. The same helper answers git when
a token is used.

`fleet-askpass` is a packaged file because a run's scratch directory is
mounted `noexec`: a helper script written there, as a first version did, cannot
be executed. It prints the file named in its environment, so the secret itself
is still in no command line and no environment.

**Several vaults.** A template lists any number of vault credentials; each has
an identifier, and each reaches Ansible as `--vault-id IDENTIFIER@FILE`, in the
template's order. Credentials used together must have different identifiers.
One without an identifier is given as `default@FILE`, Ansible's own name for
it, never as the file alone: the path of a unit's credentials has the `@` of
the unit's name in it, and Ansible would split it there (found running
`ansible-win_edulab`, whose vault has no identifier).

**Limits a launcher can change.** Launching a template, an operator may add a
host limit and ask for a dry run. The limit narrows the template's own and
cannot widen it. They are not joined into one pattern: Ansible reads the
plain terms of a pattern as a union before its `&` ones, so
`template:&all:other` would reach `other` (found by the security review of
24 October). With both, the spec holds `limits`, and `fleet-run`, in the
sandbox, lists the hosts of each with `ansible-inventory --list --limit`,
keeps those in all of them, and runs the playbook with `--limit @FILE` of
their names. A name that a limit file would read as a pattern (a group's,
`all`, one with `*` or `[`) stops the run rather than widen it; so does an
empty intersection. It costs one reading of the inventory per limit.

**Ad-hoc commands.** `run.adhoc` runs one module on hosts of an inventory,
with no template: an inventory, a host pattern, a module, its arguments, and
optionally a machine credential, vault credentials, `become` and a dry run.
It is for administrators only, and the module must be in `[adhoc] modules` of
`config.toml`, by its full name (`ansible.builtin.command`): a short name
could be answered by another collection. The file belongs to root, so the
list is set by whoever administers the machine, not from the interface; the
default has what one looks at a fleet with (ping, facts, `command`, `shell`
and their Windows counterparts), and an empty list turns ad-hoc commands off.
An administrator can write any playbook anyway: the list is not what keeps
them from a module, it is what bounds a single call, which matters once the
caller is an agent (phase 3b).

The job does not call `ansible`: `fleet-run` writes a playbook of one play and
one task in the run's runtime directory and runs `ansible-playbook` on it,
with the same command line a template gets.

```
[{"name": "Ad-hoc command", "hosts": "all", "gather_facts": false,
  "tasks": [{"name": "<module>", "<module>": "<free form>", "args": {…}}]}]
```

So the module and its arguments are in a file and never in argv, the events
callback, the view by task, the outcome per host and the differences work as
for any run, and there is one way to build a command line to keep safe. The
arguments are those of a task: a line in free form (the command of `command`
and `shell`, or `key=value` pairs, as after `-a`), a mapping, or both; the
interface offers the line. The play targets `all` and the pattern is given as
`--limit`, so one that matches no host fails the run instead of ending with
nothing done. The arguments are written by an administrator and stay
templates, as in a playbook. They are kept with the run, in `spec.json` and in
the audit record, in clear: they are not for secrets.

The project is optional: a file inventory brings its own, and one can be
named for a module of a collection it carries or asks for. The playbook is
not inside the project, so variables beside the inventory are read, those
beside a playbook are not. The output is the one of `ansible`, what each host
answered (`ANSIBLE_STDOUT_CALLBACK=ansible.builtin.minimal`), since a play of
one task has no use for banners and a recap.

Checked on Debian 13 with ansible-core 2.19.11 (`adhoc.sh`).

**Surveys.** A template may ask questions at launch: typed fields (text,
number, choice, yes or no) with a default and bounds, defined by an
administrator. Each answer becomes a variable of the run, and this is the only
way an operator gives variables. `run.start` checks the answers against the
definition and refuses a variable that was not asked for.

Answers reach Ansible in a second variables file, after the template's own, so
an answer wins over a fixed value of the same name. The two files differ in
one thing. Ansible renders any variable holding `{{ ... }}` as a template
wherever it is used, lookups included: `{{ lookup('pipe', ...) }}` in a
variable is a command executed on the controller, inside the sandbox but next
to the run's decrypted secrets. The template's variables are written by an
administrator and stay templates. The answers are written as YAML with every
string tagged `!unsafe`, which Ansible uses as it is written; checked on
ansible-core 2.19 (Debian 13) and 2.20.

A survey cannot set a variable whose name starts with `ansible_`: those change
how a run connects (`ansible_ssh_common_args`, `ansible_python_interpreter`),
and belong in the template's variables. The answers are stored with the run
and in the audit record, in clear.

**Secret answers.** A secret field is the exception: its answer must not pass
through `fleetctl`, which would have it in memory, in the audit record and in
`spec.json`. Only root can encrypt, and operators could not reach
`fleet-secret`; letting them call `set` would let them replace any stored
secret. So the helper has one more form, `fleet-secret answer`, with no
argument: sudoers allows operators exactly that command line, and since the
caller chooses no name it can overwrite nothing.

```
browser ── value on stdin ──▶ fleet-secret answer        (root)
                                 │ /run/cockpit-fleet/answers/a-<random>.cred
browser ◀── ticket a-<random> ───┘
browser ── run.start {answers: {token: "a-<random>"}} ──▶ fleetctl
                                 │ moves the file to runs/<id>/creds/survey_0
PID 1 decrypts it for the unit; fleet-run writes it, tagged !unsafe, into the
variables file in the run's runtime directory
```

The ticket is not a secret and serves one launch: `run.start` moves the
ciphertext into the run, and a second use is refused. The waiting directory is
on tmpfs, writable by the service account so that it can take a file away, and
tmpfiles removes what no run took within an hour. The helper refuses to store
more than 500 waiting answers. The value is in clear in two places only, both
private to the unit and gone with it: the credentials directory and the
variables file. The run records which variables were given as secrets, never
their values. A secret field has no default and no bounds: a default would be
stored in clear, and nothing that could check a length ever sees the value.

**Masking.** A playbook that prints a secret would put it in the run's
output. What a run prints is therefore filtered against the secrets of that
run, the decrypted files of its credentials directory: passwords, vault
passwords, tokens, secret answers, and a private key line by line. Each is
replaced by `********` in two places: `fleet-run` filters every line before it
goes to `stdout.log` and to the journal, and the events callback filters the
values of an event before writing it (it is told the directory with
`FLEET_EVENTS_SECRETS_DIR`; the values, not the line of JSON, where a secret
that reads like a field name would break the event).

This is a net, not a guarantee, and `no_log` is still the way to keep a value
out. A value is looked for as it is and as JSON escapes it: base64, a hash, a
value cut over two lines are not recognised. Secrets shorter than four bytes
are left alone, since masking them would mostly hide ordinary output. And only
the secrets cockpit-fleet handed to the run are known: a password an inventory
takes from a vaulted variable is one the run decrypts itself.

## Schedules

A schedule launches a job template at the times of an OnCalendar expression,
with a limit, a dry run and answers to the survey stored with it. There is
one static timer, `cockpit-fleet-scheduler.timer`, every minute on the minute,
and its service runs `fleetctl scheduler.tick` as the service account, like
`cockpit-fleet-finish@`: the caller is `system`, with the admin role. A unit
per schedule was discarded: root would have to write units from what a user
typed, and the queue and its limit are needed anyway. The package enables
the timer and starts nothing else; `selfcheck` warns when it is not active.

**Times.** The expression is read by `systemd-analyze calendar`, so it means
here what it means in a unit, and a time zone of the tz database may follow
it (`timezone`, empty for the controller's own). The answer is read in UTC
(the `(in UTC):` lines, under `LC_ALL=C.UTF-8`); the same output was checked
on systemd 257 and 259. `next_at` is computed when a schedule is saved or
turned on, and by the tick from the current time after each time it handles;
a random delay, if asked for, is added to it then, so the list shows the time
it will really go. An expression with no time left is refused when saved.
`schedule.preview` gives the next times of an expression before it is saved,
and converts a crontab line: the five fields, names of days and months,
steps and the `@daily` family; a line that restricts both the day of the
month and the day of the week is refused, since cron runs when either
matches and OnCalendar wants both.

**The tick.** Under a lock (`scheduler.lock` in the state directory, taken
without waiting: a second tick does nothing), it first calls `reconcile`, so
a run lost by a reboot is recorded and a queued run left by it is started.
Then, for each schedule that is due, highest priority first, it moves
`next_at` on with a conditional update (a schedule changed meanwhile by an
administrator is left to the change, and a launch that fails in a way nobody
foresaw is not retried every minute) and then:

- more than five minutes late (`MISSED_AFTER`) means the controller was off:
  `missed`, unless the schedule catches up, in which case it is launched once
  for all the times lost;
- if a run of the same schedule is still waiting for approval, queued or
  running, `overlap` decides:
  `skip` (the default), `queue` this one too, or `cancel` the previous one;
- otherwise it is launched through `runs.launch`, the code of `run.start`,
  with the stored answers. The run's `created_by` is `schedule:NAME` and it
  carries `schedule_id` and the priority.

What happened is kept with the schedule (`last_outcome`, `last_error`,
`last_run_id`) and sent to the journal (`FLEET_SCHEDULE`, `FLEET_OUTCOME`).
The tick is not a mutating command: the audit trail has who created,
changed, turned on or off and ran a schedule, not one record per minute.

**Roles.** As for templates: an administrator creates, changes and deletes;
an operator turns a schedule on or off and runs it now (`schedule.run`, as
the operator, with the stored answers, without moving its times); a viewer
sees the list. A template that a schedule uses cannot be deleted.

**Answers.** They are checked against the survey when the schedule is saved,
and again at each launch, as the template may have changed since: then the
time is recorded as `error`, with the reason, and the schedule goes on. A
secret is never stored: a template with a required secret question cannot be
scheduled, and an optional one is left unanswered.

## Approvals

A job template may need approval (`needs_approval`): each of its runs waits
until someone other than who launched it lets it start. What waits is the
run as it will be: `runs.launch` prepares its directory as for any other
run (the copy of the project, the ciphertext of its secrets, a secret answer
taken from its ticket, `spec.json`) and records it as `pending_approval`,
which `dispatch` never starts. So what is approved is what runs, whatever
changes meanwhile in the template or the project, and no secret has to be
asked for again. Approving it makes it `queued`, behind nothing (it keeps
the time it was launched); rejecting it, or letting it expire, ends it as
`rejected` or `expired` and removes its directory, ciphertext with it.

```
run.start ─▶ pending_approval ──approval.approve──▶ queued ─▶ running ─▶ …
                    │ approval.reject
                    ├──────────────▶ rejected          the directory is removed
                    │ [approvals] expire_hours (24), by the tick
                    ├──────────────▶ expired
                    │ run.cancel
                    └──────────────▶ canceled
```

**Who.** An administrator, or a member of `cockpit-fleet-approver` whatever
their role, even none: an approver who is neither operator nor viewer gets
`whoami` and the three commands of approvals, and the page shows them only
the runs that wait, with what each would do (template, project and revision,
inventory and limit, dry run, the answers and which secrets were given).
Not the user who launched it, except an administrator, who could launch
the same playbook without the approval anyway by changing the template. A
run of a schedule is launched by `schedule:NAME`, so anyone allowed may
approve it; for that, only an administrator turns on a schedule of a
template that needs an approval (an operator who did could approve what it
launches, one person doing both). An operator still turns one off. An agent never approves: a call made for one has no right, and
the commands refuse it whatever its role; `approval.list` is a tool,
deciding is not. Through `fleet` at a terminal, a person does.

**Records.** The table `approvals` (schema 16) has one row per run that
waited: who asked and when, until when, the decision, who took it and when,
and a comment. A decision is claimed by a conditional update, so two
approvers at once give one decision and a conflict. Every decision made by
a person is in the audit trail; an expiry is in the journal. `run.get`
carries the approval of its run and whether the caller may decide.

**Telling.** A rule may ask for `approval`: a run that starts waiting is
told to its channel, with a link to the run. A rejected or expired run is
told as `canceled`.

## Workflows

A workflow chains job templates into a graph. A step is a template with a
limit that narrows its own and answers to its survey (no secret: as for a
schedule, a template with a required secret question cannot be a step), and
may wait for an approval even if its template does not ask for one. A link
says when a step runs after another: when it succeeds, fails, or always. A
step with no link to it runs at launch; one with several waits until all the
steps before it have ended, then runs if one of its links holds, or is
skipped, and so are those that only follow it. Loops are refused, so a step
runs at most once.

**Runs.** `workflow.launch` (operator) records a run of kind `workflow`.
It has no unit and no place in the queue (the lanes of `dispatch` leave it
out); it holds a copy of the steps and links, so that changing the workflow
does not change a run under way. Its steps are runs of their templates,
launched through `runs.launch` as the user who launched the workflow, with
`workflow_run_id` and `workflow_node`; they queue, wait for approvals and
are told about as any other run.

```
workflow.launch ─▶ run (kind workflow, running) ─▶ advance()
                                                   │ steps whose links hold
                                                   ▼
                        runs.launch(step) ─▶ queued/pending_approval ─▶ … ─▶ _finish
                                                   ▲                          │
                                                   └──────── advance() ◀──────┘
```

`workflows.advance` looks at the latest run of each step, starts those whose
time has come and records the end when nothing is left: it is called when a
step ends (`jobs._finish`), at launch, and for every workflow still running
by `reconcile`, so the tick takes up one left behind by a crash. One process
at a time per workflow run (a lock file in its directory); a step that ends
while the same process advances it (its unit could not start) asks for
another pass. A step that cannot even be launched (its template changed and
the stored answers no longer fit, its project has no files) counts as
failed, with the reason kept in the run. The workflow ends `failed` when a
step failed (or was rejected, expired, canceled) and no step runs for that
failure: a link "always", such as one to a step that tells about the
outcome, does not handle it. Otherwise `successful`.

**Passing on.** A playbook that calls `set_stats` with `per_host: false`
leaves data that the callback writes with the stats of the run
(`artifacts`), and `_collect` keeps it with the run (at most 64 kB of
JSON). A step gets what the steps before it in the graph passed on, merged
in the order they ended, as an extra-vars file of its own, after the
template's variables and the answers of its node: what was passed on wins,
as in AWX. A playbook wrote it, so every string at every depth is tagged
`!unsafe` and is never rendered as a template in the next step. It is kept
in clear, like extra variables: not for secrets, which only the known ones
are masked in.

**Cancel and resume.** Canceling a workflow run (`run.cancel`) asks for it,
cancels its steps under way and starts nothing more; it ends `canceled` once
they have. `workflow.resume` (operator), on a run that failed or was
canceled, makes a new run with the same steps and links as that one, where
the steps that succeeded there count as done, with what they passed on, and
the others run. The run it resumes stays as it ended.

**Roles and records.** An administrator creates, changes and deletes; an
operator launches, cancels and resumes; a viewer sees the workflows and the
progress of their runs (`workflow.progress`). A template used by a workflow
cannot be deleted (table `workflow_templates`). Workflows are in the
exported document, their steps naming templates by name. Schema 17.

## Notifications

A rule names the ends of a run to tell about (`failed`, which covers `error`
too, `changed`, `succeeded`, `canceled`, which covers a run rejected or
expired before it started), a run that waits for approval (`approval`), which runs (every one, or those of
one project, template, schedule or inventory) and a channel; what a rule
says about checks, and about check cycles falling behind (`late`,
`caught_up`) or that cannot be prepared (`unprepared`, `prepared`), is in "Checks". Channels are Telegram, a
webhook, mail (SMTP) and Matrix, written with the standard library. Only
playbook runs and ad-hoc commands are told about, not the jobs that serve a
project.

```
reconcile → _finish(run)  ── rules ──▶ notifications (the outbox, one row per channel)
                                           │ writes /var/lib/cockpit-fleet/notify.wake
cockpit-fleet-notify.path ─────────────────┘
   ▼
cockpit-fleet-notify.service    User=cockpit-fleet, secrets/notify/ decrypted by PID 1
   │ fleetctl notification.deliver
   ▼
Telegram, webhook, SMTP, Matrix
```

**Waking without privilege.** The service account cannot start units but
run units, through `fleet-unit`. A path unit needs nothing of it: writing
`notify.wake` is enough, and systemd 257 starts the service again when the
file was written while it ran (tried on the VM). Retries need no timer of
their own: the tick of the scheduler writes the file when a notification's
time to be tried again has come. No root code was added.

**Secrets.** A channel has one secret (a bot token, an access token, a
password; optional for a webhook and for mail), stored with `fleet-secret`
under the name `n-<channel>-token`. `fleet-secret` keeps the names that begin
with `n-` in `/etc/cockpit-fleet/secrets/notify/`, and the unit has
`LoadCredentialEncrypted=notify:` on that directory: PID 1 decrypts the
secrets of the channels and nothing else, into
`notify_n-<channel>-token.cred` (systemd keeps the extension). `fleetctl
notification.deliver` refuses to run without `CREDENTIALS_DIRECTORY`, so
only the unit delivers. A secret is never in the database, the journal or
an error: Telegram's token is part of the URL, and every error text is
masked before it is stored. A webhook URL may say `$secret`, for the
services that put the token in the address (Slack, Discord); otherwise the
secret goes in a header the channel names.

**The outbox.** One row per run and channel, however many rules match (a
workflow run and each of its steps are runs of their own); a
run with changes that a rule wants as `changed` and another as `succeeded`
is told once, as `changed`. Channels are tried one after the other in a
delivery, each step of a request waiting 15 s at most, so one that does not
answer delays those after it by that much, and is not tried again in the
same delivery.
A failure is tried again after 1, 2, 4 … minutes, at most an hour apart,
for a day, then given up (`failed`), which an administrator can try again.
Matrix gets the same transaction identifier when the same message is tried
again, so a request that arrived but whose answer was lost is not posted
twice. A redirect is an error, not followed: it would carry a token to
another host. Anything else that goes wrong while sending (a server that
does not speak HTTP, a header a secret makes invalid) is a failed attempt
like the others, said by the class of the error only, as its text may quote
the secret; before, it stopped the whole delivery, every tick. An error
keeps an API's own reason, never the raw body of an answer (viewers read
errors, and the URL may be one of the network's own). A mail's subject is
made one line: a host name from an inventory may hold a line break. A
secret with a control character, or with a space for Telegram and Matrix,
is refused before anything is sent. Ended notifications are kept 90 days.

**Grouping and limit.** `window` (seconds, per channel) makes a run's
notification wait: whatever else ends meanwhile leaves in the same message.
Beyond `max_per_hour` messages in the last hour, notifications wait until
one more may go and then leave as one. A test message (`channel.test`) goes
at once and alone.

**Messages.** English or Italian, chosen per channel, written in the backend
(`notify._WORDS`): the outcome, template, project, schedule, who launched,
hosts by outcome and which failed, and a link to the run,
`<[notifications] url>/fleet#/runs/ID` (by default
`https://<fqdn>:9090`). A webhook's body is a JSON template whose strings
have `$title`, `$text`, `$link`, `$count` replaced; a string that is only
`"$events"` becomes the list of events. Substitution happens inside the
parsed strings, so nothing in a message can change the shape of the JSON.

**The unit** is the backend unsandboxed as `cockpit-fleet-finish@` is,
plus what a program that only talks to the network can bear:
`RestrictAddressFamilies`, `@system-service`, no capabilities, no access to
runs and projects. `systemd-analyze security` rates it 1.5. HTTP goes through
`[network] proxy`; mail goes straight to its server.

**Roles.** An administrator creates channels and rules, sets secrets, sends
tests and tries again; anyone with a role sees the channels (not their
secrets), the rules and the notifications. Channels and rules are in the
exported document, without secrets.

Checked on Debian 13 (`notify.sh`): a failed run reaches a Telegram Bot API,
a webhook, an SMTP server and a Matrix homeserver (stand-ins on the VM,
`notify-sinks.py`), each with its secret where it belongs and in neither the
database nor the journal; a channel whose server was down got the
notification about 90 s later, from the tick, once it answered.

## Checks

A check is a kind of the catalog (reachability, disk, processor, memory,
services, uptime and reboot, certificates, clock, and the slow kinds `cmdb`
and `updates`, see "The inventory of hosts"), a playbook of the user, or a
playbook run for compliance, applied to a group of an inventory, with an interval, the attempts
before a problem is confirmed and the interval meanwhile, parameters,
thresholds and a machine credential. Each host of the group has a state for
it. Nothing is installed on the hosts: Ansible reaches them as for a run.

```
scheduler.tick ── due states, by inventory, credential and become ──▶ shards of hosts
   │ jobs.queue: a run of the kind "check", its plan in spec.json
   ▼
cockpit-fleet-run@ID   the same sandbox, a limit of its own ([checks] max_concurrent)
   │ one task per host: lineadicomando.fleet.checks → checks.py or win_checks.ps1
   │ events.jsonl
   ▼
reconcile → checks.ingest ── thresholds, soft and hard ──▶ check_states, state_changes
   │ a hard state that changed
   ▼
notify.check_event → the outbox, as for runs
```

**The hosts** come from the `inventory` job: when it succeeds, `hosts` gets
a row per host of that inventory (the identity is the inventory and the
name), with its address and groups, and loses those that are gone, with
their states. A check on an inventory never read queues the job, and the
tick reads again, every hour, the inventories that checks use. What a host
is (platform, system, kernel) is what the first cycle reports; no fact
gathering is spent on it.

**One task per host.** The catalog is one module in two files: `checks.py`
for Linux and `win_checks.ps1` for Windows, with the same parameters and the
same output. Ansible would not choose between them by itself: for a host
without a PowerShell shell it looks a module up with no suffix, and took the
`.ps1` (found on ansible-core 2.20). So the task names the action
`lineadicomando.fleet.checks`, which runs on the controller and calls one
module or the other according to the shell plugin of the connection. Every
check of a host runs in that one task, which matters on Windows, where a
task costs 4 to 5 s: eight checks take 5.4 s there (`windows.sh`). The
Windows module needs nothing but ansible-core (`Ansible.Basic` is part of
it), so AlmaLinux, which has no `ansible.windows`, checks Windows too, and
it sets the invariant culture, so that a summary says `32.2%` whatever the
language of the node. The Linux module runs on Python 3.6 and later, what
ansible-core 2.16 supports on a node, with the standard library only;
certificates are read with a reader of DER of its own.

**The plan** is data. `fleet-run` writes it into the runtime directory as a
variables file whose strings are all tagged `!unsafe`, the hosts into a file
given to `--limit @…`, and the playbook (one play, `strategy: free`, the
task with a `timeout`) beside them; nothing of it is in the command line. A
service named `{{ lookup('pipe', 'id') }}` reaches the node as those
characters.

**Results are untrusted.** They are written by the nodes. A result is taken
only for a check planned for that host in that cycle, and the host is the
one Ansible names in the event, never one a result names. States, metrics
(name, value, unit, instance, bounds), summaries and details are checked and
cut to size; a node cannot report for another host, nor for a check it was
not asked for.

**Thresholds** are applied on the controller, at ingest, so that changing
one needs no new collection and a node does not decide whether it is in
trouble. Each metric may have a warning and a critical; when the critical is
below the warning, lower is worse (the days before a certificate expires).
A check sets only what differs from the catalog's defaults. The modules of
the catalog report metrics and leave the state to the thresholds (they say
`unknown` when they cannot measure); the clock's offset is computed here,
from the time the node says and the time of the event.

**States.** Ok, warning, critical, unknown, with unknown between warning and
critical when they are compared. A problem is soft until it has been seen
`max_attempts` times in a row, checked again at `retry_interval` meanwhile;
then it is hard. A change between two problems is hard at once, and so is a
recovery. The next time is counted from when the cycle was planned, on the
tick, with 10 s of slack: counted from when the result arrived, it slipped
to the following tick every time (177 s instead of two intervals of 60).
Every change of the last state or of the hard one is kept in
`state_changes`. A check that changed in at least half of its last 20
results, with 10 at least, flaps, and stops flapping under a quarter.

**Unreachable hosts.** A host Ansible cannot reach has its `reachability`
check critical and its other checks unknown and suppressed: they keep their
hard state and their count, and nothing is told about them. A host is told
about once, not once per check: `reachability` should be applied to every
host.

**Cycles.** A cycle is a run of the kind `check`, in the same unit as every
job, with a limit of its own, `[checks] max_concurrent` (1), apart from
`[runs] max_concurrent`: checks do not wait behind a long playbook, nor a
playbook behind them. What is due is grouped by inventory, credential and
`become`, then cut in shards of at most `[checks] shard_size` hosts (50),
as even as they can be, each a cycle with at most `[checks] forks` (20);
fewer of either when what the last cycles took says so (below). A state held
by a cycle is not planned again until it ends: when it is due again
meanwhile, that due time is skipped (below). ansible-playbook says 2 when a host failed and 4 when
one could not be reached: for a cycle these are outcomes of hosts, which
their states record, so the cycle is successful and its unit is not left
failed; with a host down, a failed unit a minute would pile up. The output
of a cycle is the default callback without the hosts that went well, so the
journal gets a line per failure, not the results. The slow kinds (`cmdb`,
`updates`, `compliance`) go in cycles of their own kind only, with
`[checks] slow_timeout` (900 s) as the time limit and a lane of the dispatch
of their own, as many as `[checks] max_concurrent`: a Windows Update search
of some minutes does not hold back a disk check. The lane is the `slow`
mark in the options of the run. Cycles are not in the
list of runs unless asked for (`run.list` with `kind: check`), and are
deleted with their directory after a day, by the tick: their results are in
the states.

**Connections.** Within a run, SSH keeps one connection per host
(`ControlMaster=auto`, `ControlPersist=60s`), its socket in the run's
runtime directory, gone with it. The catalog is pipelined
(`ANSIBLE_PIPELINING`, set by `runner.check_env`): its one task per host
goes as one command over that connection, instead of a directory made on
the node, the module copied, run and removed. A host whose inventory sets
`ansible_pipelining` keeps that, a playbook of the user keeps what its
project says, and SSH to a PowerShell shell is always pipelined by Ansible.
Connections are not kept from one cycle to the next. Kept by the account,
any run of any project could use the open connections of another inventory,
`become` included, without its key (rule 8 of `CLAUDE.md`). Kept by a unit
per group, mounted into its cycles only, they would need a unit that stays
up, `fleet-unit` adding mounts per run, and sessions that outlive a
credential revoked, a host key no longer trusted, or options (forwards, a
`ProxyCommand`) that the first cycle's inventory gave them; that is weighed
again only if the load test shows that opening connections is what keeps a
controller from its target. Nor are facts cached: the checks gather none
(`gather_facts: false`), what is known of a host comes from the checks.

**Sizing.** Every cycle leaves a row in `cycles`: its group (the
inventory, and the kind or the check of the user when it goes in cycles of
its own), its hosts, checks and forks, when it started and ended, the peak of
its memory and its processor time. `fleet-run` reads the last two from its
own cgroup (`memory.peak`, `cpu.stat`) once Ansible has ended, and writes
them in `result.json`: it is the main process of the unit, and systemd
forgets both once the unit has stopped (`MemoryPeak` of a stopped unit is
not set). They are written in the run's directory, so believed only as far
as their shape: a playbook of the user could skew the sizing of its own
cycles, nothing else. The rows stay 30 days, after the cycles themselves are
gone. The planner sizes the next cycle of a group on its last 20 successful
ones:

- forks: as many as fit in the memory one cycle may take, `[checks]
  memory_mb` (by default half the controller's) shared by every cycle that
  may run at once (`max_concurrent` in each of the two lanes). The memory of
  a cycle is fitted as a base plus so much per fork when cycles ran with
  different forks, else all of it is counted per fork, which errs low and
  grows back as cycles with more forks are measured;
- hosts: as many as those forks get through in half the shortest interval of
  what the group checks, at the time a host took a fork (the time of a cycle
  times its forks over its hosts), and never fewer than the forks.

With nothing measured, the configured values hold. They stay the most there
may be: sizing only takes away, since more forks than configured is more
memory than the administrator agreed to, and a larger shard only saves the
start of a playbook. What a cycle is too slow for, sizing cannot fix: it
ends late, and the next is not planned until it does.

**At scale.** `test/integration/load.sh` runs real cycles, started by the
timer, on aliases of the controller with the local connection (the modules
then measure the sandbox of a run) and a few container nodes over SSH, ten
checks each: nine of the catalog (seven kinds, `disk` and `certificates`
twice) in one task per host, and a playbook of the user in cycles of its
own. Measured on a Debian 13 machine with 2 vCPUs and 4 GB, every 10
minutes (21 October): a cycle of 50 hosts and 20 forks (`shard_size` and
`forks` as configured; sizing kept both, half the interval being far off)
takes ~9 s for the catalog and ~6 s for the playbook, ~680 MB at its peak,
0.27 s of processor per host; its results are taken in within 0.3 s. With
`[checks] max_concurrent = 1` the cycles of an interval run one after the
other: 500 hosts make 20 of them, done in ~3 minutes, the processor at
~93% meanwhile and idle the rest; the last waits ~160 s in the queue.
1000 hosts make 40 and the last waits 266 s, near half the interval, which
is where a second cycle at once (`max_concurrent = 2`) would be needed.
What gives first is the interface: `state.list` returns every state, and
with 10 000 of them answers in 0.44 s (median) and 2.9 s (p95, while
cycles run), against 0.27 s and 1.3 s at 500 hosts and 0.16 s and 0.18 s
at 200; the other calls stay at ~0.1 s median. Three nodes over SSH beside the aliases took ~2.1 s per cycle,
0.33 s of processor per host: too few to weigh what opening connections
costs at scale. Over 10 hours with 500 hosts (21–22 October, stopped by
the host's shutdown, not by the test) the cycles held the same: 1289,
every one successful, none skipped, the oldest result 1.13 intervals
old, at least 2.8 GB of memory available and no swap. But `state.list`
came out worse than in the first 1.6 h: 0.30 s median, 2.5 s at p95 and
3.9 s at most, against 2 s and 5 s allowed (`host.list` 1.6 s at p95,
the others under 0.5 s). Timed in phases on the same database (22
October), the cause was not the query: with no cycle running `state.list`
took 0.17 s inside fleetctl (5030 states, 3.6 MB of JSON), with one 1.1–1.7
s, and importing Python alone went from 0.07 s to 0.9 s. systemd had left
the cpu controller off in `system.slice`, so every process there competed
on its own and the forks of a cycle took the processor from fleetctl. Runs
now go in `system-cockpitfleet.slice`, with `CPUWeight=20`: under
contention the interface and the units that record results come first,
and with nothing else to do the runs still have all of it. Set at runtime
on that machine, it brought `state.list` back to 0.17 s while a cycle ran.
The page of hosts also stopped asking for every state but in its grid: the
default view asks for the problems, with the counters of all. With both,
24 hours with 500 hosts (22–23 October, the target of phase 8) passed
every check of `load.sh`: 3124 cycles, every one successful, none
skipped, no group behind, the oldest result 1.05 intervals old, the last
cycle of an interval waiting at most 176 s, at least 2.5 GB of memory
available and no swap; every call of the interface under 0.4 s at p95
and 0.75 s at most (`state.list` of the problems 0.17 s at p95, every
state for the grid 0.38 s). The database grew by ~60 MB a day (raw
metrics are kept 14 days, so it levels off later), the run directories
by ~400 MB a day, which levels off after the first day: those of check
cycles go after a day (Retention), so a test of 24 hours ends just
before it shows.

**Skipped, and falling behind.** A cycle answers the due time it was queued
for; when one interval later it has not ended (still queued counts too),
the next due time of each state it holds is skipped, never doubled, and
counted once: `skipped` in the state, how many of its due times have passed
since the cycle was queued, the same reckoning as the next time of a result.
The tick adds what is new to the options of the cycle, which end in its row
of `cycles`, and writes a warning to the journal for each group with the
due times it skipped. A hundred hosts behind by one cycle are one due time
of the group, not a hundred. `cycle_lag` keeps, per inventory and group,
the due times skipped in a row and since when; at `[checks] late_after` (3)
the group is `late`, told once to the rules that ask for it. It is
`caught_up` when one of its cycles ends with nothing skipped and no other
cycle of the group still holds a skipped state, so that a small shard on
time does not end the delay of a large one; the row is then deleted, and a
delay told about is told to be over. A row nothing has added to in 30 days
goes with the measures: its group is gone. These events are about the
controller, not a host: their rules have the scope of everything or of an
inventory, and their message links to the System tab.

**Cycles that cannot be prepared.** A cycle the tick cannot prepare (its
project gone, or one the scheduler cannot see) leaves each check it would
have held unknown, with the reason as summary. Unknown is not a problem of
a node, so rules about problems would stay silent while the checks never
run. `cycle_faults` keeps, per inventory and group, the reason and since
when: the first failure is `unprepared`, told once with an error in the
journal, and the first cycle of the group queued afterwards is `prepared`,
which deletes the row. Among the cycles of a group in one tick (one per
credential), one failure is enough to hold the fault. The System tab shows
the group as "Cannot start", with the reason. Same scopes and link as
`late`.

**Checks of the user.** A check of the kind `playbook` names a playbook of a
project; it runs alone, on the hosts of its group, sharded the same way.
Its last task is `lineadicomando.fleet.fleet_result` with a state, a summary,
metrics and details: an action, which runs on the controller and sends
nothing to the node, so it is the same on Linux and Windows. It checks what
it is given and hands it back; ingest takes the last one of each host and
applies the thresholds of the check, keeping the worse of that state and
the one the playbook gave. A task that failed (and was not ignored) makes
the state unknown with its message. Every task has `[checks] timeout`
(`ANSIBLE_TASK_TIMEOUT`).

**Notifications.** After each ingest and at each tick, a hard state that
differs from the last one told about is told: `problem`, or `recovery` from
a problem told about. A problem nobody acknowledged is told again after
`[checks] reminder_hours` (24; 0 never) as `reminder`. Nothing is told for a
check that flaps or whose host is unreachable, nor for a host in a
maintenance window: what is still a problem when the window ends is told at
the next tick. Rules have three more scopes: an inventory (its runs and its
checks), a group of an inventory, one check; the scopes of a project,
template or schedule have runs only, those of a group or a check have checks
only, and a rule that mixes them is refused. The message links to the host,
`/fleet#/hosts/ID`.

**Roles.** A viewer sees hosts, states and their history; an operator also
acknowledges a problem (until it is ok again), runs a check now, and opens
and closes maintenance windows (a host, or a group of an inventory, at most
90 days); an administrator configures checks. Checks are in the exported
document, maintenance windows are not.

**The interface.** "Hosts" has the counters, the problems with what can be
done about each, the hosts, and a grid of hosts and checks; the page of a
host (`#/hosts/ID`) has what it is, its checks with their metrics, and its
last changes. "Checks" has the checks and the maintenance windows.

The list of checks is filtered, grouped and sorted in the browser, from
what `check.list` already returns (see "Long lists" under the interface):
a text searched in the name, the kind, the inventory, the group and the
playbook; the state (with a problem, critical, all OK, not checked yet);
the inventory; the kind, in four families (resources: disk, cpu, memory;
services and system: reachability, services, uptime, certificates, clock;
inventory and compliance: cmdb, updates, compliance; playbooks of the
user); enabled or not. It is grouped by inventory (the default), family,
worst state or not at all; a group with a problem starts open, one all OK
closed, and every group is open while a filter is on. Ended maintenance
windows are hidden unless asked for. The logic is in `src/lib/checks.ts`.

Checked on Debian 13 (`checks.sh`, four Linux containers and an address
with no host): every kind of the catalog and a playbook of the user, an
unreachable host told about once, a disk of 16 MB filled up told about in
117 to 119 s with an interval of 60 s and two attempts, and its recovery;
and on Windows 11 over SSH (`windows.sh`), the catalog with the same metrics
as on Linux, `C:` for a mount point.

## The inventory of hosts

What each host is made of, the updates it lacks and whether it drifted from
what a playbook describes: three kinds of check, so that they inherit
planning, sharding, the sandbox, states, notifications, acknowledgement and
maintenance windows, decided with the user on 16 October.

```
cmdb      checks.py / win_checks.ps1 ── hardware, packages, services ──▶ cmdb_hosts, cmdb_packages, cmdb_services
                                                       a difference ──▶ cmdb_changes
updates   checks.py / win_checks.ps1 ── what is missing, security apart ──▶ cmdb_updates
compliance  a playbook of a project in --check --diff ── tasks that would change ──▶ drift
```

**Collection.** `cmdb` and `updates` are kinds of the catalog in the same
module as the other checks; their data travel in the result beside the
state, in `cmdb` and `updates`, because a node's packages do not fit in
`details` (16 KB). The events of a cycle keep lists of up to 50 000 entries
(`FLEET_EVENTS_MAX_ITEMS`, 200 for a run). On Linux: hardware from `/proc`,
`/sys/class/dmi` (the serial number only with `become`), `/sys/block`,
`/sys/class/net` and `ip -j address`; packages from `dpkg-query`, `rpm` or
`pacman`; services from `systemctl` when systemd runs. On Windows: CIM, the
uninstall keys of the registry (what "Apps and features" lists, 64 and 32
bits), `Get-NetAdapter`. Services are normalised to `running`, `stopped`,
`failed`, and `enabled`, `disabled`, `manual`, `static`.

**Updates.** apt by `apt-get -s dist-upgrade` (security when the origin says
so), dnf by `check-update` and `updateinfo list --security`, zypper by its XML
output and the security patches; the metadata the node has is used, and
`refresh` (which needs `become`) updates it first. Windows Update is searched
through its COM interface without downloading anything, from the SSH logon
with a key, with no `become` (`windows.sh`, Windows 11, 16 October). The node
reports the metrics `updates` and `security_updates`; the default threshold
is a warning from one security update.

**Storage.** The last snapshot of each host in normalised tables, the history
as changes only: a package that came, went or changed version, a service
that came, went or changed whether it starts at boot (not whether it runs,
which is a matter for the checks), a change of hardware, a change of the
addresses of an interface. The first snapshot records none. Changes are kept
365 days, then deleted by the tick (see "Maintenance"). What a node
sends is read as untrusted: cut to size (20 000 packages, 5 000 services and
updates, 64 disks and interfaces), malformed entries left out. Memory that
moves by less than 5% is the kernel keeping more or less for itself, not a
change.

**Hardware.** A change of hardware (vendor, model, serial, processor, their
number, memory, disks, the MAC address of an interface) makes the `cmdb`
check warning for that cycle, hard at once whatever its attempts, so that
the rules tell about it once, and a recovery follows at the next cycle.

**Drift.** A check of the kind `compliance` names a playbook of a project,
which runs alone in `--check --diff`. Its events say, per host, which tasks
would change something and their diffs (those of the items of a loop
joined); the metric `changed` counts the tasks, with a warning from one by
default. A failed task makes the state unknown, an unreachable host is
suppressed as for any check. The diffs of the last cycle are kept per host
and check in `drift` (256 KB at most).

**Roles.** A viewer sees the CMDB (`cmdb.list`, `cmdb.get`, `cmdb.packages`),
which says no more than an outcome would of what is installed. The diffs of
drift may hold the content of files: `drift.get` is for operators and
administrators, as the output of runs.

**The interface.** "Assets" has the hosts with their system, hardware,
updates and compliance, filtered by any word and counted by system, exported
as CSV (a cell that would start a formula is opened by an apostrophe) or
JSON, and a search of a package across hosts. An export is made in the
browser as a `blob:` link; Firefox checks such a download against the
`frame-src` of the page's policy, which Cockpit sets to `'self'` by default,
so the manifest adds `frame-src 'self' blob:`. The page of a host has what it
is made of: hardware, packages, services, updates, changes and, for an
operator, drift. From its updates an operator launches a template of the
same inventory, narrowed to that host, through the usual launch (survey and
dry run included): which template applies updates is the user's, the
plugin installs nothing by itself.

Checked in containers of Debian 13, Fedora 44 and AlmaLinux 10 (`cmdb.sh`,
on 16 October): a Debian node's packages
from dpkg, a package removed by hand recorded as a change, a managed file
changed by hand as drift at the next cycle, its diff for an operator and not
for a viewer. The module was also run by hand in containers of Fedora 44
(dnf5), AlmaLinux 10 (dnf4, security updates told apart) and openSUSE Leap
15.6 (zypper, which had no update to find).

## Time series

The values the checks measure are kept over time, in `metrics.py`, for
the charts and for sizing.

```
ingest ── a result's metrics ──▶ metric_series      one per host, check, metric, instance
                                 metric_points      (series, time) → value, as it came
                                 metric_hourly      (series, hour) → count, lowest, highest, sum
                                 metric_daily       (series, day)  → the same
```

**Narrow rows.** A series is a metric of a check on a host with its instance
(a mount point, a certificate); its values are rows of the series, a time in
whole seconds and the value, in tables `WITHOUT ROWID` whose key is those
two, so that a value costs about 25 bytes and is reached through the key.
The time is the one of the event that brought the result, believed within
five minutes of the cycle, else the time it was taken in. A second value of
a series at the same second is left out. A host that could not be reached,
a task that failed, a result suppressed record nothing: a gap is a gap.

**Aggregates as values arrive.** Each value is added to the bucket of its
hour and of its day (UTC) by an upsert, so no pass reads the values back to
sum them and the current hour is already in the charts; an average is the
sum over the count. Retention is then deletion only: `[metrics] raw_days`
(14), `hourly_days` (90), `daily_days` (730), checked not to decrease. The
tick deletes past each, one series after another through the key (`series_id
IN (…) AND ts < ?`); a series that stopped reporting goes a day after its
daily values. Deleting a host or a check takes its series with it.

**Reading.** `metric.query`, for viewers like the states it comes from,
returns the series of a host, narrowed by check, metric and instance, over a
span: as they came for up to two days within `raw_days`, hourly up to 62
days within `hourly_days`, daily beyond, or as asked; at most 5000 values a
series, saying when it was cut.

**Charts.** The page of a host draws them under a check once its row is
opened: a day, a week, a month or a year, one chart per metric and one line
per instance (a mount point, a certificate), loaded again when the check has
new values. Aggregates of a single line are drawn as their average with a
band from the lowest to the highest. A line breaks where the distance from the
previous value is more than two and a half times the usual one (the median,
or the hour or day of an aggregate), so a host that could not be reached shows
as a gap. A percentage is drawn from 0 to 100, anything else from 0 unless it
goes below; bytes get ticks at round binary sizes. What turns the answer
into lines is in `src/lib/metrics.ts`, tested apart from the page.

They use `@patternfly/react-charts` 8 (Victory 37), the major that goes with
PatternFly 6. It costs about 380 kB of minified bundle, a quarter of the rest;
the bundle is therefore split (`splitting` in `build.js`) and the charts are a
chunk of their own, loaded the first time a check is opened. A page that shows
no chart loads as before. `build.js` empties `dist/` before a build, since
chunks are named by their hash and the package copies the whole directory. A
page loaded before an upgrade asks for a chunk that is no longer there: it
then says to reload the page instead of failing.

Measured on the development host (20 October), 500 hosts with 10 metrics
each: a cycle's 5000 values are written in 0.12 s outside a transaction
(0.06 s in one), the tick's deletion takes 12 ms when nothing is due and
0.1 s for a day of 125 000 values. At the target of phase 8 (500 hosts, 10
checks every 10 minutes, about 20 values per host) 14 days of values are
some 20 million rows, about 500 MB. The load test found the database
growing faster than that: ~47 MB a day at 200 hosts over 1.5 hours,
which would be ~120 MB a day at 500 and well above 500 MB in 14 days; the
24 hours at 500 hosts measured ~60 MB a day, while retention had yet
to delete anything.

## Maintenance

Every retention is applied in one place, `maintenance.tidy`, at each tick
after the checks; only the outbox of notifications is pruned by the service
that delivers it (90 days). One step that fails is written to the journal
and does not keep the others from running.

| What | Kept | How |
|---|---|---|
| Check cycles | a day | row and directory deleted |
| What check cycles took | 30 days | deleted |
| Output of other runs | `[runs] keep_days` (90) | directory deleted, 500 a tick at most; the row stays with its outcome and `pruned_at` |
| Directories of `runs/` with no run | a day | deleted, the directory looked through once an hour (a stamp file) |
| CMDB changes | 365 days | deleted |
| Metrics | `[metrics]` (14, 90, 730 days) | deleted, see "Time series" |
| Runs, state changes, audit | always | |

A run whose output was deleted says so in its page, in `fleet log` and in
`fleet result`: its status, rc and outcome per host are in its row.

**Giving space back.** Deleted rows leave free pages, which new rows reuse:
at a steady state, the values that expire make room for those that arrive,
and the file stops growing. Giving pages back to the file system matters
after a large deletion (a shorter retention, a check removed). A database is
created with `auto_vacuum = INCREMENTAL`; the tick then gives back up to
32 MB of free pages at a time, and only when they are a quarter of the file
or more, so that it does not shrink a file that will grow again at once. A
database made before is rewritten once by `VACUUM` to work that way, if it
is below 64 MB (every one made before phase 8); a larger one is left as it
is. The scheduler's unit has a private `/tmp`, where `VACUUM` writes its
copy.

## System health

`system.health` says how the controller keeps up, from what is already
kept, for viewers like `selfcheck`: watching the checks includes knowing
whether their results are fresh. It holds no configuration beyond the
memory a cycle may take. The System tab shows it above the installation
checks, read again every 30 s.

- For each group of cycles that the enabled checks make (by inventory: the
  checks that go together, each slow kind, each check of the user): its
  shortest interval; from `cycles`, over the last day, how many cycles
  ended and how many not successfully, their usual (median) and longest
  duration, the highest peak of memory against the budget of one cycle,
  the due times they skipped (with those of cycles still under way); the
  delay of `cycle_lag`; and the forks and hosts per cycle the planner would
  choose now, from `checks.size()`, measured or as configured.
- The queue of each lane of the dispatch (`jobs.lanes()`, the ones
  `dispatch()` uses): running against its limit, waiting, the longest wait.
- The database: pages, free pages and the page size as SQLite counts them
  (`PRAGMA page_count`, `freelist_count`), as `maintenance.vacuum()` reads
  them, the size of the write-ahead log, and whether free pages are given
  back. Never `count(*)`: the tables of metrics reach millions of rows.

The page puts the worst first for each group (`src/lib/health.ts`): falling
behind; skipped, or a cycle longer than the interval; slow, a usual cycle
over half of it, which is what sizing aims at; over its memory; cycles not
successful.

## Export and import

`config.export` returns the configuration as one JSON document: credentials,
projects, inventories, trusted host keys, job templates, schedules, checks,
notification channels and rules. Things refer to
each other by name, since identifiers differ between two installations. It
holds no secret: a credential is its name, its kind, its user name and its
vault identifier, and its secrets are entered again where it is imported.
Neither does it hold the files of a project that has no source elsewhere
(written in the interface, uploaded): such a project arrives empty. The
files of an inventory written in the interface (`files`: path, and content,
or data in base64 for one that is not text, with `content` its main file for
an import made before inventories were trees) and the variables of a
template are in it as they are.

`config.import` creates what the document lists and is not there yet, by
name; what exists is left as it is, so importing twice changes nothing. Each
thing goes through the command that creates it from the interface: the same
checks apply (a local project outside the allowed roots is refused, a host key
that differs from the trusted one is not replaced) and the same audit record
is left, next to the one of the import itself. One thing that cannot be
created does not stop the others, and the answer says what happened to each.
The document is not copied into the audit record. Both commands are for
administrators.

## Agents

`fleet-mcp` is the backend as a Model Context Protocol server: JSON-RPC on
its standard input and output, one message per line, tools only. A user
starts it, on the controller or as the command of an ssh connection, and it
runs as that user with no privilege of its own. Every tool is one call to
`fleetctl` through sudo, exactly what the interface does, so there is still
one way into the backend, and roles and the audit trail are the same. It
uses the standard library only and listens on nothing.

```
agent ── JSON-RPC on stdio ──▶ fleet-mcp          (the user, e.g. over ssh)
                                 │ sudo -u cockpit-fleet fleetctl COMMAND
                                 │   < {…arguments, "via": "mcp:<client>"}
                                 ▼
                               fleetctl            role = min(user's, ceiling)
```

**The ceiling.** `[mcp]` in `config.toml` has `enabled`, off by default, and
`max_role`, `operator` by default. `fleet-mcp` adds `via` to the JSON of every
call. When `fleetctl` finds it, it refuses everything while agents are not
enabled, and lowers the caller's role to the ceiling for that call, never
raises it; the audit record and the journal (`FLEET_VIA`) say where the call
came from. The reason for a ceiling below what the person may do: the output
of a run is written by the nodes, and an agent that reads it and may also
write files and launch anything can be steered by what a node prints.

Only a `via` that is `mcp` or starts with `mcp:` is an agent's; any other
value, such as the `cli` of the command `fleet` (see "The terminal"), is
recorded and changes nothing else. Saying `via` can only restrict, and that
is all it is: a declaration. A user
with a shell can call `fleetctl` without it, with their whole role, which is
no more than they could do by hand. It binds an agent only when the agent
cannot reach anything but `fleet-mcp`: a key of its own in `authorized_keys`
with a forced command,

```
restrict,command="/usr/libexec/cockpit-fleet/fleet-mcp" ssh-ed25519 AAAA… agent
```

which gets `fleet-mcp` whatever it asks for (`mcp.sh` tries), or, stricter
and with no setting at all, a Unix account for the agent in the role group it
should have.

**Tools.** `mcp.tools` gives `fleet-mcp` what to offer: one tool per command
the caller's role allows under the ceiling (`run.start` becomes `run_start`),
with the first lines of its documentation as description and its declared
parameters as `inputSchema`. Tools above the ceiling are not listed, and
`fleetctl` refuses them anyway. A few commands are not tools: the upload of an
archive, the list of all modules and the raw events, which are too large to be
useful to an agent, and those only the backend calls. There is no tool for
secrets, because `fleet-secret` is not a command of `fleetctl`: an agent may
create a credential, a person enters its value.

**Templates as tools.** Each job template is also a tool of its own,
`launch_<name>_<id>`, for whoever may launch it: its description says what it
runs and where, and its parameters are the questions of its survey, typed and
bounded as the survey is, plus the limit and the dry run. The tool fixes the
template: what it fixes and `via` are set by `fleet-mcp` after the agent's
arguments, so a call cannot replace them. A template that cannot start
without a secret is not offered; an optional secret question is left out.
This is what stands in for the MCP servers a project may carry, which are
never started: they would run Ansible outside the sandbox.

**Reading a run.** `run.result` answers with the status of a run, the outcome
per host and, task by task, how many hosts went each way and what was said
where one failed, cut to 500 characters; with `wait` it answers when the run
ends or after at most 50 seconds, so an agent follows a run with one call at
a time and no polling loop of its own. It is built from the events, not from
the output. The raw output stays a separate tool, `run_log`, in chunks, whose
description says to read it only when asked.

Checked on Debian 13 (`mcp.sh`), with a client written for the test, and
from Claude Code 2.1 over ssh with a forced-command key: five turns to launch
a template, wait for it and report host and tasks.

## The terminal

`fleet`, in `/usr/bin`, does from a terminal what the page does, for whoever
is in a role group: Cockpit's own terminal, an ssh session, a script. It is
the third client of `fleetctl`, after the page and `fleet-mcp`, and it is
built the same way: it runs as the user with no privilege of its own, and
every command is one call to `fleetctl` through sudo, with `"via": "cli"`.
So roles, validation and the audit trail are the interface's, a viewer still
does not read the output of a run, and the audit trail and the journal say
the call came from a terminal. `cli` is not an agent's `via`: the ceiling of
`[mcp]` does not apply, as it does not to a person at the page.

```
fleet run list --status failed      ──▶ sudo -u cockpit-fleet fleetctl run.list
fleet launch "Say hello" -a k=v     ──▶ template.list, run.start, run.log…, run.result
fleet secret vault vault_password   ──▶ credential.list, then sudo fleet-secret set NAME < value
```

**Commands from the registry.** Every command of `fleetctl` is `fleet`
followed by its area and verb, with its declared parameters as options typed
as they are declared (`--limit 3`, a flag for a boolean and `--no-` before it,
a repeated option for a list, JSON for an object, `@path` or `@-` for a text
read from a file or stdin). Its help is its docstring and its parameters,
the same that describe it to an agent: a command added to the backend is in
`fleet` with no work, phase 4 included. The registry is read from the
installed package, so `fleet help AREA VERB` needs no call; `fleet help`
asks `whoami` which commands the role has. Where a command takes the id of
something that has a name (a template, project, inventory, credential,
check, host, schedule, channel, rule), the name may be given: `fleet` asks
the list for it and passes the id, and says so when two have that name.
The completion for bash offers commands, options and their choices from the
registry, and asks the backend, as the user, for the names of what an id
names, the last runs and the secrets of a credential: nothing to whoever has
no role. `project upload --archive PATH` reads the archive from a file, or
from stdin with `-`, and sends it as the base64 the backend takes.

**From the page to the terminal.** The launch of a template, the page of a
run and the upload of an archive show, under "At a terminal", the `fleet`
command that does the same (`fleet launch` with the answers typed so far,
never the secret ones, which `fleet` asks for; `fleet log -f` or `fleet
result`; `fleet project upload --archive`), quoted for a POSIX shell, with a
button to copy it and one that opens Cockpit's terminal in a new tab:
Cockpit's terminal cannot be handed a command, so it is pasted. Four
commands meant for other programs (`run.reconcile`, `mcp.tools`,
`chat.start`, `scheduler.tick`) are not listed, and still answer.

**Shortcuts** for what is done most often: `launch` takes a template by name
or id and answers to its survey as `-a NAME=VALUE`, follows the output and
ends with the outcome per host and per task (`run.result`); `log` prints the
output, `-f` follows it; `result` reads the outcome; `secret` sets a secret
of a credential. The exit status lets a script tell a run that failed (1)
from a command line that is wrong (2) and a refusal or an unreachable
backend (3).

**Secrets stay off the command line.** A secret question of a survey is
never taken from `-a`: it is asked for at the terminal, with no echo, and
the value goes to `fleet-secret answer` on its stdin, whose ticket is what
`run.start` gets, as from the page. Without a terminal a required secret
question is an error. `fleet secret` reads the value twice at a terminal, or
once from stdin; one line read from stdin loses its newline, a key keeps it.
The value is in no argv, environment or record (`cli.sh` looks).

**Output** is for a person by default: lists of records as tables, the
known columns first, cut to the width of the terminal; anything else as
`key: value`; times in local time; the output of a run with its colours at a
terminal and without them elsewhere. `--json` gives the answer as the
backend gives it, for a program. English only, like the backend's messages.

**Why not more.** A user at the terminal does not get the projects' files
(`/var/lib/cockpit-fleet` is the service account's), the credentials, or an
`ansible-playbook` of their own: the secrets are decrypted only by PID 1 for
a run, and a run outside the unit would have neither the sandbox nor the
audit trail.

**Projects at a terminal** (decided with the user on 16 October). Two ways,
for administrators only, since files are theirs in the interface too:

- A directory of local projects under `[projects] local_roots`, by a
  convention that `selfcheck` (`project_roots`) checks on the directory
  itself: group `cockpit-fleet-admin`, mode 2770, and an ACL that lets the
  service account read (`u:cockpit-fleet:rX`, and the same by default for
  what is created there). Administrators write with their own editor and
  git, the service reads, no other account sees the files. Writable by
  another group is a warning (what is there is what operators launch),
  readable by everyone a warning, writable by everyone a failure. Not under
  `/home`, `/root`, `/run/user`, `/tmp` or `/var/tmp`
  (`jobs.HIDDEN_FROM_UNITS`): the scheduler and `cockpit-fleet-finish@` have
  `ProtectHome` and `PrivateTmp`, so such a project worked when launched
  through sudo and failed whenever a timer started it, with "is not a
  directory". It is refused when created and at every use, a link leading
  there too, and `selfcheck` fails on such a root. A project that has to
  stay in a home is bind-mounted under a root (fleet(1)). The ACL
  mask follows the group bits of a new file: who writes there keeps a umask
  that leaves the group read (022 or 002). The manual, fleet(1), has the
  three commands.
- A project written in the interface is cloned with `fleet project clone`:
  `file.bundle` gives its repository as a git bundle (recorded in the audit
  trail, as a copy of every file), which `fleet` clones as the user, keeping
  the project's id in the clone's configuration and the revision it knows
  as `refs/remotes/fleet/main`. `fleet project push` sends a bundle of the
  commits of `main` since that revision to `file.push`, which takes them only
  if the project is still at that revision, as a line of commits (no merge),
  and if every file of the result is one the editor could have written
  (regular files of at most 1 MB, the names `check_file_path` takes: no
  link, no submodule, nothing called `.git`); then a fast forward, and what
  a write in the interface does: a syntax check of each playbook changed,
  the collections when the requirements changed. The authors are those of
  the commits; who pushed is in the audit trail. `fleet project pull`
  fetches the same way and fast-forwards; a clone whose commits diverge is
  told to rebase on `fleet/main`.

Checked on Debian 13 (`cli.sh`): a project, an inventory and a template set
up by an administrator, a file written from a file and from stdin, an
operator's launch followed to its end, exit statuses, refusals by role, a
user in no group told why, a secret from stdin; and by hand, at a terminal,
a secret question asked with no echo, which reached the run masked and is in
no record.

## The chat

A page of the interface, "Assistant" in its tabs, where the user talks to an
agent that works through `fleet-mcp`. The page is a client of the Agent Client Protocol (ACP): JSON-RPC
on the standard input and output of the agent, which is a process of the
user, on a Cockpit channel.

```
ChatPage ── cockpit.spawn ──▶ fleet-chat NAME        (the user, no privilege)
   │ ACP on stdio               │ sudo -u cockpit-fleet fleetctl chat.start
   │                            ▼
   └──────────────────────▶ the agent ──▶ fleet-mcp ──▶ fleetctl (via, ceiling)
```

**The agent is not ours.** The package does not carry it and downloads
nothing by itself: Node.js 22 is not in Debian 13, and the adapter brings a
`claude` binary that is not ours to redistribute. What the package carries is
how to install it: the role `lineadicomando.fleet.chat_agent` of the bundled
collection, and a playbook an administrator of the machine runs once, as
root, in a terminal (`docs/chat.md`). Ansible is already there, so this adds
no program of ours that runs as root and no rule to sudoers. A run of Fleet
could not do it on its own controller: the sandbox has no sudo and cannot
write under `/opt`; with the controller as a host reached over ssh, the same
role works from a project.

The role fetches Node.js from `nodejs.org` and checks it against the
checksum published with the release, then installs the adapter with `npm ci
--ignore-scripts` from a lockfile shipped in the role, which names each of
its 115 packages with its hash. The lockfile does not say which C library a
package is for, so the build for musl comes too and is removed. Running the
role again changes nothing; `chat_agent_state=absent` removes what it wrote.

Naming an agent in `[chat.agents.NAME]` of `config.toml` is what turns the
chat on, apart from `[mcp] enabled`, which the chat also needs: enabling
`fleet-mcp` sends data to nobody, a chat sends what is said in it to the
provider of the model. The role writes that section only when asked
(`chat_agent_configure`), between two marker lines, through the backend's own
parser, and never over an agent it did not write. `whoami` says whether an
agent is named, and the tab is shown only then. `chat.agents` says whether
each agent is there to be started, and for one under
`/opt/cockpit-fleet-agent` that is not, the command that installs it: the
page shows it, to be copied, with a button that opens Cockpit's terminal,
which is part of `cockpit-system` and runs as the user of the page. It opens
in a new tab, at an address built from the shell's own (`src/lib/shell.ts`):
`cockpit.jump` would replace the page, and with it end the agent the page
has started, which is what the user is logging in for. Outside the shell
the button falls back to `cockpit.jump`.

**The page names the agent, it does not start it.** It spawns `fleet-chat
NAME`, a packaged program that runs as the user. `fleet-chat` asks
`chat.start` for the command line (any role; the start is in the audit
trail), runs it with its own input and output, and waits for it. It writes
one line before the agent says anything, in the same form: `_fleet/ready`,
with the home of the user and `fleet-mcp` as the server to give the agent, or
`_fleet/error` with the refusal of the backend. It also sets
`ENABLE_CLAUDEAI_MCP_SERVERS=false`, so that a login to claude.ai does not
bring that account's connectors into the session.

**The limit on sessions.** `[chat] max_sessions` (2 by default) is for the
whole controller. Nothing is recorded: `chat.start` counts the `fleet-chat`
processes in `/proc`, the one asking included, which is why `fleet-chat`
stays around its agent instead of becoming it. It bounds memory and is not a
control: a user with a shell can start the agent by hand.

**What a session is given.** `session/new` and `session/load` carry
`fleet-mcp` as the only MCP server, `_meta.disableBuiltInTools` (the agent
has no shell and no files), `settingSources: []` and `strictMcpConfig` among
the options of the adapter (none of the user's own settings for the agent,
which could add servers or rules that allow a call without asking), and a
few lines added to the agent's instructions. The client offers neither files
nor a terminal. The session's directory is the user's home.

**At a terminal.** `fleet assistant` runs `fleet-chat --terminal NAME`,
which asks `chat.start` with `terminal` for the agent's program for a
terminal (`terminal` in `[chat.agents.NAME]`), writes a private MCP
configuration naming `fleet-mcp` alone, fills `{mcp_config}` and
`{instructions}` in that command line, and runs it on the user's terminal,
ignoring ^C, which is the agent's, in `~/.local/state/cockpit-fleet/assistant`
(under `XDG_STATE_HOME` when set; mode 0700), the same directory each time:
Claude asks whether to trust the directory it starts in and keeps no answer
for a home directory, so from the home it asked at every start. Being a `fleet-chat`, it is counted as a
session; the configuration is deleted when the agent ends. For Claude the
role names `--strict-mcp-config`, `--tools ""` and `--setting-sources ""`,
what `_meta` does for a session of the page, and `--permission-mode manual`:
without it the CLI may start in its "auto" mode, which calls tools without
asking (seen on 20 October with an account on a Max plan). Tried with a
stand-in program (`chat.sh`), and with the CLI of Claude on Debian 13: a
launch asked for, refused, and audited as `mcp:claude-code`.

**Permissions.** The agent asks before each call to a tool
(`session/request_permission`). The page answers by itself for a tool of
`fleet-mcp` that `mcp.tools` marks as read-only; for anything else it shows
the description of the tool and its arguments and waits: once, always in
this conversation, refuse. "Always" is kept by the page and answered as
"once": the agent's own option of that kind writes a rule in the user's
settings. A call that names something `mcp.tools` does not list is always
asked about, with no "always". Stopping an answer cancels what was waiting.
None of this is a control: the backend decides again, with the role of the
user under the ceiling of `[mcp]`.

**Conversations.** They are the agent's: the page lists them
(`session/list`) and has the agent replay one (`session/load`) to go on
after a reload. The session lives outside the React tree, so it goes on
across the tabs of the plugin; a reload of the page closes the channel, and
`fleet-chat`, the agent and `fleet-mcp` end with it. Answers are shown as
plain text. The page takes the height left in the window and the
conversation scrolls inside it, so that the message box stays in sight
(measured, not a fixed share of the window: what is above it changes).

**No login to the model.** The adapter says so (`_auth/status_update`, and
error -32000 to a prompt): the page shows the command `config.toml` gives
for the login, to be run once by the user in a terminal, with the same button
to Cockpit's own.

Measured on the test controller with the adapter for Claude,
`@agentclientprotocol/claude-agent-acp` 0.89.1:

- The adapter wants Node.js 22 or later and Debian 13 has 20: the official
  Node 22 tarball is 203 MB, adapter and SDK 308 MB (about 540 with the build
  for musl, before it is removed).
- One idle session takes about 360 MB: adapter 93, `claude` 256, `fleet-mcp`
  12.
- With `_meta.disableBuiltInTools` the agent, asked for a shell command and a
  file, said it had neither (13 October, logged in, before the page existed).

Checked on Debian 13: `chat.sh` drives `fleet-chat` with an agent written
for the test (`fake-agent.py`, the protocol with no model behind it), and
the page was tried from the browser with it as an operator and as a viewer:
a read-only tool with no question, a template launched after a
confirmation, a tool outside the role refused by the backend, no process
left behind after a reload.

Then with the adapter and its model, as an operator (13 October): asked
what it could do, the agent called `whoami` and `template_list` with no
question and listed its tools and its role; asked to launch a template, it
waited for the confirmation, which showed the description of the tool and
the answer to the survey, then called `run_result` and told the outcome by
host and by task; the audit trail has the launch as the user's, via
`mcp:claude-code`. Asked for a shell command, a file and an ad-hoc command,
it said it had no tool for the first two and that the third is not for an
operator. After a reload the conversation was in the list, was replayed, and
the agent remembered the run. The adapter names the tool of a permission
request in `_meta.claudeCode.toolName`, which is what the page reads;
`settingSources: []` and `strictMcpConfig` do not get in the way of the
login.

The role, from the snapshot of the test controller, where there is neither
Node.js nor an agent (13 October): the playbook installs 511 MB, a second run
changes nothing, `chat_agent_configure` writes the section and a second time
does not, an agent of the same name written by hand is refused, a user who is
not root is told so, `absent` leaves `/opt` empty and `config.toml` as
packaged; `ansible-lint` has nothing to say. With the agent so installed the
page started it and reached the missing login; with the agent named and not
installed it showed the command, copied it and opened the terminal.

On Fedora 44 and AlmaLinux 10 (24 October, SELinux enforcing, ansible-core
2.20 and 2.16), as `docs/chat.md` says: the role installs `xz` and the
agent (about 505 MB), a second run changes nothing, `absent` leaves `/opt`
empty; `fleet-chat`, as an operator with `[mcp] enabled`, started it with
`fleet-mcp` named and reached the missing login; no denial. Its commands
in `bin/` are `bin_t`, by the policy's own rules for `/opt`.

With the model, through an agent installed by the role (14 October, as an
operator with no earlier login): the page reached the missing login, the
user logged in at Cockpit's terminal, and the assistant then launched a
template with an answer to its survey, after a confirmation that showed
the answer, and reported its outcome per host and per task from
`run.result`; the launch is in the audit trail with `via` `mcp:claude-code`.
The button to the terminal then replaced the page and ended the agent,
which is why it opens a new tab now.

## Events

The callback `lineadicomando.fleet.events` appends one JSON object per line:
`playbook_start`, `play_start`, `task_start`, `host_ok`, `host_failed`,
`host_skipped`, `host_unreachable`, `item_ok`, `item_failed`, `item_skipped`
(one per element of a loop), `stats`. Results are trimmed (long strings cut,
`invocation`, `diff` and, by default, `ansible_facts` dropped).

What a task changed, or would change in a dry run, is the field `diff` of the
host's event, or of the element's for a loop: a unified diff as text, the same
`ansible-playbook --diff` prints. The callback renders it, not the browser,
because it takes the whole of `before` and `after`, which in the event would be
cut like any other string. One result's diff is cut at 16 kB (`diff_cut`); the
run's output has all of it. Ansible gives no diff for a task with `no_log` or
`diff: false`, so neither does the event. The "Differences" tab of a run groups
them by host and draws a host's only when it is opened. Like the output, they
are for operators and administrators: a diff shows the content of files.

Checked with Debian's ansible-core 2.19.11: six tasks in a dry run on two hosts,
one of them rewriting a file of 4000 lines, give 41 kB of events.

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
collection is not part of `ansible-core`: it comes from the `ansible` package,
from a copy inside the project, or from the project's requirements file (see
"Collections from Galaxy").

The test nodes accept public keys only, so the run uses a private key stored
as a secret and passed with `--private-key`. Measured from the sandboxed unit
against a Windows 11 VM (4 vCPU, 8 GB), `test/integration/windows.sh`:

| Task | Time |
|---|---|
| fact gathering | 10 to 11 s |
| `win_command` | 4 s |
| `win_shell` (one CIM query) | 5 s |
| whole run | 20 s |

The same run launched through `fleetctl` and from the interface takes 21 to 24
seconds.

Every task on Windows costs 4 to 5 seconds whatever it does, against a fraction
of a second on Linux. A collection cycle must therefore gather everything a
Windows node has to report in one task, with one script, and not run one task
per check.

`become` on Windows is `runas`, with `ansible_become_method: runas` and
`ansible_become_user: SYSTEM` in the inventory. It fails before any module
runs when `ansible_remote_tmp` is set to a directory such as
`C:\Windows\Temp\ansible`, and works with the default (`%TEMP%`): tried with
`win_whoami` and with the checks of the catalog on Windows 11, 16 October.
Windows Update needs no `become` over SSH with a key.

## Packaging

The `.deb` is built in a Debian 13 container (`make deb`, output in `deb/`),
which also runs lintian; on a Debian 13 system `scripts/build-deb.sh --native`
does the same without a container. The frontend bundle is built beforehand with npm,
because the package build has no network. The package creates the account and
the role groups (`sysusers.d`), the state and secret directories
(`tmpfiles.d`), and installs the units, the helpers, the sudoers line, the
bundled collection, and `fleet` with its completion for bash and its manual.
Of the units only `cockpit-fleet-scheduler.timer` and `cockpit-fleet-notify.path`
are enabled and started.

A package carries its version in what it installs, so that a controller says
which build it runs. At a release tag that is the release, `X.Y.Z`. Between
two releases `scripts/version.sh` derives it from `git describe`:
`0.1.0+3.gabc1234` for dpkg and `0.1.0^3.gabc1234` for rpm, the commits
since the tag and the commit, `.dirty` when the tree has changes not
committed. Both forms sort above `0.1.0` and below `0.1.1`, so an upgrade to
the next release, or from one build to a later one, is an upgrade and not a
reinstall. The build scripts write it into their copy of the tree, with an
entry of `debian/changelog` or of the spec's `%changelog` and into
`__init__.py`, never into the repository; a container that builds has no
git, so the host passes `FLEET_DESCRIBE` into it. A release itself is
`scripts/release.mjs` (`make release`): the version computed from the kinds
of change in `CHANGELOG.md`, set in the six files that carry it, a commit
and a tag.

The `.rpm` for Fedora 44 and AlmaLinux 10 comes from `packaging/cockpit-fleet.spec`,
built in a container of each (`make rpm`, output in `rpm/`) and checked with
rpmlint, whose exceptions are in `packaging/cockpit-fleet.rpmlintrc`, each with
its reason; `scripts/build-rpm.sh --native` builds on the system it runs on. It
installs the same files through the same `make install-files`. rpm 4.19 creates
the account and the groups from the `sysusers.d` file before it installs the
files that belong to them. The package also owns the directories that
`tmpfiles.d` creates, with the same modes. A preset file
(`system/cockpit-fleet.preset`) enables `cockpit-fleet-scheduler.timer` and
`cockpit-fleet-notify.path`, and `%post` starts them on the first install, as
the `.deb` does.

**Upgrade, removal, purge.** An upgrade keeps the database, the projects,
the secrets and a configuration changed by the administrator (a conffile on
Debian, `%config(noreplace)` with an `.rpmnew` beside it on rpm), restarts
the timer and the path unit, and leaves a run going alone: it keeps the unit
it was started with and is recorded when it ends. The database is brought to
the new schema by the first backend call that opens it, which the timer
makes within a minute. Removal stops what the package's programs would no
longer be there for: the runs still going, the scheduler and the delivery
of notifications midway, and the slice of the runs; a run stopped so is
failed, and its unit is reset so as not to leave the system degraded (the
`prerm` of the `.deb`, with a lintian override since only a pattern names
those units, and `%preun`). Data, secrets and the configuration stay; dnf
removes the directories it owns that are empty, and saves a changed
`config.toml` as `config.toml.rpmsave`. On AlmaLinux 10 the removed units
stayed loaded until the next reload, on a machine as in a container, so
`%postun` reloads systemd. `purge` of the `.deb` also deletes
`/var/lib/cockpit-fleet`, `/etc/cockpit-fleet` (the secrets with it) and
`/run/cockpit-fleet`; rpm has no purge, and `docs/install.md` says what to
delete. The account and its groups stay on both: files elsewhere, a
directory of local projects, may belong to them.

`scripts/ct-lifecycle.sh` (`make ct-lifecycle DISTRO=… OLD=…`) checks all of
it, `test/integration/lifecycle.sh` in a container where the package was
never installed: a package built from an older commit (or the latest tag),
its version set below the current one, installed and used (a project, an
inventory, a secret, templates, a schedule, a run); the current package
installed over it with a run going; then removal with a run going,
installing again, and purge. From the commit that closed phase 8 (schema 15
to 17) it passed on the three on 25 October, and on the AlmaLinux VM, with
no SELinux denial. In the Debian container the script removes the
`policy-rc.d` of Debian's images, which forbids packages to start services.

### Other distributions

The same code runs on the three controllers; what differs:

| | Debian 13 | Fedora 44 | AlmaLinux 10.2 |
|---|---|---|---|
| ansible-core | 2.19 | 2.20 | 2.16 (AppStream); no `ansible`, no `ansible-lint` |
| Python | 3.13 | 3.14 | 3.12, so the code keeps to 3.12 (ruff's `target-version`) |
| systemd, Cockpit | 257, 337 | 259, 368 | 257, 356 |
| SELinux | none | enforcing | enforcing |

Checked on 15 October on Fedora 44 and AlmaLinux 10.2, both enforcing, with
every integration script but `windows.sh` and `measure-events.sh`: all pass,
and the audit log has no denial. The units, the helpers and sudo need no
policy of their own: what systemd starts from `/usr/libexec` runs unconfined,
and `LoadCredentialEncrypted`, the tmpfs over the state and `PrivatePIDs`
behave as on Debian. ansible-core 2.16 passed everything that 2.19 passes,
`multihost.sh` included, so it is the minimum of both packages and of
`selfcheck` (`_MIN_ANSIBLE` in `system.py`). 2.16 is no longer supported
upstream: the integration scripts run on AlmaLinux at every phase. They did
again on 16 October, `checks.sh` included, on the three controllers: the
modules of the checks run on the Debian nodes from ansible-core 2.16 as from
2.20. Checking Windows should need nothing that AlmaLinux lacks, since the
module uses ansible-core only, but `windows.sh` was run from Debian only.

Found on the way:

- `module.list` decides when to list the modules again from the package
  database: `/var/lib/dpkg/status` or the directory of rpm's SQLite database,
  whose writes go to a write-ahead log, and the collections of the `ansible`
  package, in `dist-packages` or in `site-packages` of the Python Ansible runs on.
- AlmaLinux's `nsswitch.conf` asks DNS before `myhostname`. If the controller's
  name is not in DNS, `socket.getfqdn()` takes seconds (14 on the test VM).
  The link in a notification asked for it once per channel, so a webhook came
  20 seconds late. The name is now looked up once per process, that is once per delivery. Setting
  `[notifications] url` avoids the lookup.
- The test nodes of `nodes.sh` need the key file relabelled (`:z`) where
  SELinux is enforcing, and `CAP_AUDIT_WRITE`, which the podman of EL does
  not give: Debian's sshd closes a session with a terminal when it cannot
  write its audit record, and ansible-core 2.16 asks for a terminal (`-tt`).
  Real nodes are not concerned.

## The interface: language and accessibility

The page is in English or Italian, as the Cockpit session is (the
`CockpitLang` cookie, then the shell's language, then the browser's). Every
string goes through `t()`; the backend's messages, and what the nodes
write in the summary of a check, stay in English, like the terminal.

PatternFly falls back on English text of its own: labels only a screen
reader says (an alert's kind, a close button, the value of a spinner, the
menu of a row, the toggle of an expandable row), tooltips, the buttons of an
upload. `src/components/pf.tsx` wraps those components with translated text,
and pages import them from there, never from PatternFly: `Alert`,
`AlertActionCloseButton`, `ClipboardCopy`, `FileUpload`, `Modal`,
`NumberInput`, `SearchInput`, `Spinner`, `Tabs`, `ActionsColumn`, and
`ExpandCell` for the cell that opens a row (`Td`'s own `expand` takes no
label). The wrappers also mend what an audit found:

- a modal's close button is drawn by the wrapper, since PatternFly gives it
  no label of ours; Escape still closes it;
- tabs whose content is drawn apart name it: `Tabs` takes a `panelId`, and
  the content goes in a `TabPanel` of the same id, which the tabs'
  `aria-controls` point to;
- an alert's title is a paragraph, not a heading: an alert sits at any depth
  of the page, and a heading of a fixed level broke their order;
- a `NumberInput` given an `id` puts it on the field, where the label of its
  `FormGroup` reaches it, and no name of its own over that label;
- a `FormGroup` around something that is not a field (a code editor, the
  links of a workflow step, an upload) is `role="group"`, so that its label
  names the group instead of pointing at nothing.

**Long lists.** Checks and job templates are filtered, grouped and sorted
in the browser, from what their `list` command returns: neither is paged,
and at the sizes measured (hundreds of each) there is no reason to ask the
backend again for each change of a filter. The pieces they share are in
`src/lib/listview.ts` (groups, the stored view read back defensively) and
`src/components/ListView.tsx` (the filters in use as labels that remove
them, the choice of grouping with a button that opens or closes every group,
the first row of a group, nothing matching). What a user chose, the order,
and the groups opened or closed by hand, are kept in the browser's
`localStorage` under `cockpit-fleet.NAME.USER`, so that they outlive a
change of tab and a reload; a link carries none of them, and a stored
value that is not understood (an older version, an edit by hand) falls
back to the default. PatternFly's toolbar filters are not used: their
labels ("Show Filters", "Close …") are English that cannot be translated.

Job templates are grouped by project (the default), inventory, outcome of
the last run or not at all, every group open at first; filtered by text
(name, playbook, project, inventory, tags), project, inventory, a feature
(an approval, questions, scheduled or not, from `schedule.list`) and the
outcome of the last run (failed or error; under way, waiting for approval
included; canceled, rejected or expired; successful; never launched).
`template.list` gives each template its `last_run`, any run with its
`template_id` (a step of a workflow too), in one query over the index
`runs_template` (schema 19); it does not reconcile runs as `run.list` does,
so a run whose unit died unseen shows as running until a page of runs is
opened.

`<html lang>` of the frame is the language of the page, not the "en" of
`index.html`, so that a screen reader reads Italian as Italian. The
placeholder of the code editor uses the theme's subtle text colour: its
own grey fell short of 4.5:1.

Checked on 25 October on the Debian VM, in Italian, with data from
`checks.sh` and a project, a template and a schedule: Lighthouse scores
accessibility 100 on every tab, on the page of a host with a chart open, on
a run, on the modal of a new template, and in the dark theme; a scan of
the DOM on every tab and on each modal for a new item finds no English text
of PatternFly, no label pointing at nothing and no field without a name.
What Lighthouse does not judge: the text of the charts (an SVG), and the
order of focus beyond what PatternFly gives.

## Tests

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

## Acceptance of the phases

What each closed phase had to show, and how it was shown.

- **3, complete runner** (13 October): `ansible-win_edulab` ran unmodified
  from the interface on a Windows 11 node: uploaded as an archive (its vault
  file is not in git), the two collections of its requirements installed
  from GitHub, the inventory read with the vault credential, host keys
  trusted, `playbooks/win_wm.yaml` launched by an operator with a survey (14
  tasks, 14 s), an ad-hoc `win_command`, and `shutdown.yaml`, which delegates
  a task to the controller.
- **3b, agents** (13 October): Claude Code over ssh, with a key forced on
  `fleet-mcp`, as an operator, launched `win_wm.yaml` through its tool, waited
  with `run_result` and reported the outcome; asked for an ad-hoc command it
  found no tool, and the same call sent with `via`, as an operator and as an
  administrator under the ceiling, was refused and audited. The chat was
  tried from the page with `fake-agent.py` and with Claude, as an operator.
- **4, schedules** (14 October): ten schedules on one minute with
  `max_concurrent = 2` never ran more than two, highest priority first
  (`schedules.sh`); after a shutdown with one run running and two queued,
  the first tick recorded the cut run as failed, started the queue, caught
  up one schedule once and recorded the other as missed.
- **5, notifications** (14 and 15 October): a failed run reached stand-ins
  of the four channels in one delivery, a refused fifth was retried by the
  tick (`notify.sh`); then real Telegram, mail over TLS and Matrix, the page
  in the browser in Italian for the three roles, no token in the database
  or the journal.
- **6, checks** (16 October): see "Checks": the same disk metrics from
  Debian and Windows, a full disk told about within two intervals.
- **7, inventory of hosts** (16 October): see "The inventory of hosts":
  `cmdb.sh` on the machines of the three distributions, drift from a file
  changed by hand at the next cycle, the CMDB of Windows by `windows.sh`.
- **8, time series and scale** (23 October): 500 hosts with ten checks
  each every 10 minutes ran 24 hours on a Debian machine of 2 vCPUs and
  4 GB with no backlog and every call of the interface under 0.4 s at
  p95 (`load.sh`; see "Checks", "At scale"), after runs were given a
  slice of their own; then every script on the machines of the three
  distributions, and the health section and the charts in a browser on
  Debian with the 500 hosts.
- **9, workflows and approvals** (24 October): a workflow of three steps
  with an approval at the second stopped there, was approved by an account
  that has only the approver's right, and completed, what the first step
  passed on with `set_stats` reaching the next ones unrendered; a failed
  step handled by a step for it, a resume that ran only the steps not yet
  done, a cancel (`workflows.sh`), on containers and on the machines of the
  three distributions with `sandbox`, `roles` and `runs`, SELinux enforcing
  on Fedora 44 and AlmaLinux 10 with no denial; in a browser on Debian,
  approving as an administrator and as an approver with no role, the steps
  of a workflow run, the editor.
