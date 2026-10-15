#!/bin/sh
# Build the .rpm for Fedora 44 and AlmaLinux 10 and check it with rpmlint.
# Output goes to rpm/.
#
#   build-rpm.sh [fedora|alma]...   in a container of each distribution
#                                   (podman), both when none is named
#   build-rpm.sh --native           directly, on a system that has rpm-build
#                                   and rpmlint (the containers above)
#
# dist/ must already be built: the package build needs neither Node.js nor network.
set -eu

cd "$(dirname "$0")/.."

test -f dist/index.js || { echo "dist/ is missing: run 'make build' first" >&2; exit 1; }
mkdir -p rpm

if [ "${1:-}" = "--native" ]; then
    version=$(sed -n 's/^Version: *//p' packaging/cockpit-fleet.spec)
    top=$(mktemp -d)
    trap 'rm -rf "$top"' EXIT
    mkdir -p "$top/SOURCES"
    # Makefile.local is not part of the project: it holds the VM targets.
    tar --exclude=./node_modules --exclude=./.git --exclude=./deb --exclude=./rpm \
        --exclude=./Makefile.local --transform "s,^\.,cockpit-fleet-$version," \
        -czf "$top/SOURCES/cockpit-fleet-$version.tar.gz" .
    rpmbuild -bb --define "_topdir $top" packaging/cockpit-fleet.spec
    rpmlint -r packaging/cockpit-fleet.rpmlintrc "$top"/RPMS/noarch/*.rpm
    cp "$top"/RPMS/noarch/*.rpm rpm/
    exit 0
fi

[ "$#" -gt 0 ] || set -- fedora alma
for target in "$@"; do
    case "$target" in
        fedora) image=registry.fedoraproject.org/fedora:44 ;;
        alma) image=docker.io/library/almalinux:10 ;;
        *) echo "unknown target: $target" >&2; exit 2 ;;
    esac
    builder=cockpit-fleet-builder:$target
    if ! podman image exists "$builder"; then
        # rpmlint is in EPEL on AlmaLinux.
        podman build -t "$builder" - <<CONTAINERFILE
FROM $image
RUN { [ "$target" = fedora ] || dnf install -y epel-release; } \
 && dnf install -y rpm-build make systemd-rpm-macros rpmlint \
 && dnf clean all
CONTAINERFILE
    fi
    podman run --rm --network=none --security-opt label=disable \
        -v "$PWD:/src:ro" -v "$PWD/rpm:/src/rpm" "$builder" /src/scripts/build-rpm.sh --native
done
ls -l rpm/*.rpm
