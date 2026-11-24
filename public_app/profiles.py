from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlPacket:
    request_type: int
    request: int
    value: int
    index: int
    payload: bytes


# Validated USB PC Link activation (FWM589 and other Philips Audio Set units).
PC_LINK_PACKETS: tuple[ControlPacket, ...] = (
    ControlPacket(0x40, 0x04, 0x0000, 0xA4EF, bytes.fromhex("01")),
    ControlPacket(0x40, 0x04, 0x0000, 0xA4F0, bytes.fromhex("FF")),
)


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
        pc_link_packets=PC_LINK_PACKETS,
        known_models=(
            # FWM / FWC micro systems
            "FWM589",
            "FW-M779 / FWM779",
            "FW-C577 / FWC577",
            "FWM922",
            "FWM992",
            # MCM micro systems with USB PC Link
            "MC-M530 / MCM530",
            "MC-M570 / MCM570",
            "MCM590",
            "MCM595",
            "other Philips USB PC Link systems detected as 0471:0111",
        ),
    ),
    # Variant with the HID control interface on MI_02 (no MI_03). The original
    # Philips uacflt.inf lists 0471:0110 with the same A4EF/A4F0 plug-in data.
    # Replies use the older A4F4/A4F2/B0A0 transport (sound_agent.send_frame_legacy).
    # Profile and transport contributed by geracerejo (github.com/geracerejo).
    DeviceProfile(
        profile_id="philips-uac3553b-0471-0110",
        display_name="Philips USB PC Link / UAC3553B (0110)",
        vendor_id=0x0471,
        product_id=0x0110,
        control_interface=2,
        control_hardware_id_suffix="MI_02",
        audio_interface=0,
        interrupt_endpoint=0x83,
        interrupt_packet_size=2,
        driver_package="winusb-philips-0110-mi02",
        pc_link_packets=PC_LINK_PACKETS,
        known_models=("FW-C577 / FWC577 (0471:0110)",),
    ),
)

# Names Windows gives the stereo's audio output (0111: "Philips Audio Set";
# some 0110 units show the chip name).
PHILIPS_OUTPUT_NAMES = ("Philips Audio Set", "UAC3553B")


def is_philips_output_name(name: str | None) -> bool:
    text = str(name or "").lower()
    return any(candidate.lower() in text for candidate in PHILIPS_OUTPUT_NAMES)


def uses_legacy_transport(product_id: int) -> bool:
    """0471:0110 units answer through A4F2/B0A0 and have no A544 mailbox hook."""
    return product_id == 0x0110


def find_profile_by_usb_id(vendor_id: int, product_id: int) -> DeviceProfile | None:
    for profile in SUPPORTED_PROFILES:
        if profile.vendor_id == vendor_id and profile.product_id == product_id:
            return profile
    return None


def default_profile() -> DeviceProfile:
    return SUPPORTED_PROFILES[0]
