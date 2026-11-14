# Linux project

- `site.yaml`: the playbook of every host, through the role `common`.
- `ping.yaml`: whether the hosts answer, without gathering facts.
- `roles/common/`: what every host shares; its variables, with their
  defaults, in `defaults/main.yaml`.
- `group_vars/all.yaml`: variables of every host; those of a group go in
  `group_vars/GROUP.yaml`, those of one host in `host_vars/HOST.yaml`.

How Ansible reaches the hosts (user, port, keys) belongs to the inventory
and to the credential of the template, not here.
