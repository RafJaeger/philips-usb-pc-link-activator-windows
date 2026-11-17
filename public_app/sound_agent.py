from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


class SoundAgentError(RuntimeError):
    """Raised when the MCM does not complete the Sound Agent handshake."""


KNOWN_DEVICE_STATUS_REPORTS = frozenset((b"\x80\x4a", b"\x80\x80"))


def is_device_status_report(data: bytes) -> bool:
    """Return True for autonomous MCM status reports, never button presses."""
    return bytes(data) in KNOWN_DEVICE_STATUS_REPORTS


def is_sound_agent_notification(data: bytes) -> bool:
    raw = bytes(data)
    return len(raw) >= 2 and raw[0] == 0x80 and 0x60 <= raw[1] <= 0x6F


@dataclass
class SoundAgentState:
    effects_enabled: bool = False
    ui_visible: bool = False
    parameter_mask: int = 0x60


def _b0_state_packet(state: SoundAgentState) -> bytes:
    """Build the original Sound Agent state packet (base/effects/UI flags)."""
    flags = 0x01
    if state.effects_enabled:
        flags |= 0x04
    if state.ui_visible:
        flags |= 0x02
    return bytes((0x02, 0xB0, flags))


# Initialization recovered from the Philips USB audio filter shipped on the
# MCM595 installation CD (0471:0111, revision 0402).
LEGACY_MCM_SETUP: tuple[tuple[int, bytes], ...] = (
    (0xA4EF, bytes.fromhex("01")),
    (0xA4F0, bytes.fromhex("FF")),
    (
        0xA556,
        bytes.fromhex(
            "80 81 e8 12 b0 e1 02 21 90 1a e9 44 a5 98 1f 28 "
            "c8 43 a5 c8 f2 a4 e0 30 c8 a1 b0 e8 44 a5 c8 f4 "
            "a4 bc 84 a5 e8 43 a5 98 05 e0 20 c8 a1 b0 89 88 "
            "b9 80 81 e8 c0 a4 98 03 c8 ee a4 e8 44 a5 98 15 "
            "e8 43 a5 90 10 e8 f2 a4 e9 44 a5 01 01 01 79 90 "
            "04 28 c8 44 a5 89 88 ba"
        ),
    ),
    (0xA0DE, bytes.fromhex("56 A5")),
    (0xA0DD, bytes.fromhex("BC")),
    (0xA087, bytes.fromhex("87 A5")),
    (0xA086, bytes.fromhex("BC")),
    # This is a real zero-length transfer, not an end marker.
    (0xB08C, b""),
)


def _write_memory(handle, address: int, payload: bytes, *, timeout_ms: int) -> int:
    sent = handle.controlWrite(
        0x40,
        0x04,
        0x0000,
        address,
        payload,
        timeout=timeout_ms,
    )
    if sent != len(payload):
        raise SoundAgentError(
            f"Transferencia incompleta em 0x{address:04X}: "
            f"enviados {sent} de {len(payload)} bytes."
        )
    return sent


def _read_memory(handle, address: int, length: int, *, timeout_ms: int) -> bytes:
    data = bytes(
        handle.controlRead(
            0xC0,
            0x05,
            0x0000,
            address,
            length,
            timeout=timeout_ms,
        )
    )
    if len(data) != length:
        raise SoundAgentError(
            f"Leitura incompleta em 0x{address:04X}: "
            f"recebidos {len(data)} de {length} bytes."
        )
    return data


