# cockpit-fleet — plan

What is left. How each closed phase was shown:
`docs/architecture/acceptance.md`; this workstation and its lab VMs:
`CLAUDE.local.md` (not tracked).

## Status (27 November 2026)

Phases 0–24 closed, and the assistant without questions.
21–24 were the last to add features (`decisions.md`, "Freeze of
features"): then fixes, minor changes and field use, toward 1.0.0. Last
release 0.4.0 (18 November); since then: `[Unreleased]` of `CHANGELOG.md`.
`make release` only when asked.

To resume: `git status`, `git log origin/main..`; `make check` (878
backend, 246 frontend tests). Each backend module's docstring says what it
is; `cli.py` the registry of commands, `db.py` the schema (41
migrations). Under way: the integration on the lab's VMs
(`sandbox` run D, SELinux on `fed44` and `alma10`).

## Minor changes decided

- **"Close all groups" / "Open all groups" as an icon** (27 November): the
  link beside "Group by" (`ListView.tsx`, every grouped list) becomes an
  icon button (folded/unfolded, as Cockpit's lists show it: `AngleDown`/
  `AngleRight` or `CompressIcon`/`ExpandIcon` of PatternFly, to choose),
  its text kept as tooltip and `aria-label`.

## To study

- **The names of `overview.md` and `dashboard.md`** (`docs/architecture/`).
  The first is "Overview and security model" (the parts, a run's flow,
  privilege boundaries, secrets, the sandbox: what `CLAUDE.md`'s rules
  cite), the second "The Overview", the first tab of the page
  (`overview.get`): a search for one finds the other. To weigh, before
  renaming anything:
  - the names: `security-model.md` for the first (then what of its
    section on the parts and the flow: there, in `backend.md`, or a short
    file of its own), `overview-page.md` or as it is for the second,
    whose command and component are `overview`; `docs/security.md`, the
    administrator's, must not be taken for it;
  - what cites them: 7 references to `overview.md` in 5 files
    (`CLAUDE.md`, the index, `checks.md`, `interface.md`,
    `docs/security.md`), 5 to `dashboard.md` in 4, `docs/history/dashboard.md`
    beside it, the sections cited by name ("Secrets");
  - what breaks outside: links from the installed documents and from
    released packages and the README on GitHub to the old paths;
  - whether a line at the top of each ("not the page: see …") is enough.

## Integration

Last runs, 26 November. Containers, the three distributions: `runs`,
`schedules`, `workflows`, `negative`, `checks`, `chat`, `mcp`, `cli`,
`documents`, `store`, `sandbox` (its run D in containers only); Debian
only: `netscan`, `make ct-lifecycle` (from 0.3.0). VMs: `netscan` on the
three (`win11` up), `windows` on `deb13`; `runs`, `workflows`,
`schedules` on `fed44` (SELinux, no denial); `sandbox` on `deb13` and
`alma10` before run D. Earlier: `files` in containers.

## Future

After the freeze, only if field use shows one blocking.

- Ad-hoc commands: sets of variables chosen for one (today the inventory's).
- External triggers (git webhooks, auto-remediation); `ansible-pull` for
  very large fleets.
- Secrets on OpenBao or Bitwarden; export to Prometheus or InfluxDB.
- Execution environments in containers; other controller distributions.
- A limit on the rows of state changes (off by default).
- Store: credentials for downloads, parts kept for some projects.
- Overview: a filter by inventory.
