"""Install Equalizer APO and enable it ONLY on the "Philips Audio Set" output.

Runs inside the elevated copy of the app (see admin_tasks). It reproduces what
Equalizer APO's Device Selector does (DeviceAPOInfo.cpp, version 1.4.2):

* the original LFX/GFX/SFX/MFX/EFX values are saved under
  HKLM\\SOFTWARE\\EqualizerAPO\\Child APOs\\{endpoint} ("!VALUE" when absent),
  so Equalizer APO's own uninstaller can restore them;
* install mode, as Device Selector picks it on Windows 8.1+:
  LFX/GFX when the output only has LFX/GFX effects, SFX/MFX when it has the
  "combined device" property, SFX/EFX otherwise;
* when Windows denies writing to FxProperties, the key's owner is changed to
  Administrators just long enough to write, then the original owner and
  permissions are put back.

If that still fails, Equalizer APO's Device Selector is opened so the user can
tick "Philips Audio Set" by hand. ``remove()`` restores the Philips output.
"""
from __future__ import annotations

import ctypes
import subprocess
import time
import winreg
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

HKLM = winreg.HKEY_LOCAL_MACHINE
VIEW = winreg.KEY_WOW64_64KEY

RENDER = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
EAPO_KEY = r"SOFTWARE\EqualizerAPO"
CHILD_BASE = EAPO_KEY + r"\Child APOs"
APO_ROOT = r"SOFTWARE\Classes\AudioEngine\AudioProcessingObjects"

FX_GUID = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d}"
LFX, GFX, SFX, MFX, EFX = (f"{FX_GUID},{n}" for n in (1, 2, 5, 6, 7))
MULTI_FX = tuple(f"{FX_GUID},{n}" for n in (13, 14, 15))
FX_NAMES = (LFX, GFX, SFX, MFX, EFX)
FRIENDLY_NAME = "{b725f130-47ef-101a-a5f1-02608c9eebac},10"
MODE_KEY = "{d3993a3f-99c2-4402-b5ec-a92a0367664b}"
MODE_SFX, MODE_MFX, MODE_EFX = (f"{MODE_KEY},{n}" for n in (5, 6, 7))
DEFAULT_MODE = "{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}"
DISABLE_SYSFX = "{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5"
DEVICE_NAME_KEY = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
COMBINED_KEY = "{b3f8fa53-0004-438e-9003-51a46e139bfc},41"
EXTRA = (FRIENDLY_NAME, MODE_SFX, MODE_MFX, MODE_EFX, DISABLE_SYSFX)  # restored by remove()
BACKUP_PREFIX = "PhilipsPcLink:"
BACKUP_MARK = BACKUP_PREFIX + "Backup"

INCLUDE_LINE = "Include: philips-pc-link.txt"
DEFAULT_142 = (
    "Preamp: -6 dB Include: example.txt GraphicEQ: 25 0; 40 0; 63 0; 100 0; 160 0; 250 0; "
    "400 0; 630 0; 1000 0; 1600 0; 2500 0; 4000 0; 6300 0; 10000 0; 16000 0"
)
ADMIN_FULL_CONTROL_ACE = "(A;;KA;;;BA)"


# ------------------------------------------------------------ registry I/O

def get_value(path: str, name: str):
    """(value, type) or None."""
    try:
        with winreg.OpenKey(HKLM, path, 0, winreg.KEY_READ | VIEW) as key:
            return winreg.QueryValueEx(key, name)
    except OSError:
        return None


def get(path: str, name: str):
    found = get_value(path, name)
    return None if found is None else found[0]


def key_exists(path: str) -> bool:
    try:
        winreg.OpenKey(HKLM, path, 0, winreg.KEY_READ | VIEW).Close()
        return True
    except OSError:
        return False


def subkeys(path: str) -> list[str]:
    names: list[str] = []
    try:
        with winreg.OpenKey(HKLM, path, 0, winreg.KEY_READ | VIEW) as key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        pass
    return names


def set_child(guid: str, name: str, value, kind: int = winreg.REG_SZ) -> None:
    with winreg.CreateKeyEx(HKLM, rf"{CHILD_BASE}\{guid}", 0, winreg.KEY_ALL_ACCESS | VIEW) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


