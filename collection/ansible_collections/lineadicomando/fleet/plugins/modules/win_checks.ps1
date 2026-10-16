#!powershell
# SPDX-License-Identifier: LGPL-2.1-or-later

#AnsibleRequires -CSharpUtil Ansible.Basic

# The checks of the cockpit-fleet catalog on Windows: the same parameters and
# the same results as checks.py, documented there. The action
# lineadicomando.fleet.checks runs this module where the shell is PowerShell.

$spec = @{
    options = @{
        checks = @{ type = 'list'; elements = 'dict'; required = $true }
    }
    supports_check_mode = $true
}
$module = [Ansible.Basic.AnsibleModule]::Create($args, $spec)
# Summaries are read on the controller, whatever the language of the node: 32.2%, not 32,2%.
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture

$OK = 0; $UNKNOWN = 3

function New-Metric($Name, $Value, $Unit = '', $Instance = '', $Min = $null, $Max = $null) {
    $found = @{ name = $Name; value = $Value; unit = $Unit }
    if ($Instance) { $found.instance = $Instance }
    if ($null -ne $Min) { $found.min = $Min }
    if ($null -ne $Max) { $found.max = $Max }
    $found
}

function New-Result($State, $Summary, $Metrics, $Details) {
    @{ state = $State; summary = $Summary; metrics = @($Metrics); details = $Details }
}

function Get-Param($Params, $Name) {
    if ($null -ne $Params -and $Params.ContainsKey($Name) -and $null -ne $Params[$Name]) {
        return @($Params[$Name] | ForEach-Object { [string]$_ })
    }
    return @()
}

