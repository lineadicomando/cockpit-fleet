# Jobs

A run unit executes one job described by `spec.json` in its directory;
`fleet-run` dispatches on its `kind` (`runner.KINDS`):

| Kind | Does |
|---|---|
| `playbook` | ansible-playbook |
| `adhoc` | one module, as a playbook written for the job (below) |
| `inventory` | `ansible-inventory --list`, to learn hosts and groups |
| `syntax` | check a playbook without running it (then ansible-lint, when installed), or an inventory written in the interface |
| `sync` | fetch a git project |
| `unpack` | extract an uploaded archive |
| `galaxy` | install the collections a project asks for |
| `check` | a cycle of checks ([Checks](checks.md)) |
| `download`, `copy` | files of the store ([The store](store.md)) |
| `netscan` | the scan of a subnet ([The network](network.md)) |

When a reading of an inventory fails, Ansible quotes the lines it could
not read, which may hold passwords: why, and the output of an
`inventory` job or of the `syntax` check of an inventory, are for
administrators only. A run of a workflow is not a job: it has no unit, and
its steps are runs of playbooks ([Workflows](workflows.md)). All of them
need the sandbox, because all of them work on what a project brings:
inventory plugins and scripts, git itself, the rules a project gives to
ansible-lint, an archive made by anyone, a list of sources to download
from.

When a job is queued, `fleetctl` prepares `/var/lib/cockpit-fleet/runs/<id>/`:

| Entry | Content |
|---|---|
| `spec.json` | what to run; validated again by `fleet-run` |
| `project/` | a copy of the project (symbolic links copied, never followed; `.git` left out), taken under a shared lock of its tree, which a change or the end of a sync holds exclusive: never files half changed |
| `creds/` | ciphertext of the secrets this job may use, named by purpose |
| `known_hosts` | the host keys an administrator has trusted |
| `inventory/` | a copy of the tree of the inventory, when it is written in the interface rather than a project file |
| `collections/` | a copy of the collections installed for the project, when it asks for some |
| `store/` | made at dispatch, not here: the store as hard links (see [The store](store.md)) |

The copy of a local project opens each directory and file from the one
that holds it, following no link, so a tree its owner changes while it is
copied cannot lead outside it (into the database, say). What the service
account cannot read there (logs or reports that a tool of the owner writes
with mode 0600, in a directory git ignores) is left out of the copy instead
of failing the job, which would fail every run of such a project on files
it never used; the paths left out head the run's output, written to
`stdout.log` before the unit starts.

The run can write in its own directory, so what it leaves there is read by
`fleetctl`, outside the sandbox, through `jobs.open_run_file`: without
following a link (the backend could otherwise be led to the database, or to
another run's decrypted credentials through `/proc/PID/root`, both reachable
by the account), without waiting on a FIFO, and with a size limit for the
small files (1 MB for `result.json`).

