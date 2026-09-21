@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Anonimizar capturas antes de compartir
echo ============================================
echo.

if not exist "capturas" (
  echo Todavia no hay ninguna captura. Corre arrancar.bat primero.
  echo.
  pause
  exit /b 1
)

REM La carpeta mas reciente, salteando las que ya limpiamos antes.
set "ULTIMA="
for /f "delims=" %%D in ('dir /b /ad /o-d "capturas" 2^>nul') do (
  set "N=%%D"
  if not defined ULTIMA if "!N:_limpio=!"=="!N!" set "ULTIMA=%%D"
)

if not defined ULTIMA (
  echo Todavia no hay ninguna captura. Corre arrancar.bat primero.
  echo.
  pause
  exit /b 1
)

echo Carpeta: capturas\!ULTIMA!
echo.

if exist "terminos.txt" (
  python scrub.py "capturas\!ULTIMA!" --terminos terminos.txt
) else (
  echo OJO: no hay terminos.txt.
  echo Solo voy a tapar CUITs y tokens, NO los nombres de comitente.
  echo.
  echo Si en las pantallas aparecen nombres de clientes: crea un terminos.txt
  echo aca al lado, uno por linea, y volve a correr esto.
  echo.
  python scrub.py "capturas\!ULTIMA!"
)

echo.
echo Abri un par de archivos de la carpeta _limpio y verifica antes de mandarlos.
echo NO compartas MAPEO_NO_COMPARTIR.json.
echo.
pause
