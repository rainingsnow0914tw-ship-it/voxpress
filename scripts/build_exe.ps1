# Build single-file VoxPress.exe via PyInstaller.
# Usage:
#   pip install voxpress[dev]
#   .\scripts\build_exe.ps1

Set-Location $PSScriptRoot\..

$ErrorActionPreference = "Stop"

Write-Host "[build] cleaning dist/ and build/" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "[build] running PyInstaller" -ForegroundColor Cyan
pyinstaller `
    --name voxpress `
    --onefile `
    --windowed `
    --icon "src/voxpress/voxpress.ico" `
    --collect-all faster_whisper `
    --collect-submodules ctranslate2 `
    --hidden-import sounddevice `
    --hidden-import keyboard `
    --hidden-import pystray._win32 `
    src/voxpress/__main__.py

if (Test-Path "dist\voxpress.exe") {
    $size = (Get-Item "dist\voxpress.exe").Length / 1MB
    Write-Host "[build] OK -> dist\voxpress.exe ($([Math]::Round($size, 1)) MB)" -ForegroundColor Green
} else {
    Write-Host "[build] FAIL — no exe produced" -ForegroundColor Red
    exit 1
}
