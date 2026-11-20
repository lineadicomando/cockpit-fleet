#!powershell
# SPDX-License-Identifier: LGPL-2.1-or-later

#AnsibleRequires -CSharpUtil Ansible.Basic

# net_probe on Windows: the same parameters and the same results as
# net_probe.py, documented there. The action lineadicomando.fleet.net_probe
# runs this module where the shell is PowerShell. No privilege is needed:
# System.Net.NetworkInformation.Ping, TcpClient and Get-NetNeighbor.

$spec = @{
    options = @{
        subnet = @{ type = 'str' }
        addresses = @{ type = 'list'; elements = 'str' }
        ports = @{ type = 'list'; elements = 'int'; default = @(22, 80, 135, 139, 443, 445, 3389, 5985, 5986) }
        timeout = @{ type = 'float'; default = 1.0 }
    }
    supports_check_mode = $true
}
$module = [Ansible.Basic.AnsibleModule]::Create($args, $spec)

$MaxAddresses = 1024
$Batch = 32
$Settle = 9

function ConvertTo-Number([System.Net.IPAddress]$Address) {
    $bytes = $Address.GetAddressBytes()
    [Array]::Reverse($bytes)
    [BitConverter]::ToUInt32($bytes, 0)
}

function ConvertFrom-Number([uint32]$Number) {
    $bytes = [BitConverter]::GetBytes($Number)
    [Array]::Reverse($bytes)
    ([System.Net.IPAddress]::new($bytes)).ToString()
}

function Get-Target($Subnet, $Addresses) {
    if ($Subnet) {
        $parts = $Subnet.Split('/')
        $address = $null
        if ($parts.Count -ne 2 -or -not [System.Net.IPAddress]::TryParse($parts[0], [ref]$address) -or
            $address.AddressFamily -ne 'InterNetwork' -or $parts[1] -notmatch '^[0-9]{1,2}$' -or [int]$parts[1] -gt 32) {
            throw "not an IPv4 subnet: $Subnet"
        }
        $prefix = [int]$parts[1]
        $size = [math]::Pow(2, 32 - $prefix)
        if ($size -gt $MaxAddresses) { throw "more than $MaxAddresses addresses" }
        $mask = if ($prefix -eq 0) { [uint32]0 } else { [uint32]([math]::Pow(2, 32) - $size) }
        $base = (ConvertTo-Number $address) -band $mask
        if ($base -ne (ConvertTo-Number $address)) { throw "not an IPv4 subnet: $Subnet (bits of a host)" }
        if ($prefix -ge 31) { return @(0..($size - 1) | ForEach-Object { ConvertFrom-Number ($base + $_) }) }
        return @(1..($size - 2) | ForEach-Object { ConvertFrom-Number ($base + $_) })
    }
    $found = [System.Collections.Generic.List[string]]::new()
    foreach ($item in $Addresses) {
        $address = $null
        if (-not [System.Net.IPAddress]::TryParse($item, [ref]$address) -or $address.AddressFamily -ne 'InterNetwork') {
            throw "not an IPv4 address: $item"
        }
        if (-not $found.Contains($address.ToString())) { $found.Add($address.ToString()) }
    }
    if ($found.Count -eq 0 -or $found.Count -gt $MaxAddresses) { throw "give 1 to $MaxAddresses addresses" }
    return $found.ToArray()
}

$params = $module.Params
if ([bool]$params.subnet -eq [bool]$params.addresses) { $module.FailJson('give either subnet or addresses') }
try {
    $targets = @(Get-Target $params.subnet $params.addresses)
} catch {
    $module.FailJson($_.Exception.Message)
}
$ports = @($params.ports | Sort-Object -Unique)
if ($ports.Count -lt 1 -or $ports.Count -gt 32 -or @($ports | Where-Object { $_ -lt 1 -or $_ -gt 65535 }).Count) {
    $module.FailJson('ports must be 1 to 32 port numbers')
}
$timeout = [double]$params.timeout
if ($timeout -lt 0.2 -or $timeout -gt 5) { $module.FailJson('timeout must be between 0.2 and 5 seconds') }
$milliseconds = [int]($timeout * 1000)

