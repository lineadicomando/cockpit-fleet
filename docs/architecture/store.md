# The store

Files on the controller that playbooks copy to nodes (`storage.py`):
installers, images, what runs fetched from nodes. The model is the store
of `ansible-win_edulab`, a directory its roles fill with `get_url`
delegated to the controller, that ISOs are put in by hand, and that
`win_copy` reads: 61 GB, files of 5 or 6 GB.

```
/var/lib/cockpit-fleet/store/            the tree, files by their own names
/var/lib/cockpit-fleet/store-uploads/    uploads on their way, per user and path
/var/lib/cockpit-fleet/store-farms/      what each running run was given
store_files (database)                   path, size, mtime, inode, sha256, origin, who
```

One store for the controller, read by every run, not for secrets (decided
on 9 November). Viewers list and download; operators and administrators
also upload, move and delete. The tree is on disk; the database keeps what
the disk cannot say: the digest, where a file came from (`upload`, `run`,
`found` when put there by hand) and who put it there. `sync()` brings the
rows up to date with the tree at each listing (no digest there: a listing
must answer at once); a file whose inode, size or mtime changed, or that
appeared, gets its digest from the tick of the scheduler, 40 s of it at
most per tick, a file begun being finished.

**A run gets the store as hard links.** When a run of a playbook or an
ad-hoc command starts (in `dispatch`, not when it is queued: a run waiting
a day for approval would keep alive files deleted meanwhile), `fleetctl`
makes `runs/<id>/store/`, the tree again with a hard link to every file,
and notes the inode, size and mtime of each in `store-farms/<id>.json`,
out of the run's reach. The playbook gets the directory as `fleet_store`
(the first `--extra-vars`, so a template may set its own). A link costs
nothing whatever the size of the file, and the store itself stays out of
the sandbox. When the run ends, `_collect` takes in what it left:

| In the farm | What happens |
|---|---|
| a link as it was given | nothing |
| a file with another inode at a path that was given (Ansible writes to a temporary name and renames it over the old one) | moved into the store, replacing the file: `rename`, same file system |
| a file at a new path, a new directory | moved in, made |
| a link with another size or mtime (written in place) | the store has it already, through the link: its digest is taken again, the change put down to the run |
| a path that was given and is gone | nothing: a run does not delete from the store |
| a file with other links, a symbolic link, a device, a name the store does not take, a file beyond the limit | left out, counted |

A file replaced in the store during the run is not taken back from the
farm (its inode is not the one noted); among two runs that replace the same
file, the last to end wins. What was taken is in the run's options
(`store`: the paths, by kind, and their counts) and in the audit trail as
`store.intake`, by who launched the run. The farm and its note go when the
job ends, not after `[runs] keep_days`, so that it pins no file.

Symbolic links were tried first (a read-only bind of the store, a farm of
links to it): `get_url` checks `os.access(dest, W_OK)` through the link
and refused to replace a file. Hard links need the store and the runs on
one file system (a volume is mounted on `/var/lib/cockpit-fleet` whole):
across two, the run gets no store and its output says so. They are only
made to the account's own files (`fs.protected_hardlinks`): a file of root
put there by hand is left out of runs, said at the head of the output, and
marked on the page.

Tried with a playbook in the run sandbox on Debian 13 (ansible-core 2.19)
and AlmaLinux 10 (2.16, SELinux enforcing, no denial), the same results
(9 November): `get_url` of a file present with the right checksum changes
nothing; a stale or absent one gives a new inode; `copy` of a file and of a
directory of links copies the files under the names of the links; `fetch`
into the farm works; a write in place reaches the store.

**Uploads.** A file of any size cannot be a parameter (that is how archives
travel, held whole in memory, 50 MB at most). The page opens a channel of
Cockpit's bridge that spawns `sudo -n -u cockpit-fleet fleetctl store.put`
with binary input, sends the parameters on one line and the file after
them, 64 KiB at a time read from the `File`, and keeps at most 16 MB
not acknowledged (`send-acks: bytes`). No message of the page is larger
than 64 KiB (`FRAME`, `frames()` in `fleet.ts` for input on stdin):
cockpit-ws 370 closes the whole session on a message from the browser over
128 KiB, where 337 took a megabyte. `fleetctl` writes to a `.part` in
`store-uploads/`, digests as it receives, refuses more bytes than declared,
checks the digest it was given, and renames the file into the store under
the lock of the store. An upload that stops keeps its `.part`, named after
the user and the path: `store.uploads` lists them, and the page resumes one
by itself when the same file is uploaded to the same path (`offset`). The
tick drops those not resumed for a week. Measured on Debian 13 from the
browser through Cockpit: 1 GiB in 12 s (85 MB/s), the memory of the page
flat; a file of 1.5 GiB dropped on the Store page arrived at about the
same rate, its digest right.

