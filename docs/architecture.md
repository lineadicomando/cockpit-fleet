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
| `fleet-secret set\|delete NAME`, `list` | administrators | encrypts stdin with `systemd-creds` into `/etc/cockpit-fleet/secrets/NAME.cred` |
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
The input is at most 1 MB, for every command but two, which declare their
own limit: `project.upload` and `config.import`.

- **Who is calling.** fleetctl refuses to run as anything but the service
  account, so it is always reached through sudo, which sets `SUDO_UID` and does
  not let the caller override it.
- **Roles.** `cockpit-fleet-viewer`, `-operator`, `-admin`; the highest group
  wins. Root is an administrator. Every command declares the least role that
  may call it, and the check happens in one place, before the handler runs.
- **Audit.** Commands that change something, and every refusal, are written to
  the `audit` table and to the journal (`journalctl -t cockpit-fleet`, with
  `FLEET_USER`, `FLEET_COMMAND`, `FLEET_OUTCOME` fields, and `FLEET_VIA` when
  the call was made for an agent). Parameters marked as
  secret are replaced before being stored. `fleet-secret` runs as root and
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

A run unit executes one job, of one of seven kinds, described by `spec.json`
in its directory: `playbook` (ansible-playbook), `adhoc` (one module, as a
playbook written for the job), `inventory` (`ansible-inventory --list`, to
learn hosts and groups), `sync` (fetch a git project), `syntax` (check a
playbook without running it), `unpack` (extract an uploaded archive) and
`galaxy` (install the collections a project asks for). All of them need the sandbox, because all of them work on what a project
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
| `inventory/` | the inventory, when it is written in the interface rather than a project file |
| `collections/` | a copy of the collections installed for the project, when it asks for some |

The run then sees a self-contained directory and nothing else. `project/`,
`creds/`, `collections/` and an uploaded `archive` are removed when the job
ends; `stdout.log`, `events.jsonl` and `result.json` stay.

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
has no source elsewhere: its files are written in the "Files" page, one at a
time, by administrators. They are the work tree of a git repository, and every
change (write, rename, delete) is one commit whose author is the caller and
whose committer is the service account. A run records the commit it ran.

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
overwritten. The content is not copied into the audit record. Files are text,
UTF-8, up to 512 kB. The same page shows the files of the other kinds of
project, read-only.

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
else; one written in the interface may be a plugin's file too, but its name is
`hosts.yml`, which the plugins that want a name of their own
(`*.aws_ec2.yml`) refuse. A plugin gets no credential beyond the vault of the
inventory.

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
explicitly, because that is also what an impersonated host looks like.

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
host limit and ask for a dry run. The limit is combined with the template's
own (`template:&caller`), so it can narrow the target but not widen it.

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
- if a run of the same schedule is still queued or running, `overlap` decides:
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

## Export and import

`config.export` returns the configuration as one JSON document: credentials,
projects, inventories, trusted host keys, job templates and schedules. Things refer to
each other by name, since identifiers differ between two installations. It
holds no secret: a credential is its name, its kind, its user name and its
vault identifier, and its secrets are entered again where it is imported.
Neither does it hold the files of a project that has no source elsewhere
(written in the interface, uploaded): such a project arrives empty. The text
of an inventory written in the interface and the variables of a template are
in it as they are.

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
installed package, so `fleet help AREA VERB` and the completion for bash need
no call; `fleet help` asks `whoami` which commands the role has. Three
commands meant for other programs (`run.reconcile`, `mcp.tools`,
`chat.start`) are not listed, and still answer.

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
audit trail. Who wants to work on a project's files at a terminal uses a
local-directory project under `[projects] local_roots`.

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

## Packaging

The `.deb` is built in a Debian 13 container (`make deb`, output in `deb/`),
which also runs lintian; on a Debian 13 system `scripts/build-deb.sh --native`
does the same without a container. The frontend bundle is built beforehand with npm,
because the package build has no network. The package creates the account and
the role groups (`sysusers.d`), the state and secret directories
(`tmpfiles.d`), and installs the units, the helpers, the sudoers line, the
bundled collection, and `fleet` with its completion for bash and its manual.
Of the units only `cockpit-fleet-scheduler.timer` is enabled and started.

## Tests

| What | Where | Runs on |
|---|---|---|
| Frontend unit tests | `test/*.test.ts` | development host, CI |
| Backend unit tests | `backend/tests/` | development host, CI |
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
| Several Linux nodes | `test/integration/multihost.sh` | controller with podman, as root; wipes the database |
| Callback cost | `test/integration/measure-events.sh` | controller, as root |
| Windows node over SSH | `test/integration/windows.sh` | controller, as root; wipes the database |

CI runs on Gitea (`.gitea/workflows/ci.yml`): type check, build, both unit test
suites, ruff, and a native package build with lintian, which fails on errors
and warnings. The integration scripts need the package installed on a
controller and are not part of CI yet.
