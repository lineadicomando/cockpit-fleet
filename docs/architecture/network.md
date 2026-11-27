# The network

What a controller finds out about the network by itself, without Ansible
and without the nodes: the scan of a subnet, turned into an inventory or
into the MAC addresses of one; the hosts of an inventory probed one by
one; and a probe from a node of a segment the controller does not reach
(`decisions.md`, "Network discovery").

```
NetworkPage ── netscan.start ──▶ fleetctl: the subnet checked, a run queued
                                   │ cockpit-fleet-run@ID (the sandbox)
                                   ▼
                                 fleet-run ── scanner.scan ──▶ netscan.json
                                   │ the unit ends
                                   ▼
                                 jobs._collect ── netscan.ingest ──▶ netscan_hosts
```

**Which subnets.** `netscan.subnets` lists those the controller is
attached to, the routes of `/proc/net/route` with no gateway (not those of
the loopback, nor of single hosts), unless `[network] scan_connected` is
false, and those of `[network] scan_subnets`. A scan takes one of them or
a part of one, of at most `scan_max_addresses` addresses (1024 by default,
4096 at most): `check_subnet` refuses anything else, an IPv6 subnet and one
written with bits of a host among them. This is what keeps an agent, which
what a node writes may steer, from turning the controller on a network the
administrator did not name. IPv4 only: a /64 cannot be swept.

**A run, in the sandbox.** The scan is a job of the kind `netscan`: a
place in the queue (the lane of runs), the audit of `netscan.start`, its
output, cancellation, and the sandbox, which needs nothing more: an ICMP
echo goes on a datagram socket (`AF_INET`, which the unit allows), a TCP
connection too, and `/proc/net/arp` is readable with no netlink. Not in
`fleetctl`, as `hostkey.scan` is: a /22 takes tens of seconds, and what a
scan found is kept, to be turned into an inventory later.

**What a scan does** (`scanner.py`, standard library only, no privilege).
For each address, at most 64 at a time (fewer with many ports, at most 512
sockets open): a TCP connection to each port of `[network] scan_ports` at
once, waiting one second, and an ICMP echo. A connection refused says the
host is there as much as a port open does. ICMP goes only when the kernel
allows it to the service account's group (`net.ipv4.ping_group_range`:
`0 0` in a container, `0 2147483647` as Debian 13, Fedora 44 and
AlmaLinux 10 are installed); a scan says so when it did not, and finds
hosts by TCP and the neighbour table only. After the sweep it waits 9 s,
the time the kernel takes to drop a neighbour it had seen before (5 s
before its first probe, then three a second apart), then reads
`/proc/net/arp`: the complete entries in the subnet give the MAC
addresses, and a host found only there was seen on the segment though it
answered nothing else (Windows refuses the echo by default). Then, for
each host found, the reverse DNS and a NetBIOS node status request (UDP
137), which gives the name of the machine, its workgroup and the MAC
address of its adapter: the only one a host beyond a router gives, marked
as given by the host (`mac_source`). Beyond a router the neighbour table
holds no entry for the hosts at all, never the router's. The controller's
own addresses are marked (`self`): the kernel picks the address as the
source of a datagram sent to it.

A scan stopped by a cancellation ends the sweep and writes what it found,
with `complete` false; `_collect` takes it in whatever the status.

**What the backend keeps.** `netscan.json` is read as everything a run
writes: no link followed, 8 MB at most, each host checked (an address in
the subnet scanned, a MAC address of the right form, ports and ways it was
seen among those known, names cut to size), the rest left out. The hosts
go to `netscan_hosts` (schema 34), with the run; the run's options say the
subnet, whether it is the controller's own segment, how many hosts, whether
ICMP was used and whether the scan ended. The file is then removed.

**Reading it.** `netscan.result` answers with the hosts and, for each, the
hosts of inventories it may be, from their last reading: by address
(`ansible_host`, or a name that is an address) and by name (the reverse
DNS or the NetBIOS name, whole or its first label, against the name of a
host or its `ansible_host`), whatever their case. With `wait`, as
`run.result`, it answers when the scan ends or after at most 50 s.

**An inventory from a scan.** `netscan.inventory` makes an inventory
written here (managed, YAML) of the hosts of an ended scan that the caller
chose by address, through `inventory.create` itself, so that an agent's
waits in a draft as any inventory an agent creates; one made in main is
read at once. Each host is named as asked: `short` (the NetBIOS name in
lower case, or the first label of the DNS name), `dns` (the whole name) or
`address`; a name Ansible or a path would not take, or one an earlier host
took, gives way to the address. Each gets `ansible_host` (unless named by
it) and `mac_address` when the scan saw one, all under one group if asked.
The text is written by the backend, every name and value as a quoted
string (the standard library has no YAML, and a host named `off` would be
false).

**MAC addresses into an inventory.** `netscan.macs` writes, for the hosts
of an inventory written here that a scan found by address (and by name
only when asked, `by_name`), `mac_address: "…"` in
`host_vars/HOST/fleet_mac.yaml`, beside the source the inventory reads (its
`path`, or the directory that holds it), as one commit, or in the draft of
an agent's session. A file of ours, so that no YAML of the administrator's
is edited, which the backend could not do without losing its comments.
Ansible reads the first of `host_vars/HOST`, `HOST.yml`, `HOST.yaml`,
`HOST.json` that exists, a directory included (`find_vars_files`): a
`HOST.yaml` beside `HOST/` is no longer read. So the file of the host that
Ansible was reading is moved into the directory, unchanged, in the same
commit; a host whose files there already set `mac_address` (a line that
begins with it), whose directory or file is a link, which the scan found
with two MAC addresses, or whose name cannot name a file, is left alone
and told why. With `preview` nothing is written: the page shows what would
be, host by host, before asking. Writing it again with the same address
changes nothing. Only inventories written here: a project's is changed in
the project.