**Downloads.** The page asks `store.get` (role, existence, size), then has
the browser open a link to an external channel of Cockpit,
`/cockpit/channel/<csrf token>?<base64 of the options>`, which spawns
`fleetctl store.read PATH` and serves its output with the
`content-disposition` the page put in the options: the browser saves it as
it comes, with nothing in the page's memory. Such a channel has no input,
hence the path in argv. A command that fails there still answers 200 with
no bytes, which is why the page asks first. Measured: 2 GiB in 7 s. A
wrong CSRF token is refused (404).

At a terminal, `fleet store put --file FILE --path PATH [--resume]` and
`fleet store read --path PATH [--output FILE|-]` do the same through
`fleetctl`.

**Downloads from URLs.** `store.fetch` (operators) queues a job of kind
`download` (`fetch.py`), in the sandbox of a run like every job that
handles what comes from elsewhere. Its spec holds the URL, the digest
expected, the proxy of `[network]`, and the bytes the store has room for
(what `[store] max_gb` and the disk leave, taken when it is queued). The
job opens the URL with an opener that has handlers for http and https only
(on redirects too) and no proxy from the environment, writes to
`download/file` in the run's directory, on disk, digesting as it goes, and
a `progress.json` that `store.downloads` reads through `open_run_file`. A
connection that drops is taken up with a `Range` request, five times at
most with a pause that grows; a server that ignores `Range` sends the file
again. When the job has ended well, `_collect` moves the file into the
store as an upload's is (same lock, same placing, refused if a file came
to its path meanwhile and replacing was not asked), with the job's digest:
fleetctl does not read gigabytes again in a call, and the tick takes the
digest again only if the file changes.

Downloads have a lane of their own in `dispatch` (`[store]
max_downloads`, 2): a file of some gigabytes must not hold a place of the
playbooks for its whole time. `run.cancel` stops one as it stops a run.

**Sources.** A file downloaded from a URL keeps it as its source
(`store_sources`, schema 27; the files downloaded before were given one by
the migration): the URL, the digest it was asked to have, the validators
the server gave (ETag, Last-Modified), how often it is checked (`never`,
`daily`, `weekly`, `monthly`, `store.track`), when it was last checked and
last changed, and what went wrong. A file replaced by other means (an
upload, a run, a copy, a hand) leaves its source; a move takes it along;
a delete drops it. `store.refresh` (operators) queues a download of the
source with `If-None-Match` and `If-Modified-Since`: a 304 leaves the file
as it is and says so (`unchanged`), anything else replaces it, and the
digest tells whether it really changed (`changed_at`). The tick queues the
checks that are due, as the person who set how often, with a record in
the audit trail; one at a time for a file, a failure kept on the file.
Checks from the tick, rather than schedules of their own, because a
schedule launches a template: a source has nothing to choose but how
often (decided on 11 November).

A download that ends short (failed, canceled) keeps what arrived in its
run directory for a week, with the validators of the version it was in
`progress.json`; `store.downloads` says how much (`partial`), and "Resume"
on the page is `store.fetch` with `resume`: the bytes move into the new
run, which digests them and asks for the rest with `Range` and `If-Range`
(a strong ETag, else Last-Modified; with neither it starts again). A
server whose file changed meanwhile answers with the whole file, which
then starts from nothing.

An operator may now make the controller open a URL, which a playbook of an
administrator could already do but an operator could not. The job refuses
the controller's own addresses (loopback, and those of its interfaces, read
from `/proc/net/fib_trie` and `/proc/net/if_inet6`: the run shares the
machine's network, and Cockpit listens there) and link-local, multicast and
unspecified ones, in the connection itself: before it, on every address the
name resolves to, so that an address that does not answer is not waited on;
after it, on the address the socket is connected to, should the name
resolve otherwise the second time. Through a proxy, the proxy is what is
connected to. Other addresses of the local network are allowed.

Tried on 9 November: `store.sh` downloads 256 MB from a container node on
Debian, and is refused `127.0.0.1`, the controller's address on podman's
network and `169.254.169.254`; on `deb13`, 1 GiB queued from the page came
from a container node in about 10 s, its digest checked, the list of the
store brought up to date when it landed.

