#!/usr/bin/env bash
# Builds a controller VM of the lab from scratch, from the distribution's
# cloud image (lab.conf) and cloud-init, then provision.sh over SSH:
#   build-linux.sh deb13|fed44|alma10
# Run through "lab.sh build", which creates the network first. Leaves the VM
# shut off with the snapshot "baseline": the OS up to date with what a
# controller needs besides the package, the admin account LAB_USER.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
lab=$VM_DIR/lab.sh
t=${1:?usage: build-linux.sh TARGET}
lab_target "$t"
is_linux "$t" || lab_die "$t is not a Linux target"
[[ -n ${LAB_PASSWORD:-} ]] || lab_die "LAB_PASSWORD is not set in $LAB_ENV"
virsh dominfo "$VM" >/dev/null 2>&1 && lab_die "$VM already exists: lab.sh rebuild $t"
"$lab" resources "$t" || lab_die "not enough resources on the host to build $VM"

case $t in
    deb13) family=debian ADMIN_GROUP=sudo ;;
    fed44) family=fedora ADMIN_GROUP=wheel ;;
    alma10) family=alma ADMIN_GROUP=wheel ;;
esac

# The image, verified against the checksum file of the distribution; kept in
# LAB_CACHE and downloaded again when the checksum changes ("latest" names).
url_var=IMG_$t sums_var=SUMS_$t
url=${!url_var} file=${url##*/} cache=$LAB_CACHE/$t
mkdir -p "$cache"
curl -fsSL --retry 3 -o "$cache/SUMS" "${!sums_var}"
expected=$(awk -v f="$file" 'index($0, "(" f ")") || $NF == f' "$cache/SUMS" |
    grep -oE '\b([0-9a-f]{128}|[0-9a-f]{64})\b' | head -1 || true)
[[ -n $expected ]] || lab_die "no checksum for $file in ${!sums_var}"
sum_of() { if [[ ${#expected} -eq 128 ]]; then sha512sum "$1"; else sha256sum "$1"; fi | cut -d' ' -f1; }
if [[ ! -f $cache/$file || $(sum_of "$cache/$file") != "$expected" ]]; then
    echo "downloading $file"
    curl -fL --retry 3 --progress-bar -o "$cache/$file.part" "$url"
    [[ $(sum_of "$cache/$file.part") == "$expected" ]] || lab_die "checksum mismatch for $file"
    mv "$cache/$file.part" "$cache/$file"
fi
echo "image: $file (checksum verified)"

# The disk: the image uploaded into the pool, grown to DISK_GIB (cloud-init
# grows the root file system at first boot). No sudo: libvirt writes it.
disk=$VM.qcow2 seed=$VM-seed.iso
virsh vol-create-as "$LAB_POOL" "$disk" "$(stat -c %s "$cache/$file")b" --format qcow2 >/dev/null
virsh vol-upload --pool "$LAB_POOL" "$disk" "$cache/$file"
virsh vol-resize --pool "$LAB_POOL" "$disk" "${DISK_GIB}G" >/dev/null

# The seed holds the password's hash only; it is built in a private
# directory, never in the work tree, and deleted from the pool at the end.
tmp=$(umask 077; mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/seed"
PASSWORD_HASH=$(printf '%s' "$LAB_PASSWORD" | openssl passwd -6 -stdin)
export VM LAB_NET LAB_TIMEZONE LAB_USER ADMIN_GROUP PASSWORD_HASH
"$VM_DIR/render.py" "$VM_DIR/cloud-init/user-data" > "$tmp/seed/user-data"
"$VM_DIR/render.py" "$VM_DIR/cloud-init/meta-data" > "$tmp/seed/meta-data"
unset PASSWORD_HASH
xorriso -as mkisofs -quiet -J -r -V CIDATA -o "$tmp/seed.iso" "$tmp/seed"
virsh vol-create-as "$LAB_POOL" "$seed" "$(stat -c %s "$tmp/seed.iso")b" --format raw >/dev/null
virsh vol-upload --pool "$LAB_POOL" "$seed" "$tmp/seed.iso"
rm -rf "$tmp"

virt-install --import --name "$VM" --osinfo "$OSINFO" \
    --memory "$MEM_MIB" --vcpus "$VCPUS" --cpu host-passthrough \
    --disk "vol=$LAB_POOL/$disk,bus=virtio" \
    --disk "vol=$LAB_POOL/$seed,device=cdrom,bus=sata" \
    --network "network=$LAB_NET,mac=$MAC,model=virtio" \
    --graphics none --console pty,target.type=serial --noautoconsole >/dev/null
echo "$VM started, waiting for cloud-init"
"$lab" wait "$t"
rc=0; "$lab" root "$t" 'cloud-init status --wait --long' </dev/null || rc=$?
# 2: done, with recoverable errors (they are in the output above).
[[ $rc -eq 0 || $rc -eq 2 ]] || lab_die "cloud-init failed on $VM"

echo "provisioning ($family)"
"$lab" root "$t" "sh -s $family" < "$VM_DIR/provision.sh"
"$lab" root "$t" 'systemctl reboot' </dev/null || true
sleep 15
"$lab" wait "$t"
echo "after a reboot: $("$lab" root "$t" 'systemctl is-system-running --wait; systemctl --failed --no-legend --plain | cut -d" " -f1 | paste -sd" "' </dev/null | paste -sd' ' || true)"

"$lab" stop "$t"
virsh change-media "$VM" sda --eject --config >/dev/null
virsh vol-delete --pool "$LAB_POOL" "$seed" >/dev/null
virsh snapshot-create-as "$VM" baseline \
    --description "$file up to date on $(date +%F), $LAB_USER, controller prerequisites; built by test/vm/build-linux.sh" >/dev/null
echo "$VM built: snapshot baseline, shut off"
