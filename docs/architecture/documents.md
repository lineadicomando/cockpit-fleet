# Documents

Markdown written by people and by the assistant (reports of interventions,
studies, analyses), in folders, with their attachments (`documents.py`).
Decided on 25 November (`decisions.md`, "Documents").

```
/var/lib/cockpit-fleet/documents/           the tree: texts (.md) and attachments
/var/lib/cockpit-fleet/documents.git/       the repository of the texts
/var/lib/cockpit-fleet/documents-uploads/   uploads on their way in
/var/lib/cockpit-fleet/documents.lock       one change at a time (waited for 30 s at most)
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
person's, with the person it acts for as its author, and a trailer
`Fleet-Agent: VIA (PROFILE)` saying it came through an agent (phase 24;
`doc.history` gives it as `agent`, the page as "through the assistant"),
as the audit trail does.

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

**The page** (Resources, Documents; `DocumentsPage.tsx`). A folder is the
store's file manager (`FileManager.tsx`) on `doc.list`, with the changes
made by `doc.*` and attachments uploaded through `doc.put`; a text
(`#/documents/PATH.md`) is shown as `doc.render` gives it, in an iframe of
its own: `srcdoc`, `sandbox="allow-same-origin allow-modals"` (no
scripts; same origin so that the page can reach into it, modals for
printing) and a policy of its own in it (`default-src 'none'; img-src
data:`). The frame inherits Cockpit's policy too, which refuses inline
styles: the renderer aligns cells by class, and the page adopts the
document's stylesheet into the frame as a constructed sheet
(`adoptedStyleSheets`), as CodeMirror's are; the frame is as tall as the
document. A click on a link is taken by a listener the page puts on the
frame's document (it runs in the page's realm: tried with a trusted click
and keyboard on Chrome, 25 November): `fleet:doc/` opens another document,
`fleet:run/` and `fleet:host/` their pages, `#id` scrolls, http(s) and
mailto open in a tab of their own with `noopener`; the frame never
navigates. Editing (`?mode=edit`) is CodeMirror with Markdown beside a
preview, rendered by the backend 400 ms after the last keystroke; a save
quotes the digest read, with an optional line for the history. History,
diff and restore are a dialog as for projects. Exports: "Print or save as
PDF" prints the frame (the stylesheet has print rules: page margins, no
breaks inside code and rows), "Download the text" is `doc.get`, "Download
as a web page" is the rendered page saved from the browser with the
stylesheet and its policy inside it, the images in it (a `blob:` link
clicked in the shell's document, `saveBlob`: Firefox refuses it from the
page's frame). The document follows the theme: the page sets a class on
the frame's root as Cockpit's theme changes, a saved page follows the
system's `prefers-color-scheme`, and on paper it is always light. In the chat, a write
of the agent that went through (`doc_write`, `doc_restore`, a copy or a
move to a text) has "Open the document".

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
