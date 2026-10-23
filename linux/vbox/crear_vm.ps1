<#
ErlanOS LB0: crea la VM de desarrollo "ErlanOS-Base" en VirtualBox e instala Debian 13
minima sin intervencion (ver docs/LINUX-BASE.md).

  powershell -ExecutionPolicy Bypass -File linux\vbox\crear_vm.ps1            # crea e instala
  powershell -ExecutionPolicy Bypass -File linux\vbox\crear_vm.ps1 -Ensayo    # solo genera archivos
  powershell -ExecutionPolicy Bypass -File linux\vbox\crear_vm.ps1 -Recrear   # borra SOLO ErlanOS-Base y la rehace

Nunca toca otras VMs. La ISO debe estar en build\linux\iso y coincidir con SHA256SUMS.
La llave SSH y el hash de la contrasena salen de build\linux (fuera de git).
Archivo en ASCII a proposito: PowerShell 5.1 lee mal UTF-8 sin BOM.
#>
param(
    [switch]$Ensayo,
    [switch]$Recrear,
    [int]$Cpus = 4,
    [int]$MemoriaMB = 4096,
    [int]$DiscoGB = 32,
    [int]$PuertoSsh = 2222
)
$ErrorActionPreference = 'Stop'

$Raiz = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$VBox = Join-Path $env:ProgramFiles 'Oracle\VirtualBox\VBoxManage.exe'
$Nombre = 'ErlanOS-Base'
$Build = Join-Path $Raiz 'build\linux'
$IsoNombre = 'debian-13.7.0-amd64-netinst.iso'
$Iso = Join-Path $Build "iso\$IsoNombre"
$VmDir = Join-Path $Build 'vm'
$Aux = Join-Path $Build 'aux'
$Serial = Join-Path $Build 'serial-lb0.log'

function VB {
    & $VBox @args
    if ($LASTEXITCODE -ne 0) { throw "VBoxManage $($args -join ' ') fallo con codigo $LASTEXITCODE" }
}

if (-not (Test-Path $VBox)) { throw "No se encontro VBoxManage en $VBox" }
if (-not (Test-Path $Iso)) { throw "Falta la ISO: $Iso" }

# 1. La ISO debe coincidir con la suma oficial (la firma GPG se verifico al descargar).
$linea = Select-String -Path (Join-Path $Build 'iso\SHA256SUMS') -Pattern ([regex]::Escape($IsoNombre) + '$') | Select-Object -First 1
if (-not $linea) { throw "SHA256SUMS no lista $IsoNombre" }
$esperado = ($linea.Line -split '\s+')[0].ToLower()
$real = (Get-FileHash $Iso -Algorithm SHA256).Hash.ToLower()
if ($esperado -ne $real) { throw "SHA-256 de la ISO no coincide: $real (esperado $esperado)" }
Write-Output "[lb0] ISO verificada sha256=$real"

# 2. Preseed con la llave y el hash (se escribe en build\linux\secrets, con saltos LF).
$llave = (Get-Content (Join-Path $Build 'ssh\erlanos_vm.pub') -Raw).Trim()
$hash = (Get-Content (Join-Path $Build 'secrets\vm-password.sha512') -Raw).Trim()
if (-not $llave.StartsWith('ssh-ed25519 ')) { throw 'Llave SSH invalida' }
if (-not $hash.StartsWith('$6$')) { throw 'Hash de contrasena invalido' }
$plantilla = Get-Content (Join-Path $PSScriptRoot 'preseed.cfg.in') -Raw
$preseed = $plantilla.Replace('@@ERLAN_SSH_KEY@@', $llave).Replace('@@ERLAN_PASSWORD_HASH@@', $hash).Replace("`r`n", "`n")
if ($preseed.Contains('@@')) { throw 'Quedaron marcadores sin sustituir en el preseed' }
$PreseedPath = Join-Path $Build 'secrets\preseed.cfg'
[IO.File]::WriteAllText($PreseedPath, $preseed, (New-Object Text.UTF8Encoding $false))
Write-Output "[lb0] preseed generado en $PreseedPath"

# 3. VM: si existe, solo se rehace con -Recrear.
$existe = @(& $VBox list vms | Where-Object { $_ -like "`"$Nombre`" *" }).Count -gt 0
if ($existe -and -not $Ensayo) {
    if (-not $Recrear) { throw "La VM $Nombre ya existe. Usa -Recrear para rehacerla." }
    Write-Output "[lb0] borrando la VM anterior $Nombre"
    & $VBox controlvm $Nombre poweroff 2>$null | Out-Null
    Start-Sleep -Seconds 3
    VB unregistervm $Nombre --delete
}

$tipos = & $VBox list ostypes | Where-Object { $_ -match '^ID:\s+(\S+)' } | ForEach-Object { ($_ -split '\s+')[1] }
$OsType = @('Debian13_64', 'Debian12_64', 'Debian_64') | Where-Object { $tipos -contains $_ } | Select-Object -First 1
if (-not $OsType) { throw 'VirtualBox no ofrece un tipo Debian de 64 bits' }

if ($Ensayo) {
    if (-not $existe) {
        VB createvm --name $Nombre --ostype $OsType --register --basefolder $VmDir
    }
    New-Item -ItemType Directory -Force $Aux | Out-Null
    VB unattended install $Nombre "--iso=$Iso" --user=erlan "--user-password-file=$(Join-Path $Build 'secrets\vm-password.txt')" `
        --full-user-name=ErlanOS --hostname=erlanos.local --locale=es_BO --country=BO --time-zone=America/La_Paz `
        "--script-template=$PreseedPath" "--auxiliary-base-path=$Aux\" --dry-run
    Write-Output "[lb0] ensayo listo: revisa $Aux"
    exit 0
}

VB createvm --name $Nombre --ostype $OsType --register --basefolder $VmDir
VB modifyvm $Nombre --cpus $Cpus --memory $MemoriaMB --vram 32 --graphicscontroller vmsvga `
    --firmware bios --rtcuseutc on --audio-driver none `
    --nic1 nat --nictype1 82540EM --natpf1 "ssh,tcp,127.0.0.1,$PuertoSsh,,22" `
    --uart1 0x3F8 4 --uartmode1 file $Serial `
    --boot1 dvd --boot2 disk --boot3 none --boot4 none

$Vdi = Join-Path $VmDir "$Nombre\$Nombre.vdi"
VB createmedium disk --filename $Vdi --size ($DiscoGB * 1024) --format VDI
VB storagectl $Nombre --name SATA --add sata --controller IntelAhci --portcount 4 --bootable on
VB storageattach $Nombre --storagectl SATA --port 0 --device 0 --type hdd --medium $Vdi

New-Item -ItemType Directory -Force $Aux | Out-Null
VB unattended install $Nombre "--iso=$Iso" --user=erlan "--user-password-file=$(Join-Path $Build 'secrets\vm-password.txt')" `
    --full-user-name=ErlanOS --hostname=erlanos.local --locale=es_BO --country=BO --time-zone=America/La_Paz `
    "--script-template=$PreseedPath" "--auxiliary-base-path=$Aux\" --start-vm=headless

Write-Output "[lb0] VM $Nombre instalando sin pantalla. SSH cuando termine: 127.0.0.1:$PuertoSsh"
Write-Output "[lb0] consola serie: $Serial"
Write-Output "[lb0] verificar: python linux\vbox\verificar_lb0.py"
