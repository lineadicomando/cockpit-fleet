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
`AlertActionCloseButton`, `ClipboardCopy`, `FileUpload`, `Modal`,
`NumberInput`, `SearchInput`, `Spinner`, `Tabs`, `ActionsColumn`, and
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

**Long lists.** Checks and job templates are filtered, grouped and sorted
in the browser, from what their `list` command returns: neither is paged,
and at the sizes measured (hundreds of each) there is no reason to ask the
backend again for each change of a filter. The pieces they share are in
`src/lib/listview.ts` (groups, the stored view read back defensively) and
`src/components/ListView.tsx` (the filters in use as labels that remove
them, the choice of grouping with a button that opens or closes every group,
the first row of a group, nothing matching). What a user chose, the order,
and the groups opened or closed by hand, are kept in the browser's
`localStorage` under `cockpit-fleet.NAME.USER`, so that they outlive a
change of tab and a reload; a link carries none of them, and a stored
value that is not understood (an older version, an edit by hand) falls
back to the default. PatternFly's toolbar filters are not used: their
labels ("Show Filters", "Close …") are English that cannot be translated.

Job templates are grouped by project (the default), inventory, outcome of
the last run or not at all, every group open at first; filtered by text
(name, playbook, project, inventory, tags), project, inventory, a feature
(an approval, questions, scheduled or not, from `schedule.list`) and the
outcome of the last run (failed or error; under way, waiting for approval
included; canceled, rejected or expired; successful; never launched).
`template.list` gives each template its `last_run`, any run with its
`template_id` (a step of a workflow too), in one query over the index
`runs_template` (schema 19); it does not reconcile runs as `run.list` does,
so a run whose unit died unseen shows as running until a page of runs is
opened.

`<html lang>` of the frame is the language of the page, not the "en" of
`index.html`, so that a screen reader reads Italian as Italian. The
placeholder of the code editor uses the theme's subtle text colour: its
own grey fell short of 4.5:1.

Checked on 25 October on the Debian VM, in Italian, with data from
`checks.sh` and a project, a template and a schedule: Lighthouse scores
accessibility 100 on every tab, on the page of a host with a chart open, on
a run, on the modal of a new template, and in the dark theme; a scan of
the DOM on every tab and on each modal for a new item finds no English text
of PatternFly, no label pointing at nothing and no field without a name.
What Lighthouse does not judge: the text of the charts (an SVG), and the
order of focus beyond what PatternFly gives.
