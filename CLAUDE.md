# CLAUDE.md

Agent instructions for this project.

## What this is

`cockpit-fleet` is a Cockpit plugin that drives Ansible from the browser,
inspired by Semaphore UI, and that also collects, stores and notifies on
information from managed nodes using Ansible only (agentless).

Documents of the project, each with one job; do not repeat one in another:

- `TODO.md`: status, the phase under way, what is left. Read it first.
  Tick items as they are done and keep it short: what is done moves out,
  to the places below.
- `docs/decisions.md`: what was decided with the user, one row per topic,
  recorded when taken or changed (the detail of the phase under way stays
  in `TODO.md` until it closes).
- `docs/limits.md`: known limits left as they are, by area: add one when a
  change leaves something undone, remove it when it is done.
- `docs/architecture/*.md`, one file per area, listed in
  `docs/architecture.md`: what exists, why, what was measured. Update the
  file of the area with every change of design (the index when a file is
  added); read only the one at hand. `acceptance.md` records how each
  closed phase was shown.
- `CHANGELOG.md`: for users. A line under `[Unreleased]`, under the heading
  of its kind ("Versions and releases"), for every change a user or an
  administrator would notice, in the commit that makes it.
- This file: how to work on the project.

To keep the context small: at the start of a session read `TODO.md`
alone; then only the architecture file of the area at hand; of
`CHANGELOG.md` only `[Unreleased]` (the top); search `decisions.md` and
`limits.md` for the topic rather than reading them whole.

`docs/install.md`, `security.md`, `checks.md`, `backup.md` and `chat.md`
are for the administrator of a controller; keep them in step with what they
describe (roles, `config.toml`, the catalog, `fleet_result`, the files a
controller keeps, `[chat]`).

`CLAUDE.local.md` (and other `*.local.md`), `Makefile.local` and `.env`, when
present, are not tracked: one developer's workstation (test VMs, passwords,
paths, hosting, deploy targets). Nothing of them goes into tracked files,
which must make sense to anyone who clones the repository.

Layout: `src/` frontend; `backend/cockpit_fleet/` Python package,
`backend/libexec/` helpers installed in `/usr/libexec/cockpit-fleet`,
`backend/bin/fleet` (`/usr/bin`), `backend/tests/` unit tests;
`collection/` the bundled Ansible collection (the runs' callback, roles an
administrator runs on the controller); `system/` units, sudoers, sysusers,
tmpfiles, default config; `debian/`, `packaging/` (RPM spec); `test/`
frontend tests, `test/integration/` scripts for a controller,
`test/containers/` and `test/vm/` the controllers they run on;
`.claude/skills/` agent skills; `po/` translations.

## Non-negotiable rules

From the security model in `docs/architecture/overview.md`; breaking one
needs the user's explicit agreement.

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

- Frontend: TypeScript, React 18, PatternFly 6, esbuild; layout, build,
  i18n, tests and style as the sibling `cockpit-samba-dc` (4-space indent,
  `.ts` import extensions, comments only where the reason is not obvious).
- Backend: Python 3.12+ (AlmaLinux 10 has 3.12), standard library only at
  runtime (packages depend on `ansible-core` alone); tests with `unittest`;
  lint `ruff check backend collection` at the version CI pins.
- Controllers: Debian 13 (the main one; systemd 257, Cockpit 337, Python
  3.13), Fedora 44, AlmaLinux 10 (ansible-core 2.16, the minimum; Python
  3.12; SELinux enforcing, as Fedora). Whatever touches Ansible, systemd or
  packaging is verified with the package installed, never on the
  development host: `make ct-test` on the three after a change to the unit,
  the helpers, `fleetctl`, sudoers or packaging (AlmaLinux above all), and
  the test VMs at the close of a phase ("Test environment").
- User-visible strings English in source, Italian in `po/it.po`.
- Code, comments, commit messages, docs (`TODO.md` too) in English; the
  conversation with the user in Italian.
- Commits: short imperative subject.

## Adding things

