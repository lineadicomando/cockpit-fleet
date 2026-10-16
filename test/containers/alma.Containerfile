# An AlmaLinux 10 controller for the integration scripts; see
# debian.Containerfile. sshpass is in EPEL.
FROM docker.io/library/almalinux:10
RUN dnf install -y epel-release \
 && dnf install -y \
      systemd sudo openssh-server openssh-clients procps-ng iproute passwd \
      python3 git-core ansible-core sshpass cockpit-bridge cockpit-ws \
      podman nftables \
 && dnf clean all
COPY make-rshared.service /etc/systemd/system/
RUN systemctl enable make-rshared.service sshd \
 && useradd -m fleet-it && echo 'fleet-it:ct-ssh-pass-4271' | chpasswd
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
