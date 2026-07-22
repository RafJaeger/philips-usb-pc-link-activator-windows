from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlPacket:
    request_type: int
    request: int
    value: int
    index: int
    payload: bytes


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    display_name: str
    vendor_id: int
    product_id: int
    control_interface: int
    control_hardware_id_suffix: str
    audio_interface: int
    interrupt_endpoint: int
    interrupt_packet_size: int
    driver_package: str
    pc_link_packets: tuple[ControlPacket, ...]
    known_models: tuple[str, ...]

    @property
    def vid_pid_label(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"

    @property
    def control_hardware_id(self) -> str:
        return (
            f"USB\\VID_{self.vendor_id:04X}&PID_{self.product_id:04X}"
            f"&{self.control_hardware_id_suffix}"
        )

    @property
    def audio_hardware_prefix(self) -> str:
        return f"USB\\VID_{self.vendor_id:04X}&PID_{self.product_id:04X}&MI_{self.audio_interface:02X}"


SUPPORTED_PROFILES: tuple[DeviceProfile, ...] = (
    DeviceProfile(
        profile_id="philips-uac3553b-0471-0111",
        display_name="Philips USB PC Link / Philips Audio Set",
        vendor_id=0x0471,
        product_id=0x0111,
        control_interface=3,
        control_hardware_id_suffix="MI_03",
        audio_interface=0,
        interrupt_endpoint=0x83,
        interrupt_packet_size=2,
        driver_package="winusb-philips-mi03",
        pc_link_packets=(
            ControlPacket(0x40, 0x04, 0x0000, 0xA4EF, bytes([0x01])),
            ControlPacket(0x40, 0x04, 0x0000, 0xA4F0, bytes([0xFF])),
        ),
        known_models=(
            "FWM589",
            "FW-M779 / FWM779",
            "MC-M530 / MCM530",
            "MC-M570 / MCM570",
            "MCM590",
            "MCM595",
            "other Philips USB PC Link systems detected as 0471:0111",
        ),
    ),
)


def find_profile_by_usb_id(vendor_id: int, product_id: int) -> DeviceProfile | None:
    for profile in SUPPORTED_PROFILES:
        if profile.vendor_id == vendor_id and profile.product_id == product_id:
            return profile
    return None


def default_profile() -> DeviceProfile:
    return SUPPORTED_PROFILES[0]
