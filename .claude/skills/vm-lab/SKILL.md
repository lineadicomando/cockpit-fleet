---
name: vm-lab
description: Test VMs of cockpit-fleet (controllers on Debian 13, Fedora 44 and AlmaLinux 10, a Windows 11 node, on libvirt). Use before building, rebuilding, starting, stopping, reverting or snapshotting them, deploying the package to one, running integration scripts on one, reaching Cockpit on one, or testing a change on a machine rather than in a container.
---

# Test VMs

Four libvirt VMs (`qemu:///system`) dedicated to this project, on a NAT
network of their own. Names, addresses and sizes are in `test/vm/lab.conf`
(tracked, the same for everyone); the password and the Windows ISO are in
`.env` (from `.env.example`, not tracked). Everything goes through
`test/vm/lab.sh`; run it from anywhere, no sudo.

| Target | VM | Address | For |
|---|---|---|---|
| `deb13` | `cockpit-fleet-deb13` | `.10` | the main controller: sandbox on its own kernel, the interface, load tests |
| `fed44` | `cockpit-fleet-fed44` | `.11` | SELinux enforcing |
| `alma10` | `cockpit-fleet-alma10` | `.12` | SELinux, ansible-core 2.16 (the minimum) |
| `win11` | `cockpit-fleet-win11` | `.20` | a Windows node over SSH (`windows.sh`, the Windows checks) |

Addresses are in `LAB_SUBNET` (`192.168.140` by default), reserved for each
VM's MAC by the network's DHCP. Linux nodes are containers on a controller
(`nodes.sh`), not VMs.

```
test/vm/lab.sh status                    # network, VMs, state, snapshots
test/vm/lab.sh reset deb13               # stop, back to baseline
test/vm/lab.sh start deb13               # refuses when the host is short
test/vm/lab.sh wait deb13                # until SSH answers
test/vm/lab.sh deploy deb13              # build the .deb/.rpm for it, install it
test/vm/lab.sh it deb13 sandbox runs     # scripts as root, logs in it-results/vm-deb13/
test/vm/lab.sh root deb13 'journalctl -u cockpit-fleet-run@* -n 50' </dev/null
test/vm/lab.sh ssh win11 'Get-ComputerInfo -Property OsName'
test/vm/lab.sh tunnel deb13              # Cockpit on http://127.0.0.1:9091
test/vm/lab.sh stop all
```

## Rules

- **Only `cockpit-fleet-*`.** Building, rebuilding, destroying, reverting
  and snapshotting the VMs of this lab needs no agreement: they are made
  from scratch by the scripts and hold no data of value. Any other VM on the
  host belongs to someone else: never start, change or delete it without
  the user's agreement. `lab.sh` refuses names without the prefix.
- **Check resources before every start.** `start` and `build` do it; when
  more VMs are needed together, `resources deb13 win11` first. If they do
  not fit, say so instead of starting fewer silently. Shut down what you
  started when the test is over.
- **Passwords stay in `.env`.** The scripts pass them through stdin and the
  environment (`sshpass -e`, `sudo -S`); never print them, write them
  elsewhere, or put them in argv. Data the scripts write with the password
  in it (the Windows answer file) is made in a private directory under
  `/tmp`, never in the work tree.
- **Start a test from `baseline`** (`reset`, then `deploy`). Take another
  snapshot only for a state worth going back to within one task; delete it
  afterwards.
- **What is on the VMs now** (packages deployed, data left by a test,
  results) goes in `CLAUDE.local.md`, not here: this file says how, for
  anyone.

## Snapshot

`baseline`, taken shut off, internal to the qcow2 (one file per VM):

- controllers: the cloud image brought up to date, the admin account
  `LAB_USER` with sudo (password), SSH with passwords, `cockpit`, `podman`,
  `sshpass`, `git`, `acl`, `ansible-core` (`ansible-lint` on Debian and
  Fedora), the guest agent; no `cockpit-fleet`. Time zone `LAB_TIMEZONE`,
  not UTC on purpose (the containers run in UTC).
- Windows: Windows 11 Enterprise Evaluation, the local administrator
  `LAB_USER`, OpenSSH Server with PowerShell as its shell, no sleep.

## Building

```
test/vm/lab.sh build deb13       # about 5 minutes; fed44, alma10 the same
test/vm/lab.sh build win11       # 20-40 minutes: run it in the background
test/vm/lab.sh rebuild deb13     # destroy, then build
```

`build` creates the network first. Linux (`build-linux.sh`): the cloud
image of `lab.conf`, verified against the distribution's checksum file and
cached in `LAB_CACHE`, uploaded into `LAB_POOL` and grown; a cloud-init seed
(`cloud-init/`) with the account; then `provision.sh` as root over SSH, a
reboot, the snapshot. Windows (`build-win.sh`): Setup from `WIN_ISO` with
`win/autounattend.xml`, Secure Boot, TPM 2.0, SATA disk and e1000e NIC (no
virtio drivers); `win/setup.ps1` at the first logon; then the snapshot.
When an unattended Windows build stops, look at its screen: `virsh
screenshot cockpit-fleet-win11 /tmp/win.png` and read the image.

Windows is an evaluation, for test use only: it expires after 90 days
(`build` prints the date, also in the snapshot's description). Rebuild it
then, from a freshly downloaded ISO; do not rearm or work around it.

## Integration scripts on a VM

`lab.sh it TARGET SCRIPT...` copies `test/integration` to `/tmp/it`, runs
each script as root with `IT_WIPE=yes` and `IT_SSH_USER`/`IT_SSH_PASSWORD`
of the admin account (the password on stdin), and passes on the caller's
`IT_*`. Scripts run one after another; long ones (`checks`, `load`) are
better in the background. For `windows.sh`, authorise a key pair made for
the test on `win11` (in `C:\ProgramData\ssh\administrators_authorized_keys`)
and store its private half as the secret that `IT_WIN_KEY` names; remove
the key afterwards, or `reset win11`.

## The interface in the browser tools

No password may go into a browser tool call (it would end up in the
conversation). With `lab.sh tunnel deb13` in the background, log in with
`curl` and hand the browser only the session cookie, through a file:

```bash
. test/vm/env.sh && umask 077
printf 'user = "%s:%s"\n' "$LAB_USER" "$LAB_PASSWORD" |
    curl -s -K - -c /tmp/cockpit-jar -o /dev/null http://127.0.0.1:9091/cockpit/login
awk '$6=="cockpit" {printf "document.cookie = \"cockpit=%s; path=/\"; void 0;\n", $7}' /tmp/cockpit-jar > /tmp/cockpit-cookie.js
rm /tmp/cockpit-jar
```

Then open `http://127.0.0.1:9091/cockpit/login`, run `evaluate_script` with
`sourcePath: /tmp/cockpit-cookie.js` (it must end in `void 0`, or the cookie
is echoed back), navigate to `http://127.0.0.1:9091/fleet`, and delete the
file at the end. To see a page as a role, use a throwaway account in the
role's group, as `CLAUDE.md` says.

## Notes

- `root` runs the command with `sh -c` under `sudo -k -S`: the password is
  the first line of its stdin, the rest reaches the command (`tar -cf - … |
  lab.sh root deb13 'tar -xf - …'`). With nothing to pass, give it
  `</dev/null`.
- SSH skips host key checks: the VMs are rebuilt and reverted.
- Messages of the libvirt daemon may come in the host's language whatever
  the client's locale: parse `--name` lists, not messages.
- Do not edit a script under `test/vm/` while it runs: bash reads it as it
  goes.
