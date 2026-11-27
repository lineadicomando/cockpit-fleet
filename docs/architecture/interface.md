# The interface: language and accessibility

The page is in English or Italian, as the Cockpit session is (the
`CockpitLang` cookie, then the shell's language, then the browser's). Every
string goes through `t()`; the backend's messages, and what the nodes
write in the summary of a check, stay in English, like the terminal.

PatternFly falls back on English text of its own: labels only a screen
reader says (an alert's kind, a close button, the value of a spinner, the
menu of a row, the toggle of an expandable row), tooltips, the buttons of an
upload. `src/components/pf.tsx` wraps those components with translated text,
and pages import them from there, never from PatternFly: `Alert`,
`AlertActionCloseButton`, `Button`, `ClipboardCopy`, `FileUpload`, `Modal`,
`NumberInput`, `Pagination`, `SearchInput`, `Spinner`, `Tabs`, `TabPanel`,
`ActionsColumn`, and
`ExpandCell` for the cell that opens a row (`Td`'s own `expand` takes no
label). The wrappers also mend what an audit found:

- a modal's close button is drawn by the wrapper, since PatternFly gives it
  no label of ours; Escape still closes it;
- tabs whose content is drawn apart name it: `Tabs` takes a `panelId`, and
  the content goes in a `TabPanel` of the same id, which the tabs'
  `aria-controls` point to;
- an alert's title is a paragraph, not a heading: an alert sits at any depth
  of the page, and a heading of a fixed level broke their order;
- a `NumberInput` given an `id` puts it on the field, where the label of its
  `FormGroup` reaches it, and no name of its own over that label;
- a `FormGroup` around something that is not a field (a code editor, the
  links of a workflow step, an upload) is `role="group"`, so that its label
  names the group instead of pointing at nothing.

**Sections.** Cockpit's menu has no levels: a package can only add flat
entries under System, Tools or Apps, each an HTML file of its own in a
frame of its own (a bundle and a session loaded again for each). The page
is therefore one entry, and orders its sections itself: a row of tabs for
six groups and, under a group of more than one section, a row of subtabs
(`src/lib/sections.ts`). The URL names the section alone (`#/runs`), so
links do not depend on the grouping; a group of one section is keyed by it,
so that its tab names the content's panel. A section a role cannot see
leaves its group, and a group left empty goes. The manifest's keywords
lead from Cockpit's search to a section: a `goto` without a leading slash
is the hash of the page (`"goto": "runs"` opens `/fleet#runs`), one with a
slash would be another page; the shell gives no translation of a package's
keywords without a `po.manifest` of Cockpit's kind, so the Italian words are
among the English ones.

**Runs started and opened.** Launching a template, a workflow, a schedule,
an ad-hoc command, a sync or an upload does not open the run: the page
stays where it is, and a notice in a corner (`StartedRuns` in
`common.tsx`) links to it for a few seconds. A link to a run (`openRun`)
or to a host (`openHost`) carries the page it was followed from in the URL
(`#/runs/ID?from=templates`), and the page offers "Back" to it;
`originPath` takes only a known section, since the URL can be typed. The
page's own list needs no such link: "All runs" or "All hosts" is there
anyway.

**Ways out apart from actions.** A detail page (a run, a host, the files
of a project or an inventory) has its ways out as links above the title
(`PageNav` in `common.tsx`), the first with an arrow, and only actions in
the header on the right. With the two in one row of buttons alike, "Back"
sat beside "Cancel run", which acted at once. Actions that cannot be
taken back ask first (canceling a run, a download or a copy, with
"Keep running" rather than "Cancel" to close the dialog), and a button
that only leaves does not sit among the actions of a dialog either.

**Long lists.** Checks, job templates, host keys and the hosts of Hosts
and Assets are filtered and grouped (checks and templates also sorted) in
the browser, from what their `list` command returns: none is paged, and at
the sizes measured (hundreds of each) there is no reason to ask the
backend again for each change of a filter. The pieces they share are in
`src/lib/listview.ts` (groups, the stored view read back defensively) and
`src/components/ListView.tsx` (the filters in use as labels that remove
them, the choice of grouping with a button that opens or closes every
group, the first row of a group, nothing matching). What a user chose, the
order, and the groups opened or closed by hand, are kept in the browser's
`localStorage` under `cockpit-fleet.NAME.USER`, so that they outlive a
change of tab and a reload; a link carries none of them, and a stored
value that is not understood (an older version, an edit by hand) falls
back to the default. PatternFly's toolbar filters are not used: their
labels ("Show Filters", "Close …") are English that cannot be translated.

