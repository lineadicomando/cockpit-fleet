#!/bin/sh
# Build the .deb in a Debian 13 container, so that the result does not depend
# on the packaging tools of the development host. Output goes to deb/.
#
# dist/ must already be built: the container has no Node.js and needs no network.
set -eu

IMAGE=cockpit-fleet-builder:trixie
cd "$(dirname "$0")/.."

test -f dist/index.js || { echo "dist/ is missing: run 'make build' first" >&2; exit 1; }

if ! podman image exists "$IMAGE"; then
    podman build -t "$IMAGE" - <<'CONTAINERFILE'
FROM docker.io/library/debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential debhelper lintian \
 && rm -rf /var/lib/apt/lists/*
CONTAINERFILE
fi

mkdir -p deb
podman run --rm --network=none --security-opt label=disable \
    -v "$PWD:/src:ro" -v "$PWD/deb:/out" "$IMAGE" sh -euc '
        mkdir /build
        cp -a /src /build/cockpit-fleet
        cd /build/cockpit-fleet
        rm -rf node_modules deb .git
        dpkg-buildpackage -b --no-sign
        lintian --tag-display-limit 0 --allow-root ../*.changes || true
        cp ../*.deb /out/
    '
ls -l deb/*.deb
