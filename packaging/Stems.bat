@echo off
title Stems
cd /d "%~dp0"

rem %~dp0 termina en '\', y "...\" hace que cmd se coma la comilla de cierre y con ella
rem los argumentos que siguen (--host/--port se metian dentro de --app-dir, uvicorn moria).
rem HERE es la misma ruta sin la barra final.
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

set "PATH=%HERE%\ffmpeg;%PATH%"

rem Arranca el server. Sin /min: si crashea, la ventana queda visible para ver el error.
start "Stems server" "%HERE%\python\python.exe" -m uvicorn app:app --app-dir "%HERE%" --host 127.0.0.1 --port 8765

rem Espera a que el server responda (max 30s) y abre el navegador
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{(New-Object Net.Sockets.TcpClient('127.0.0.1',8765)).Close();exit 0}catch{Start-Sleep -m 500}};exit 1"
start "" "http://127.0.0.1:8765"
