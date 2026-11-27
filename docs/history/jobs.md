# Jobs: history

Archive: what was tried, measured and found while building jobs; not
needed to work on it.

- **Copying a local project.** An earlier `os.walk` could be raced into
  copying the database by an owner changing the tree during the copy; the
  copy now opens each directory from the one that holds it. Failing on a
  file the service account could not read made every run of such a project
  fail on files it never used; such files are now left out and listed at
  the head of the output.
- **The outcome.** A playbook made a failed run successful by writing
  `result.json` from a process it left running, or by changing `fleet-run`
  in memory through `/proc/1/mem` (review of 16 November); hence PID 1 not
  dumpable, the namespace emptied before the outcome is written.
- **Reconcile under a lock.** Found with the galaxy job, whose result takes
  longer to take in: the unit that ends and a page that polls collected the
  same run together.
- **`fleet-askpass`.** A first version wrote a helper script into the run's
  scratch directory, which is mounted `noexec` and could not execute it.
- **Vaults without an identifier.** Found running `ansible-win_edulab`,
  whose vault has no identifier: given as the file alone, Ansible split the
  path at the `@` of the unit's name.
- **Secret variables** (phase 15). Checked with ansible-core 2.16: an extra
  variable given as `secret_vars.yaml` reaches the playbook and the
  inventory's own files (a `group_vars` holding `serial: "{{ secret }}"`),
  and the listing of the inventory keeps the reference, not the value.
  Rejected: editing Ansible Vault files in the interface, which would have
  had to decrypt them in a job; a secret is a set's, not an inventory's own,
  as Semaphore's variable groups do for templates.
- **Limits.** Joining the template's limit and the launcher's into one
  pattern (`template:&all:other`) reached `other`: found by the security
  review of 24 October.
- **Tags.** Noted on 16 November that tags let an operator run tasks a
  template leaves out by them: they are no control of a role.
- **Ad-hoc commands** (phase 3b in view: the caller as an agent). Checked on
  Debian 13 with ansible-core 2.19.11 (`adhoc.sh`).
- **Surveys.** Answers tagged `!unsafe` used as written: checked on
  ansible-core 2.19 (Debian 13) and 2.20.
