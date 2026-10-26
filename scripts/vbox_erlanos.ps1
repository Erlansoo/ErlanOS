<#
ErlanOS en VirtualBox. Toma la imagen de disco que produce el build, crea la VM "ErlanOS-Lab"
con firmware EFI, puerto serie a archivo y NIC Intel PRO/1000 MT Desktop (82540EM, el mismo
chip que atiende kernel\src\e1000.e) en NAT, la arranca sin pantalla, espera los marcadores del
serial, toma capturas y deja la evidencia en build\vbox-erlanos\<fecha>-<perfil>\:

  serial.log      todo lo que el kernel escribio por COM1
  *.png           capturas de la pantalla de la VM (VBoxManage controlvm screenshotpng)
  red.pcap        trazas de la NIC que escribio VirtualBox (perfil red)
  vbox.log        registro de VirtualBox de esa ejecucion (motor usado, firmware, errores)
  vm.txt          configuracion completa de la VM (showvminfo)
  result.json     resultado: marcadores con su segundo, tiempos, verificaciones del anfitrion

Uso:
  powershell -ExecutionPolicy Bypass -File scripts\vbox_erlanos.ps1                    # qwen35: SO completo con modelo
  powershell -ExecutionPolicy Bypass -File scripts\vbox_erlanos.ps1 -Perfil red        # DHCP, DNS, TCP y HTTP por la pila net\
  powershell -ExecutionPolicy Bypass -File scripts\vbox_erlanos.ps1 -Perfil hardware   # inventario PCI (diagnostico G0)
  powershell -ExecutionPolicy Bypass -File scripts\vbox_erlanos.ps1 -Interactivo       # con ventana; la VM queda encendida
  powershell -ExecutionPolicy Bypass -File scripts\vbox_erlanos.ps1 -Construir         # antes, make en Git Bash
  powershell -ExecutionPolicy Bypass -File scripts\vbox_erlanos.ps1 -Taller            # disco con particion de trabajos (AHCI)
  powershell -ExecutionPolicy Bypass -File scripts\vbox_erlanos.ps1 -Taller -Nvme      # el mismo disco en un controlador NVMe

Perfiles e imagenes (las produce el build; ver README.md y Makefile):
  red       build\erlanos-net.img        make net-image
  hardware  build\erlanos-hardware.img   make hardware-image
  qwen35    build\erlanos-qwen35.img     make esp
  coder     build\erlanos-coder.img      make esp MODEL=coder
  smollm    build\erlanos-smollm.img     make esp MODEL=smollm

Solo toca la VM "ErlanOS-Lab" y solo si su archivo .vbox vive en build\vbox-erlanos. Nunca borra
otra VM, no escribe en discos fisicos y no copia ni mueve los GGUF de models\ (la imagen ya los
contiene). Archivo en ASCII a proposito: PowerShell 5.1 lee mal UTF-8 sin BOM.
#>
param(
    [ValidateSet('red', 'hardware', 'qwen35', 'coder', 'smollm')][string]$Perfil = 'qwen35',
    [string]$Imagen,
    [int]$MemoriaMB = 0,
    [int]$Cpus = 2,
    [int]$Tiempo = 0,
    [string]$Comando = '/estado',
    [string]$Resolucion = '1280x800',
    [switch]$Interactivo,
    [switch]$Mantener,
    [switch]$Construir,
    [switch]$Taller,
    [switch]$Nvme
)
$ErrorActionPreference = 'Stop'

$Raiz = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VBox = Join-Path $env:ProgramFiles 'Oracle\VirtualBox\VBoxManage.exe'
if ($env:VBOX_MSI_INSTALL_PATH) { $VBox = Join-Path $env:VBOX_MSI_INSTALL_PATH 'VBoxManage.exe' }
$Nombre = 'ErlanOS-Lab'
$Base = Join-Path $Raiz 'build\vbox-erlanos'
$Inicio = Get-Date
$Run = Join-Path $Base ($Inicio.ToString('yyyyMMdd-HHmmss') + "-$Perfil")

