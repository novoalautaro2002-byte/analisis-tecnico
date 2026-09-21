@echo off
setlocal
cd /d "%~dp0"
title Practica MAV (simulacro)
echo ============================================
echo   Practica: MAV de mentira
echo ============================================
echo.
echo Esto NO toca la plataforma real. Es para probar
echo el bot contra una subasta inventada.
echo.
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY where python >nul 2>&1 && set PY=python
if not defined PY (
  echo.
  echo No encuentro Python.
  echo Instalalo desde python.org y marca "Add Python to PATH".
  echo.
  pause
  exit /b 1
)
%PY% simulacro.py
echo.
pause