**The page.** The Store page works as a file manager does, on what
`store.list` gives (one listing of the whole tree, indexed by folder in the
page): a filter of the folder or a search of the whole store by name,
columns to sort by (kept in the browser), many entries selected (checkboxes,
Ctrl or Shift and a click, Ctrl+A), moved by a drag onto a folder or onto
the path above, by cut and paste (Ctrl+X, Ctrl+V: a clipboard of the page,
not the system's, which cannot hold a file of the server) or by "Move to…"
with the folders offered as one types; renamed in place (F2), deleted
(Delete); files and whole folders dropped from the desktop onto the list or
onto a folder (the folders read through `webkitGetAsEntry`, an upload per
file with its path, empty folders made with `store.mkdir`), or chosen with
"Upload a folder". Names already there are asked about once for all:
replaced (uploads only: `store.move` never replaces), kept beside under
`name (1).ext`, or skipped. Each entry has the same actions in its menu ⋮,
in a menu where the pointer is (right click, the menu key, Shift+F10) and,
for one entry, in a panel of details beside the list (the digest, where it
came from, `{{ fleet_store }}/path` to copy into a playbook). A move is
shown before the backend answers and can be undone from the notice that
follows it. Many entries are moved, deleted or copied in one call, with
one record in the audit trail. Every action that a drag does has a key or a menu that does
it too (WCAG 2.2, 2.5.7). Tried in Chrome on `deb13` on 11 November:
selection, cut and paste, a drag onto the path, undo, rename, the context
menu from the keyboard, search, a move and an upload onto names already
there; the drop of a real folder from a desktop was not (the browser tools
cannot make one), only the reading of its entries in a unit test.

**Many at once, copies, and a listing that has not changed.** `store.move`
takes `moves` and `store.delete` takes `paths` besides their single path:
under one lock every source is looked at and every target found free
before anything is touched, and a rename that still fails half way puts
back those made, so that the call did all or nothing. The page cuts or
copies (Ctrl+X, Ctrl+C), pastes (Ctrl+V), drags with Ctrl to copy, and has
"Copy to…" beside "Move to…".

`store.copy` takes `copies` (path, to). A file is never a hard link to
another in the store: the intake of a run leaves out a file with other
links, and a write in place through one name would change the other. It is
copied with `copy_file_range`, which on XFS and Btrfs shares the blocks
(a reflink, at once whatever the size; the store counts the whole size all
the same) and on ext4 copies in the kernel. Up to 1 GiB in all
(`copier.COPY_AT_ONCE`, decided on 11 November) `fleetctl` copies at once,
each file beside the uploads then put in place under the lock as an
upload is, keeping the digest of its source when the source did not change
meanwhile; a failure removes what the call made. Above that a call would
hold the page for tens of seconds on ext4, so it queues a job of kind
`copy` in the lane of downloads: `store.copy` gives it the files as hard
links in `runs/<id>/copy/in/N` (they are copied as they were when asked),
the job (`copier.py`, in the sandbox of a run like every job) copies them
to `copy/out/N` with a `progress.json` as a download writes, and `_collect`
puts what it made in place, each target still free (`take_copy`), the tick
taking the digests. `store.downloads` lists copies beside downloads.

`store.list` answers with a `token`, a digest of what it lists but the free
space. Given back as `since`, it is answered `unchanged` (with the free
space) when nothing changed: the walk of the tree is done either way, what
is saved is the answer and the page drawing it again. The page asks every
10 s while it is shown, so that what a run or a hand put there appears.

**Archives, and paths in a playbook.** `store.archive` (viewers) writes
files and directories of the store out as one tar, not compressed (what
the store holds is installers and images, which do not compress), named in
it from their own name down. It streams out as `store.read` does, through
the link of an external channel, so its paths come in argv: `fleetctl
COMMAND PATH...` takes several for a command that declares `paths`. Every
file is opened before the first byte, so that a path that is not there
fails while the answer can still say so; then a thread writes the tar into
a pipe that `main()` copies out, nothing of it held whole. The paths of a
link are bounded by the length of a URL: the page refuses a selection whose
link would pass 6000 characters and asks for its folder instead.

The editor of a project completes a path of the store after
`{{ fleet_store }}/`, a level at a time (a folder chosen opens what is in
it), from a `store.list` kept for 30 s.
