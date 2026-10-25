#!/bin/sh
# Upgrade, removal and purge of the package, in a container of a target
# distribution where it was never installed: test/integration/lifecycle.sh
# with an older package, built from OLD_REF, and the package of the work tree.
#
#   ct-lifecycle.sh DISTRO [OLD_REF]   DISTRO is debian, fedora or alma;
#                                      OLD_REF a commit or tag, by default
#                                      the latest tag
#
# The older package is built in a worktree of OLD_REF, with its version set
# below the current one, so that installing the newer one is an upgrade.
# Needs what make deb or make rpm needs, and rootless podman as ct-test.sh.
# With CT_KEEP_PACKAGES=DIR the two packages are also copied to DIR.
# The log goes to it-results/DISTRO/lifecycle.log.
set -eu

cd "$(dirname "$0")/.."
PREFIX=cockpit-fleet-it
distro=${1:-}
case "$distro" in
    debian) kind=deb ;;
    fedora|alma) kind=rpm ;;
    *) echo "usage: $0 debian|fedora|alma [OLD_REF]" >&2; exit 2 ;;
esac
old_ref=${2:-$(git describe --tags --abbrev=0 2>/dev/null || true)}
[ -n "$old_ref" ] || { echo "no tag yet: name the older commit" >&2; exit 2; }
git rev-parse --verify --quiet "$old_ref^{commit}" >/dev/null || { echo "no such commit: $old_ref" >&2; exit 2; }

work=$(mktemp -d)
cleanup() {
    git worktree remove --force "$work/old" 2>/dev/null || true
    rm -rf "$work"
}
trap cleanup EXIT
mkdir -p "$work/packages" "it-results/$distro"
log=it-results/$distro/lifecycle.log
: > "$log"

package_of() {  # package_of TREE: the package built there for DISTRO
    case "$distro" in
        debian) ls "$1"/deb/cockpit-fleet_*_all.deb ;;
        fedora) ls "$1"/rpm/cockpit-fleet-*.fc44.noarch.rpm ;;
        alma) ls "$1"/rpm/cockpit-fleet-*.el10.noarch.rpm ;;
    esac | tail -n 1
}
build() {  # build TREE: the bundle and the package of DISTRO
    (cd "$1" && make build >/dev/null &&
        if [ "$kind" = deb ]; then scripts/build-deb.sh; else scripts/build-rpm.sh "$distro"; fi) >>"$log" 2>&1 ||
        { tail -n 20 "$log" >&2; exit 1; }
}

echo "older package: $old_ref"
git worktree add --detach --quiet "$work/old" "$old_ref"
ln -s "$PWD/node_modules" "$work/old/node_modules"
# Below any version of the project: 0.0.0.
sed -i '1s/(\([^)]*\))/(0.0.0~old)/' "$work/old/debian/changelog"
sed -i 's/^Version:.*/Version:        0.0.0/' "$work/old/packaging/cockpit-fleet.spec"
build "$work/old"
cp "$(package_of "$work/old")" "$work/packages/old.$kind"
echo "newer package: the work tree"
build .
cp "$(package_of .)" "$work/packages/new.$kind"
# To run lifecycle.sh on a machine as well: the two packages are kept there.
if [ -n "${CT_KEEP_PACKAGES:-}" ]; then
    mkdir -p "$CT_KEEP_PACKAGES" && cp "$work/packages/old.$kind" "$work/packages/new.$kind" "$CT_KEEP_PACKAGES/"
fi

podman build -t "$PREFIX:$distro-base" -f "test/containers/$distro.Containerfile" test/containers >>"$log" 2>&1 ||
    { tail -n 20 "$log" >&2; exit 1; }
name=$PREFIX-$distro-lifecycle-$$
podman run -d --name "$name" --hostname "fleet-$distro" --systemd=always --privileged \
    -v "$PWD/test/integration:/it:ro,Z" -v "$work/packages:/packages:ro,Z" "$PREFIX:$distro-base" >/dev/null
trap 'podman rm -f -v "$name" >/dev/null 2>&1; cleanup' EXIT
n=0
until podman exec "$name" systemctl is-system-running 2>/dev/null | grep -qE '^(running|degraded)$' ||
    [ "$n" -ge 60 ]; do
    sleep 1; n=$((n + 1))
done
status=0
podman exec -e IT_PKG_OLD="/packages/old.$kind" -e IT_PKG_NEW="/packages/new.$kind" \
    "$name" sh /it/lifecycle.sh >>"$log" 2>&1 || status=1
grep -E '^(PASS|FAIL)|checks' "$log" | tail -n 60
exit "$status"
