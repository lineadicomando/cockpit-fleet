#!/bin/sh
# Measure what the events callback costs on a large inventory: run time with
# and without it, and the size of the events file. Hosts are aliases of the
# controller reached with the local connection, so only Ansible itself is
# measured. Usage: measure-events.sh [HOSTS] [FORKS]
set -eu
. "$(dirname "$0")/lib.sh"
require_root

HOSTS=${1:-200}
FORKS=${2:-10}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

i=1
while [ "$i" -le "$HOSTS" ]; do
    printf 'node%03d ansible_connection=local ansible_python_interpreter=/usr/bin/python3\n' "$i"
    i=$((i + 1))
done > "$WORK/hosts"
cat > "$WORK/site.yml" <<'YAML'
- hosts: all
  gather_facts: true
  tasks:
    - ansible.builtin.command: uptime
      changed_when: false
    - ansible.builtin.stat:
        path: /etc/os-release
    - ansible.builtin.debug:
        msg: "{{ ansible_facts.distribution }} {{ ansible_facts.distribution_version }}"
YAML
chown -R cockpit-fleet:cockpit-fleet "$WORK"

play() {  # play LABEL [ENV=VALUE...]
    label=$1; shift
    start=$(date +%s%N)
    sudo -u cockpit-fleet env -i PATH=/usr/bin:/bin HOME="$WORK" LANG=C.UTF-8 \
        ANSIBLE_COLLECTIONS_PATH=/usr/share/cockpit-fleet/collections "$@" \
        ansible-playbook -i "$WORK/hosts" --forks "$FORKS" "$WORK/site.yml" > "$WORK/$label.out" 2>&1 ||
        { tail -n 20 "$WORK/$label.out"; exit 1; }
    end=$(date +%s%N)
    printf '%-28s %4d s\n' "$label" "$(((end - start) / 1000000000))"
}
size() { printf '%-28s %8d bytes, %d lines, %d bytes per host\n' "$1" "$(stat -c %s "$2")" "$(wc -l < "$2")" "$(($(stat -c %s "$2") / HOSTS))"; }

echo "$HOSTS hosts, $FORKS forks, 4 tasks per host"
play without-callback
play with-callback ANSIBLE_CALLBACKS_ENABLED=lineadicomando.fleet.events FLEET_EVENTS_FILE="$WORK/events.jsonl"
play with-callback-and-facts ANSIBLE_CALLBACKS_ENABLED=lineadicomando.fleet.events \
    FLEET_EVENTS_FILE="$WORK/events-facts.jsonl" FLEET_EVENTS_KEEP_FACTS=1
size "events" "$WORK/events.jsonl"
size "events with facts" "$WORK/events-facts.jsonl"
size "human-readable output" "$WORK/with-callback.out"
