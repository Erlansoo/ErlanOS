<#
Construye Halo.exe en la raiz del repositorio con el compilador de C# que trae Windows.

  powershell -ExecutionPolicy Bypass -File halo\lanzador\construir.ps1

Archivo en ASCII a proposito: PowerShell 5.1 lee mal UTF-8 sin BOM.
#>
$ErrorActionPreference = 'Stop'
$Raiz = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Csc = Join-Path $env:windir 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $Csc)) { throw "No encuentro el compilador de C# en $Csc" }

$py = (Get-Command python).Source
& $py (Join-Path $PSScriptRoot 'icono.py')
if ($LASTEXITCODE -ne 0) { throw 'No se pudo dibujar el icono' }

$Icono = Join-Path $Raiz 'build\halo\halo.ico'
$Salida = Join-Path $Raiz 'Halo.exe'
& $Csc /nologo /target:winexe /optimize+ "/out:$Salida" "/win32icon:$Icono" /reference:System.Windows.Forms.dll (Join-Path $PSScriptRoot 'Halo.cs')
if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar Halo.exe' }
Write-Output "Listo: $Salida"
