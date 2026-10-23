<#
  flash_usb.ps1 — Escribe una imagen de disco de ErlanOS en una memoria USB.

  SEGURIDAD: sólo escribe en un disco USB extraíble que NO sea de arranque ni de
  sistema. Se niega ante cualquier disco interno. Borra TODO el contenido del USB.

  Uso (en una terminal de PowerShell ABIERTA COMO ADMINISTRADOR):
    powershell -ExecutionPolicy Bypass -File scripts\flash_usb.ps1 `
        -Image build\erlanos-hardware.img -DiskNumber 4

  Encuentra el número de disco del USB con:
    Get-Disk | Format-Table Number, FriendlyName, BusType, @{n='GB';e={[math]::Round($_.Size/1GB,1)}}, IsBoot, IsSystem
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][int]$DiskNumber,
    [double]$MaxSizeGB = 64,          # rechaza discos grandes: los USB de prueba son pequeños
    [switch]$Yes                      # omite la confirmación interactiva (para automatización)
)

$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Host "ABORTADO: $msg" -ForegroundColor Red; exit 1 }

# 1. Elevación
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Fail "Esta ventana no es de Administrador. Abre PowerShell como administrador y repite."
}

# 2. Imagen
$imagePath = (Resolve-Path -LiteralPath $Image).Path
$imageInfo = Get-Item -LiteralPath $imagePath
if ($imageInfo.Length -lt 1MB) { Fail "La imagen es sospechosamente pequeña ($($imageInfo.Length) bytes)." }
Write-Host "Imagen: $imagePath  ($([math]::Round($imageInfo.Length/1MB,1)) MiB)"

# 3. Disco destino y verificaciones de seguridad
$disk = Get-Disk -Number $DiskNumber -ErrorAction SilentlyContinue
if (-not $disk) { Fail "No existe el disco fisico numero $DiskNumber." }
$sizeGB = [math]::Round($disk.Size / 1GB, 2)
Write-Host "Destino: Disco $($disk.Number)  '$($disk.FriendlyName)'  bus=$($disk.BusType)  $sizeGB GB  arranque=$($disk.IsBoot)  sistema=$($disk.IsSystem)"

if ($disk.BusType -ne 'USB')        { Fail "El disco $DiskNumber NO es USB (bus=$($disk.BusType)). Me niego." }
if ($disk.IsBoot)                    { Fail "El disco $DiskNumber es de ARRANQUE. Me niego." }
if ($disk.IsSystem)                  { Fail "El disco $DiskNumber es de SISTEMA. Me niego." }
if ($sizeGB -gt $MaxSizeGB)          { Fail "El disco mide $sizeGB GB (> $MaxSizeGB). Demasiado grande para ser un USB de prueba. Me niego." }
if ($imageInfo.Length -gt $disk.Size) { Fail "La imagen no cabe en el disco." }

# 4. Confirmación explícita
if (-not $Yes) {
    Write-Host ""
    Write-Host "Se BORRARA TODO el contenido del Disco $($disk.Number) ('$($disk.FriendlyName)', $sizeGB GB)." -ForegroundColor Yellow
    $answer = Read-Host "Escribe exactamente BORRAR para continuar"
    if ($answer -ne 'BORRAR') { Fail "Confirmacion no recibida." }
}

# 5. Limpiar el disco (desmonta volumenes) y escribir la imagen cruda
Write-Host "Limpiando la tabla de particiones..."
Clear-Disk -Number $disk.Number -RemoveData -RemoveOEM -Confirm:$false
Start-Sleep -Seconds 1

$devicePath = "\\.\PhysicalDrive$($disk.Number)"
Write-Host "Escribiendo $($imageInfo.Length) bytes en $devicePath ..."
$src = [System.IO.File]::OpenRead($imagePath)
try {
    $dst = New-Object System.IO.FileStream($devicePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $bufSize = 1MB                       # multiplo del tamano de sector
        $buffer = New-Object byte[] $bufSize
        $total = 0L
        while (($read = $src.Read($buffer, 0, $bufSize)) -gt 0) {
            if ($read -lt $bufSize) {
                # alinear la ultima escritura a 4096 (los .img de ErlanOS ya son multiplos)
                $aligned = [math]::Ceiling($read / 4096) * 4096
                for ($i = $read; $i -lt $aligned; $i++) { $buffer[$i] = 0 }
                $dst.Write($buffer, 0, $aligned)
            } else {
                $dst.Write($buffer, 0, $read)
            }
            $total += $read
            Write-Progress -Activity "Escribiendo en USB" -Status "$([math]::Round($total/1MB,1)) MiB" -PercentComplete ([math]::Min(100, $total * 100 / $imageInfo.Length))
        }
        $dst.Flush()
    } finally { $dst.Dispose() }
} finally { $src.Dispose() }

Write-Progress -Activity "Escribiendo en USB" -Completed
Write-Host ""
Write-Host "LISTO. El USB (Disco $($disk.Number)) contiene la imagen de ErlanOS." -ForegroundColor Green
Write-Host "Reinicia, entra al menu de arranque (F8 en placas ASUS) y elige el USB en modo UEFI."
