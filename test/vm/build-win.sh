#!/usr/bin/env bash
# Builds the Windows node of the lab from scratch, with no manual step:
# Windows Setup from WIN_ISO driven by win/autounattend.xml, then
# win/setup.ps1 at the first logon (OpenSSH Server, PowerShell as its shell):
#   build-win.sh win11
# Run through "lab.sh build", which creates the network first. Takes 20-40
# minutes. Leaves the VM shut off with the snapshot "baseline".
# What fails where: Setup and OOBE show on the VM's screen (virt-viewer);
# setup.ps1 logs to C:\Windows\Temp\cockpit-fleet-setup.log.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
lab=$VM_DIR/lab.sh
t=${1:?usage: build-win.sh TARGET}
lab_target "$t"
is_linux "$t" && lab_die "$t is not a Windows target"
[[ -n ${LAB_PASSWORD:-} ]] || lab_die "LAB_PASSWORD is not set in $LAB_ENV"
[[ -n ${WIN_ISO:-} && -f $WIN_ISO ]] || lab_die "WIN_ISO in $LAB_ENV must name the Windows 11 Enterprise Evaluation ISO"
virsh dominfo "$VM" >/dev/null 2>&1 && lab_die "$VM already exists: lab.sh rebuild $t"
"$lab" resources "$t" || lab_die "not enough resources on the host to build $VM"

# The answer file holds the password in clear: it is built in a private
# directory, never in the work tree (which may be synced elsewhere), and goes
# straight into the pool, from which it is deleted at the end.
answer=$VM-unattend.iso
tmp=$(umask 077; mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/iso/cockpit-fleet"
WIN_COMPUTER_NAME=CFLEET-${t^^}  # NetBIOS: at most 15 characters
export LAB_USER LAB_PASSWORD WIN_LANG WIN_KEYBOARD WIN_TIMEZONE WIN_COMPUTER_NAME
"$VM_DIR/render.py" --xml "$VM_DIR/win/autounattend.xml" > "$tmp/iso/autounattend.xml"
cp "$VM_DIR/win/setup.ps1" "$tmp/iso/cockpit-fleet/setup.ps1"
xorriso -as mkisofs -quiet -J -r -V CFLEET -o "$tmp/unattend.iso" "$tmp/iso"
virsh vol-delete --pool "$LAB_POOL" "$answer" >/dev/null 2>&1 || true
virsh vol-create-as "$LAB_POOL" "$answer" "$(stat -c %s "$tmp/unattend.iso")b" --format raw >/dev/null
virsh vol-upload --pool "$LAB_POOL" "$answer" "$tmp/unattend.iso"
rm -rf "$tmp"

# Secure Boot with Microsoft's keys, TPM 2.0 and SMM: what Windows 11 Setup
# requires. SATA disk and e1000e NIC: drivers in the box, no virtio ISO.
# virt-install runs Setup with on_reboot=destroy and starts the VM again with
# the final configuration at its first reboot, only while it is still
# running: hence --wait in the background.
virt-install --name "$VM" --osinfo "$OSINFO" \
    --memory "$MEM_MIB" --vcpus "$VCPUS" --cpu host-passthrough \
    --machine q35 --features smm.state=on \
    --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes,firmware.feature1.name=enrolled-keys,firmware.feature1.enabled=yes \
    --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
    --disk "pool=$LAB_POOL,size=$DISK_GIB,format=qcow2,bus=sata" \
    --cdrom "$WIN_ISO" \
    --disk "vol=$LAB_POOL/$answer,device=cdrom,bus=sata" \
    --network "network=$LAB_NET,mac=$MAC,model=e1000e" \
    --graphics spice --video qxl --input tablet,bus=usb \
    --noautoconsole --wait=-1 >/dev/null &
installer=$!

# The firmware boots the ISO only if a key is pressed within a few seconds of
# its prompt. Stop soon: once Setup copies files its Cancel button has the
# focus, and a late Enter would abort the installation.
sleep 2
for _ in $(seq 1 15); do virsh send-key "$VM" KEY_ENTER >/dev/null 2>&1 || true; sleep 1; done
echo "$VM: Windows Setup running (20-40 minutes)"
wait "$installer" || lab_die "virt-install failed"

# Until the first logon has installed OpenSSH: up to 90 minutes.
deadline=$(($(date +%s) + 5400))
until "$lab" ssh "$t" exit </dev/null 2>/dev/null; do
    (( $(date +%s) < deadline )) || lab_die "no SSH on $VM ($IP) after 90 minutes: look at its screen"
    sleep 30
done
outcome=$("$lab" ssh "$t" 'if (Test-Path C:\Windows\Temp\cockpit-fleet-setup.done) { "done" } else { Get-Content C:\Windows\Temp\cockpit-fleet-setup.log -Raw }' </dev/null)
[[ $outcome == done* ]] || lab_die "setup.ps1 failed on $VM: $outcome"
eval_info=$("$lab" ssh "$t" '$p = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL" | Where-Object Name -like "Windows*" | Select-Object -First 1; "$($p.Name), evaluation ends on $((Get-Date).AddMinutes($p.GracePeriodRemaining).ToString("yyyy-MM-dd"))"' </dev/null | tr -d '\r')
echo "$VM: $eval_info"

"$lab" ssh "$t" 'shutdown.exe /s /t 5 /f' </dev/null >/dev/null || true
"$lab" stop "$t"
# The snapshot keeps the domain's XML: with the media still attached, a
# revert would point at an answer volume that no longer exists.
for dev in $(virsh domblklist "$VM" --details | awk '$2 == "cdrom" && $4 != "-" {print $3}'); do
    virsh change-media "$VM" "$dev" --eject --config >/dev/null
done
virsh vol-delete --pool "$LAB_POOL" "$answer" >/dev/null
virsh snapshot-create-as "$VM" baseline \
    --description "$eval_info; $LAB_USER, OpenSSH Server; built by test/vm/build-win.sh" >/dev/null
echo "$VM built: snapshot baseline, shut off"