# ------------------------------------------------- privileges and security

READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
OWNER_SECURITY_INFORMATION = 0x1
DACL_SECURITY_INFORMATION = 0x4
SDDL_REVISION_1 = 1

_advapi = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi.RegGetKeySecurity.argtypes = [wintypes.HKEY, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
_advapi.RegGetKeySecurity.restype = wintypes.LONG
_advapi.RegSetKeySecurity.argtypes = [wintypes.HKEY, wintypes.DWORD, ctypes.c_void_p]
_advapi.RegSetKeySecurity.restype = wintypes.LONG
_advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG),
]
_advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
_advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG),
]
_advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
_kernel.LocalFree.argtypes = [ctypes.c_void_p]
_kernel.GetCurrentProcess.restype = wintypes.HANDLE
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", LUID), ("Attributes", wintypes.DWORD)]


_advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_advapi.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)]
_advapi.AdjustTokenPrivileges.argtypes = [
    wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES), wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
]


def enable_privilege(name: str) -> bool:
    token = wintypes.HANDLE()
    if not _advapi.OpenProcessToken(_kernel.GetCurrentProcess(), 0x28, ctypes.byref(token)):  # ADJUST | QUERY
        return False
    try:
        state = TOKEN_PRIVILEGES(1, LUID(), 2)  # SE_PRIVILEGE_ENABLED
        if not _advapi.LookupPrivilegeValueW(None, name, ctypes.byref(state.Luid)):
            return False
        if not _advapi.AdjustTokenPrivileges(token, False, ctypes.byref(state), 0, None, None):
            return False
        return ctypes.get_last_error() == 0
    finally:
        _kernel.CloseHandle(token)


def _handle(key) -> int:
    return int(key)


