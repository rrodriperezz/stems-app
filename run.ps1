# Starts Stems Web at http://127.0.0.1:8765
# Uses the GPU venv from the Day 1 experiment (ROCm torch + demucs + fastapi).
$py = "$PSScriptRoot\.venv-gpu\Scripts\python.exe"
$app = $PSScriptRoot
Start-Process "http://127.0.0.1:8765"
& $py -m uvicorn app:app --app-dir $app --host 127.0.0.1 --port 8765
