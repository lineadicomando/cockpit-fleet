# CLAUDE.md

Agent instructions for this project.

## What this is

`cockpit-fleet` is a Cockpit plugin that drives Ansible
from the browser, inspired by Semaphore UI, and that also collects, stores and
notifies on information from managed nodes using Ansible only (agentless).

Three documents, each with one job; do not repeat one in another:

- `TODO.md`: status, decisions, open points and what is left to do.
  Read it first. Tick items as they are completed, record a decision when it
  changes, and keep it short: what is done moves out, to the place below.
- `docs/architecture.md`: what exists, why it is built that way, and what was
  measured. Update it with every change of design.
- This file: how to work on the project.

`docs/install.md`, `docs/security.md`, `docs/checks.md`, `docs/backup.md`
and `docs/chat.md` are for the administrator of a controller: installing and
giving access, what is protected and what is not, writing checks, backups,
turning the chat on. Keep them in step with what they describe (roles,
`config.toml`, the catalog, `fleet_result`, the files a controller keeps,
`[chat]`) when that changes.

`CLAUDE.local.md`, `Makefile.local` and `.env`, when present, are not
tracked: they describe one developer's own workstation (the state of its test
VMs, passwords, paths, hosting, deploy targets). Nothing of them goes into tracked files, which must make sense to
anyone who clones the repository.

Layout: `src/` frontend, `backend/cockpit_fleet/` Python package,
`backend/libexec/` the helpers installed in `/usr/libexec/cockpit-fleet`,
`backend/bin/` the command `fleet` installed in `/usr/bin`,
`backend/tests/` unit tests, `collection/` the bundled Ansible collection
(the callback of the runs, and roles an administrator runs on the controller),
`system/` units, sudoers, sysusers, tmpfiles and the default config,
`debian/` and `packaging/` (the RPM spec) packaging, `test/` frontend tests, `test/integration/` scripts for
a controller, `test/containers/` and `test/vm/` the controllers they run on
(containers, VMs), `.claude/skills/` agent skills, `po/` translations.

## Non-negotiable rules

These come from the security model in `docs/architecture.md`; a change that breaks one of
them needs the user's explicit agreement.

1. **No daemon, no listening port.** Work happens in systemd units started on demand.
2. **The frontend never builds an Ansible command line.** It calls `fleetctl`
   subcommands with JSON on stdin; `fleet-run` composes argv from a stored `spec.json`.
3. **No shell.** Spawn with argv arrays, in TypeScript and in Python
   (`subprocess` with a list, never `shell=True`).
4. **Secrets never appear** in argv, environment, logs, events, the database or any
   response to the frontend. They enter through stdin and reach Ansible as files in
   the run's runtime directory.
5. **Every `fleetctl` command declares its minimum role** and every mutating command
   writes an audit record. Roles come from Unix groups of the caller (`SUDO_UID`).
6. **Privileged code stays tiny.** Only `fleet-unit` and `fleet-secret` run as
   root. Do not add a third root entry point or a sudoers wildcard, and do not
   use polkit rules that match unit names: they also authorise transient units
   with that name, which means arbitrary code as root.
7. **Validate on the backend.** UI validation is a convenience, not a control.
8. **Runs share one Unix account, so they must stay isolated from each other.**
   Anything a run can reach, a playbook of another project can reach. Do not
   remove `PrivatePIDs`, the tmpfs over the state directory or over `/run`, and
   test a change to the unit with `sandbox.sh`.
9. **Nothing executable in a run's scratch directory.** It is mounted `noexec`.
   A helper a run must execute is a packaged file in `/usr/libexec/cockpit-fleet`
   that takes paths from its environment, like `fleet-askpass`.
10. **Checks are OS agnostic.** A catalog check has one output schema and one
   implementation per platform; Windows nodes are reached over SSH, not WinRM.

## Stack and conventions

- Frontend: TypeScript, React 18, PatternFly 6, esbuild. Mirror the sibling
  project `cockpit-samba-dc` for layout, build, i18n, test setup and code
  style (4-space indent, `.ts` import extensions, comments only where the
  reason is not obvious).
- Backend: Python 3.12 or later (AlmaLinux 10 has 3.12), standard library only
  at runtime (it ships in a `.deb` and an `.rpm` that depend on `ansible-core`). Tests use `unittest`, so they run with no extra
  package. Lint with `ruff check backend collection`, at the version CI pins.
