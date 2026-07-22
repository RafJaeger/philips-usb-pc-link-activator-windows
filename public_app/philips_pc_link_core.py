from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import comtypes
import usb1
from pycaw.pycaw import AudioUtilities, ERole

from profiles import SUPPORTED_PROFILES, DeviceProfile, default_profile, find_profile_by_usb_id

MEDIA_KEYS = {
    0: ("Play/Pause", 0xB3),
    1: ("Stop", 0xB2),
    2: ("Next Track", 0xB0),
    3: ("Previous Track", 0xB1),
    4: ("Mute", 0xAD),
    5: ("Volume Up", 0xAF),
    6: ("Volume Down", 0xAE),
}

APP_RUN_KEY = "PhilipsUSBPCLinkActivator"
USB_LOCK = threading.RLock()
SUPPORTED_MEDIA_MASK = 0x7F


@dataclass(frozen=True)
class ButtonEvent:
    bit: int
    name: str
    vk_code: int
    source: str
    raw: bytes

    @property
    def label(self) -> str:
        if self.source == "standard":
            return self.name
        return f"{self.name} ({self.source})"


@dataclass(frozen=True)
class DeviceStatus:
    present: bool
    profile: DeviceProfile | None
    interface3_service: str | None
    interface3_ok: bool
    audio_present: bool
    default_output: bool
    windows_volume: float | None
    muted: bool | None
    summary: str


@dataclass(frozen=True)
class PnpStatus:
    profile: DeviceProfile
    seen: bool
    present: bool
    service: str | None
    audio_present: bool


def app_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def driver_dir(profile: DeviceProfile | None = None) -> Path:
    active_profile = profile or default_profile()
    return app_root() / "driver" / active_profile.driver_package


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --minimized'
    return f'"{Path(sys.executable).resolve()}" "{Path(__file__).with_name("app.py").resolve()}" --minimized'


def set_start_with_windows(enabled: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(key, APP_RUN_KEY, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_RUN_KEY)
            except FileNotFoundError:
                pass


def get_start_with_windows() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as key:
            winreg.QueryValueEx(key, APP_RUN_KEY)
            return True
    except FileNotFoundError:
        return False


def _hidden_subprocess_options() -> dict:
    if os.name != "nt":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _run_powershell(script: str, elevated: bool = False) -> subprocess.CompletedProcess:
    if elevated:
        script_path = os.environ.get("TEMP", str(Path.home()))
        path = Path(script_path) / "philips_pc_link_driver_install.ps1"
        path.write_text(script, encoding="utf-8")
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{path}\"'",
        ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_subprocess_options(),
        )

    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_hidden_subprocess_options(),
    )


def _profile_instance_filter(profile: DeviceProfile) -> str:
    return f"VID_{profile.vendor_id:04X}&PID_{profile.product_id:04X}"


def _parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def _usb_device_present(profile: DeviceProfile) -> bool:
    try:
        with usb1.USBContext() as ctx:
            return _find_device(ctx, profile) is not None
    except Exception:
        return False


def _read_usb_interface_service(profile: DeviceProfile) -> str | None:
    interface_key = (
        r"SYSTEM\CurrentControlSet\Enum\USB"
        rf"\VID_{profile.vendor_id:04X}&PID_{profile.product_id:04X}&{profile.control_hardware_id_suffix}"
    )
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, interface_key, 0, winreg.KEY_READ) as parent:
            index = 0
            while True:
                try:
                    child_name = winreg.EnumKey(parent, index)
                except OSError:
                    break
                index += 1

                try:
                    with winreg.OpenKey(parent, child_name, 0, winreg.KEY_READ) as child:
                        service, _ = winreg.QueryValueEx(child, "Service")
                        if service:
                            return str(service)
                except OSError:
                    continue
    except OSError:
        return None
    return None


