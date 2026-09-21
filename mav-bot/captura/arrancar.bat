@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Captura MAV - solo lectura
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

set "NAV="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "NAV=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined NAV if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "NAV=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined NAV if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "NAV=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined NAV if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "NAV=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined NAV if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "NAV=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if not defined NAV (
  echo No encuentro Chrome ni Edge en las rutas habituales.
  echo Abri el navegador a mano con --remote-debugging-port=9222 ^(ver README^).
  echo.
  pause
  exit /b 1
)

echo Abriendo el navegador con perfil aparte...
start "" "%NAV%" --remote-debugging-port=9222 --user-data-dir="%~dp0perfil-mav" "https://trading.mav-sa.com.ar/cgi-bin/wspd_cgi.sh/WService=wsbroker1/mvr-usuarios.r"

echo.
echo   1. Logueate en la ventana que se abrio ^(usuario + 2FA^)
echo   2. Anda a la pantalla de subasta
echo   3. Volve aca y apreta una tecla
echo.
pause

echo.
echo Capturando. Segui operando normal; toma todas las pantallas que abras.
echo Ctrl+C aca cuando termines.
echo.
python capturar.py

echo.
pause