function Test-Disk($Params) {
    $wanted = @(Get-Param $Params 'mounts' | ForEach-Object { $_.TrimEnd('\').ToUpper() })
    $metrics = [System.Collections.Generic.List[object]]::new()
    $details = @{}
    $worst = $null
    foreach ($drive in [System.IO.DriveInfo]::GetDrives()) {
        $name = $drive.Name.TrimEnd('\').ToUpper()
        if ($wanted.Count -gt 0) {
            if ($wanted -notcontains $name) { continue }
        } elseif ($drive.DriveType -ne 'Fixed') { continue }
        if (-not $drive.IsReady -or $drive.TotalSize -eq 0) { continue }
        $size = [int64]$drive.TotalSize
        $available = [int64]$drive.AvailableFreeSpace
        $used = $size - [int64]$drive.TotalFreeSpace
        $percent = [math]::Round(100.0 * $used / [math]::Max($used + $available, 1), 1)
        $metrics.Add((New-Metric 'used_pct' $percent '%' $name 0 100))
        $metrics.Add((New-Metric 'free' $available 'B' $name 0 $size))
        $details[$name] = $drive.DriveFormat
        if ($null -eq $worst -or $percent -gt $worst[1]) { $worst = @($name, $percent) }
    }
    $missing = @($wanted | Where-Object { -not $details.ContainsKey($_) })
    if ($missing.Count -gt 0) {
        return New-Result $UNKNOWN ("not mounted: " + ($missing -join ', ')) $metrics $details
    }
    if ($null -eq $worst) { return New-Result $UNKNOWN 'no filesystem found' $metrics $details }
    New-Result $OK ("{0} filesystems, fullest {1} at {2}%" -f $details.Count, $worst[0], $worst[1]) `
        $metrics $details
}

function Test-Cpu($Params) {
    $busy = (Get-CimInstance -ClassName Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    $busy = [math]::Round([double]$busy, 1)
    $count = [Environment]::ProcessorCount
    New-Result $OK ("{0}% busy on {1} CPUs" -f $busy, $count) @(New-Metric 'cpu_pct' $busy '%' '' 0 100) `
        @{ cpus = $count }
}

function Test-Memory($Params) {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $total = [int64]$os.TotalVisibleMemorySize * 1024
    $available = [int64]$os.FreePhysicalMemory * 1024
    $used = [math]::Round(100.0 * ($total - $available) / [math]::Max($total, 1), 1)
    $metrics = [System.Collections.Generic.List[object]]::new()
    $metrics.Add((New-Metric 'memory_used_pct' $used '%' '' 0 100))
    $metrics.Add((New-Metric 'memory_available' $available 'B' '' 0 $total))
    $summary = "{0}% of {1} MiB used" -f $used, [int64]($total / 1048576)
    $swap = 0
    $files = @(Get-CimInstance -ClassName Win32_PageFileUsage)
    if ($files.Count -gt 0) {
        $swap = [int64](($files | Measure-Object -Property AllocatedBaseSize -Sum).Sum) * 1048576
        $current = [int64](($files | Measure-Object -Property CurrentUsage -Sum).Sum) * 1048576
        if ($swap -gt 0) {
            $swapUsed = [math]::Round(100.0 * $current / $swap, 1)
            $metrics.Add((New-Metric 'swap_used_pct' $swapUsed '%' '' 0 100))
            $summary += ", swap {0}%" -f $swapUsed
        }
    }
    New-Result $OK $summary $metrics @{ total = $total; swap_total = $swap }
}

function Test-Services($Params) {
    $names = @(Get-Param $Params 'names')
    if ($names.Count -eq 0) { return New-Result $UNKNOWN 'no service named' @() @{} }
    $details = @{}
    $down = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $names) {
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($null -eq $service) {
            $details[$name] = 'not found'
            $down.Add($name)
            continue
        }
        $details[$name] = "{0} ({1})" -f $service.Status, $service.StartType
        if ($service.Status -ne 'Running') { $down.Add($name) }
    }
    $summary = if ($down.Count -gt 0) { "not running: " + ($down -join ', ') } else { "{0} running" -f $names.Count }
    New-Result $OK $summary @(New-Metric 'not_running' $down.Count '' '' 0 $names.Count) $details
}

function Test-Uptime($Params) {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $seconds = [int64]((Get-Date) - $os.LastBootUpTime).TotalSeconds
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    )
    $found = @($keys | Where-Object { Test-Path -LiteralPath $_ })
    $required = if ($found.Count -gt 0) { 1 } else { 0 }
    $summary = "up {0} days" -f [int64]($seconds / 86400)
    if ($required) { $summary += ', reboot required' }
    $metrics = @((New-Metric 'uptime' $seconds 's'), (New-Metric 'reboot_required' $required '' '' 0 1))
    New-Result $OK $summary $metrics @{ method = 'registry' }
}

function Test-Certificates($Params) {
    $paths = @(Get-Param $Params 'paths')
    if ($paths.Count -eq 0) { return New-Result $UNKNOWN 'no path named' @() @{} }
    $now = [DateTime]::UtcNow
    $metrics = [System.Collections.Generic.List[object]]::new()
    $details = @{}
    $errors = [System.Collections.Generic.List[string]]::new()
    $soonest = $null
    foreach ($path in $paths) {
        $found = [System.Collections.Generic.List[object]]::new()
        try {
            if ($path -like 'Cert:*') {
                # A store: every certificate in it, named by its thumbprint.
                foreach ($cert in Get-ChildItem -LiteralPath $path -ErrorAction Stop) {
                    if ($cert -is [System.Security.Cryptography.X509Certificates.X509Certificate2]) {
                        $found.Add(@("$path\$($cert.Thumbprint)", $cert))
                    }
                }
            } elseif (Test-Path -LiteralPath $path -PathType Container) {
                foreach ($file in Get-ChildItem -LiteralPath $path -File | Where-Object { $_.Extension -in '.pem', '.crt', '.cer' }) {
                    $found.Add(@($file.FullName, [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($file.FullName)))
                }
            } else {
                $found.Add(@($path, [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($path)))
            }
        } catch {
            $errors.Add($path)
            $details[$path] = "unreadable: $($_.Exception.Message)"
            continue
        }
        foreach ($entry in $found) {
            $days = [math]::Round(($entry[1].NotAfter.ToUniversalTime() - $now).TotalDays, 1)
            $metrics.Add((New-Metric 'days_left' $days 'd' $entry[0]))
            $details[$entry[0]] = $entry[1].NotAfter.ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
            if ($null -eq $soonest -or $days -lt $soonest[1]) { $soonest = @($entry[0], $days) }
        }
    }
    if ($errors.Count -gt 0) { return New-Result $UNKNOWN ("cannot read: " + ($errors -join ', ')) $metrics $details }
    if ($null -eq $soonest) { return New-Result $UNKNOWN 'no certificate found' $metrics $details }
    New-Result $OK ("{0} certificates, the first to expire {1} in {2} days" -f $metrics.Count, $soonest[0], $soonest[1]) `
        $metrics $details
}

function Get-Prop($Object, $Name) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) { $property.Value } else { $null }
}

function Get-Virtual($Text) {
    switch -Regex ($Text) {
        'Virtual Machine|Hyper-V' { return 'microsoft' }
        'VMware' { return 'vmware' }
        'VirtualBox' { return 'oracle' }
        'KVM|QEMU|Standard PC' { return 'kvm' }
        'Xen|HVM domU' { return 'xen' }
    }
    ''
}

function Test-Cmdb($Params) {
    $system = Get-CimInstance -ClassName Win32_ComputerSystem
    $bios = Get-CimInstance -ClassName Win32_BIOS
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $cpu = @(Get-CimInstance -ClassName Win32_Processor)[0]
    $hardware = @{
        vendor = [string]$system.Manufacturer
        model = [string]$system.Model
        serial = [string]$bios.SerialNumber
        bios = [string]$bios.SMBIOSBIOSVersion
        virtual = Get-Virtual ("{0} {1}" -f $system.Manufacturer, $system.Model)
        cpu_model = ([string]$cpu.Name).Trim()
        cpus = [Environment]::ProcessorCount
        memory = [int64]$system.TotalPhysicalMemory
        boot_time = ([DateTimeOffset]$os.LastBootUpTime).ToUnixTimeSeconds()
    }
    $disks = @(Get-CimInstance -ClassName Win32_DiskDrive | Sort-Object -Property Index | ForEach-Object {
        @{ name = "PhysicalDrive$($_.Index)"; size = [int64]$_.Size; model = [string]$_.Model }
    })
    $interfaces = [System.Collections.Generic.List[object]]::new()
    foreach ($adapter in @(Get-NetAdapter -ErrorAction SilentlyContinue | Sort-Object -Property Name)) {
        $addresses = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike 'fe80*' -and $_.IPAddress -notlike '169.254.*' } |
            ForEach-Object { "{0}/{1}" -f $_.IPAddress, $_.PrefixLength })
        $interfaces.Add(@{ name = [string]$adapter.Name; mac = ([string]$adapter.MacAddress).Replace('-', ':').ToLower()
            addresses = $addresses })
    }
    # What "Apps and features" lists: the uninstall keys, 64 and 32 bits.
    $packages = [System.Collections.Generic.List[object]]::new()
    $seen = @{}
    $hives = @(
        @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'x64'),
        @('HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall', 'x86')
    )
    foreach ($hive in $hives) {
        if (-not (Test-Path -LiteralPath $hive[0])) { continue }
        foreach ($key in Get-ChildItem -LiteralPath $hive[0] -ErrorAction SilentlyContinue) {
            $entry = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            if ($null -eq $entry) { continue }
            $name = [string](Get-Prop $entry 'DisplayName')
            if (-not $name -or (Get-Prop $entry 'SystemComponent') -eq 1 -or (Get-Prop $entry 'ParentKeyName')) { continue }
            $id = "{0}|{1}" -f $name.Trim(), $hive[1]
            if ($seen.ContainsKey($id)) { continue }
            $seen[$id] = $true
            $packages.Add(@{ name = $name.Trim(); version = [string](Get-Prop $entry 'DisplayVersion'); arch = $hive[1] })
        }
    }
    $states = @{ Running = 'running'; Stopped = 'stopped' }
    $modes = @{ Auto = 'enabled'; Manual = 'manual'; Disabled = 'disabled' }
    $services = @(Get-CimInstance -ClassName Win32_Service | Sort-Object -Property Name | ForEach-Object {
        $state = if ($states.ContainsKey([string]$_.State)) { $states[[string]$_.State] } else { ([string]$_.State).ToLower() }
        $mode = if ($modes.ContainsKey([string]$_.StartMode)) { $modes[[string]$_.StartMode] } else { ([string]$_.StartMode).ToLower() }
        @{ name = [string]$_.Name; state = $state; enabled = $mode }
    })
    $metrics = @(
        (New-Metric 'cpus' $hardware.cpus),
        (New-Metric 'memory' $hardware.memory 'B'),
        (New-Metric 'packages' $packages.Count),
        (New-Metric 'services' $services.Count)
    )
    $summary = "{0} CPUs, {1:N1} GiB, {2} packages, {3} services" -f $hardware.cpus,
        ($hardware.memory / 1GB), $packages.Count, $services.Count
    $result = New-Result $OK $summary $metrics @{ package_manager = 'registry' }
    $result.cmdb = @{
        hardware = $hardware; disks = $disks; interfaces = $interfaces
        package_manager = 'registry'; packages = $packages; services = $services
    }
    $result
}

# Classifications of Windows Update that are about security.
$SECURITY_CLASSES = @(
    '0fa1201d-4330-4fa8-8ae9-b877473b6441',  # Security Updates
    'e6cf1350-c01b-414d-a61f-263d14d133b4'   # Critical Updates
)

function Test-Updates($Params) {
    # Search only: nothing is downloaded or installed. The search asks the
    # server each time, so there is nothing to refresh.
    try {
        $session = New-Object -ComObject Microsoft.Update.Session
        $searcher = $session.CreateUpdateSearcher()
        $found = $searcher.Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
    } catch {
        $failure = $_.Exception
        if ($null -ne $failure.InnerException) { $failure = $failure.InnerException }
        if ($failure.HResult -eq -2147024891) {
            return New-Result $UNKNOWN 'Windows Update refused the search to this logon (access denied)' @() @{}
        }
        return New-Result $UNKNOWN ("Windows Update: {0}" -f $failure.Message) @() @{}
    }
    $updates = [System.Collections.Generic.List[object]]::new()
    $security = 0
    foreach ($update in $found.Updates) {
        $classes = @($update.Categories | Where-Object { $_.Type -eq 'UpdateClassification' })
        $isSecurity = [bool]@($classes | Where-Object { $SECURITY_CLASSES -contains ([string]$_.CategoryID).ToLower() }) -or
            [bool]$update.MsrcSeverity
        if ($isSecurity) { $security++ }
        $kb = @($update.KBArticleIDs | ForEach-Object { "KB$_" }) -join ' '
        $updates.Add(@{ name = [string]$update.Title; current = ''; available = $kb; security = $isSecurity
            source = (@($classes | ForEach-Object { [string]$_.Name }) -join ', ') })
    }
    $metrics = @((New-Metric 'updates' $updates.Count), (New-Metric 'security_updates' $security))
    $result = New-Result $OK ("{0} update{1}, {2} for security (Windows Update)" -f $updates.Count, $(if ($updates.Count -eq 1) { '' } else { 's' }), $security) `
        $metrics @{ manager = 'windows_update' }
    $result.updates = @{ manager = 'windows_update'; updates = $updates }
    $result
}

$checks = @{
    reachability = { param($p) New-Result $OK 'reachable' @() @{} }
    disk = { param($p) Test-Disk $p }
    cpu = { param($p) Test-Cpu $p }
    memory = { param($p) Test-Memory $p }
    services = { param($p) Test-Services $p }
    uptime = { param($p) Test-Uptime $p }
    certificates = { param($p) Test-Certificates $p }
    # The offset is computed on the controller, from fleet_host.time.
    clock = { param($p) New-Result $OK '' @() @{} }
    cmdb = { param($p) Test-Cmdb $p }
    updates = { param($p) Test-Updates $p }
}

$results = [System.Collections.Generic.List[object]]::new()
foreach ($entry in $module.Params.checks) {
    $name = [string]$entry['check']
    $result = $null
    try {
        if (-not $checks.ContainsKey($name)) { throw 'no such check on this platform' }
        $result = & $checks[$name] $entry['params']
    } catch {
        $result = New-Result $UNKNOWN ("{0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message) @() @{}
    }
    $result.id = [string]$entry['id']
    $result.check = $name
    $results.Add($result)
}

$os = Get-CimInstance -ClassName Win32_OperatingSystem
$module.Result.changed = $false
$module.Result.fleet_results = $results
$module.Result.fleet_host = @{
    platform = 'windows'
    os = $os.Caption
    os_id = 'windows'
    os_version = $os.Version
    kernel = $os.BuildNumber
    arch = $os.OSArchitecture
    time = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
}
$module.ExitJson()
