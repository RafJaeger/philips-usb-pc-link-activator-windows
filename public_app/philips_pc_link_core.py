from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import comtypes
import usb1

import admin_tasks
import pnp_devices
from pycaw.pycaw import AudioUtilities, ERole

import platform

from pc_link_protocol import MediaInfo, PcLinkSession, is_pc_link_request
from profiles import (
    SUPPORTED_PROFILES,
    DeviceProfile,
    default_profile,
    find_profile_by_usb_id,
    is_philips_output_name,
    uses_legacy_transport,
)
from sound_agent import (
    SoundAgentState,
    handle_mcm_notification,
    initialize_mcm_sound_agent,
    install_mailbox_handler,
    is_device_status_report,
    is_sound_agent_notification,
    mailbox_handler_installed,
)

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

# How the radio's volume buttons act on the PC:
#   "windows": the Windows volume of the default output, with the on-screen
#              volume indicator (same as a keyboard volume key).
#   "philips": only the Philips Audio Set output volume.
VOLUME_MODES = ("windows", "philips")
_volume_mode = "windows"


def set_volume_mode(mode: str) -> None:
    global _volume_mode
    value = str(mode).strip().lower()
    _volume_mode = value if value in VOLUME_MODES else "windows"


def get_volume_mode() -> str:
    return _volume_mode

MEDIA_KEYS = {
    0: ("Play/Pause", 0xB3),
    1: ("Stop", 0xB2),
    2: ("Next Track", 0xB0),
    3: ("Previous Track", 0xB1),
    4: ("Mute", 0xAD),
    5: ("Volume Up", 0xAF),
    6: ("Volume Down", 0xAE),
}

# Actions the user can teach from a remote control. Each one reuses the same
# handling as a panel button: (action_id, label, bit, Windows media vk_code).
# The bit drives the volume/mute special-casing in handle_media_button.
LEARNABLE_ACTIONS: tuple[tuple[str, str, int, int], ...] = (
    ("playpause", "Play / Pause", 0, 0xB3),
    ("next", "Avancar", 2, 0xB0),
    ("prev", "Voltar", 3, 0xB1),
    ("stop", "Parar", 1, 0xB2),
    ("volup", "Volume +", 5, 0xAF),
    ("voldown", "Volume -", 6, 0xAE),
    ("mute", "Mudo", 4, 0xAD),
)

ACTION_LABELS = {action_id: label for action_id, label, _bit, _vk in LEARNABLE_ACTIONS}

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
    usb_connection_key: str | None = None


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


def desktop_dir() -> Path:
    """Real Desktop folder, including when Windows redirects it (e.g. OneDrive).

    Falls back to Documents and then the user's home folder, so saving a
    diagnostic never fails just because ``~/Desktop`` does not exist.
    """
    shell_folders = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_folders) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
        candidate = Path(os.path.expandvars(str(value)))
        if candidate.is_dir():
            return candidate
    except OSError:
        pass
    for candidate in (Path.home() / "Desktop", Path.home() / "Documents"):
        if candidate.is_dir():
            return candidate
    return Path.home()


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


def _admin(task: str, arguments: list[str] | None = None, failure: str = "Falha.") -> str:
    """Run a task as administrator in a copy of this app (UAC); raise on failure."""
    code, output = admin_tasks.run_as_admin(task, arguments)
    if code != 0:
        raise RuntimeError(output or failure)
    return output


EQUALIZER_INSTALLER = ("vendor", "equalizerapo", "EqualizerAPO-x64-1.4.2.exe")


def equalizer_installer_path() -> Path:
    return app_root().joinpath(*EQUALIZER_INSTALLER)


def install_equalizer() -> str:
    """Install Equalizer APO (bundled) and enable it on the Philips output."""
    installer = equalizer_installer_path()
    if not installer.exists():
        raise FileNotFoundError(f"Instalador do Equalizer APO ausente: {installer}")
    return _admin("equalizer-install", [str(installer)], "Falha ao ativar o equalizador.")


