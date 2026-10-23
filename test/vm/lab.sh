#!/usr/bin/env bash
# The test VMs of cockpit-fleet (lab.conf, .env): build, lifecycle, deploy,
# integration scripts. TARGET is deb13, fed44, alma10 (controllers) or win11
# (a Windows node). Run from anywhere; nothing here needs sudo on the host.
#
#   lab.sh status                       VMs, state, address, snapshots
#   lab.sh net                          create the lab's network if missing
#   lab.sh build TARGET                 build the VM from scratch, snapshot "baseline"
#   lab.sh rebuild TARGET               destroy, then build
#   lab.sh destroy TARGET               remove the VM, its disks and snapshots
#   lab.sh resources TARGET...          would these fit on the host now?
#   lab.sh start TARGET...              start (refuses if the host is short)
#   lab.sh stop TARGET...|all           clean shutdown, waits until off
#   lab.sh snapshot TARGET NAME         snapshot of a shut-off VM
#   lab.sh revert TARGET NAME           revert a shut-off VM to NAME
#   lab.sh reset [TARGET...]            stop, revert to baseline (all built VMs by default)
#   lab.sh wait TARGET                  until SSH answers
#   lab.sh ssh TARGET [COMMAND]         SSH as the admin account (PowerShell on win11)
#   lab.sh root TARGET COMMAND          COMMAND as root on a controller; stdin is passed on
#   lab.sh deploy TARGET                build the package for TARGET and install it
#   lab.sh it TARGET SCRIPT...          integration scripts as root, logs in it-results/vm-TARGET/
#   lab.sh tunnel TARGET [PORT]         Cockpit of TARGET on http://127.0.0.1:PORT (9091)
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"

# SendEnv=-...: the host's locale may not exist on a guest (setlocale warnings).
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5
    -o "SendEnv=-LANG -LC_*")
die() { lab_die "$@"; }

defined() { virsh dominfo "$1" >/dev/null 2>&1; }
state() { if defined "$1"; then virsh domstate "$1" | head -1; else echo "not built"; fi; }
is_running() { [[ $(state "$1") == running ]]; }
need_password() { [[ -n ${LAB_PASSWORD:-} ]] || die "LAB_PASSWORD is not set in $LAB_ENV"; }

built_targets() {
    local t
    for t in $LAB_TARGETS; do lab_target "$t"; defined "$VM" && echo "$t"; done
    return 0
}

# Called in a condition, where "set -e" does not apply: check every step.
resources() {
    local need_mem=0 need_cpu=0 run_cpu=0 t vm
    for t in "$@"; do
        lab_target "$t"
        is_running "$VM" && continue
        need_mem=$((need_mem + MEM_MIB)); need_cpu=$((need_cpu + VCPUS))
    done
    for vm in $(virsh list --name); do
        run_cpu=$((run_cpu + $(virsh dominfo "$vm" | awk '/^CPU\(s\):/ {print $2}')))
    done
    local avail cores
    avail=$(free -m | awk '/^Mem:/ {print $7}'); cores=$(nproc)
    echo "to start: ${need_mem} MiB, ${need_cpu} vCPU; host: ${avail} MiB available," \
        "$((run_cpu + need_cpu))/${cores} vCPU with the running VMs"
    (( need_mem <= avail - 2048 )) && (( run_cpu + need_cpu <= cores ))
}

net_xml() {
    local t hosts=""
    for t in $LAB_TARGETS; do
        lab_target "$t"
        hosts+="      <host mac='$MAC' name='$VM' ip='$IP'/>"$'\n'
    done
    cat <<XML
<network>
  <name>$LAB_NET</name>
  <forward mode='nat'/>
  <bridge name='$LAB_BRIDGE' stp='on' delay='0'/>
  <domain name='$LAB_NET' localOnly='yes'/>
  <ip address='$LAB_SUBNET.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='$LAB_SUBNET.100' end='$LAB_SUBNET.199'/>
$hosts    </dhcp>
  </ip>
</network>
XML
}

ensure_net() {
    if ! virsh net-info "$LAB_NET" >/dev/null 2>&1; then
        ip -4 route | grep -q "^$LAB_SUBNET\.0/" && die "$LAB_SUBNET.0/24 is already routed on this host: set LAB_SUBNET in .env"
        local f; f=$(mktemp); net_xml > "$f"
        virsh net-define "$f" >/dev/null; rm -f "$f"
        virsh net-autostart "$LAB_NET" >/dev/null
        echo "network $LAB_NET defined ($LAB_SUBNET.0/24)"
    fi
    virsh net-list --name | grep -qx "$LAB_NET" || virsh net-start "$LAB_NET" >/dev/null
}

stop_vm() {
    is_running "$VM" || { echo "$VM off"; return 0; }
    virsh shutdown "$VM" >/dev/null
    # An ACPI shutdown is sometimes ignored (Windows while it boots): ask again.
    for i in $(seq 1 60); do
        is_running "$VM" || { echo "$VM off"; return 0; }
        (( i % 10 == 0 )) && virsh shutdown "$VM" >/dev/null 2>&1
        sleep 3
    done
    die "$VM still running after 3 minutes (virsh destroy $VM forces it off)"
}

