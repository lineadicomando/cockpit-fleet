# Security model

What cockpit-fleet protects, from whom, and what it leaves to the
administrator. How each part is built and tested is in
[architecture.md](architecture.md); how to report a vulnerability is in
[SECURITY.md](../SECURITY.md).

## Who is trusted with what

Whoever can launch a playbook on a node is, in practice, root on that node:
Ansible exists to change machines. So the question is not whether Fleet's
users can harm the nodes, but who can make Ansible do what, and what else
they can reach.

| Who | Is trusted to | Cannot |
|---|---|---|
| An administrator of Fleet (`cockpit-fleet-admin`) | decide what runs where: projects, inventories, credentials, templates, checks, ad-hoc commands | become root on the controller, read a secret back, or reach the database outside the backend's commands |
| Whoever writes a project | write code that runs, in the sandbox of a run, with the credentials of the templates that use it | reach other projects (but see the note on local directories below), other runs, their secrets, or the controller beyond the sandbox |
| An operator | launch what administrators defined, on fewer hosts than the template says, with answers to its survey; put files in the store, which playbooks may install on nodes | widen a template's hosts, change what it runs, or have an answer read as code |
| A viewer | see what exists and how runs ended, and download the files of the store | read the output of runs, which may hold data of the nodes |
| An approver | let a run that waits for approval start, or refuse it | approve their own launch where another person is asked for (an administrator may, unless `[approvals] admins_exempt = false`) |
| A node | answer what it is asked | report for another host or another check, or have its answers read as code |

An administrator of Fleet is not an administrator of the machine: their
group reaches the backend through sudo as the service account and nothing
else. An administrator of the machine can, of course, do anything.

## The layers

1. **Cockpit** authenticates the person. Fleet listens on no port of its own.
2. **sudo** runs `fleetctl` as the service account for members of the four
   groups only, and sets `SUDO_UID`, which the caller cannot choose. The
   caller's environment does not reach it.
3. **fleetctl** finds the role from the groups of that account, checks it
   against the least role every command declares, before any of the
   command's code runs, and checks every parameter against the command's
   declaration. The page hides what a role cannot do, but that is a
   convenience: the backend decides.
4. **Two programs run as root**, a few dozen lines each: `fleet-unit` starts
   or stops one instance of the packaged run unit, and `fleet-secret`
   encrypts a value read from its input. Each takes one identifier and
   nothing else: no path, no option, no unit name.
5. **Each run is a systemd unit**, under the service account, sandboxed.
   It sees its own directory, its own decrypted secrets and the network;
   not the database, the projects the backend keeps, other runs, the
   system bus or `/home`. The directories of `[projects] local_roots` are
   the exception: a run works on a copy of its project, but the roots
   themselves stay readable in the sandbox, as everywhere else on the
   machine outside `/home`, so a playbook of any project can read every
   local project. Where that matters, hide them from runs with a drop-in
   (`docs/install.md` says how).
   The store is given to every run of a playbook or an ad-hoc command, as
   hard links in its own directory: any playbook reads any file of it, and
   what it adds or replaces there is kept in the store when it ends. It is
   for installers and the like, never for secrets (keys, licences that must
   not travel): keep those in credentials or vault files. Since an operator
   or a playbook may replace an installer that other templates run as
   administrator on the nodes, give the operator role to people you would
   trust with that, and check digests in playbooks (`checksum:` of
   `get_url`, `copy`) where it matters. An operator may also have the
   controller download a URL into the store: the job runs in the sandbox
   of a run, and refuses to connect to the controller's own addresses
   (loopback and those of its interfaces, where Cockpit listens) and to
   link-local ones (cloud metadata), checked where the connection is made.
   Other machines of the networks the controller reaches are not refused:
   mirrors live there, and so may internal services, which an operator
   could then read through the store. Where that matters, give the
   controller a proxy (`[network] proxy`) that allows only what downloads
   need. A file downloaded from a URL may be checked against it again,
   by hand or every day, week or month: what the URL serves then replaces
   it and reaches the next runs, so give a digest (`sha256`) to a file that
   must not change, which then refuses another version.
   Each run has its own process and IPC namespaces, so that one run cannot
   reach the secrets of another through its processes, though all run as
   the same account. sudo and setuid programs do nothing inside it.
6. **Ansible's command line is built by the backend**, never by the page,
   from validated values: a host pattern, a tag, a user name cannot become
   an option. Survey answers reach Ansible as data, tagged so that it never
   renders them as templates. SSH accepts only host keys an administrator
   has trusted.

## Secrets

- A secret is typed in the page (or given on standard input to `fleet`) and
  goes to `fleet-secret`, which encrypts it with `systemd-creds`: the host
  key of the machine, and its TPM when it has one. There is no way to read
  it back, for anybody.
- The service account can read the ciphertext and cannot decrypt it. Only
  PID 1 does, for the one run that needs it, into a directory mounted for
  that unit alone and removed with it. Ansible gets it as a file; it never
  appears in a command line, the environment, the database, the journal or
  an answer of the backend.
