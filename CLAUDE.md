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

Layout: `src/` frontend, `backend/cockpit_fleet/` Python package,
`backend/libexec/` the helpers installed in `/usr/libexec/cockpit-fleet`,
`backend/tests/` unit tests, `collection/` the bundled Ansible collection,
`system/` units, sudoers, sysusers, tmpfiles and the default config,
`debian/` packaging, `test/` frontend tests, `test/integration/` scripts for
the VM, `po/` translations.

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

- Frontend: TypeScript, React 18, PatternFly 6, esbuild. Mirror
  `../cockpit-samba-dc` for layout, build, i18n, test
  setup and code style (4-space indent, `.ts` import extensions, comments only
  where the reason is not obvious).
- Backend: Python 3.13, standard library only at runtime (it ships in a `.deb` that
  depends on `ansible-core`). Tests use `unittest`, so they run with no extra
  package. `ruff` is not installed on the host: run it in a container, as CI does
  (`pip install ruff && ruff check backend collection`).
- Controller target: Debian 13 only (systemd 257, Cockpit 337). The development
  host is Fedora with ansible-core 2.20, newer than Debian's: anything involving
  Ansible, systemd or packaging must be verified on the VM, not locally.
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
  keeps it from agents). Raise the errors in `errors.py`; their codes are part of the
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
  from `common.tsx`, and `useCan()` to decide which actions to offer.
- **A string**: English in the source through `t()`, Italian in `po/it.po`, then
  `make pot && make po-compile`. Plurals use a symbolic key (`things_count`)
  whose English forms are edited by hand in `src/locales/en/translation.json`.

To drive the interface from the browser tools, the page is an iframe named
`cockpit1:localhost/fleet`; React inputs need the native value setter followed
by an `input` event. After a redeploy, reload the page ignoring the cache: a
change of the URL fragment does not reload the bundle.

To look at a page as a given role, create a throwaway account on the VM in the
matching group, log in to Cockpit with it, and delete it afterwards.

## Commands

```
make build | watch              # frontend bundle, watch mode
make check                      # frontend (node --test) and backend (unittest) tests
make deb                        # .deb built in a Debian 13 container (podman), into deb/
make vm-deploy                  # frontend only, into ~maint/.local/share/cockpit
make vm-deb-deploy              # full package on the VM; needs SSHPASS exported
```

`Makefile.local` holds the VM targets and is not tracked. Prefer
`vm-deb-deploy`: a copy left by `vm-deploy` in `~/.local/share/cockpit` shadows
the packaged one.

Integration tests run as root on the VM, with the package installed:

```
test/integration/privileges.sh       # what the service account can reach
test/integration/sandbox.sh          # run sandbox, secrets, SSH (IT_SSH_USER, IT_SSH_PASSWORD)
test/integration/roles.sh            # roles, audit, sudoers (creates and removes test accounts)
test/integration/runs.sh             # a whole run through fleetctl; WIPES the database (IT_WIPE=yes)
test/integration/files.sh            # a project written in the interface, syntax checks; WIPES
test/integration/upload.sh           # a project uploaded as an archive, hostile archives; WIPES
test/integration/galaxy.sh           # collections from a requirements file (IT_GALAXY_NET=yes for git); WIPES
test/integration/adhoc.sh            # ad-hoc commands, the allowlist of modules (IT_SSH_USER, IT_SSH_PASSWORD); WIPES
test/integration/inventories.sh      # inventory plugins, directories of sources, scripts off and on; WIPES
test/integration/mcp.sh              # fleet-mcp: tools by role, ceiling, via in the audit, forced-command key; WIPES
test/integration/chat.sh             # fleet-chat with fake-agent.py: start, limit on sessions, tools by role; WIPES
test/integration/measure-events.sh   # callback cost on N simulated hosts
test/integration/multihost.sh        # five container nodes: vaults, passphrase key, key changes; WIPES
test/integration/windows.sh          # Windows node over SSH (IT_WIN_HOST, IT_WIN_USER, IT_WIN_KEY); WIPES
test/integration/nodes.sh            # the container nodes themselves: up N KEYS | recreate I KEYS | down
```

Run these after any change to the unit, the helpers, `fleetctl` or sudoers. The
tests that go through `fleetctl` share helpers in `lib.sh` (`admin`, `get`,
`expect_run`, `wipe`, `prepare`). A failed `expect_run` prints the end of the
run's output. With `IT_KEEP=yes`, `windows.sh` leaves its data in place so the
same run can be launched from the interface.