- Controller targets: Debian 13 (systemd 257, Cockpit 337), Fedora 44 and
  AlmaLinux 10 (ansible-core 2.16, Python 3.12, SELinux enforcing). Debian is
  the main one. Anything involving Ansible, systemd or packaging must be
  verified with the package installed, not on the development host: with
  `make ct-test` on the three after a change to the unit, the helpers,
  `fleetctl`, sudoers or packaging (AlmaLinux above all, the only one with
  ansible-core 2.16, the minimum), and on the test VMs at the close of a
  phase, for what a container cannot show (see "Test environment").
- User-visible strings are English in source, translated in `po/it.po`.
- Code, comments, commit messages and docs (`TODO.md` included) in English;
  conversation with the user in Italian.
- Commits: short imperative subject, as in the sibling project's history.

## Adding things

- **A backend command**: a function with `@command("area.verb", role=...,
  mutating=..., redact=(...), params={...})` in `backend/cockpit_fleet/`, in a
  module that `cli.main` imports. Declare every parameter (`Str`, `Int`,
  `Bool`, `Id`, `Strs`, `Objs`, `Obj`, `Choice` from `cli.py`), read them from
  the `Values` the handler gets, and give the handler a docstring: with the
  parameters it is what an agent sees of the command as a tool (`tool=False`
  keeps it from agents) and what `fleet help` shows of it at a terminal. Raise the errors in `errors.py`; their codes are part of the
  interface, so add but never rename. Test it in `backend/tests/test_cli.py` style.
- **A schema change**: append an entry to `MIGRATIONS` in `db.py`. Never edit
  an entry that has been released.
- **A frontend call**: `call<T>("area.verb", params)` from `src/lib/fleet.ts`.
  Hide what a role cannot do using `session.commands`, but never rely on it.
- **A job kind**: `fleet-run` dispatches on `spec["kind"]`; build the command
  line in `runner.py` (tested), prepare the run directory in the command that
  queues it, and handle the result in `jobs._collect`.
- **A page**: a component under `src/components/`, a tab in `TABS` in
  `src/app.tsx`. Use `useLoad`, `ListState`, `FormModal` and `ConfirmDelete`
  from `common.tsx`, and `useCan()` to decide which actions to offer. Take
  `Alert`, `Modal`, `Spinner`, `Tabs`, `ActionsColumn` and the other
  components `src/components/pf.tsx` wraps from there, not from PatternFly:
  they carry translated text where PatternFly has English of its own.
- **A string**: English in the source through `t()`, Italian in `po/it.po`, then
  `make pot && make po-compile`. Plurals use a symbolic key (`things_count`)
  whose English forms are edited by hand in `src/locales/en/translation.json`.

To drive the interface from the browser tools, the page is an iframe named
`cockpit1:localhost/fleet`; React inputs need the native value setter followed
by an `input` event. After a redeploy, reload the page ignoring the cache: a
change of the URL fragment does not reload the bundle.

To look at a page as a given role, create a throwaway account on the
controller in the matching group, log in to Cockpit with it, and delete it
afterwards.

## Commands

```
make build | watch              # frontend bundle, watch mode
make check                      # frontend (node --test) and backend (unittest) tests
make deb                        # .deb built in a Debian 13 container (podman), into deb/
make rpm                        # .rpm for Fedora 44 and AlmaLinux 10, in containers, into rpm/
make ct-test DISTRO=alma TESTS="sandbox runs"   # integration scripts in containers (all by default)
make ct-lifecycle DISTRO=alma OLD=<ref>   # upgrade from OLD (default: latest tag), removal, purge
make ct-clean                   # remove the containers and images of ct-test
```

Integration tests run as root on a controller, with the package installed:

