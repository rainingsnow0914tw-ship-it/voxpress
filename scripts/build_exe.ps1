# Build a single-file VoxPress executable and SHA-256 checksum.

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$buildPath = Join-Path $repoRoot "build"
$distPath = Join-Path $repoRoot "dist"

foreach ($target in @($buildPath)) {
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $target))
    if ($parent -ne $repoRoot) {
        throw "Refusing cleanup outside repository root: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $distPath -Force | Out-Null
foreach ($artifact in @(
    (Join-Path $distPath "voxpress.exe"),
    (Join-Path $distPath "voxpress.exe.sha256")
)) {
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $artifact))
    if ($parent -ne $distPath) {
        throw "Refusing cleanup outside dist: $artifact"
    }
    if (Test-Path -LiteralPath $artifact) {
        Remove-Item -LiteralPath $artifact -Force
    }
}

Push-Location -LiteralPath $repoRoot
try {
    python -m PyInstaller `
        --name voxpress `
        --onefile `
        --windowed `
        --noconfirm `
        --clean `
        --workpath $buildPath `
        --specpath $buildPath `
        --distpath $distPath `
        --icon "src/voxpress/voxpress.ico" `
        --collect-all faster_whisper `
        --collect-submodules ctranslate2 `
        --hidden-import sounddevice `
        --hidden-import keyboard `
        --hidden-import pystray._win32 `
        src/voxpress/__main__.py

    $exe = Join-Path $distPath "voxpress.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "PyInstaller completed without dist\voxpress.exe"
    }
    $hash = Get-FileHash -LiteralPath $exe -Algorithm SHA256
    $checksumPath = Join-Path $distPath "voxpress.exe.sha256"
    "{0}  voxpress.exe" -f $hash.Hash.ToLowerInvariant() |
        Set-Content -LiteralPath $checksumPath -Encoding ascii
    Write-Host "Built $exe"
    Write-Host "Checksum $checksumPath"
}
finally {
    Pop-Location
}
