# Windows project

For Windows hosts reached over SSH, in the group `windows` of the inventory.

- `site.yml`: the playbook of the group, through the role `common`.
- `ping.yml`: whether the hosts answer, without gathering facts.
- `roles/common/`: what every host shares; its variables, with their
  defaults, in `defaults/main.yml`.
- `group_vars/windows.yml`: how Ansible talks to a Windows host over SSH;
  the inventory may say the same, and what is here wins.
- `requirements.yml`: the collection `ansible.windows`, which is not part of
  ansible-core; it is installed when this file changes.
