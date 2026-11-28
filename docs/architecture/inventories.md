# Inventories

Inventories of files, the inventory chosen at launch, and inventories
written in the interface. Projects are in [projects.md](projects.md); the
page of the files and the editor in [files.md](files.md).

**Inventories.** A file inventory is a path of a project: a file of hosts, a
directory of sources (read together, as Ansible does: static files and plugin
configurations side by side), or the YAML file of an inventory plugin. Every
one is read by a job, in the sandbox, so a plugin runs there and nowhere
else. A plugin gets no credential beyond the vault of the inventory.

A job enables Ansible's default inventory plugins but `script`: an
executable file of a project is read as text and never run. `[inventories]
scripts = true` in `config.toml` puts it back, for the whole installation;
the script runs in the sandbox like the rest of the project. And
`ANSIBLE_INVENTORY_UNPARSED_FAILED` is set: an inventory that nothing can
read fails the job, instead of a playbook going on with no host and ending
as a successful run of nothing.

The reading is `ansible-inventory --list --export`, whose listing holds
every variable of the inventory, and those of a file encrypted with
Ansible Vault in clear (inline `!vault` values stay encrypted). So it is written in the run's runtime directory,
gone with the unit, and `fleet-run` writes to the run's directory only
the hosts, the groups and the three variables that say how a host is
reached (`runner.listing_hosts`), which fleetctl deletes once it has taken
them. The whole listings that versions before 0.3.0 kept are removed by
the packages on such an upgrade (`debian/cockpit-fleet.postinst`, `%pre` of the spec),
not those of later versions, which a run not yet collected needs.

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
inventory's project is not there.

**The inventory chosen at launch.** A template's `inventory_id` is its
default; `inventory_ids` lists the others whoever launches it may choose
instead (table `template_inventories`, schema 38), and `any_inventory`
lets them choose any, so that one template serves several labs alike.
`run.start` takes `inventory_id`, refused (`conflict`) when the template
does not allow it, and the run records the inventory it is on, which is
what runs in series, the rules of notifications, approvals and the filters
read. The template's limit applies to the inventory chosen; the sets of
variables are that inventory's, then the template's. An inventory carries
a machine credential of its own (`machine_credential_id`): a run takes the
template's if it has one, else the inventory's, so the accounts of labs
that differ need no template each; an ad-hoc command or a check with none
takes it too. An inventory that a template allows cannot be deleted (the
answer names the templates), nor kept as a starter. The list of templates
shows a template under each inventory it allows, "also on N others"
beside it, and launches it there. A row speaks of what it is listed
under: its "Last run" is the last there ("Never here", and muted the last
elsewhere with its inventory, when it has none there), and so are the
order by last run and the filter by outcome; grouped otherwise, the last
on any inventory, with that inventory's name when it is not the one of
the column. `template.list` gives `last_runs`, the last run on each
inventory by id, beside `last_run`, which carries its `inventory_id` (the
one chosen at launch: `runs.inventory_id`). The launch form offers the choice when
there is one, and `fleet launch --inventory` takes a name. A schedule
keeps the inventory it launches on (null: the default), and an agent's
tool of the template takes `inventory_id`, an enum of the inventories
allowed (every one not kept as a starter, for "any").

A template may have no default (`inventory_id` null, schema 39), so that
one serving every lab alike cannot run on the wrong one by a launch that
forgot to choose: it must then allow others, or any (`bad_request`
otherwise), and every launch names one. `run.start` without
`inventory_id` answers `bad_request` ("no default inventory: choose one");
so does `schedule.create` or `.update` without one, and an agent's tool of
it requires `inventory_id` even when it allows a single inventory (a
template whose allowed inventories are all starters is no tool). The
default is not taken away while a schedule relies on it (`conflict`,
naming the schedules): one of the template naming no inventory, or one of
a workflow that names none with the template as a step. In the interface
the form offers "None: chosen at each launch", the launch form has no
inventory chosen until one is, and the list shows such a template, when
not grouped by inventory, as "chosen at launch (N allowed)".

**Inventories written in the interface** are a tree of files, like a managed
project, with a git repository beside it (not a hidden managed project
behind each, which every list, export and check of projects would have had
to leave out):

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
hosts with `group_vars/` and `host_vars/` beside it, a vault file, files
of plugins with the names they want, `*.aws_ec2.yml`), or a file or
directory of it: the inventory's `path`. A run gets a copy of the tree as
`inventory/`, made as the copy of a project is; an executable file in it
is never run as a script, whatever `[inventories] scripts` says.

`inventory.create` and `inventory.update` still take `content`: the text of
its main file, `hosts.yaml` (or `hosts` for INI), written as a commit;
`inventory.create` also takes `files`, every file of the tree, which is how
an export brings an inventory back. An inventory written before it was a
tree had its text in the database (`content`): the first time its tree is
needed, the text becomes `hosts.yaml` (or `hosts`) of a new repository, in
a commit of the service account, and leaves the database, with no step of
the upgrade. The revision of the tree is in the database (schema 20) and in
what `inventory.get` answers.

The YAML files the backend names itself end in `.yaml` (`decisions.md`,
"YAML extension"). An inventory made before 0.3.0 keeps its `hosts.yml`:
`inventory.get` and `inventory.update` use it as the main file while there
is no `hosts.yaml` beside it, and renaming it is left to the
administrator. What a user names keeps its name, and what is read is
still read under both (`requirements.yml`, a playbook starter `NAME.yml`).
