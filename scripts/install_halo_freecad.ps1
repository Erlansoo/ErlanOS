<# Motor FreeCAD portatil oficial para Halo. No necesita administrador. #>
$ErrorActionPreference = 'Stop'
$fcRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$fcBase = Join-Path $fcRepo 'build/halo-motores'
$fcRoot = Join-Path $fcBase 'freecad'
$fcArchive = Join-Path $fcBase 'FreeCAD-1.1.3.7z'
$fcPython = Join-Path $fcRoot 'FreeCAD_1.1.3-Windows-x86_64-py311/bin/python.exe'
$fcHash = '9c6959dc9c4dba64dd818a62447e3dfedb4221d776fb044b239d462f150bcec4'
$fcUrl = 'https://github.com/FreeCAD/FreeCAD/releases/download/1.1.3/FreeCAD_1.1.3-Windows-x86_64-py311.7z'
New-Item -ItemType Directory -Force $fcRoot | Out-Null
if (-not (Test-Path -LiteralPath $fcArchive)) {
    & curl.exe --fail --location --retry 3 $fcUrl --output $fcArchive
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo descargar FreeCAD' }
}
if ((Get-FileHash -LiteralPath $fcArchive -Algorithm SHA256).Hash -ne $fcHash) { throw 'El archivo no coincide con el SHA256 oficial' }
if (-not (Test-Path -LiteralPath $fcPython)) {
    & tar.exe -xf $fcArchive -C $fcRoot
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo extraer FreeCAD' }
}
& $fcPython -c 'import FreeCAD, Part, Sketcher; print("FreeCAD", FreeCAD.Version()[:3]); assert abs(Part.makeBox(10,20,3).Volume-600)<1e-6'
if ($LASTEXITCODE -ne 0) { throw 'FreeCAD no supero la prueba de su API nativa' }
@{ version='1.1.3'; source=$fcUrl; sha256=$fcHash; python=$fcPython; verified=$true } | ConvertTo-Json | Set-Content (Join-Path $fcBase 'freecad-provenance.json') -Encoding UTF8
Write-Output "Motor listo: $fcPython"