def _send_framed_packet(
    handle,
    packet: bytes,
    *,
    timeout_ms: int,
    ready_timeout_s: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> int:
    if not packet or packet[0] + 1 != len(packet):
        raise SoundAgentError("Pacote Sound Agent com tamanho invalido.")
    _write_memory(handle, 0xA4F4, packet, timeout_ms=timeout_ms)
    _write_memory(handle, 0xA544, bytes((len(packet),)), timeout_ms=timeout_ms)

    deadline = monotonic() + max(0.05, float(ready_timeout_s))
    polls = 0
    last_state = 0
    while True:
        last_state = _read_memory(handle, 0xA544, 1, timeout_ms=timeout_ms)[0]
        polls += 1
        if last_state == 0:
            return polls
        if monotonic() >= deadline:
            raise SoundAgentError(
                "O MCM recebeu um pacote Sound Agent, mas nao confirmou o processamento: "
                f"A544 permaneceu em 0x{last_state:02X}."
            )
        sleep(0.01)


def _read_hid_mailbox(handle, length: int, *, timeout_ms: int) -> bytes:
    """Mirror UacbGetHidBuffer: A4EE is byte 0, A4C1 the remaining bytes."""
    if length < 1:
        return b""
    first = _read_memory(handle, 0xA4EE, 1, timeout_ms=timeout_ms)
    if length == 1:
        return first
    return first + _read_memory(handle, 0xA4C1, length - 1, timeout_ms=timeout_ms)


def handle_mcm_notification(
    handle,
    report: bytes,
    *,
    state: SoundAgentState | None = None,
    timeout_ms: int = 1000,
    ready_timeout_s: float = 1.25,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> str | None:
    """Handle the complete 0x60-0x6F control dispatcher from Sound Agent 2.

    The optional QSound/equalizer UI is not present in this app. For its name
    and preset queries, Sound Agent's own documented failure packet (0x81) is
    returned so the MCM never waits indefinitely for a response.
    """
    raw = bytes(report)
    if len(raw) < 2 or raw[0] != 0x80:
        return None
    command = raw[1]
    if command < 0x60 or command > 0x6F:
        return None
    active_state = state if state is not None else SoundAgentState()

    fixed_responses = {
        0x6B: bytes.fromhex("03 6B A1 01"),
        0x6C: bytes.fromhex("02 6C 82"),
        0x6D: bytes.fromhex("06 6D 82 03 04 05 06"),
    }
    optional_error_responses = {
        0x64: bytes.fromhex("02 64 81"),
        0x65: bytes.fromhex("02 65 81"),
        0x67: bytes.fromhex("02 64 81"),
        0x68: bytes.fromhex("02 65 81"),
        0x69: bytes.fromhex("02 69 81"),
        0x6A: bytes.fromhex("02 6A 81"),
    }
    mailbox_lengths = {0x61: 2, 0x62: 2, 0x63: 3, 0x64: 2, 0x65: 2, 0x66: 3, 0x6F: 2}
    mailbox = b""
    if command in mailbox_lengths:
        mailbox = _read_hid_mailbox(
            handle,
            mailbox_lengths[command],
            timeout_ms=timeout_ms,
        )

    packet = fixed_responses.get(command) or optional_error_responses.get(command)
    detail = ""
    if command == 0x61:
        requested_visible = bool(mailbox[1])
        detail = f"pedido para {'abrir' if requested_visible else 'fechar'} a interface"
        # In the Philips application, creating/destroying the Sound Agent pane
        # updates member +0x160 and consequently sends B0 again with flag 0x02.
        # Reproduce that state transition even though our UI is not the old pane.
        if requested_visible != active_state.ui_visible:
            active_state.ui_visible = requested_visible
            packet = _b0_state_packet(active_state)
    elif command == 0x62:
        active_state.effects_enabled = bool(mailbox[1])
        packet = _b0_state_packet(active_state)
        detail = f"estado de efeitos atualizado para {int(active_state.effects_enabled)}"
    elif command == 0x63:
        detail = "ajuste de efeito opcional recebido"
        parameter = mailbox[1] & 0x0F
        if parameter <= 7 and active_state.parameter_mask & (1 << parameter):
            if parameter < 5:
                packet = bytes((0x03, 0xB1, parameter, mailbox[2]))
            else:
                packet = bytes((0x02, 0xB1, parameter))
    elif command == 0x66:
        detail = "ajuste de equalizador opcional recebido"
        band = min(mailbox[1] & 0x0F, 9)
        if active_state.parameter_mask & 0x80:
            packet = bytes((0x03, 0xB1, band + 0x10, mailbox[2]))
    elif command == 0x6E:
        detail = "notificacao reservada recebida"
    elif command == 0x6F:
        active_state.parameter_mask = mailbox[1]
        detail = f"mascara de parametros=0x{active_state.parameter_mask:02X}"

    if command == 0x6B:
        _write_memory(handle, 0xA535, b"\x01", timeout_ms=timeout_ms)
    if packet is not None:
        polls = _send_framed_packet(
            handle,
            packet,
            timeout_ms=timeout_ms,
            ready_timeout_s=ready_timeout_s,
            sleep=sleep,
            monotonic=monotonic,
        )
        return (
            f"Sound Agent: comando 0x{command:02X} respondido com "
            f"{packet.hex(' ')}; confirmado em {polls} leitura(s)"
            + (f" ({detail})" if detail else "")
        )
    return (
        f"Sound Agent: comando 0x{command:02X} processado"
        + (f" ({detail})" if detail else "")
        + (f" mailbox={mailbox.hex(' ')}" if mailbox else "")
    )


def initialize_mcm_sound_agent(
    handle,
    *,
    revision: int | None = None,
    timeout_ms: int = 1000,
    ready_timeout_s: float = 1.25,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> str:
    """Run the MCM startup used by the original Philips Sound Agent 2.

    The old kernel filter first installed the UAC3553B setup block. Sound Agent
    then announced the PC client twice, read the device state, queued a B0
    command and waited for the unit to acknowledge it by clearing A544.
    """
    trace = [
        "MCM Sound Agent 2: iniciando protocolo Philips"
        + (f" (revisao USB {revision:04X})" if revision is not None else "")
    ]

    for address, payload in LEGACY_MCM_SETUP:
        sent = _write_memory(handle, address, payload, timeout_ms=timeout_ms)
        trace.append(
            f"MCM setup: wIndex=0x{address:04X} sent={sent}/{len(payload)}"
        )
        sleep(0.02)

    states: list[int] = []
    for attempt in range(1, 3):
        _write_memory(handle, 0xA4EF, b"\x01", timeout_ms=timeout_ms)
        _write_memory(handle, 0xA4F0, b"\x02", timeout_ms=timeout_ms)
        state = _read_memory(handle, 0xA4C1, 1, timeout_ms=timeout_ms)[0]
        states.append(state)
        trace.append(
            f"Sound Agent: anuncio {attempt}/2 aceito; A4C1=0x{state:02X}"
        )
        sleep(0.02)

    # Packet format is [payload length, command, flags]. The executable ships
    # with base flag 0x01; optional Sound Agent effects add bits 0x02/0x04.
    # Those effects are unrelated to releasing normal PC Link audio.
    packet = bytes((0x02, 0xB0, 0x01))
    trace.append("Sound Agent: pacote B0 enviado (02 B0 01); aguardando MCM")
    polls = _send_framed_packet(
        handle,
        packet,
        timeout_ms=timeout_ms,
        ready_timeout_s=ready_timeout_s,
        sleep=sleep,
        monotonic=monotonic,
    )

    _write_memory(handle, 0xA535, b"\x01", timeout_ms=timeout_ms)
    trace.append(
        f"Sound Agent: MCM confirmou o pacote apos {polls} leitura(s); A544=0x00"
    )
    trace.append("Sound Agent: sessao finalizada com A535=0x01")
    trace.append(
        "MCM pronto para teste de audio; estados A4C1="
        + ", ".join(f"0x{state:02X}" for state in states)
    )
    return "\n".join(trace)
