# Approvals

A job template, or a step of a workflow, says whether its runs wait for an
approval and from whom (`approval`): `none`; `confirm`, a checkpoint that
who launched the run may pass too, without the right to approve, on their
own run or between two steps; `other`, another person. A step waits as the
stronger of its own mode and its template's. `needs_approval` (true is
`other`) is still taken and given, so that an older controller importing
an export keeps an approval; old steps in the options of runs are read by
`workflows.step_mode`. What waits is the run as it will be: `runs.launch`
prepares its directory as for any run (the copy of the project, the
ciphertext of its secrets, a secret answer from its ticket, `spec.json`)
and records it as `pending_approval`, which `dispatch` never starts. So
what is approved is what runs, whatever changes meanwhile, and no secret
is asked for again. Approved, it is `queued`, behind nothing (it keeps
its launch time); rejected or expired, it ends as `rejected` or `expired`
and its directory goes, ciphertext with it.

```
run.start ─▶ pending_approval ──approval.approve──▶ queued ─▶ running ─▶ …
                    │ approval.reject
                    ├──────────────▶ rejected          the directory is removed
                    │ [approvals] expire_hours (24), by the tick
                    ├──────────────▶ expired
                    │ run.cancel
                    └──────────────▶ canceled
```

**Who.** An administrator, or a member of `cockpit-fleet-approver`
whatever their role, even none: such an approver gets `whoami` and the
three commands of approvals, and the page shows them only the runs that
wait, with what each would do (template, project and revision, inventory
and limit, dry run, the answers and which secrets were given). In
`confirm`, also who launched it: `approval.approve` and `reject` need the
role operator or the right, and `approvals.may_decide` does the rest. In
`other`, not who launched it, except an administrator while `[approvals]
admins_exempt` is true (the default), who could change the template
anyway. A run of a schedule is launched by `schedule:NAME`, so anyone
allowed may approve it, and in `confirm` it waits as in `other`
(`approvals.request` stores the mode); so only an administrator turns on
a schedule of a template that needs an approval (an operator could
approve what it launches); an operator still turns one off.

**Agents.** An ad-hoc command an agent asks for waits in `confirm` for the
person it acts for (`run.adhoc`), unless diagnostic (`profiles.is_diagnostic`,
as in `_through`: a module of `[mcp] adhoc_modules`, without become and
with no template in its arguments, whose lookups would run on the
controller); it expires as the others. Nobody reads
what an agent runs before it runs, and the agent may be steered by what a
node wrote. Unless the person allowed that very call in the chat page
just before (`consent.py`, table `chat_consents`, schema 40): on "once"
to the question of `run_adhoc`, the page calls `chat.consent` (admin, no
tool) with the session of `fleet-mcp` and the arguments shown, before it
answers the agent; an agent's `run.adhoc` in that session, for that user,
with the same digest of its checked parameters, within ten minutes, uses
the consent (once) and starts with an approval already `approved` by the
person in `confirm`, comment "confirmed in the chat page". A consent
covers that very call only, not one in other words. In a session
"Without questions" ([The chat](chat.md)) the command starts at once,
`unattended` in its options, with no row of approval. `run.result` gives
a waiting run as not done, with its mode, at once. An agent never
approves nor confirms, not even its user's run: a call made for one has
no rights, and the commands refuse it whatever its role; `approval.list`
is a tool, deciding is not. At a terminal, through `fleet`, a person does.

**Records.** The table `approvals` (schema 16) has one row per run that
waited: who asked and when, until when, the mode (schema 24), the
decision, who took it and when, a comment. A decision is claimed by a
conditional update: two approvers at once give one decision and a
conflict. Every decision of a person is audited; an expiry is in the
journal. `run.get` carries the approval and whether the caller may
decide.

**Telling.** A rule may ask for `approval`: a run that starts waiting is
told to its channel, with a link to it. A rejected or expired run is told
as `canceled`.
