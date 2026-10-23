#!/bin/sh
# Run as root on a Linux VM of the lab by build-linux.sh, after cloud-init:
# brings the system up to date and installs what a controller needs besides
# the package (which comes later, with lab.sh deploy).
#   provision.sh debian|fedora|alma
set -eu
family=${1:?usage: provision.sh debian|fedora|alma}

case $family in
    debian)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -q
        apt-get full-upgrade -y -q
        apt-get install -y -q cockpit podman sshpass git acl ansible-core ansible-lint qemu-guest-agent
        apt-get clean ;;
    fedora)
        dnf upgrade -y -q
        dnf install -y -q cockpit podman sshpass git-core acl ansible-core ansible-lint qemu-guest-agent
        dnf clean all -q ;;
    alma)
        # sshpass is in EPEL; ansible-lint is not packaged for EL 10.
        dnf install -y -q epel-release
        dnf upgrade -y -q
        dnf install -y -q cockpit podman sshpass git-core acl ansible-core qemu-guest-agent
        dnf clean all -q ;;
    *) echo "unknown family: $family" >&2; exit 2 ;;
esac

systemctl enable --now cockpit.socket
if systemctl is-active -q firewalld; then
    firewall-cmd -q --permanent --add-service=cockpit && firewall-cmd -q --reload
fi
# The seed ISO is gone after the build: cloud-init would only look for it at
# every boot.
touch /etc/cloud/cloud-init.disabled

. /etc/os-release
echo "provisioned: $PRETTY_NAME, $(ansible --version | head -1), SELinux $(getenforce 2>/dev/null || echo none)"
