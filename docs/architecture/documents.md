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

**One renderer, on the backend** (`docrender.py`, standard library only).
`doc.render` gives a text as HTML, for the page, the preview while
editing (it takes the text being written as `content`, and the path for
its links and images) and the exports; never to agents, which read the
Markdown. A subset of GitHub's Markdown: headings, emphasis, strong and
strikethrough, code spans and blocks, quotes, lists nested and with tasks,
tables, thematic breaks, links inline, by reference, `<autolinks>` and bare
addresses, images; a front matter of `key: value` lines between two `---`
is given apart (`meta`), its `title` or the first heading of level 1 as
the title, and the headings with their ids for an outline. What it lets
through is decided there, not in the browser:

- raw HTML is text: the source reaches the page escaped, and the only
  tags and attributes are the renderer's own (a test parses the output of
  a corpus of attacks and of random input against that list);
- a link goes to `http(s)` (opened apart, `noopener noreferrer`),
  `mailto`, `fleet:KIND/ID` (an object of Fleet, for the page to open), or
  a file of the documents, written relative to the text or to the root of
  the tree and made `fleet:doc/PATH`; anything else (`javascript:`,
  `data:`, `file:`, `//host`, a path out of the tree) is left as its text;
- an image is a file of the tree, read as the others are (no link
  followed) and embedded as a `data:` URI, 4 MiB each and 16 MiB for a
  text at most: so the page and an exported `.html` need nothing else, and
  an image from elsewhere is fetched neither by the controller nor by the
  browser (a tracking pixel in a document an agent wrote, say); it shows
  as its text instead, and the answer lists it.

It is not CommonMark to the letter (a blank line in an item makes the
whole list loose, a table must have as many cells in its delimiter row as
in its header, HTML is never HTML); where it differs, it shows the text as
it was written. Its costs are bounded for a text of 1 MiB: brackets and
backticks are paired in one pass, emphasis looks back over 200 pieces at
most, quotes and lists nest 16 deep.

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