## Test environment

- Controller VM: libvirt `deb13-samba-dc`, `maint@VM`, Cockpit on
  `https://VM:9090`. Password-only SSH (`sshpass -e`).
- Its snapshot `baseline_fleet` is where a test starts from: the package and
  what the integration tests need, no data. Go back to it with
  `virsh -c qemu:///system snapshot-revert deb13-samba-dc baseline_fleet`, then
  `make vm-deb-deploy` to install the current code. The snapshots are external:
  reverting deletes whatever was on the VM since, with no way back.
- Windows nodes over SSH: `teacher` .10, `student01` .11, `student02` .12, from
  `../ansible-win_edulab/inventories/school`.
- Linux nodes: containers on the controller VM (podman is installed there;
  `test/integration/nodes.sh` starts them on 10.88.0.101 onwards), or a clone of
  `debian13-base`.
- VMs are usually shut off: `virsh -c qemu:///system list --all`, then start what
  is needed. Ask before creating, cloning or deleting a VM.
- Before starting a VM, check that the running VMs plus the new one do not need
  more than the host can give: compare the memory and vCPUs assigned to them
  (`virsh dominfo <domain>`) with what is free on the host (`free -m`, `nproc`).
  If they do not fit, start fewer VMs or shut down the ones not needed for the
  test, and shut down what you started when the test is over.
- After a deploy, a Cockpit tab keeps running the old bundle until reloaded, and a
  package in `/usr/share/cockpit` is shadowed by a copy in `~/.local/share/cockpit`.
- Despite its name the VM has no Samba AD DC for now: tests run without it.
- Cockpit there has a self-signed certificate, which the browser tools refuse.
  Open an SSH tunnel (`ssh -L 9091:127.0.0.1:9090`) and use
  `http://127.0.0.1:9091`: Cockpit accepts plain HTTP from localhost.
- Do not `pkill -f` with a pattern that also appears in your own command line.
- The controller has no standing key for the Windows nodes: make a key pair
  for the test, authorise its public half with
  `local/cockpit_fleet_ssh_key.yaml` in `ansible-win_edulab` from the host
  (pass the key as JSON: `-e '{"fleet_ssh_public_key": "ssh-ed25519 ..."}'`),
  and store the private half as a credential, or as the secret `it-win-key`
  that `windows.sh` expects.
- `ansible-win_edulab` is meant to be used from inside this plugin: read it
  freely and use it to act on the test VMs. Its own `CLAUDE.md` applies there
  (one-off playbooks go in its `local/`, which is not tracked). `win11-pc00` is `teacher` (.10),
  `win11-pc01` is `student01` (.11), `win11-pc02` is `student02` (.12); each has
  4 GB assigned: check what is free before starting more than one.

## Git hosting

- A private Gitea. CLI: `tea`, login `example`, which
  is not the default: always pass `-l LOGIN`.
- `tea` fails outside a git work tree (`git rev-parse` error): run it from inside
  the repository.
- Remotes use HTTPS (credentials come from the git credential store). Gitea SSH
  listens on port 2221 and is not set up on this host (no known host key).
- Repository: a private Gitea, remote `origin`.
- The repository will be published on GitHub once the project is stable. Treat
  every commit as future public history: no secrets, no passwords, no details of
  real (non-test) environments, in files or in commit messages.
- License: `LGPL-2.1-or-later`, same as `cockpit-samba-dc` (provisional).
- CI: `.gitea/workflows/ci.yml`, on the runner labelled `cockpit-fleet`
  (container `gitea-runner` on the CI host, defined in
  `/srv/gitea/compose.yaml` and `/srv/gitea/runner-ci/`). It is a
  Debian 13 arm64 container where jobs run directly, as a non-root user. Tools
  with a version (Node.js, ruff) are pinned in the workflow and cached in
  `/data/tools`. That host runs production services: change nothing there
  beyond `runner-ci` without asking.
- Never commit, push or create remote repositories unless asked.

## Reference material

- `cockpit-samba-dc`: frontend patterns (`src/lib/exec.ts` for spawn wrappers,
  `test/mocks/cockpit.ts` for the cockpit mock, `Makefile.local` for VM targets).
- `ansible-win_edulab`: a real project that must run unmodified from this plugin
  (acceptance test of Phase 3); its `.claude/skills/ansible-jinja2` is useful when
  writing the bundled collection.
