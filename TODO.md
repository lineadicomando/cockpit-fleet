# cockpit-fleet — plan

Status and what is left. Decisions: `docs/decisions.md`; known limits:
`docs/limits.md`; how each part works and how each phase was accepted:
`docs/architecture.md` (2600 lines: list its sections with `grep -n '^## '
docs/architecture.md` and read only the one needed).

## Status (6 November 2026)

Phases 0–11 closed; 0.2.0 released on 2 November. Phase 12 (profiles of
the assistant) is built, documented and tested but for the trial with the
real agent below; then it closes. Under `[Unreleased]`: runs in series,
and phase 12. Its commits on `main` are not pushed (`git log
origin/main..`).

To resume: `git status`; `make check`. Each backend module says what it is
in its docstring; `cli.py` is the registry, `db.py` the schema (25).

Tests on 6 November: unit tests (582 backend, 133 frontend); full
`ct-test` on the three, all passed but `cmdb` on Fedora (only in a
container: netavark "invalid namespace path", as on 1 November); the page
in a browser on `deb13` with `fake-agent.py`. Run two distributions'
`ct-test` one after the other, not together.

## Next: close phase 12

Where it is: decisions in `docs/decisions.md` ("Assistant profiles");
design in `docs/architecture.md` ("Agents", "Approvals", "The chat");
for administrators `docs/chat.md`. Code: `profiles.py` (classes, the table
of questions), `autonomy.py`, `approvals.py`, `drafts.py`,
`instructions.py`, `chat.py` (`chat.accept`, `chat.start`), `terminal.py`
(`fleet assistant --profile`, `fleet draft diff`); `src/components/
ChatPage.tsx`, `Drafts.tsx`, `src/lib/chat.ts`.

1. [ ] **Trial with Claude** (the user logs in; agreed to do it in a later
   session). Prepare `cockpit-fleet-deb13` (skill `vm-lab`): `reset`,
   `deploy`, add the lab user to `cockpit-fleet-admin`, run the role
   `lineadicomando.fleet.chat_agent` with `chat_agent_configure=true
   chat_agent_enable_mcp=true` (about 510 MB), set `[mcp] max_role =
   "admin"` and `[chat] profiles` to all five, make a managed project with
   a playbook, a managed inventory (`self ansible_connection=local`) and a
   template. Then the user runs `/opt/cockpit-fleet-agent/bin/claude
   /login` as the lab user. Try, and note in `architecture.md`:
   - the page: each profile's start (risks accepted once), questions with
     profile and class, "always" for launches, an ad-hoc command in
     `administrator` asked every time and then waiting for confirmation,
     the agent writing a playbook to a draft, reviewed and merged;
   - `fleet assistant --profile assisted|autonomous`: that
     `--allowedTools` works beside `--permission-mode manual` (reads
     unasked, launches asked in `assisted`, unasked in `autonomous`).
