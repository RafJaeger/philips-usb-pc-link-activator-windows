"""Sound Agent style 10-band equalizer, processed by Equalizer APO.

The original Philips Sound Agent 2 (mc500cpl.exe) had a 10-band graphic
equalizer (31 Hz ... 16 kHz), factory presets Rock / Pop / Jazz / Classic,
user presets and VBass. Its audio processing ran in QSound's 32-bit kernel
driver, which cannot run on 64-bit Windows 10/11.

Here the same equalizer is applied by Equalizer APO (GPL-2.0), installed
only on the Philips Audio Set output. The app writes the curve to its own
file in the Equalizer APO config folder (writable by all users); Equalizer
APO reloads it as soon as it changes.
"""

from __future__ import annotations

import json
import winreg
from dataclasses import dataclass, field
from pathlib import Path

from profiles import PHILIPS_OUTPUT_NAMES, is_philips_output_name

BANDS_HZ = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
BAND_LABELS = ("31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K")
GAIN_MIN_DB = -12.0
GAIN_MAX_DB = 12.0
BAND_Q = 1.41  # one octave per band, as in a graphic equalizer

# Factory presets (dB per band). The original Sound Agent values were in
# registry files of the CD that could not be extracted, so these are the
# usual curves for each style.
FACTORY_PRESETS: dict[str, tuple[float, ...]] = {
    "Normal": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Rock": (5, 4, 3, 1, -1, -1, 1, 3, 4, 5),
    "Pop": (-1, 1, 3, 4, 3, 0, -1, -1, 1, 2),
    "Jazz": (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "Classic": (4, 3, 2, 1, 0, 0, 0, 1, 2, 3),
    "Graves": (7, 6, 4, 2, 0, 0, 0, 0, 0, 0),
    "Vocal": (-2, -1, 0, 2, 4, 4, 3, 1, 0, -1),
}

VBASS_FREQ_HZ = 80
VBASS_MAX_DB = 9.0

CONFIG_FILE_NAME = "philips-pc-link.txt"
INCLUDE_LINE = f"Include: {CONFIG_FILE_NAME}"
# Equalizer APO "Device:" line: one or more name patterns separated by ";"
# ("Philips Audio Set" on 0471:0111, "UAC3553B" on some 0471:0110 units).
DEVICE_PATTERN = "; ".join(PHILIPS_OUTPUT_NAMES)
EAPO_REG_PATH = r"SOFTWARE\EqualizerAPO"


def clamp_gain(value: float) -> float:
    return round(max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(value))), 1)


@dataclass
class EqualizerSettings:
    enabled: bool = True
    gains: list[float] = field(default_factory=lambda: [0.0] * len(BANDS_HZ))
    vbass: bool = False
    vbass_level: float = 50.0  # percent of VBASS_MAX_DB
    preset: str = "Normal"

    def to_json(self) -> str:
        return json.dumps(
            {
                "enabled": self.enabled,
                "gains": [clamp_gain(g) for g in self.gains],
                "vbass": self.vbass,
                "vbass_level": self.vbass_level,
                "preset": self.preset,
            }
        )

    @classmethod
    def from_json(cls, raw: str | None) -> "EqualizerSettings":
        try:
            data = json.loads(raw or "")
        except (TypeError, ValueError):
            return cls()
        gains = [clamp_gain(g) for g in (data.get("gains") or [])][: len(BANDS_HZ)]
        gains += [0.0] * (len(BANDS_HZ) - len(gains))
        return cls(
            enabled=bool(data.get("enabled", True)),
            gains=gains,
            vbass=bool(data.get("vbass", False)),
            vbass_level=max(0.0, min(100.0, float(data.get("vbass_level", 50.0)))),
            preset=str(data.get("preset", "Normal")),
        )


def vbass_gain(settings: EqualizerSettings) -> float:
    if not settings.vbass:
        return 0.0
    return round(VBASS_MAX_DB * settings.vbass_level / 100.0, 1)


def preamp_db(settings: EqualizerSettings) -> float:
    """Headroom so boosted bands never clip (Sound Agent did the same)."""
    peak = max([0.0] + [clamp_gain(g) for g in settings.gains])
    peak = max(peak, vbass_gain(settings) + max(0.0, clamp_gain(settings.gains[0])))
    return -round(peak, 1) or 0.0  # never "-0.0"


