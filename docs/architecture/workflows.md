# Workflows

A workflow chains job templates into a graph. A step is a template with a
limit that narrows its own and answers to its survey (no secret: as for a
schedule, a template with a required secret question cannot be a step), and
may wait for an approval even if its template does not ask for one. A link
says when a step runs after another: when it succeeds, fails, or always. A
step with no link to it runs at launch; one with several waits until all the
steps before it have ended, then runs if one of its links holds, or is
skipped, and so are those that only follow it. Loops are refused, so a step
runs at most once.

**Runs.** `workflow.launch` (operator) records a run of kind `workflow`.
It has no unit and no place in the queue (the lanes of `dispatch` leave it
out); it holds a copy of the steps and links, so that changing the workflow
does not change a run under way. Its steps are runs of their templates,
launched through `runs.launch` as the user who launched the workflow, with
`workflow_run_id` and `workflow_node`; they queue, wait for approvals and
are told about as any other run.

```
workflow.launch ─▶ run (kind workflow, running) ─▶ advance()
                                                   │ steps whose links hold
                                                   ▼
                        runs.launch(step) ─▶ queued/pending_approval ─▶ … ─▶ _finish
                                                   ▲                          │
                                                   └──────── advance() ◀──────┘
```

`workflows.advance` looks at the latest run of each step, starts those whose
time has come and records the end when nothing is left: it is called when a
step ends (`jobs._finish`), at launch, and for every workflow still running
by `reconcile`, so the tick takes up one left behind by a crash. One process
at a time per workflow run (a lock file in its directory); a step that ends
while the same process advances it (its unit could not start) asks for
another pass. A step that cannot even be launched (its template changed and
the stored answers no longer fit, its project has no files) counts as
failed, with the reason kept in the run. The workflow ends `failed` when a
step failed (or was rejected, expired, canceled) and no step runs for that
failure: a link "always", such as one to a step that tells about the
outcome, does not handle it. Otherwise `successful`.

**Passing on.** A playbook that calls `set_stats` with `per_host: false`
leaves data that the callback writes with the stats of the run
(`artifacts`), and `_collect` keeps it with the run (at most 64 kB of
JSON). A step gets what the steps before it in the graph passed on, merged
in the order they ended, as an extra-vars file of its own, after the
template's variables and the answers of its node: what was passed on wins,
as in AWX. A playbook wrote it, so every string at every depth is tagged
`!unsafe` and is never rendered as a template in the next step. It is kept
in clear, like extra variables: not for secrets, which only the known ones
are masked in.

**Cancel and resume.** Canceling a workflow run (`run.cancel`) asks for it,
cancels its steps under way and starts nothing more; it ends `canceled` once
they have. `workflow.resume` (operator), on a run that failed or was
canceled, makes a new run with the same steps and links as that one, where
the steps that succeeded there count as done, with what they passed on, and
the others run. The run it resumes stays as it ended.

**Roles and records.** An administrator creates, changes and deletes; an
operator launches, cancels and resumes; a viewer sees the workflows and the
progress of their runs (`workflow.progress`). A template used by a workflow
cannot be deleted (table `workflow_templates`). Workflows are in the
exported document, their steps naming templates by name. Schema 17.
