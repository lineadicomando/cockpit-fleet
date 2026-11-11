# Known limits

What is left as it is, by area: none of it blocks the use of a
controller. What is protected and what is not, for an administrator, is
in [security.md](security.md); the decisions behind these limits in
[decisions.md](decisions.md).

- Runs: a failed run leaves its unit failed, so the system is `degraded`
  until `reset-failed`
  (`CollectMode=inactive-or-failed` untried); Cockpit's admin access without
  a group is audited as `root`; integration tests not in CI (no privileged
  containers on the runner).
- Projects: a git token over https untried on a real private repo; with
  `ansible-win_edulab` whether the key or the inventory's password logged in
  was not looked at; the
  uploaded archive is not kept, no progress bar; Galaxy public sources only,
  roles of the requirements file not installed, no job timeout; an inventory
  has one vault credential; inventory plugins get no credential; the address
  shown for a host is its own `ansible_host`, not one its groups set (the
  listing is `--export`ed, so group variables stay with the groups); a file
  inventory of another project gets the collections of the job's project only.
- Editor: open files and their unsaved changes kept while another tab of
  Fleet is shown, lost when the browser's tab is reloaded (asked first);
  no search in the content of files; syntax
  check of a playbook without vault password, of an inventory on save or
  on request only (a file of its tree that is no source is not checked), listed among runs; completion and module
  help know only the controller's modules, no sub-parameters.
- Store: read by every run, no part of it kept for some projects; a write in
  place through a link reaches the store during the run, not at its end;
  no versions of a file (a replaced one is gone); the listing stops at
  20 000 entries; a file of root put there by hand is not given to runs.
  Downloads: no credentials for a URL; one that ended short is taken up
  only with a strong ETag or a Last-Modified, and for a week; a source is
  checked at most daily, by the tick, at no set time, and a server with no
  validator sends the whole file at each check (the digest then says
  whether it changed). Downloads to the browser: one that fails half way
  leaves a short file (compare the digest shown); an archive of many
  entries is bounded by the length of its link (hundreds go through their
  folder), and one whose file shrinks meanwhile ends short; one upload of
  1.5 GiB dropped on the page failed at once and was not seen again (9
  November; the page now says why). Copies share blocks only on XFS and
  Btrfs, and count their whole size against `[store] max_gb` there too; a
  copy made by a job is of the files as they were when it was asked.
- Ad-hoc: arguments as one line in the page, stored in clear.
- Masking: other encodings, and secrets a run decrypts itself, not masked.
- Agents: `tools/list` while disabled audits a denial; `run.result` holds a
  process up to 50 s; 216 of 329 parameters have no description (205 of
  307 of the tools; 5 November); in the profile diagnostic, `setup` with
  `fact_path` runs the `*.fact` programs of that directory on the node; a draft is taken file by file (a file
  changed in main too is a conflict, settled by hand) and is not brought
  up to date with main, nor exported; `inventory.create` and
  `inventory.update` (class config) write the main file of a managed
  inventory without a draft; the acceptance of a profile's risks is not
  asked again when their text changes.
