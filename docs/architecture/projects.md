# Projects

Where the files of a run come from: projects of four kinds, their
collections, their `ansible.cfg`, starters and duplicates. Inventories are
in [inventories.md](inventories.md); the page of the files, the editor and
syntax checks in [files.md](files.md); how a run uses them in
[Jobs](jobs.md).

**Git projects.** `project.sync` (admin) queues a sync job, which makes a fresh shallow fetch of one reference into
its own directory and drops `.git`; the result replaces the project's tree
only if the fetch succeeded. Hooks are disabled, submodules are not followed,
and `GIT_ALLOW_PROTOCOL` leaves only https and ssh. A token is handed to git by
an askpass helper that reads it from the credentials directory, so it is in
neither argv nor the environment. The git commands are quiet, so the job
ends by writing what it fetched (the reference, the revision, the date and
the subject of the commit): the output of a sync that succeeded is not empty.

**Local projects.** A directory of the controller, under one of `[projects]
local_roots` (none by default) and not under a directory the units cannot
see (`/home`, `/root`, `/tmp`, `/var/tmp`, `/run/user`:
`jobs.HIDDEN_FROM_UNITS`), copied for each run (see [Jobs](jobs.md)). It is
not ours to write, and nobody tells the backend when it changes.
`project.files` (admin) lists the YAML and INI files and the directories of
a project of any kind, to choose a playbook or an inventory from.

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
/var/lib/cockpit-fleet/projects/<id>/lock        one change at a time (flock); a copy for a run shares it
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
`operations[].data` are redacted inside the list). What else the page of
the files does (restore, download, import, the limits of a file) is in
[files.md](files.md).

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

An inventory that is a file of a project is read again when a change in
the interface or a push touches what it reads: its own file or directory,
the directory it is in (its `group_vars` and `host_vars`), or a directory
it is in, moved or deleted. A fetch or an upload reads again every
inventory of the project.

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
into the backend. The browser sends the JSON in pieces of at most 64 KiB (`frames()`
of `fleet.ts`) on the same standard input, and `fleetctl` holds it in memory whole: that is what bounds
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
directory, not a link, as for a git fetch. An archive of 19 MB is answered
in 1.7 s and unpacked in under a second; 600 MB of zeros in a gzip of 600
kB are refused at the limit.

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

ansible-galaxy skips what the controller or the project already has, so with Debian's
`ansible` package a requirement for `ansible.windows` installs nothing, and an
installation that installs nothing is still one. A collection of 1000 files
from a public git repository is installed in 7 to 17 s and takes 5.7 MB.

**A project's `ansible.cfg`** is not used as it is: a run gets a generated one
with only the allowlisted keys of the project's file (`roles_path` and
`collections_path` when they stay inside the project, `gathering`,
`pipelining`, `become` settings and a few more). Anything that would load
plugins from elsewhere, point to a vault password file or relax host key
checking is dropped. The integration test ships a project that tries.

**Starters.** A new managed project, a new managed inventory and a new
file of a managed project may begin with ready-made content, a *starter*
(`starters.py`; "punto di partenza" in Italian, since "template" is the job
template's and Ansible's). They come from the package and from the
administrator, laid out the same way:

```
/usr/share/cockpit-fleet/starters/   the package's (starters/ in the source)
/etc/cockpit-fleet/starters/         the administrator's
    projects/NAME/                   a tree, with starter.toml (title, description)
    inventories/NAME/                a tree, the same
    playbooks/NAME.yaml              one file (or NAME.yml), with NAME.toml beside it
```

The metadata file is optional and never copied. `starter.list` (admin)
gives each one by `SOURCE/NAME` (`package/linux`, `local/linux`): both are
offered when both sources have one of the same name, since replacing the
package's would hide what an upgrade brings. `project.create` and
`inventory.create` take `starter` (managed only): its files are the first
commit, `Start from TITLE`, and the inventory's format is that of its main
file, so `content` and `files` are not taken with it. A playbook starter
is text: `starter.list` gives it, and the page writes it with `file.write`
in "New file". Nothing links what was made to its starter afterwards.

A starter is read under the rules of the editor, not trusted because it
sits in `/etc` or `/usr`: plain names that `file.change` would take, no
symbolic link (a link to `/etc/cockpit-fleet/secrets` would copy a secret
into a project), no special file, each file at most 1 MB, at most 500 files
and 16 MB. One that breaks them is listed with the reason and refused.
The package's titles are translated by the page, by name; an
administrator's are shown as written.

A managed project or inventory can be kept as a starter itself
(`project.starter`, `inventory.starter`, admin; a column `starter`,
schema 29): `starter.list` offers it as a third source, `saved/<id>`,
titled by its name, and `project.create` or `inventory.create` copy its
files as `main` has them, through the same `files.copy_operations` as a
duplicate, within the limits of a tree rather than of a starter on disk,
since the editor wrote them (an inventory also gives its `path`). The flag
is reversible, and nothing but the flag changes: it stays in its list and
is edited, checked and read as before. What makes it a starter is that no
run uses it, which the backend holds, not the page that hides it:
`store.usable` refuses it to a template (created, changed, launched), an
ad-hoc command, a check, a maintenance window and a file inventory of a
project so kept; and the flag is refused while one of those uses it. The
hosts of an inventory so kept are no nodes: `checks.sync_hosts` lists none
for it (so the host registry, the CMDB and the checks lose them, with what
was gathered), and `hostkey.list` leaves it out; making it usable again
queues a reading. `config.export` carries the flag and `config.import`
sets it again.

The package ships general ones, not shaped on a real project: a Linux and
a Windows project (a `site.yaml` through a role `common`, a `ping.yaml`,
variables by group; the Windows one with a `requirements.yaml` for
`ansible.windows`, which AlmaLinux does not package), a Linux and a Windows
inventory in YAML with an empty group, and a playbook of each. The
connection variables of Windows over SSH (`ansible_connection`,
`ansible_shell_type`) are in the Windows inventory and in the Windows
project's `group_vars/windows.yaml`, the project's winning;
`ansible_remote_tmp: C:\Windows\Temp\ansible` is there commented out,
since set it breaks `become` (see [Windows nodes](windows.md)).

**Duplicates.** `project.duplicate` and `inventory.duplicate` (admin) make
a new managed project or inventory from the files of another as `main` has
them, read under the same lock as a change, as the first commit of a new
repository (`Copy of NAME`): an empty `.gitkeep` becomes the folder it
keeps, made again. The history is not copied, so that a secret taken out
of a file stays where it was; a draft is not copied either. An inventory
keeps its format, `path`, vault and sets of variables, and is read once
made; nothing that uses the original (templates, checks, schedules)
follows. A project copied gets no syntax check of its playbooks (they
were checked as they were written), but its collections are installed if
its requirements ask for what is missing, as for any change.
