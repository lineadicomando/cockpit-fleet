# A Debian 13 controller for the integration scripts: systemd as PID 1, what
# the package depends on, and podman for the nodes of nodes.sh. The package
# itself is added by scripts/ct-test.sh.
FROM docker.io/library/debian:13
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus sudo openssh-server openssh-client procps iproute2 \
      ca-certificates python3 git ansible-core sshpass cockpit-bridge cockpit-ws \
      podman crun netavark aardvark-dns passt nftables fuse-overlayfs \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY make-rshared.service /etc/systemd/system/
# The account the scripts log in to over SSH (IT_SSH_USER).
RUN systemctl enable make-rshared.service ssh \
 && useradd -m -s /bin/bash fleet-it && echo 'fleet-it:ct-ssh-pass-4271' | chpasswd
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
