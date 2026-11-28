# Overview and security model: history

Archive: what was tried, measured and found while building the security
model; not needed to work on it.

- **A polkit rule, then `fleet-unit`.** The first design let the service
  account manage its units through a polkit rule matching
  `cockpit-fleet-run@*.service`. `StartTransientUnit` carries the same
  `unit` and `verb` details as a normal start, so the rule also allowed
  `systemd-run --unit=cockpit-fleet-prepare@x.service /usr/bin/id` →
  `uid=0(root)` for any matching name that had no unit file yet. A name with
  a template is refused by systemd, but that depends on a file being
  installed, not on the rule.
- **Credentials copied by root.** Seen on 24 October: systemd 257 (Debian
  13) followed a `creds/` turned into a symbolic link and decrypted root's
  own credentials into a run; Fedora 44's 259 and AlmaLinux 10 refused.
  Hence `credstage.py`.
- **Runs isolated from each other.** A first version of the unit had no PID
  namespace: one run could read the decrypted secrets of a concurrent one
  through `/proc/PID/root` of its processes. Hence `PrivatePIDs=yes`.
- **`~/.ansible` on disk.** With the home as a tmpfs only, `get_url` on the
  controller failed with ENOSPC beyond about half the memory (a 3 GB file
  on a VM of 2 GB, 9 November); hence the bind of `scratch/remote_tmp`.
- **`SystemCallErrorNumber=EPERM`.** A seccomp kill made `copy` fail with
  "Bad system call": modules on the controller call `chown()` even when
  nothing changes.
