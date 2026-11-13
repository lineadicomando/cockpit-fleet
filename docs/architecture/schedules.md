# Schedules

A schedule launches a job template at the times of an OnCalendar expression,
with a limit, a dry run and answers to the survey stored with it. There is
one static timer, `cockpit-fleet-scheduler.timer`, every minute on the minute,
and its service runs `fleetctl scheduler.tick` as the service account, like
`cockpit-fleet-finish@`: the caller is `system`, with the admin role. A unit
per schedule was discarded: root would have to write units from what a user
typed, and the queue and its limit are needed anyway. The package enables
the timer and starts nothing else; `selfcheck` warns when it is not active.

**Times.** The expression is read by `systemd-analyze calendar`, so it means
here what it means in a unit, and a time zone of the tz database may follow
it (`timezone`, empty for the controller's own). The answer is read in UTC
(the `(in UTC):` lines, under `LC_ALL=C.UTF-8`); the same output was checked
on systemd 257 and 259. `next_at` is computed when a schedule is saved or
turned on, and by the tick from the current time after each time it handles;
a random delay, if asked for, is added to it then, so the list shows the time
it will really go. An expression with no time left is refused when saved.
`schedule.preview` gives the next times of an expression before it is saved,
and converts a crontab line: the five fields, names of days and months,
steps and the `@daily` family; a line that restricts both the day of the
month and the day of the week is refused, since cron runs when either
matches and OnCalendar wants both.

**The tick.** Under a lock (`scheduler.lock` in the state directory, taken
without waiting: a second tick does nothing), it first calls `reconcile`, so
a run lost by a reboot is recorded and a queued run left by it is started.
Then, for each schedule that is due, highest priority first, it moves
`next_at` on with a conditional update (a schedule changed meanwhile by an
administrator is left to the change, and a launch that fails in a way nobody
foresaw is not retried every minute) and then:

- more than five minutes late (`MISSED_AFTER`) means the controller was off:
  `missed`, unless the schedule catches up, in which case it is launched once
  for all the times lost;
- if a run of the same schedule is still waiting for approval, queued or
  running, `overlap` decides:
  `skip` (the default), `queue` this one too, or `cancel` the previous one;
- otherwise it is launched through `runs.launch`, the code of `run.start`,
  with the stored answers. The run's `created_by` is `schedule:NAME` and it
  carries `schedule_id` and the priority.

What happened is kept with the schedule (`last_outcome`, `last_error`,
`last_run_id`) and sent to the journal (`FLEET_SCHEDULE`, `FLEET_OUTCOME`).
The tick is not a mutating command: the audit trail has who created,
changed, turned on or off and ran a schedule, not one record per minute.

**Roles.** As for templates: an administrator creates, changes and deletes;
an operator turns a schedule on or off and runs it now (`schedule.run`, as
the operator, with the stored answers, without moving its times); a viewer
sees the list. A template that a schedule uses cannot be deleted.

**Answers.** They are checked against the survey when the schedule is saved,
and again at each launch, as the template may have changed since: then the
time is recorded as `error`, with the reason, and the schedule goes on. A
secret is never stored: a template with a required secret question cannot be
scheduled, and an optional one is left unanswered.
