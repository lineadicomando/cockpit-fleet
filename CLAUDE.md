# CLAUDE.md

Agent instructions for this project.

## What this is

`cockpit-fleet` is a Cockpit plugin that drives Ansible
from the browser, inspired by Semaphore UI, and that also collects, stores and
notifies on information from managed nodes using Ansible only (agentless).

Documents of the project, each with one job; do not repeat one in another:

- `TODO.md`: status, the phase under way and what is left to do. Read it
  first. Tick items as they are completed, and keep it short: what is done
  moves out, to the places below.
- `docs/decisions.md`: what was decided with the user, one row per topic.
  Record a decision there when it is taken or changes (the detail of the
  phase under way stays in `TODO.md` until the phase closes).
- `docs/limits.md`: known limits left as they are, by area. Add one when a
  change leaves something undone, remove it when it is done.
- `docs/architecture/*.md`, one file per area, listed in `docs/architecture.md`:
  what exists, why it is built that way, and what was measured. Update the
  file of the area with every change of design (and the index when a file
  is added); read only the one at hand.
- `CHANGELOG.md`: what changed in each release, for its users. Add a line
  under `[Unreleased]` with every change a user or an administrator would
  notice, in the commit that makes it, under the heading of its kind (see
  "Versions and releases").
- This file: how to work on the project.

`docs/install.md`, `docs/security.md`, `docs/checks.md`, `docs/backup.md`
and `docs/chat.md` are for the administrator of a controller: installing and
giving access, what is protected and what is not, writing checks, backups,
turning the chat on. Keep them in step with what they describe (roles,
`config.toml`, the catalog, `fleet_result`, the files a controller keeps,
`[chat]`) when that changes.

`CLAUDE.local.md` (and other `*.local.md`), `Makefile.local` and `.env`,
when present, are not tracked: they describe one developer's own workstation (the state of its test
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

These come from the security model in `docs/architecture/overview.md`; a change that breaks one of
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
  keeps it from agents) and what `fleet help` shows of it at a terminal.
  Give it `agent=` when its class for agents is not the default (ad-hoc,
  files, projects, credentials, deletes: see `cli.AGENT_CLASSES`), and add
  it to `CLASS_TABLE` in `test_agents.py`. Raise the errors in `errors.py`; their codes are part of the
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

## Commands

```
make build | watch              # frontend bundle, watch mode
make check                      # frontend (node --test) and backend (unittest) tests
make deb                        # .deb built in a Debian 13 container (podman), into deb/
make rpm                        # .rpm for Fedora 44 and AlmaLinux 10, in containers, into rpm/
make ct-test DISTRO=alma TESTS="sandbox runs"   # integration scripts in containers (all by default)
make ct-lifecycle DISTRO=alma OLD=<ref>   # upgrade from OLD (default: latest tag), removal, purge
make ct-clean                   # remove the containers and images of ct-test
make next-version               # version of the next release, from CHANGELOG.md
make release                    # check, set the version, commit and tag it (only when asked)
```

Integration tests are the scripts in `test/integration/`, run as root on
a controller with the package installed. What each shows, the variables
it takes, whether it wipes, and how to run them in containers, on the lab
or on another machine: `docs/architecture/tests.md`.

## Test environment

- What a container cannot show, and so needs a machine of the distribution:
  SELinux (Fedora and AlmaLinux are enforcing); that the sandbox of a run
  holds on its own kernel: confirm a change to the unit with `sandbox.sh`
  on a machine (rule 8); the interface in a browser; Windows nodes.
- The machines are the lab's VMs (`cockpit-fleet-deb13`, `-fed44`,
  `-alma10`, `-win11`), built and driven by `test/vm/`. Load the skill
  `vm-lab` before using them. An agent may build, rebuild, revert and
  destroy the VMs named `cockpit-fleet-*` without asking; any other VM on
  the host belongs to something else: do not touch it without the user's
  agreement. Check `lab.sh resources` before starting VMs, and shut down
  what you started.
- Cockpit has a self-signed certificate, which the browser tools refuse:
  tunnel (`lab.sh tunnel`, or `ssh -L 9091:127.0.0.1:9090`) and use
  `http://127.0.0.1:9091`; logging in and driving the page: skill
  `vm-lab`, "The interface in the browser tools".
- Do not `pkill -f` or `pgrep -f` with a pattern that also appears in your own
  command line: it matches the shell that runs it.

## Versions and releases

- Semantic Versioning; a release is a tag `vX.Y.Z` on `main`. Before
  1.0.0 the minor number goes up for features and for breaking changes
  (anything that asks the administrator to act on upgrade: a key of
  `config.toml` or a kept file changes meaning, a requirement is raised, a
  command of `fleetctl` or `fleet` changes or goes, a feature is removed),
  the patch number for fixes only; from 1.0.0 breaking changes raise the
  major number.
- Under `[Unreleased]`: `Added`, `Changed`, `Deprecated`, `Removed`,
  `Breaking` raise the minor number, `Fixed` and `Security` alone the
  patch. Tests, CI or the lab alone go in no entry.
- The version is never set by hand. A release is made only when the user
  asks, with `make release` on a clean `main`; the procedure, the files
  that carry the version and how to check the published release:
  `docs/architecture/packaging.md`, "Releases".

## Repository

- The repository is public on GitHub (`github.com/lineadicomando/cockpit-fleet`),
  with the same history on the private Gitea where CI also runs. Every commit
  is public history: no secrets, no passwords, no details of real (non-test)
  environments or of a developer's machines, in files or in commit messages.
- License: `LGPL-2.1-or-later`, same as `cockpit-samba-dc`.
- CI: `.gitea/workflows/ci.yml` (Gitea) and `.github/workflows/ci.yml`
  (GitHub) run the same steps: keep the two in step; see
  `docs/architecture/tests.md`, "CI".
- Never commit, push or create remote repositories unless asked.

## Reference material

- `cockpit-samba-dc`: frontend patterns (`src/lib/exec.ts` for spawn wrappers,
  `test/mocks/cockpit.ts` for the cockpit mock).
- `ansible-win_edulab`: a real Windows project that must run unmodified from
  this plugin (acceptance test of Phase 3).
