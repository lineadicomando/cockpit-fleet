#!/bin/sh
# Tests in a browser: a controller in a container that stays up, with Cockpit
# on a port of this machine, and a browser in another container that goes
# through a scenario of scenarios/.
#
#   browser.sh up [DISTRO]        build and start the controller (debian by default)
#   browser.sh setup NAME         prepare its data: setup/NAME.sh inside it, as root
#   browser.sh test SCENARIO      go through scenarios/SCENARIO.mjs
#   browser.sh with COMMAND...    run COMMAND with BROWSER_URL, BROWSER_USER and
#                                 BROWSER_PASSWORD in its environment, the
#                                 controller reachable at that URL meanwhile
#   browser.sh shell              a root shell in the controller
#   browser.sh down               remove the controller, its nodes and its data
#
# With BROWSER_LAB=TARGET (deb13, fed44, alma10) the controller is that VM of
# the lab, with the package deployed ('lab.sh deploy'), instead of the
# container: 'setup' copies test/integration and test/browser there and runs
# the setup as root; the account the browser logs in with is a throwaway
# "tester", made by the first 'setup' with a random password that stays in
# $XDG_RUNTIME_DIR/cockpit-fleet-browser/ (gone with 'lab.sh reset'); 'test'
# and 'with' open the tunnel of 'lab.sh' on BROWSER_PORT (9091) for the time
# they take. 'up' and 'down' are lab.sh's 'reset', 'start' and 'deploy'.
#
# Variables: BROWSER_PORT (9093) where Cockpit answers, on 127.0.0.1 only;
# BROWSER_URL instead of it, for a controller elsewhere (a VM through
# 'lab.sh tunnel'); BROWSER_USER and BROWSER_PASSWORD, the account the
# browser logs in with (on the controller of 'up', the throwaway account
# below); BROWSER_SIZE (1920x1080); BROWSER_LANG (it); BROWSER_SUBNET, the
# lab's (192.168.140); BROWSER_STEPS (3-5, or 4) only those steps. They may
# be set in .env too.
#
# The package comes from deb/ or rpm/, as for 'make ct-test'. Needs rootless
# podman on cgroup v2, nothing else of the host.
set -eu

cd "$(dirname "$0")/../.."
ROOT=$PWD
# Only the BROWSER_ lines of .env, taken as they are (it is not shell: the value
# is everything after "="); the environment wins.
if [ -f .env ]; then
    while IFS= read -r line; do
        case "$line" in
            BROWSER_[A-Z_]*=*)
                key=${line%%=*} value=${line#*=}
                case "$value" in "~/"*) value=$HOME/${value#"~/"} ;; esac
                eval "[ -n \"\${$key+set}\" ]" || export "$key=$value" ;;
        esac
    done < .env
fi
NAME=cockpit-fleet-browser
IMAGE=localhost/cockpit-fleet-browser:1
LAB=${BROWSER_LAB:-}
LABSH=$ROOT/test/vm/lab.sh
BROWSER_USER=${BROWSER_USER:-tester}
die() { echo "browser.sh: $*" >&2; exit 1; }
if [ -n "$LAB" ]; then
    PORT=${BROWSER_PORT:-9091}
    SECRETS=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/cockpit-fleet-browser
    PWFILE=$SECRETS/$LAB.pw
    [ -n "${BROWSER_PASSWORD:-}" ] || [ ! -f "$PWFILE" ] || BROWSER_PASSWORD=$(cat "$PWFILE")
else
    PORT=${BROWSER_PORT:-9093}
    # Throwaway: the controller of 'up' answers on 127.0.0.1 only and is deleted by 'down'.
    BROWSER_PASSWORD=${BROWSER_PASSWORD:-tester-pass-5813}
fi

running() { podman container exists "$NAME" && [ "$(podman inspect -f '{{.State.Running}}' "$NAME")" = true ]; }

up() {
    distro=${1:-debian}
    case "$distro" in
        debian) package=$(ls -t deb/cockpit-fleet_*_all.deb 2>/dev/null | head -n 1)
                install="apt-get update && apt-get install -y --no-install-recommends cockpit-system /tmp/pkg/*.deb && apt-get clean" ;;
        fedora) package=$(ls -t rpm/cockpit-fleet-*.fc44.noarch.rpm 2>/dev/null | head -n 1)
                install="dnf install -y cockpit-system /tmp/pkg/*.rpm && dnf clean all" ;;
        alma) package=$(ls -t rpm/cockpit-fleet-*.el10.noarch.rpm 2>/dev/null | head -n 1)
              install="dnf install -y cockpit-system /tmp/pkg/*.rpm && dnf clean all" ;;
        *) die "usage: browser.sh up debian|fedora|alma" ;;
    esac
    [ -n "$package" ] || die "no package for $distro: run 'make deb' or 'make rpm' first"
    running && die "$NAME is already up: 'browser.sh down' first"
    echo "package: $package"
    mkdir -p it-results/browser
    log=it-results/browser/build.log
    podman build -t "cockpit-fleet-it:$distro-base" -f "test/containers/$distro.Containerfile" \
        test/containers >"$log" 2>&1 || { tail -n 20 "$log" >&2; exit 1; }
    context=$(mktemp -d)
    trap 'rm -rf "$context"' EXIT
    cp "$package" "$context/"
    # Cockpit's shell (cockpit-system) for the menu and the language; plain
    # HTTP, since what reaches it through podman's port comes from no local
    # address, and Cockpit would send it to HTTPS. The units a container
    # cannot start are masked, so that Cockpit shows no failed service.
    podman build -t "$NAME:$distro" -f - "$context" >>"$log" 2>&1 <<CONTAINERFILE || { tail -n 20 "$log" >&2; exit 1; }