def remove_equalizer() -> str:
    """Restore the Philips output (Equalizer APO stays installed)."""
    return _admin("equalizer-remove", None, "Falha ao remover o equalizador.")


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


def _usb_prefix(profile: DeviceProfile) -> str:
    return f"USB\\VID_{profile.vendor_id:04X}&PID_{profile.product_id:04X}"


def _get_pnp_status(profile: DeviceProfile) -> PnpStatus:
    """What Windows knows about the radio, read directly through SetupAPI."""
    try:
        devices = pnp_devices.find_devices(_usb_prefix(profile))
    except OSError:
        devices = []
    control_prefix = profile.control_hardware_id.upper() + "\\"
    audio_prefix = profile.audio_hardware_prefix.upper() + "\\"
    controls = [d for d in devices if d.instance_id.upper().startswith(control_prefix)]
    # The node that is plugged in now wins over old ones from other USB ports.
    control = next((d for d in controls if d.present), controls[0] if controls else None)
    return PnpStatus(
        profile=profile,
        seen=bool(devices),
        present=any(d.present for d in devices),
        service=control.service if control else None,
        audio_present=any(d.present and d.instance_id.upper().startswith(audio_prefix) for d in devices),
    )


def detect_pnp_profile() -> PnpStatus | None:
    for profile in SUPPORTED_PROFILES:
        pnp = _get_pnp_status(profile)
        if pnp.seen:
            return pnp
    return None


def detect_profile() -> DeviceProfile | None:
    profile, _ = detect_usb_connection()
    return profile


def detect_usb_connection() -> tuple[DeviceProfile | None, str | None]:
    try:
        with usb1.USBContext() as ctx:
            for dev in ctx.getDeviceIterator(skip_on_error=True):
                profile = find_profile_by_usb_id(dev.getVendorID(), dev.getProductID())
                if profile is None:
                    continue
                key = (
                    f"{dev.getBusNumber()}:{dev.getDeviceAddress()}:"
                    f"{dev.getbcdDevice():04x}"
                )
                return profile, key
    except Exception:
        pass
    return None, None


def get_status() -> DeviceStatus:
    usb_profile, usb_connection_key = detect_usb_connection()
    pnp = detect_pnp_profile()
    profile = usb_profile or (pnp.profile if pnp else None)
    if profile is None:
        return DeviceStatus(False, None, None, False, False, False, None, None, "Nenhum perfil USB PC Link suportado encontrado.")

    present = bool(usb_profile) or (bool(pnp.present) if pnp else False)
    service = pnp.service if pnp else _read_usb_interface_service(profile)
    control_access = bool(usb_profile)
    audio = bool(pnp.audio_present) if pnp and pnp.present else find_philips_render_endpoint(profile) is not None
    winusb = admin_tasks.is_winusb(service)
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
        usb_connection_key=usb_connection_key,
    )


def decode_button_report(
    data: bytes,
    previous_standard_bits: int = 0,
    previous_alt_bits: int = 0,
) -> tuple[list[ButtonEvent], int, int]:
    if not data:
        return [], 0, 0

    if data[0] & ~SUPPORTED_MEDIA_MASK:
        return [], 0, 0

    standard_bits = data[0] & SUPPORTED_MEDIA_MASK
    events: list[ButtonEvent] = []
    standard_pressed = standard_bits & ~previous_standard_bits
    for bit, (name, vk_code) in MEDIA_KEYS.items():
        if standard_pressed & (1 << bit):
            events.append(ButtonEvent(bit, name, vk_code, "standard", data))

    return events, standard_bits, 0


