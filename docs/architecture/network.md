# The network

What a controller finds out about the network by itself, without Ansible
and without the nodes: the scan of a subnet, decided with the user on 20
November (`decisions.md`, "Network discovery"). The steps after it (an
inventory made from what a scan found, `mac_address` written into one, the
hosts of an inventory probed one by one, a probe from a node of the
segment) are in `TODO.md`.

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

**A run, in the sandbox.** The scan is a job of the kind `netscan`, so it
has what runs have: a place in the queue (the lane of runs), the audit of
`netscan.start`, its output, cancellation, and the sandbox, which needs
nothing more: an ICMP echo goes on a datagram socket (`AF_INET`, which the
unit allows), a TCP connection too, and `/proc/net/arp` is readable with
no netlink. Doing it in `fleetctl`, as `hostkey.scan` does, was left: a
/22 takes tens of seconds, and what a scan found is kept to be looked at
again, and later turned into an inventory.

**What a scan does** (`scanner.py`, standard library only, no privilege).
For each address, at most 64 at a time (fewer with many ports, at most 512
sockets open): a TCP connection to each port of `[network] scan_ports` at
once, waiting one second, and an ICMP echo. A connection refused says the
host is there as much as a port open does. ICMP goes only when the kernel
allows it to the service account's group (`net.ipv4.ping_group_range`); a
scan says so when it did not, and finds hosts by TCP and the neighbour
table only. After the sweep it waits 9 s, the time the kernel takes to
drop a neighbour it had seen before (5 s before its first probe, then
three a second apart), then reads `/proc/net/arp`: the complete entries
in the subnet give the MAC addresses, and a host found only there was
seen on the segment though it answered nothing else (Windows refuses the
echo by default). Then, for each host found, the reverse DNS and a
NetBIOS node status request (UDP 137), which gives the name of the
machine, its workgroup and the MAC address of its adapter: the only one a
host beyond a router gives, marked as given by the host (`mac_source`).
Beyond a router the neighbour table holds no entry for the hosts at all,
never the router's. The controller's own addresses are marked (`self`):
the kernel picks the address as the source of a datagram sent to it.

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

**Who.** Administrators only, for all three commands and for the output of
the run (`run.log`), which is a map of the network. For an agent
`netscan.start` is of its own class, `scan`, between `run` and `adhoc`:
allowed in the profiles `diagnostic` and `administrator`, and asked about
every time (`profiles.ALWAYS_ASKED`), never trusted for a conversation.
It does not wait for an approval as an agent's ad-hoc command does: it
changes nothing on the hosts, and what it may reach is bounded by the
subnets allowed. Being an administrator's command, an agent of an operator
cannot call it whatever its profile.

**The page.** Nodes, Network (`src/components/NetworkPage.tsx`), shown to
those who may call `netscan.result`: the scans, newest first, with their
status and the hosts found; a dialog that offers the subnets allowed, a
field to narrow one, and says how many addresses it may have and which
ports are tried; the hosts a scan found, with their names, MAC address
(and whether the host gave it over NetBIOS), open ports, how each was seen
and the hosts of inventories it may be, filtered by any of these. The page
of the run links to it.

Checked in containers on Debian 13, Fedora 44 and AlmaLinux 10
(`netscan.sh`, 20 November): on a dummy interface with 192.0.2.0/24, so
that nothing leaves the machine, the controller found alone with a port
it listens on, ICMP from the sandbox once `ping_group_range` let the
service account (in a container it is `0 0`), the refusals, the output
kept from operators, and a scan of the /24 canceled with what it had
found. In a rootless container the network is the host's own: the default
of the script never scans it.
