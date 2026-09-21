@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Bot MAV
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo No encuentro Python en el PATH.
  echo Instalalo desde python.org marcando "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

python -c "import playwright" >nul 2>&1
if errorlevel 1 (
  echo Instalando Playwright, tarda un minuto...
  python -m pip install --quiet playwright
  if errorlevel 1 (
    echo.
    echo Fallo la instalacion. Proba a mano:  pip install playwright
    echo.
    pause
    exit /b 1
  )
  echo Listo.
  echo.
)

python ui.py

echo.
pause
