#!/bin/sh
# Run the integration scripts on a controller in a container: systemd as PID
# 1, the package installed, one fresh container per script, so that scripts
# that wipe the database can run side by side.
#
#   ct-test.sh DISTRO [SCRIPT...]   DISTRO is debian, fedora or alma; SCRIPT a
#                                   name in test/integration without .sh;
#                                   all of them when none is named, but
#                                   windows, measure-events and load
#   ct-test.sh clean                remove the containers and images it made
#
# The package comes from deb/ or rpm/: run 'make deb' or 'make rpm' first.
# CT_JOBS scripts run at once (2 by default); variables IT_* of the caller
# reach the scripts. Logs go to it-results/DISTRO/.
#
# Needs rootless podman on cgroup v2. The containers are privileged (in the
# user's namespace): the sandbox of a run needs to mount and to make
# namespaces. A container shares the host's kernel, has no SELinux of its own
# and shows the host's /proc/meminfo, so what depends on those still needs a
# machine.
set -eu

SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
cd "$(dirname "$0")/.."
PREFIX=cockpit-fleet-it

clean() {
    podman ps -a --format '{{.Names}}' | grep "^$PREFIX-" | xargs -r podman rm -f -v >/dev/null
    podman images --format '{{.Repository}}:{{.Tag}}' | grep "/$PREFIX:" | xargs -r podman rmi >/dev/null
}

run_one() {  # run_one DISTRO SCRIPT: one script in its own container
    distro=$1 script=$2
    name=$PREFIX-$distro-$script-$$
    log=it-results/$distro/$script.log
    # The nodes' images go on a volume: podman cannot put overlay on the
    # container's own overlay everywhere.
    podman run -d --name "$name" --hostname "fleet-$distro" --systemd=always --privileged \
        -v /var/lib/containers -v "$PWD/test/integration:/it:ro,Z" "$PREFIX:$distro" >/dev/null
    # Until systemd has booted, is-system-running fails at once: poll it.
    # "degraded" is what a container reaches (some mounts of /sys fail).
    n=0
    until podman exec "$name" systemctl is-system-running 2>/dev/null | grep -qE '^(running|degraded)$' ||
        [ "$n" -ge 60 ]; do
        sleep 1; n=$((n + 1))
    done
    set --
    for var in $(env | sed -n 's/^\(IT_[A-Z0-9_]*\)=.*/\1/p'); do set -- "$@" -e "$var"; done
    start=$(date +%s)
    if podman exec "$@" -e IT_WIPE=yes -e IT_SSH_USER=fleet-it -e IT_SSH_PASSWORD=ct-ssh-pass-4271 \
        "$name" sh "/it/$script.sh" >"$log" 2>&1; then
        status=ok
    else
        status=FAILED
    fi
    podman rm -f -v "$name" >/dev/null
    printf '%-7s %-15s %4ss  %s\n' "$status" "$script" "$(($(date +%s) - start))" "$log"
}

if [ "${1:-}" = --one ]; then
    run_one "$2" "$3"
    exit 0
fi
if [ "${1:-}" = clean ]; then
    clean
    exit 0
fi

distro=${1:-}
case "$distro" in
    debian) package=$(ls deb/cockpit-fleet_*_all.deb 2>/dev/null | tail -n 1) ;;
    fedora) package=$(ls rpm/cockpit-fleet-*.fc44.noarch.rpm 2>/dev/null | tail -n 1) ;;
    alma) package=$(ls rpm/cockpit-fleet-*.el10.noarch.rpm 2>/dev/null | tail -n 1) ;;
    *) echo "usage: $0 debian|fedora|alma [SCRIPT...] | clean" >&2; exit 2 ;;
esac
shift
[ -n "$package" ] || { echo "no package for $distro: run 'make deb' or 'make rpm' first" >&2; exit 1; }
if [ "$#" -eq 0 ]; then
    for file in test/integration/*.sh; do
        script=$(basename "$file" .sh)
        case "$script" in lib|nodes|windows|measure-events|load|lifecycle) ;; *) set -- "$@" "$script" ;; esac
    done
fi
for script in "$@"; do
    [ -f "test/integration/$script.sh" ] || { echo "no such script: $script" >&2; exit 2; }
done

echo "package: $package ($(date -r "$package" '+%Y-%m-%d %H:%M'))"
rm -rf "it-results/$distro"
mkdir -p "it-results/$distro"
build=it-results/$distro/build.log
podman build -t "$PREFIX:$distro-base" -f "test/containers/$distro.Containerfile" test/containers \
    >"$build" 2>&1 || { tail -n 20 "$build" >&2; exit 1; }
context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT
cp "$package" "$context/"
case "$distro" in
    debian) install="apt-get install -y /tmp/${package##*/}" ;;
    *) install="dnf install -y --disablerepo='*' /tmp/${package##*/}" ;;
esac
podman build -t "$PREFIX:$distro" -f - "$context" >>"$build" 2>&1 <<CONTAINERFILE || { tail -n 20 "$build" >&2; exit 1; }
FROM $PREFIX:$distro-base
COPY ${package##*/} /tmp/
RUN $install && rm /tmp/${package##*/}
CONTAINERFILE

printf '%s\n' "$@" | xargs -P "${CT_JOBS:-2}" -I{} sh "$SELF" --one "$distro" {} |
    tee "it-results/$distro/summary"
if grep -q '^FAILED' "it-results/$distro/summary"; then
    echo "Some scripts failed: see their logs." >&2
    exit 1
fi
