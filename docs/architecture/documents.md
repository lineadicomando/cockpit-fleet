# Documents

Markdown written by people and by the assistant (reports of interventions,
studies, analyses), in folders, with their attachments (`documents.py`).
Decided on 25 November (`decisions.md`, "Documents").

```
/var/lib/cockpit-fleet/documents/           the tree: texts (.md) and attachments
/var/lib/cockpit-fleet/documents.git/       the repository of the texts
/var/lib/cockpit-fleet/documents-uploads/   uploads on their way in
/var/lib/cockpit-fleet/documents.lock       one change at a time
document_files (database)                   path, size, sha256, origin, source, who
```

A tree of its own, apart from the store and never given to runs: no run
gets it in its directory, and the tmpfs over the state directory hides it
from the sandbox as it hides everything else there. Roles are the store's:
viewers list, read, search and look at the history; operators and
administrators also write, upload, move, copy, delete and restore.

**The texts have a history; the rest does not.** A file whose name ends in
`.md` is a text: it goes into a git repository kept beside the tree, not
inside it (`documents.git`, the work tree given to git on its command
line, as for a project written in Fleet; the git helpers are those of
`files.py`). Every change is one commit, its author the caller: a write, a
move or a copy, a deletion, a restore. Any other file is an attachment (an
image a text shows, an export kept beside a text): out of the history, a
row of `document_files` says where it came from (`origin`: `upload` or
`export`; `source`: the text it belongs to, or what it was made from) and
who put it there. A commit takes the texts only: before each, the texts on
disk and those of the index are given to `git add --all` by path, so that
an attachment never enters the history and a text gone leaves it. A text
put in the tree by hand, or changed there, is committed by the service
before the next change, so that the commit of a person holds only what
they did. There are no drafts: an agent's write is a commit like a
person's, with the person it acts for as its author and the audit trail
saying it came through an agent.

**A replacement quotes what was read.** `doc.read` gives the digest of the
text; `doc.write` replacing a text takes it as `expected`, and a text
someone changed meanwhile, or deleted, is not overwritten. A new text is
written with no digest; one that exists is not, without it.

**What an agent sees.** `doc.read` gives an agent at most 16 KiB at once,
cut between characters, with the offset of the next part; a person gets
the whole text (1 MiB at most, also the bound of a write). `doc.search`
looks for every word of a query in the name and text of each text, the
most hits first, with a few of the lines that have one. Commands
that change the documents are in the class `doc` (`agents.md`); those that
read them in `read`.

**Files of any size** come in and go out as the store's do (`store.md`):
`doc.put` takes the parameters on one line and the bytes after them (no
resume: attachments are small next to the store's files), `doc.get`
writes them out for a channel opened by a link. At a terminal: `fleet doc
put` and `fleet doc get`. A text uploaded goes into the history, if it is
UTF-8.

**Limits.** `[documents] max_mb` (1024) bounds the whole tree, attachments
included; like the store, a file that would leave less than 1 GB free on
the disk is refused. Links in the tree are neither listed nor followed:
every directory is opened from the one above with `O_NOFOLLOW`.
