#!/bin/sh
# The scan of a subnet on the installed package: the subnets the controller
# is attached to, refusals (a role, a subnet outside them, one too large), a
# scan in the sandbox of a run that finds the controller itself with a port
# open there, ICMP when net.ipv4.ping_group_range lets the service account
# (opened to it for the test on the dummy interface, as it was otherwise),
# the output kept from operators, a scan canceled keeping what it found; then
# an inventory made from a scan, and MAC addresses written into another, a
# file of a host's variables moved, both read by Ansible as they should be;
# the hosts of an inventory probed one by one; and the module net_probe run
# on a node (the controller itself, by a local connection).
# By default it scans 192.0.2.0/28, a subnet for documentation put on a
# dummy interface for the test, where only the controller answers and a
# neighbour is written in by hand: nothing leaves the machine (in a
# rootless container the network may be the host's). The inventories need
# that neighbour, so they are left out with IT_NETSCAN_SUBNET.
# IT_NETSCAN_SUBNET names a subnet of the controller's to scan instead, on a
# lab's network; then only the controller itself is checked among the hosts.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

PORT=5985
LISTENER=
DUMMY=fleetit0
RANGE=$(sysctl -n net.ipv4.ping_group_range)
cleanup() {
    [ -n "$LISTENER" ] && kill "$LISTENER" 2>/dev/null
    if [ -z "${IT_NETSCAN_SUBNET:-}" ]; then
        ip link del "$DUMMY" 2>/dev/null
        sysctl -qw net.ipv4.ping_group_range="$RANGE"
    fi
    wipe
}
trap cleanup EXIT
cleanup
prepare