```
test/integration/privileges.sh       # what the service account can reach
test/integration/sandbox.sh          # run sandbox, secrets, SSH (IT_SSH_USER, IT_SSH_PASSWORD)
test/integration/roles.sh            # roles, audit, sudoers (creates and removes test accounts)
test/integration/negative.sh         # hostile input to every command as each role, the caller's environment, secrets typed in clear; WIPES
test/integration/runs.sh             # a whole run through fleetctl; WIPES the database (IT_WIPE=yes)
test/integration/files.sh            # a project written in the interface, syntax checks; WIPES
test/integration/upload.sh           # a project uploaded as an archive, hostile archives; WIPES
test/integration/galaxy.sh           # collections from a requirements file (IT_GALAXY_NET=yes for git); WIPES
test/integration/adhoc.sh            # ad-hoc commands, the allowlist of modules (IT_SSH_USER, IT_SSH_PASSWORD); WIPES
test/integration/inventories.sh      # inventory plugins, directories of sources, scripts off and on; WIPES
test/integration/mcp.sh              # fleet-mcp: tools by role, ceiling, via in the audit, forced-command key; WIPES
test/integration/chat.sh             # fleet-chat with fake-agent.py: start, limit on sessions, tools by role; WIPES
test/integration/cli.sh              # fleet at a terminal: setup, launch, exit statuses, roles, secrets on stdin; WIPES
test/integration/workflows.sh        # workflows: an approval at a step, set_stats passed on, resume, cancel; WIPES
test/integration/schedules.sh        # the timer, ten schedules on one minute, missed times, overlap (about 5 min); WIPES
test/integration/notify.sh           # notifications to stand-ins of the four channels, a retry by the tick (about 3 min); WIPES
test/integration/checks.sh           # checks on container nodes, a playbook of the user, a full disk told about, a cycle falling behind (about 10 min); WIPES
test/integration/cmdb.sh             # the CMDB, updates and drift of container nodes, changes by hand (about 4 min); WIPES
test/integration/measure-events.sh   # callback cost on N simulated hosts
test/integration/load.sh             # real check cycles on IT_LOAD_HOSTS aliases and IT_LOAD_SSH nodes for IT_LOAD_MINUTES, the interface timed; WIPES
test/integration/multihost.sh        # five container nodes: vaults, passphrase key, key changes; WIPES
test/integration/windows.sh          # Windows node over SSH, a run and the checks (IT_WIN_HOST, IT_WIN_USER, IT_WIN_KEY); WIPES
test/integration/nodes.sh            # the container nodes themselves: up N KEYS | recreate I KEYS | down
test/integration/lifecycle.sh        # upgrade, removal, purge (IT_PKG_OLD, IT_PKG_NEW; no package installed): scripts/ct-lifecycle.sh
```

`make ct-test` is the usual way to run them: it builds the package of
`DISTRO` (`debian`, `fedora` or `alma`), then runs each script as root in a
fresh container of that distribution (`test/containers/`), `CT_JOBS` at a
time (2 by default), with `IT_WIPE=yes` and an account for `IT_SSH_USER`.
Other `IT_*` variables of the caller are passed on; the logs go to
`it-results/DISTRO/`. `windows.sh`, `measure-events.sh` and `load.sh` run
only when named; `lifecycle.sh` only through `make ct-lifecycle`, which
builds an older package too (`CT_KEEP_PACKAGES=DIR` keeps both, to run it
on a VM). On a VM of the lab, `test/vm/lab.sh deploy TARGET` installs
the package and `test/vm/lab.sh it TARGET SCRIPT...` runs scripts the same
way, logs in `it-results/vm-TARGET/`. On another machine, copy `test/integration` there and start one as root,
e.g. `sudo env IT_WIPE=yes IT_SSH_USER=USER IT_SSH_PASSWORD=... sh
integration/runs.sh`, where `USER` is an account of the controller that
accepts password logins over SSH. To start one with `systemd-run` (to outlive
the session) on Fedora or AlmaLinux, pass `-p
SELinuxContext=unconfined_u:unconfined_r:unconfined_t:s0` and redirect its
output in the shell, not with `StandardOutput=`: a unit runs in `initrc_t`,
where `ssh-keygen` cannot write in `/run`. The tests that go through
`fleetctl` share helpers in `lib.sh` (`admin`, `get`, `expect_run`, `wipe`, `prepare`). A
failed `expect_run` prints the end of the run's output. With `IT_KEEP=yes`,
`windows.sh` leaves its data in place so the same run can be launched from the
interface; so does `checks.sh`, whose stand-in for the webhook then stays up
(`pkill -f notify-sinks.py` as root).

## Test environment

