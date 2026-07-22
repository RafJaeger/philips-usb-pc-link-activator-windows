[CmdletBinding(SupportsShouldProcess = $true)]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TargetInstancePrefix = 'USB\VID_0471&PID_0111&MI_03\'
$AudioInstancePrefix = 'USB\VID_0471&PID_0111&MI_00\'
$PnpUtil = Join-Path $env:WINDIR 'System32\pnputil.exe'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PnpServiceName {
    param([Parameter(Mandatory = $true)][string]$InstanceId)

    $property = Get-PnpDeviceProperty -InstanceId $InstanceId -ErrorAction SilentlyContinue |
        Where-Object { $_.KeyName -eq 'DEVPKEY_Device_Service' } |
        Select-Object -First 1

    if ($null -eq $property) {
        return $null
    }

    return [string]$property.Data
}

if (-not (Test-IsAdministrator)) {
    throw 'Run this script from an elevated PowerShell window.'
}

$devices = @(Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\VID_0471&PID_0111*' })

$audio = @($devices | Where-Object { $_.InstanceId -like $AudioInstancePrefix })
foreach ($node in $audio) {
    Write-Host "Audio interface left untouched: $($node.InstanceId) service=$(Get-PnpServiceName -InstanceId $node.InstanceId)"
}

$targets = @($devices | Where-Object { $_.InstanceId -like "$TargetInstancePrefix*" })
if ($targets.Count -eq 0) {
    Write-Warning 'No Philips MI_03 control interface is currently connected. Nothing to restore.'
    exit 0
}

foreach ($target in $targets) {
    $service = Get-PnpServiceName -InstanceId $target.InstanceId
    Write-Host "MI_03 candidate: $($target.InstanceId) service=$service"

    if ($service -ne 'WinUSB') {
        Write-Host 'MI_03 is not using WinUSB; no driver package removal needed for this node.'
        continue
    }

    $signedDriver = Get-CimInstance Win32_PnPSignedDriver |
        Where-Object { $_.DeviceID -eq $target.InstanceId } |
        Select-Object -First 1

    if ($null -eq $signedDriver -or [string]::IsNullOrWhiteSpace($signedDriver.InfName)) {
        throw "Could not identify published INF for $($target.InstanceId)."
    }

    $infName = [string]$signedDriver.InfName
    if ($infName -notmatch '^oem\d+\.inf$') {
        throw "Refusing to delete non-OEM INF '$infName'. Use Device Manager manually if this is expected."
    }

    if ($PSCmdlet.ShouldProcess($target.InstanceId, "Remove WinUSB package $infName from MI_03")) {
        & $PnpUtil /delete-driver $infName /uninstall /force
        if ($LASTEXITCODE -ne 0) {
            throw "pnputil /delete-driver $infName failed with exit code $LASTEXITCODE."
        }
    }
}

if ($PSCmdlet.ShouldProcess('Plug and Play device tree', 'Scan devices')) {
    & $PnpUtil /scan-devices
    if ($LASTEXITCODE -ne 0) {
        throw "pnputil /scan-devices failed with exit code $LASTEXITCODE."
    }
}

$devices = @(Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\VID_0471&PID_0111*' })
$targets = @($devices | Where-Object { $_.InstanceId -like "$TargetInstancePrefix*" })

foreach ($target in $targets) {
    Write-Host "Post-check MI_03: $($target.InstanceId) service=$(Get-PnpServiceName -InstanceId $target.InstanceId)"
}

Write-Host 'Restore completed. If MI_03 still shows WinUSB, unplug/replug the USB cable and run verify-philips-driver-target.ps1.'
