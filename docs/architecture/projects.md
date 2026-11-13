# Projects and inventories

Where the files of a run come from, and how they are written and checked.
How a run uses them is in [Jobs](jobs.md).

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
directory, so before each commit the backend puts an empty `.gitkeep` in
every directory left empty, or made empty with `mkdir`, and takes it out
of one that holds something else: a folder stays as long as no one
deletes it, in a clone and in an export too, which an empty project or
inventory to start from needs. `file.list` does not show an empty
`.gitkeep` (one with content is a file like any other); Ansible ignores
it, as it ignores every name that starts with a dot in an inventory
directory and among variables. An archive imported keeps its empty
directories the same way.

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
No vault password is given to a check of a playbook.

A change to an inventory written in the interface (saved, imported,
pushed) queues a `syntax` job too, beside the reading that lists its hosts,
and the editor offers the same check on any of its files. It is the
reading's `ansible-inventory --list` on a copy of the tree, with the
inventory's vault password, but with
`ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED`: a reading fails only when no
source could be read, and a broken file among the sources of a directory,
or a broken `group_vars` file Ansible skips with a warning, would pass it.
The check keeps nothing of what it listed, so the inventory's hosts and
its `parse_error` are those of the last reading. A file of the tree that is
no source of the inventory (its path names another file) is not checked.

Checked on Debian 13 with ansible-core 2.19.11 and ansible-lint 25.2.1, which
works inside the unit as it is.

**The page of the files** shows a tree, a project's or an inventory's, and
the files open in tabs beside it. The tree is not PatternFly's `TreeView`,
which draws every node as a button: Firefox starts no drag from inside a
button, and dragging is how files are moved. `FileTree.tsx` follows the
tree pattern of WAI-ARIA instead (one node in the tab order, arrows to
move, Enter to open, Space to select, F2 to rename, Delete), with a menu on
each node and checkboxes for a selection of several. A node dropped on a
folder is moved there, files dropped from the desktop are uploaded there;
the same is offered without a mouse by "Move to…" and "Upload files". Every
change the page makes is one `file.change` quoting the revision the tree
was listed at, so a tree changed meanwhile is refused, not overwritten.
Open files and what was typed in them stay, in memory, while another tab
of Fleet is shown; the browser asks before a reload loses them. A folder
made in the page holds nothing in git until a file is put in it.

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

