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
