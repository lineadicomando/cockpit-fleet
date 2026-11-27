# The network: history

Archive: what was tried, measured and found while building the network;
not needed to work on it.

**20 November.** `ansible-core` 2.16: a `pc101.yaml` beside a directory
`pc101/` in `host_vars` was no longer read (`find_vars_files` takes the
first that exists, a directory included), which is why `netscan.macs`
moves the host's file into the directory.

**20 November, `netscan.sh` in containers** (Debian 13, Fedora 44,
AlmaLinux 10), on a dummy interface with 192.0.2.0/24: the controller found
alone with a port it listens on, ICMP from the sandbox once
`ping_group_range` let the service account (in a container it is `0 0`),
the refusals, the output kept from operators, and a scan of the /24
canceled with what it had found.

**20 November, on the VM `deb13`**, with
`IT_NETSCAN_SUBNET=192.168.140.0/28`, the lab's network: Debian 13 as
installed lets the service account send ICMP (`ping_group_range` is
`0 2147483647`), the controller found with ICMP, its name from the DNS of
libvirt, and the gateway; `sandbox.sh` passes. Then, with a neighbour
written in by hand on the dummy interface (`ip neigh … nud permanent`),
found by the table with its MAC address: an inventory made from the scan,
read, and Ansible (`ansible-inventory --host`) giving the MAC address; an
inventory with `host_vars/pc5.yaml`, the MAC address written and the file
moved, Ansible giving both its variables, and a second time changing
nothing. And an inventory probed by an operator: the controller
answering, the neighbour `segment_only` with its MAC address, an address
with nothing `silent`, a name of `.invalid` `unresolved`. Then `net_probe`
as an ad-hoc command on the controller itself, by a local connection, in
the sandbox of a run: a subnet that may not be scanned refused, and on
192.0.2.0/28 the controller by TCP and the neighbour with its MAC address.
The test's first host was named `off`, which YAML reads as false: since
then `netscan.inventory` quotes every name.

**26 November**, on `deb13`, `alma10` and `fed44` with `win11` up
(192.168.140.0/27): ICMP allowed as installed on all three, no denial of
SELinux. `win_net_probe` failed on its first try on `win11` (`windows.sh`
now runs it): PowerShell does not tell `$batch`, the addresses of a round,
from `$Batch`, their number, so the second round added an array; renamed,
the node found the gateway and the controller with their MAC addresses.
