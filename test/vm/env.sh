# shellcheck shell=bash
# Sourced by the scripts in this directory: lab.conf, then .env from the
# repository root (not tracked; see .env.example). .env values are taken
# verbatim (no quotes, no expansion), so a password may hold any character
# but a newline.
VM_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VM_ROOT=$(cd "$VM_DIR/../.." && pwd)
# shellcheck source=lab.conf
. "$VM_DIR/lab.conf"
LAB_ENV=${LAB_ENV:-$VM_ROOT/.env}
if [[ ! -f $LAB_ENV ]]; then
    echo "missing $LAB_ENV: copy .env.example and fill it in" >&2
    return 1 2>/dev/null || exit 1
fi
while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]] && export "${BASH_REMATCH[1]}=${BASH_REMATCH[2]}"
done < "$LAB_ENV"
unset line
export LIBVIRT_DEFAULT_URI=${LIBVIRT_DEFAULT_URI:-qemu:///system}
export LC_ALL=C.UTF-8 LANGUAGE= # virsh and free are parsed (LANGUAGE wins over LC_ALL)

lab_die() { echo "${0##*/}: $*" >&2; exit 1; }

# lab_target TARGET: sets VM IP MAC OSINFO VCPUS MEM_MIB DISK_GIB, or dies.
lab_target() {
    local def="LAB_$1" host
    [[ " $LAB_TARGETS " == *" $1 "* && -n ${!def:-} ]] || lab_die "unknown target '$1' ($LAB_TARGETS)"
    read -r VM host MAC OSINFO VCPUS MEM_MIB DISK_GIB <<<"${!def}"
    IP=$LAB_SUBNET.$host
    [[ $VM == "$LAB_PREFIX"-* ]] || lab_die "$VM does not start with $LAB_PREFIX-"
}
is_linux() { [[ " $LAB_LINUX " == *" $1 "* ]]; }
