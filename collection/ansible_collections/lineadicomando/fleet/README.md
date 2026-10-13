# lineadicomando.fleet

Ansible content bundled with cockpit-fleet and loaded on the controller from
`/usr/share/cockpit-fleet/collections`. It is not published on Galaxy.

- `lineadicomando.fleet.events` (callback): writes one JSON object per line for
  every playbook, play, task and host result, to the file named by
  `FLEET_EVENTS_FILE`.
- `lineadicomando.fleet.chat_agent` (role and playbook): installs on a
  controller the agent the chat page starts, the ACP adapter for Claude with
  the Node.js it needs, under `/opt/cockpit-fleet-agent`. Node.js is checked
  against its published checksum and the adapter comes from a lockfile that
  names every package with its hash. See `docs/chat.md` of cockpit-fleet.
