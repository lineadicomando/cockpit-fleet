# Jobs

A run unit executes one job, of one of eight kinds, described by `spec.json`
in its directory: `playbook` (ansible-playbook), `adhoc` (one module, as a
playbook written for the job), `inventory` (`ansible-inventory --list`, to
learn hosts and groups; when it fails, Ansible quotes the lines it could not
read, which may hold passwords, so why is shown to administrators only,
and so is its output), `sync` (fetch a git project), `syntax` (check a
playbook without running it, or an inventory written in the interface,
whose output is for administrators as a reading's is), `unpack` (extract an uploaded archive),
`galaxy` (install the collections a project asks for) and `check` (a cycle
of checks, see [Checks](checks.md)). A run of a workflow is not a job: it has no
unit, and its steps are runs of playbooks (see [Workflows](workflows.md)). All of them need the sandbox, because all of them work on what a project
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
directory (see [Maintenance](maintenance.md)).

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

**Runs in series.** Two templates on one inventory launched one after the
other run at once, and may step on each other's hosts. A run of a template
or an ad-hoc command launched with `serial` (`options.serial`, no column of
its own; the box of the launch forms is ticked by default, the API and
`fleet launch` leave it off unless asked) is passed over by `dispatch` while another run is running on its
inventory, or one launched before it (a lower `rowid`) is queued there; it
takes no place in its lane meanwhile, so runs on other inventories start
past it. Running ones count whatever their age, since a run approved after
a later launch is queued only then; one chosen in the same pass counts too,
as a later run of a higher priority may come first. Runs waiting for
approval do not hold it back (they may wait a day), nor do check cycles,
which have lanes of their own, nor workflow runs, whose steps count.
Outcomes do not matter: it asks for an order, not for a success, which is
what a workflow is for. The check is on the inventory, not on the hosts its
limit leaves: two inventories that list the same hosts are not ordered, and
two limits that share no host are. The waiting run keeps what it was given
at launch (a copy of the project, the inventory, `known_hosts`), as one
waiting for approval does. `run.get` and `run.list` say what it waits for
(`waiting_for`: the last launched of the runs that hold it back). Nothing
starts it but `dispatch`, which runs when a unit ends, so no process waits.

Projects and inventories, where the files of a run come from, are in
[projects.md](projects.md).

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

**Sets of variables.** A set (`variable_sets`, schema 21, commands
`varset.*`) is a named object of extra variables that job templates take,
several in order (`template_variable_sets`, like `template_vaults`), so that
a value common to many templates, such as a domain or the name of an ISO for
`ansible-win_edulab`, is written once. At launch `runs.launch` merges them
into the template's extra variables: the sets in the template's order, a
later one winning by top-level name, then the template's own; the survey's
answers and what earlier steps of a workflow pass on still come after, in
their own files. The merge happens at each launch, so a change of a set is
in the next run of every template that takes it, schedules and workflow
steps included; the run records which sets it took, by id and by name as
they were (`options.variable_sets`). Like the template's own they are
written by an administrator, seen by every role, and stay templates for
Ansible: not a place for secrets. Names are those Ansible takes for a
variable (the template's own extra variables are checked the same way
since), 64 KB at most per object. A set in use cannot be deleted (a foreign
key, as for credentials); deleting a template frees its sets. They differ
from the `group_vars` of an inventory, which depend on the hosts and lose
to extra variables: a set is chosen by the template and wins over the
defaults of roles and over the inventory. The form of a template shows what
a run will get, variable by variable, with where each comes from
(`src/lib/variables.ts`). Discarded for now: secret variables in a set
(they would be encrypted as credentials are) and sets for ad-hoc commands.

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
