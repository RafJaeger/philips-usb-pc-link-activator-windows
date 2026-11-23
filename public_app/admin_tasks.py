"""Tasks that need administrator rights, run by the app itself.

The app re-opens its own executable as administrator (the normal Windows UAC
prompt) with ``--admin-task <name>``; that copy does the job in Python and
exits. Everything is done in Python, with the Windows APIs and pnputil.

Tasks:
  install-driver     WinUSB only on the radio's control interface (MI_03)
  restore-driver     give MI_03 back to the Windows HID driver
  equalizer-install  install Equalizer APO and enable it on the Philips output
  equalizer-remove   restore the Philips output (Equalizer APO stays installed)
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import tempfile
import traceback
from ctypes import wintypes
from pathlib import Path

import pnp_devices

ADMIN_FLAG = "--admin-task"
LOG_FLAG = "--admin-log"
ERROR_CANCELLED = 1223

VENDOR_PRODUCT = "USB\\VID_0471"
# (product id, control interface) pairs this app may bind to WinUSB.
CONTROL_TARGETS = (("0111", "MI_03"), ("0110", "MI_02"))
CONTROL_PREFIXES = tuple(f"{VENDOR_PRODUCT}&PID_{pid}&{mi}\\" for pid, mi in CONTROL_TARGETS)
AUDIO_PREFIXES = tuple(f"{VENDOR_PRODUCT}&PID_{pid}&MI_00\\" for pid, _ in CONTROL_TARGETS)
INF_NAME = "philips_audio_set_(interface_3).inf"  # bundled 0111 package


# --------------------------------------------------------------- launching

class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _self_command() -> tuple[str, list[str]]:
    """Executable and leading arguments that start this app again."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else python), [str(Path(__file__).with_name("app.py"))]


def run_as_admin(task: str, arguments: list[str] | None = None) -> tuple[int, str]:
    """Run one task in an elevated copy of the app; returns (exit code, its log)."""
    log = Path(tempfile.gettempdir()) / f"philips_pc_link_{task}.log"
    try:
        log.unlink()
    except FileNotFoundError:
        pass
    executable, lead = _self_command()
    params = subprocess.list2cmdline([*lead, ADMIN_FLAG, task, LOG_FLAG, str(log), *(arguments or [])])

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100  # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = params
    info.lpDirectory = str(Path(executable).parent)
    info.nShow = 0  # the task has no window of its own
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            return 1, "Permissao de administrador negada."
        return 1, f"Nao foi possivel pedir permissao de administrador (erro {error})."

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    code = wintypes.DWORD(1)
    try:
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    finally:
        kernel32.CloseHandle(info.hProcess)
    output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    return int(code.value), output.strip()


def is_admin_invocation(argv: list[str]) -> bool:
    return ADMIN_FLAG in argv


def main(argv: list[str]) -> int:
    """Entry point of the elevated copy (called before any window is created)."""
    args = argv[argv.index(ADMIN_FLAG) + 1:]
    task = args[0] if args else ""
    rest = args[1:]
    log_path = None
    if LOG_FLAG in rest:
        i = rest.index(LOG_FLAG)
        log_path = rest[i + 1] if i + 1 < len(rest) else None
        rest = rest[:i] + rest[i + 2:]
    stream = open(log_path, "w", encoding="utf-8") if log_path else None
    if stream is not None:
        sys.stdout = sys.stderr = stream
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("Esta tarefa precisa de permissao de administrador.")
        tasks = {
            "install-driver": lambda: install_driver(Path(rest[0])),
            "restore-driver": restore_driver,
            "equalizer-install": lambda: _equalizer().install(Path(rest[0])),
            "equalizer-remove": lambda: _equalizer().remove(),
        }
        if task not in tasks:
            raise RuntimeError(f"Tarefa desconhecida: {task}")
        tasks[task]()
        return 0
    except Exception as exc:  # reported to the app through the log
        print(f"ERRO: {exc}")
        traceback.print_exc()
        return 1
    finally:
        if stream is not None:
            stream.flush()
            stream.close()


def _equalizer():
    import equalizer_setup

    return equalizer_setup


# ------------------------------------------------------------------ driver

def _hidden() -> dict:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startup}


def pnputil(*arguments: str) -> int:
    exe = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "pnputil.exe"
    result = subprocess.run(
        # pnputil writes in the ANSI code page when its output is redirected (cp1252 here).
        [str(exe), *arguments], capture_output=True, text=True, encoding="mbcs", errors="replace", **_hidden()
    )
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    return result.returncode


def _philips_nodes() -> list[pnp_devices.PnpDevice]:
    return pnp_devices.find_devices(VENDOR_PRODUCT)


def _with_prefix(nodes, prefix):
    prefixes = (prefix,) if isinstance(prefix, str) else tuple(prefix)
    return [n for n in nodes if n.instance_id.upper().startswith(prefixes)]


