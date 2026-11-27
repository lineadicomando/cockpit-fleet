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
| An operator | launch what administrators defined, on fewer hosts than the template says, with answers to its survey; put files in the store, which playbooks may install on nodes; write, move and delete documents | widen a template's hosts, change what it runs, or have an answer read as code |
| A viewer | see what exists and how runs ended, and download the files of the store; read and search the documents | read the output of runs, which may hold data of the nodes |
| An approver | let a run that waits for approval start, or refuse it | approve their own launch where another person is asked for (an administrator may, unless `[approvals] admins_exempt = false`) |
| A node | answer what it is asked | report for another host or another check, or have its answers read as code |

An administrator of Fleet is not an administrator of the machine: their
group reaches the backend through sudo as the service account and nothing
else. An administrator of the machine can, of course, do anything.

## The layers

1. **Cockpit** authenticates the person. Fleet listens on no port of its
   own.
2. **sudo** runs `fleetctl` as the service account for members of the four
   groups only, and sets `SUDO_UID`, which the caller cannot choose. The
   caller's environment does not reach it.
3. **fleetctl** finds the role from the groups of that account, checks it
   against the least role every command declares, before any of the
   command's code runs, and checks every parameter against the command's
   declaration. The page hides what a role cannot do, but that is a
   convenience: the backend decides.
4. **Two programs run as root**, short ones: `fleet-unit` starts or stops
   one instance of the packaged run unit, `fleet-secret` stores, deletes
   or lists secrets and keeps an operator's secret answer for a run. Each
   takes a verb and at most one checked identifier: no path, no option, no
   unit name ([overview.md](architecture/overview.md)).
5. **Each run is a systemd unit**, under the service account, sandboxed:
   it sees its own directory, its own decrypted secrets and the network;
   not the database, the projects the backend keeps, other runs, the
   system bus or `/home`. Each run has its own process and IPC namespaces,
   so that one cannot reach the secrets of another through its processes,
   though all run as the same account; sudo and setuid programs do nothing
   inside it. Its outcome is the runner's, not the playbook's: nothing of
   a run outlives it, and none of its processes can change whether it
   succeeded. What else it reaches is below.
6. **Ansible's command line is built by the backend**, never by the page,
   from validated values: a host pattern, a tag, a user name cannot become
   an option. Survey answers reach Ansible as data, tagged so that it never
   renders them as templates. SSH accepts only host keys an administrator
   has trusted. An executable file of a project is read as text, not run
   as an inventory script, unless `[inventories] scripts = true`; then it
   runs in the sandbox like the rest of the project.

## What a run reaches beyond its own

- **Local projects**: a run works on a copy of its project, but the
  directories of `[projects] local_roots` stay readable in the sandbox,
  as the rest of the machine outside `/home`, so a playbook of any
  project can read every local project. Where that matters, hide them
  from runs with a drop-in ([install.md](install.md)).
- **The store** is given to every run of a playbook or an ad-hoc command,
  as hard links in its own directory: any playbook reads any file of it,
  and what it adds or replaces there is kept when it ends. It is for
  installers and the like, never for secrets (keys, licences that must
  not travel): keep those in credentials or vault files. An operator or a
  playbook may replace an installer that other templates run as
  administrator on the nodes: give the operator role to people you would
  trust with that, and check digests in playbooks (`checksum:` of
  `get_url`, `copy`) where it matters.
- **Downloads into the store**, by an operator, run in the sandbox of a
  run and refuse the controller's own addresses (loopback and those of
  its interfaces, where Cockpit listens) and link-local ones (cloud
  metadata), checked where the connection is made. Other machines of the
  networks the controller reaches are not refused: mirrors live there,
  and so may internal services, which an operator could then read
  through the store. Where that matters, give the controller a proxy
  (`[network] proxy`) that allows only what downloads need. A file
  checked against its URL again (by hand, or every day, week or month)
  is replaced by what the URL then serves, for the next runs: give a
  digest (`sha256`) to a file that must not change.
- **The store over SFTP** (`fleet store sftp`) needs an SSH login to the
  controller and gives that person what their role gives them in the
  page, through the same `fleetctl` and audit trail: it opens no port,
  and an account that may log in over SSH could already call `fleetctl`.
  What it writes reaches runs only when it is closed.
- **The documents** are not given to runs: they are under the state
  directory that the sandbox hides. Every viewer reads them, so they are
  no place for secrets either. The backend renders them, escaping any
  HTML, letting links go only to http(s), mailto, Fleet and other
  documents, and showing only images among the documents; the page shows
  the result in a frame where no script runs, under a policy that lets in
  only those images. A document an agent wrote cannot make the browser of
  whoever reads it fetch anything.

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
  `fleet-secret` chooses, and serves one run. That name, the ticket,
  launches a run with the secret until a run takes it, so it is in no
  record: `fleet-secret` logs that an answer was stored, not its name, and
  the audit trail masks every ticket in the parameters of a call. A
  secret typed where a ticket was expected (in the answers of a launch,
  of a schedule, of a step of a workflow, as a default) is refused, and
  masked in the record of the refusal.
