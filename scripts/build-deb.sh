#!/bin/sh
# Build the .deb for Debian 13 and check it with lintian. Output goes to deb/.
#
#   build-deb.sh            in a Debian 13 container (podman), so that the
#                           result does not depend on the development host
#   build-deb.sh --native   directly, on a Debian 13 system that has debhelper
#                           and lintian (the CI runner, or the container above)
#
# dist/ must already be built: the package build needs neither Node.js nor network.
set -eu

IMAGE=cockpit-fleet-builder:trixie
cd "$(dirname "$0")/.."

test -f dist/index.js || { echo "dist/ is missing: run 'make build' first" >&2; exit 1; }
mkdir -p deb

if [ "${1:-}" = "--native" ]; then
    # dpkg-buildpackage writes next to the source tree: work on a copy.
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    mkdir "$work/cockpit-fleet"
    tar --exclude=./node_modules --exclude=./.git --exclude=./deb -cf - . |
        tar -C "$work/cockpit-fleet" -xf -
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
    podman run --rm --network=none --security-opt label=disable \
        -v "$PWD:/src:ro" -v "$PWD/deb:/src/deb" "$IMAGE" /src/scripts/build-deb.sh --native
fi
ls -l deb/*.deb