def install_winusb_driver(profile: DeviceProfile | None = None) -> None:
    active_profile = profile or detect_profile() or default_profile()
    package = driver_dir(active_profile)
    if not package.exists() or not any(package.glob("*.inf")):
        interface = active_profile.control_hardware_id_suffix
        raise FileNotFoundError(
            f"Este app ainda nao traz o driver WinUSB para {active_profile.display_name} "
            f"({active_profile.vid_pid_label}). Instale o WinUSB somente na interface de controle "
            f"{interface} com o Zadig (Options > List All Devices, escolha a interface {interface}); "
            "nunca na MI_00 (audio)."
        )
    _admin("install-driver", [str(package)], "Falha ao instalar o driver WinUSB.")


def restore_control_driver(profile: DeviceProfile | None = None) -> None:
    _admin("restore-driver", None, "Falha ao restaurar o controle HID.")


def _find_device(ctx: usb1.USBContext, profile: DeviceProfile):
    for dev in ctx.getDeviceIterator(skip_on_error=True):
        if dev.getVendorID() == profile.vendor_id and dev.getProductID() == profile.product_id:
            return dev
    return None


def send_pc_link_enable(
    profile: DeviceProfile | None = None,
    *,
    device_family: str = "FWM",
    pc_link: PcLinkSession | None = None,
) -> str:
    active_profile = profile or detect_profile() or default_profile()
    family = str(device_family).strip().upper()
    if family not in {"FWM", "MCM"}:
        raise ValueError("Familia do aparelho invalida; use FWM ou MCM.")
    result = _send_pc_link_enable(active_profile, family)
    if pc_link is not None:
        pc_link.on_activation(mailbox_installed=family == "MCM")
    return result


def _send_pc_link_enable(active_profile: DeviceProfile, family: str) -> str:
    with USB_LOCK:
        with usb1.USBContext() as ctx:
            dev = _find_device(ctx, active_profile)
            if dev is None:
                raise RuntimeError(f"{active_profile.display_name} {active_profile.vid_pid_label} nao encontrado.")
            try:
                serial = dev.getSerialNumber()
            except Exception:
                serial = "desconhecido"
            revision = dev.getbcdDevice()
            handle = dev.open()
            try:
                lines = [
                    f"{active_profile.display_name} encontrado: bus={dev.getBusNumber()} "
                    f"addr={dev.getDeviceAddress()} serial={serial!r} revisao={revision:04x}",
                ]
                if family == "MCM":
                    if uses_legacy_transport(active_profile.product_id):
                        raise RuntimeError(
                            f"O modo MCM (Sound Agent) usa o chip 0471:0111; este aparelho e "
                            f"{active_profile.vid_pid_label}. Escolha FWM/FWC."
                        )
                    lines.append(
                        initialize_mcm_sound_agent(handle, revision=revision)
                    )
                    return "\n".join(lines)

                for packet in active_profile.pc_link_packets:
                    sent = handle.controlWrite(
                        packet.request_type,
                        packet.request,
                        packet.value,
                        packet.index,
                        packet.payload,
                        timeout=1000,
                    )
                    if sent != len(packet.payload):
                        raise RuntimeError(
                            f"Transferencia incompleta em 0x{packet.index:04x}: enviados "
                            f"{sent} de {len(packet.payload)} bytes."
                        )
                    lines.append(
                        f"PC Link: wIndex=0x{packet.index:04x} sent="
                        f"{sent}/{len(packet.payload)}"
                    )
                    time.sleep(0.03)
                return "\n".join(lines)
            finally:
                handle.close()


_FRIENDLY_NAME_KEY = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 14)  # PKEY_Device_FriendlyName


class LightEndpoint:
    """Render endpoint without pycaw's full property dump (that takes seconds)."""

    def __init__(self, dev, endpoint_id: str, friendly_name: str) -> None:
        self._dev = dev
        self.id = endpoint_id
        self.FriendlyName = friendly_name
        self._volume = None
        self._sessions = None

    @property
    def EndpointVolume(self):
        if self._volume is None:
            from pycaw.pycaw import IAudioEndpointVolume

            iface = self._dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
            self._volume = iface.QueryInterface(IAudioEndpointVolume)
        return self._volume

    @property
    def AudioSessionManager(self):
        if self._sessions is None:
            from pycaw.pycaw import IAudioSessionManager2

            iface = self._dev.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
            self._sessions = iface.QueryInterface(IAudioSessionManager2)
        return self._sessions