- The secret variables of a set of variables are stored the same way, by an
  administrator; `varset.list` says which are set, never a value, and a
  run that takes them gets them as a file of its runtime directory.
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
who starts it, and the chat page starts one for the user who opens it; both
are off as installed. How they are turned on, and what each profile allows:
[chat.md](chat.md). What holds whatever the agent does:

- An agent never has more than its user's role, nor more than `[mcp]
  max_role` (admin by default: operator keeps agents from ad-hoc commands
  and changes), nor more than its profile (`[chat] profiles`). The
  profile is held by `fleetctl`, not by the agent or the page: one that
  names another, or none, gets no more than the default one. It approves
  nothing, and no tool reads or writes a secret.
- The output of runs is written by the nodes: an agent that reads it and
  may also change things can be steered by what a node writes, which is
  why the limits are there.
- The profile `autonomous` launches a bounded number of runs in a session
  (more only when its person chooses or allows them on the page:
  `chat.limit_runs`, `chat.grant_runs`, which no agent may call), nothing
  on an inventory in maintenance, and cannot silence a problem; a run that
  needs approval still waits for a person.
- An ad-hoc command of a module that may change something waits until
  the person confirms it (`chat.consent`, which no agent may call),
  whatever the profile; except where `[chat] unattended = true` allows
  that person to turn the questions off for the conversations of a
  session in the profile `administrator` (`chat.unattended`, audited):
  then ad-hoc commands, changes and deletions run with nobody reading
  them first.
- Accepting the risks of a profile beyond `assisted` (`chat.accept`,
  audited, required by `chat.start`) is consent, not a control; so are
  the questions of the page or of the agent's program at a terminal: the
  backend decides each call again for the profile (`profiles.question`),
  whatever was answered.
- Files an agent writes in a project or inventory written in Fleet go to
  a draft of its session, which no run reads, until a person merges it.
  Documents are not drafts: every profile may write them, asked as a
  launch is (a deletion every time, but in `autonomous`), `autonomous`
  at most `[chat] autonomous_writes` times a session; each change is in their history
  with the person as author and in the audit log as coming through an
  agent, so a text can be put back.
- Skills (the `.fleet/skills/` of projects and their collections,
  `/etc/cockpit-fleet/skills/`) are read by every role, sent to the
  provider of the model, and guide an agent without holding it: whoever
  may write a project's files or a collection it installs can steer the
  assistant through them, as they can its playbooks. A skill of the
  documents (`skills/NAME/`) whose last change came from an agent is out
  of the index and of `skill_read` until an operator or an administrator
  approves it, which no agent can do.
- The agent of an administrator may scan a subnet in the profiles
  `diagnostic` and `administrator`, asked every time, within the subnets
  below.

## Scans of the network

An administrator of Fleet, and their assistant once they confirm, may
scan a subnet from the controller (Nodes, Network): an ICMP echo and a
TCP connection to a few ports for each address, as a run in the sandbox,
recorded in the audit log. Only subnets the controller is attached to
(`[network] scan_connected`, on as installed) or those listed in
`[network] scan_subnets` may be scanned, of at most
`[network] scan_max_addresses` addresses; turn `scan_connected` off and
leave the list empty to allow none. What a scan finds (addresses, names,
MAC addresses, open ports) is shown to administrators only; it reaches
the provider of the model when the assistant reads it. An intrusion
detection system on the network may take a scan for an attack: tell
whoever watches it.

An operator, and the assistant in any profile, may
probe the hosts of an inventory the same way (Inventories, "Probe the
hosts"): only the hosts an administrator put there, at most 256 at a
time, recorded in the journal. The module `lineadicomando.fleet.net_probe`
does the same from a node, as an ad-hoc command: on the subnets that may
be scanned or the hosts of the inventory only, with the ports of
`[network] scan_ports`; it is among the modules the assistant runs
unasked in the profile `diagnostic`, which changes nothing on the nodes:
remove it from `[mcp] adhoc_modules` to keep it from agents.

## What it does not do

What the design leaves to the administrator, none of which lets a role do
more than this page says:

- All runs share one account. A run is kept from the others by its sandbox,
  not by a user of its own; a controller is one domain of trust, and nodes
  that must not share administrators should have controllers of their own.
- The backend runs as that same account, so the sandbox is what keeps a
  playbook from the database; a flaw in the sandbox would be a flaw here.

The known limits, of security and of every other area, are in
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
archives, git and bundles was done on 24 October 2026, and a review of
the whole project on 16 November 2026; what they left as it is, is in
[limits.md](limits.md), and what the second left to fix, in the plan of
the repository (`TODO.md`).
