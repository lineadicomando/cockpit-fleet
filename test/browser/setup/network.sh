#!/bin/sh
# The data of the scenario network-scan: a VM of the lab as the controller
# ('BROWSER_LAB=deb13 browser.sh setup network'), the Windows node win11 up on the
# lab's network, and an inventory "aula-win" written in Fleet that names it
# by address, as the teacher's PC, with a PC that is off. No scan: the
# scenario makes it. Starts from nothing: it deletes the database, the runs
# and the projects first.
set -eu
IT=${IT_DIR:-/it}
. "$IT/lib.sh"
require_root
USER=${BROWSER_USER:-tester}
as() { ctl "$USER" "$@"; }
# The lab's network, as lab.conf has it: win11 at .20, nothing at .31.
NET=${BROWSER_SUBNET:-192.168.140}

echo "clearing"
wipe || true  # lib.sh is written for set -u alone

echo "inventory"
hosts="[aula]\\npc-docente ansible_host=$NET.20\\npc-01 ansible_host=$NET.31\\n"
hosts="$hosts[aula:vars]\\nansible_shell_type=powershell\\n"
answer=$(as inventory.create "{\"name\": \"aula-win\", \"kind\": \"managed\", \"format\": \"ini\", \"content\": \"$hosts\"}")
inventory_id=$(get "$answer" 'a["data"]["id"]') || true
[ -n "$inventory_id" ] || { echo "setup: the inventory was not made: $answer" >&2; exit 1; }
parse=$(as inventory.parse "{\"id\": \"$inventory_id\"}")
run_id=$(get "$parse" 'a["data"]["run_id"]') || true
[ -n "$run_id" ] && [ "$(wait_status "$run_id" 90)" = successful ] ||
    { echo "setup: the inventory was not read: $parse" >&2; exit 1; }

echo "ready: $(get "$(as netscan.subnets)" '", ".join(s["subnet"] for s in a["data"]["subnets"])') may be scanned"
