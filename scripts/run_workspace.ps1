param([string]$Model='models/qwen3.5-2b-q8_0.gguf',[string]$Accelerator='whpx')
$ErrorActionPreference='Stop'
$workspaceRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$workspacePython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $workspacePython)) { $workspacePython=(Get-Command python -ErrorAction Stop).Source }
$workspaceBash=Join-Path $env:ProgramFiles 'Git/bin/bash.exe'
Push-Location -LiteralPath $workspaceRoot
try {
    & $workspaceBash -lc 'source scripts/env.sh && make all'
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar ErlanOS; se conserva el disco anterior.' }
    & $workspacePython scripts/run_workspace.py --model $Model --accel $Accelerator
    if ($LASTEXITCODE -ne 0) { throw 'La sesion termino con un error; revisa su result.json.' }
} finally { Pop-Location }