$Perfiles = @{
    red      = @{ imagen = 'build\erlanos-net.img';      make = 'net-image';        memoria = 512;  tiempo = 420;  listo = '[net] probe: ready' }
    hardware = @{ imagen = 'build\erlanos-hardware.img'; make = 'hardware-image';   memoria = 512;  tiempo = 180;  listo = '[hardware] probe: ready' }
    qwen35   = @{ imagen = 'build\erlanos-qwen35.img';   make = 'esp';              memoria = 4096; tiempo = 900;  listo = '[model] m4: ready' }
    coder    = @{ imagen = 'build\erlanos-coder.img';    make = 'esp MODEL=coder';  memoria = 4096; tiempo = 900;  listo = '[model] m4: ready' }
    smollm   = @{ imagen = 'build\erlanos-smollm.img';   make = 'esp MODEL=smollm'; memoria = 1024; tiempo = 600;  listo = '[model] m4: ready' }
}
$P = $Perfiles[$Perfil]
if ($MemoriaMB -le 0) { $MemoriaMB = $P.memoria }
if ($Tiempo -le 0) { $Tiempo = $P.tiempo }
if (-not $Imagen) { $Imagen = Join-Path $Raiz $P.imagen }
$EsModelo = ($Perfil -eq 'qwen35' -or $Perfil -eq 'coder' -or $Perfil -eq 'smollm')
if ($Interactivo) { $Mantener = $true }

# Marcadores informativos que se buscan en el serial, ademas del marcador "listo" del perfil.
$Marcadores = @('ErlanOS L1', 'bootloader: Limine', '[gdt]', '[pci] end devices=', 'framebuffer:', '[console]', '[acpi] madt', '[apic] lapic', '[apic] reloj del sistema', '[i8042] controlador', '[apic] modo IOAPIC')
if ($Perfil -eq 'red') {
    $Marcadores += @('[kbd] ps/2 listo', '[e1000] listo', '[net] mac', '[net] enlace arriba', '[net] arp ok gw=',
                     '[net] dhcp ok', '[net] arp ok dns=', '[net] dns ok', '[net] INTERNET OK', '[net] tcp bulk ok',
                     '[net] http ok', '[net] FALLO')
}
if ($Perfil -eq 'hardware') { $Marcadores += @('[hardware]') }
if ($EsModelo) {
    $Marcadores += @('[kbd] ps/2 listo', '[storage]', 'estado: idle', '[system] system: listo',
                     '[model] model: iniciando', '[model] model: gguf bytes=', '[model] matmul:',
                     '[model] model: listo', '[model] m4: command done', '[m4] receipt=')
}
$Fatal = 'KERNEL PANIC|EXCEPCI|\[panic\]|!!!! X64 Exception Type|erlan: index out of bounds'

