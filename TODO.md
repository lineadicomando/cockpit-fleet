# cockpit-fleet — plan

What is left. The other documents are listed in `CLAUDE.md`; how each
closed phase was shown is in `docs/architecture/acceptance.md`; this
workstation and its lab VMs in `CLAUDE.local.md` (not tracked).

## Status (20 November 2026)

Phases 0–18 closed; phase 19 under way (below). Last release 0.4.0 (18
November), with the Overview of phase 18. `make release` only when asked.

To resume: `git status`, `git log origin/main..`; `make check` (765
backend, 197 frontend tests). Each backend module's docstring says what
it is; `cli.py` is the registry of commands, `db.py` the schema (34
migrations).

## Phase 19: network discovery

Decided 20 November (`decisions.md`, "Network discovery");
`docs/architecture/network.md` says what exists.

- [x] The scan of a subnet: the job `netscan` in the sandbox, the subnets
  allowed (`[network] scan_*`), `netscan.subnets`, `netscan.start`,
  `netscan.result`, the class `scan` for agents (diagnostic and
  administrator, asked every time), the page Nodes, Network;
  `netscan.sh` in containers on the three distributions.
- [x] An inventory made from a scan's results (`netscan.inventory`), and
  `mac_address` written into an existing one (`netscan.macs`,
  `host_vars/HOST/fleet_mac.yaml`, a host's file moved there since
  ansible-core 2.16 ignores `HOST.yaml` beside `HOST/`); through a draft
  for an agent; `netscan.sh` reads both with Ansible.
- [ ] `net.probe` of an inventory's hosts (operator, `read`), the
  comparison of MAC addresses with the CMDB, the agent's tools.
- [ ] A module `net_probe` run from a node of the segment (Linux and
  Windows), for subnets beyond a router.
- [ ] At the close: on the VMs, `netscan.sh` with `IT_NETSCAN_SUBNET` on
  the lab's network (the Windows node among the hosts, NetBIOS and its
  MAC address), `sandbox.sh`; whether Debian 13 and AlmaLinux 10 let the
  service account send ICMP as installed (`ping_group_range`); the page
  in a browser.

Integration since the review of 16 November: `make ct-test` on AlmaLinux
(`runs`, `checks`, `store`, `files`, `schedules`, `cli`, `mcp`, `chat`,
`negative`), and `chat`, `mcp` on Debian and Fedora
after the changes of 18 November; `make ct-lifecycle` on Debian; on the
VMs `deb13` and `alma10`: `sandbox`, `runs`, `files`, `store`, `chat`,
`schedules`, `negative`. The rest on Fedora not since phase 17. The
known failure of `cmdb.sh` on Fedora in a container: `architecture/tests.md`.

## What the review of 16 November left

Left by the user for another session; checked against the code on 18
November, all still open.

Fix:

- [ ] `jobs.reconcile`: `fcntl.flock(LOCK_EX)` with no timeout; a
  `fleetctl` suspended (Ctrl-Z) holding it stops every page and dispatch.
- [ ] `fleet-secret answer` logs the ticket's name to the journal before
  a run spends it: a journal reader with the operator role can spend it.
- [ ] `store.archive` (`storage.py`) opens every file before the first
  byte: past ~1000 files `EMFILE`, and the browser saves an empty tar.
- [ ] Upgrade scriptlets (`debian/cockpit-fleet.postinst`,
  `packaging/cockpit-fleet.spec`) delete every `runs/*/inventory.json`,
  also of a reading ended and not collected: tie it to the old version.

Fix, or record in `limits.md`:

- [ ] `fleet-run` keeps the unmasked error in `result.json` (no message
  holds a secret today).
- [ ] Two `notify_states` (`checks.py`) together notify twice.
- [ ] `_last_stats` (`jobs.py`) reads the last 256 KiB of `events.jsonl`:
  stats and artifacts lost past ~1900 hosts.
- [ ] `moved_rows` (`storage.py`) can fail on a stale row after
  `store.move` moved the file.
- [ ] Files: a tab kept while its read was pending spins for ever (`kept`
  in `FilesPage.tsx`).
- [ ] Approvals: the decide modal vanishes when another decides
  (`PendingApprovals`).
- [ ] Survey: "None" on an optional choice with a default gives the
  default (`collectAnswers`).
- [ ] Installed docs (`%doc ... docs` in the spec) link to `decisions.md`
  and `TODO.md`, not packaged; the rpm has no `CHANGELOG.md`.
- [ ] `security.md` repeats limits of `limits.md`.

For the user to decide:

- [ ] A playbook can rewrite its `result.json` and turn a failed run into
  a successful one (the run writes its own directory).
- [ ] A store folder is drawn whole, without paging.
- [ ] No component tests for the frontend.
- [ ] GitHub actions pinned by tag, not by commit.

## Future

Not planned as phases yet; the user picks the next one.

- Ad-hoc commands: sets of variables chosen for one (today only the
  inventory's).
- Assistant: instructions per project.
- External triggers (git webhooks, auto-remediation); `ansible-pull` for
  very large fleets.
- Secrets on OpenBao or Bitwarden; export to Prometheus or InfluxDB.
- Execution environments in containers; other controller distributions.
- A limit on the rows of state changes (off by default).
- Store: credentials for downloads, parts kept for some projects.
- Overview: a filter by inventory.
