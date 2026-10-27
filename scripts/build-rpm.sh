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
# Between two releases the package has the version of scripts/version.sh, in
# a %changelog entry of its own: what is installed says what it is.
set -eu

cd "$(dirname "$0")/.."

test -f dist/index.js || { echo "dist/ is missing: run 'make build' first" >&2; exit 1; }
mkdir -p rpm

if [ "${1:-}" = "--native" ]; then
    version=$(scripts/version.sh rpm)
    plain=$(scripts/version.sh plain)
    top=$(mktemp -d)
    trap 'rm -rf "$top"' EXIT
    mkdir -p "$top/SOURCES" "$top/cockpit-fleet-$version"
    # Makefile.local is not part of the project: it holds the VM targets.
    tar --exclude=./node_modules --exclude=./.git --exclude=./deb --exclude=./rpm \
        --exclude=./Makefile.local --exclude=./.env -cf - . | tar -C "$top/cockpit-fleet-$version" -xf -
    spec=$top/cockpit-fleet-$version/packaging/cockpit-fleet.spec
    if [ "$version" != "$(sed -n 's/^Version: *//p' "$spec")" ]; then
        author=$(sed -n '/^%changelog/,$s/^\* [^ ]* [^ ]* [0-9]* [0-9]* \(.*>\) - .*/\1/p' "$spec" | head -n 1)
        sed -i -e "s/^Version:.*/Version:        $version/" \
            -e "/^%changelog/a * $(LC_ALL=C date '+%a %b %d %Y') $author - $version-1\\
- Build of $plain, not a release.\\
" "$spec"
        sed -i "s/^__version__ = .*/__version__ = \"$plain\"/" \
            "$top/cockpit-fleet-$version/backend/cockpit_fleet/__init__.py"
    fi
    tar -C "$top" -czf "$top/SOURCES/cockpit-fleet-$version.tar.gz" "cockpit-fleet-$version"
    rpmbuild -bb --define "_topdir $top" "$spec"
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
    # The container has no git: what git describe says goes with it.
    podman run --rm --network=none --security-opt label=disable \
        -e FLEET_DESCRIBE="$(scripts/version.sh describe)" \
        -v "$PWD:/src:ro" -v "$PWD/rpm:/src/rpm" "$builder" /src/scripts/build-rpm.sh --native
done
ls -l rpm/*.rpm
