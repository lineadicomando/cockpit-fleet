# The chat: history

Archive: what was tried, measured and found while building the chat; not
needed to work on it. The design is in
[architecture/chat.md](../architecture/chat.md); the phases as shown, in
[acceptance.md](../architecture/acceptance.md) (3b, 12).

**Measured** on the test controller with the adapter for Claude,
`@agentclientprotocol/claude-agent-acp` 0.89.1:

- The adapter wants Node.js 22 or later; Debian 13 has 20. The official
  Node 22 tarball is 203 MB, adapter and SDK 308 MB (about 540 with the
  build for musl, before it is removed); the role installs 511 MB on
  Debian 13, about 505 on Fedora 44 and AlmaLinux 10.
- One idle session takes about 360 MB: adapter 93, `claude` 256,
  `fleet-mcp` 12.
- With `_meta.disableBuiltInTools` the agent, asked for a shell command and
  a file, said it had neither (13 October).

**Found about the adapter** (13 October, as an operator): it names the
tool of a permission request in `_meta.claudeCode.toolName`, which is what
the page reads; `settingSources: []` and `strictMcpConfig` do not get in
the way of the login. A conversation was replayed after a reload and the
agent remembered the run.

**The role on the three distros.** Debian 13 (13 October, from a snapshot
with neither Node.js nor an agent): a second run changes nothing,
`chat_agent_configure` writes the section once, an agent of that name
written by hand is refused, a user who is not root is told so, `absent`
leaves `/opt` empty and `config.toml` as packaged; `ansible-lint` silent.
The page then started the agent and reached the missing login; with the
agent named and not installed it showed the command and opened the
terminal. Fedora 44 and AlmaLinux 10 (24 October, SELinux enforcing,
ansible-core 2.20 and 2.16): the role installs `xz` and the agent, a
second run changes nothing, `absent` empties `/opt`; `fleet-chat`, as an
operator with `[mcp] enabled`, started it with `fleet-mcp` and reached the
missing login, with no denial: the commands in `bin/` are `bin_t`, by the
policy's own rules for `/opt`.

**Why the terminal button opens a new tab** (14 October): `cockpit.jump`
replaced the page and ended the agent the user was logging in for. On
27 November the buttons of the chat's setup and login went back to the
same tab: the user found the tab left open in the way, and an agent not
installed or not logged in loses nothing.

**Why `--permission-mode manual`** at a terminal (20 October): without it
the CLI started in its "auto" mode, calling tools unasked, with an account
on a Max plan. The terminal was tried with the stand-in (`chat.sh`) and
with Claude on Debian 13 (a launch asked for, refused, audited as
`mcp:claude-code`), and with the stand-in on AlmaLinux 10 (6 November):
reads unasked and launches asked in `assisted`, both unasked in
`autonomous`, the risks told, refused, then accepted once and audited.

**Side findings of the profiles' trial** (13 November, Claude on Debian
13): the model's slips (an inventory's name for its id, a placeholder for
a run) were refused by the backend as `not_found`; the questions show the
tool's description in English, from its docstring. And a fault not of the
chat: ansible-lint ran its own syntax check, with no inventory, in the
environment that makes an unread inventory fail, and reported an
`internal-error` on every playbook with ansible-core 2.19; it now gets
that environment without it (`runner.lint_env`).