def _endpoint_friendly_name(dev) -> str:
    from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY

    store = dev.OpenPropertyStore(0)  # STGM_READ
    key = PROPERTYKEY()
    key.fmtid = comtypes.GUID(_FRIENDLY_NAME_KEY[0])
    key.pid = _FRIENDLY_NAME_KEY[1]
    value = store.GetValue(key)
    try:
        return str(value.GetValue() or "")
    finally:
        value.clear()


def find_philips_render_endpoint_fast() -> LightEndpoint | None:
    """Active render endpoint named 'Philips Audio Set', in milliseconds."""
    comtypes.CoInitialize()
    enumerator = AudioUtilities.GetDeviceEnumerator()
    collection = enumerator.EnumAudioEndpoints(0, 1)  # eRender, DEVICE_STATE_ACTIVE
    for index in range(collection.GetCount()):
        dev = collection.Item(index)
        try:
            name = _endpoint_friendly_name(dev)
        except Exception:  # noqa: BLE001
            continue
        if is_philips_output_name(name):
            return LightEndpoint(dev, dev.GetId(), name)
    return None


def find_philips_render_endpoint(profile: DeviceProfile | None = None):
    try:
        device = find_philips_render_endpoint_fast()
        if device is not None:
            return device
    except Exception:  # noqa: BLE001 - fall back to the full (slow) scan
        pass
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
            audio_id in property_text or is_philips_output_name(friendly_name)
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


def _default_endpoint_volume():
    comtypes.CoInitialize()
    device = AudioUtilities.GetSpeakers()
    if device is None:
        raise RuntimeError("Nenhuma saida de audio padrao no Windows.")
    return device.EndpointVolume


def get_default_volume() -> tuple[int, bool]:
    """Volume (0-100) and mute of the Windows default output."""
    endpoint = _default_endpoint_volume()
    return round(float(endpoint.GetMasterVolumeLevelScalar()) * 100), bool(endpoint.GetMute())


def set_default_volume(percent: int) -> None:
    endpoint = _default_endpoint_volume()
    endpoint.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(percent) / 100.0)), None)
    if percent > 0 and endpoint.GetMute():
        endpoint.SetMute(False, None)


def volume_key(direction: int) -> None:
    """Press the Windows volume key: changes the default output and shows the OSD."""
    if direction > 0:
        send_media_key(VK_VOLUME_UP)
    elif direction < 0:
        send_media_key(VK_VOLUME_DOWN)
    else:
        send_media_key(VK_VOLUME_MUTE)


class WindowsPcLinkHost:
    """Connect the unit's PC Link requests to Windows media and volume."""

    def __init__(self, media_monitor=None, profile: DeviceProfile | None = None) -> None:
        self.media_monitor = media_monitor
        self.profile = profile

    def media(self) -> MediaInfo | None:
        if self.media_monitor is None:
            return None
        return self.media_monitor.snapshot()

    def media_command(self, name: str) -> None:
        if self.media_monitor is not None:
            self.media_monitor.command(name)
            return
        fallback = {"play": 0xB3, "pause": 0xB3, "stop": 0xB2, "next": 0xB0, "previous": 0xB1}
        if name in fallback:
            send_media_key(fallback[name])

    def volume_step(self, direction: int) -> None:
        if get_volume_mode() == "philips":
            try:
                adjust_windows_volume(0.04 if direction > 0 else -0.04, self.profile)
                return
            except Exception:
                pass
        volume_key(direction)

    def toggle_mute(self) -> None:
        if get_volume_mode() == "philips":
            try:
                toggle_windows_mute(self.profile)
                return
            except Exception:
                pass
        volume_key(0)

    def set_volume(self, percent: int) -> None:
        if get_volume_mode() == "philips":
            try:
                set_windows_volume(percent / 100.0, False if percent > 0 else None, self.profile)
                return
            except Exception:
                pass
        set_default_volume(percent)

    def volume_state(self) -> tuple[int, bool] | None:
        if get_volume_mode() == "philips":
            state = get_windows_volume(self.profile)
            if state is None:
                return None
            return round(state[0] * 100), state[1]
        return get_default_volume()


