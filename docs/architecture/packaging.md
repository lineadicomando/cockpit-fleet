# Packaging

The `.deb` is built in a Debian 13 container (`make deb`, output in `deb/`),
which also runs lintian; on a Debian 13 system `scripts/build-deb.sh --native`
does the same without a container. The frontend bundle is built beforehand with npm,
because the package build has no network. The package creates the account and
the role groups (`sysusers.d`), the state and secret directories
(`tmpfiles.d`), and installs the units, the helpers, the sudoers line, the
bundled collection, and `fleet` with its completion for bash and its manual.
Of the units only `cockpit-fleet-scheduler.timer` and `cockpit-fleet-notify.path`
are enabled and started.

A package carries its version in what it installs, so that a controller says
which build it runs. At a release tag that is the release, `X.Y.Z`. Between
two releases `scripts/version.sh` derives it from `git describe`:
`0.1.0+3.gabc1234` for dpkg and `0.1.0^3.gabc1234` for rpm, the commits
since the tag and the commit, `.dirty` when the tree has changes not
committed. Both forms sort above `0.1.0` and below `0.1.1`, so an upgrade to
the next release, or from one build to a later one, is an upgrade and not a
reinstall. The build scripts write it into their copy of the tree, with an
entry of `debian/changelog` or of the spec's `%changelog` and into
`__init__.py`, never into the repository; a container that builds has no
git, so the host passes `FLEET_DESCRIBE` into it. A release itself is
`scripts/release.mjs` (`make release`): the version computed from the kinds
of change in `CHANGELOG.md`, set in the six files that carry it, a commit
and a tag.

The `.rpm` for Fedora 44 and AlmaLinux 10 comes from `packaging/cockpit-fleet.spec`,
built in a container of each (`make rpm`, output in `rpm/`) and checked with
rpmlint, whose exceptions are in `packaging/cockpit-fleet.rpmlintrc`, each with
its reason; `scripts/build-rpm.sh --native` builds on the system it runs on. It
installs the same files through the same `make install-files`. rpm 4.19 creates
the account and the groups from the `sysusers.d` file before it installs the
files that belong to them. The package also owns the directories that
`tmpfiles.d` creates, with the same modes. A preset file
(`system/cockpit-fleet.preset`) enables `cockpit-fleet-scheduler.timer` and
`cockpit-fleet-notify.path`, and `%post` starts them on the first install, as
the `.deb` does.

**Upgrade, removal, purge.** An upgrade keeps the database, the projects,
the secrets and a configuration changed by the administrator (a conffile on
Debian, `%config(noreplace)` with an `.rpmnew` beside it on rpm), restarts
the timer and the path unit, and leaves a run going alone: it keeps the unit
it was started with and is recorded when it ends. The database is brought to
the new schema by the first backend call that opens it, which the timer
makes within a minute. Removal stops what the package's programs would no
longer be there for: the runs still going, the scheduler and the delivery
of notifications midway, and the slice of the runs; a run stopped so is
failed, and its unit is reset so as not to leave the system degraded (the
`prerm` of the `.deb`, with a lintian override since only a pattern names
those units, and `%preun`). Data, secrets and the configuration stay; dnf
removes the directories it owns that are empty, and saves a changed
`config.toml` as `config.toml.rpmsave`. On AlmaLinux 10 the removed units
stayed loaded until the next reload, on a machine as in a container, so
`%postun` reloads systemd. `purge` of the `.deb` also deletes
`/var/lib/cockpit-fleet`, `/etc/cockpit-fleet` (the secrets with it) and
`/run/cockpit-fleet`; rpm has no purge, and `docs/install.md` says what to
delete. The account and its groups stay on both: files elsewhere, a
directory of local projects, may belong to them.

`scripts/ct-lifecycle.sh` (`make ct-lifecycle DISTRO=… OLD=…`) checks all of
it, `test/integration/lifecycle.sh` in a container where the package was
never installed: a package built from an older commit (or the latest tag),
its version set below the current one, installed and used (a project, an
inventory, a secret, templates, a schedule, a run); the current package
installed over it with a run going; then removal with a run going,
installing again, and purge. From the commit that closed phase 8 (schema 15
to 17) it passed on the three on 25 October, and on the AlmaLinux VM, with
no SELinux denial. In the Debian container the script removes the
`policy-rc.d` of Debian's images, which forbids packages to start services.

## Other distributions

The same code runs on the three controllers; what differs:

| | Debian 13 | Fedora 44 | AlmaLinux 10.2 |
|---|---|---|---|
| ansible-core | 2.19 | 2.20 | 2.16 (AppStream); no `ansible`, no `ansible-lint` |
| Python | 3.13 | 3.14 | 3.12, so the code keeps to 3.12 (ruff's `target-version`) |
| systemd, Cockpit | 257, 337 | 259, 368 | 257, 356 |
| SELinux | none | enforcing | enforcing |

Checked on 15 October on Fedora 44 and AlmaLinux 10.2, both enforcing, with
every integration script but `windows.sh` and `measure-events.sh`: all pass,
and the audit log has no denial. The units, the helpers and sudo need no
policy of their own: what systemd starts from `/usr/libexec` runs unconfined,
and `LoadCredentialEncrypted`, the tmpfs over the state and `PrivatePIDs`
behave as on Debian. ansible-core 2.16 passed everything that 2.19 passes,
`multihost.sh` included, so it is the minimum of both packages and of
`selfcheck` (`_MIN_ANSIBLE` in `system.py`). 2.16 is no longer supported
upstream: the integration scripts run on AlmaLinux at every phase. They did
again on 16 October, `checks.sh` included, on the three controllers: the
modules of the checks run on the Debian nodes from ansible-core 2.16 as from
2.20. Checking Windows should need nothing that AlmaLinux lacks, since the
module uses ansible-core only, but `windows.sh` was run from Debian only.

Found on the way:

- `module.list` decides when to list the modules again from the package
  database: `/var/lib/dpkg/status` or the directory of rpm's SQLite database,
  whose writes go to a write-ahead log, and the collections of the `ansible`
  package, in `dist-packages` or in `site-packages` of the Python Ansible runs on.
- AlmaLinux's `nsswitch.conf` asks DNS before `myhostname`. If the controller's
  name is not in DNS, `socket.getfqdn()` takes seconds (14 on the test VM).
  The link in a notification asked for it once per channel, so a webhook came
  20 seconds late. The name is now looked up once per process, that is once per delivery. Setting
  `[notifications] url` avoids the lookup.
- The test nodes of `nodes.sh` need the key file relabelled (`:z`) where
  SELinux is enforcing, and `CAP_AUDIT_WRITE`, which the podman of EL does
  not give: Debian's sshd closes a session with a terminal when it cannot
  write its audit record, and ansible-core 2.16 asks for a terminal (`-tt`).
  Real nodes are not concerned.
