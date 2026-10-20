@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\flash_8bitdo.ps1" -Action Verifier
set "result=%errorlevel%"
pause
exit /b %result%