The outcome, `result.json`, is in that directory too, so the job could
forge it: from a process it leaves running, or by changing `fleet-run` in
memory (`/proc/1/mem`, same account). `fleet-run` is PID 1 of the run's PID
namespace: it makes itself not dumpable as it starts (`/proc/1` is then
root's; ptrace is outside `@system-service`), and once the job is over it
kills every other process of the namespace and waits for all of them, so
none is in the middle of a write; then it removes whatever is under the
names of the outcome and writes its own. `sandbox.sh`, run D, tries both
ways. What the job writes for itself (events, artifacts, the data of a
check) stays its word, which is what it is.

When the job ends, everything it needed only while it ran is removed
(`jobs._tidy`: the copies, the credentials, the scratch space, the farm,
the archive), but what a download that ended short brought, kept a week to
be taken up; `stdout.log`, `events.jsonl` and `result.json` stay until
`[runs] keep_days` ([Maintenance](maintenance.md)).

No daemon tracks jobs. The `runs` table holds the state, and
`reconcile` brings it up to date from what a unit left behind: it runs when a
unit ends (`OnSuccess=`/`OnFailure=` start `cockpit-fleet-finish@ID.service`,
which is the backend itself, unsandboxed, as the service account) and whenever
runs are read. It also starts queued runs (`dispatch`), lane by lane
(`jobs.lanes()`): `runs`, every kind but the others, up to `[runs]
max_concurrent`; `downloads`, downloads and copies of the store, up to
`[store] max_downloads`; `checks` and `slow` (cycles of the slow kinds), up
to `[checks] max_concurrent` each. In a lane, a higher priority first (a
schedule gives its own to its runs; a launch by hand has 0), then the
oldest. Only one `reconcile` works at a time (a lock on the directory of
the runs): the unit that ends and a page that polls get there together, and
what a job left can be moved only once. The lock is waited for a while,
not for ever (a `fleetctl` stopped with Ctrl-Z while it holds it would
stop every page and every start): a page waits 5 s, then goes on without
collecting (the holder collects; queued runs still start); `run.reconcile`
of the unit that ends waits 2 minutes, then fails, and a later reading
collects the run (`store.flock_within`). A unit that is gone without a
`result.json` is recorded as an error, or as canceled if that was asked.

**Runs in series.** Two runs on one inventory would otherwise run at once
and may step on each other's hosts. A run of a template or an ad-hoc
command launched with `serial` (`options.serial`; ticked by default in the
launch forms, off in the API and in `fleet launch` without `--serial`) is
passed over by `dispatch` while another run is running on its inventory, or one launched before it (a lower `rowid`) is
queued there (`jobs._AHEAD`), and takes no place in its lane meanwhile.
Running ones count whatever their age (one approved after a later launch is
queued only then), and so does one chosen in the same pass. Runs waiting
for approval, check cycles and workflow runs (whose steps count) do not.
Outcomes do not matter: an order, not a success, which is what a workflow
is for. It is per inventory, not per host: two inventories that list the
same hosts are not ordered, two limits that share no host are. The waiting
run keeps what it was given at launch, as one waiting for approval does;
`run.get` and `run.list` say what it waits for (`jobs.waiting_for`).
Nothing starts it but `dispatch`, so no process waits
([decisions.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/decisions.md),
"Runs in series").

Projects and inventories, where the files of a run come from, are in
[projects.md](projects.md) and [inventories.md](inventories.md).

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

`hostkey.list` says which inventories have each host (its `known_hosts`
name, `address` or `[address]:port`, against the SSH hosts of each
inventory's last reading) and which SSH hosts no key is trusted for.
`hostkey.scan` and `hostkey.remove` also take `hosts` by that name, each
checked as `hostkey.add` checks one; a removal takes every key of those
hosts, or none if one of them has no key. `hostkey.export` gives the lines
a run gets, for some hosts or all: host keys are public, so a viewer may
read them.

**SSH keys with a passphrase.** Ansible has no option to read a passphrase from
a file, so `fleet-run` starts an `ssh-agent` inside the unit and loads the key
with `ssh-add`, which gets the passphrase from `fleet-askpass`. The agent lives
in the run's PID namespace and ends with it. The same helper answers git when
a token is used. `fleet-askpass` is a packaged file because a run's scratch
directory is mounted `noexec`; it prints the file named in its environment,
so the secret itself is in no command line and no environment.

**Several vaults.** A template lists any number of vault credentials; each has
an identifier, and each reaches Ansible as `--vault-id IDENTIFIER@FILE`, in the
template's order. Credentials used together must have different identifiers.
One without an identifier is given as `default@FILE`, Ansible's own name for
it, never as the file alone: the path of a unit's credentials has the `@` of
the unit's name in it, and Ansible would split it there.

**Sets of variables.** A set (`variable_sets`, schema 21, commands
`varset.*`) is a named object of extra variables written once and taken by
job templates, several in order (`template_variable_sets`), and by
inventories (`inventory_variable_sets`, schema 28), so that what an
inventory's files refer to reaches every job on it. At each launch
`runs.launch` merges them: the inventory's sets, then the template's, then
the template's own extra variables, a later one winning by top-level name,
plain or secret; the survey's answers and what earlier steps of a workflow
pass on come after, in their own files. An ad-hoc command and a check cycle
take the inventory's sets alone. A change of a set is thus in the next run
of everything that takes it; the run records which sets it took, by id and
name (`options.variable_sets`). Plain values are written by an
administrator, seen by every role, and stay templates for Ansible. Names
are those Ansible takes for a variable (`ansible_*` allowed, unlike a
survey's: a secret `ansible_become_password` works; a template's own extra
variables are checked the same way), 64 KB at most per object, 20 sets at
most to a template or an inventory (`variables.MAX_PER_TEMPLATE`). A set
in use cannot be deleted (a foreign key); deleting a template frees its
sets. A set wins over the defaults of roles and over the inventory's
`group_vars`, which depend on the hosts. The form of a template shows what a run will get, variable by
variable, with where each comes from (`src/lib/variables.ts`).

A set's secret variables (`secrets`: a name for a random slot) have their
values stored by `fleet-secret` as `s-SLOT`, like a credential's, set apart
from `varset.update` by the interface or `fleet secret --varset`, so that
none passes through `fleetctl`. A name left out of an update keeps its
slot, and a set sent without `secrets` keeps them all, so that a client that
knows nothing of them does not drop their values. At launch the ciphertext
of each secret that won is copied into the run's credentials
(`secret_var_N`); one with no value stops the launch (a check cycle is then
not prepared, and its states say why). `fleet-run` writes them, tagged
`!unsafe`, into `secret_vars.yaml` in the runtime directory, after the plain
extra variables, and they are masked in the output. Being extra variables,
they also reach the inventory's own files (a `group_vars` holding
`"{{ secret }}"`), while the listing of the inventory keeps the reference,
not the value. A run takes at most 30 (`variables.MAX_SECRETS`), and its
credentials at most 128 files (`credstage.MAX_FILES`). A secret is a set's,
not an inventory's, so that one value serves several inventories; Vault
files are not edited in the interface (decisions.md, "Vault files").

**Limits a launcher can change.** Launching a template, an operator may add a
host limit and ask for a dry run. The limit narrows the template's own and
cannot widen it. They are not joined into one pattern: Ansible reads the
plain terms of a pattern as a union before its `&` ones, so
`template:&all:other` would reach `other`. With both, the spec holds
`limits`, and `fleet-run`, in the sandbox, lists the hosts of each with
`ansible-inventory --list --limit`, keeps those in all of them, and runs the
playbook with `--limit @FILE` of their names. A name that a limit file would
read as a pattern (a group's, `all`, one with `*` or `[`) stops the run
rather than widen it; so does an empty intersection. It costs one reading of
the inventory per limit.

An operator may also replace, for one launch, the template's tags, tags to
skip, forks and verbosity (`runs.OVERRIDES`; `--tags`, `--skip-tags`,
`--forks`, `--verbosity` of `fleet launch`). Only what is given replaces the
template's, and tags given empty run every task; forks stay within `[runs]
max_forks`. Tags are not a control of what a role may do: an operator can
run tasks the template leaves out by them. What was replaced is kept in
`options.overrides`, shown with the run and carried by its audit record; a
template that needs approval still waits for it. A schedule stores the same
values (`overrides`, schema 30) and its runs take them; the steps of a
workflow take the template's.

**Ad-hoc commands.** `run.adhoc` (admin) runs one module on hosts of an
inventory, with no template: an inventory, a host pattern, a module, its
arguments, and optionally a machine credential, vault credentials, `become`
and a dry run. The module must be in `[adhoc] modules` of `config.toml`, by
its full name (`ansible.builtin.command`), since a short name could be
answered by another collection; root's file, so the list is set by whoever
administers the machine. The default has what one looks at a fleet with
(ping, facts, `command`, `shell` and their Windows counterparts,
`net_probe`: `config.ADHOC_MODULES`); an empty list turns ad-hoc commands
off. An administrator can write any playbook anyway: the list bounds a
single call, which matters once the caller is an agent (decisions.md,
"Ad-hoc").

The job does not call `ansible`: `fleet-run` writes a playbook of one play and
one task in the run's runtime directory and runs `ansible-playbook` on it,
with the same command line a template gets.

```
[{"name": "Ad-hoc command", "hosts": "all", "gather_facts": false,
  "tasks": [{"name": "<module>", "<module>": "<free form>", "args": {…}}]}]
```

So the module and its arguments are never in argv, the events and
everything built on them work as for any run, and there is one way to
build a command line to keep safe. The
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

**Surveys.** A template may ask questions at launch: typed fields (text,
number, choice, yes or no) with a default and bounds, defined by an
administrator. Each answer becomes a variable of the run, and this is the only
way an operator gives variables. `run.start` checks the answers against the
definition and refuses a variable that was not asked for.

Answers reach Ansible in a second variables file, after the template's own, so
an answer wins over a fixed value of the same name. They are written as
YAML with every string tagged `!unsafe`, which Ansible uses as it is
written: any variable holding `{{ ... }}` is otherwise rendered wherever it
is used, and `{{ lookup('pipe', ...) }}` is a command on the controller,
inside the sandbox but next to the run's decrypted secrets. The template's
own variables are an administrator's and stay templates. A survey cannot
set a variable whose name starts with `ansible_`: those change how a run
connects (`ansible_ssh_common_args`, `ansible_python_interpreter`). The
answers are stored with the run and in the audit record, in clear.

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

The ticket serves one launch: `run.start` moves the ciphertext into the
run, and a second use is refused. Until then it launches a run with the
secret, so it is in no record: `fleet-secret` logs no name, the audit masks
every ticket (`a-***`). The waiting directory is on tmpfs, writable by the
service account so that it can take a file away, and tmpfiles removes what
no run took within an hour. The helper refuses to store more than 500
waiting answers, or one above 4 KiB. The value is in clear in two places only, both
private to the unit and gone with it: the credentials directory and the
variables file. The run records which variables were given as secrets, never
their values. A secret field has no default and no bounds: a default would be
stored in clear, and nothing that could check a length ever sees the value.

**Masking.** What a run prints is filtered against the secrets of that run,
the decrypted files of its credentials directory: passwords, vault
passwords, tokens, secret answers, secret variables, and a private key line
by line. Each is replaced by `********` in two places: `fleet-run` filters
every line before it goes to `stdout.log` and to the journal, and the events
callback filters the values of an event before writing it (it is told the
directory with `FLEET_EVENTS_SECRETS_DIR`; the values, not the line of
JSON, where a secret that reads like a field name would break the event). A
value is looked for as it is and as JSON escapes it; secrets shorter than
four bytes are left alone, since masking them would mostly hide ordinary
output. It is a net, `no_log` is still the way to keep a value out; what
it misses is in [security.md](../security.md), "Secrets".
