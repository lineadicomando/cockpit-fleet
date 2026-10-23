# First logon of the unattended build (test/vm/build-win.sh): makes the VM a
# node a controller reaches over SSH, the way an administrator prepares one:
# OpenSSH Server with PowerShell as its shell, port 22 open. Then turns off
# sleep and hibernation. The address comes from the lab network's DHCP,
# reserved for the VM's MAC.
# Log: C:\Windows\Temp\cockpit-fleet-setup.log; outcome: .done or .failed.
$ErrorActionPreference = 'Stop'
$log = 'C:\Windows\Temp\cockpit-fleet-setup'
Start-Transcript -Path "$log.log" -Append
try {
    # The NIC may still be coming up at first logon, and the capability comes
    # from Windows Update: retry for about ten minutes.
    $cap = 'OpenSSH.Server~~~~0.0.1.0'
    for ($i = 1; $i -le 20; $i++) {
        if ((Get-WindowsCapability -Online -Name $cap).State -eq 'Installed') { break }
        try {
            Add-WindowsCapability -Online -Name $cap | Out-Null
        } catch {
            Write-Output "Add-WindowsCapability, attempt ${i}: $($_.Exception.Message)"
            Start-Sleep -Seconds 30
        }
    }
    if ((Get-WindowsCapability -Online -Name $cap).State -ne 'Installed') {
        throw "$cap is not installed"
    }
    Set-Service -Name sshd -StartupType Automatic
    Start-Service -Name sshd
    New-Item -Path HKLM:\SOFTWARE\OpenSSH -Force | Out-Null
    New-ItemProperty -Path HKLM:\SOFTWARE\OpenSSH -Name DefaultShell -PropertyType String -Force `
        -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" | Out-Null
    if (-not (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName 'OpenSSH SSH Server (sshd)' `
            -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow | Out-Null
    }
    Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Enabled True -Profile Any

    # A node that falls asleep looks unreachable; hibernation also keeps fast
    # startup on, which leaves the disk of a shut-off baseline hibernated.
    powercfg.exe /change standby-timeout-ac 0
    powercfg.exe /hibernate off

    # The autologon of the answer file has done its job: clear what Winlogon kept.
    $winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty -Path $winlogon -Name AutoAdminLogon -Value '0'
    Remove-ItemProperty -Path $winlogon -Name DefaultPassword -ErrorAction SilentlyContinue

    $nic = Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -First 1
    if ($nic) {
        try {
            Set-NetConnectionProfile -InterfaceIndex $nic.ifIndex -NetworkCategory Private
        } catch {
            Write-Output "Set-NetConnectionProfile: $($_.Exception.Message)"
        }
    }
    New-Item -Path "$log.done" -ItemType File -Force | Out-Null
} catch {
    Write-Output "FAILED: $($_.Exception.Message)"
    New-Item -Path "$log.failed" -ItemType File -Force | Out-Null
} finally {
    Stop-Transcript
}
