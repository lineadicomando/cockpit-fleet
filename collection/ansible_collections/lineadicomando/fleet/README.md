# lineadicomando.fleet

Ansible content bundled with cockpit-fleet and loaded on the controller from
`/usr/share/cockpit-fleet/collections`. It is not published on Galaxy.

- `lineadicomando.fleet.events` (callback): writes one JSON object per line for
  every playbook, play, task and host result, to the file named by
  `FLEET_EVENTS_FILE`.