**Probing the hosts of an inventory.** `netscan.probe` tells why hosts of
an inventory do not answer, which a scan of their subnet tells only in
part: what is at an address, not whether it is the host meant to be
there. It takes the hosts of an inventory as last read (or those named,
at most 256), all but those of a local connection, and for each, in
`fleetctl` and at once, not as a run: it resolves the name (the IPv4
address of `ansible_host`, or of the name), probes it as a scan probes an
address (`scanner.probe`, with the ports of `[network] scan_ports` and
the port of its connection: `ansible_port`, else 22, or 5985 for WinRM),
names it from the reverse DNS and NetBIOS, and reads the neighbour table,
waiting the 9 s only when a host was seen there and nowhere else. It is
an operator's and of the class `read`: it changes nothing, reaches only
hosts an administrator put in an inventory, and is bounded in number, so
any profile of the assistant has it without a question (the base
instructions say to use it before saying that hosts are off); the journal
records each probe. The cost: 256 hosts hold `fleetctl` for some seconds
(one per batch of up to 64, the lookups, and the 9 s when needed).

Each host comes with its findings, from the most telling: `unresolved`
(the name has no address), `ipv6` (only IPv6 addresses, not probed),
`silent` (nothing answered, not even in the neighbour table: off, or
unreachable), `segment_only` (in the table only: on, behind a firewall),
`port_closed` (it answers, but not on its connection's port), and beside
them `mac_differs` (the MAC address seen is none of those the CMDB last
collected for that host; `mac_of` names the hosts the CMDB knows with
it, in any inventory: two machines that swapped addresses),
`address_moved` (the CMDB saw the host with other addresses) and
`netbios_differs` (it gives another name over NetBIOS). MAC addresses are
compared as `aa:bb:…`, Windows' `AA-BB-…` turned into that. The CMDB is
read from `cmdb_hosts`, the last collection of the check `cmdb`: a host
never collected has no comparison.

The page shows it from the inventories ("Probe the hosts", for whoever
may call it, on an inventory with hosts): a dialog with each host, its
address, its findings, open ports, MAC address (with the CMDB's when it
differs, and whose else it is), and names.

**From a node.** Beyond a router the controller sees no MAC address, and
may not reach the segment at all; a node on it does. The bundled
collection has `lineadicomando.fleet.net_probe`, an action that runs the
module of the node's platform as `checks` does: `net_probe` (Python 3.6 or
later, standard library) where the shell is not PowerShell,
`win_net_probe` (PowerShell 5.1, no privilege: `Ping`, `TcpClient`,
`Get-NetNeighbor`) where it is. Given a subnet (at most 1024 addresses) or
addresses, it tries each with an ICMP echo (on Linux a datagram socket,
or the `ping` program when the group may not), TCP connections to the
ports, and reads the node's neighbour table, waiting 9 s when an address
is there and nowhere else; it answers with the node's interfaces and the
hosts found, in the shape of a scan's, and changes nothing. It is an
ad-hoc command, so it needs nothing new in the backend but one check in
`run.adhoc` (`netscan.check_probe_args`, for whoever calls it): a subnet
that may be scanned, or addresses each within one or of a host of the
inventory, no free form, no other argument, and the ports those of
`[network] scan_ports` (some of them at most), which the backend writes
into the arguments; a template in a string fails the check of its type.
So a node is held to the subnets the controller is, and a subnet beyond a
router is probed once an administrator lists it in `scan_subnets`. It is
among the default modules of `[adhoc] modules` and of `[mcp]
adhoc_modules`, so the assistant in `diagnostic` runs it without a
question, like `setup`. What it finds stays in the run's events, as any
module's: it is not taken into `netscan_hosts`.

**Who.** Administrators only, for scans (`netscan.start`, `result`,
`inventory`, `macs`, `subnets`) and for the output of the run
(`run.log`), which is a map of the network. For an agent
`netscan.start` is of its own class, `scan`, between `run` and `adhoc`:
allowed in the profiles `diagnostic` and `administrator`, and asked about
every time (`profiles.ALWAYS_ASKED`), never trusted for a conversation.
It does not wait for an approval as an agent's ad-hoc command does: it
changes nothing on the hosts, and what it may reach is bounded by the
subnets allowed. Being an administrator's command, an agent of an operator
cannot call it whatever its profile. `netscan.inventory` and `netscan.macs`
are `config`, as `inventory.create` and `inventory.update`: the profile
`administrator` only, every call asked, and what they write goes to a
draft.

**The page.** Nodes, Network (`src/components/NetworkPage.tsx`), shown to
those who may call `netscan.result`: the scans, newest first, with their
status and the hosts found; a dialog that offers the subnets allowed, a
field to narrow one, and says how many addresses it may have and which
ports are tried; the hosts a scan found, with their names, MAC address
(and whether the host gave it over NetBIOS), open ports, how each was seen
and the hosts of inventories it may be, filtered by any of these. Hosts
are chosen with a box on each row, all of them when none is, for two
actions: "Create an inventory" (its name, how hosts are named, a group;
the page then opens its files) and "Write the MAC addresses into an
inventory" (one written here that holds some of them, by name too if
asked, with what would be done shown before). The page of the run links
to it.

**Tests.** `netscan.sh` scans a dummy interface with 192.0.2.0/24 by
default, so that nothing leaves the machine (in a rootless container the
network is the host's own), or the lab's subnet with
`IT_NETSCAN_SUBNET`; `windows.sh` runs `net_probe` from a Windows node;
the scenario `network-scan` drives the page (`browser-tests.md`). What
they found while this was built: [history](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/network.md).