def _get_pnp_status(profile: DeviceProfile) -> PnpStatus:
    id_filter = _profile_instance_filter(profile)
    control_like = f"USB\\VID_{profile.vendor_id:04X}&PID_{profile.product_id:04X}&{profile.control_hardware_id_suffix}\\*"
    audio_like = f"{profile.audio_hardware_prefix}\\*"
    script = rf"""
function Test-Present($Device) {{
  $prop = Get-PnpDeviceProperty -InstanceId $Device.InstanceId -KeyName 'DEVPKEY_Device_IsPresent' -ErrorAction SilentlyContinue
  return [bool]($prop -and $prop.Data -eq $true)
}}

$devices = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {{
  $_.InstanceId -match '{id_filter}' -or $_.FriendlyName -match 'Philips Audio Set'
}})

$seen = [bool]($devices | Where-Object {{ $_.InstanceId -like 'USB\VID_{profile.vendor_id:04X}&PID_{profile.product_id:04X}*' }})
$presentDevices = @($devices | Where-Object {{ Test-Present $_ }})
$present = [bool]($presentDevices | Where-Object {{ $_.InstanceId -like 'USB\VID_{profile.vendor_id:04X}&PID_{profile.product_id:04X}*' }})
$audio = [bool]($presentDevices | Where-Object {{ $_.InstanceId -like '{audio_like}' -or ($_.FriendlyName -like '*Philips Audio Set*' -and $_.Class -eq 'AudioEndpoint') }})
$mi03 = $devices | Where-Object {{ $_.InstanceId -like '{control_like}' }} | Select-Object -First 1
$service = ''
if ($mi03) {{
  $prop = Get-PnpDeviceProperty -InstanceId $mi03.InstanceId -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue
  if ($prop) {{ $service = [string]$prop.Data }}
}}
"seen=$seen"
"present=$present"
"audio=$audio"
"service=$service"
"""
    result = _run_powershell(script)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

    return PnpStatus(
        profile=profile,
        seen=_parse_bool(values.get("seen")),
        present=_parse_bool(values.get("present")),
        service=values.get("service") or None,
        audio_present=_parse_bool(values.get("audio")),
    )


def detect_pnp_profile() -> PnpStatus | None:
    for profile in SUPPORTED_PROFILES:
        pnp = _get_pnp_status(profile)
        if pnp.seen:
            return pnp
    return None


def detect_profile() -> DeviceProfile | None:
    try:
        with usb1.USBContext() as ctx:
            for dev in ctx.getDeviceIterator(skip_on_error=True):
                profile = find_profile_by_usb_id(dev.getVendorID(), dev.getProductID())
                if profile is not None:
                    return profile
    except Exception:
        pass
    return None


def get_status() -> DeviceStatus:
    usb_profile = detect_profile()
    pnp = detect_pnp_profile()
    profile = usb_profile or (pnp.profile if pnp else None)
    if profile is None:
        return DeviceStatus(False, None, None, False, False, False, None, None, "Nenhum perfil USB PC Link suportado encontrado.")

    present = bool(usb_profile) or (bool(pnp.present) if pnp else False)
    service = pnp.service if pnp else _read_usb_interface_service(profile)
    control_access = bool(usb_profile)
    audio = bool(pnp.audio_present) if pnp and pnp.present else find_philips_render_endpoint(profile) is not None
    winusb = service == "WinUSB"
    ready = present and winusb and control_access
    default_endpoint = is_philips_default_output(profile) if present and audio else False

    if not present:
        summary = (
            f"{profile.display_name}: driver encontrado, mas o Windows nao ve o radio conectado agora. "
            "Tire e coloque o cabo USB, selecione PC Link novamente, ou desligue/ligue o radio."
        )
    elif not winusb:
        summary = f"{profile.display_name}: interface de controle precisa de WinUSB. Driver atual: {service or 'nao detectado'}."
    elif not control_access:
        summary = (
            f"{profile.display_name}: WinUSB instalado, mas a interface USB nao abriu para controle. "
            "Reconecte o cabo USB e tente Ativar PC Link."
        )
    else:
        summary = f"{profile.display_name} pronto para ativar USB PC Link."

    return DeviceStatus(
        present=present,
        profile=profile,
        interface3_service=service,
        interface3_ok=ready,
        audio_present=audio,
        default_output=default_endpoint,
        windows_volume=None,
        muted=None,
        summary=summary,
    )


def decode_button_report(
    data: bytes,
    previous_standard_bits: int = 0,
    previous_alt_bits: int = 0,
) -> tuple[list[ButtonEvent], int, int]:
    if not data:
        return [], 0, 0

    standard_bits = data[0] & SUPPORTED_MEDIA_MASK
    alt_bits = 0
    if len(data) > 1 and standard_bits == 0:
        alt_bits = data[1] & SUPPORTED_MEDIA_MASK

    events: list[ButtonEvent] = []
    standard_pressed = standard_bits & ~previous_standard_bits
    for bit, (name, vk_code) in MEDIA_KEYS.items():
        if standard_pressed & (1 << bit):
            events.append(ButtonEvent(bit, name, vk_code, "standard", data))

    alt_pressed = alt_bits & ~previous_alt_bits
    for bit, (name, vk_code) in MEDIA_KEYS.items():
        if alt_pressed & (1 << bit):
            events.append(ButtonEvent(bit, name, vk_code, "remote-alt-byte", data))

    return events, standard_bits, alt_bits