FROM cockpit-fleet-it:$distro-base
COPY ${package##*/} /tmp/pkg/
RUN $install && rm -rf /tmp/pkg \
 && mkdir -p /etc/cockpit && printf '[WebService]\nAllowUnencrypted = true\n' > /etc/cockpit/cockpit.conf \
 && systemctl enable cockpit.socket \
 && systemctl mask proc-sys-fs-binfmt_misc.automount sys-kernel-config.mount sys-kernel-debug.mount \
      sys-kernel-tracing.mount systemd-modules-load.service \
 && useradd -m -s /bin/bash -G cockpit-fleet-admin $BROWSER_USER \
 && echo '$BROWSER_USER:$BROWSER_PASSWORD' | chpasswd
CONTAINERFILE
    podman run -d --name "$NAME" --hostname fleet --systemd=always --privileged \
        -p "127.0.0.1:$PORT:9090" -v /var/lib/containers \
        -v "$ROOT/test/integration:/it:ro,Z" -v "$ROOT/test/browser:/browser:ro,Z" "$NAME:$distro" >/dev/null
    n=0
    until podman exec "$NAME" systemctl is-system-running 2>/dev/null | grep -qE '^(running|degraded)$' ||
        [ "$n" -ge 60 ]; do
        sleep 1; n=$((n + 1))
    done
    echo "up: http://127.0.0.1:$PORT (account $BROWSER_USER, administrator)"
}

image() {
    podman image exists "$IMAGE" && return
    podman build -t "$IMAGE" -f test/browser/browser.Containerfile test/browser >it-results/browser/image.log 2>&1 ||
        { tail -n 20 it-results/browser/image.log >&2; exit 1; }
}

# The account of the tests on a VM: made once, its password random,
# never on a command line or in the conversation.
lab_account() {
    [ -n "${BROWSER_PASSWORD:-}" ] && return
    mkdir -p "$SECRETS"
    chmod 700 "$SECRETS"
    (umask 077 && head -c 18 /dev/urandom | base64 | tr -d '/+=\n' > "$PWFILE")
    BROWSER_PASSWORD=$(cat "$PWFILE")
    printf '%s:%s\n' "$BROWSER_USER" "$BROWSER_PASSWORD" | "$LABSH" root "$LAB" \
        "id $BROWSER_USER >/dev/null 2>&1 || useradd -m -s /bin/bash -G cockpit-fleet-admin $BROWSER_USER; chpasswd"
}

