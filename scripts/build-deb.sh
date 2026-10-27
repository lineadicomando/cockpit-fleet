#!/bin/sh
# Build the .deb for Debian 13 and check it with lintian. Output goes to deb/.
#
#   build-deb.sh            in a Debian 13 container (podman), so that the
#                           result does not depend on the development host
#   build-deb.sh --native   directly, on a Debian 13 system that has debhelper
#                           and lintian (the CI runner, or the container above)
#
# dist/ must already be built: the package build needs neither Node.js nor network.
# Between two releases the package has the version of scripts/version.sh, in
# a debian/changelog entry of its own: what is installed says what it is.
set -eu

IMAGE=cockpit-fleet-builder:trixie
cd "$(dirname "$0")/.."

test -f dist/index.js || { echo "dist/ is missing: run 'make build' first" >&2; exit 1; }
mkdir -p deb

# stamp_version TREE: the version of this build, when it is not the release
# that debian/changelog begins with, in the copy TREE.
stamp_version() {
    version=$(scripts/version.sh deb)
    plain=$(scripts/version.sh plain)
    released=$(sed -n '1s/^cockpit-fleet (\([^)]*\)).*/\1/p' "$1/debian/changelog")
    [ "$version" != "$released" ] || return 0
    author=$(sed -n 's/^ -- \(.*>\)  .*/\1/p' "$1/debian/changelog" | head -n 1)
    {
        printf 'cockpit-fleet (%s) unstable; urgency=medium\n\n' "$version"
        printf '  * Build of %s, not a release.\n\n' "$plain"
        printf ' -- %s  %s\n\n' "$author" "$(LC_ALL=C date -R)"
        cat "$1/debian/changelog"
    } > "$1/debian/changelog.new"
    mv "$1/debian/changelog.new" "$1/debian/changelog"
    sed -i "s/^__version__ = .*/__version__ = \"$plain\"/" "$1/backend/cockpit_fleet/__init__.py"
}

if [ "${1:-}" = "--native" ]; then
    # dpkg-buildpackage writes next to the source tree: work on a copy.
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    mkdir "$work/cockpit-fleet"
    tar --exclude=./node_modules --exclude=./.git --exclude=./deb --exclude=./.env -cf - . |
        tar -C "$work/cockpit-fleet" -xf -
    stamp_version "$work/cockpit-fleet"
    (cd "$work/cockpit-fleet" && dpkg-buildpackage -b --no-sign)
    root_flag=
    [ "$(id -u)" -ne 0 ] || root_flag=--allow-root
    lintian $root_flag --tag-display-limit 0 --fail-on error,warning "$work"/*.changes
    cp "$work"/*.deb deb/
else
    if ! podman image exists "$IMAGE"; then
        podman build -t "$IMAGE" - <<'CONTAINERFILE'
FROM docker.io/library/debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential debhelper lintian \
 && rm -rf /var/lib/apt/lists/*
CONTAINERFILE
    fi
    # The container has no git: what git describe says goes with it.
    podman run --rm --network=none --security-opt label=disable \
        -e FLEET_DESCRIBE="$(scripts/version.sh describe)" \
        -v "$PWD:/src:ro" -v "$PWD/deb:/src/deb" "$IMAGE" /src/scripts/build-deb.sh --native
fi
ls -l deb/*.deb
