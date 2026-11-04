# Reproducible setup for the Day 1 experiment — from scratch.
# Requires ffmpeg/ffprobe on PATH. Does not touch the system Python.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

# 1. uv (per-user installer, goes to ~\.local\bin)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Test-Path "$env:USERPROFILE\.local\bin\uv.exe") {
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    } else {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    }
}

# 2. Isolated Python 3.12 venv (uv downloads the interpreter if needed)
if (-not (Test-Path "$root\.venv")) {
    uv venv --python 3.12 "$root\.venv"
}

# 3. demucs + torch (CPU build from PyPI — guaranteed baseline)
$env:VIRTUAL_ENV = "$root\.venv"
uv pip install demucs soundfile

# 4. verification
& "$root\.venv\Scripts\python.exe" "$root\scripts\check_env.py"

Write-Host "`nSetup OK. Try: .venv\Scripts\python.exe scripts\separate.py input\test-cc-nightowl.mp3" -ForegroundColor Green
