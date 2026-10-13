@echo off
setlocal
set "ROOT=D:\pr\07"
set "FRONTEND=%ROOT%\frontend"

if not exist "%FRONTEND%\.next\standalone\server.js" (
  cd /d "%FRONTEND%"
  call npm.cmd run build
)

if not exist "%FRONTEND%\.next\standalone\.next\static" (
  xcopy "%FRONTEND%\.next\static" "%FRONTEND%\.next\standalone\.next\static" /E /I /Y >nul
)

if not exist "%FRONTEND%\.next\standalone\public" (
  xcopy "%FRONTEND%\public" "%FRONTEND%\.next\standalone\public" /E /I /Y >nul
)

set "PORT=4000"
set "HOSTNAME=127.0.0.1"
cd /d "%FRONTEND%\.next\standalone"
"C:\Program Files\nodejs\node.exe" server.js
