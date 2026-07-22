[CmdletBinding()]
param(
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppRoot

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '.\build', '.\dist'
}

python -m pip install -r .\requirements.txt

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --noupx `
    --name Philips_PC_Link_Activator `
    --icon "assets\app.ico" `
    --add-data "assets;assets" `
    --add-data "driver\winusb-philips-mi03;driver\winusb-philips-mi03" `
    --add-data "scripts;scripts" `
    --add-data "docs;docs" `
    --add-data "AUTHORS.md;." `
    --add-data "LICENSE;." `
    --add-data "VERSION;." `
    --add-data "THIRD_PARTY_NOTICES.md;." `
    app.py

Write-Host "Build ready: $AppRoot\dist\Philips_PC_Link_Activator"