- `make ct-test` needs rootless podman on cgroup v2, nothing else of the
  host. Its containers are privileged within the user's namespace and make
  `/` a shared mount at boot (`test/containers/make-rshared.service`): the
  sandbox of a run cannot start otherwise, and without the shared mount a
  run gets no credentials, with no error. They run in UTC.
- What a container cannot show, and so needs a machine of the distribution:
  SELinux (Fedora and AlmaLinux are enforcing; a container has none of its
  own); that the sandbox of a run holds on its own kernel, since a container
  shares the host's and, unprivileged, ignores `PrivateIPC` with only a
  warning: confirm a change to the unit with `sandbox.sh` on a machine (rule
  8); the interface in a browser; Windows nodes. Container nodes also show
  the memory and processor of the host, which is why `checks.sh` turns off
  the thresholds of those two checks.
- The machines are the VMs of the lab, dedicated to this project:
  `cockpit-fleet-deb13`, `cockpit-fleet-fed44` and `cockpit-fleet-alma10`
  (controllers) and `cockpit-fleet-win11` (a Windows node), on a libvirt
  network of their own. `test/vm/` builds them from scratch (cloud images
  with cloud-init; Windows Setup with an answer file, from the Evaluation
  ISO) and drives them; `test/vm/lab.conf` says what they are, the same for
  everyone, and `.env` (from `.env.example`) holds what belongs to one
  workstation: the password and the path of the ISO. Load the skill
  `vm-lab` before using them. An agent may build, rebuild, revert and
  destroy the VMs named `cockpit-fleet-*` without asking; any other VM on
  the host belongs to something else: do not touch it without the user's
  agreement. Before starting VMs, check that the host can give what they
  need (`lab.sh resources`), and shut down what you started.
- A controller is a machine of one of the target distributions with
  `cockpit`, the package, `podman`, `sshpass` and `acl` (for `cli.sh`); `nodes.sh` needs podman
  there and starts Linux nodes on its default network, at 10.88.0.101
  onwards. Windows nodes are reached over SSH with a key: authorise a key
  pair made for the test on the node and store its private half as a
  credential, or as the secret `it-win-key` that `windows.sh` expects.
- Install the `.deb` with `apt-get install ./cockpit-fleet_*.deb`, the `.rpm`
  with `dnf install ./cockpit-fleet-*.noarch.rpm`; `dnf install` of a `.rpm`
  of the same version does nothing: use `dnf reinstall`.
- An install stops at dpkg's question when `/etc/cockpit-fleet/config.toml`
  was changed on the controller and the packaged one changed too: put the
  packaged file back first, or install with `--force-confold` to keep the
  test configuration.
- After a deploy, a Cockpit tab keeps running the old bundle until reloaded,
  and a package in `/usr/share/cockpit` is shadowed by a copy in
  `~/.local/share/cockpit` (what `make devel-install` leaves).
- Cockpit has a self-signed certificate, which the browser tools refuse.
  Open an SSH tunnel to the controller (`ssh -L 9091:127.0.0.1:9090`) and use
  `http://127.0.0.1:9091`: Cockpit accepts plain HTTP from localhost.
- Do not `pkill -f` or `pgrep -f` with a pattern that also appears in your own
  command line: it matches the shell that runs it.

## Repository

- The repository will be published on GitHub once the project is stable. Treat
  every commit as future public history: no secrets, no passwords, no details of
  real (non-test) environments or of a developer's machines, in files or in
  commit messages.
- License: `LGPL-2.1-or-later`, same as `cockpit-samba-dc` (provisional).
- CI: `.gitea/workflows/ci.yml`, on runners labelled `cockpit-fleet`
  (Debian 13: tests, the `.deb`), `cockpit-fleet-fedora` and
  `cockpit-fleet-alma` (the `.rpm` with rpmlint): arm64 containers where
  jobs run directly, as a non-root user. Tools with a version (Node.js,
  ruff) are pinned in the workflow and installed by `scripts/ci-tool.sh`.
- Never commit, push or create remote repositories unless asked.

## Reference material

- `cockpit-samba-dc`: frontend patterns (`src/lib/exec.ts` for spawn wrappers,
  `test/mocks/cockpit.ts` for the cockpit mock).
- `ansible-win_edulab`: a real Windows project that must run unmodified from
  this plugin (acceptance test of Phase 3).