def install_winusb_driver(profile: DeviceProfile | None = None) -> None:
    active_profile = profile or detect_profile() or default_profile()
    package = driver_dir(active_profile)
    install_script = app_root() / "scripts" / "install-winusb-mi03.ps1"
    if install_script.exists() and active_profile.profile_id == "philips-uac3553b-0471-0111":
        script = rf"""
$ErrorActionPreference = 'Stop'
& '{install_script}' -DriverDirectory '{package}' -AcceptLocalTestCertificate
"""
        result = _run_powershell(script, elevated=True)
        if result.returncode != 0:
            raise RuntimeError((result.stdout + "\n" + result.stderr).strip())
        return

    inf = next(package.glob("*.inf"), None)
    cert = next(package.glob("*.cer"), None)
    if inf is None or cert is None or not inf.exists() or not cert.exists():
        raise FileNotFoundError(f"Driver package is incomplete: {package}")

    script = rf"""
$ErrorActionPreference = 'Stop'
$cert = '{cert}'
$inf = '{inf}'
Import-Certificate -FilePath $cert -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Import-Certificate -FilePath $cert -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
pnputil /add-driver $inf /install
pnputil /scan-devices
"""
    result = _run_powershell(script, elevated=True)
    if result.returncode != 0:
        raise RuntimeError((result.stdout + "\n" + result.stderr).strip())


def restore_control_driver(profile: DeviceProfile | None = None) -> None:
    active_profile = profile or detect_profile() or default_profile()
    restore_script = app_root() / "scripts" / "restore-mi03-hid.ps1"
    if not restore_script.exists() or active_profile.profile_id != "philips-uac3553b-0471-0111":
        raise FileNotFoundError("Restore script is not available for this profile.")

    script = rf"""
$ErrorActionPreference = 'Stop'
& '{restore_script}'
"""
    result = _run_powershell(script, elevated=True)
    if result.returncode != 0:
        raise RuntimeError((result.stdout + "\n" + result.stderr).strip())


def _find_device(ctx: usb1.USBContext, profile: DeviceProfile):
    for dev in ctx.getDeviceIterator(skip_on_error=True):
        if dev.getVendorID() == profile.vendor_id and dev.getProductID() == profile.product_id:
            return dev
    return None


def send_pc_link_enable(profile: DeviceProfile | None = None) -> str:
    active_profile = profile or detect_profile() or default_profile()
    with USB_LOCK:
        with usb1.USBContext() as ctx:
            dev = _find_device(ctx, active_profile)
            if dev is None:
                raise RuntimeError(f"{active_profile.display_name} {active_profile.vid_pid_label} nao encontrado.")
            try:
                serial = dev.getSerialNumber()
            except Exception:
                serial = "desconhecido"
            handle = dev.open()
            try:
                lines = [
                    f"{active_profile.display_name} encontrado: bus={dev.getBusNumber()} addr={dev.getDeviceAddress()} serial={serial!r}"
                ]
                for packet in active_profile.pc_link_packets:
                    sent = handle.controlWrite(
                        packet.request_type,
                        packet.request,
                        packet.value,
                        packet.index,
                        packet.payload,
                        timeout=1000,
                    )
                    lines.append(f"PC Link: wIndex=0x{packet.index:04x} sent={sent}")
                    time.sleep(0.03)
                return "\n".join(lines)
            finally:
                handle.close()


def try_radio_power_on(profile: DeviceProfile | None = None) -> str:
    active_profile = profile or detect_profile() or default_profile()
    if not _usb_device_present(active_profile):
        return (
            f"{active_profile.display_name} nao aparece no USB agora. "
            "Para ligar de verdade, o radio precisa estar em standby com USB enumerado; "
            "se ele sumiu do Windows, use o botao do aparelho ou controle remoto."
        )

    result = send_pc_link_enable(active_profile)
    return (
        result
        + "\nTentativa de ligar/reativar enviada. Este app ainda nao tem um comando Power On dedicado "
        "confirmado; por enquanto ele usa a sequencia segura de ativacao do PC Link."
    )


def find_philips_render_endpoint(profile: DeviceProfile | None = None):
    active_profile = profile or detect_profile() or default_profile()
    audio_id = active_profile.audio_hardware_prefix.upper()
    comtypes.CoInitialize()
    for device in AudioUtilities.GetAllDevices():
        friendly_name = getattr(device, "FriendlyName", "") or ""
        endpoint_id = getattr(device, "id", "") or ""
        if str(getattr(device, "state", "")).lower().find("active") == -1:
            continue
        properties = getattr(device, "properties", {}) or {}
        property_text = " ".join(str(value).upper() for value in properties.values())
        if endpoint_id.startswith("{0.0.0.") and (
            audio_id in property_text or "Philips Audio Set" in friendly_name
        ):
            return device
    return None