2. [ ] Acceptance of phase 12 in `architecture.md` ("Acceptance of the
   phases"); update `CLAUDE.local.md` with the state of the VM.

Open question for the user: should `inventory.create`/`inventory.update`
(class config) write a managed inventory through a draft too? Today they
write `main` (in `limits.md`).

## Phase 13: the store

A tree of files on the controller that playbooks copy to nodes, that runs
may add to, filled from the browser or from URLs through a queue. The
model is the store of `ansible-win_edulab` (`win_workman_storage_path`):
installers downloaded by `get_url` delegated to the controller, ISOs put
there by hand, files fetched from nodes, 61 GB with files of 5–6 GB; it
must run unmodified with the store as its storage path.

Decided on 9 November:

- One store for the controller, read by every run: not meant for secrets
  (say so in `security.md`; win_edulab's Veyon keys stay out of it).
- Viewers list and download files; operators and administrators also
  upload, change, delete and queue downloads from URLs.
- What a run writes into the store is always taken in, with the run and
  who launched it in the audit.

Design, after the prototypes of 9 November (scripts not kept; what they
showed goes to `architecture.md` with the store):

- `/var/lib/cockpit-fleet/store/`, files by their real names, metadata in
  the database (sha256, size, origin, who, when); limits in `[store]`; out
  of backups by default. It must be on the filesystem of `runs/` (hard
  links): a volume for it is mounted on `/var/lib/cockpit-fleet` whole.
- In the run: a farm of **hard links** in `runs/<id>/store/`, made by
  `fleetctl` when the run is dispatched (not when queued: a run waiting
  for approval would pin old files), given as `fleet_store`. The store
  itself is not bound into the unit, so the unit does not change. Symbolic
  links do not work: `get_url` checks `os.access(dest, W_OK)` through the
  link and refuses to replace a file. With hard links, on ansible-core
  2.19 (Debian): `get_url` present with the right checksum changes
  nothing, replaced or new gives a new inode; `copy` of a file and of a
  directory and `fetch` into the farm work; a delete in the farm leaves
  the store alone (not taken in).
- Intake when the run ends: the inode and mtime of every link are noted
  at dispatch; a regular file with a new inode or a new path is moved into
  the store (`rename`, one link, no symbolic link or other kind, read
  without following links); a file written in place through a link has
  already changed the store (seen): its digest is taken again and the
  change audited. The farm goes when the job ends, not after `keep_days`.
- `get_url` of a large file failed with ENOSPC (on both): its temporary file goes to
  `remote_tmp`, under the home, a tmpfs. With `remote_tmp` on the run's
  disk (`scratch/`) 3 GB passed: set it in the generated `ansible.cfg`
  for the local connection.
- Upload: `fleetctl store.put`, a JSON header line then raw bytes, through
  `cockpit.channel` (`payload: stream`, `binary`, `send-acks: bytes`, at
  most 16 MiB not acknowledged): 1 GiB in 12 s (85 MB/s), digest right,
  memory of the page flat. Into a `.part` that a broken upload resumes.
- Download to the browser: a Cockpit external channel
  (`/cockpit/channel/<csrf-token>?<base64 of the options>`, `payload:
  stream`, `external: {content-disposition}`) spawning `sudo -n -u
  cockpit-fleet fleetctl store.read ID` (the id in argv: no stdin): 2 GiB
  in 7 s. A command that fails still answers 200 with no bytes, so the
  page asks `store.get` first (role, existence, size, sha256 shown).
- Downloads from URLs: a job kind `download` in the sandbox (urllib, the
  `[network]` proxy, `Range` to resume, sha256 expected), a lane of its
  own (`[store] max_downloads`), progress read from the run's directory.

1. [x] Prototypes: external channel and upload on `deb13`; the farm on
   `deb13` (ansible-core 2.19) and `alma10` (2.16, SELinux enforcing, no
   denial), the same results.
2. [x] Store core: schema, `store.*` commands, farm and intake, `remote_tmp`,
   page, `fleet store`, docs, tests (`store.sh`; design now in
   `architecture.md`, "The store").
   [x] `sandbox.sh` and `store.sh` on `deb13` (the unit changed:
   `~/.ansible` bound to the run's disk); the page there as operator (1.5
   GiB dropped and downloaded) and as viewer; full `ct-test` on Debian.
   [x] Full `ct-test` on AlmaLinux and Fedora (9 November): all passed but
   `cmdb` on Fedora, the netavark error of a container seen since 1
   November.
3. [ ] Download queue: job `download`, lane, progress, resume, cancel.
4. [ ] Sources (URL and checksum kept, refreshed by a conditional GET,
   from a schedule too); acceptance with `ansible-win_edulab`.

## Future

Sets of variables: secret variables in a set (encrypted as credentials
are, kept off the page and out of export), and sets for ad-hoc commands.
Instructions per project for the assistant. External triggers (git
webhooks, auto-remediation); `ansible-pull` for very large fleets; secrets
on OpenBao or Bitwarden; export to Prometheus or InfluxDB; execution
environments in containers; other controller distributions. A limit on the
rows of state changes (off by default).