def build_config(settings: EqualizerSettings) -> str:
    """Equalizer APO configuration for the Philips output only."""
    lines = [
        "# Gerado pelo Philips USB PC Link Activator - nao edite a mao.",
        "# Equalizador estilo Sound Agent 2 (10 bandas) na saida Philips.",
        f"Device: {DEVICE_PATTERN}",
    ]
    if settings.enabled:
        lines.append(f"Preamp: {preamp_db(settings):.1f} dB")
        for frequency, gain in zip(BANDS_HZ, settings.gains):
            gain = clamp_gain(gain)
            if gain:
                lines.append(f"Filter: ON PK Fc {frequency} Hz Gain {gain:.1f} dB Q {BAND_Q:.2f}")
        boost = vbass_gain(settings)
        if boost:
            lines.append(f"Filter: ON LS Fc {VBASS_FREQ_HZ} Hz Gain {boost:.1f} dB")
    else:
        lines.append("# Equalizador desligado no app.")
    # Restore the default scope for anything included after this file.
    lines.append("Device: all")
    return "\n".join(lines) + "\n"


def _read_reg_str(name: str) -> str | None:
    for view in (winreg.KEY_WOW64_64KEY, 0):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, EAPO_REG_PATH, 0, winreg.KEY_READ | view) as key:
                value, _kind = winreg.QueryValueEx(key, name)
                return str(value)
        except OSError:
            continue
    return None


def config_dir() -> Path | None:
    configured = _read_reg_str("ConfigPath")
    if configured:
        return Path(configured)
    install = _read_reg_str("InstallPath")
    if install:
        return Path(install) / "config"
    return None


@dataclass(frozen=True)
class ApoStatus:
    installed: bool
    on_philips: bool
    include_ready: bool
    config_path: Path | None

    @property
    def ready(self) -> bool:
        return self.installed and self.on_philips and self.include_ready

    @property
    def summary(self) -> str:
        if not self.installed:
            return "Equalizador nao instalado. Clique em Ativar equalizador."
        if not self.on_philips:
            return "Equalizer APO instalado, mas nao ativo na saida Philips. Clique em Ativar equalizador."
        if not self.include_ready:
            return "Equalizer APO ativo, mas sem a configuracao do app. Clique em Ativar equalizador."
        return "Equalizador ativo na saida Philips."


def philips_has_apo(endpoint_ids: list[str] | None = None) -> bool:
    """True when Equalizer APO is installed on a Philips Audio Set output."""
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as render:
            index = 0
            while True:
                try:
                    guid = winreg.EnumKey(render, index)
                except OSError:
                    break
                index += 1
                if endpoint_ids and guid.lower() not in {e.lower() for e in endpoint_ids}:
                    continue
                try:
                    with winreg.OpenKey(render, guid + r"\Properties") as props:
                        name, _ = winreg.QueryValueEx(props, "{b3f8fa53-0004-438e-9003-51a46e139bfc},6")
                except OSError:
                    continue
                if not is_philips_output_name(name):
                    continue
                try:
                    with winreg.OpenKey(render, guid + r"\FxProperties") as fx:
                        friendly, _ = winreg.QueryValueEx(fx, "{b725f130-47ef-101a-a5f1-02608c9eebac},10")
                except OSError:
                    continue
                if "equalizer apo" in str(friendly).lower():
                    return True
    except OSError:
        return False
    return False


def apo_status() -> ApoStatus:
    folder = config_dir()
    installed = bool(folder and folder.parent.joinpath("EqualizerAPO.dll").exists())
    include_ready = False
    if folder is not None:
        try:
            text = (folder / "config.txt").read_text(encoding="utf-8", errors="ignore")
            include_ready = INCLUDE_LINE.lower() in text.lower()
        except OSError:
            include_ready = False
    return ApoStatus(installed, philips_has_apo() if installed else False, include_ready, folder)


def write_config(settings: EqualizerSettings, folder: Path | None = None) -> Path:
    """Write the curve (no admin needed: the config folder is user-writable)."""
    target_dir = folder or config_dir()
    if target_dir is None:
        raise RuntimeError("Equalizer APO nao esta instalado.")
    target = Path(target_dir) / CONFIG_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(build_config(settings), encoding="utf-8")
    temporary.replace(target)
    # 0.6.1 wrote config.txt with "\r\r\n": Equalizer APO then ignored the include.
    try:
        from equalizer_setup import repair_line_endings

        repair_line_endings(Path(target_dir) / "config.txt")
    except Exception:  # noqa: BLE001 - never block saving the curve
        pass
    return target
