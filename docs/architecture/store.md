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
store_files (database)                   path, size, mtime_ns, inode, sha256, origin, source, updated_by
```

One store for the controller, read by every run, not for secrets
([decisions.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/decisions.md), "Store"). Viewers list and download; operators and administrators also
upload, move, copy, delete and download from URLs (`store.uploads`,
`store.downloads` and `store.discard` are an operator's). The tree is on
disk; the database keeps what the disk cannot say: the digest, where a file
came from (`upload`, `run`, `found` when put there by hand) and who put it
there. `sync()` brings the rows up to date with the tree at each listing
(no digest there: a listing must answer at once), which holds at most
20 000 entries and twelve levels (`complete` false beyond, which the page
says); a file whose inode, size
or mtime changed, or that appeared, gets its digest from the tick of the
scheduler, 40 s of it at most per tick, a file begun being finished.

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

Not symbolic links: `get_url` checks `os.access(dest, W_OK)` through the
link and refuses to replace a file. Hard links need the store and the runs
on one file system (a volume is mounted on `/var/lib/cockpit-fleet` whole):
across two, the run gets no store and its output says so. They are only
made to the account's own files (`fs.protected_hardlinks`): a file of root
put there by hand is left out of runs, said at the head of the output, and
marked on the page. How `get_url`, `copy` and `fetch` behave on the farm:
[history](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/store.md).

**Uploads.** A file of any size cannot be a parameter (that is how archives
travel, held whole in memory, 50 MB at most). The page opens a channel of
Cockpit's bridge that spawns `sudo -n -u cockpit-fleet fleetctl store.put`
with binary input, sends the parameters on one line and the file after
them, 64 KiB at a time read from the `File`, and keeps at most 16 MB
ahead of what `fleetctl` says it has written: with `progress`, `store.put`
writes a line `{"received": N}` before its answer for each MiB it writes.
Not Cockpit's acknowledgements (`send-acks: bytes`): the bridge sends them
as it receives the bytes, before the pipe to `fleetctl` takes them, so a
fast network fills the bridge's memory. No message
of the page is larger than 64 KiB (`FRAME`, `frames()` in `fleet.ts` for
input on stdin): cockpit-ws 370 closes the whole session on a message from
the browser over 128 KiB. `fleetctl` writes to a `.part` in
`store-uploads/`, digests as it receives, refuses more bytes than declared,
checks the digest it was given, and renames the file into the store under
the lock of the store. An upload that stops keeps its `.part`, named after
the user and the path: `store.uploads` lists them, `store.discard` drops
one, and the page resumes one by itself when the same file is uploaded to
the same path (`offset`). The tick drops those not resumed for a week.

**Downloads.** The page asks `store.get` (role, existence, size), then has
the browser open a link to an external channel of Cockpit,
`/cockpit/channel/<csrf token>?<base64 of the options>`, which spawns
`fleetctl store.read PATH` and serves its output with the
`content-disposition` the page put in the options: the browser saves it as
it comes, with nothing in the page's memory. Such a channel has no input,
hence the path in argv. A command that fails there still answers 200 with
no bytes, which is why the page asks first. A wrong CSRF token is refused
(404).

At a terminal, `fleet store put --file FILE --path PATH [--resume]` and
`fleet store read --path PATH [--output FILE|-]` do the same through
`fleetctl`.

**Over SFTP.** `store.sftp` (`sftp.py`, viewers) is a server of SFTP
version 3, the one OpenSSH speaks, on `fleetctl`'s input and output: a
command whose `stream` is `session` takes no parameters, and writes an
answer only when it fails, on stderr. A person points an SFTP client at
their own SSH login on the controller with `/usr/bin/fleet store sftp` as
the server to start there (WinSCP's "SFTP server" setting, `sftp -s`,
`sshfs -o sftp_server=`), and `fleet` hands its input and output to
`sudo -u cockpit-fleet fleetctl store.sftp`: the sudoers rule that exists
is all it takes, with no daemon, no root, nothing of sshd's to configure,
and the caller's role from their groups. Not OpenSSH's own `sftp-server`,
which would need a chroot owned by root, leave files owned by whoever
connected, give runs files half written and record nothing.

The root of the session is the root of the store; paths are made
absolute and normalised before `check_file_path`, so `..` stops at the
root, and every directory is opened from the one above with `O_NOFOLLOW`
as everywhere in the store: a link is neither listed nor followed. Viewers
read; a request that writes, from a viewer, is refused with
`PERMISSION_DENIED` and a record in the audit trail. A file opened for
writing goes to a `.part` among the uploads (named after the user and
`sftp` and the path, its `.json` marked `via: sftp` so that the page does
not offer to resume it), and is put in the store at its close, under the
lock, as `store.put` puts an upload: a run never gets half a file. A name
ending in `.filepart`, what WinSCP writes before renaming it, stays there
when closed, given to its writer only, and goes into the store when
renamed; a session that ends without a close leaves its file there as
well, and opening it again without truncating writes on from where it
stopped, which is how WinSCP and `reput` resume. The tick drops them after
a week. A file of the store opened for writing without truncating is
refused, since a write in place would reach runs before its end. The digest
is left to the tick: writes come at offsets, not in order. A time set
afterwards (WinSCP keeps the file's own) is kept, the row following it so
that the file is not taken for one changed by hand; permissions and owners
are taken and ignored. Renames are `store.move`'s
(`posix-rename@openssh.com` replaces a file, plain `RENAME` does not),
`REMOVE` and `RMDIR` (empty only) are `store.delete`'s, `MKDIR` is
`store.mkdir`'s: each is a record of the audit trail under that name, with
`sftp` as what it came through, and so is each file opened for reading
(`store.read`); the session is one more, `store.sftp`, at its end. It reads
no parameters, so a call is never told it comes from an agent: fleet-mcp
offers it to none (it is not a tool), and input that is not SFTP ends it at
once. `statvfs@openssh.com` gives what `store.put` would accept: the free
space of the disk less the gigabyte it keeps, within `[store] max_gb`. The
Store page says how to connect, under "With an SFTP client"
(`SftpAccess`): the server to name, and the line of `sftp` for the caller
at the address the browser reached. What SFTP leaves undone: [limits.md](../limits.md),
"Store".

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
digest again only if the file changes. Downloads have a lane of their own
in `dispatch` (`[store] max_downloads`, 2), so that a file of some
gigabytes does not hold a place of the playbooks; `run.cancel` stops one as
it stops a run.

**Sources.** A file downloaded from a URL keeps it as its source
(`store_sources`, schema 27, which gave one to the files downloaded
before): the URL, the digest it was asked to have, the
validators the server gave (ETag, Last-Modified), how often it is checked
(`never`, `daily`, `weekly`, `monthly`, `store.track`), when it was last
checked and last changed, and what went wrong. A file replaced by other
means (an upload, a run, a copy, a hand) leaves its source; a move takes it
along; a delete drops it. `store.refresh` (operators) queues a download of
the source with `If-None-Match` and `If-Modified-Since`: a 304 leaves the
file as it is and says so (`unchanged`), anything else replaces it, and the
digest tells whether it really changed (`changed_at`). The tick queues the
checks that are due, as the person who set how often, with a record in the
audit trail; one at a time for a file, a failure kept on the file. Not
schedules, which launch a template: a source has nothing to choose but how
often.

A download that ends short (failed, canceled) keeps what arrived in its
run directory for a week, with the validators of the version it was in
`progress.json`; `store.downloads` says how much (`partial`), and "Resume"
on the page is `store.fetch` with `resume`: the bytes move into the new
run, which digests them and asks for the rest with `Range` and `If-Range`
(a strong ETag, else Last-Modified; with neither it starts again). A
server whose file changed meanwhile answers with the whole file, which
then starts from nothing.

An operator may make the controller open a URL, which a playbook of an
administrator could already do. The job refuses the controller's own
addresses (loopback, and those of its interfaces, read from
`/proc/net/fib_trie` and `/proc/net/if_inet6`: the run shares the machine's
network, and Cockpit listens there) and link-local, multicast and
unspecified ones, in the connection itself: before it, on every address the
name resolves to, so that an address that does not answer is not waited on;
after it, on the address the socket is connected to, should the name
resolve otherwise the second time. Through a proxy, the proxy is what is
connected to. Other addresses of the local network are allowed.

**The page.** The Store page is `FileManager.tsx` (shared with the
Documents page, which gives it how each change is made, its columns and
its panel) over one `store.list` of the whole tree, indexed by folder in
the page: filter and search by name, sortable columns, a folder or a search
of more than 50 entries drawn a page at a time (50 to 500; paging saves
drawing rows, not reading them), multiple selection, move by drag, cut and
paste (a clipboard of the page) or "Move to…", copy (Ctrl+C, Ctrl with a
drag, "Copy to…"), rename (F2), delete, uploads of files and folders
dropped from the desktop (`webkitGetAsEntry`, empty folders made with
`store.mkdir`) or chosen, names already there asked about once for all
(replaced, uploads only; kept beside as `name (1).ext`; skipped), a menu
per entry and at the pointer, a panel of details (digest, origin,
`{{ fleet_store }}/path` to copy into a playbook). Reloading the page while
uploads go on asks first. A move is shown before
the backend answers and can be undone from the notice. Every action that a
drag does has a key or a menu too (WCAG 2.2, 2.5.7). Chrome on Linux gives
a drag's `dragover` and `drop` no modifier, so the page keeps whether Ctrl
or Alt is down from the keys and `dragstart`. The lists of uploads, of
downloads and of copies stay until closed, so as not to move the page under
the pointer.

**Many at once, copies, and a listing that has not changed.** `store.move`
takes `moves` and `store.delete` takes `paths` besides their single path:
under one lock every source is looked at and every target found free
before anything is touched, and a rename that still fails half way puts
back those made, so that the call did all or nothing, with one record in
the audit trail.

`store.copy` takes `copies` (path, to). A file is never a hard link to
another in the store: the intake of a run leaves out a file with other
links, and a write in place through one name would change the other. It is
copied with `copy_file_range`, which on XFS and Btrfs shares the blocks
(a reflink, at once whatever the size; the store counts the whole size all
the same) and on ext4 copies in the kernel. Up to 1 GiB in all
(`copier.COPY_AT_ONCE`) `fleetctl` copies at once, each file beside the
uploads then put in place under the lock as an upload is, keeping the
digest of its source when the source did not change meanwhile; a failure
removes what the call made. Above that a call would hold the page for tens
of seconds on ext4, so it queues a job of kind `copy` in the lane of
downloads: `store.copy` gives it the files as hard links in
`runs/<id>/copy/in/N` (they are copied as they were when asked), the job
(`copier.py`, in the sandbox of a run like every job) copies them to
`copy/out/N` with a `progress.json` as a download writes, and `_collect`
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
entry is listed, and every file opened and closed, before the first byte,
so that a path that is not there or not readable fails while the answer
can still say so; then a thread writes the tar into a pipe that `main()`
copies out, nothing of it held whole, opening each file again as it gets
to it: one open at a time (all of them kept open ran out of descriptors
past about a thousand). A file gone meanwhile is left out. The paths of a
link are bounded by the length of a URL: the page refuses a selection whose
link would pass 6000 characters (`ARCHIVE_LINK`) and asks for its folder
instead.

The editor of a project completes a path of the store after
`{{ fleet_store }}/`, a level at a time (a folder chosen opens what is in
it), from a `store.list` kept for 30 s.
