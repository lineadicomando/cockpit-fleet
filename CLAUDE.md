# CLAUDE.md

Agent instructions for this project.

## What this is

`cockpit-fleet` is a Cockpit plugin that drives Ansible
from the browser, inspired by Semaphore UI, and that also collects, stores and
notifies on information from managed nodes using Ansible only (agentless).

`TODO.md` is the source of truth for decisions, architecture, open points and the
phased plan. Read it before starting any work, tick items as they are completed,
and update it when a decision changes. Do not restate its content here.

Status: Phase 0 done (scaffold, package, run sandbox, secrets), except the Windows
spike. `docs/architecture.md` describes what exists and what was measured.

## Non-negotiable rules

These come from the security model in `TODO.md` §3; a change that breaks one of
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
8. **Checks are OS agnostic.** A catalog check has one output schema and one
   implementation per platform; Windows nodes are reached over SSH, not WinRM.

## Stack and conventions

- Frontend: TypeScript, React 18, PatternFly 6, esbuild. Mirror
  `../cockpit-samba-ad-dc` for layout, build, i18n, test
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
- Code, comments, commit messages and docs in English; conversation with the user
  in Italian.
- Commits: short imperative subject, as in the sibling project's history.

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
test/integration/measure-events.sh   # callback cost on N simulated hosts
```

Run both test scripts after any change to the unit, the helpers or sudoers.

## Test environment

- Controller VM: libvirt `deb13-samba-dc`, `maint@VM`, Cockpit on
  `https://VM:9090`. Password-only SSH (`sshpass -e`).
- Windows nodes over SSH: `teacher` .10, `student01` .11, `student02` .12, from
  `../ansible-win_edulab/inventories/school`.
- Linux nodes: containers on the controller VM, or a clone of `debian13-base`.
- VMs are usually shut off: `virsh -c qemu:///system list --all`, then start what
  is needed. Ask before creating, cloning or deleting a VM.
- Before starting a VM, check that the running VMs plus the new one do not need
  more than the host can give: compare the memory and vCPUs assigned to them
  (`virsh dominfo <domain>`) with what is free on the host (`free -m`, `nproc`).
  If they do not fit, start fewer VMs or shut down the ones not needed for the
  test, and shut down what you started when the test is over.
- After a deploy, a Cockpit tab keeps running the old bundle until reloaded, and a
  package in `/usr/share/cockpit` is shadowed by a copy in `~/.local/share/cockpit`.
- The VM also hosts the Samba AD DC used by other projects: do not break it.
- Cockpit there has a self-signed certificate, which the browser tools refuse.
  Open an SSH tunnel (`ssh -L 9091:127.0.0.1:9090`) and use
  `http://127.0.0.1:9091`: Cockpit accepts plain HTTP from localhost.
- Do not `pkill -f` with a pattern that also appears in your own command line.
- Windows nodes accept public keys only. `win11-pc00` is `teacher` (.10),
  `win11-pc01` is `student01` (.11), `win11-pc02` is `student02` (.12); each has
  8 GB assigned, so they do not all fit on the host together with the controller.

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
- License: `LGPL-2.1-or-later`, same as `cockpit-samba-ad-dc` (provisional).
- Never commit, push or create remote repositories unless asked.

## Reference material

- `cockpit-samba-ad-dc`: frontend patterns (`src/lib/exec.ts` for spawn wrappers,
  `test/mocks/cockpit.ts` for the cockpit mock, `Makefile.local` for VM targets).
- `ansible-win_edulab`: a real project that must run unmodified from this plugin
  (acceptance test of Phase 3); its `.claude/skills/ansible-jinja2` is useful when
  writing the bundled collection.
