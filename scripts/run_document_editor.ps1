param([string]$Model = 'models/qwen3.5-2b-q8_0.gguf', [string]$Accelerator = 'whpx')
$ErrorActionPreference = 'Stop'
$documentRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$documentPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $documentPython)) { $documentPython = (Get-Command python -ErrorAction Stop).Source }
$documentBash = Join-Path $env:ProgramFiles 'Git/bin/bash.exe'
Push-Location -LiteralPath $documentRoot
try {
    & $documentBash -lc 'source scripts/env.sh && make all'
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar ErlanOS.' }
    & $documentPython scripts/run_document_editor.py --accel $Accelerator --model $Model
    if ($LASTEXITCODE -ne 0) { throw 'La sesion de Documentos termino con un error; revisa su result.json.' }
} finally { Pop-Location }
