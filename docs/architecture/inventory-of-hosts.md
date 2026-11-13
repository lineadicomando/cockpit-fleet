# The inventory of hosts

What each host is made of, the updates it lacks and whether it drifted from
what a playbook describes: three kinds of check, so that they inherit
planning, sharding, the sandbox, states, notifications, acknowledgement and
maintenance windows, decided with the user on 16 October.

```
cmdb      checks.py / win_checks.ps1 ── hardware, packages, services ──▶ cmdb_hosts, cmdb_packages, cmdb_services
                                                       a difference ──▶ cmdb_changes
updates   checks.py / win_checks.ps1 ── what is missing, security apart ──▶ cmdb_updates
compliance  a playbook of a project in --check --diff ── tasks that would change ──▶ drift
```

**Collection.** `cmdb` and `updates` are kinds of the catalog in the same
module as the other checks; their data travel in the result beside the
state, in `cmdb` and `updates`, because a node's packages do not fit in
`details` (16 KB). The events of a cycle keep lists of up to 50 000 entries
(`FLEET_EVENTS_MAX_ITEMS`, 200 for a run). On Linux: hardware from `/proc`,
`/sys/class/dmi` (the serial number only with `become`), `/sys/block`,
`/sys/class/net` and `ip -j address`; packages from `dpkg-query`, `rpm` or
`pacman`; services from `systemctl` when systemd runs. On Windows: CIM, the
uninstall keys of the registry (what "Apps and features" lists, 64 and 32
bits), `Get-NetAdapter`. Services are normalised to `running`, `stopped`,
`failed`, and `enabled`, `disabled`, `manual`, `static`.

**Updates.** apt by `apt-get -s dist-upgrade` (security when the origin says
so), dnf by `check-update` and `updateinfo list --security`, zypper by its XML
output and the security patches; the metadata the node has is used, and
`refresh` (which needs `become`) updates it first. Windows Update is searched
through its COM interface without downloading anything, from the SSH logon
with a key, with no `become` (`windows.sh`, Windows 11, 16 October). The node
reports the metrics `updates` and `security_updates`; the default threshold
is a warning from one security update.

**Storage.** The last snapshot of each host in normalised tables, the history
as changes only: a package that came, went or changed version, a service
that came, went or changed whether it starts at boot (not whether it runs,
which is a matter for the checks), a change of hardware, a change of the
addresses of an interface. The first snapshot records none. Changes are kept
365 days, then deleted by the tick (see [Maintenance](maintenance.md)). What a node
sends is read as untrusted: cut to size (20 000 packages, 5 000 services and
updates, 64 disks and interfaces), malformed entries left out. Memory that
moves by less than 5% is the kernel keeping more or less for itself, not a
change.

**Hardware.** A change of hardware (vendor, model, serial, processor, their
number, memory, disks, the MAC address of an interface) makes the `cmdb`
check warning for that cycle, hard at once whatever its attempts, so that
the rules tell about it once, and a recovery follows at the next cycle.

**Drift.** A check of the kind `compliance` names a playbook of a project,
which runs alone in `--check --diff`. Its events say, per host, which tasks
would change something and their diffs (those of the items of a loop
joined); the metric `changed` counts the tasks, with a warning from one by
default. A failed task makes the state unknown, an unreachable host is
suppressed as for any check. The diffs of the last cycle are kept per host
and check in `drift` (256 KB at most).

**Roles.** A viewer sees the CMDB (`cmdb.list`, `cmdb.get`, `cmdb.packages`),
which says no more than an outcome would of what is installed. The diffs of
drift may hold the content of files: `drift.get` is for operators and
administrators, as the output of runs.

**The interface.** "Assets" has the hosts with their system, hardware,
updates and compliance, filtered by any word and counted by system, exported
as CSV (a cell that would start a formula is opened by an apostrophe) or
JSON, and a search of a package across hosts. An export is made in the
browser as a `blob:` link; Firefox checks such a download against the
`frame-src` of the page's policy, which Cockpit sets to `'self'` by default,
so the manifest adds `frame-src 'self' blob:`. The page of a host has what it
is made of: hardware, packages, services, updates, changes and, for an
operator, drift. From its updates an operator launches a template of the
same inventory, narrowed to that host, through the usual launch (survey and
dry run included): which template applies updates is the user's, the
plugin installs nothing by itself.

Checked in containers of Debian 13, Fedora 44 and AlmaLinux 10 (`cmdb.sh`,
on 16 October): a Debian node's packages
from dpkg, a package removed by hand recorded as a change, a managed file
changed by hand as drift at the next cycle, its diff for an operator and not
for a viewer. The module was also run by hand in containers of Fedora 44
(dnf5), AlmaLinux 10 (dnf4, security updates told apart) and openSUSE Leap
15.6 (zypper, which had no update to find).
