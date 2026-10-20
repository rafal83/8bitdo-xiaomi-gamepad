@echo off
chcp 65001 >nul
setlocal
set "XIAOMI_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "XIAOMI_PYTHON=%~dp0.venv\Scripts\python.exe"
"%XIAOMI_PYTHON%" "%~dp0scripts\configure_xiaomi.py"
if errorlevel 1 (
  echo Le configurateur requiert Python 3 avec Tkinter et les dependances du projet.
  pause
  exit /b 1
)
