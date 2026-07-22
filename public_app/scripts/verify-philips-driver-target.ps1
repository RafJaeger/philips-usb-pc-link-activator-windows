[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-PnpServiceName {
    param([Parameter(Mandatory = $true)][string]$InstanceId)

    $property = Get-PnpDeviceProperty -InstanceId $InstanceId -ErrorAction SilentlyContinue |
        Where-Object { $_.KeyName -eq 'DEVPKEY_Device_Service' } |
        Select-Object -First 1

    if ($null -eq $property) {
        return ''
    }

    return [string]$property.Data
}

$devices = @(Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object {
        $_.InstanceId -like 'USB\VID_0471&PID_0111*' -or
        $_.FriendlyName -like '*Philips Audio Set*'
    } |
    Sort-Object InstanceId)

if ($devices.Count -eq 0) {
    Write-Host 'No Philips USB PC Link device with VID_0471&PID_0111 is visible.'
    exit 1
}

$rows = foreach ($device in $devices) {
    [pscustomobject]@{
        Status = $device.Status
        Class = $device.Class
        FriendlyName = $device.FriendlyName
        Service = if ($device.InstanceId -like 'USB\*') { Get-PnpServiceName -InstanceId $device.InstanceId } else { '' }
        InstanceId = $device.InstanceId
    }
}

$rows | Format-Table -AutoSize

$mi00 = @($devices | Where-Object { $_.InstanceId -like 'USB\VID_0471&PID_0111&MI_00\*' })
$mi03 = @($devices | Where-Object { $_.InstanceId -like 'USB\VID_0471&PID_0111&MI_03\*' })

if ($mi00.Count -eq 0) {
    Write-Warning 'MI_00 audio interface is not visible.'
} else {
    foreach ($node in $mi00) {
        $service = Get-PnpServiceName -InstanceId $node.InstanceId
        if ($service -eq 'WinUSB') {
            Write-Error "MI_00 is incorrectly using WinUSB: $($node.InstanceId)"
        } else {
            Write-Host "MI_00 OK: service=$service"
        }
    }
}

if ($mi03.Count -eq 0) {
    Write-Warning 'MI_03 control interface is not visible.'
} else {
    foreach ($node in $mi03) {
        $service = Get-PnpServiceName -InstanceId $node.InstanceId
        if ($service -eq 'WinUSB') {
            Write-Host "MI_03 OK for PC Link activator: service=$service"
        } else {
            Write-Warning "MI_03 is not using WinUSB yet: service=$service"
        }
    }
}
