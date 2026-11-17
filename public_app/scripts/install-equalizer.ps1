<#
Installs Equalizer APO (bundled, GPL-2.0) and enables it ONLY on the
"Philips Audio Set" output, the same way Equalizer APO's Device Selector does
it, without its window. -Remove restores the Philips output.

Device Selector logic reproduced (DeviceAPOInfo.cpp, Equalizer APO 1.4.2):
  * the originals of LFX/GFX/SFX/MFX/EFX are saved under
    HKLM\SOFTWARE\EqualizerAPO\Child APOs\{endpoint} ("!VALUE" when absent),
    so Equalizer APO's own uninstaller can restore them;
  * install mode, as Device Selector picks it on Windows 8.1+:
      LFX/GFX  when the output only has LFX/GFX effects,
      SFX/MFX  when the output has the "combined device" property,
      SFX/EFX  otherwise;
    the original effects are kept as child APOs;
  * when Windows denies writing to FxProperties, ownership is taken, the
    values are written and the original owner and permissions are restored.
If anything still fails, Equalizer APO's Device Selector is opened so the
user can tick "Philips Audio Set" by hand.
Must run elevated.
#>
[CmdletBinding()]
param(
    [string]$Installer,
    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RenderSub = 'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
$RenderBase = "HKLM:\$RenderSub"
$EapoKey = 'HKLM:\SOFTWARE\EqualizerAPO'
$ChildBase = "$EapoKey\Child APOs"
$FxGuid = '{d04e05a6-594b-4fb6-a80d-01af5eed7d1d}'
$LFX = "$FxGuid,1"; $GFX = "$FxGuid,2"; $SFX = "$FxGuid,5"; $MFX = "$FxGuid,6"; $EFX = "$FxGuid,7"
$MultiFx = @("$FxGuid,13", "$FxGuid,14", "$FxGuid,15")
$FxNames = @($LFX, $GFX, $SFX, $MFX, $EFX)
$FriendlyName = '{b725f130-47ef-101a-a5f1-02608c9eebac},10'
$ModeKey = '{d3993a3f-99c2-4402-b5ec-a92a0367664b}'
$ModeSfx = "$ModeKey,5"; $ModeMfx = "$ModeKey,6"; $ModeEfx = "$ModeKey,7"
$DefaultMode = '{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}'
$DisableSysFx = '{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5'
$DeviceNameKey = '{b3f8fa53-0004-438e-9003-51a46e139bfc},6'
$CombinedKey = '{b3f8fa53-0004-438e-9003-51a46e139bfc},41'
$Extra = @($FriendlyName, $ModeSfx, $ModeMfx, $ModeEfx, $DisableSysFx)   # restored by -Remove
$IncludeLine = 'Include: philips-pc-link.txt'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PhilipsTokenPrivilege {
    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    struct TOKEN_PRIVILEGES { public int Count; public long Luid; public int Attributes; }
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool LookupPrivilegeValue(string system, string name, out long luid);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAll, ref TOKEN_PRIVILEGES state,
        int length, IntPtr previous, IntPtr returnLength);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    public static bool Enable(string name) {
        IntPtr token;
        if (!OpenProcessToken(GetCurrentProcess(), 0x28, out token)) return false;  // ADJUST | QUERY
        try {
            TOKEN_PRIVILEGES state = new TOKEN_PRIVILEGES();
            state.Count = 1; state.Attributes = 2;  // SE_PRIVILEGE_ENABLED
            if (!LookupPrivilegeValue(null, name, out state.Luid)) return false;
            if (!AdjustTokenPrivileges(token, false, ref state, 0, IntPtr.Zero, IntPtr.Zero)) return false;
            return Marshal.GetLastWin32Error() == 0;
        } finally { CloseHandle(token); }
    }
}
'@

