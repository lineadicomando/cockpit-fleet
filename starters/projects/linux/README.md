# Linux project

- `site.yml`: the playbook of every host, through the role `common`.
- `ping.yml`: whether the hosts answer, without gathering facts.
- `roles/common/`: what every host shares; its variables, with their
  defaults, in `defaults/main.yml`.
- `group_vars/all.yml`: variables of every host; those of a group go in
  `group_vars/GROUP.yml`, those of one host in `host_vars/HOST.yml`.

How Ansible reaches the hosts (user, port, keys) belongs to the inventory
and to the credential of the template, not here.
