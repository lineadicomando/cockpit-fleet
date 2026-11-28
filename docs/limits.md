# Known limits

What is left as it is, by area: none of it blocks the use of a controller.
What is protected and what is not, for an administrator, is in
[security.md](security.md); the decisions behind these limits in
[decisions.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/decisions.md).

- Runs: a failed run leaves its unit failed, so the system is `degraded`
  until `reset-failed` (`CollectMode=inactive-or-failed` untried);
  Cockpit's admin access without a group is audited as `root`; integration
  tests not in CI ([tests.md](architecture/tests.md), "CI").
- Projects: a git token over https untried on a real private repo; with
  `ansible-win_edulab` whether the key or the inventory's password logged
  in was not looked at; the uploaded archive is not kept, no progress bar;
  Galaxy public sources only, roles of the requirements file not installed,
  no job timeout; an inventory has one vault credential; inventory plugins
  get no credential; the address shown for a host is its own
  `ansible_host`, not one its groups set (the listing is `--export`ed, so
  group variables stay with the groups); a file inventory of another
  project gets the collections of the job's project only.
- Editor: open files and their unsaved changes kept while another tab of
  Fleet is shown, lost when the browser's tab is reloaded (asked first); no
  search in the content of files; syntax check of a playbook without vault
  password, of an inventory on save or on request only (a file of its tree
  that is no source is not checked), listed among runs; completion and
  module help know only the controller's modules, no sub-parameters.
