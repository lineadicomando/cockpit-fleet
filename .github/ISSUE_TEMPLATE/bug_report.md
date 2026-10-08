---
name: Bug report
about: Report a problem with cockpit-fleet
labels: bug
---

**Describe the bug**
A clear description of what the bug is.

**Steps to reproduce**
1. Go to '...'
2. Click on '...'
3. See error

**Expected behavior**
What you expected to happen.

**Environment**
- cockpit-fleet version (`dpkg -s cockpit-fleet | grep Version`):
- Cockpit version (`cockpit-bridge --version`):
- ansible-core version (`ansible --version`):
- Controller OS and version:
- Managed node OS, if relevant:

**Logs**
Paste any relevant output from the browser console or from
`journalctl -u 'cockpit-fleet-run@*'`. Remove secrets and host names first.
