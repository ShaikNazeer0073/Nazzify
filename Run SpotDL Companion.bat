@echo off
setlocal
cd /d "%~dp0"
set "PYW="
where pyw >nul 2>nul && set "PYW=pyw -3"
if not defined PYW ( where pythonw >nul 2>nul && set "PYW=pythonw" )
if not defined PYW (
  echo Python was not found on this computer.
  echo Install Python from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
start "" %PYW% "%~dp0spotdl_companion.py"
exit /b 0
