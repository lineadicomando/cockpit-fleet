# Approvals

A job template, or a step of a workflow, says whether its runs wait for an
approval and from whom (`approval`): `none`; `confirm`, a checkpoint that
who launched the run may pass too, without the right to approve, on their
own run or between two steps; `other`, another person. A step waits as the
stronger of its own mode and its template's. `needs_approval`, the yes or no
of before (schema 24 turned it into `other`), is still taken and given:
true is `other`, and an export carries both, so that an older controller
importing it keeps an approval, in `other` where it was `confirm`. Steps
kept in the options of runs from before are read as they were
(`workflows.step_mode`). What waits is the
run as it will be: `runs.launch` prepares its directory as for any other
run (the copy of the project, the ciphertext of its secrets, a secret answer
taken from its ticket, `spec.json`) and records it as `pending_approval`,
which `dispatch` never starts. So what is approved is what runs, whatever
changes meanwhile in the template or the project, and no secret has to be
asked for again. Approving it makes it `queued`, behind nothing (it keeps
the time it was launched); rejecting it, or letting it expire, ends it as
`rejected` or `expired` and removes its directory, ciphertext with it.

```
run.start ─▶ pending_approval ──approval.approve──▶ queued ─▶ running ─▶ …
                    │ approval.reject
                    ├──────────────▶ rejected          the directory is removed
                    │ [approvals] expire_hours (24), by the tick
                    ├──────────────▶ expired
                    │ run.cancel
                    └──────────────▶ canceled
```

**Who.** An administrator, or a member of `cockpit-fleet-approver` whatever
their role, even none: an approver who is neither operator nor viewer gets
`whoami` and the three commands of approvals, and the page shows them only
the runs that wait, with what each would do (template, project and revision,
inventory and limit, dry run, the answers and which secrets were given).
In `confirm`, also who launched it: `approval.approve` and `reject` need
the role operator (who can launch) or the right, and `approvals.may_decide`
does the rest. In `other`, not who launched it, except an administrator
while `[approvals] admins_exempt` is true (the default), who could launch
the same playbook without the approval anyway by changing the template. A
run of a schedule is launched by `schedule:NAME`, so anyone allowed may
approve it, and one in `confirm` waits as in `other` (`approvals.request`
stores the mode it waits in); for that, only an administrator turns on a
schedule of a template that needs an approval (an operator who did could
approve what it launches, one person doing both). An operator still turns
one off. An ad-hoc command an agent asks for waits in `confirm` for the
person it acts for (`run.adhoc`), unless it is diagnostic (a module of
`[mcp] adhoc_modules`, without become), and expires as the others; nobody
reads what an agent runs before it runs, and the agent may be steered by
what a node wrote. `run.result` gives a run that waits as not done, with
its mode, at once. An agent never approves nor confirms, not even a run it
launched for its user: a call made for one has no right, and the commands refuse it
whatever its role; `approval.list` is a tool, deciding is not. Through
`fleet` at a terminal, a person does.

**Records.** The table `approvals` (schema 16) has one row per run that
waited: who asked and when, until when, in which mode (schema 24), the
decision, who took it and when, and a comment. A decision is claimed by a conditional update, so two
approvers at once give one decision and a conflict. Every decision made by
a person is in the audit trail; an expiry is in the journal. `run.get`
carries the approval of its run and whether the caller may decide.

**Telling.** A rule may ask for `approval`: a run that starts waiting is
told to its channel, with a link to the run. A rejected or expired run is
told as `canceled`.