def _is_legacy_handle(handle) -> bool:
    try:
        return uses_legacy_transport(handle.getDevice().getProductID())
    except Exception:  # noqa: BLE001 - test doubles and closed handles
        return False


def ensure_pc_link_mailbox(handle) -> list[str]:
    """Install the original setup block once, if the unit does not have it.

    Only 0471:0111 units use that block (A544 mailbox); 0471:0110 units reply
    through A4F2/B0A0 and must not receive code meant for the other chip.
    """
    if _is_legacy_handle(handle):
        return []
    if mailbox_handler_installed(handle):
        return []
    return install_mailbox_handler(handle)


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


def describe_usb_topology(profile: DeviceProfile) -> str:
    """Read-only libusb descriptor dump used by the diagnostics export."""
    lines = ["USB interfaces and endpoints (read-only descriptors):"]
    try:
        with USB_LOCK, usb1.USBContext() as ctx:
            found = False
            for dev in ctx.getDeviceIterator(skip_on_error=True):
                if (dev.getVendorID(), dev.getProductID()) != (profile.vendor_id, profile.product_id):
                    continue
                found = True
                lines.append(
                    f"Device {dev.getVendorID():04x}:{dev.getProductID():04x} bcdDevice="
                    f"{dev.getbcdDevice():04x} bcdUSB={dev.getbcdUSB():04x} bus="
                    f"{dev.getBusNumber()} address={dev.getDeviceAddress()}"
                )
                for config in dev.iterConfigurations():
                    lines.append(f"  Configuration {config.getConfigurationValue()}")
                    control_match = False
                    for interface in config:
                        for setting in interface:
                            number = setting.getNumber()
                            cls, subclass = setting.getClass(), setting.getSubClass()
                            role = {(1, 1): "AudioControl", (1, 2): "AudioStreaming"}.get(
                                (cls, subclass), "HID" if cls == 3 else "Other"
                            )
                            lines.append(
                                f"    Interface {number} (MI_{number:02X}) alt={setting.getAlternateSetting()} class=0x"
                                f"{cls:02x} subclass=0x{subclass:02x} protocol=0x"
                                f"{setting.getProtocol():02x} {role}"
                            )
                            for extra in setting.getExtra():
                                lines.append(f"      Extra descriptor: {bytes(extra).hex(' ')}")
                            for endpoint in setting:
                                address = endpoint.getAddress()
                                kind = ("control", "isochronous", "bulk", "interrupt")[endpoint.getAttributes() & 3]
                                direction = "IN (device to PC)" if address & 0x80 else "OUT (PC to device)"
                                lines.append(
                                    f"      Endpoint 0x{address:02x} {direction} {kind} maxPacketSize="
                                    f"{endpoint.getMaxPacketSize()} interval={endpoint.getInterval()}"
                                )
                                if number == profile.control_interface and (
                                    address == profile.interrupt_endpoint and kind == "interrupt"
                                ):
                                    control_match = True
                    lines.append(
                        f"  Expected control MI_{profile.control_interface:02X} / endpoint 0x"
                        f"{profile.interrupt_endpoint:02x}: "
                        + ("present" if control_match else "NOT FOUND in this configuration; review descriptors")
                    )
            if not found:
                lines.append("Device not visible through libusb; see Windows PnP/driver data below.")
    except Exception as exc:
        lines.append(f"Descriptor read failed: {type(exc).__name__}: {exc}")
    return "\n".join(lines)