wait_ssh() {
    need_password
    for _ in $(seq 1 "${2:-60}"); do
        SSHPASS=$LAB_PASSWORD sshpass -e ssh "${SSH_OPTS[@]}" "$LAB_USER@$IP" exit 2>/dev/null && return 0
        sleep 5
    done
    die "no SSH answer from $1 ($IP)"
}

# root TARGET COMMAND: sudo reads the password from the first line of stdin
# (-k: always, even if cached); the rest of stdin reaches the command.
root() {
    local t=$1; shift
    lab_target "$t"; is_linux "$t" || die "root is for the controllers"
    need_password
    { printf '%s\n' "$LAB_PASSWORD"; [[ -t 0 ]] || cat; } |
        SSHPASS=$LAB_PASSWORD sshpass -e ssh "${SSH_OPTS[@]}" "$LAB_USER@$IP" \
            "sudo -k -S -p '' sh -c $(printf '%q' "$*")"
}

package_for() {  # package_for TARGET: builds it, prints its path
    local files
    case $1 in
        deb13) make -C "$VM_ROOT" --no-print-directory -s deb >/dev/null; files=("$VM_ROOT"/deb/cockpit-fleet_*_all.deb) ;;
        fed44) make -C "$VM_ROOT" --no-print-directory -s rpm >/dev/null; files=("$VM_ROOT"/rpm/cockpit-fleet-*.fc44.noarch.rpm) ;;
        alma10) make -C "$VM_ROOT" --no-print-directory -s rpm >/dev/null; files=("$VM_ROOT"/rpm/cockpit-fleet-*.el10.noarch.rpm) ;;
    esac
    [[ ${#files[@]} -eq 1 && -f ${files[0]} ]] || die "expected one package for $1, found: ${files[*]}"
    echo "${files[0]}"
}

cmd=${1:-status}; shift || true
case $cmd in
    status)
        if virsh net-info "$LAB_NET" >/dev/null 2>&1; then
            echo "network $LAB_NET: $LAB_SUBNET.0/24, $(virsh net-info "$LAB_NET" | awk '/^Active:/ {print ($2 == "yes" ? "active" : "inactive")}')"
        else
            echo "network $LAB_NET: not defined (lab.sh net)"
        fi
        for t in $LAB_TARGETS; do
            lab_target "$t"
            printf '%-7s %-22s %-16s %s\n' "$t" "$VM" "$IP" "$(state "$VM")"
            { virsh snapshot-list "$VM" --name 2>/dev/null || true; } | sed '/^$/d; s/^/        snapshot: /'
        done ;;
    net)
        ensure_net; virsh net-dumpxml "$LAB_NET" | grep -E "<host |<range " ;;
    build)
        [[ $# -eq 1 ]] || die "build needs one target"
        lab_target "$1"; ensure_net
        if is_linux "$1"; then exec "$VM_DIR/build-linux.sh" "$1"; else exec "$VM_DIR/build-win.sh" "$1"; fi ;;
    rebuild)
        [[ $# -eq 1 ]] || die "rebuild needs one target"
        "$0" destroy "$1"; exec "$0" build "$1" ;;
    destroy)
        [[ $# -eq 1 ]] || die "destroy needs one target"
        lab_target "$1"
        if defined "$VM"; then
            is_running "$VM" && virsh destroy "$VM" >/dev/null
            # Media of a pool (the Windows ISO) must survive: eject before undefine.
            for dev in $(virsh domblklist "$VM" --details | awk '$2 == "cdrom" && $4 != "-" {print $3}'); do
                virsh change-media "$VM" "$dev" --eject --config >/dev/null
            done
            virsh undefine "$VM" --nvram --tpm --snapshots-metadata --managed-save >/dev/null
            echo "$VM undefined"
        fi
        # Its volumes: the disk, external snapshots, seed and answer media.
        virsh pool-refresh "$LAB_POOL" >/dev/null
        for vol in $(virsh vol-list "$LAB_POOL" | awk 'NR > 2 {print $1}' | grep -E "^$VM([.-]|\$)" || true); do
            virsh vol-delete --pool "$LAB_POOL" "$vol" >/dev/null && echo "volume $vol deleted"
        done ;;
    resources)
        [[ $# -gt 0 ]] || die "resources needs targets"
        resources "$@" && echo ok || { echo "does not fit"; exit 1; } ;;
    start)
        [[ $# -gt 0 ]] || die "start needs targets"
        resources "$@" || die "not enough resources on the host for $*"
        ensure_net
        for t in "$@"; do lab_target "$t"; is_running "$VM" || virsh start "$VM" >/dev/null; echo "$VM running"; done ;;
    stop)
        [[ $# -gt 0 ]] || die "stop needs targets, or all"
        [[ $1 == all ]] && set -- $(built_targets)
        for t in "$@"; do lab_target "$t"; stop_vm; done ;;
    snapshot | revert)
        [[ $# -eq 2 ]] || die "$cmd needs a target and a snapshot name"
        lab_target "$1"
        is_running "$VM" && die "shut $VM off first"
        if [[ $cmd == snapshot ]]; then virsh snapshot-create-as "$VM" "$2" >/dev/null; else virsh snapshot-revert "$VM" "$2"; fi
        echo "$VM: $cmd $2" ;;
    reset)
        [[ $# -gt 0 ]] || set -- $(built_targets)
        for t in "$@"; do lab_target "$t"; stop_vm; virsh snapshot-revert "$VM" baseline; echo "$VM at baseline"; done ;;
    wait)
        [[ $# -eq 1 ]] || die "wait needs one target"
        lab_target "$1"; wait_ssh "$1" ;;
    ssh)
        [[ $# -ge 1 ]] || die "ssh needs a target"
        lab_target "$1"; shift; need_password
        SSHPASS=$LAB_PASSWORD exec sshpass -e ssh "${SSH_OPTS[@]}" "$LAB_USER@$IP" "$@" ;;
    root)
        [[ $# -ge 2 ]] || die "root needs a target and a command"
        root "$@" ;;
    deploy)
        [[ $# -eq 1 ]] || die "deploy needs one target"
        lab_target "$1"; is_linux "$1" || die "deploy is for the controllers"
        pkg=$(package_for "$1")
        echo "package: ${pkg##*/}"
        # Copies in ~/.local (make devel-install) would shadow the package.
        root "$1" 'set -e
            rm -rf /root/.local/share/cockpit/fleet /home/*/.local/share/cockpit/fleet
            f=$(mktemp --suffix=.'"${pkg##*.}"'); trap "rm -f $f" EXIT
            cat > "$f"; chmod 644 "$f"
            if command -v apt-get >/dev/null; then
                DEBIAN_FRONTEND=noninteractive apt-get install -y -q --reinstall --allow-downgrades \
                    -o Dpkg::Options::=--force-confold "$f" >/dev/null
                dpkg-query -W -f="installed: \${Package} \${Version}\n" cockpit-fleet
            else
                if [ "$(rpm -q --qf "%{VERSION}-%{RELEASE}" cockpit-fleet 2>/dev/null)" = "$(rpm -qp --qf "%{VERSION}-%{RELEASE}" "$f")" ]; then
                    dnf reinstall -y -q "$f" >/dev/null
                else
                    dnf install -y -q "$f" >/dev/null
                fi
                rpm -q --qf "installed: %{NAME} %{VERSION}-%{RELEASE}\n" cockpit-fleet
            fi
            sha256sum /usr/share/cockpit/fleet/index.js | sed "s/^/remote: /"' < "$pkg"
        sha256sum "$VM_ROOT/dist/index.js" | sed 's/^/local:  /'
        echo "reload the Cockpit page, ignoring the cache, to load the new bundle" ;;
    it)
        [[ $# -ge 2 ]] || die "it needs a target and scripts"
        t=$1; shift; lab_target "$t"; is_linux "$t" || die "integration scripts run on a controller"
        tar -C "$VM_ROOT/test" -cf - integration | root "$t" 'rm -rf /tmp/it && mkdir /tmp/it && tar -C /tmp/it -xf -'
        # IT_* of the caller reach the scripts; the SSH password goes on stdin.
        extra=""
        for var in $(env | sed -n 's/^\(IT_[A-Z0-9_]*\)=.*/\1/p'); do
            [[ $var == IT_SSH_PASSWORD ]] || extra+=" $var=$(printf '%q' "${!var}")"
        done
        mkdir -p "$VM_ROOT/it-results/vm-$t"
        failed=0
        for s in "$@"; do
            [[ -f $VM_ROOT/test/integration/$s.sh ]] || die "no test/integration/$s.sh"
            log=$VM_ROOT/it-results/vm-$t/$s.log start=$(date +%s)
            if printf '%s\n' "$LAB_PASSWORD" | root "$t" "IFS= read -r p; IT_SSH_PASSWORD=\$p; unset p; export IT_SSH_PASSWORD
                    exec env IT_WIPE=yes IT_SSH_USER=$LAB_USER$extra sh /tmp/it/integration/$s.sh </dev/null" >"$log" 2>&1; then
                status=ok
            else
                status=FAILED failed=1
            fi
            printf '%-7s %-15s %4ss  %s\n' "$status" "$s" "$(($(date +%s) - start))" "${log#"$VM_ROOT"/}"
        done
        exit "$failed" ;;
    tunnel)
        [[ $# -ge 1 ]] || die "tunnel needs a target"
        lab_target "$1"; port=${2:-9091}; need_password
        echo "Cockpit of $VM on http://127.0.0.1:$port (Ctrl-C to close)"
        SSHPASS=$LAB_PASSWORD exec sshpass -e ssh "${SSH_OPTS[@]}" -N -L "$port:127.0.0.1:9090" "$LAB_USER@$IP" ;;
    *)
        sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 1 ;;
esac