lab_setup() {  # lab_setup NAME
    lab_account
    src=/run/fleet-browser-src
    # The admin account of the lab's VMs, which the Windows node accepts a key for.
    win_user=$(. "$ROOT/test/vm/lab.conf" && echo "$LAB_USER")
    tar -cf - test/integration test/browser | "$LABSH" root "$LAB" \
        "rm -rf $src && mkdir -p $src && tar -xf - -C $src && chmod -R a+rX $src && \
         IT_DIR=$src/test/integration BROWSER_DIR=$src/test/browser BROWSER_USER=$BROWSER_USER BROWSER_WIN_USER=$win_user \
         sh $src/test/browser/setup/$1.sh"
}

# The tunnel to the VM's Cockpit, for as long as this runs.
lab_tunnel() {
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then return; fi
    "$LABSH" tunnel "$LAB" "$PORT" >/dev/null 2>&1 &
    TUNNEL=$!
    trap 'kill $TUNNEL 2>/dev/null' EXIT
    n=0
    until curl -s -o /dev/null "http://127.0.0.1:$PORT/" || [ "$n" -ge 30 ]; do sleep 1; n=$((n + 1)); done
}

# The controller reachable at $url: the tunnel to a VM opened, or the
# container checked.
reach() {
    url=${BROWSER_URL:-http://127.0.0.1:$PORT}
    if [ -n "$LAB" ]; then
        [ -n "${BROWSER_PASSWORD:-}" ] || die "no account on $LAB yet: 'browser.sh setup' first"
        [ -n "${BROWSER_URL:-}" ] || lab_tunnel
    else
        [ -n "${BROWSER_URL:-}" ] || running || die "$NAME is not up: 'browser.sh up' first"
    fi
}

drive() {  # drive SCENARIO
    [ -f "test/browser/scenarios/$1.mjs" ] || die "no such scenario: $1"
    reach
    mkdir -p it-results/browser
    image
    out=$ROOT/it-results/browser/$1
    rm -rf "$out"
    mkdir -p "$out"
    # The password reaches the browser through the environment of its
    # container, never its command line. The network is the host's, for
    # 127.0.0.1 and the lab's tunnel.
    BROWSER_PASSWORD=$BROWSER_PASSWORD podman run --rm --network host --init --ipc host \
        -e BROWSER_PASSWORD -e "BROWSER_USER=$BROWSER_USER" -e "BROWSER_URL=$url" \
        -e "BROWSER_SCENARIO=$1" -e "BROWSER_SIZE=${BROWSER_SIZE:-1920x1080}" \
        -e "BROWSER_LANG=${BROWSER_LANG:-it}" -e "BROWSER_SUBNET=${BROWSER_SUBNET:-192.168.140}" \
        -e "BROWSER_STEPS=${BROWSER_STEPS:-}" \
        -v "$ROOT/test/browser:/browser:ro,Z" -v "$ROOT/po:/po:ro,Z" -v "$out:/out:Z" \
        "$IMAGE" node /browser/run.mjs
}

case "${1:-}" in
    up)
        if [ -n "$LAB" ]; then
            rm -f "$PWFILE"
            "$LABSH" reset "$LAB" && "$LABSH" start "$LAB" && "$LABSH" wait "$LAB" && "$LABSH" deploy "$LAB"
        else
            up "${2:-}"
        fi ;;
    setup)
        [ -f "test/browser/setup/${2:-}.sh" ] || die "no such setup: ${2:-}"
        if [ -n "$LAB" ]; then
            lab_setup "$2"
        else
            running || die "$NAME is not up"
            podman exec -e "BROWSER_USER=$BROWSER_USER" "$NAME" sh "/browser/setup/$2.sh"
        fi ;;
    test) drive "${2:-}" ;;
    with)
        [ $# -ge 2 ] || die "usage: browser.sh with COMMAND..."
        shift
        reach
        # Not exec: the tunnel goes when this shell does.
        BROWSER_URL=$url BROWSER_USER=$BROWSER_USER BROWSER_PASSWORD=$BROWSER_PASSWORD "$@" ;;
    shell) if [ -n "$LAB" ]; then "$LABSH" ssh "$LAB"; else podman exec -it "$NAME" bash; fi ;;
    down)
        if [ -n "$LAB" ]; then
            rm -f "$PWFILE"
            "$LABSH" reset "$LAB"
        else
            podman rm -f -v "$NAME" >/dev/null 2>&1 || true
        fi ;;
    *) sed -n '2,33p' "$ROOT/test/browser/browser.sh" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