- The secret answer to a survey goes the same way, under a name
  `fleet-secret` chooses, and serves one run. A secret typed where a ticket
  was expected (in the answers of a launch, of a schedule, of a step of a
  workflow, as a default) is refused, and masked in the record of the
  refusal.
- What a run prints is masked for the secrets it was given, as a net, not as
  a guarantee: a playbook that prints a secret in another encoding, or one
  it decrypts itself, is not masked.
- Notification channels have a secret each (a token, a password), kept
  apart: only the unit that delivers notifications has them decrypted.

What this guarantees is narrower than it may sound. The service account
decides which ciphertext goes to which run, so whoever controls that
account, or writes the code of a project used with a credential, can make a
run use that credential. Secrets are safe at rest, in backups and from the
page; they are not safe from a malicious administrator of Fleet.

## What is recorded

Every command that changes something, and every refusal (by role, by the
limit on agents, or by the command itself), is in the audit log (the tab
"Audit log", for administrators) and in the journal
(`journalctl -t cockpit-fleet`, with `FLEET_USER`, `FLEET_COMMAND`,
`FLEET_OUTCOME`), with who did it, through what (the page, `fleet` at a
terminal, an agent), and the parameters, secrets left out. `fleet-secret`
logs which secret was set or deleted, never the value. The database is the
record; the journal is a copy for convenience, and any local account can
send the journal a record with the same identifier, so when reading it
add `_UID=$(id -u cockpit-fleet)`, a field the journal sets itself.

## Agents

`fleet-mcp` offers the backend's commands as tools to an agent, as the user
who starts it, and the chat page starts one for the user who opens it
([chat.md](chat.md)). Both are off as installed (`[mcp]` in `config.toml`).
An agent never has more than that user's role, nor more than
`[mcp] max_role` (operator by default), nor more than its profile of the
assistant allows (`[chat] profiles`: as installed, reading, and launching
what is defined); it approves nothing, and no tool reads or writes a
secret. The output of runs is written by the nodes: an agent that reads it
and may also change things can be steered by what a node writes, which is
why the limits are there. A profile is held by `fleetctl`, not by the agent
or the page: one that names another, or none, gets no more than the
default one. An agent that acts without asking (the profile `autonomous`,
off as installed) launches a bounded number of runs in a session, nothing
on an inventory in maintenance, and cannot silence a problem; a run that
needs approval still waits for a person. An ad-hoc command an agent asks
for, of a module that may change something, waits until the person it acts
for confirms it, whatever the profile. A user accepts the risks of a profile
beyond `assisted` once before a session in it (`chat.accept`, audited), and
`chat.start` refuses until then; that is consent, not a control. Neither
are the questions of the page or of the agent's program at a terminal:
what they let through without asking is decided by the backend for the
profile (`profiles.question`), and the backend decides each call again
whatever was answered. Files an agent writes in a project or inventory
written in Fleet go to a draft of its session, which no run reads; only a
person merges or discards it, after seeing what it changes.

## What it does not do

Known limits, none of which lets a role do more than this page says:

- All runs share one account. A run is kept from the others by its sandbox,
  not by a user of its own; a controller is one domain of trust, and nodes
  that must not share administrators should have controllers of their own.
- The backend runs as that same account, so the sandbox is what keeps a
  playbook from the database; a flaw in the sandbox would be a flaw here.
- Webhook bodies carry what nodes said as it is (a Slack or Discord message
  may get their markup); `http://` is allowed for a channel with a secret;
  webhook URLs, the extra variables of a template and the sets of variables
  are visible to viewers (none is meant for secrets).
- `config.export` and `inventory.get` by an administrator are not audited.
- An operator can fill the 500 places for waiting secret answers for an
  hour, which would hold up other launches that need one.
- `fleet` shows the output of a run with its colours, escape sequences of
  the nodes included.
- A failed run leaves its unit failed until `systemctl reset-failed`, which
  makes the system "degraded" meanwhile.

The full list, with what is left in every area, is in
[limits.md](limits.md).

## Advice

- Keep `cockpit-fleet-admin` small: it decides what runs on every node.
- Give templates credentials with no more rights than they need, and
  `become` passwords rather than passwordless sudo on the nodes.
- Trust host keys only after checking their fingerprints.
- Review what a project's code does before a template uses it with a
  powerful credential; a project of the interface keeps its history, with
  who changed what.
- Leave agents off unless their use is understood: what they read goes to
  the provider of their model.

## How it is tested

Unit tests take every command of the backend in turn: called with a role
below its own, by an agent above its limit, and with values made to become
options, leave a directory, run a shell or render a template, and check
that nothing gets through and no secret comes out
(`backend/tests/test_negative.py`). Integration scripts run as root on a
controller with the package installed, in containers of the three
distributions and on machines: what the service account can reach
(`privileges.sh`), what a run can reach (`sandbox.sh`), roles and sudoers
(`roles.sh`), and hostile input through sudo as each role
(`negative.sh`). A review of the root helpers, the sandbox, sudoers,
archives, git and bundles was done on 24 October 2026; what it fixed and
what it left is in the TODO.
