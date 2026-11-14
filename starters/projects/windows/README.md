# Windows project

For Windows hosts reached over SSH, in the group `windows` of the inventory.

- `site.yaml`: the playbook of the group, through the role `common`.
- `ping.yaml`: whether the hosts answer, without gathering facts.
- `roles/common/`: what every host shares; its variables, with their
  defaults, in `defaults/main.yaml`.
- `group_vars/windows.yaml`: how Ansible talks to a Windows host over SSH;
  the inventory may say the same, and what is here wins.
- `requirements.yaml`: the collection `ansible.windows`, which is not part of
  ansible-core; it is installed when this file changes.
