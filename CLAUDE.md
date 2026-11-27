# CLAUDE.md

`cockpit-fleet`: a Cockpit plugin that drives Ansible from the browser
(after Semaphore UI) and collects, stores and notifies on information from
managed nodes through Ansible alone (agentless).

## Documents: one job each, never repeated in another

| File | Job | Read |
|---|---|---|
| `TODO.md` | status, the phase under way, what is left; done items move out to the files below | first, at the start of a session |
| `docs/architecture/*.md` (index `docs/architecture.md`) | per area: what exists, why, the figures that matter; `acceptance.md` how each closed phase was shown | only the area at hand; update it with every change of design |
| `docs/decisions.md` | what was decided with the user, one row per topic, dated | search for the topic |
| `docs/limits.md` | known limits left as they are, by area; add when a change leaves something undone, remove when done | search for the topic |
| `docs/history/*.md` | archive per area: trials, measurements, how things were found | only when asked, or to know why a figure is what it is |
| `CHANGELOG.md` | for users: a line under `[Unreleased]`, in the commit that makes the change | the headings of `[Unreleased]` when adding a line; never whole |
| `docs/install.md`, `security.md`, `checks.md`, `backup.md`, `chat.md` | for the administrator of a controller: keep them in step with roles, `config.toml`, the catalog, `fleet_result`, the files kept, `[chat]` | when that changes |

An architecture document says how things are now; how they came to be
goes to `docs/history/`, a decision to `decisions.md`, a phase's proof to
`acceptance.md`.

`CLAUDE.local.md` (and other `*.local.md`), `Makefile.local`, `.env`: not
tracked, one developer's workstation (VMs, passwords, paths, hosting).
Nothing of them goes into tracked files, which must make sense to anyone
who clones the repository.

Layout: `src/` frontend; `backend/cockpit_fleet/` the Python package (each
module's docstring says what it is; `cli.py` the registry of commands,
`db.py` the schema), `backend/libexec/` helpers, `backend/bin/fleet`,
`backend/tests/`; `collection/` the bundled Ansible collection; `system/`
units, sudoers, sysusers, tmpfiles, default config; `debian/`,
`packaging/`; `starters/`; `test/` frontend tests, `test/integration/`
scripts for a controller, `test/containers/` and `test/vm/` where they
run, `test/browser/` scenarios in a browser; `po/`.

## Non-negotiable rules

From the security model (`docs/architecture/overview.md`); breaking one
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
   root. No third root entry point, no sudoers wildcard, no polkit rule that
   matches unit names (it also authorises transient units of that name:
   arbitrary code as root).
7. **Validate on the backend.** UI validation is a convenience, not a control.
8. **Runs share one Unix account, so they must stay isolated from each other.**
   Anything a run can reach, a playbook of another project can reach. Do not
   remove `PrivatePIDs`, the tmpfs over the state directory or over `/run`;
   test a change to the unit or to `fleet-run` with `sandbox.sh`.
9. **Nothing executable in a run's scratch directory** (mounted `noexec`).
   A helper a run must execute is a packaged file in `/usr/libexec/cockpit-fleet`
   that takes paths from its environment, like `fleet-askpass`.
10. **Checks are OS agnostic.** A catalog check has one output schema and one
   implementation per platform; Windows nodes are reached over SSH, not WinRM.

## Stack and conventions

- Frontend: TypeScript, React 18, PatternFly 6, esbuild; 4-space indent,
  `.ts` import extensions, comments only where the reason is not obvious.
- Backend: Python 3.12+, standard library only at runtime (packages depend
  on `ansible-core` alone); `unittest`; lint `ruff check backend collection`
  at the version CI pins (`RUFF_VERSION`).
- Controllers: Debian 13 (the main one), Fedora 44, AlmaLinux 10
  (ansible-core 2.16, the minimum; Python 3.12; SELinux enforcing, as on
  Fedora). Whatever touches Ansible, systemd or packaging is verified with
  the package installed, never on the development host: `make ct-test` on
  the three after a change to the unit, the helpers, `fleetctl`, sudoers
  or packaging (AlmaLinux above all); the lab's VMs at the close of a phase.
- Code, comments, commit messages and docs in English; user-visible strings
  English in source, Italian in `po/it.po`; the conversation with the user
  in Italian. Commits: short imperative subject.

## Adding things

