# Levanta Stems Web en http://127.0.0.1:8765
# Usa el venv GPU del experimento Dia 1 (torch ROCm + demucs + fastapi).
$py = "$PSScriptRoot\.venv-gpu\Scripts\python.exe"
$app = $PSScriptRoot
Start-Process "http://127.0.0.1:8765"
& $py -m uvicorn app:app --app-dir $app --host 127.0.0.1 --port 8765