def get_sddl(key, info: int) -> str:
    size = wintypes.DWORD(0)
    _advapi.RegGetKeySecurity(_handle(key), info, None, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(max(size.value, 1))
    error = _advapi.RegGetKeySecurity(_handle(key), info, buffer, ctypes.byref(size))
    if error:
        raise ctypes.WinError(error)
    text = wintypes.LPWSTR()
    if not _advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        buffer, SDDL_REVISION_1, info, ctypes.byref(text), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        _kernel.LocalFree(text)


def set_sddl(key, sddl: str, info: int) -> None:
    descriptor = ctypes.c_void_p()
    if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        error = _advapi.RegSetKeySecurity(_handle(key), info, descriptor)
        if error:
            raise ctypes.WinError(error)
    finally:
        _kernel.LocalFree(descriptor)


def with_admin_full_control(dacl_sddl: str) -> str:
    """Add "Administrators: full control" as the first explicit entry of a DACL."""
    if ADMIN_FULL_CONTROL_ACE in dacl_sddl:
        return dacl_sddl
    start = dacl_sddl.find("D:")
    if start < 0:
        return dacl_sddl + "D:" + ADMIN_FULL_CONTROL_ACE
    first_ace = dacl_sddl.find("(", start)
    if first_ace < 0:
        return dacl_sddl + ADMIN_FULL_CONTROL_ACE
    return dacl_sddl[:first_ace] + ADMIN_FULL_CONTROL_ACE + dacl_sddl[first_ace:]


class FxWriter:
    """Writes FxProperties values, borrowing ownership only when Windows denies it."""

    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    @staticmethod
    def _path(guid: str) -> str:
        return rf"{RENDER}\{guid}\FxProperties"

    def _grant(self, guid: str) -> None:
        if guid in self.saved:
            return
        for privilege in ("SeTakeOwnershipPrivilege", "SeRestorePrivilege", "SeBackupPrivilege"):
            enable_privilege(privilege)
        path = self._path(guid)
        with winreg.OpenKey(HKLM, path, 0, READ_CONTROL | WRITE_OWNER | VIEW) as key:
            self.saved[guid] = get_sddl(key, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION)
            set_sddl(key, "O:BA", OWNER_SECURITY_INFORMATION)
        with winreg.OpenKey(HKLM, path, 0, READ_CONTROL | WRITE_DAC | VIEW) as key:
            dacl = get_sddl(key, DACL_SECURITY_INFORMATION)
            set_sddl(key, with_admin_full_control(dacl), DACL_SECURITY_INFORMATION)
        print(f"Permissao de escrita obtida em FxProperties da saida {guid} (sera restaurada).")

    def restore(self) -> None:
        for guid, sddl in list(self.saved.items()):
            try:
                access = READ_CONTROL | WRITE_DAC | WRITE_OWNER | VIEW
                with winreg.OpenKey(HKLM, self._path(guid), 0, access) as key:
                    set_sddl(key, sddl, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION)
                print(f"Dono e permissoes originais de FxProperties restaurados ({guid}).")
            except OSError as exc:
                print(f"Aviso: nao foi possivel restaurar as permissoes de {guid} ({exc}).")
            del self.saved[guid]

    def _open(self, guid: str):
        access = winreg.KEY_READ | winreg.KEY_SET_VALUE | VIEW
        try:
            return winreg.OpenKey(HKLM, self._path(guid), 0, access)
        except PermissionError:
            self._grant(guid)
            return winreg.OpenKey(HKLM, self._path(guid), 0, access)

    def set(self, guid: str, name: str, value, kind: int = winreg.REG_SZ) -> None:
        with self._open(guid) as key:
            winreg.SetValueEx(key, name, 0, kind, value)

    def delete(self, guid: str, name: str) -> None:
        with self._open(guid) as key:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


# ----------------------------------------------------------------- outputs

def philips_endpoints() -> list[str]:
    found = []
    for guid in subkeys(RENDER):
        name = get(rf"{RENDER}\{guid}\Properties", DEVICE_NAME_KEY)
        if isinstance(name, str) and "philips audio set" in name.lower():
            found.append(guid)
    return found


def eapo_on_endpoint(guid: str) -> bool:
    return (
        get(rf"{RENDER}\{guid}\FxProperties", FRIENDLY_NAME) == "Equalizer APO"
        and key_exists(rf"{CHILD_BASE}\{guid}")
    )


def restart_audio() -> None:
    """Restart the Windows audio service so the change takes effect."""
    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in (
            "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode",
            "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint",
        )]

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.OpenSCManagerW.restype = ctypes.c_void_p
    api.OpenServiceW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD]
    api.OpenServiceW.restype = ctypes.c_void_p
    api.ControlService.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SERVICE_STATUS)]
    api.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]
    api.StartServiceW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    api.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    manager = api.OpenSCManagerW(None, None, 0x1)
    service = api.OpenServiceW(manager, "Audiosrv", 0x20 | 0x10 | 0x4) if manager else None
    try:
        if not service:
            raise ctypes.WinError(ctypes.get_last_error())
        status = SERVICE_STATUS()
        api.ControlService(service, 1, ctypes.byref(status))  # STOP
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if api.QueryServiceStatus(service, ctypes.byref(status)) and status.dwCurrentState == 1:  # STOPPED
                break
            time.sleep(0.25)
        if not api.StartServiceW(service, 0, None) and ctypes.get_last_error() != 1056:  # already running
            raise ctypes.WinError(ctypes.get_last_error())
        print("Servico de audio do Windows reiniciado.")
    except OSError as exc:
        print(f"Nao foi possivel reiniciar o servico de audio ({exc}). Reinicie o Windows.")
    finally:
        if service:
            api.CloseServiceHandle(service)
        if manager:
            api.CloseServiceHandle(manager)


# ------------------------------------------------------------ install path

def _install_path() -> Path | None:
    path = get(EAPO_KEY, "InstallPath")
    if path and (Path(path) / "EqualizerAPO.dll").exists():
        return Path(path)
    return None


def _close_device_selector() -> None:
    import psutil

    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() == "deviceselector.exe":
            try:
                proc.kill()
            except psutil.Error:
                pass


