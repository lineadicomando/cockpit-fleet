# Files of projects and inventories

The page of the files, the editor, syntax checks and help on modules, for
the projects ([projects.md](projects.md)) and inventories
([inventories.md](inventories.md)) written in the interface.

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

ansible-lint works inside the unit as it is.

**Files of a managed project or inventory.** Up to 1 MB each: text is
edited, a file that is not UTF-8 (an installer a playbook copies to nodes)
is uploaded in base64 and shown as binary, never edited. The same page
shows the files of the other kinds of project, read-only. How a change
becomes a commit is in [projects.md](projects.md).

`file.diff` gives what one commit did, as a unified diff. `file.restore`
puts a file or a directory back as it was at a revision, as a new commit,
exactly (files added since are removed). Only a revision in the history of
`main` is taken: a push that was refused leaves its objects in the
repository, links among them, and they must not reach the tree.
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
All of these are for administrators.

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

**Help on modules.** `module.help` returns the whole of what `ansible-doc`
knows about one module: description, requirements, parameters with their
suboptions, notes, examples and return values. It runs like `module.doc`,
outside the sandbox on what root installed, so it has the same limit: no
module of a project's own collections. The interface shows it in a dialog,
opened from the page of the files and from the ad-hoc command.
