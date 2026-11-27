# Packaging: history

Archive: what was tried, measured and found while building packaging; not
needed to work on it.

- **Lifecycle.** From the commit that closed phase 8 (schema 15 to 17)
  `make ct-lifecycle` passed on the three on 25 October, and on the
  AlmaLinux VM, with no SELinux denial. On AlmaLinux 10 the removed units
  stayed loaded until the next reload, on a machine as in a container,
  hence the reload in `%postun`.
- **Other distributions.** Checked on 15 October on Fedora 44 and AlmaLinux
  10.2, both enforcing, with every integration script but `windows.sh` and
  `measure-events.sh`: all pass, and the audit log has no denial.
  ansible-core 2.16 passed everything that 2.19 passes, `multihost.sh`
  included. Again on 16 October, `checks.sh` included, on the three
  controllers: the modules of the checks run on the Debian nodes from
  ansible-core 2.16 as from 2.20. Checking Windows should need nothing that
  AlmaLinux lacks, since the module uses ansible-core only, but `windows.sh`
  was run from Debian only.
- **`getfqdn` on AlmaLinux.** 14 s on the test VM; the link in a
  notification asked for it once per channel, so a webhook came 20 seconds
  late.
