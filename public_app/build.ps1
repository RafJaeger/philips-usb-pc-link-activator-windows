[CmdletBinding()]
param(
    [switch]$Clean,
    # Single portable .exe (no installer, no _internal folder).
    [switch]$OneFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppRoot

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '.\build', '.\dist'
}

# Equalizer APO installer (GPL-2.0), bundled unmodified and checked by hash.
$EapoDir = Join-Path $AppRoot 'vendor\equalizerapo'
$EapoFile = Join-Path $EapoDir 'EqualizerAPO-x64-1.4.2.exe'
$EapoSha256 = '7403be7427bbe1936a40dded082829b6e217fc4f5990fee5cba501f0ae055afa'
if (-not (Test-Path $EapoFile)) {
    New-Item -ItemType Directory -Force -Path $EapoDir | Out-Null
    Write-Host 'Baixando o Equalizer APO 1.4.2 (SourceForge)...'
    Invoke-WebRequest -UseBasicParsing -UserAgent 'Wget' -OutFile $EapoFile `
        -Uri 'https://sourceforge.net/projects/equalizerapo/files/1.4.2/EqualizerAPO-x64-1.4.2.exe/download'
}
$hash = (Get-FileHash -Algorithm SHA256 $EapoFile).Hash.ToLowerInvariant()
if ($hash -ne $EapoSha256) {
    Remove-Item -Force $EapoFile
    throw "Equalizer APO installer hash mismatch ($hash)."
}

python -m pip install -r .\requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed (exit code $LASTEXITCODE)."
}

$name = 'Philips_PC_Link_Activator'
$mode = '--onedir'
if ($OneFile) {
    $name = 'Philips_PC_Link_Activator_Portable'
    $mode = '--onefile'
}

python -m PyInstaller `
    $mode `
    --noconfirm `
    --clean `
    --windowed `
    --noupx `
    --name $name `
    --icon "assets\app.ico" `
    --add-data "assets;assets" `
    --add-data "driver\winusb-philips-mi03;driver\winusb-philips-mi03" `
    --add-data "scripts;scripts" `
    --add-data "docs;docs" `
    --add-data "AUTHORS.md;." `
    --add-data "LICENSE;." `
    --add-data "VERSION;." `
    --add-data "THIRD_PARTY_NOTICES.md;." `
    --add-data "vendor\equalizerapo;vendor\equalizerapo" `
    --collect-submodules winrt `
    app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed (exit code $LASTEXITCODE). If dist is locked, close the running app first."
}

if ($OneFile) {
    Write-Host "Build ready: $AppRoot\dist\$name.exe"
} else {
    Write-Host "Build ready: $AppRoot\dist\$name"
}
