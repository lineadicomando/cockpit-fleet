# lineadicomando.fleet

Ansible content bundled with cockpit-fleet and loaded on the controller from
`/usr/share/cockpit-fleet/collections`. It is not published on Galaxy.

- `lineadicomando.fleet.events` (callback): writes one JSON object per line for
  every playbook, play, task and host result, to the file named by
  `FLEET_EVENTS_FILE`.
- `lineadicomando.fleet.checks` (action, with the modules `checks` for Linux
  and `win_checks` for Windows): runs every check of the catalog planned for
  a host in one task, and returns their results in the contract of
  cockpit-fleet. The action chooses the module by the shell of the host.
- `lineadicomando.fleet.fleet_result` (action, documented as a module): the
  last task of a check written as a playbook; it checks a state, a summary,
  metrics and details and hands them back, running nothing on the node.
- `lineadicomando.fleet.chat_agent` (role and playbook): installs on a
  controller the agent the chat page starts, the ACP adapter for Claude with
  the Node.js it needs, under `/opt/cockpit-fleet-agent`. Node.js is checked
  against its published checksum and the adapter comes from a lockfile that
  names every package with its hash. See `docs/chat.md` of cockpit-fleet.