function Get-Prop($path, $name) {
    $item = Get-ItemProperty -LiteralPath $path -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    $prop = $item.PSObject.Properties[$name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Get-Hklm {
    [Microsoft.Win32.RegistryKey]::OpenBaseKey(
        [Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
}

function Open-RegKey([string]$sub, [string]$rights) {
    $key = (Get-Hklm).OpenSubKey($sub, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree,
        [System.Security.AccessControl.RegistryRights]$rights)
    if ($null -eq $key) { throw "Chave do registro nao encontrada: HKLM\$sub" }
    return $key
}

# ---- FxProperties writes, with ownership taken only when Windows denies them ----
$script:SavedSecurity = @{}

function Grant-FxWrite([string]$guid) {
    if ($script:SavedSecurity.ContainsKey($guid)) { return }
    $sub = "$RenderSub\$guid\FxProperties"
    foreach ($privilege in 'SeTakeOwnershipPrivilege', 'SeRestorePrivilege', 'SeBackupPrivilege') {
        [void][PhilipsTokenPrivilege]::Enable($privilege)
    }
    $sections = [System.Security.AccessControl.AccessControlSections]'Owner, Access'
    $key = Open-RegKey $sub 'ReadPermissions, TakeOwnership'
    try {
        $acl = $key.GetAccessControl($sections)
        $script:SavedSecurity[$guid] = $acl.GetSecurityDescriptorSddlForm($sections)
        $admins = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
        $acl.SetOwner($admins)
        $key.SetAccessControl($acl)
    } finally { $key.Close() }
    $key = Open-RegKey $sub 'ReadPermissions, ChangePermissions'
    try {
        $acl = $key.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
        $rule = New-Object System.Security.AccessControl.RegistryAccessRule($admins, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
        $key.SetAccessControl($acl)
    } finally { $key.Close() }
    Write-Output "Permissao de escrita obtida em FxProperties da saida $guid (sera restaurada)."
}

function Restore-FxSecurity {
    foreach ($guid in @($script:SavedSecurity.Keys)) {
        $sub = "$RenderSub\$guid\FxProperties"
        try {
            $key = Open-RegKey $sub 'ReadPermissions, ChangePermissions, TakeOwnership'
            try {
                $acl = New-Object System.Security.AccessControl.RegistrySecurity
                $acl.SetSecurityDescriptorSddlForm($script:SavedSecurity[$guid],
                    [System.Security.AccessControl.AccessControlSections]'Owner, Access')
                $key.SetAccessControl($acl)
            } finally { $key.Close() }
            Write-Output "Dono e permissoes originais de FxProperties restaurados ($guid)."
        } catch {
            Write-Output "Aviso: nao foi possivel restaurar as permissoes de $guid ($($_.Exception.Message))."
        }
        $script:SavedSecurity.Remove($guid)
    }
}

function Invoke-FxWrite([string]$guid, [scriptblock]$action) {
    try {
        $key = Open-RegKey "$RenderSub\$guid\FxProperties" 'ReadKey, SetValue'
    } catch [System.Security.SecurityException], [System.UnauthorizedAccessException] {
        Grant-FxWrite $guid
        $key = Open-RegKey "$RenderSub\$guid\FxProperties" 'ReadKey, SetValue'
    }
    try { & $action $key } finally { $key.Close() }
}

function Set-Fx([string]$guid, [string]$name, $value, [string]$kind = 'String') {
    if ($kind -eq 'MultiString') { $value = [string[]]@($value) } else { $value = [string]$value }
    Invoke-FxWrite $guid { param($key) $key.SetValue($name, $value, [Microsoft.Win32.RegistryValueKind]::$kind) }
}

function Remove-Fx([string]$guid, [string]$name) {
    Invoke-FxWrite $guid { param($key) $key.DeleteValue($name, $false) }
}

function Get-PhilipsEndpoints {
    @(Get-ChildItem -LiteralPath $RenderBase | Where-Object {
        $name = Get-Prop "$($_.PSPath)\Properties" $DeviceNameKey
        $name -and ($name -like '*Philips Audio Set*')
    })
}

function Test-EapoOnEndpoint($endpoint) {
    $fx = "$($endpoint.PSPath)\FxProperties"
    ((Get-Prop $fx $FriendlyName) -eq 'Equalizer APO') -and (Test-Path -LiteralPath "$ChildBase\$($endpoint.PSChildName)")
}

function Restart-Audio {
    try {
        Restart-Service -Name Audiosrv -Force -ErrorAction Stop
        Write-Output 'Servico de audio do Windows reiniciado.'
    } catch {
        Write-Output "Nao foi possivel reiniciar o servico de audio ($($_.Exception.Message)). Reinicie o Windows."
    }
}

if ($Remove) {
    $count = 0
    try {
        foreach ($endpoint in Get-PhilipsEndpoints) {
            $guid = $endpoint.PSChildName
            $child = "$ChildBase\$guid"
            if (-not (Test-Path -LiteralPath $child)) { continue }
            foreach ($name in $FxNames + $Extra) {
                $saved = if ($Extra -contains $name) { Get-Prop $child "PhilipsPcLink:$name" } else { Get-Prop $child $name }
                if ($null -eq $saved) { continue }
                if ($saved -is [string] -and ($saved -eq '!VALUE' -or $saved -eq '!KEY')) {
                    Remove-Fx $guid $name
                } elseif ($saved -is [string[]]) {
                    Set-Fx $guid $name $saved 'MultiString'
                } else {
                    Set-Fx $guid $name ([string]$saved)
                }
            }
            Remove-Item -LiteralPath $child -Recurse -Force
            $count++
            Write-Output "Saida Philips $guid restaurada."
        }
    } finally { Restore-FxSecurity }
    if ($count -eq 0) { Write-Output 'Nenhuma saida Philips com o equalizador.' } else { Restart-Audio }
    exit 0
}

# ---------- 1. Equalizer APO itself ----------
$installPath = Get-Prop $EapoKey 'InstallPath'
if (-not $installPath -or -not (Test-Path -LiteralPath (Join-Path $installPath 'EqualizerAPO.dll'))) {
    if (-not $Installer -or -not (Test-Path -LiteralPath $Installer)) {
        throw "Instalador do Equalizer APO nao encontrado: $Installer"
    }
    Write-Output 'Instalando o Equalizer APO (silencioso)...'
    $process = Start-Process -FilePath $Installer -ArgumentList '/S' -PassThru
    $deadline = (Get-Date).AddMinutes(4)
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        # The installer opens Device Selector (a window). This script selects
        # the Philips output itself, so that window is closed.
        Get-Process -Name DeviceSelector -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
    if (-not $process.HasExited) { throw 'O instalador do Equalizer APO nao terminou a tempo.' }
    Get-Process -Name DeviceSelector -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $installPath = Get-Prop $EapoKey 'InstallPath'
    if (-not $installPath -or -not (Test-Path -LiteralPath (Join-Path $installPath 'EqualizerAPO.dll'))) {
        throw 'O Equalizer APO nao foi instalado.'
    }
    Write-Output "Equalizer APO instalado em $installPath."
} else {
    Write-Output "Equalizer APO ja instalado em $installPath."
}

# ---------- 2. Its APO class ids ----------
$pre = $null; $post = $null
$apoRoot = 'HKLM:\SOFTWARE\Classes\AudioEngine\AudioProcessingObjects'
foreach ($apo in Get-ChildItem -LiteralPath $apoRoot -ErrorAction SilentlyContinue) {
    if ((Get-Prop $apo.PSPath 'FriendlyName') -ne 'EqualizerAPO') { continue }
    $id = $apo.PSChildName
    $className = Get-Prop "HKLM:\SOFTWARE\Classes\CLSID\$id" '(default)'
    if ($className -like '*Pre-Mix*') { $pre = $id }
    elseif ($className -like '*Post-Mix*') { $post = $id }
}
if (-not $pre -or -not $post) { throw 'Classes do Equalizer APO nao registradas (reinstale).' }

# ---------- 3. Enable it on every Philips Audio Set output ----------
$endpoints = Get-PhilipsEndpoints
if ($endpoints.Count -eq 0) { throw 'Saida Philips Audio Set nao encontrada no Windows. Conecte o radio.' }
$changed = 0
$failed = @()
foreach ($endpoint in $endpoints) {
    $guid = $endpoint.PSChildName
    $fx = "$($endpoint.PSPath)\FxProperties"
    $child = "$ChildBase\$guid"
    if (Test-EapoOnEndpoint $endpoint) {
        Write-Output "Saida Philips $guid ja usa o equalizador."
        continue
    }
    try {
        if (-not (Test-Path -LiteralPath $fx)) {
            throw "a saida nao tem FxProperties (efeitos de audio)"
        }

        # Install mode chosen like Device Selector does on Windows 8.1+.
        $hasSfxFamily = @($SFX, $MFX, $EFX) + $MultiFx | Where-Object { $null -ne (Get-Prop $fx $_) }
        $hasLfxGfx = ($null -ne (Get-Prop $fx $LFX)) -or ($null -ne (Get-Prop $fx $GFX))
        if ($hasLfxGfx -and -not $hasSfxFamily) {
            $mode = 'LFX_GFX'; $preSlot = $LFX; $postSlot = $GFX; $clear = @($SFX, $MFX, $EFX); $modes = @()
        } elseif ($null -ne (Get-Prop "$($endpoint.PSPath)\Properties" $CombinedKey)) {
            $mode = 'SFX_MFX'; $preSlot = $SFX; $postSlot = $MFX; $clear = @($LFX, $GFX); $modes = @($ModeSfx, $ModeMfx)
        } else {
            $mode = 'SFX_EFX'; $preSlot = $SFX; $postSlot = $EFX; $clear = @($LFX, $GFX); $modes = @($ModeSfx, $ModeEfx)
        }

        $original = @{}
        $backupDone = (Test-Path -LiteralPath $child) -and ($null -ne (Get-Prop $child 'PhilipsPcLink:Backup'))
        if ($backupDone) {
            # Keep the first backup: it holds the values from before any change.
            foreach ($name in $FxNames) { $original[$name] = [string](Get-Prop $child $name) }
            Write-Output "Usando o backup ja salvo da saida Philips $guid."
        } else {
            if (-not (Test-Path -LiteralPath $child)) { New-Item -Path $child -Force | Out-Null }
            foreach ($name in $FxNames) {
                $v = Get-Prop $fx $name
                $value = if ($null -eq $v) { '!VALUE' } else { [string]$v }
                $original[$name] = $value
                New-ItemProperty -LiteralPath $child -Name $name -Value $value -PropertyType String -Force | Out-Null
            }
            foreach ($name in $Extra) {
                $v = Get-Prop $fx $name
                if ($null -eq $v) { $v = '!VALUE' }
                $type = if ($v -is [string[]]) { 'MultiString' } else { 'String' }
                New-ItemProperty -LiteralPath $child -Name "PhilipsPcLink:$name" -Value $v -PropertyType $type -Force | Out-Null
            }
            New-ItemProperty -LiteralPath $child -Name 'PhilipsPcLink:Backup' -Value (Get-Date -Format s) -PropertyType String -Force | Out-Null
        }
        $preChild = if ($original[$preSlot] -like '!*') { '' } else { $original[$preSlot] }
        $postChild = if ($original[$postSlot] -like '!*') { '' } else { $original[$postSlot] }
        New-ItemProperty -LiteralPath $child -Name 'PreMixChild' -Value $preChild -PropertyType String -Force | Out-Null
        New-ItemProperty -LiteralPath $child -Name 'PostMixChild' -Value $postChild -PropertyType String -Force | Out-Null
        New-ItemProperty -LiteralPath $child -Name 'AllowSilentBufferModification' -Value 'false' -PropertyType String -Force | Out-Null
        New-ItemProperty -LiteralPath $child -Name 'Version' -Value '2' -PropertyType String -Force | Out-Null

        Set-Fx $guid $preSlot $pre
        Set-Fx $guid $postSlot $post
        foreach ($name in $clear) { Remove-Fx $guid $name }
        foreach ($name in $modes) { Set-Fx $guid $name $DefaultMode 'MultiString' }
        Remove-Fx $guid $DisableSysFx
        # Written last: it marks the output as "Equalizer APO" for its tools.
        Set-Fx $guid $FriendlyName 'Equalizer APO'
        $changed++
        Write-Output "Equalizador ativado na saida Philips $guid (modo $mode)."
    } catch {
        $failed += $endpoint
        Write-Output "Nao foi possivel ativar automaticamente na saida $guid ($($_.Exception.Message))."
    } finally {
        Restore-FxSecurity
    }
}

# ---------- Last resort: Equalizer APO's own Device Selector ----------
if ($failed.Count -gt 0) {
    $selector = Join-Path $installPath 'DeviceSelector.exe'
    if (Test-Path -LiteralPath $selector) {
        Write-Output 'Abrindo o configurador do Equalizer APO: marque "Philips Audio Set" e clique em OK.'
        Start-Process -FilePath $selector -Wait
        $still = @($failed | Where-Object { -not (Test-EapoOnEndpoint $_) })
        if ($still.Count -gt 0) {
            throw 'O equalizador nao foi ativado na saida Philips. Abra o Device Selector do Equalizer APO e marque Philips Audio Set.'
        }
        Write-Output 'Equalizador ativado pelo configurador do Equalizer APO.'
        $changed++
    } else {
        throw 'Device Selector do Equalizer APO nao encontrado.'
    }
}

# ---------- 4. Configuration: only the app's file on the Philips output ----------
$configDir = Get-Prop $EapoKey 'ConfigPath'
if (-not $configDir) { $configDir = Join-Path $installPath 'config' }
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
$configFile = Join-Path $configDir 'config.txt'
$current = if (Test-Path -LiteralPath $configFile) { [IO.File]::ReadAllText($configFile) } else { '' }
if ($current -notmatch [regex]::Escape($IncludeLine)) {
    $normalized = ($current -replace '\s+', ' ').Trim()
    # The untouched config.txt of Equalizer APO 1.4.2 (example filters and a
    # -6 dB preamp that would change the Philips sound).
    $default142 = 'Preamp: -6 dB Include: example.txt GraphicEQ: 25 0; 40 0; 63 0; 100 0; 160 0; 250 0; 400 0; 630 0; 1000 0; 1600 0; 2500 0; 4000 0; 6300 0; 10000 0; 16000 0'
    $isDefault = ($normalized -eq '') -or ($normalized -eq $default142)
    if ($isDefault) {
        if ($current) { Copy-Item -LiteralPath $configFile -Destination "$configFile.original" -Force }
        $text = "# Equalizer APO - configurado pelo Philips USB PC Link Activator`r`n$IncludeLine`r`n"
    } else {
        Copy-Item -LiteralPath $configFile -Destination "$configFile.antes-philips" -Force
        $text = $current.TrimEnd() + "`r`n$IncludeLine`r`n"
    }
    [IO.File]::WriteAllText($configFile, $text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output 'config.txt do Equalizer APO aponta para o equalizador do app.'
}
$appFile = Join-Path $configDir 'philips-pc-link.txt'
if (-not (Test-Path -LiteralPath $appFile)) {
    [IO.File]::WriteAllText($appFile, "Device: Philips Audio Set`r`nDevice: all`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

if ($changed -gt 0) { Restart-Audio }
Write-Output 'Equalizador pronto.'
