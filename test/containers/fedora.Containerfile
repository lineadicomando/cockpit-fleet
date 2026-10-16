# A Fedora 44 controller for the integration scripts; see debian.Containerfile.
FROM registry.fedoraproject.org/fedora:44
# The image leaves out documentation; cli.sh looks for the manual.
RUN sed -i '/^tsflags=nodocs/d' /etc/dnf/dnf.conf \
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
