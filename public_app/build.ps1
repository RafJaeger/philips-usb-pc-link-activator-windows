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

# Version resource (name, company, description) shown in the file properties.
$version = (Get-Content -Raw (Join-Path $AppRoot 'VERSION')).Trim()
$parts = @($version.Split('.') | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$tuple = ($parts[0..3] -join ', ')
$versionFile = Join-Path $AppRoot 'build\version_info.txt'
New-Item -ItemType Directory -Force -Path (Split-Path $versionFile) | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004,
    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable(u'041604B0', [
      StringStruct(u'CompanyName', u'RafJaeger'),
      StringStruct(u'FileDescription', u'Philips USB PC Link Activator for Windows'),
      StringStruct(u'FileVersion', u'$version'),
      StringStruct(u'InternalName', u'Philips_PC_Link_Activator'),
      StringStruct(u'LegalCopyright', u'MIT License - github.com/RafJaeger/philips-usb-pc-link-activator-windows'),
      StringStruct(u'OriginalFilename', u'Philips_PC_Link_Activator.exe'),
      StringStruct(u'ProductName', u'Philips USB PC Link Activator'),
      StringStruct(u'ProductVersion', u'$version')])]),
    VarFileInfo([VarStruct(u'Translation', [1046, 1200])])
  ]
)
"@ | Set-Content -Encoding UTF8 $versionFile

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
    --version-file $versionFile `
    --add-data "assets;assets" `
    --add-data "driver\winusb-philips-mi03;driver\winusb-philips-mi03" `
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
