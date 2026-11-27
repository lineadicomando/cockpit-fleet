# The chat: what was tried and measured

An archive of the trials of [The chat](chat.md), kept for what they found;
what the chat is and does is there.

The page of the phase was tried on Debian 13 with the stand-in agent
(6 November): the profiles offered, the risks of `administrator` accepted
once, an ad-hoc command asked about with the profile and the class and no
"always", the instructions by layer, and a draft written through
`fleet-mcp` reviewed, edited on the page of the files and merged, the
draft gone and the audit holding `chat.accept`, `chat.start` and
`draft.merge`.

Then with Claude (Opus 5.5 through the adapter 0.89.1) on Debian 13 (13
November), as an administrator with `[mcp] max_role = "admin"` and the five
profiles enabled, on a managed project, a managed inventory of the
controller itself and a template. On the page: `observer` read templates,
projects, inventories and runs with no question; `assisted` asked before a
launch, with the profile and the class, and after "always" launched again
unasked and read the run's output; `diagnostic`, `autonomous` and
`administrator` each told their risks before the first session and not
again, `diagnostic` ran `ping` and `setup` unasked, `autonomous` launched
unasked and had no `run_adhoc` among its tools; in `administrator` an
ad-hoc `command` was asked about once, with no "always", then waited for
approval until the user approved it in the list of runs, and a playbook
the assistant wrote (`file_write`, asked about once) went to a draft, which
the user reviewed and merged. At the terminal, `fleet assistant --profile
assisted` read unasked and asked before the launch, `--profile autonomous`
launched unasked, with no question of risks already accepted on the page.
The audit has the three `chat.accept`, every `chat.start` (`cli` and
`terminal` for the two at the terminal), and each call of the agent as the
user's, via `mcp:claude-code` with the profile. What it showed beside: the
model's own slips (an inventory's name for its id, a placeholder for a run)
refused by the backend as `not_found`; an inventory created through
`fleetctl` is not read until something reads it (the page asks
`inventory.parse` after creating one), so the agent saw it with no hosts;
the questions show the tool's description in English, from its docstring;
`whoami` lists the commands of the role, not of the profile. And a fault
not of the chat: ansible-lint ran its own syntax check, with no inventory,
in the environment that makes an inventory not read fail, and reported an
`internal-error` on every playbook with ansible-core 2.19; it now gets that
environment without it (`runner.lint_env`).

Measured on the test controller with the adapter for Claude,
`@agentclientprotocol/claude-agent-acp` 0.89.1:

- The adapter wants Node.js 22 or later and Debian 13 has 20: the official
  Node 22 tarball is 203 MB, adapter and SDK 308 MB (about 540 with the build
  for musl, before it is removed).
- One idle session takes about 360 MB: adapter 93, `claude` 256, `fleet-mcp`
  12.
- With `_meta.disableBuiltInTools` the agent, asked for a shell command and a
  file, said it had neither (13 October, logged in, before the page existed).

Checked on Debian 13: `chat.sh` drives `fleet-chat` with an agent written
for the test (`fake-agent.py`, the protocol with no model behind it), and
the page was tried from the browser with it as an operator and as a viewer:
a read-only tool with no question, a template launched after a
confirmation, a tool outside the role refused by the backend, no process
left behind after a reload.

Then with the adapter and its model, as an operator (13 October): asked
what it could do, the agent called `whoami` and `template_list` with no
question and listed its tools and its role; asked to launch a template, it
waited for the confirmation, which showed the description of the tool and
the answer to the survey, then called `run_result` and told the outcome by
host and by task; the audit trail has the launch as the user's, via
`mcp:claude-code`. Asked for a shell command, a file and an ad-hoc command,
it said it had no tool for the first two and that the third is not for an
operator. After a reload the conversation was in the list, was replayed, and
the agent remembered the run. The adapter names the tool of a permission
request in `_meta.claudeCode.toolName`, which is what the page reads;
`settingSources: []` and `strictMcpConfig` do not get in the way of the
login.

The role, from the snapshot of the test controller, where there is neither
Node.js nor an agent (13 October): the playbook installs 511 MB, a second run
changes nothing, `chat_agent_configure` writes the section and a second time
does not, an agent of the same name written by hand is refused, a user who is
not root is told so, `absent` leaves `/opt` empty and `config.toml` as
packaged; `ansible-lint` has nothing to say. With the agent so installed the
page started it and reached the missing login; with the agent named and not
installed it showed the command, copied it and opened the terminal.

On Fedora 44 and AlmaLinux 10 (24 October, SELinux enforcing, ansible-core
2.20 and 2.16), as `docs/chat.md` says: the role installs `xz` and the
agent (about 505 MB), a second run changes nothing, `absent` leaves `/opt`
empty; `fleet-chat`, as an operator with `[mcp] enabled`, started it with
`fleet-mcp` named and reached the missing login; no denial. Its commands
in `bin/` are `bin_t`, by the policy's own rules for `/opt`.

With the model, through an agent installed by the role (14 October, as an
operator with no earlier login): the page reached the missing login, the
user logged in at Cockpit's terminal, and the assistant then launched a
template with an answer to its survey, after a confirmation that showed
the answer, and reported its outcome per host and per task from
`run.result`; the launch is in the audit trail with `via` `mcp:claude-code`.
The button to the terminal then replaced the page and ended the agent,
which is why it opens a new tab now.