- **A backend command**: `@command("area.verb", role=..., mutating=...,
  redact=(...), params={...})` in a module that `cli.load_commands`
  imports; every parameter declared (`Str`, `Int`, `Bool`, `Id`, `Strs`,
  `Objs`, `Obj`, `Choice` of `cli.py`) and read from the `Values` the
  handler gets. Its docstring and parameters are what an agent sees of it
  as a tool (`tool=False` keeps it from agents) and what `fleet help`
  shows. `agent=` when its class is not the default (`cli.AGENT_CLASSES`;
  then `CLASS_TABLE` in `test_agents.py`); always the table of roles in
  `test_negative.py`, also `RIGHTS` for `right=` and `HOUSEKEEPING` for a
  read that writes the database. A file: `max_input=`, or of any size
  `stream=` (not a tool; as `store.put`, `store.read`). Raise the errors
  of `errors.py`: their codes are part of the interface (add, never
  rename). Test it as `backend/tests/test_cli.py` does.
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
  in `src/manifest.json`; `useLoad` (`lib/hooks.ts`), `ListState`,
  `FormModal`, `ConfirmDelete` (`common.tsx`), `FileManager.tsx` for a
  tree. What `src/components/pf.tsx` wraps (`Alert`, `Modal`, `Tabs`,
  `Pagination`, `ActionsColumn`…) comes from there, not from PatternFly:
  it carries translated text. Cockpit's policy refuses inline `<style>`
  and `style` attributes (React's `style` prop is fine). What it does for
  a role and sends the backend: `test/NAME.test.tsx`.
- **A string**: English through `t()`, Italian in `po/it.po`, then
  `make pot && make po-compile`. Plurals: a symbolic key (`things_count`)
  whose English forms are edited by hand in `src/locales/en/translation.json`.

## Commands

```
make help                       # every target
make build | watch              # frontend bundle, watch mode
make check | check-ts | check-py   # frontend (node --test) and backend (unittest) tests
make deb | rpm                  # packages, built in containers (podman), into deb/ or rpm/
make ct-test DISTRO=alma TESTS="sandbox runs"   # integration scripts in containers (all by default)
make ct-lifecycle DISTRO=alma OLD=<ref>   # upgrade from OLD (default: latest tag), removal, purge
make ct-clean                   # remove the containers and images of ct-test
make browser-up SETUP=checks    # a controller for the tests in a browser on 127.0.0.1:9093, with data
make browser-test SCENARIO=checks-cycles  # a scenario in a browser
make browser-down               # remove that controller, its nodes and its data
make next-version               # version of the next release, from CHANGELOG.md
make release                    # check, set the version, commit and tag it (only when asked)
```

Integration scripts (`test/integration/`) run as root on a controller with
the package installed; what each shows, its variables, whether it wipes:
`docs/architecture/tests.md`. A new check of a fix should fail without it.

## Test environment

- What needs a machine, not a container: SELinux; the sandbox on its own
  kernel (`sandbox.sh` after a change to the unit); Windows nodes.
- The machines are the lab's VMs (`cockpit-fleet-deb13`, `-fed44`,
  `-alma10`, `-win11`), driven by `test/vm/`: load the skill `vm-lab`
  first. An agent may build, rebuild, revert and destroy `cockpit-fleet-*`
  without asking; never another VM without the user's agreement.
  `lab.sh resources` before starting VMs; shut down what you started.
- The interface in a browser: a scenario of `test/browser/`
  (`docs/architecture/browser-tests.md`), in a container or on a VM; a
  throwaway one that saves screenshots and `ariaSnapshot()` explores a
  page (a click may launch a real run: a workflow's "Launch" starts at
  once). The browser tools: skill `vm-lab`.
- Never `pkill -f` or `pgrep -f` with a pattern that is also in your own
  command line: it matches the shell that runs it.

## Versions and releases

- Semantic Versioning, a release a tag `vX.Y.Z` on `main`. Before 1.0.0
  the minor number goes up for features and breaking changes (anything
  that asks the administrator to act on upgrade: a key of `config.toml` or
  a kept file changes meaning, a requirement is raised, a command of
  `fleetctl` or `fleet` changes or goes, a feature is removed), the patch
  for fixes only; from 1.0.0 breaking changes raise the major.
- Under `[Unreleased]`: `Added`, `Changed`, `Deprecated`, `Removed`,
  `Breaking` raise the minor number, `Fixed` and `Security` alone the
  patch. Tests, CI or the lab alone go in no entry.
- The version is never set by hand; a release only when the user asks
  (`make release`, `docs/architecture/packaging.md`).

## Repository

- Public on GitHub (`github.com/lineadicomando/cockpit-fleet`), mirrored
  from a private Gitea where CI also runs. Every commit is public: no
  secrets, passwords, details of real (non-test) environments or of a
  developer's machines, in files or commit messages; nothing that is not
  the project itself.
- License `LGPL-2.1-or-later`. CI: `.gitea/workflows/ci.yml` and
  `.github/workflows/ci.yml` run the same steps, actions pinned by commit:
  keep them in step (`docs/architecture/tests.md`, "CI").
- Never commit, push or create remote repositories unless asked.
- `ansible-win_edulab`, a sibling repository: a real Windows project that
  must run unmodified from this plugin.
