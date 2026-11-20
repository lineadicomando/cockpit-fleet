#!/bin/sh
# The scan of a subnet on the installed package: the subnets the controller
# is attached to, refusals (a role, a subnet outside them, one too large), a
# scan in the sandbox of a run that finds the controller itself with a port
# open there, ICMP when net.ipv4.ping_group_range lets the service account
# (opened to it for the test on the dummy interface, as it was otherwise),
# the output kept from operators, and a scan canceled keeping what it found.
# By default it scans 192.0.2.0/28, a subnet for documentation put on a
# dummy interface for the test, where only the controller answers: nothing
# leaves the machine (in a rootless container the network may be the host's).
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
    ip link add "$DUMMY" type dummy && ip addr add 192.0.2.1/24 dev "$DUMMY" && ip link set "$DUMMY" up ||
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
    expect_eq "and nothing else, where nothing else is" "$(get "$result" "len(a['data']['hosts'])")" 1
fi

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

finish