def ensure_installed(installer: Path) -> Path:
    path = _install_path()
    if path is not None:
        print(f"Equalizer APO ja instalado em {path}.")
        return path
    if not installer.exists():
        raise FileNotFoundError(f"Instalador do Equalizer APO nao encontrado: {installer}")
    print("Instalando o Equalizer APO (silencioso)...")
    process = subprocess.Popen([str(installer), "/S"])
    deadline = time.monotonic() + 240
    while process.poll() is None and time.monotonic() < deadline:
        # The installer opens Device Selector (a window); we select the output ourselves.
        _close_device_selector()
        time.sleep(0.3)
    if process.poll() is None:
        raise RuntimeError("O instalador do Equalizer APO nao terminou a tempo.")
    _close_device_selector()
    path = _install_path()
    if path is None:
        raise RuntimeError("O Equalizer APO nao foi instalado.")
    print(f"Equalizer APO instalado em {path}.")
    return path


def apo_class_ids() -> tuple[str, str]:
    pre = post = None
    for clsid in subkeys(APO_ROOT):
        if get(rf"{APO_ROOT}\{clsid}", "FriendlyName") != "EqualizerAPO":
            continue
        class_name = get(rf"SOFTWARE\Classes\CLSID\{clsid}", "") or ""
        if "Pre-Mix" in class_name:
            pre = clsid
        elif "Post-Mix" in class_name:
            post = clsid
    if not pre or not post:
        raise RuntimeError("Classes do Equalizer APO nao registradas (reinstale).")
    return pre, post


# ------------------------------------------------------------- enable/undo

def _choose_mode(guid: str):
    fx = rf"{RENDER}\{guid}\FxProperties"
    has_sfx_family = any(get_value(fx, n) is not None for n in (SFX, MFX, EFX, *MULTI_FX))
    has_lfx_gfx = get_value(fx, LFX) is not None or get_value(fx, GFX) is not None
    if has_lfx_gfx and not has_sfx_family:
        return "LFX_GFX", LFX, GFX, (SFX, MFX, EFX), ()
    if get_value(rf"{RENDER}\{guid}\Properties", COMBINED_KEY) is not None:
        return "SFX_MFX", SFX, MFX, (LFX, GFX), (MODE_SFX, MODE_MFX)
    return "SFX_EFX", SFX, EFX, (LFX, GFX), (MODE_SFX, MODE_EFX)


def _backup(guid: str) -> dict[str, str]:
    """Original effect values of one output (saved once, before any change)."""
    child = rf"{CHILD_BASE}\{guid}"
    fx = rf"{RENDER}\{guid}\FxProperties"
    if key_exists(child) and get(child, BACKUP_MARK) is not None:
        print(f"Usando o backup ja salvo da saida Philips {guid}.")
        return {name: str(get(child, name) or "!VALUE") for name in FX_NAMES}
    original = {}
    for name in FX_NAMES:
        value = get(fx, name)
        original[name] = "!VALUE" if value is None else str(value)
        set_child(guid, name, original[name])
    for name in EXTRA:
        found = get_value(fx, name)
        if found is None:
            set_child(guid, BACKUP_PREFIX + name, "!VALUE")
        else:
            set_child(guid, BACKUP_PREFIX + name, found[0], found[1])
    set_child(guid, BACKUP_MARK, datetime.now().isoformat(timespec="seconds"))
    return original


def enable_on(guid: str, pre: str, post: str, writer: FxWriter) -> None:
    if not key_exists(rf"{RENDER}\{guid}\FxProperties"):
        raise RuntimeError("a saida nao tem FxProperties (efeitos de audio)")
    mode, pre_slot, post_slot, clear, modes = _choose_mode(guid)
    original = _backup(guid)
    set_child(guid, "PreMixChild", "" if original[pre_slot].startswith("!") else original[pre_slot])
    set_child(guid, "PostMixChild", "" if original[post_slot].startswith("!") else original[post_slot])
    set_child(guid, "AllowSilentBufferModification", "false")
    set_child(guid, "Version", "2")

    writer.set(guid, pre_slot, pre)
    writer.set(guid, post_slot, post)
    for name in clear:
        writer.delete(guid, name)
    for name in modes:
        writer.set(guid, name, [DEFAULT_MODE], winreg.REG_MULTI_SZ)
    writer.delete(guid, DISABLE_SYSFX)
    # Written last: it marks the output as "Equalizer APO" for its tools.
    writer.set(guid, FRIENDLY_NAME, "Equalizer APO")
    print(f"Equalizador ativado na saida Philips {guid} (modo {mode}).")


