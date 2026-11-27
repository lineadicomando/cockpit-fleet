# Projects: history

Archive: what was tried, measured and found while building projects; not
needed to work on it.

- **Inventories read again.** Before an inventory that is a file of a
  project was read again on a change or a push touching what it reads, its
  hosts were those of the last reading until someone asked, or the hourly
  reading for checks.
- **Uploaded projects.** Measured on the test controller, from the browser
  through Cockpit: an archive of 19 MB answered in 1.7 s and unpacked in
  under a second; 600 MB of zeros in a gzip of 600 kB refused at the limit.
  The rule that what the job leaves is taken only if it is a directory,
  not a link, was then extended to the git fetch.
- **Galaxy.** Checked on Debian 13 with ansible-core 2.19.11 (`galaxy.sh`):
  with Debian's `ansible` package a requirement for `ansible.windows`
  installs nothing; a collection of 1000 files from a public git
  repository is installed in 7 to 17 s and takes 5.7 MB.
