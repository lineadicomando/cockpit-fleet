# Installing a controller

The controller is the machine where Cockpit runs and Ansible starts: the
nodes it manages need nothing installed, only SSH (Linux, and Windows with
OpenSSH Server). This page is for the administrator who installs it and
gives people access. How it is built is in [architecture.md](architecture.md),
what it protects and what it does not in [security.md](security.md).

## What it needs

- Debian 13, Fedora 44 or AlmaLinux 10. Other distributions are not tried.
- Cockpit 337 or later, `ansible-core` 2.16 or later, systemd 257 or later,
  Python 3.12 or later (3.13 on Debian), `sudo`, `git` and the OpenSSH
  client, all from the distribution; the package depends on them.
- Recommended: `ansible`, for the collections it brings
  (`ansible.windows`, `community.general` and the rest), and `sshpass`, for
  nodes reached with a password. AlmaLinux has neither: there, collections
  come from a project's requirements file. `ansible-lint`, when installed,
  checks the playbooks written in the interface.
- No port of its own. People reach it through Cockpit (9090/tcp); the
  controller reaches the nodes over SSH, and the internet for git,
  `ansible-galaxy` and notifications, through `[network] proxy` if needed.
- Memory: about 1 GB beside the system for runs; check cycles of 50 hosts
  take some 700 MB at their peak. A machine of 2 vCPUs and 4 GB watched 500
  hosts with ten checks every 10 minutes ([architecture/checks.md](architecture/checks.md), "At scale").
- SELinux may stay enforcing on Fedora and AlmaLinux: the package needs no
  policy of its own.

## Installing