def _restore_value(writer: FxWriter, guid: str, name: str, saved) -> None:
    value, kind = saved
    if isinstance(value, str) and value in ("!VALUE", "!KEY"):
        writer.delete(guid, name)
    elif kind == winreg.REG_MULTI_SZ:
        writer.set(guid, name, list(value), winreg.REG_MULTI_SZ)
    elif kind == winreg.REG_DWORD:
        writer.set(guid, name, int(value), winreg.REG_DWORD)
    elif name == DISABLE_SYSFX and str(value).isdigit():
        # Older backups stored this number as text.
        writer.set(guid, name, int(value), winreg.REG_DWORD)
    else:
        writer.set(guid, name, str(value))


def remove() -> None:
    writer = FxWriter()
    count = 0
    try:
        for guid in philips_endpoints():
            child = rf"{CHILD_BASE}\{guid}"
            if not key_exists(child):
                continue
            for name in FX_NAMES + EXTRA:
                saved = get_value(child, BACKUP_PREFIX + name if name in EXTRA else name)
                if saved is not None:
                    _restore_value(writer, guid, name, saved)
            winreg.DeleteKeyEx(HKLM, child, VIEW)
            count += 1
            print(f"Saida Philips {guid} restaurada.")
    finally:
        writer.restore()
    if count == 0:
        print("Nenhuma saida Philips com o equalizador.")
    else:
        restart_audio()


def _write_config(install_path: Path) -> None:
    folder = Path(get(EAPO_KEY, "ConfigPath") or install_path / "config")
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "config.txt"
    current = config.read_text(encoding="utf-8", errors="replace") if config.exists() else ""
    if INCLUDE_LINE not in current:
        normalized = " ".join(current.split())
        if normalized in ("", DEFAULT_142):
            if current:
                (folder / "config.txt.original").write_text(current, encoding="utf-8")
            text = f"# Equalizer APO - configurado pelo Philips USB PC Link Activator\r\n{INCLUDE_LINE}\r\n"
        else:
            (folder / "config.txt.antes-philips").write_text(current, encoding="utf-8")
            text = current.rstrip() + f"\r\n{INCLUDE_LINE}\r\n"
        config.write_text(text, encoding="utf-8")
        print("config.txt do Equalizer APO aponta para o equalizador do app.")
    app_file = folder / "philips-pc-link.txt"
    if not app_file.exists():
        app_file.write_text("Device: Philips Audio Set\r\nDevice: all\r\n", encoding="utf-8")


def install(installer: Path) -> None:
    install_path = ensure_installed(installer)
    pre, post = apo_class_ids()
    endpoints = philips_endpoints()
    if not endpoints:
        raise RuntimeError("Saida Philips Audio Set nao encontrada no Windows. Conecte o radio.")

    changed = 0
    failed: list[str] = []
    for guid in endpoints:
        if eapo_on_endpoint(guid):
            print(f"Saida Philips {guid} ja usa o equalizador.")
            continue
        writer = FxWriter()
        try:
            enable_on(guid, pre, post, writer)
            changed += 1
        except OSError as exc:
            failed.append(guid)
            print(f"Nao foi possivel ativar automaticamente na saida {guid} ({exc}).")
        except RuntimeError as exc:
            failed.append(guid)
            print(f"Nao foi possivel ativar automaticamente na saida {guid} ({exc}).")
        finally:
            writer.restore()

    if failed:
        selector = install_path / "DeviceSelector.exe"
        if not selector.exists():
            raise RuntimeError("Device Selector do Equalizer APO nao encontrado.")
        print('Abrindo o configurador do Equalizer APO: marque "Philips Audio Set" e clique em OK.')
        subprocess.run([str(selector)])
        if any(not eapo_on_endpoint(guid) for guid in failed):
            raise RuntimeError(
                "O equalizador nao foi ativado na saida Philips. Abra o Device Selector do "
                "Equalizer APO e marque Philips Audio Set."
            )
        print("Equalizador ativado pelo configurador do Equalizer APO.")
        changed += 1

    _write_config(install_path)
    if changed:
        restart_audio()
    print("Equalizador pronto.")
