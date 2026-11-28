# Inventories: history

Archive: what was tried, measured and found while building inventories;
not needed to work on it. The design is in
[architecture/inventories.md](../architecture/inventories.md).

- **Plugins and scripts.** Tried with ansible-core 2.19.11
  (`inventories.sh`): `ansible.builtin.generator`, a directory with a
  static file and `ansible.builtin.constructed`, a script with
  `[inventories] scripts` off and on.
- **The listing.** `ansible-inventory --list --export` gives a file
  encrypted with Ansible Vault in clear, inline `!vault` values encrypted:
  checked with ansible-core 2.16.
