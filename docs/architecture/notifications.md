# Notifications

A rule names the ends of a run to tell about (`failed`, which covers `error`
too, `changed`, `succeeded`, `canceled`, which covers a run rejected or
expired before it started), a run that waits for approval (`approval`), which runs (every one, or those of
one project, template, schedule or inventory) and a channel; what a rule
says about checks, and about check cycles falling behind (`late`,
`caught_up`) or that cannot be prepared (`unprepared`, `prepared`), is in [Checks](checks.md). Channels are Telegram, a
webhook, mail (SMTP) and Matrix, written with the standard library. Only
playbook runs and ad-hoc commands are told about, not the jobs that serve a
project.

```
reconcile → _finish(run)  ── rules ──▶ notifications (the outbox, one row per channel)
                                           │ writes /var/lib/cockpit-fleet/notify.wake
cockpit-fleet-notify.path ─────────────────┘
   ▼
cockpit-fleet-notify.service    User=cockpit-fleet, secrets/notify/ decrypted by PID 1
   │ fleetctl notification.deliver
   ▼
Telegram, webhook, SMTP, Matrix
```

**Waking without privilege.** The service account cannot start units but
run units, through `fleet-unit`. A path unit needs nothing of it: writing
`notify.wake` is enough, and systemd 257 starts the service again when the
file was written while it ran (tried on the VM). Retries need no timer of
their own: the tick of the scheduler writes the file when a notification's
time to be tried again has come. No root code was added.

**Secrets.** A channel has one secret (a bot token, an access token, a
password; optional for a webhook and for mail), stored with `fleet-secret`
under the name `n-<channel>-token`. `fleet-secret` keeps the names that begin
with `n-` in `/etc/cockpit-fleet/secrets/notify/`, and the unit has
`LoadCredentialEncrypted=notify:` on that directory: PID 1 decrypts the
secrets of the channels and nothing else, into
`notify_n-<channel>-token.cred` (systemd keeps the extension). `fleetctl
notification.deliver` refuses to run without `CREDENTIALS_DIRECTORY`, so
only the unit delivers. A secret is never in the database, the journal or
an error: Telegram's token is part of the URL, and every error text is
masked before it is stored. A webhook URL may say `$secret`, for the
services that put the token in the address (Slack, Discord); otherwise the
secret goes in a header the channel names.

**The outbox.** One row per run and channel, however many rules match (a
workflow run and each of its steps are runs of their own); a
run with changes that a rule wants as `changed` and another as `succeeded`
is told once, as `changed`. Channels are tried one after the other in a
delivery, each step of a request waiting 15 s at most, so one that does not
answer delays those after it by that much, and is not tried again in the
same delivery.
A failure is tried again after 1, 2, 4 … minutes, at most an hour apart,
for a day, then given up (`failed`), which an administrator can try again.
Matrix gets the same transaction identifier when the same message is tried
again, so a request that arrived but whose answer was lost is not posted
twice. A redirect is an error, not followed: it would carry a token to
another host. Anything else that goes wrong while sending (a server that
does not speak HTTP, a header a secret makes invalid) is a failed attempt
like the others, said by the class of the error only, as its text may quote
the secret; before, it stopped the whole delivery, every tick. An error
keeps an API's own reason, never the raw body of an answer (viewers read
errors, and the URL may be one of the network's own). A mail's subject is
made one line: a host name from an inventory may hold a line break. A
secret with a control character, or with a space for Telegram and Matrix,
is refused before anything is sent. Ended notifications are kept 90 days.

**Grouping and limit.** `window` (seconds, per channel) makes a run's
notification wait: whatever else ends meanwhile leaves in the same message.
Beyond `max_per_hour` messages in the last hour, notifications wait until
one more may go and then leave as one. A test message (`channel.test`) goes
at once and alone.

**Messages.** English or Italian, chosen per channel, written in the backend
(`notify._WORDS`): the outcome, template, project, schedule, who launched,
hosts by outcome and which failed, and a link to the run,
`<[notifications] url>/fleet#/runs/ID` (by default
`https://<fqdn>:9090`). A webhook's body is a JSON template whose strings
have `$title`, `$text`, `$link`, `$count` replaced; a string that is only
`"$events"` becomes the list of events. Substitution happens inside the
parsed strings, so nothing in a message can change the shape of the JSON.

**The unit** is the backend unsandboxed as `cockpit-fleet-finish@` is,
plus what a program that only talks to the network can bear:
`RestrictAddressFamilies`, `@system-service`, no capabilities, no access to
runs and projects. `systemd-analyze security` rates it 1.5. HTTP goes through
`[network] proxy`; mail goes straight to its server.

**Roles.** An administrator creates channels and rules, sets secrets, sends
tests and tries again; anyone with a role sees the channels (not their
secrets), the rules and the notifications. Channels and rules are in the
exported document, without secrets.

Checked on Debian 13 (`notify.sh`): a failed run reaches a Telegram Bot API,
a webhook, an SMTP server and a Matrix homeserver (stand-ins on the VM,
`notify-sinks.py`), each with its secret where it belongs and in neither the
database nor the journal; a channel whose server was down got the
notification about 90 s later, from the tick, once it answered.