def app_version() -> str:
    try:
        return (app_root() / "VERSION").read_text(encoding="utf-8").strip() or "desconhecida"
    except OSError:
        return "desconhecida"


def default_log_path() -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return desktop_dir() / f"philips-usb-pc-link-log-{timestamp}.txt"


def export_log(
    profile: DeviceProfile | None = None,
    *,
    destination: Path | str | None = None,
    device_family: str = "FWM",
    app_trace: str = "",
    pc_link: PcLinkSession | None = None,
) -> Path:
    """Write a single support log: app/device status, Windows PnP/driver data and USB descriptors."""
    active_profile = profile or detect_profile() or default_profile()
    destination = Path(destination) if destination is not None else default_log_path()

    status = get_status()
    pc_link_lines: list[str] = []
    if pc_link is not None:
        state = pc_link.state
        seen = ", ".join(
            f"0x{command:02X}x{count}" for command, count in sorted(state.requests_seen.items())
        )
        pc_link_lines = [
            "",
            "PC Link (protocolo PhilipsRemote):",
            f"  Aparelho fez pedidos PC Link: {'sim' if state.unit_active else 'nao'}",
            f"  Pedidos recebidos: {seen or 'nenhum'}",
            f"  Canal A544 pronto: {'sim' if state.mailbox_ready else 'nao'}",
            f"  Eventos do PC ligados: {'sim' if state.events_enabled else 'nao'}",
            f"  Controle de volume UAC (0x22): {'sim' if state.uac_volume_control else 'nao'}",
        ]
    header = [
        "Philips USB PC Link - log",
        f"App: {app_version()}",
        f"Data: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Windows: {platform.platform()}",
        f"Perfil: {active_profile.display_name} ({active_profile.vid_pid_label})",
        f"Modo selecionado: {str(device_family).upper()}",
        f"Botoes de volume: {get_volume_mode()}",
        f"Modelos suportados: {', '.join(active_profile.known_models)}",
        "",
        "Estado atual:",
        f"  Aparelho presente: {'sim' if status.present else 'nao'}",
        f"  Driver da Interface 3: {status.interface3_service or 'nao detectado'}",
        f"  Pronto para PC Link: {'sim' if status.interface3_ok else 'nao'}",
        f"  Audio presente no Windows: {'sim' if status.audio_present else 'nao'}",
        f"  Saida padrao do Windows: {'sim' if status.default_output else 'nao'}",
        f"  Resumo: {status.summary}",
        *pc_link_lines,
        "",
    ]
    if app_trace.strip():
        header.extend(("Log da sessao:", app_trace.strip(), ""))

    try:
        devices = pnp_devices.describe(pnp_devices.find_devices(_usb_prefix(active_profile)))
    except OSError as exc:
        devices = f"(nao foi possivel listar: {exc})"
    topology = describe_usb_topology(active_profile)

    body = "\n".join(header) + "Dispositivos PnP:\n" + devices + "\n\n" + topology + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding="utf-8")
    return destination


def capture_next_report(
    profile: DeviceProfile | None = None,
    *,
    timeout_s: float = 8.0,
    stop_event: threading.Event | None = None,
    device_family: str = "FWM",
    pc_link: PcLinkSession | None = None,
) -> bytes | None:
    """Wait for the next non-empty HID report from the control interface.

    Used by the "learn remote" flow: the caller asks the user to press one
    button, and this returns the raw bytes that button sends (or None on
    timeout). Returns the raw report so different remotes/models can be mapped.
    """
    active_profile = profile or detect_profile() or default_profile()
    family = str(device_family).strip().upper()
    if family not in {"FWM", "MCM"}:
        raise ValueError("Familia do aparelho invalida; use FWM ou MCM.")
    sound_agent_state = SoundAgentState()
    end = time.monotonic() + max(1.0, float(timeout_s))
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
                while time.monotonic() < end:
                    if stop_event is not None and stop_event.is_set():
                        return None
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
                        continue
                    if family == "MCM" and is_sound_agent_notification(data):
                        try:
                            handle_mcm_notification(handle, data, state=sound_agent_state)
                        except Exception:
                            pass
                        continue
                    # Requests from the unit (status polls, title queries)
                    # are answered, never learned as buttons.
                    if is_pc_link_request(data):
                        if pc_link is not None:
                            pc_link.handle_request(handle, data)
                        continue
                    if data and any(data) and not is_device_status_report(data):
                        return data
                return None
            finally:
                try:
                    handle.releaseInterface(active_profile.control_interface)
                except Exception:
                    pass
                handle.close()