def get_windows_volume(profile: DeviceProfile | None = None) -> tuple[float, bool] | None:
    device = find_philips_render_endpoint(profile)
    if device is None:
        return None
    endpoint = device.EndpointVolume
    return float(endpoint.GetMasterVolumeLevelScalar()), bool(endpoint.GetMute())


def set_windows_volume(scalar: float, mute: bool | None = None, profile: DeviceProfile | None = None) -> None:
    device = find_philips_render_endpoint(profile)
    if device is None:
        raise RuntimeError("Endpoint de saida Philips nao encontrado no Windows.")
    endpoint = device.EndpointVolume
    scalar = max(0.0, min(1.0, float(scalar)))
    endpoint.SetMasterVolumeLevelScalar(scalar, None)
    if mute is not None:
        endpoint.SetMute(bool(mute), None)


def adjust_windows_volume(delta: float, profile: DeviceProfile | None = None) -> None:
    state = get_windows_volume(profile)
    if state is None:
        raise RuntimeError("Endpoint de saida Philips nao encontrado no Windows.")
    scalar, muted = state
    set_windows_volume(max(0.0, min(1.0, scalar + delta)), False if muted and delta > 0 else muted, profile)


def toggle_windows_mute(profile: DeviceProfile | None = None) -> None:
    state = get_windows_volume(profile)
    if state is None:
        raise RuntimeError("Endpoint de saida Philips nao encontrado no Windows.")
    scalar, muted = state
    set_windows_volume(scalar, not muted, profile)


def set_default_output(profile: DeviceProfile | None = None) -> None:
    device = find_philips_render_endpoint(profile)
    if device is None:
        raise RuntimeError("Endpoint de saida Philips nao encontrado no Windows.")
    AudioUtilities.SetDefaultDevice(device.id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])


def is_philips_default_output(profile: DeviceProfile | None = None) -> bool:
    device = find_philips_render_endpoint(profile)
    speakers = AudioUtilities.GetSpeakers()
    if device is None or speakers is None:
        return False
    return str(getattr(device, "id", "")).lower() == str(getattr(speakers, "id", "")).lower()


def export_diagnostics(profile: DeviceProfile | None = None) -> Path:
    active_profile = profile or detect_profile() or default_profile()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    destination = Path.home() / "Desktop" / f"philips-usb-pc-link-diagnostics-{timestamp}.txt"
    script = rf"""
"Philips USB PC Link Diagnostics"
"Timestamp: $(Get-Date -Format o)"
"Profile: {active_profile.profile_id} ({active_profile.vid_pid_label})"
""
"PnP devices:"
Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {{
  $_.InstanceId -match 'VID_{active_profile.vendor_id:04X}&PID_{active_profile.product_id:04X}|Philips Audio Set'
}} | Format-List Status,Class,FriendlyName,InstanceId
""
"Driver properties:"
Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {{
  $_.InstanceId -match 'VID_{active_profile.vendor_id:04X}&PID_{active_profile.product_id:04X}'
}} | ForEach-Object {{
  $_.InstanceId
  Get-PnpDeviceProperty -InstanceId $_.InstanceId -ErrorAction SilentlyContinue |
    Where-Object {{ $_.KeyName -match 'Service|DriverInfPath|DriverDesc|HardwareIds|CompatibleIds|BusReportedDeviceDesc' }} |
    Format-Table -AutoSize KeyName,Data
}}
"""
    result = _run_powershell(script)
    destination.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    return destination


