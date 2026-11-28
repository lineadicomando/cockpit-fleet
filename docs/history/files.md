# Files of projects and inventories: history

Archive: what was tried, measured and found while building the files of
projects and inventories; not needed to work on it.

- **Syntax checks.** Checked on Debian 13 with ansible-core 2.19.11 and
  ansible-lint 25.2.1, which works inside the unit as it is.
- **The editor.** CodeMirror 6 cost about 650 kB of bundle when it came,
  which went from 0.65 to 1.3 MB.
- **Modules.** With the `ansible` package, 9201 modules (a listing of 0.9
  MB) took seconds to list; the documentation of one module 0.4 s.
