@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\flash_8bitdo.ps1" -Action Restaurer
set "result=%errorlevel%"
pause
exit /b %result%
