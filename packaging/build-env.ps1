# Capa pesada del paquete portable: Python embebido + torch CUDA + deps.
# Corre una sola vez; los archivos de la app se copian despues (capa liviana).
$ErrorActionPreference = "Stop"
$root = "C:\Users\Usuario\Desktop\stems-app-investigacion"
$pack = "$root\dist\stems-pack"
$tmp  = "$env:TEMP\stems-build"
New-Item -ItemType Directory -Force $pack | Out-Null
New-Item -ItemType Directory -Force $tmp | Out-Null

Write-Output "[1/6] Descargando Python 3.12.10 embeddable (~11 MB, python.org)..."
curl.exe -sSL -o "$tmp\py-embed.zip" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
if (Test-Path "$pack\python") { Remove-Item -Recurse -Force "$pack\python" }
Expand-Archive "$tmp\py-embed.zip" "$pack\python" -Force

Write-Output "[2/6] Habilitando site-packages en ._pth..."
$pth = "$pack\python\python312._pth"
(Get-Content $pth) -replace '#import site', 'import site' | Set-Content $pth -Encoding ascii

Write-Output "[3/6] Bootstrapping pip (get-pip.py, bootstrap.pypa.io)..."
curl.exe -sSL -o "$tmp\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
& "$pack\python\python.exe" "$tmp\get-pip.py" --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip fallo" }

Write-Output "[4/6] Instalando torch CUDA cu126 (~3.3 GB, download.pytorch.org)..."
& "$pack\python\python.exe" -m pip install torch torchaudio --index-url "https://download.pytorch.org/whl/cu126" --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "pip torch fallo" }

Write-Output "[5/6] Instalando audio-separator + demucs + fastapi + resto (PyPI)..."
& "$pack\python\python.exe" -m pip install "audio-separator==0.44.2" "demucs==4.0.1" fastapi "uvicorn[standard]" python-multipart soundfile yt-dlp --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "pip deps fallo" }

Write-Output "[6/6] Smoke test de imports..."
& "$pack\python\python.exe" -c "import torch, fastapi, soundfile, yt_dlp; from audio_separator.separator import Separator; import demucs.pretrained; print('ENV OK - torch', torch.__version__, '- cuda build:', torch.version.cuda)"
if ($LASTEXITCODE -ne 0) { throw "smoke test fallo" }

Write-Output "LISTO: capa de entorno completa en $pack\python"