The packages of each release are on
[GitHub](https://github.com/lineadicomando/cockpit-fleet/releases), with
their `SHA256SUMS`; they can also be built from the sources (`make deb`,
`make rpm`, see the README). Install one with the distribution's tool,
which brings what it depends on (`X.Y.Z` is the version):

```sh
sha256sum --check --ignore-missing SHA256SUMS
sudo apt-get install ./cockpit-fleet_X.Y.Z_all.deb            # Debian
sudo dnf install ./cockpit-fleet-X.Y.Z-1.fc44.noarch.rpm      # Fedora
sudo dnf install ./cockpit-fleet-X.Y.Z-1.el10.noarch.rpm      # AlmaLinux
```

It installs:

- the page, "Fleet" in Cockpit's menu, and the command `fleet` with its
  manual (`man fleet`);
- these pages, in `/usr/share/doc/cockpit-fleet/docs` (compressed on
  Debian);
- the account `cockpit-fleet`, which owns the database and the projects and
  runs Ansible, and four groups: `cockpit-fleet-viewer`,
  `cockpit-fleet-operator`, `cockpit-fleet-admin` and
  `cockpit-fleet-approver`;
- `/var/lib/cockpit-fleet` (the database, projects, runs), `/etc/cockpit-fleet`
  (`config.toml`, and `secrets/`, the encrypted secrets);
- the template of the unit each run is, a timer that wakes the scheduler
  every minute, and a path unit that starts the delivery of notifications
  when there is something to send. Nothing else is running between runs.
- sudoers rules: the role groups reach the backend as the service account,
  administrators store secrets, operators hand over the secret answers of a
  survey, and the service account starts its run units, each through one
  fixed program.

## Giving people access

A person logs in to Cockpit with an account of the machine (or of its
domain), and what they may do in Fleet comes from the group of that account:

| Group | May |
|---|---|
| `cockpit-fleet-viewer` | see projects, inventories, templates, schedules, the outcome of runs, hosts and the states of their checks; not the output of runs |
| `cockpit-fleet-operator` | also launch templates (narrowing the hosts, a dry run, answering the survey, other tags, forks or output detail for that run), read the output of runs, cancel them, acknowledge problems, open maintenance windows, run a check or a schedule now, switch schedules on and off |
| `cockpit-fleet-admin` | everything: projects, inventories, credentials and their secrets, sets of variables and their secret variables, host keys, templates, schedules, checks, notifications, ad-hoc commands, the audit log, export and import |
| `cockpit-fleet-approver` | approve or reject the runs of templates that ask for it, whatever their role, or alone |

The highest of the three roles wins; the approver group adds to it. A
template or a step of a workflow asks for a confirmation, which who
launched the run may give too, or for another person: then nobody approves
their own launch but an administrator (unless `[approvals] admins_exempt =
false`).

```sh
sudo usermod -aG cockpit-fleet-operator anna
```

The new group counts from the next login. Someone in none of the groups
sees a page that says which ones exist. A user who turns on Cockpit's
administrative access, with no group, is an administrator of Fleet as root,
and the audit log then shows `root`, not the person: give people a group
instead.

## Configuration

`/etc/cockpit-fleet/config.toml` holds the limits an administrator of the
machine sets; the file installed shows every key with its default,
commented out. An unknown key is an error, which the System tab and
`fleet selfcheck` show. The parts most often changed:

- `[runs] max_concurrent`: runs at the same time; the rest wait in a queue.
- `[projects] local_roots`: directories of the machine that may be a project
  as they are. Empty, as installed, means none: projects come from git, are
  written in the interface, or are uploaded as an archive. `man fleet`,
  "Projects at a terminal", says how to make such a directory that
  administrators write and the service only reads. Not under `/home`,
  `/root`, `/run/user`, `/tmp` or `/var/tmp`: the scheduler does not see them, so such
  projects are refused; to keep one in a home, bind-mount it under a root
  (the manual says how). A run works on a copy of its project, but the
  roots themselves stay readable in its sandbox, so a playbook of any
  project could read every local project. If a root holds something the
  other projects must not see, hide it from runs with a drop-in, one line
  per root:

  ```ini
  # /etc/systemd/system/cockpit-fleet-run@.service.d/local-roots.conf
  [Service]
  InaccessiblePaths=-/srv/ansible
  ```

  then `systemctl daemon-reload`. The backend reads the roots outside the
  sandbox, so the copy still works.
- `[network] proxy`, and `[notifications] url`, where people reach Cockpit,
  for the links in the messages.
- `[network] scan_connected`, `scan_subnets`, `scan_ports` and
  `scan_max_addresses`: which subnets administrators may scan (Nodes,
  Network), with which ports and how large; as installed, those the
  controller is attached to, up to 1024 addresses. See
  [security.md](security.md).
- `[adhoc] modules`: the modules an administrator may run as an ad-hoc
  command; an empty list turns them off.
- `[mcp]` and `[chat]`: agents, off as installed; see [chat.md](chat.md).
- `[store] max_gb`: how much the store may take. The store is in
  `/var/lib/cockpit-fleet/store`, and must be on the same file system as
  the runs (`/var/lib/cockpit-fleet/runs`): a run gets its files as hard
  links. To give it a disk of its own, mount that disk on
  `/var/lib/cockpit-fleet` as a whole, with the package's services stopped
  and the directory's content moved onto it. Files copied there by hand
  appear on the Store page; give them to the service account (`chown -R
  cockpit-fleet: /var/lib/cockpit-fleet/store`), or runs are not given
  them.

### The store from an SFTP client

Anyone who may log in to the controller over SSH reaches the store with an
SFTP client, with their own role: viewers read, operators and
administrators also write. Nothing is to be set up on the controller; in
the client, name `/usr/bin/fleet store sftp` as the SFTP server to start:

- WinSCP: the site's Advanced settings, Environment, SFTP, "SFTP server".
- `sftp -s "/usr/bin/fleet store sftp" user@controller`.
- `sshfs -o sftp_server="/usr/bin/fleet store sftp" user@controller: DIR`.

The folder the client opens is the store. A file written there reaches the
store, and runs, when it is closed; WinSCP's transfers to a temporary name
(`.filepart`) are resumed if they stop. Clients that cannot name the server
(FileZilla, the file managers of GNOME and KDE) need sshd to start it
for them, which then is all those accounts can do over SSH:

```
# /etc/ssh/sshd_config.d/fleet-store.conf
Match Group store-sftp
    ForceCommand /usr/bin/fleet store sftp
```

The backend reads the file at each call: nothing needs restarting.

## First steps

As an administrator, in the page:

1. **System**: the self-check should find no problem.
2. **Host keys**: scan the nodes (one, or every SSH host of an inventory)
   and trust their keys. A run connects only to hosts whose key is trusted.
3. **Credentials**: a machine credential with the user Ansible logs in as,
   and its secrets (a private key, a password, the password for `become`).
   A secret is written, never read back. Vault passwords and the token or
   key of a private git repository are credentials too.
4. **Projects**: a git repository, a project written in the interface
   (empty, or from a starter: see below), or an archive.
5. **Inventories**: a file or directory of a project, or one written in the
   interface (empty, or from a starter).
6. **Job templates**: a playbook of a project, an inventory, a credential,
   and a survey if the launch needs answers. Then launch it.

The **Overview**, the first tab, then shows the state of the fleet at a
glance to every role: hosts, open problems, runs by day and outcome, what
fails most, what is coming up, and whether the controller keeps up.

To watch the nodes, add checks in **Checks** (see [checks.md](checks.md)),
and in **Notifications** a channel and a rule that says what to tell it.
The same can be done at a terminal: `fleet help` lists what the role of the
user may do.

## Starters

A project or an inventory written in the interface, and a new playbook,
may begin with a starter: files copied as they are, with no link kept to
them afterwards. The package has a Linux and a Windows one of each kind.
Your own go in `/etc/cockpit-fleet/starters`, laid out as the package's in
`/usr/share/cockpit-fleet/starters`:

```
/etc/cockpit-fleet/starters/projects/web/starter.toml
/etc/cockpit-fleet/starters/projects/web/site.yaml
/etc/cockpit-fleet/starters/projects/web/roles/...
/etc/cockpit-fleet/starters/inventories/branch/starter.toml
/etc/cockpit-fleet/starters/inventories/branch/hosts.yaml
/etc/cockpit-fleet/starters/playbooks/patch.yaml
/etc/cockpit-fleet/starters/playbooks/patch.toml
```

`starter.toml` (or `NAME.toml` beside a playbook) is optional and is not
copied:

```toml
title = "Web servers"
description = "nginx behind the company's proxy."
```

A name is letters, digits, `-` and `_`. One with the same name as the
package's does not replace it: both are offered. The files must be readable
by the group `cockpit-fleet`, and are refused if they hold a symbolic link
or a special file, a file larger than 1 MB, or more than 500 files; the
page shows why next to its name. An inventory's format is that of its main
file, `hosts.yaml` or `hosts.yml` (YAML) or `hosts` (INI). A playbook may
end in `.yml` too; what is made from it ends in `.yaml`. Do not put
passwords in them: a starter is copied into the history of everything made
from it.

Without writing files on the controller, a project or an inventory written
in the interface can itself be a starter: "Use as starter" in the menu of
its row (`fleet project starter` and `fleet inventory starter` at a
terminal). It stays in its list with the label "Starter", its files are
edited as before, and it is offered when a project or an inventory is
created, with what it holds then. No template, check, ad-hoc command or
maintenance window can use it, and it cannot be made one while something
uses it; an inventory's hosts leave the list of hosts, with what was
gathered of them, until "Use in runs again" and a new reading. Duplicate
one first to keep it in use as well.

## Upgrading

Install the newer package as the first one. The database, the projects, the
secrets and a changed `config.toml` are kept (on Fedora and AlmaLinux the
packaged one is then written beside it as `config.toml.rpmnew`, to compare);
runs going finish and are recorded. The database is brought up to date by
the backend within a minute. A page left open before the upgrade loads the
new version once the user logs in to Cockpit again.

On Debian, `apt` stops to ask about `config.toml` when it was changed here
and in the package too: keep yours, and compare with the packaged
`config.toml.dpkg-dist`.

## Removing

Removing the package stops the runs still going, and keeps the data, the
secrets and the configuration, so that installing it again brings
everything back:

```sh
sudo apt-get remove cockpit-fleet       # Debian
sudo dnf remove cockpit-fleet           # Fedora, AlmaLinux
```

To remove everything on Debian, purge it: the database, the projects, the
runs, the configuration and the encrypted secrets go too. A disk mounted on
`/var/lib/cockpit-fleet` is emptied, and stays mounted.

```sh
sudo apt-get purge cockpit-fleet
```

Fedora and AlmaLinux have no purge; after `dnf remove`, delete what is left:

```sh
sudo rm -rf /var/lib/cockpit-fleet /etc/cockpit-fleet /run/cockpit-fleet
```

On all of them the account `cockpit-fleet` and the four groups stay, since
files elsewhere (a directory of local projects) may belong to them; remove
them, if nothing does, with `userdel cockpit-fleet` and `groupdel` for each
`cockpit-fleet-*` group.

## Backups

See [backup.md](backup.md): the database and the projects are copied as
they are, the secrets cannot be, and an export without secrets moves a
configuration to another controller.