def read_inf(path: Path) -> str:
    """INF files are often UTF-16 (with BOM); fall back to UTF-8."""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def check_inf_targets_mi03_only(inf_text: str) -> None:
    """Refuse a driver package that could touch the audio interface.

    The INF must name exactly one allowed control interface (CONTROL_TARGETS).
    """
    allowed = "|".join(f"VID_0471&PID_{pid}&{mi}" for pid, mi in CONTROL_TARGETS)
    if not re.search(rf'(?im)^\s*DeviceID\s*=\s*"(?:{allowed})"\s*$', inf_text):
        raise RuntimeError("INF recusado: o DeviceID nao e uma interface de controle permitida.")
    if not re.search(r"(?im)^\s*%DeviceName%\s*=\s*USB_Install\s*,\s*USB\\%DeviceID%\s*$", inf_text):
        raise RuntimeError("INF recusado: a linha de instalacao nao usa USB\\%DeviceID%.")
    if re.search(r"VID_0471&PID_01(?:10|11)&MI_00", inf_text.upper()):
        raise RuntimeError("INF recusado: menciona MI_00, a interface de audio.")


def _package_file(driver_dir: Path, pattern: str) -> Path:
    found = sorted(driver_dir.glob(pattern))
    if len(found) != 1:
        raise FileNotFoundError(f"Esperado um arquivo {pattern} em {driver_dir}, achei {len(found)}.")
    return found[0]


def trust_certificate(cer_path: Path) -> None:
    """Add the driver's test certificate to Root and TrustedPublisher (if missing)."""
    data = cer_path.read_bytes()
    if data.lstrip().startswith(b"-----BEGIN"):
        import base64

        body = b"".join(line for line in data.splitlines() if line and not line.startswith(b"-----"))
        data = base64.b64decode(body)
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CertOpenStore.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    crypt32.CertOpenStore.restype = ctypes.c_void_p
    crypt32.CertAddEncodedCertificateToStore.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    CERT_STORE_PROV_SYSTEM_W = 10
    CERT_SYSTEM_STORE_LOCAL_MACHINE = 0x00020000
    X509_ASN_ENCODING = 0x1
    CERT_STORE_ADD_USE_EXISTING = 2
    for store_name in ("Root", "TrustedPublisher"):
        store = crypt32.CertOpenStore(
            CERT_STORE_PROV_SYSTEM_W, 0, None, CERT_SYSTEM_STORE_LOCAL_MACHINE, ctypes.c_wchar_p(store_name)
        )
        if not store:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not crypt32.CertAddEncodedCertificateToStore(
                store, X509_ASN_ENCODING, data, len(data), CERT_STORE_ADD_USE_EXISTING, None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            crypt32.CertCloseStore(store, 0)
    print("Certificado do driver confiavel em Root e TrustedPublisher.")


def install_driver(driver_dir: Path) -> None:
    inf = _package_file(driver_dir, "*.inf")
    cat = _package_file(driver_dir, "*.cat")
    cer = _package_file(driver_dir, "*.cer")
    check_inf_targets_mi03_only(read_inf(inf))
    trust_certificate(cer)

    nodes = _philips_nodes()
    for node in _with_prefix(nodes, AUDIO_PREFIXES):
        if (node.service or "").upper() == "WINUSB":
            raise RuntimeError(f"A interface de audio MI_00 esta com WinUSB ({node.instance_id}). Restaure antes.")
        print(f"Audio preservado: {node.instance_id} servico={node.service}")

    code = pnputil("/add-driver", str(inf), "/install")
    if code not in (0, 3010):
        raise RuntimeError(f"pnputil /add-driver falhou (codigo {code}).")
    pnputil("/scan-devices")

    nodes = _philips_nodes()
    for node in _with_prefix(nodes, AUDIO_PREFIXES):
        if (node.service or "").upper() == "WINUSB":
            raise RuntimeError(f"Verificacao falhou: MI_00 ficou com WinUSB ({node.instance_id}).")
    controls = [n for n in _with_prefix(nodes, CONTROL_PREFIXES) if n.present]
    for node in controls:
        print(f"Controle: {node.instance_id} servico={node.service}")
    if controls and not any((n.service or "").upper() == "WINUSB" for n in controls):
        raise RuntimeError("Verificacao falhou: a interface de controle esta conectada mas sem WinUSB.")
    print("Driver instalado somente na interface de controle.")


def restore_driver() -> None:
    nodes = _philips_nodes()
    targets = [n for n in _with_prefix(nodes, CONTROL_PREFIXES) if (n.service or "").upper() == "WINUSB"]
    if not targets:
        print("A interface de controle nao esta com WinUSB; nada para restaurar.")
        return
    removed: set[str] = set()
    for node in targets:
        inf = node.inf_name
        if not inf or not re.fullmatch(r"oem\d+\.inf", inf, re.IGNORECASE):
            raise RuntimeError(f"INF '{inf}' de {node.instance_id} nao e de terceiros; use o Gerenciador de Dispositivos.")
        if inf.lower() in removed:
            continue
        code = pnputil("/delete-driver", inf, "/uninstall", "/force")
        if code not in (0, 3010):
            raise RuntimeError(f"pnputil /delete-driver {inf} falhou (codigo {code}).")
        removed.add(inf.lower())
    pnputil("/scan-devices")
    for node in _with_prefix(_philips_nodes(), CONTROL_PREFIXES):
        if node.present:
            print(f"Controle agora: {node.instance_id} servico={node.service}")
    print("Controle devolvido ao Windows. Se ainda aparecer WinUSB, tire e coloque o cabo USB.")