- Store:
  - Read by every run, no part of it kept for some projects, so no place
    for keys a role keeps on the controller between runs (Veyon's in
    `ansible-win_edulab`). A write in place through a link reaches the
    store during the run, not at its end, and so does a change of mode
    (`get_url` with `mode` gives it to the store's file). No versions of a
    file (a replaced one is gone); the listing stops at 20 000 entries.
  - Downloads from URLs: no credentials for a URL; one that ended short is
    taken up only with a strong ETag or a Last-Modified, and for a week; a
    source is checked at most daily, by the tick, at no set time, and a
    server with no validator sends the whole file at each check (the digest
    then says whether it changed); each download is given the room left
    when it is queued, so downloads queued together may each take it, and
    the free disk with it (`[store] max_gb` is checked again when one is
    taken into the store).
  - Downloads to the browser: one that fails half way leaves a short file
    (compare the digest shown); an archive of many entries is bounded by
    the length of its link (hundreds go through their folder), and one
    whose file shrinks meanwhile ends short. One upload of 1.5 GiB dropped
    on the page failed at once and was not seen again (9 November; the page
    now says why).
  - Copies share blocks only on XFS and Btrfs, and count their whole size
    against `[store] max_gb` there too; a copy made by a job is of the
    files as they were when it was asked.
  - SFTP: version 3 only; a file is replaced whole, never written in place
    (an editor saving through `sshfs` in place is refused); `[store]
    max_gb` is checked when a file is closed, the free disk every 256 MiB
    written; a file written is listed with no digest until the tick; a
    `.filepart` closed and not renamed waits a week, seen by its writer
    only; links are neither listed nor made; WinSCP replaces a file by
    deleting it and renaming the new one, so for a moment the store has
    none there; through `sshfs` a file reaches the store a moment after the
    copy returns.
- Documents: only the texts (`.md`) have a history; an attachment replaced
  or deleted is gone. No drafts: an agent's write is a commit like a
  person's, restored from the history if unwanted. Markdown is a subset of
  GitHub's, not CommonMark to the letter (`documents.md`); no HTML, no
  images from outside the documents, 4 MB each and 16 MB a document at
  most. A text is at most 1 MiB; an upload of an attachment is not resumed.
  The search reads every text at each call (no index), the listing stops at
  20 000 entries. A PDF only through the browser's printing, never on the
  controller. Two people editing one text: the second save is refused, and
  its changes must be carried over by hand.
- Ad-hoc: arguments as one line in the page, stored in clear.
- Sets of variables: a secret variable is an extra variable, one value for
  every host of a run (a value per group needs a name per group); its value
  is not in the inventory's tree, so a clone of it used at a terminal does
  not have it; a value cannot be checked against one typed (no "verify");
  the file of a secret variable taken out of a set by a client other than
  the page or `fleet` stays in `/etc/cockpit-fleet/secrets` (`fleet-secret
  delete` removes it).
- Agents: `tools/list` while disabled audits a denial; `run.result` holds a
  process up to 50 s; 220 of 502 parameters have no description (206 of 420
  of the tools; 27 November); in the profile diagnostic, `setup` with
  `fact_path` runs the `*.fact` programs of that directory on the node; a
  draft is taken file by file (a file changed in main too is a conflict,
  settled by hand) and is not brought up to date with main, nor exported;
  an agent's `inventory.create` and `inventory.update` change the
  inventory's row at once (name, path, sets) and only its files wait in the
  draft, and an agent does not change the format of a managed inventory;
  the acceptance of a profile's risks is not asked again when their text
  changes; an inventory created by an agent, `fleetctl` or `fleet` is not
  read until something reads it (the page asks for it), so an agent sees it
  with no hosts; `whoami` lists the commands of the role, not of the
  profile; the questions of the page show a tool's description in English.
- Skills: the frontmatter of a `SKILL.md` is read for its name and
  description only, not as YAML; only `.md` and `.txt` files beside it,
  none in subdirectories; read when a session starts, so a change waits for
  the next; a collection's only when installed for the project's present
  requirements; no page of their own beyond the instructions of the chat.
- Chat:
  - Plain text; conversations not deletable; model not choosable; one user
    can fill the sessions; missing login said only after a first message; a
    message written and not sent is lost on a reload of the page; for a
    viewer or an operator the tab appears only once the page is reloaded
    after the agent is named (an administrator's guide looks again every 5
    s); the page lists Claude's own `/login` among past conversations.
  - A change of profile starts the agent again (seconds, and the replay of
    the conversation), turns "Without questions" off, restarts the count
    of the runs launched without asking (and what "Stop everything"
    reaches: runs launched before the change are canceled from the Runs
    page), starts another draft for an administrator's files, and is not
    offered by `fleet assistant` (`--profile` and the agent's own resume).
  - Without questions in `assisted`, the launches confirmed before it was
    turned on count against its runs; it is lost on a reload of the page,
    and the agent learns of it only from its instructions and from what
    the backend refuses, not from a message when it is turned on or off.
  - The role `chat_agent`: tried on x86_64 only, versions pinned in the
    role, no script runs it, untried behind a proxy; controllers configured
    before `{allowed_tools}` ask about every call until the role is run
    again; Claude asks whether to trust its directory the first time (it
    says the agent may edit and run files: it may not).
  - `fleet assistant` asks with Claude's own questions, whose "don't ask
    again" trusts one tool, of any class (adhoc, config and code included),
    for the session, and only the tools the profile lets through are known
    when it starts (a template made later is asked about). It has neither
    "Allow more" nor "Stop everything" (its `fleet-mcp` draws a session the
    terminal does not show: its runs are canceled one by one), nor "Without
    questions", in any profile (it keeps `--permission-mode manual`).
  - A check cycle a launch of `check.run` makes due is not the session's,
    and "Stop everything" leaves it to end. The end of a run is told to the
    agent only while the page is open, not at a terminal, not for runs that
    ended within the answer that launched them, and not for those of a
    conversation before a change of profile.
  - "Without questions" is lost on a reload, and has no cap of calls:
    nothing but a person watching stops an agent steered by a node, before
    "Stop everything".
- AlmaLinux: no `ansible-lint`, so no lint ([install.md](install.md));
  `selfcheck` leaves `/var/lib/cockpit-fleet/.ansible`.
- Schedules: builder knows few shapes; no end date or window; runs not
  filterable by schedule; a calendar read error leaves no next time; a
  missed or failed launch is not notified by itself.
- Notifications: one message per run, plain text; mail never via proxy;
  channels tried in sequence (a dead one delays the rest ≥ 15 s); a refused
  test retried 24 h; the default webhook body has no title; `fleet channel
  update` wants the name again.
- Checks: host facts only from cycles; "Recent cycles" lists the last 100 a
  check had; what a cycle found host by host is kept only for cycles that
  ended after the upgrade to phase 22; one `fleet_result` per host;
  thresholds per check, not per host; `state.list` is not paged (the grid
  of the page of hosts asks for every state: 0.38 s at p95 for 5030; at
  many thousands more it will need paging); maintenance windows not
  exported; inventories re-read hourly, each a run; `certificates` on a
  Windows store lists every expired root it keeps, in one long summary.
  Untried: pages as a viewer, Windows checks from Fedora/Alma, multi-shard
  cycles on real nodes.
- Time series and maintenance: ingest writes outside a transaction (0.12 s
  for 5000 values, half in one); aggregates by UTC day; rows of runs, state
  changes and the audit grow without end; deleting run output and giving
  pages back seen in unit tests and containers only.
- Scale: SSH not weighed (3 nodes beside 500 local aliases are too few to
  say what opening connections costs; connections kept per group stay
  unbuilt unless a test with tens of SSH nodes asks for them); beyond ~1000
  hosts every 10 minutes, `[checks] max_concurrent = 2`; the run
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
- Health: the last day only, no history; three column headers of the cycles
  cut short at 1400 px ("Inve…", "Interv…", "Durata, …").
- Overview: no filter by inventory; no history of what is kept as it is now
  (the number of hosts, updates, compliance), nor fleet-wide metrics; days
  in the browser's offset of today, so a day across a change of summer time
  is an hour off, and in UTC at a terminal; the hosts that fail most count
  the runs they failed in, not out of how many; a host is named, not
  linked, since a name may be in several inventories; changes of state
  recorded before schema 32 (0.4.0) have no hard state, so an unknown
  suppressed because the host could not be reached counts there as a
  problem, recovered only when the host is reached again: until then
  "Problems and recoveries" can show problems with no recovery while none
  is open. It fades within 30 days of the upgrade.
- Charts: on the page of a host (per check) and the Overview only; no zoom,
  no chosen dates, no thresholds drawn.
- Interface (25 October): the summary a node writes for a check is English
  ("accounts 2 (warning 2)"), as are the backend's messages; the counts of
  the states on the page of hosts are headings; pages not seen as a viewer
  or an operator, nor the chat, in this audit; the header "Which check" of
  the tables of states cut short at 1440 px beside Cockpit's menu; with a
  few hours of values a chart of a day is mostly empty.
- CMDB: zypper never had an update to find; dnf without `become` refetches
  metadata into the account's cache; a security-update notification end to
  end not seen.
- Network scans: IPv4 only; MAC addresses only of the controller's own
  segment, or given over NetBIOS by a host beyond a router; a neighbour the
  kernel still holds as stale 9 s after the sweep is shown as seen; a host
  that answers nothing and is beyond a router is not found; the ports are
  the same for every scan (`[network] scan_ports`); the hosts appear only
  when a scan ends; an agent's scan is asked about by the page or the
  agent's program, not held for an approval; ICMP needs
  `net.ipv4.ping_group_range` to include the service account's group, which
  is not set by the package. `mac_address` is written only into inventories
  written in Fleet, never into a project's; one set in the main file of the
  inventory or in `group_vars` is not looked for (the one written in
  `host_vars` wins over them); `fleet_mac.yaml` is read before the other
  files of `host_vars/HOST/`, so one of them that comes to set
  `mac_address` later wins; a host's file is moved into `host_vars/HOST/`,
  which a person editing it may not expect. A probe of an inventory holds
  `fleetctl` for some seconds and is in the journal, not the audit log; it
  takes the first IPv4 address of a name; the CMDB is as last collected.
  What `net_probe` finds on a node stays in the events of its run, not
  among the hosts of scans, and is not turned into an inventory; it has no
  NetBIOS names.
- Approvals: one approver suffices (no quorum); an approval is not notified
  to the approvers by name, only to channels; a pending run shows its
  launch time as "Started".
- Workflows: the launch takes no limit or answers of its own, nor does a
  schedule of one; a workflow with an approval at a step is never
  scheduled; the graph is drawn in columns, not with its arrows;
  `set_stats` per host is not passed on; what a step passed on is kept in
  clear and masked only for known secrets; the steps a workflow starts are
  not in the audit (its launch, cancel and resume are); its commands are
  tools of fleet-mcp like any other (deciding an approval is not); no rule
  scope for a workflow; an approval at a step is seen on the page of runs,
  not on the workflow's.
- Terminal: survey choices not pickable or completed; the lists of ids of a
  command (`--inventory-ids`, `--vault-credential-ids`) take ids, not
  names; pushed commits keep the authors of the clone (who pushed is
  audited).
- Security review (24 October), left as they are: a run directory swapped
  for a link between `fleet-unit`'s check and the start binds another
  directory (needs the account itself); the backend shares the runs' uid,
  so anything of a run it followed would reach their credentials through
  `/proc/PID/root` (the files of a run are read without following links);
  webhook bodies carry what nodes said unescaped (Slack and Discord markup,
  mentions); `http://` allowed for channels with a secret; webhook URLs,
  template `extra_vars` and the plain variables of a set visible to viewers
  (none is for secrets); an administrator who writes a template or an
  ad-hoc command can have a run print or send elsewhere a secret it takes
  (masking the output is a net); `config.export` and `inventory.get` by an
  administrator not audited; an operator can fill the 500 waiting secret
  answers for an hour; `fleet` shows run output with its colours raw
  (escape sequences of nodes); unpack jobs have no time limit.
- Review (1 November), left as they are: `fleetctl` without `SUDO_UID` is
  an administrator and a playbook can run it (gains nothing while the
  database is hidden by the tmpfs); `ct-lifecycle.sh` picks a package with
  `ls`. The readable `[projects] local_roots` and the journal's forgeable
  records: [security.md](security.md).
- Negative tests (25 October): a value typed in clear for a secret is
  masked in the audit wherever it appears, so a short one masks other text
  of that record too; the fuzzer gives one hostile value at a time, never
  two together; `chat.start`, `state.ack`, `workflow.resume` and the
  deletes of things in use stop before most of their checks in it.