function Log($m) { Write-Host ("[vbox] " + $m) }
# VBoxManage escribe los errores en stderr; en PowerShell 5.1 redirigirlos con $ErrorActionPreference
# = Stop aborta el script, asi que estas tres funciones lo bajan a Continue mientras llaman.
function VB {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $salida = & $VBox @args 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0) { throw "VBoxManage $($args -join ' ') fallo con codigo $LASTEXITCODE`n$($salida -join "`n")" }
    return $salida
}
function VBQuiet {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $VBox @args 2>&1 | Out-Null
    $ErrorActionPreference = $prev
}
function VBOut {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $salida = & $VBox @args 2>$null
    $ErrorActionPreference = $prev
    return $salida
}
function LeerSerial {
    if (-not (Test-Path $Serial)) { return '' }
    $fs = [IO.File]::Open($Serial, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try { $sr = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8); $t = $sr.ReadToEnd(); $sr.Close(); return $t }
    finally { $fs.Dispose() }
}
function EstadoVm {
    $linea = VBOut showvminfo $Nombre --machinereadable | Where-Object { $_ -like 'VMState=*' } | Select-Object -First 1
    if ($linea) { return ($linea -split '=')[1].Trim('"') }
    return 'desconocido'
}
# El parametro no se llama $nombre: PowerShell no distingue mayusculas y taparia $Nombre (la VM).
function Captura($archivo) {
    $destino = Join-Path $Run $archivo
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $salida = & $VBox controlvm $Nombre screenshotpng $destino 2>&1
    $ErrorActionPreference = $prev
    if (Test-Path $destino) { Log "captura $archivo"; return $archivo }
    Log ("sin captura " + $archivo + ": " + (($salida | ForEach-Object { [string]$_ }) -join ' '))
    return $null
}
$Vistos = @{}
function RegistrarMarcadores($texto) {
    foreach ($m in ($Marcadores + @($P.listo))) {
        if (-not $Vistos.ContainsKey($m) -and $texto.Contains($m)) {
            $Vistos[$m] = [math]::Round(((Get-Date) - $Arranque).TotalSeconds, 1)
            Log ("{0,7:n1}s  {1}" -f $Vistos[$m], $m)
        }
    }
}
function Esperar($marca, $segundos, $descripcion) {
    $limite = (Get-Date).AddSeconds($segundos); $ultimo = Get-Date
    while ($true) {
        $texto = LeerSerial
        RegistrarMarcadores $texto
        if ($texto.Contains($marca)) { return $texto }
        if ($texto -match $Fatal) { throw ("fallo del kernel en el serial: " + $Matches[0]) }
        if ((Get-Date) -gt $limite) { throw "plazo de $segundos s agotado esperando $descripcion" }
        if (((Get-Date) - $ultimo).TotalSeconds -ge 10) {
            $estado = EstadoVm
            if ($estado -ne 'running' -and $estado -ne 'starting') { throw "la VM paso a estado '$estado' esperando $descripcion" }
            $ultimo = Get-Date
        }
        Start-Sleep -Milliseconds 700
    }
}
# Teclado PS/2: scancodes set 1 (los que traduce kernel\src\keyboard.e). Mayusculas y simbolos con Shift.
$Scan = @{ a=0x1E; b=0x30; c=0x2E; d=0x20; e=0x12; f=0x21; g=0x22; h=0x23; i=0x17; j=0x24; k=0x25; l=0x26; m=0x32;
           n=0x31; o=0x18; p=0x19; q=0x10; r=0x13; s=0x1F; t=0x14; u=0x16; v=0x2F; w=0x11; x=0x2D; y=0x15; z=0x2C;
           '1'=0x02; '2'=0x03; '3'=0x04; '4'=0x05; '5'=0x06; '6'=0x07; '7'=0x08; '8'=0x09; '9'=0x0A; '0'=0x0B;
           '-'=0x0C; '='=0x0D; ' '=0x39; '/'=0x35; '.'=0x34; ','=0x33; ';'=0x27; "'"=0x28; '['=0x1A; ']'=0x1B; '\'=0x2B }
$ScanShift = @{ '!'='1'; '@'='2'; '#'='3'; '$'='4'; '%'='5'; '^'='6'; '&'='7'; '*'='8'; '('='9'; ')'='0'; '_'='-'; '+'='=';
                '?'='/'; ':'=';'; '"'="'"; '<'=','; '>'='.'; '{'='['; '}'=']'; '|'='\' }
function Teclear($texto) {
    foreach ($ch in $texto.ToCharArray()) {
        $c = [string]$ch; $shift = $false
        if ($c -cmatch '^[A-Z]$') { $c = $c.ToLower(); $shift = $true }
        elseif ($ScanShift.ContainsKey($c)) { $c = $ScanShift[$c]; $shift = $true }
        if (-not $Scan.ContainsKey($c)) { throw "caracter sin scancode: '$ch'" }
        $code = $Scan[$c]
        $codigos = @()
        if ($shift) { $codigos += '2a' }
        $codigos += ('{0:x2}' -f $code); $codigos += ('{0:x2}' -f ($code + 0x80))
        if ($shift) { $codigos += 'aa' }
        VBQuiet controlvm $Nombre keyboardputscancode @codigos
        Start-Sleep -Milliseconds 60
    }
}
function Enter { VBQuiet controlvm $Nombre keyboardputscancode 1c 9c; Start-Sleep -Milliseconds 60 }

# ------------------------------------------------------------------ comprobaciones previas
if (-not (Test-Path $VBox)) { throw "No se encontro VBoxManage en $VBox (instala VirtualBox o define VBOX_MSI_INSTALL_PATH)" }
$VersionVBox = [string](VBOut --version | Select-Object -First 1)
if ($Construir) {
    $bash = Get-Command bash -ErrorAction SilentlyContinue
    $bashExe = if ($bash) { $bash.Source } else { 'C:\Program Files\Git\bin\bash.exe' }
    Log "make $($P.make) en Git Bash"
    & $bashExe -lc "cd '$($Raiz -replace '\\','/')' && source scripts/env.sh && make $($P.make)"
    if ($LASTEXITCODE -ne 0) { throw "make $($P.make) fallo" }
}
New-Item -ItemType Directory -Force $Run | Out-Null
if ($Taller) {
    # Disco de trabajo nuevo (ESP + particion "Erlan Workspace" vacia), como data\workspace.img
    # pero privado de esta ejecucion: prueba el driver AHCI y la persistencia D1 sin tocar datos.
    if (-not $EsModelo) { throw "-Taller requiere un perfil con servicios (qwen35, coder, smollm)" }
    $modelo = @{ qwen35 = 'models\qwen3.5-2b-q8_0.gguf'; coder = 'models\qwen2.5-coder-1.5b-instruct-q8_0.gguf'; smollm = 'models\smollm2-135m-instruct-q8_0.gguf' }[$Perfil]
    $Imagen = Join-Path $Run 'taller.img'
    $codigo = "import sys; sys.path.insert(0, 'scripts'); from pathlib import Path; from workspace_disk import ensure_disk; print(ensure_disk(Path(r'$Imagen'), Path(r'$modelo'))['bytes'])"
    Log "creando el disco de trabajo $Imagen"
    & python -c $codigo
    if ($LASTEXITCODE -ne 0) { throw "no se pudo crear el disco de trabajo" }
    if ($Nvme) { $Marcadores += @('[nvme] listo', '[storage] nvme') } else { $Marcadores += @('[ahci]', '[storage] ahci') }
    $Marcadores += @('[storage] volumen Erlan montado', '[system] workspace: objetos=')
}
if (-not (Test-Path $Imagen)) { throw "Falta la imagen $Imagen. Construyela en Git Bash: source scripts/env.sh && make $($P.make)" }
$Imagen = (Resolve-Path $Imagen).Path
$Manifiesto = "$Imagen.manifest.json"

$Serial = Join-Path $Run 'serial.log'
$Pcap = Join-Path $Run 'red.pcap'
$Vdi = Join-Path $Run 'disk.vdi'
$VmDir = Join-Path $Run 'vm'
Log "VirtualBox $VersionVBox"
Log "perfil=$Perfil imagen=$Imagen memoria=${MemoriaMB}MB plazo=${Tiempo}s"
Log "evidencia en $Run"

$Resultado = [ordered]@{
    suite = 'vbox-erlanos'; perfil = $Perfil; passed = $false; fecha = $Inicio.ToString('s')
    directorio = $Run; vm = $Nombre; virtualbox = $VersionVBox
    host = [ordered]@{ hypervisor_present = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent
                       cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
                       windows = (Get-CimInstance Win32_OperatingSystem).Version }
    imagen = [ordered]@{ ruta = $Imagen; bytes = (Get-Item $Imagen).Length; sha256 = $null; manifiesto = $null }
    vm_config = [ordered]@{ firmware = 'efi'; chipset = 'piix3'; ioapic = 'on'; cpus = $Cpus; memoria_mb = $MemoriaMB
                            nic = '82540EM (Intel PRO/1000 MT Desktop) en NAT con traza pcap y localhost alcanzable (10.0.2.2 -> 127.0.0.1)'
                            serie = 'COM1 0x3F8 irq4 -> serial.log'; teclado = 'ps2'; raton = 'ps2'
                            grafica = 'vmsvga'; resolucion_efi = $Resolucion
                            disco = 'SATA AHCI puerto 0, VDI convertido de la imagen cruda' }
    marcadores = $null; marcadores_faltantes = $null; capturas = @(); comando = $null; red = $null
    motor = $null; duracion_s = $null; fallo = $null
}
Log "sha256 de la imagen (puede tardar con 2 GB)"
$Resultado.imagen.sha256 = (Get-FileHash $Imagen -Algorithm SHA256).Hash.ToLower()
if (Test-Path $Manifiesto) {
    Copy-Item $Manifiesto (Join-Path $Run 'imagen.manifest.json')
    $Resultado.imagen.manifiesto = (Get-Content $Manifiesto -Raw | ConvertFrom-Json)
}

# ------------------------------------------------------------------ VM anterior con el mismo nombre
$existe = @(VBOut list vms | Where-Object { $_ -like "`"$Nombre`" *" }).Count -gt 0
if ($existe) {
    $cfg = VBOut showvminfo $Nombre --machinereadable | Where-Object { $_ -like 'CfgFile=*' } | Select-Object -First 1
    $cfg = if ($cfg) { ($cfg -split '=', 2)[1].Trim('"') } else { '' }
    if (-not $cfg.StartsWith($Base, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Ya existe una VM '$Nombre' fuera de $Base ($cfg). No la toco: renombrala o borrala tu."
    }
    Log "quitando la VM anterior $Nombre ($cfg)"
    VBQuiet controlvm $Nombre poweroff
    Start-Sleep -Seconds 2
    VB unregistervm $Nombre --delete | Out-Null
}

$Servidor = $null
$Arranque = Get-Date
try {
    # ------------------------------------------------------------------ disco y VM
    Log "convirtiendo la imagen cruda a VDI"
    VB convertfromraw $Imagen $Vdi --format VDI | Out-Null
    VB createvm --name $Nombre --ostype Other_64 --register --basefolder $VmDir | Out-Null
    VB modifyvm $Nombre --cpus $Cpus --memory $MemoriaMB --vram 32 --graphicscontroller vmsvga `
        --firmware efi --chipset piix3 --ioapic on --acpi on --rtc-use-utc on `
        --audio-enabled off --mouse ps2 --keyboard ps2 `
        --nic1 nat --nic-type1 82540EM --cable-connected1 on --nic-trace1 on --nic-trace-file1 $Pcap `
        --nat-localhostreachable1 on `
        --uart1 0x3F8 4 --uart-mode1 file $Serial `
        --boot1 disk --boot2 none --boot3 none --boot4 none | Out-Null
    VB setextradata $Nombre VBoxInternal2/EfiGraphicsResolution $Resolucion | Out-Null
    if ($Nvme) {
        VB storagectl $Nombre --name NVMe --add pcie --controller NVMe --portcount 1 --bootable on | Out-Null
        VB storageattach $Nombre --storagectl NVMe --port 0 --device 0 --type hdd --medium $Vdi | Out-Null
    } else {
        VB storagectl $Nombre --name SATA --add sata --controller IntelAhci --portcount 1 --bootable on | Out-Null
        VB storageattach $Nombre --storagectl SATA --port 0 --device 0 --type hdd --medium $Vdi | Out-Null
    }
    VBOut showvminfo $Nombre | Out-File (Join-Path $Run 'vm.txt') -Encoding utf8

    # ------------------------------------------------------------------ oraculo del anfitrion (perfil red)
    if ($Perfil -eq 'red') {
        $argumentos = "`"$(Join-Path $Raiz 'scripts\vbox_erlanos_oracle.py')`" serve --serial `"$Serial`" --timeout $($Tiempo + 60) --out `"$(Join-Path $Run 'servidor.json')`""
        $Servidor = Start-Process python -ArgumentList $argumentos -WorkingDirectory $Raiz -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $Run 'servidor.log') -RedirectStandardError (Join-Path $Run 'servidor.err')
        Log "servidor TCP del anfitrion en 127.0.0.1:18765 (pid $($Servidor.Id))"
    }

    # ------------------------------------------------------------------ arranque
    $tipo = if ($Interactivo) { 'gui' } else { 'headless' }
    Log "arrancando ($tipo)"
    $Arranque = Get-Date
    VB startvm $Nombre --type $tipo | Out-Null
    Esperar 'ErlanOS L1' ([math]::Min($Tiempo, 120)) 'la primera linea del kernel' | Out-Null
    Start-Sleep -Seconds 2
    $c = Captura '01-arranque.png'; if ($c) { $Resultado.capturas += $c }
    Esperar $P.listo $Tiempo "el marcador '$($P.listo)'" | Out-Null
    Start-Sleep -Seconds 2
    $c = Captura '02-listo.png'; if ($c) { $Resultado.capturas += $c }

    # ------------------------------------------------------------------ teclado PS/2 (perfiles con servicios)
    if ($EsModelo -and $Comando) {
        $antes = (LeerSerial).Split([string[]]@('[model] m4: command done'), [StringSplitOptions]::None).Count
        Log "tecleando '$Comando' por PS/2"
        $t0 = Get-Date
        Teclear $Comando; Enter
        $texto = Esperar '[model] m4: command done' 180 "la respuesta a '$Comando'"
        $despues = $texto.Split([string[]]@('[model] m4: command done'), [StringSplitOptions]::None).Count
        $recibos = @([regex]::Matches($texto, '\[m4\] receipt=\d+ request=\d+ op=\d+ status=\d+ mode=\d+ revision=\d+') | ForEach-Object { $_.Value })
        $Resultado.comando = [ordered]@{ texto = $Comando; respondido = ($despues -gt $antes)
                                         segundos = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1); recibos = $recibos }
        Start-Sleep -Seconds 2
        $c = Captura '03-comando.png'; if ($c) { $Resultado.capturas += $c }
    }
    if ($Perfil -eq 'red') {
        Start-Sleep -Seconds 3
        $c = Captura '03-red.png'; if ($c) { $Resultado.capturas += $c }
    }
    $Resultado.passed = $true
}
catch {
    $Resultado.fallo = $_.Exception.Message
    Log ("FALLO: " + $_.Exception.Message)
    $c = Captura '90-fallo.png'; if ($c) { $Resultado.capturas += $c }
}
finally {
    RegistrarMarcadores (LeerSerial)
    $c = Captura '99-final.png'; if ($c) { $Resultado.capturas += $c }
    if (-not $Interactivo) {
        VBQuiet controlvm $Nombre poweroff
        $espera = 0
        while ((EstadoVm) -ne 'poweroff' -and $espera -lt 30) { Start-Sleep -Seconds 1; $espera++ }
    }
    if ($Servidor) {
        if (-not $Servidor.WaitForExit(40000)) { try { $Servidor.Kill() } catch { } }
        $verificacion = Join-Path $Run 'red.json'
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & python (Join-Path $Raiz 'scripts\vbox_erlanos_oracle.py') verify --serial $Serial --pcap $Pcap `
            --server (Join-Path $Run 'servidor.json') --out $verificacion 2>&1 | ForEach-Object { Log $_ }
        $ErrorActionPreference = $prev
        if (Test-Path $verificacion) {
            $Resultado.red = (Get-Content $verificacion -Raw -Encoding UTF8 | ConvertFrom-Json)
            if (-not $Resultado.red.passed_basic) {
                $Resultado.passed = $false
                if (-not $Resultado.fallo) { $Resultado.fallo = ($Resultado.red.reasons -join '; ') }
            }
        }
    }
    $logVm = Get-ChildItem (Join-Path $VmDir "$Nombre\Logs\VBox.log") -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($logVm) {
        Copy-Item $logVm.FullName (Join-Path $Run 'vbox.log')
        $motor = Select-String -Path $logVm.FullName -Pattern 'HM: HMR3Init: Attempting fall back to NEM|NEM: .*Hyper-V|NEM: NEMR3Init|HM: Using VT-x implementation|HM: Using AMD-V|VMSetError' | Select-Object -First 4
        $Resultado.motor = @($motor | ForEach-Object { $_.Line.Trim() })
    }
    $orden = [ordered]@{}
    foreach ($k in ($Vistos.Keys | Sort-Object { $Vistos[$_] })) { $orden[$k] = $Vistos[$k] }
    $Resultado.marcadores = $orden
    $Resultado.marcadores_faltantes = @(($Marcadores + @($P.listo)) | Where-Object { -not $Vistos.ContainsKey($_) })
    $Resultado.duracion_s = [math]::Round(((Get-Date) - $Inicio).TotalSeconds, 1)
    $json = $Resultado | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText((Join-Path $Run 'result.json'), $json, (New-Object Text.UTF8Encoding $false))
    if (-not $Mantener) {
        VBQuiet unregistervm $Nombre --delete
        Remove-Item $Vdi -Force -ErrorAction SilentlyContinue
    } else {
        Log "la VM $Nombre queda registrada; apagala con: VBoxManage controlvm $Nombre poweroff"
    }
    $estado = if ($Resultado.passed) { 'PASS' } else { 'FAIL' }
    Log "$estado $Perfil -> $(Join-Path $Run 'result.json')"
}
if (-not $Resultado.passed) { exit 1 }