- **A backend command**: `@command("area.verb", role=..., mutating=...,
  redact=(...), params={...})` in a module that `cli.load_commands`
  imports. Declare every parameter (`Str`, `Int`, `Bool`, `Id`, `Strs`,
  `Objs`, `Obj`, `Choice` of `cli.py`) and read them from the `Values` the
  handler gets. Its docstring and parameters are what an agent sees of it
  as a tool (`tool=False` keeps it from agents) and what `fleet help`
  shows. Give it `agent=` when its class is not the default (see
  `cli.AGENT_CLASSES`) and add it to `CLASS_TABLE` in `test_agents.py`; add
  it to the table of roles in `test_negative.py`. A file of any size is
  streamed (`stream=`, as `store.put` and `store.read`). Raise the errors of
  `errors.py`: their codes are part of the interface (add, never rename).
  Test it as `backend/tests/test_cli.py` does.
- **A schema change**: append to `MIGRATIONS` in `db.py`; never edit a
  released entry.
- **A frontend call**: `call<T>("area.verb", params)` from `src/lib/fleet.ts`.
  Hide what a role cannot do with `useCan()` (`lib/session.tsx`), never
  relying on it.
- **A job kind**: `fleet-run` dispatches on `spec["kind"]`; the command
  line in `runner.py` (tested), the run directory prepared by the command
  that queues it, the result handled in `jobs._collect`.
- **A page**: a component under `src/components/`, its section in `GROUPS`
  of `src/lib/sections.ts`, its title and route in `src/app.tsx`, a keyword
  in `src/manifest.json`. `useLoad` (`lib/hooks.ts`), `ListState`,
  `FormModal`, `ConfirmDelete` (`common.tsx`); a tree of files is
  `FileManager.tsx`. Take `Alert`, `Modal`, `Spinner`, `Tabs`,
  `ActionsColumn` and the rest `src/components/pf.tsx` wraps from there,
  not from PatternFly: they carry translated text. Cockpit's policy
  refuses inline `<style>` and `style` attributes (React's `style` prop is
  fine). What it does for a role, and what it sends the backend, tested in
  `test/NAME.test.tsx` (`docs/architecture/tests.md`).
- **A string**: English through `t()`, Italian in `po/it.po`, then
  `make pot && make po-compile`. Plurals: a symbolic key (`things_count`)
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

The integration scripts (`test/integration/`) run as root on a controller
with the package installed; what each shows, its variables, whether it
wipes, and where to run it: `docs/architecture/tests.md`.

## Test environment

- What needs a machine, not a container: SELinux; the sandbox of a run on
  its own kernel (`sandbox.sh` after a change to the unit, rule 8); the
  interface in a browser; Windows nodes.
- The machines are the lab's VMs (`cockpit-fleet-deb13`, `-fed44`,
  `-alma10`, `-win11`), driven by `test/vm/`: load the skill `vm-lab`
  first. An agent may build, rebuild, revert and destroy `cockpit-fleet-*`
  without asking; never touch another VM without the user's agreement.
  `lab.sh resources` before starting VMs; shut down what you started.
- The browser tools refuse Cockpit's self-signed certificate: tunnel
  (`lab.sh tunnel`) to `http://127.0.0.1:9091`; logging in and driving the
  page: skill `vm-lab`, "The interface in the browser tools".
- Never `pkill -f` or `pgrep -f` with a pattern that is also in your own
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
- The version is never set by hand. A release only when the user asks,
  with `make release` on a clean `main`; procedure, files that carry the
  version, checking the published release: `docs/architecture/packaging.md`,
  "Releases".

## Repository

- Public on GitHub (`github.com/lineadicomando/cockpit-fleet`), the same
  history on the private Gitea where CI also runs. Every commit is public:
  no secrets, passwords, details of real (non-test) environments or of a
  developer's machines, in files or commit messages.
- License: `LGPL-2.1-or-later`, as `cockpit-samba-dc`.
- CI: `.gitea/workflows/ci.yml` and `.github/workflows/ci.yml` run the same
  steps: keep them in step (`docs/architecture/tests.md`, "CI").
- Never commit, push or create remote repositories unless asked.

## Reference material

- `cockpit-samba-dc`: frontend patterns (`src/lib/exec.ts` for spawn
  wrappers, `test/mocks/cockpit.ts` for the cockpit mock).
- `ansible-win_edulab`: a real Windows project that must run unmodified
  from this plugin (acceptance test of Phase 3).
