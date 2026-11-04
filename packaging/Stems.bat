@echo off
title Stems
cd /d "%~dp0"

rem %~dp0 ends in '\', and "...\" makes cmd swallow the closing quote and with it
rem the arguments that follow (--host/--port ended up inside --app-dir and uvicorn died).
rem HERE is the same path without the trailing backslash.
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

set "PATH=%HERE%\ffmpeg;%PATH%"

rem Start the server. No /min: if it crashes, the window stays visible to show the error.
start "Stems server" "%HERE%\python\python.exe" -m uvicorn app:app --app-dir "%HERE%" --host 127.0.0.1 --port 8765

rem Wait for the server to respond (max 30s) and open the browser
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{(New-Object Net.Sockets.TcpClient('127.0.0.1',8765)).Close();exit 0}catch{Start-Sleep -m 500}};exit 1"
start "" "http://127.0.0.1:8765"