- Chat: plain text; conversations not deletable; model not choosable; one
  user can fill the sessions; missing login said only after a first message;
  role tried on x86_64 only, versions pinned in the role, no script runs it,
  untried behind a proxy; `fleet assistant` asks with Claude's own
  questions, whose "don't ask again" trusts one tool, of any class (adhoc,
  config and code included), for the session, and only the tools the
  profile lets through are known when it starts (a template made later is
  asked about); controllers configured before `{allowed_tools}` ask about
  every call until the role is run again; Claude's question on trusting its directory
  the first time (it says the agent may edit and run files: it may not);
  the page lists Claude's own `/login` among past conversations; for a viewer
  or an operator the tab appears only once the page is reloaded after the
  agent is named (an administrator's guide looks again every 5 s).
- AlmaLinux: no `ansible`/`ansible-lint` (no lint; collections from the
  requirements file); `selfcheck` leaves `/var/lib/cockpit-fleet/.ansible`.
- Schedules: builder knows few shapes; no end date or window; runs not
  filterable by schedule; a calendar read error leaves no next time; a
  missed or failed launch is not notified by itself.
- Notifications: one message per run, plain text; mail never via proxy;
  channels tried in sequence (a dead one delays the rest ≥ 15 s); a refused
  test retried 24 h; the default webhook body has no title; `fleet channel
  update` wants the name again.
- Checks: host facts only from cycles; one `fleet_result` per host;
  thresholds per check, not per host; `state.list` is not paged (the
  grid of the page of hosts asks for every state: 0.38 s at p95 for 5030;
  at many thousands more it will need paging);
  maintenance windows not exported; inventories re-read hourly, each a run;
  `certificates` on a Windows store lists every expired root it keeps, in
  one long summary.
  Untried: pages as a viewer, Windows checks from Fedora/Alma, multi-shard
  cycles on real nodes.
- Time series and maintenance: ingest writes outside a transaction (0.12 s
  for 5000 values, half in one); aggregates by UTC day; a database of
  before phase 8 above 64 MB is never made incremental (none exists); rows
  of runs, state changes and the audit grow without end; deleting run
  output and giving pages back seen in unit tests and containers only.
- Scale: SSH not weighed (3 nodes beside 500 local aliases are too few to
  say what opening connections costs; connections kept per group stay
  unbuilt unless a test with tens of SSH nodes asks for them); beyond
  ~1000 hosts every 10 minutes, `[checks] max_concurrent = 2`; the run
  directories levelling off after a day not seen (the 24 hours end just
  before the first deletion).
- Sizing: per group, whatever checks a shard holds (one task per host, so
  close enough); a host's time is the cycle's spread evenly; the fit of
  memory may come out flat with few cycles, then all of it counts per fork;
  with up to 1000 hosts it kept the configured shard and forks (half the
  interval far off), so lowering them was never seen outside unit tests.
- Falling behind: counted per inventory and group, not per credential or
  `become` within them; a group turned off while behind is never told back
  on time (its row goes after 30 days); a slow playbook of the user shares
  the lane of the other checks and makes them skip too.
- Health: the last day only, no history; three column headers of the
  cycles cut short at 1400 px ("Inve…", "Interv…", "Durata, …").
- Charts: on the page of a host only, per check; no zoom, no chosen dates,
  no thresholds drawn; the labels of the axes faint on the dark theme.
- Interface (25 October): the summary a node writes for a check is English
  ("accounts 2 (warning 2)"), as are the backend's messages; the counts of
  the states on the page of hosts are headings; a page left open across an
  upgrade of the package cannot load the new bundle's chunks until the user
  logs in again (Cockpit lists a package's files when the session starts);
  pages not seen as a viewer or an operator, nor the chat, in this audit;
  the header "Which check" of the tables of states cut short at 1440 px
  beside Cockpit's menu; with a few hours of values a chart of a day is
  mostly empty.
- CMDB: zypper never had an update to find; dnf without `become` refetches
  metadata into the account's cache; a security-update notification end to
  end not seen.
- Approvals: one approver suffices (no quorum); an approval is not
  notified to the approvers by name, only to channels; a pending run shows
  its launch time as "Started".
- Workflows: no schedule launches a workflow; the launch takes no limit or
  answers of its own; the graph is drawn in columns, not with its arrows;
  `set_stats` per host is not passed on; what a step passed on is kept in
  clear and masked only for known secrets; the steps a workflow starts are
  not in the audit (its launch, cancel and resume are); its commands are
  tools of fleet-mcp like any other (deciding an approval is not); no rule
  scope for a workflow; an approval at a step is seen on the page of runs,
  not on the workflow's.
- Terminal: survey choices not pickable or completed; pushed commits keep
  the authors of the clone (who pushed is audited).
- Security review (24 October), left as they are: a run directory swapped
  for a link between `fleet-unit`'s check and the start binds another
  directory (needs the account itself); the backend shares the runs' uid,
  so anything of a run it followed would reach their credentials through
  `/proc/PID/root` (the files of a run are read without following links);
  webhook bodies carry what nodes said unescaped (Slack and Discord markup,
  mentions); `http://` allowed for channels with a secret; webhook URLs and
  template `extra_vars` visible to viewers (neither is for secrets);
  `config.export` and `inventory.get` by an administrator not audited; an
  operator can fill the 500 waiting secret answers for an hour; `fleet`
  shows run output with its colours raw (escape sequences of nodes); unpack
  jobs have no time limit.
- Review (1 November), left as they are: the directories of
  `[projects] local_roots` stay readable in the sandbox of a run (the
  administrator hides them with a drop-in, `install.md`); `fleetctl`
  without `SUDO_UID` is an administrator and a playbook can run it (gains
  nothing while the database is hidden by the tmpfs); the journal's audit
  records can be forged by any local account (the database is the record,
  `security.md`); `ct-lifecycle.sh` still picks a package with `ls`
  (one per distribution since the build scripts clean up).
- Negative tests (25 October): a value typed in clear for a secret is
  masked in the audit wherever it appears, so a short one masks other text
  of that record too; the fuzzer gives one hostile value at a time, never
  two together; `chat.start`, `state.ack`, `workflow.resume` and the
  deletes of things in use stop before most of their checks in it.