if [ -n "${IT_NETSCAN_SUBNET:-}" ]; then
    subnet=$IT_NETSCAN_SUBNET
    address=$(python3 -c "
import ipaddress, socket, sys
net = ipaddress.ip_network(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect((str(net[1]), 9))
print(s.getsockname()[0])" "$subnet")
    wide=$subnet
else
    ip link add "$DUMMY" type dummy && ip addr add 192.0.2.1/24 dev "$DUMMY" && ip link set "$DUMMY" up &&
        ip neigh add 192.0.2.5 lladdr 52:54:00:12:34:56 dev "$DUMMY" nud permanent ||
        { echo "cannot make a dummy interface: set IT_NETSCAN_SUBNET" >&2; exit 2; }
    echo "      | net.ipv4.ping_group_range was $RANGE; opened to the service account"
    gid=$(id -g cockpit-fleet)
    sysctl -qw net.ipv4.ping_group_range="$gid $gid"
    subnet=192.0.2.0/28
    address=192.0.2.1
    wide=192.0.2.0/24
fi

subnets=$(admin netscan.subnets)
expect_eq "the subnet is among those attached" "$(get "$subnets" "any(__import__('ipaddress').ip_network('$subnet').subnet_of(__import__('ipaddress').ip_network(s['subnet'])) for s in a['data']['subnets'] if s['source'] == 'connected')")" True
echo "      | this controller $address, scanning $subnet"

got=$(ctl itoperator netscan.start "{\"subnet\": \"$subnet\"}")
expect_eq "an operator may not scan" "$(get "$got" "a['error']['code']")" forbidden
got=$(admin netscan.start '{"subnet": "198.51.100.0/24"}')
expect_eq "a subnet outside those allowed is refused" "$(get "$got" "a['error']['code']")" forbidden
got=$(admin netscan.start '{"subnet": "10.0.0.0/8"}')
expect_eq "a subnet too large is refused" "$(get "$got" "a['error']['code']")" bad_request

# Something that answers on the controller, on a port a scan tries.
python3 -c "
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', $PORT)); s.listen(); time.sleep(600)" &
LISTENER=$!
sleep 1

run=$(admin netscan.start "{\"subnet\": \"$subnet\"}")
id=$(get "$run" "a['data']['id']")
expect_run "the scan runs to the end" "$id" 180 successful
result=$(admin netscan.result "{\"id\": \"$id\"}")
self=$(get "$result" "[h for h in a['data']['hosts'] if h['address'] == '$address'][0]")
expect_eq "it found the controller itself" "$(get "$result" "[h['self'] for h in a['data']['hosts'] if h['address'] == '$address'][0]")" True
expect_eq "with the port open there" "$(get "$result" "$PORT in [h for h in a['data']['hosts'] if h['address'] == '$address'][0]['ports']")" True
expect_eq "on the controller's own segment" "$(get "$result" "a['data']['on_link']")" True
echo "      | $(get "$result" "len(a['data']['hosts'])") hosts found; the controller: $self"
if [ -z "${IT_NETSCAN_SUBNET:-}" ]; then
    expect_eq "and the neighbour, nothing else" "$(get "$result" "[h['address'] for h in a['data']['hosts']]")" "['192.0.2.1', '192.0.2.5']"
    expect_eq "the neighbour seen in the table, with its MAC address" "$(get "$result" "[(h['seen'], h['mac']) for h in a['data']['hosts'] if h['address'] == '192.0.2.5'][0]")" "(['arp'], '52:54:00:12:34:56')"
fi
scan=$id

# ICMP is the kernel's to allow, by group: what the run could do must match.
gid=$(id -g cockpit-fleet)
range=$(sysctl -n net.ipv4.ping_group_range)
low=$(echo "$range" | awk '{print $1}'); high=$(echo "$range" | awk '{print $2}')
if [ "$gid" -ge "$low" ] && [ "$gid" -le "$high" ]; then want=True; else want=False; fi
expect_eq "ICMP used as ping_group_range ($range) allows it" "$(get "$result" "a['data']['run']['options']['icmp']")" "$want"
if [ "$want" = True ]; then
    expect_eq "and the controller answered it" "$(get "$result" "'icmp' in [h for h in a['data']['hosts'] if h['address'] == '$address'][0]['seen']")" True
fi

expect_eq "the output is not for operators" "$(get "$(ctl itoperator run.log "{\"id\": \"$id\"}")" "a['error']['code']")" forbidden
expect_eq "nor the result" "$(get "$(ctl itoperator netscan.result "{\"id\": \"$id\"}")" "a['error']['code']")" forbidden
expect_eq "an administrator reads the output" "$(get "$(admin run.log "{\"id\": \"$id\"}")" "'hosts found' in a['data']['text']")" True
expect_eq "the run's file is taken in, then removed" "$(test -e "/var/lib/cockpit-fleet/runs/$id/netscan.json" && echo kept || echo gone)" gone
expect_eq "the scan is in the audit trail" "$(db "SELECT count(*) FROM audit WHERE command = 'netscan.start' AND outcome = 'ok'")" 1

# A larger scan, canceled while it runs: what it found until then is kept.
run=$(admin netscan.start "{\"subnet\": \"$wide\"}")
id=$(get "$run" "a['data']['id']")
sleep 3
admin run.cancel "{\"id\": \"$id\"}" >/dev/null
expect_run "a scan canceled" "$id" 60 canceled
result=$(admin netscan.result "{\"id\": \"$id\"}")
expect_eq "says it did not end" "$(get "$result" "a['data']['run']['options'].get('complete')")" False

if [ -n "${IT_NETSCAN_SUBNET:-}" ]; then
    finish
fi

# --- inventories ----------------------------------------------------------------
hostvars() {  # hostvars INVENTORY_ID HOST: its variables as Ansible reads the tree
    ansible-inventory -i "/var/lib/cockpit-fleet/inventories/$1/tree" --host "$2" 2>/dev/null |
        python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted((k, v) for k, v in d.items() if k in ('ansible_host', 'mac_address', 'role')))"
}
made=$(admin netscan.inventory "{\"id\": \"$scan\", \"name\": \"itscan\", \"naming\": \"address\", \"group\": \"found\", \"addresses\": [\"192.0.2.5\", \"192.0.2.1\"]}")
inv=$(get "$made" "a['data']['inventory']['id']")
expect_run "an inventory made from the scan is read" "$(get "$made" "a['data']['parse_run_id']")" 120 successful
expect_eq "with its two hosts" "$(db "SELECT json_array_length(hosts) FROM inventories WHERE id = '$inv'")" 2
expect_eq "Ansible reads the MAC address the scan saw" "$(hostvars "$inv" 192.0.2.5)" "[('mac_address', '52:54:00:12:34:56')]"

payload=$(python3 -c "import json; print(json.dumps({'name': 'itlab', 'kind': 'managed',
    'content': 'all:\n  hosts:\n    pc5:\n      ansible_host: 192.0.2.5\n',
    'files': [{'path': 'host_vars/pc5.yaml', 'content': 'role: lab\n'}]}))")
