@echo off
setlocal
cd /d "%~dp0"
title Acceso directo
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$s=(New-Object -ComObject WScript.Shell);" ^
 "$l=$s.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Mesa MAV.lnk');" ^
 "$l.TargetPath='%~dp0MAV.bat';" ^
 "$l.WorkingDirectory='%~dp0';" ^
 "$l.IconLocation='%SystemRoot%\System32\shell32.dll,137';" ^
 "$l.Description='Bot de cotizacion MAV';" ^
 "$l.Save();" ^
 "Write-Host 'Listo: ya tenes Mesa MAV en el Escritorio.' -ForegroundColor Green"
echo.
pause
