# Read-only inspection of the specific Kingston USB. Never mounts or writes it.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$outputDirectory = Join-Path $workspace ('build\usb-inspection-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $outputDirectory -ErrorAction Stop | Out-Null
$statusPath = Join-Path $outputDirectory 'status.json'
try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
        throw 'This reader requires an administrator process.'
    }
    $expectedId = 'USBSTOR\DISK&VEN_KINGSTON&PROD_DATATRAVELER_2.0&REV_1.00\08606E6B6612FDA0F716879D&0:DESKTOP-DKT77GU'
    $matches = @(Get-Disk | Where-Object { $_.UniqueId -eq $expectedId })
    if ($matches.Count -ne 1) { throw 'The identified Kingston USB is not uniquely present.' }
    $usbDisk = $matches[0]
    if ($usbDisk.BusType -ne 'USB' -or $usbDisk.IsBoot -or $usbDisk.IsSystem -or $usbDisk.Size -ne 7747397632) {
        throw 'USB identity or safety checks failed.'
    }
    $partitions = @(Get-Partition -DiskNumber $usbDisk.Number)
    $usbDisk | Select-Object Number,FriendlyName,UniqueId,Size,BusType,IsBoot,IsSystem,PartitionStyle |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputDirectory 'disk.json') -Encoding UTF8
    $partitions | Select-Object DiskNumber,PartitionNumber,Offset,Size,Type,GptType,AccessPaths |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $outputDirectory 'partitions.json') -Encoding UTF8
    $readEnd = 0L
    foreach ($partition in $partitions) {
        $readEnd = [Math]::Max($readEnd, [long]$partition.Offset + [long]$partition.Size)
    }
    if ($readEnd -lt 1MB -or $readEnd -gt 512MB -or $readEnd -gt $usbDisk.Size) {
        throw 'Unexpected partition span; refusing automatic snapshot.'
    }
    # Explicit FileAccess.Read: the physical device cannot be written by this handle.
    $devicePath = '\\.\PhysicalDrive' + $usbDisk.Number
    $usbReader = [IO.File]::Open($devicePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try {
        $snapshotPath = Join-Path $outputDirectory 'usb-partition-snapshot.img'
        $snapshotWriter = [IO.File]::Open($snapshotPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $buffer = New-Object byte[] (1MB)
            $remaining = $readEnd
            while ($remaining -gt 0) {
                $read = $usbReader.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                if ($read -eq 0) { throw 'Unexpected end of USB device.' }
                $snapshotWriter.Write($buffer, 0, $read)
                $remaining -= $read
            }
        } finally { $snapshotWriter.Dispose() }
    } finally { $usbReader.Dispose() }
    @{state='complete'; bytes=$readEnd; snapshot=$snapshotPath; sourceReadOnly=$true} |
        ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
} catch {
    @{state='error'; message=$_.Exception.Message; sourceReadOnly=$true} |
        ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
    exit 1
}
