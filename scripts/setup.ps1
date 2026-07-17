# Setup reproducible del experimento Día 1 — desde cero.
# Requiere: ffmpeg/ffprobe en PATH. No toca el Python del sistema.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

# 1. uv (instalador per-user, va a ~\.local\bin)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Test-Path "$env:USERPROFILE\.local\bin\uv.exe") {
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    } else {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    }
}

# 2. venv Python 3.12 aislado (uv descarga el intérprete si hace falta)
if (-not (Test-Path "$root\.venv")) {
    uv venv --python 3.12 "$root\.venv"
}

# 3. demucs + torch (build CPU de PyPI — baseline garantizado)
$env:VIRTUAL_ENV = "$root\.venv"
uv pip install demucs soundfile

# 4. verificación
& "$root\.venv\Scripts\python.exe" "$root\scripts\check_env.py"

Write-Host "`nSetup OK. Probar: .venv\Scripts\python.exe scripts\separate.py input\test-cc-nightowl.mp3" -ForegroundColor Green
