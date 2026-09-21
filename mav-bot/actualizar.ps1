# Baja la ultima version del bot encima de esta carpeta.
#
# PowerShell lee el script entero antes de correrlo, asi que puede
# sobrescribirse a si mismo sin romperse. El .bat no puede: por eso el .bat
# solo lanza esto y se va.

$ErrorActionPreference = 'Stop'
$zip  = 'https://github.com/novoalautaro2002-byte/analisis-tecnico/archive/refs/heads/claude/mav-cheques-bot-p6jluz.zip'
$aqui = $PSScriptRoot

Write-Host ''
Write-Host '  Actualizando el bot' -ForegroundColor Cyan
Write-Host "  $aqui"
Write-Host ''

try {
    Write-Host '  bajando...'
    Invoke-WebRequest $zip -OutFile "$env:TEMP\mav.zip"

    Write-Host '  descomprimiendo...'
    Remove-Item "$env:TEMP\mavx" -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive "$env:TEMP\mav.zip" "$env:TEMP\mavx" -Force

    Write-Host '  copiando...'
    # Solo los archivos del bot. No toca logs\ ni nada que hayas agregado vos.
    Copy-Item "$env:TEMP\mavx\*\mav-bot\*" $aqui -Recurse -Force

    Remove-Item "$env:TEMP\mav.zip" -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:TEMP\mavx" -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host ''
    Write-Host '  Listo. Ya podes abrir Mesa MAV.' -ForegroundColor Green
}
catch {
    Write-Host ''
    Write-Host "  No se pudo actualizar: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host '  Fijate si tenes internet. Tu version actual quedo intacta.'
}

Write-Host ''
Read-Host '  Enter para cerrar'