class RadioButtonListener:
    def __init__(
        self,
        on_event: Callable[[str, bytes], None],
        on_error: Callable[[str], None],
        profile: DeviceProfile | None = None,
        pc_link: PcLinkSession | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_error = on_error
        self._profile = profile or default_profile()
        self.pc_link = pc_link
        self._pc_link_log_times: dict[str, float] = {}
        self._next_event_poll = 0.0
        self._buttons_enabled = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_bits = 0
        self._last_alt_bits = 0
        self._last_raw: bytes | None = None
        self._last_unknown_report: bytes | None = None
        self._last_unknown_report_time = 0.0
        self._device_family = "FWM"
        self._sound_agent_state = SoundAgentState()
        # Learned remote map: {raw report hex (no spaces): action_id}
        self._learned: dict[str, str] = {}

    def set_learned_map(self, mapping: dict[str, str] | None) -> None:
        self._learned = dict(mapping or {})

    def set_buttons_enabled(self, enabled: bool) -> None:
        """Buttons off: still answer the unit's PC Link requests, ignore presses."""
        self._buttons_enabled = bool(enabled)

    def set_device_family(self, device_family: str) -> None:
        family = str(device_family).strip().upper()
        if family not in {"FWM", "MCM"}:
            raise ValueError("Familia do aparelho invalida; use FWM ou MCM.")
        if family != self._device_family:
            self._sound_agent_state = SoundAgentState()
        self._device_family = family

    @property
    def active_profile(self) -> DeviceProfile:
        return self._profile

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
        self._last_raw = None
        self._last_unknown_report = None
        self._last_unknown_report_time = 0.0
        if was_running:
            self.start()

    def start(self) -> None:
        if self.running:
            return
        self._sound_agent_state = SoundAgentState()
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
        self._last_raw = None
        self._last_unknown_report = None
        self._last_unknown_report_time = 0.0

    def _dispatch_mcm_notification(self, handle, data: bytes) -> bool:
        """Handle one MCM request without letting a response error stop the reader."""
        try:
            response = handle_mcm_notification(
                handle,
                data,
                state=self._sound_agent_state,
            )
        except Exception as exc:
            self._on_error(
                "Sound Agent: falha ao responder ao pedido "
                f"{data.hex(' ')}: {exc}. Leitura mantida ativa."
            )
            return True

        if response is None:
            return False
        self._on_event(response, data)
        return True

    # Polls the unit repeats constantly; log them once in a while only.
    # 0x40/0x52 are fetched again at every marquee step of the display.
    _QUIET_PC_LINK_COMMANDS = ("(0x4A)", "(0x4B)", "(0x4C)", "(0x47)", "(0x49)", "(0x40)", "(0x52)")

    def _report_pc_link(self, message: str | None, data: bytes) -> None:
        if not message:
            return
        quiet = any(tag in message for tag in self._QUIET_PC_LINK_COMMANDS)
        if quiet and "falha" not in message and "pausadas" not in message:
            key = message.split(" respondido")[0]
            now = time.monotonic()
            if now - self._pc_link_log_times.get(key, -1e9) < 120:
                return
            self._pc_link_log_times[key] = now
        self._on_event(message, data)

    def _dispatch_pc_link(self, handle, data: bytes) -> bool:
        if self.pc_link is None or not is_pc_link_request(data):
            return False
        # Transport/volume commands are radio buttons: obey the buttons option.
        if not self._buttons_enabled and data[1] < 0x40 and data[1] not in (0x22, 0x23):
            return True
        try:
            message = self.pc_link.handle_request(handle, data)
        except Exception as exc:  # noqa: BLE001
            message = f"PC Link: falha inesperada em {data.hex(' ')}: {exc}"
        self._report_pc_link(message, data)
        for note in self.pc_link.take_notes():
            self._on_event(note, data)
        return True

    def _poll_pc_link_events(self, handle) -> None:
        if self.pc_link is None:
            return
        now = time.monotonic()
        if now < self._next_event_poll:
            return
        self._next_event_poll = now + 0.2
        try:
            messages = self.pc_link.poll_events(handle)
        except Exception as exc:  # noqa: BLE001
            messages = [f"PC Link: falha ao avisar o aparelho: {exc}"]
        for message in messages:
            self._on_event(message, b"")
        for note in self.pc_link.take_notes():
            self._on_event(note, b"")

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
                        self._last_raw = None
                        self._poll_pc_link_events(handle)
                        continue
                    except usb1.USBError as exc:
                        self._on_error(f"Leitura dos botoes falhou: {exc}")
                        break

                    self._poll_pc_link_events(handle)
                    if not data:
                        continue

                    if self._device_family == "MCM" and self._dispatch_mcm_notification(handle, data):
                        continue

                    # PhilipsRemote protocol: title/status requests and the
                    # unit's PC transport/volume commands (FWM and MCM).
                    if self._dispatch_pc_link(handle, data):
                        continue

                    # The MCM emits these status reports on its own. They are
                    # never remote-control presses, even if a stale learned map
                    # contains the same raw bytes.
                    if is_device_status_report(data):
                        now = time.monotonic()
                        if data != self._last_unknown_report or now - self._last_unknown_report_time > 15:
                            self._last_unknown_report = data
                            self._last_unknown_report_time = now
                            self._on_event("Status do aparelho (nao e botao)", data)
                        continue

                    if not self._buttons_enabled:
                        continue

                    # 1) A remote button the user has taught (learn mode).
                    action = self._learned.get(data.hex())
                    if action is not None:
                        if data != self._last_raw:
                            self._last_raw = data
                            label = perform_learned_action(action, self._profile)
                            self._on_event(f"Controle: {label}", data)
                        continue

                    # 2) The built-in panel media-bit format.
                    events, self._last_bits, self._last_alt_bits = decode_button_report(
                        data,
                        self._last_bits,
                        self._last_alt_bits,
                    )
                    if events:
                        self._last_raw = data
                        for event in events:
                            handle_media_button(event.bit, event.vk_code, self._profile)
                            self._on_event(event.label, data)
                    elif any(data):
                        now = time.monotonic()
                        if data != self._last_unknown_report or now - self._last_unknown_report_time > 15:
                            self._last_unknown_report = data
                            self._last_unknown_report_time = now
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
    # Volume/mute: in "windows" mode press the real Windows volume key, so the
    # default output changes and the Windows volume indicator appears.
    if bit in (4, 5, 6) and get_volume_mode() == "philips":
        try:
            if bit == 4:
                toggle_windows_mute(profile)
            elif bit == 5:
                adjust_windows_volume(0.04, profile)
            else:
                adjust_windows_volume(-0.04, profile)
            return
        except Exception:
            pass
    send_media_key(vk_code)


def perform_learned_action(action_id: str, profile: DeviceProfile) -> str:
    """Run a learned remote action and return its label (for the log)."""
    for aid, label, bit, vk_code in LEARNABLE_ACTIONS:
        if aid == action_id:
            handle_media_button(bit, vk_code, profile)
            return label
    return action_id
