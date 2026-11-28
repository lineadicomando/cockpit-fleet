# The terminal

`fleet`, in `/usr/bin`, does from a terminal what the page does, for whoever
is in a role group: Cockpit's own terminal, an ssh session, a script. It is
the third client of `fleetctl`, after the page and `fleet-mcp`, and it is
built the same way: it runs as the user with no privilege of its own, and
every command is one call to `fleetctl` through sudo, with `"via": "cli"`.
So roles, validation and the audit trail are the interface's, a viewer still
does not read the output of a run, and the audit trail and the journal say
the call came from a terminal. `cli` is not an agent's `via`: the ceiling of
`[mcp]` does not apply, as it does not to a person at the page.

```
fleet run list --status failed      ──▶ sudo -u cockpit-fleet fleetctl run.list
fleet launch "Say hello" -a k=v     ──▶ template.list, run.start, run.log…, run.result
fleet secret vault vault_password   ──▶ credential.list, then sudo fleet-secret set NAME < value
```

**Commands from the registry.** Every command of `fleetctl` is `fleet`
followed by its area and verb, with its declared parameters as options typed
as they are declared (`--limit 3`, a flag for a boolean and `--no-` before it,
a repeated option for a list, JSON for an object, `@path` or `@-` for a text
read from a file or stdin). Its help is its docstring and its parameters,
the same that describe it to an agent: a command added to the backend is in
`fleet` with no work. The registry is read from the
installed package, so `fleet help AREA VERB` needs no call; `fleet help`
asks `whoami` which commands the role has. Where a command takes the id of
something that has a name (a template, project, inventory, credential,
check, host, schedule, channel, rule, set of variables, workflow), the
name may be given: `fleet` asks the list for it and passes the id, and
says so when two have that name.
The completion for bash offers commands, options and their choices from the
registry, and asks the backend, as the user, for the names of what an id
names, the last runs and the secrets of a credential: nothing to whoever has
no role. `project upload --archive PATH` reads the archive from a file, or
from stdin with `-`, and sends it as the base64 the backend takes.

**From the page to the terminal.** The launch of a template, the page of a
run, the upload of an archive and the uploads of the Store and of Documents
show, under "At a terminal" (`AtTerminal` in `common.tsx`), the `fleet`
command that does the same (`fleet launch` with the answers typed so far,
never the secret ones, which `fleet` asks for; `fleet log -f` or `fleet
result`; `fleet project upload --archive`; `fleet store put`, `fleet doc
put`), quoted for a POSIX shell, with a
button to copy it and one that opens Cockpit's terminal in a new tab:
Cockpit's terminal cannot be handed a command, so it is pasted. Five
commands meant for other programs (`run.reconcile`, `mcp.tools`,
`chat.start`, `scheduler.tick`, `notification.deliver`) are not listed,
and still answer.

**Shortcuts** for what is done most often: `launch` takes a template by name
or id and answers to its survey as `-a NAME=VALUE`, follows the output and
ends with the outcome per host and per task (`run.result`); `log` prints the
output, `-f` follows it; `result` reads the outcome; `secret` sets the
secret of a credential, a channel or a set of variables; `assistant`
starts the chat's agent here ([The chat](chat.md)). The streams of `fleet
store`, `fleet doc` and `fleet draft diff`: [The store](store.md),
[Documents](documents.md), [Agents](agents.md). The exit status tells a
run that failed (1) from a wrong command line (2) and a refusal or an
unreachable backend (3).

**Secrets stay off the command line.** A secret question of a survey is
never taken from `-a`: it is asked for at the terminal, with no echo, and
the value goes to `fleet-secret answer` on its stdin, whose ticket is what
`run.start` gets, as from the page. Without a terminal a required secret
question is an error. `fleet secret` reads the value twice at a terminal, or
once from stdin; one line read from stdin loses its newline, a key keeps it.
The value is in no argv, environment or record (`cli.sh` looks).

**Output** is for a person by default: lists of records as tables, the
known columns first, cut to the width of the terminal; anything else as
`key: value`; times in local time; the output of a run with its colours at a
terminal and without them elsewhere. `--json` gives the answer as the
backend gives it, for a program. English only, like the backend's messages.

**Why not more.** A user at the terminal does not get the projects' files
(`/var/lib/cockpit-fleet` is the service account's), the credentials, or an
`ansible-playbook` of their own: the secrets are decrypted only by PID 1 for
a run, and a run outside the unit would have neither the sandbox nor the
audit trail.

**Projects at a terminal.** Two ways, for administrators only, since
files are theirs in the interface too:

- A directory of local projects under `[projects] local_roots`, by a
  convention that `selfcheck` (`project_roots`) checks on the directory
  itself: group `cockpit-fleet-admin`, mode 2770, and an ACL that lets the
  service account read (`u:cockpit-fleet:rX`, and the same by default for
  what is created there). Administrators write with their own editor and
  git, the service reads, no other account sees the files. Writable by
  another group is a warning (what is there is what operators launch),
  readable by everyone a warning, writable by everyone a failure. Not
  under a directory hidden from the units (`/home`, `/tmp`…:
  [projects.md](projects.md), "Local projects"), and `selfcheck` fails on
  such a root; a project that has to stay in a home is bind-mounted under
  a root (fleet(1)). The ACL
  mask follows the group bits of a new file: who writes there keeps a umask
  that leaves the group read (022 or 002). The manual, fleet(1), has the
  three commands.
- A project written in the interface is cloned with `fleet project clone`:
  `file.bundle` gives its repository as a git bundle (recorded in the audit
  trail, as a copy of every file), which `fleet` clones as the user, keeping
  the project's id in the clone's configuration and the revision it knows
  as `refs/remotes/fleet/main`. `fleet project push` sends a bundle of the
  commits of `main` since that revision to `file.push`, which takes them only
  if the project is still at that revision, as a line of commits (no merge),
  and if every file of the result is one the editor could have written
  (regular files of at most 1 MB, the names `check_file_path` takes: no
  link, no submodule, nothing called `.git`); then a fast forward, and what
  a write in the interface does: a syntax check of each playbook changed,
  the collections when the requirements changed. The authors are those of
  the commits; who pushed is in the audit trail. `fleet project pull`
  fetches the same way and fast-forwards; a clone whose commits diverge is
  told to rebase on `fleet/main`.
- An inventory written in the interface is cloned, pulled and pushed the
  same way, with `fleet inventory clone`, `pull` and `push`: the same
  bundles, by its id; the clone keeps it as `fleet.inventory` rather than
  `fleet.project`, so a clone of one is never pushed as the other. A push
  reads the inventory again.

Trials: [history](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/terminal.md).