made=$(admin inventory.create "$payload")
lab=$(get "$made" "a['data']['id']")
parse=$(admin inventory.parse "{\"id\": \"$lab\"}")
expect_run "an inventory with a file of a host's variables is read" "$(get "$parse" "a['data']['run_id']")" 120 successful
expect_eq "Ansible reads that file" "$(hostvars "$lab" pc5)" "[('ansible_host', '192.0.2.5'), ('role', 'lab')]"
wrote=$(admin netscan.macs "{\"id\": \"$scan\", \"inventory_id\": \"$lab\"}")
expect_eq "the MAC address is written, the file moved" "$(get "$wrote" "[(h['host'], h['result'], h['moved']['to']) for h in a['data']['hosts']]")" "[('pc5', 'written', 'host_vars/pc5/pc5.yaml')]"
expect_eq "Ansible reads both" "$(hostvars "$lab" pc5)" "[('ansible_host', '192.0.2.5'), ('mac_address', '52:54:00:12:34:56'), ('role', 'lab')]"
again=$(admin netscan.macs "{\"id\": \"$scan\", \"inventory_id\": \"$lab\"}")
expect_eq "a second time changes nothing" "$(get "$again" "[h['result'] for h in a['data']['hosts']]")" "['unchanged']"

# --- the hosts of an inventory probed one by one ------------------------------------
payload=$(python3 -c "import json; print(json.dumps({'name': 'itprobe', 'kind': 'managed',
    'content': 'all:\n  hosts:\n    me:\n      ansible_host: 192.0.2.1\n    pc5:\n      ansible_host: 192.0.2.5\n    pc9:\n      ansible_host: 192.0.2.9\n    gone.invalid: {}\n'}))")
probe=$(get "$(admin inventory.create "$payload")" "a['data']['id']")
parse=$(admin inventory.parse "{\"id\": \"$probe\"}")
expect_run "an inventory to probe is read" "$(get "$parse" "a['data']['run_id']")" 120 successful
probed=$(ctl itoperator netscan.probe "{\"inventory_id\": \"$probe\"}")
expect_eq "an operator probes it: what each host says" "$(get "$probed" "sorted((h['name'], h['findings']) for h in a['data']['hosts'])")" "[('gone.invalid', ['unresolved']), ('me', []), ('pc5', ['segment_only']), ('pc9', ['silent'])]"
expect_eq "the neighbour with its MAC address" "$(get "$probed" "[h['mac'] for h in a['data']['hosts'] if h['name'] == 'pc5'][0]")" "52:54:00:12:34:56"

# --- a probe from a node: here the controller itself, by a local connection ---------
payload=$(python3 -c "import json; print(json.dumps({'name': 'itnode', 'kind': 'managed',
    'content': 'all:\n  hosts:\n    node:\n      ansible_connection: local\n'}))")
node=$(get "$(admin inventory.create "$payload")" "a['data']['id']")
got=$(admin run.adhoc "{\"inventory_id\": \"$node\", \"limit\": \"all\", \"module\": \"lineadicomando.fleet.net_probe\", \"args\": {\"subnet\": \"198.51.100.0/24\"}}")
expect_eq "a node may not probe a subnet that may not be scanned" "$(get "$got" "a['error']['code']")" forbidden
got=$(admin run.adhoc "{\"inventory_id\": \"$node\", \"limit\": \"all\", \"module\": \"lineadicomando.fleet.net_probe\", \"args\": {\"subnet\": \"192.0.2.0/28\"}}")
run=$(get "$got" "a['data']['id']")
expect_run "net_probe runs on the node" "$run" 120 successful
found=$(python3 -c "
import json, sys
for line in open(sys.argv[1]):
    event = json.loads(line)
    if event.get('event') == 'host_ok':
        hosts = event['result']['net_probe']['hosts']
        print(sorted((h['address'], h['mac'], 'arp' in h['seen'], 'tcp' in h['seen']) for h in hosts))
" "/var/lib/cockpit-fleet/runs/$run/events.jsonl")
expect_eq "it finds the controller and the neighbour with its MAC address" "$found" "[('192.0.2.1', '', False, True), ('192.0.2.5', '52:54:00:12:34:56', True, False)]"

finish
