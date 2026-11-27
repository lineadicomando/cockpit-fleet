# The inventory of hosts: history

Archive: what was tried, measured and found while building the inventory
of hosts; not needed to work on it.

**16 October**, `cmdb.sh` in containers of Debian 13, Fedora 44 and
AlmaLinux 10: a Debian node's packages from dpkg, a package removed by
hand recorded as a change, a managed file changed by hand as drift at the
next cycle, its diff for an operator and not for a viewer. The module was
also run by hand in containers of Fedora 44 (dnf5), AlmaLinux 10 (dnf4,
security updates told apart) and openSUSE Leap 15.6 (zypper, which had no
update to find). Windows Update searched over SSH with a key and no
`become` on Windows 11 (`windows.sh`).