def capture_button_reports(
    duration_seconds: float = 15.0,
    profile: DeviceProfile | None = None,
    max_reports: int = 80,
) -> str:
    active_profile = profile or detect_profile() or default_profile()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    destination = Path.home() / "Desktop" / f"philips-usb-pc-link-button-capture-{timestamp}.txt"
    lines = [
        "Philips USB PC Link button capture",
        f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Profile: {active_profile.profile_id} ({active_profile.vid_pid_label})",
        f"Duration: {duration_seconds:.1f}s",
        "",
        "Pressione os botoes fisicos e do controle remoto durante a captura.",
        "",
    ]

    standard_bits = 0
    alt_bits = 0
    last_raw: bytes | None = None
    report_count = 0
    start = time.monotonic()
    end = start + max(1.0, float(duration_seconds))

    with USB_LOCK:
        with usb1.USBContext() as ctx:
            dev = _find_device(ctx, active_profile)
            if dev is None:
                raise RuntimeError(f"{active_profile.display_name} {active_profile.vid_pid_label} nao encontrado.")
            handle = dev.open()
            try:
                try:
                    handle.claimInterface(active_profile.control_interface)
                except usb1.USBError:
                    pass

                while time.monotonic() < end and report_count < max_reports:
                    timeout_ms = min(250, max(1, int((end - time.monotonic()) * 1000)))
                    try:
                        data = bytes(
                            handle.interruptRead(
                                active_profile.interrupt_endpoint,
                                active_profile.interrupt_packet_size,
                                timeout=timeout_ms,
                            )
                        )
                    except usb1.USBErrorTimeout:
                        standard_bits = 0
                        alt_bits = 0
                        continue

                    if not data:
                        continue
                    events, standard_bits, alt_bits = decode_button_report(data, standard_bits, alt_bits)
                    if data == last_raw and not events:
                        continue

                    elapsed = time.monotonic() - start
                    decoded = ", ".join(event.label for event in events) if events else "sem mapeamento"
                    lines.append(f"{elapsed:05.2f}s raw={data.hex(' ')} decoded={decoded}")
                    last_raw = data
                    report_count += 1
            finally:
                try:
                    handle.releaseInterface(active_profile.control_interface)
                except Exception:
                    pass
                handle.close()

    if report_count == 0:
        lines.append(
            "Nenhum relatorio recebido. Se os botoes fisicos funcionam fora da captura, "
            "confirme se o leitor de botoes estava pausado e tente novamente."
        )
    elif report_count >= max_reports:
        lines.append("")
        lines.append(f"Captura encerrada no limite de {max_reports} relatorios.")

    destination.write_text("\n".join(lines), encoding="utf-8")
    lines.append("")
    lines.append(f"Arquivo salvo: {destination}")
    return "\n".join(lines)


def export_portable_release(destination: Path) -> None:
    source = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else app_root()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "build", "*.pyc"),
    )


class RadioButtonListener:
    def __init__(
        self,
        on_event: Callable[[str, bytes], None],
        on_error: Callable[[str], None],
        profile: DeviceProfile | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_error = on_error
        self._profile = profile or default_profile()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_bits = 0
        self._last_alt_bits = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def profile_id(self) -> str:
        return self._profile.profile_id

    def set_profile(self, profile: DeviceProfile) -> None:
        if profile.profile_id == self._profile.profile_id:
            return
        was_running = self.running
        if was_running:
            self.stop()
        self._profile = profile
        self._last_bits = 0
        self._last_alt_bits = 0
        if was_running:
            self.start()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PhilipsRadioButtons", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self._last_bits = 0
        self._last_alt_bits = 0

    def _run(self) -> None:
        try:
            with USB_LOCK:
                ctx = usb1.USBContext()
                dev = _find_device(ctx, self._profile)
                if dev is None:
                    ctx.close()
                    self._on_error("Philips Audio Set nao encontrado para botoes.")
                    return
                handle = dev.open()
            try:
                try:
                    handle.claimInterface(self._profile.control_interface)
                except usb1.USBError:
                    pass

                while not self._stop.is_set():
                    try:
                        data = bytes(handle.interruptRead(
                            self._profile.interrupt_endpoint,
                            self._profile.interrupt_packet_size,
                            timeout=250,
                        ))
                    except usb1.USBErrorTimeout:
                        self._last_bits = 0
                        self._last_alt_bits = 0
                        continue
                    except usb1.USBError as exc:
                        self._on_error(f"Leitura dos botoes falhou: {exc}")
                        break

                    if not data:
                        continue
                    events, self._last_bits, self._last_alt_bits = decode_button_report(
                        data,
                        self._last_bits,
                        self._last_alt_bits,
                    )
                    if events:
                        for event in events:
                            handle_media_button(event.bit, event.vk_code, self._profile)
                            self._on_event(event.label, data)
                    elif any(data):
                        self._on_event("Relatorio HID desconhecido", data)
            finally:
                try:
                    handle.releaseInterface(self._profile.control_interface)
                except Exception:
                    pass
                handle.close()
                ctx.close()
        except Exception as exc:
            self._on_error(str(exc))


def send_media_key(vk_code: int) -> None:
    user32 = ctypes.windll.user32
    keyeventf_keyup = 0x0002
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, keyeventf_keyup, 0)


def handle_media_button(bit: int, vk_code: int, profile: DeviceProfile) -> None:
    try:
        if bit == 4:
            toggle_windows_mute(profile)
            return
        if bit == 5:
            adjust_windows_volume(0.04, profile)
            return
        if bit == 6:
            adjust_windows_volume(-0.04, profile)
            return
    except Exception:
        pass
    send_media_key(vk_code)
