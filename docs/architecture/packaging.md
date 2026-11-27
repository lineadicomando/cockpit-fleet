# Packaging

The `.deb` is built in a Debian 13 container (`make deb`, output in `deb/`),
which also runs lintian; on a Debian 13 system `scripts/build-deb.sh --native`
does the same without a container. The frontend bundle is built beforehand
with npm, because the package build has no network. Both packages install
the same files through `make install-files`: the bundle into Cockpit's
directory; the Python package into `/usr/lib/cockpit-fleet`; the helpers
(`fleetctl`, `fleet-run`, `fleet-unit`, `fleet-secret`, `fleet-askpass`,
`fleet-mcp`, `fleet-chat`) into `/usr/libexec/cockpit-fleet`; `fleet` into
`/usr/bin` with its completion for bash and its manual; the bundled
collection and the starters into `/usr/share/cockpit-fleet`; the units, the
slice, the sudoers line, `config.toml`, and the `sysusers.d` (the account
and the role groups) and `tmpfiles.d` (the state and secret directories)
files. Of the units only `cockpit-fleet-scheduler.timer` and
`cockpit-fleet-notify.path` are enabled and started.

The documentation installed is `README.md`, `SECURITY.md`, `CHANGELOG.md`
and `docs/`, less the screenshots and `decisions.md`, which are for the
repository: the documents link to it, and to `TODO.md`, by their address
on GitHub. On Debian `CHANGELOG.md` is copied after `dh_installchangelogs`,
which would otherwise install it as the changelog of a native package, in
place of `debian/changelog` (lintian refuses that).

The `.rpm` for Fedora 44 and AlmaLinux 10 comes from `packaging/cockpit-fleet.spec`,
built in a container of each (`make rpm`, output in `rpm/`) and checked with
rpmlint, whose exceptions are in `packaging/cockpit-fleet.rpmlintrc`, each with
its reason; `scripts/build-rpm.sh --native` builds on the system it runs on.
rpm 4.19 creates the account and the groups from the `sysusers.d` file
before it installs the files that belong to them. The package also owns the
directories that `tmpfiles.d` creates, with the same modes. A preset file
(`system/cockpit-fleet.preset`) enables the timer and the path unit, and
`%post` starts them on the first install, as the `.deb` does.

**Versions.** A package carries its version in what it installs, so that a
controller says which build it runs. At a release tag that is the release,
`X.Y.Z`. Between two releases `scripts/version.sh` derives it from `git
describe`: `0.1.0+3.gabc1234` for dpkg and `0.1.0^3.gabc1234` for rpm, the
commits since the tag and the commit, `.dirty` when the tree has changes not
committed. Both forms sort above `0.1.0` and below `0.1.1`, so an upgrade to
the next release, or from one build to a later one, is an upgrade and not a
reinstall. The build scripts write it into their copy of the tree, with an
entry of `debian/changelog` or of the spec's `%changelog` and into
`__init__.py`, never into the repository; a container that builds has no
git, so the host passes `FLEET_DESCRIBE` into it. The Overview tab shows a
build as `0.1.0-3-gabc1234`.

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
stay loaded until the next reload, so `%postun` reloads systemd. `purge` of
the `.deb` also deletes `/var/lib/cockpit-fleet`, `/etc/cockpit-fleet` (the
secrets with it) and `/run/cockpit-fleet`; rpm has no purge, and
`docs/install.md` says what to delete. The account and its groups stay on
both: files elsewhere, a directory of local projects, may belong to them.

`scripts/ct-lifecycle.sh` (`make ct-lifecycle DISTRO=… OLD=…`) checks all of
it, `test/integration/lifecycle.sh` in a container where the package was
never installed: a package built from an older commit (or the latest tag),
its version set below the current one, installed and used (a project, an
inventory, a secret, templates, a schedule, a run); the current package
installed over it with a run going; then removal with a run going,
installing again, and purge. In the Debian container the script removes the
`policy-rc.d` of Debian's images, which forbids packages to start services.

## Releases

Made only when the user asks, with `make release` (`scripts/release.mjs`)
on a clean `main`. It runs the checks, computes the version from the kinds
of change under `[Unreleased]` of `CHANGELOG.md` (`make next-version` says
it beforehand; `VERSION=X.Y.Z` only goes higher), moves those entries under
`## [X.Y.Z] - YYYY-MM-DD` with the links at the end of the file, sets the
version in the files that carry it (`backend/cockpit_fleet/__init__.py`,
`package.json`, `package-lock.json`, the collection's `galaxy.yml`, the
spec's `Version:` and `%changelog`, `debian/changelog`; listed in
`scripts/versions.mjs`, checked by `test_version.py`), commits `Release
X.Y.Z` and tags `vX.Y.Z`. Nothing is pushed unless asked: then `git push
origin main vX.Y.Z`. The mirror brings the tag to GitHub, whose CI publishes
the packages built at it as the release of the tag ([tests.md](tests.md),
"CI"); check that it did (`gh release view vX.Y.Z -R
lineadicomando/cockpit-fleet`).

## Other distributions

The same code runs on the three controllers; what differs:

| | Debian 13 | Fedora 44 | AlmaLinux 10.2 |
|---|---|---|---|
| ansible-core | 2.19 | 2.20 | 2.16 (AppStream); no `ansible`, no `ansible-lint` |
| Python | 3.13 | 3.14 | 3.12, so the code keeps to 3.12 (ruff's `target-version`) |
| systemd, Cockpit | 257, 337 | 259, 368 | 257, 356 |
| SELinux | none | enforcing | enforcing |

The units, the helpers and sudo need no SELinux policy of their own: what
systemd starts from `/usr/libexec` runs unconfined, and
`LoadCredentialEncrypted`, the tmpfs over the state and `PrivatePIDs`
behave as on Debian. ansible-core 2.16 passes everything 2.19 passes, so it
is the minimum of both packages and of `selfcheck` (`_MIN_ANSIBLE` in
`system.py`); it is no longer supported upstream, so the integration
scripts run on AlmaLinux at every phase.

- `module.list` decides when to list the modules again from the package
  database: `/var/lib/dpkg/status` or the directory of rpm's SQLite database,
  whose writes go to a write-ahead log, and the collections of the `ansible`
  package, in `dist-packages` or in `site-packages` of the Python Ansible runs on.
- AlmaLinux's `nsswitch.conf` asks DNS before `myhostname`: if the
  controller's name is not in DNS, `socket.getfqdn()` takes seconds, so the
  name for the link of a notification is looked up once per process (once
  per delivery); `[notifications] url` avoids the lookup.
- The test nodes of `nodes.sh` need the key file relabelled (`:z`) where
  SELinux is enforcing, and `CAP_AUDIT_WRITE`, which the podman of EL does
  not give: Debian's sshd closes a session with a terminal when it cannot
  write its audit record, and ansible-core 2.16 asks for a terminal (`-tt`).
  Real nodes are not concerned.
