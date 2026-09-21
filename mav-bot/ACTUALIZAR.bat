@echo off
cd /d "%~dp0"
start "Actualizar MAV" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0actualizar.ps1"
exit
