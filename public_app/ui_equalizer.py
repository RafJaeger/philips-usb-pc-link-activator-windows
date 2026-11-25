"""Equalizer tab: Sound Agent style 10-band EQ and the chip's tone controls."""

from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from equalizer import (
    BAND_LABELS,
    FACTORY_PRESETS,
    GAIN_MAX_DB,
    GAIN_MIN_DB,
    EqualizerSettings,
    apo_status,
    clamp_gain,
    write_config,
)

STEPS_PER_DB = 2  # 0.5 dB per slider step


def _db_text(value: float) -> str:
    return f"{value:+.1f}".replace("+0.0", "0.0")


class EqualizerPanel(QWidget):
    """Emits install/remove requests; the window runs them with admin rights."""

    install_requested = Signal()
    remove_requested = Signal()
    message = Signal(str)

    def __init__(self, settings, *, chip_tone_factory: Callable[[], object | None]) -> None:
        super().__init__()
        self.settings = settings
        self._chip_tone_factory = chip_tone_factory
        self._chip = None
        self._loading = False
        self.eq = EqualizerSettings.from_json(self.settings.value("equalizer"))
        self.user_presets: dict[str, list[float]] = self._load_user_presets()
        self._write_timer = QTimer(self)
        self._write_timer.setSingleShot(True)
        self._write_timer.setInterval(150)
        self._write_timer.timeout.connect(self.apply_equalizer)

        # ---- Equalizer APO status ----
        self.status_label = QLabel("Verificando o equalizador...")
        self.status_label.setObjectName("eqStatus")
        self.install_button = QPushButton("Ativar equalizador")
        self.remove_button = QPushButton("Remover da saida Philips")
        self.remove_button.setObjectName("secondaryButton")
        self.install_button.clicked.connect(self.install_requested.emit)
        self.remove_button.clicked.connect(self._confirm_remove)
        self.status_label.setWordWrap(True)
        header_buttons = QHBoxLayout()
        header_buttons.addWidget(self.install_button)
        header_buttons.addWidget(self.remove_button)
        header_buttons.addStretch(1)
        header = QVBoxLayout()
        header.addWidget(self.status_label)
        header.addLayout(header_buttons)

        # ---- Sound Agent equalizer ----
        self.enabled_checkbox = QCheckBox("Equalizador ligado")
        self.enabled_checkbox.setChecked(self.eq.enabled)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(150)
        self.save_button = QPushButton("Salvar")
        self.delete_button = QPushButton("Apagar")
        self.reset_button = QPushButton("Reset")
        for button in (self.save_button, self.delete_button, self.reset_button):
            button.setObjectName("secondaryButton")
        presets_top = QHBoxLayout()
        presets_top.addWidget(self.enabled_checkbox)
        presets_top.addSpacing(16)
        presets_top.addWidget(QLabel("Predefinicao:"))
        presets_top.addWidget(self.preset_combo, 1)
        presets_buttons = QHBoxLayout()
        presets_buttons.addWidget(self.save_button)
        presets_buttons.addWidget(self.delete_button)
        presets_buttons.addWidget(self.reset_button)
        presets_buttons.addStretch(1)
        presets_row = QVBoxLayout()
        presets_row.addLayout(presets_top)
        presets_row.addLayout(presets_buttons)

        bands = QGridLayout()
        bands.setHorizontalSpacing(10)
        self.band_sliders: list[QSlider] = []
        self.band_values: list[QLabel] = []
        for column, label in enumerate(BAND_LABELS):
            value = QLabel("0.0")
            value.setAlignment(Qt.AlignCenter)
            value.setObjectName("bandValue")
            slider = QSlider(Qt.Vertical)
            slider.setRange(int(GAIN_MIN_DB * STEPS_PER_DB), int(GAIN_MAX_DB * STEPS_PER_DB))
            slider.setPageStep(STEPS_PER_DB)
            slider.setTickPosition(QSlider.TicksBothSides)
            slider.setTickInterval(int(6 * STEPS_PER_DB))
            slider.setMinimumHeight(150)
            slider.valueChanged.connect(lambda _v, index=column: self._band_changed(index))
            name = QLabel(label)
            name.setAlignment(Qt.AlignCenter)
            name.setObjectName("bandLabel")
            bands.addWidget(value, 0, column)
            bands.addWidget(slider, 1, column, Qt.AlignHCenter)
            bands.addWidget(name, 2, column)
            self.band_sliders.append(slider)
            self.band_values.append(value)
        scale = QVBoxLayout()
        for text in ("+12 dB", "", "0 dB", "", "-12 dB"):
            mark = QLabel(text)
            mark.setObjectName("bandLabel")
            scale.addWidget(mark, 1)
        bands_row = QHBoxLayout()
        bands_row.addLayout(scale)
        bands_row.addLayout(bands, 1)

        self.vbass_checkbox = QCheckBox("VBass")
        self.vbass_checkbox.setToolTip("Reforco de graves do Sound Agent")
        self.vbass_slider = QSlider(Qt.Horizontal)
        self.vbass_slider.setRange(0, 100)
        self.vbass_value = QLabel("")
        vbass_row = QHBoxLayout()
        vbass_row.addWidget(self.vbass_checkbox)
        vbass_row.addWidget(self.vbass_slider, 1)
        vbass_row.addWidget(self.vbass_value)

        eq_box = QGroupBox("Equalizador do Sound Agent (10 bandas)")
        eq_box.setObjectName("panel")
        eq_layout = QVBoxLayout()
        eq_layout.addLayout(presets_row)
        eq_layout.addLayout(bands_row)
        eq_layout.addLayout(vbass_row)
        eq_box.setLayout(eq_layout)

        # ---- Chip tone (works without Equalizer APO) ----
        self.bass_slider = QSlider(Qt.Horizontal)
        self.treble_slider = QSlider(Qt.Horizontal)
        for slider in (self.bass_slider, self.treble_slider):
            slider.setRange(int(GAIN_MIN_DB * STEPS_PER_DB), int(GAIN_MAX_DB * STEPS_PER_DB))
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(int(6 * STEPS_PER_DB))
        self.bass_value = QLabel("0.0 dB")
        self.treble_value = QLabel("0.0 dB")
        self.agc_checkbox = QCheckBox("Ganho automatico (AGC)")
        self.tone_reset = QPushButton("Zerar")
        self.tone_reset.setObjectName("secondaryButton")
        self.tone_status = QLabel("")
        self.tone_status.setObjectName("hintLabel")
        tone_grid = QGridLayout()
        tone_grid.addWidget(QLabel("Graves"), 0, 0)
        tone_grid.addWidget(self.bass_slider, 0, 1)
        tone_grid.addWidget(self.bass_value, 0, 2)
        tone_grid.addWidget(QLabel("Agudos"), 1, 0)
        tone_grid.addWidget(self.treble_slider, 1, 1)
        tone_grid.addWidget(self.treble_value, 1, 2)
        tone_row = QHBoxLayout()
        tone_row.addWidget(self.agc_checkbox)
        tone_row.addStretch(1)
        tone_row.addWidget(self.tone_reset)
        tone_box = QGroupBox("Tom do radio (no proprio chip, sem instalar nada)")
        tone_box.setObjectName("panel")
        tone_layout = QVBoxLayout()
        tone_layout.addLayout(tone_grid)
        tone_layout.addLayout(tone_row)
        tone_layout.addWidget(self.tone_status)
        tone_box.setLayout(tone_layout)

        layout = QVBoxLayout()
        layout.addLayout(header)
        layout.addWidget(eq_box)
        layout.addWidget(tone_box)
        layout.addStretch(1)
        self.setLayout(layout)

        self.enabled_checkbox.stateChanged.connect(self._eq_changed)
        self.vbass_checkbox.stateChanged.connect(self._eq_changed)
        self.vbass_slider.valueChanged.connect(self._eq_changed)
        self.preset_combo.currentIndexChanged.connect(self._preset_selected)
        self.save_button.clicked.connect(self._save_preset)
        self.delete_button.clicked.connect(self._delete_preset)
        self.reset_button.clicked.connect(lambda: self._load_curve("Normal", FACTORY_PRESETS["Normal"]))
        self.bass_slider.valueChanged.connect(self._tone_changed)
        self.treble_slider.valueChanged.connect(self._tone_changed)
        self.agc_checkbox.stateChanged.connect(self._agc_changed)
        self.tone_reset.clicked.connect(self._reset_tone)

        self._fill_presets()
        self._show_settings()
        self.refresh_status()
        self.connect_chip()

    # ----- presets -----

    def _load_user_presets(self) -> dict[str, list[float]]:
        try:
            data = json.loads(self.settings.value("equalizer_presets") or "{}")
        except (TypeError, ValueError):
            return {}
        return {str(k): [clamp_gain(g) for g in v][:10] for k, v in data.items() if isinstance(v, list)}

    def _fill_presets(self) -> None:
        self._loading = True
        self.preset_combo.clear()
        for name in FACTORY_PRESETS:
            self.preset_combo.addItem(name, ("factory", name))
        for name in sorted(self.user_presets):
            self.preset_combo.addItem(f"{name} (meu)", ("user", name))
        self.preset_combo.addItem("Personalizado", ("custom", ""))
        self._select_preset(self.eq.preset)
        self._loading = False

    def _select_preset(self, name: str) -> None:
        for index in range(self.preset_combo.count()):
            kind, preset = self.preset_combo.itemData(index)
            if preset == name or (kind == "custom" and name == "Personalizado"):
                self.preset_combo.setCurrentIndex(index)
                break
        kind, _name = self.preset_combo.currentData() or ("custom", "")
        self.delete_button.setEnabled(kind == "user")

    def _preset_selected(self, _index: int) -> None:
        if self._loading:
            return
        kind, name = self.preset_combo.currentData() or ("custom", "")
        self.delete_button.setEnabled(kind == "user")
        if kind == "factory":
            self._load_curve(name, FACTORY_PRESETS[name])
        elif kind == "user":
            self._load_curve(name, self.user_presets[name])

    def _load_curve(self, name: str, gains) -> None:
        self.eq.gains = [clamp_gain(g) for g in gains]
        self.eq.preset = name
        self._show_settings()
        self._schedule_write()

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Salvar predefinicao", "Nome da predefinicao:")
        name = (name or "").strip()[:24]
        if not ok or not name:
            return
        if name in FACTORY_PRESETS:
            QMessageBox.information(self, "Salvar predefinicao", "Escolha um nome diferente das predefinicoes de fabrica.")
            return
        self.user_presets[name] = list(self.eq.gains)
        self.settings.setValue("equalizer_presets", json.dumps(self.user_presets))
        self.eq.preset = name
        self._fill_presets()
        self._persist()
        self.message.emit(f"Predefinicao '{name}' salva.")

    def _delete_preset(self) -> None:
        kind, name = self.preset_combo.currentData() or ("custom", "")
        if kind != "user":
            return
        self.user_presets.pop(name, None)
        self.settings.setValue("equalizer_presets", json.dumps(self.user_presets))
        self.eq.preset = "Personalizado"
        self._fill_presets()
        self._persist()

    # ----- equalizer -----

    def _show_settings(self) -> None:
        self._loading = True
        for slider, label, gain in zip(self.band_sliders, self.band_values, self.eq.gains):
            slider.setValue(int(round(gain * STEPS_PER_DB)))
            label.setText(_db_text(gain))
        self.enabled_checkbox.setChecked(self.eq.enabled)
        self.vbass_checkbox.setChecked(self.eq.vbass)
        self.vbass_slider.setValue(int(self.eq.vbass_level))
        self.vbass_slider.setEnabled(self.eq.vbass)
        self.vbass_value.setText(f"{int(self.eq.vbass_level)}%")
        self._select_preset(self.eq.preset)
        self._loading = False

    def _band_changed(self, index: int) -> None:
        gain = self.band_sliders[index].value() / STEPS_PER_DB
        self.band_values[index].setText(_db_text(gain))
        if self._loading:
            return
        self.eq.gains[index] = clamp_gain(gain)
        if self.eq.preset != "Personalizado":
            self.eq.preset = "Personalizado"
            self._loading = True
            self._select_preset("Personalizado")
            self._loading = False
        self._schedule_write()

    def _eq_changed(self, *_args) -> None:
        if self._loading:
            return
        self.eq.enabled = self.enabled_checkbox.isChecked()
        self.eq.vbass = self.vbass_checkbox.isChecked()
        self.eq.vbass_level = float(self.vbass_slider.value())
        self.vbass_slider.setEnabled(self.eq.vbass)
        self.vbass_value.setText(f"{int(self.eq.vbass_level)}%")
        self._schedule_write()

    def _schedule_write(self) -> None:
        self._persist()
        self._write_timer.start()

    def _persist(self) -> None:
        self.settings.setValue("equalizer", self.eq.to_json())

    def apply_equalizer(self) -> None:
        status = apo_status()
        if not status.installed or status.config_path is None:
            return
        try:
            write_config(self.eq, status.config_path)
        except OSError as exc:
            self.message.emit(f"Equalizador: nao foi possivel gravar a configuracao ({exc}).")

    last_ready = False

    def refresh_status(self) -> None:
        status = apo_status()
        self.last_ready = status.ready
        self.status_label.setText(status.summary)
        self.install_button.setText("Reativar equalizador" if status.ready else "Ativar equalizador")
        self.remove_button.setEnabled(status.on_philips)
        if status.installed:
            self.apply_equalizer()

    def _confirm_remove(self) -> None:
        answer = QMessageBox.question(
            self,
            "Remover equalizador",
            "Tirar o equalizador da saida Philips? O Equalizer APO continua instalado "
            "(pode ser desinstalado pelo Windows).",
        )
        if answer == QMessageBox.Yes:
            self.remove_requested.emit()

    # ----- chip tone -----

    def connect_chip(self, *, keep_sliders: bool = False) -> bool:
        """(Re)open the chip's bass/treble controls on the current Philips output.

        keep_sliders=True keeps the user's slider positions (they are applied
        right after); otherwise the sliders show what the chip has now.
        """
        try:
            self._chip = self._chip_tone_factory()
        except Exception as exc:  # noqa: BLE001
            self._chip = None
            self.tone_status.setText(f"Controles do chip indisponiveis: {exc}")
        available = bool(self._chip is not None and self._chip.available)
        # Sliders stay usable: a move made while the radio is away is applied
        # as soon as it comes back (see _tone_changed).
        self.agc_checkbox.setEnabled(bool(available and self._chip.has_agc))
        if not available:
            self.tone_status.setText("Saida Philips nao encontrada. Conecte o radio em PC LINK.")
            return False
        self.tone_status.setText("Aplicado direto no chip do radio.")
        if keep_sliders:
            return True
        self._loading = True
        bass = self._chip.bass or 0.0
        treble = self._chip.treble or 0.0
        self.bass_slider.setValue(int(round(bass * STEPS_PER_DB)))
        self.treble_slider.setValue(int(round(treble * STEPS_PER_DB)))
        self.bass_value.setText(f"{_db_text(bass)} dB")
        self.treble_value.setText(f"{_db_text(treble)} dB")
        self.agc_checkbox.setChecked(bool(self._chip.agc))
        self._loading = False
        return True

    def radio_returned(self) -> None:
        """The stereo's audio output came back: reconnect and re-apply the sliders."""
        if self.connect_chip(keep_sliders=True):
            self._apply_tone(self.bass_slider.value() / STEPS_PER_DB, self.treble_slider.value() / STEPS_PER_DB)

    def _tone_changed(self, *_args) -> None:
        bass = self.bass_slider.value() / STEPS_PER_DB
        treble = self.treble_slider.value() / STEPS_PER_DB
        self.bass_value.setText(f"{_db_text(bass)} dB")
        self.treble_value.setText(f"{_db_text(treble)} dB")
        if self._loading:
            return
        if self._apply_tone(bass, treble):
            return
        # The radio's audio output is recreated by Windows whenever the stereo
        # leaves/returns to PC Link or the cable is replugged: reconnect once.
        self.connect_chip(keep_sliders=True)
        if not self._apply_tone(bass, treble):
            self.tone_status.setText("Saida Philips nao encontrada. Conecte o radio em PC LINK.")

    def _apply_tone(self, bass: float, treble: float) -> bool:
        if self._chip is None or not self._chip.available:
            return False
        try:
            self._chip.set_bass(bass)
            self._chip.set_treble(treble)
            return True
        except Exception:  # noqa: BLE001 - stale endpoint after replug
            return False

    def _agc_changed(self, *_args) -> None:
        if self._loading or self._chip is None:
            return
        try:
            self._chip.set_agc(self.agc_checkbox.isChecked())
        except Exception as exc:  # noqa: BLE001
            self.tone_status.setText(f"Falha ao ajustar o AGC: {exc}")

    def _reset_tone(self) -> None:
        self.bass_slider.setValue(0)
        self.treble_slider.setValue(0)
