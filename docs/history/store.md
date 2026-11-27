# The store: history

Archive: what was tried, measured and found while building the store; not
needed to work on it.

- **Hard links, not symbolic ones.** Symbolic links were tried first (a
  read-only bind of the store, a farm of links to it): `get_url` checks
  `os.access(dest, W_OK)` through the link and refused to replace a file.
- **A playbook in the run sandbox** on Debian 13 (ansible-core 2.19) and
  AlmaLinux 10 (2.16, SELinux enforcing, no denial), the same results (9
  November): `get_url` of a file present with the right checksum changes
  nothing; a stale or absent one gives a new inode; `copy` of a file and of
  a directory of links copies the files under the names of the links;
  `fetch` into the farm works; a write in place reaches the store.
- **Uploads and Cockpit's acknowledgements** (14 November). With
  `send-acks: bytes`, a file of 7 GB from a browser through an SSH tunnel
  to `deb13` (2 GB of memory) filled the bridge to 1.4 GB and then crawled
  at under 1 MB/s, the page at 55 % and the disk at 44 %; with the lines of
  `fleetctl`, the bridge stayed at 97 MB and the file arrived in 3 min 15 s
  (36 MB/s), its digest right. cockpit-ws 337 took a message of a
  megabyte from the browser; 370 closes the session over 128 KiB. A file of
  1.5 GiB dropped on the Store page arrived at about 85 MB/s, its digest
  right.
- **SFTP.** SFTP and FTP were ruled out on 11 November because OpenSSH's own
  `sftp-server` on the store would need a chroot owned by root, leave files
  owned by whoever connected, give runs files half written and record
  nothing; a server of our own avoids the four (15 November). Tried on 15
  November: unit tests with a client of the protocol written in them;
  OpenSSH's `sftp -D` against the module; `store.sh`, OpenSSH's `sftp` over
  an SSH login to the container through `fleet store sftp`, on Debian 13,
  Fedora 44 and AlmaLinux 10. On `deb13`, WinSCP 6.7.1 from `win11` (its
  scripting, an account of the operator role with a key): 300 MB up at 65
  MB/s and down at 32 MB/s, the digest the same; it wrote a `.filepart`,
  renamed it and set the time, which the store kept; a transfer killed at
  86 MB left its part among the uploads and nothing in the store, and `put
  -resume` went on from there; a file put over one that existed went as
  `REMOVE` then `RENAME` (WinSCP does not use `posix-rename` there), so the
  store has no file at that path for the moment between. `sshfs` 3.17 on
  `deb13` itself: copy, move, overwrite, directories and delete work, an
  append in place is refused (`Operation not supported`), and a file
  copied appears in the store a few tenths of a second after `cp` returns:
  FUSE closes it after the copy ends. The audit trail had each change.
- **Downloads from URLs** (9 November): `store.sh` downloads 256 MB from a
  container node on Debian, and is refused `127.0.0.1`, the controller's
  address on podman's network and `169.254.169.254`; on `deb13`, 1 GiB
  queued from the page came from a container node in about 10 s, its
  digest checked, the list of the store brought up to date when it landed.
  Checks of sources from the tick rather than schedules: decided on 11
  November.
- **The page** (11 November), tried in Chrome on `deb13`: selection, cut and
  paste, a drag onto the path, undo, rename, the context menu from the
  keyboard, search, a move and an upload onto names already there; the
  drop of a real folder from a desktop was not (the browser tools cannot
  make one), only the reading of its entries in a unit test. Chrome on
  Linux giving `dragover` and `drop` no modifier (`ctrlKey` false,
  `dropEffect` move) was seen on 14 November. Copies at once up to 1 GiB:
  decided on 11 November.