$found = @{}
for ($start = 0; $start -lt $targets.Count; $start += $Batch) {
    $batch = $targets[$start..([math]::Min($start + $Batch, $targets.Count) - 1)]
    $pings = @{}
    $connections = [System.Collections.Generic.List[object]]::new()
    foreach ($address in $batch) {
        $pings[$address] = ([System.Net.NetworkInformation.Ping]::new()).SendPingAsync($address, $milliseconds)
        foreach ($port in $ports) {
            $client = [System.Net.Sockets.TcpClient]::new()
            $connections.Add(@{ address = $address; port = $port; client = $client; task = $client.ConnectAsync($address, $port) })
        }
    }
    $tasks = @($pings.Values) + @($connections | ForEach-Object { $_.task })
    try { [void][System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$tasks, $milliseconds + 200) } catch { }
    foreach ($address in $batch) {
        $seen = [System.Collections.Generic.List[string]]::new()
        $open = [System.Collections.Generic.List[int]]::new()
        $answered = $false
        foreach ($connection in @($connections | Where-Object { $_.address -eq $address })) {
            $task = $connection.task
            if ($task.Status -eq 'RanToCompletion') {
                $open.Add($connection.port)
                $answered = $true
            } elseif ($task.IsFaulted) {
                $inner = $task.Exception.InnerException
                if ($inner -is [System.Net.Sockets.SocketException] -and $inner.SocketErrorCode -eq 'ConnectionRefused') {
                    $answered = $true
                }
            }
            $connection.client.Dispose()
        }
        if ($answered) { $seen.Add('tcp') }
        $ping = $pings[$address]
        if ($ping.Status -eq 'RanToCompletion' -and $ping.Result.Status -eq 'Success') { $seen.Add('icmp') }
        if ($seen.Count) {
            $found[$address] = @{ address = $address; seen = $seen.ToArray(); ports = @($open | Sort-Object); mac = '' }
        }
    }
}

function Get-Neighbour($Wanted) {
    $table = @{}
    $states = 'Reachable', 'Stale', 'Delay', 'Probe', 'Permanent'
    foreach ($entry in @(Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue)) {
        $mac = ([string]$entry.LinkLayerAddress).ToLower().Replace('-', ':')
        if ($Wanted.Contains($entry.IPAddress) -and $states -contains [string]$entry.State -and
            $mac -match '^[0-9a-f]{2}(:[0-9a-f]{2}){5}$' -and $mac -ne '00:00:00:00:00:00' -and $mac -ne 'ff:ff:ff:ff:ff:ff') {
            $table[$entry.IPAddress] = $mac
        }
    }
    $table
}

$wanted = [System.Collections.Generic.HashSet[string]]::new([string[]]$targets)
$neighbours = Get-Neighbour $wanted
if (@($neighbours.Keys | Where-Object { -not $found.ContainsKey($_) }).Count) {
    Start-Sleep -Seconds $Settle
    $neighbours = Get-Neighbour $wanted
}
foreach ($address in $neighbours.Keys) {
    if (-not $found.ContainsKey($address)) {
        $found[$address] = @{ address = $address; seen = @(); ports = @(); mac = '' }
    }
    $found[$address].mac = $neighbours[$address]
    $found[$address].seen = @($found[$address].seen) + 'arp'
}

$node = foreach ($adapter in @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' })) {
    $addresses = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" })
    if ($addresses.Count) {
        @{ name = $adapter.Name; mac = ([string]$adapter.MacAddress).ToLower().Replace('-', ':'); addresses = $addresses }
    }
}

$hosts = @($found.Values | Sort-Object { ConvertTo-Number ([System.Net.IPAddress]::Parse($_.address)) })
$module.Result.changed = $false
$module.Result.net_probe = @{ node = @($node); icmp = $true; hosts = $hosts; addresses = $targets.Count }
$module.ExitJson()
