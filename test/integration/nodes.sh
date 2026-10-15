#!/bin/sh
# Linux nodes to run against: containers with sshd, Python and sudo, on fixed
# addresses of podman's default network. Needs podman and root.
#
#   nodes.sh up N AUTHORIZED_KEYS_FILE    start fleet-node-1..N (10.88.0.101 onwards)
#   nodes.sh recreate I AUTHORIZED_KEYS_FILE   replace node I: same address, new host keys
#   nodes.sh down                         remove them all
#   nodes.sh address I                    print the address of node I
set -eu

IMAGE=localhost/fleet-node:trixie
address() { echo "10.88.0.$((100 + $1))"; }

build() {
    podman image exists "$IMAGE" && return
    podman build -q -t "$IMAGE" - <<'CONTAINERFILE'
FROM docker.io/library/debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server python3 sudo git \
 && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \
 && useradd -m -s /bin/bash ansible \
 && echo 'ansible ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/ansible \
 && mkdir -p /run/sshd /home/ansible/.ssh \
 && printf 'PasswordAuthentication no\nPermitRootLogin no\n' > /etc/ssh/sshd_config.d/fleet.conf
# Host keys are made at first start, so every node has its own.
CMD ["/bin/sh", "-c", "ssh-keygen -A >/dev/null && exec /usr/sbin/sshd -D -e"]
CONTAINERFILE
}

start() {  # start INDEX AUTHORIZED_KEYS_FILE
    # sshd writes an audit record for a session with a terminal, and closes it
    # when it cannot: the podman of the Enterprise Linux family does not grant
    # that by default, and ansible-core before 2.19 asks for a terminal. z lets
    # the node read the key file where SELinux is enforcing.
    podman run -d --quiet --name "fleet-node-$1" --hostname "node$1" --ip "$(address "$1")" \
        --cap-add AUDIT_WRITE -v "$2:/home/ansible/.ssh/authorized_keys:ro,z" "$IMAGE" >/dev/null
}

case "${1:-}" in
    up)
        build
        i=1
        while [ "$i" -le "$2" ]; do start "$i" "$3"; i=$((i + 1)); done
        # Wait until the last one accepts connections.
        n=0
        until ssh-keyscan -T 1 "$(address "$2")" >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n + 1)); done
        ;;
    recreate)
        podman rm -f "fleet-node-$2" >/dev/null
        start "$2" "$3"
        n=0
        until ssh-keyscan -T 1 "$(address "$2")" >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n + 1)); done
        ;;
    down)
        podman ps -a --format '{{.Names}}' | grep '^fleet-node-' | xargs -r podman rm -f >/dev/null
        ;;
    address)
        address "$2"
        ;;
    *)
        echo "usage: nodes.sh up N KEYS | recreate I KEYS | down | address I" >&2
        exit 2
        ;;
esac