- Hosts (its three views) and Assets share `src/lib/hosts.ts` and, in
  `ListView.tsx`, a toolbar (`HostListToolbar`) and the bodies of a table
  in groups or not (`GroupedRows`): a text of which every word must be
  found, and groups by inventory (the default), open at first, offered
  only when there is more than one inventory. A host of two inventories
  is a row of each. The inventory chosen in the header stays a parameter
  of `host.list`, `state.list` and `cmdb.list`, so that the grid of a
  large fleet loads only the states of one.
- Job templates are grouped by inventory (the default), project, outcome
  of the last run or not at all, every group open at first; filtered by
  text (name, playbook, project, inventory, tags), project, inventory, a
  feature (an approval, questions, scheduled or not, from `schedule.list`)
  and the outcome of the last run (failed or error; under way, waiting for
  approval included; canceled, rejected or expired; successful; never
  launched). `template.list` gives each template its `last_run`, any run
  with its `template_id` (a step of a workflow too), in one query over the
  index `runs_template` (schema 19); it does not reconcile runs as
  `run.list` does, so a run whose unit died unseen shows as running until
  a page of runs is opened.
- Checks: [Checks](checks.md), "The interface".
- A row is highlighted under the pointer and while one of its controls
  has the focus (`app.scss`, the token of PatternFly's clickable rows, as
  Cockpit's lists), so that on a wide screen the actions at its end stay
  tied to its name. Not: tables of details (`borders={false}`), the row
  holding the expanded details of the one above, selected and clickable
  rows (PatternFly styles them) and the head of a group (`fleet-group-row`
  of `GroupRow`). The rows of a group are expandable rows of PatternFly,
  so that a closed group hides them, and are highlighted.

**Pages that follow changes.** Nothing tells a page that something
changed: it asks again with `usePolling` (`src/lib/hooks.ts`), which waits
for one answer before the next. Hosts every 15 s, the Overview and System
every 30 s, pending approvals (in Runs and the Assistant) every 5 s,
always; Runs, Templates, Checks, Workflows, Schedules, Notifications,
Network, and Projects and Inventories after a change, only while something
they show is not over (every 1.5–5 s). So the list of checks misses a
cycle the scheduler's tick queues (each minute) while none runs. The ways
to follow it, to choose (`TODO.md`):
- a slow poll while nothing runs (30 s), paused while the tab is hidden
  (`visibilitychange`, as `StorePage`): at most 30 s late, two calls of
  `fleetctl` a minute with the page seen;
- a reload just after the tick that follows the earliest `next_at` of a
  check's states (a new field of `check.list`): no call while nothing is
  due, but blind to a cycle someone else starts and to the end of a
  maintenance;
- systemd's signals on D-Bus (`cockpit.dbus`, `Subscribe`: the end of
  `cockpit-fleet-scheduler.service`, a new `cockpit-fleet-run@` unit): at
  once and free while idle, for every page; but a second channel beside
  `fleetctl` (a change of `overview.md`'s design), tied to the names of
  units, to be tried under SELinux.

**Choosing one of many.** Where a form chooses a job template (a
workflow's step, a schedule, a rule of notifications), a workflow, or the
target of a rule, the choice is a `SearchSelect`
(`src/components/SearchSelect.tsx`) rather than the browser's `<select>`,
which can only jump to an initial: typing narrows the list to the options
every word typed is in, in any order, with no regard to case or accents,
in the label, the line under it or the label of its group
(`src/lib/search.ts`), so `info2 wu` finds the update templates of the
inventory `spalla_info2`. Job templates are grouped by inventory, with
their project and playbook under each. Only an option of the list is
taken: leaving the field, by Escape or a click elsewhere, puts back the
one chosen, so the form still sends an id the backend checks. Escape
closes the list and not the dialog around it, and Enter chooses rather
than submits, as in `PathInput`. Short, fixed lists (verbosity, modes,
conditions) stay `<select>`.

**Accessibility.** `<html lang>` of the frame is the language of the page,
not the "en" of `index.html`, so that a screen reader reads Italian as
Italian. The placeholder of the code editor uses the theme's subtle text
colour: its own grey fell short of 4.5:1. Lighthouse and a scan of the DOM
are how it is checked (`acceptance.md`); neither judges the text of the
charts (an SVG) nor the order of focus beyond what PatternFly gives.
