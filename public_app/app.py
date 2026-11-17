from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime

from PySide6.QtCore import QTimer, Signal, QObject, QSettings, QEventLoop
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from media_session import MediaMonitor
from software_volume import SoftwareVolume
from pc_link_protocol import DEFAULT_DISPLAY_WIDTH, MAX_TEXT_CHARS, PcLinkSession, display_text
from philips_pc_link_core import (
    LEARNABLE_ACTIONS,
    RadioButtonListener,
    WindowsPcLinkHost,
    app_root,
    app_version,
    capture_next_report,
    default_log_path,
    detect_profile,
    ensure_pc_link_mailbox,
    find_philips_render_endpoint_fast,
    export_log,
    get_start_with_windows,
    get_status,
    install_winusb_driver,
    restore_control_driver,
    set_default_output,
    set_start_with_windows,
    set_volume_mode,
    send_pc_link_enable,
)

SINGLE_INSTANCE_NAME = "PhilipsUSBPCLinkActivator"
MCM_RETRY_SECONDS = 10.0


def notify_existing_instance() -> bool:
    """If another copy is already running, ask it to show its window and return True.

    Uses a local socket instead of a system mutex: a left-over lock from a crash
    is cleared automatically (QLocalServer.removeServer), so re-opening the app
    never requires logging out or rebooting.
    """
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_NAME)
    if socket.waitForConnected(300):
        try:
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(300)
        finally:
            socket.disconnectFromServer()
        return True
    return False


class Bridge(QObject):
    log = Signal(str)
    status = Signal(str)
    refresh = Signal()
    device_status = Signal(object)
    capture_done = Signal()
    sound_agent_window = Signal(bool)
    mcm_retry = Signal(int)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Philips USB PC Link Activator for Windows")
        self.resize(860, 620)
        self.app_icon = QIcon(str(app_root() / "assets" / "app.ico"))
        self.setWindowIcon(self.app_icon)

        self.bridge = Bridge()
        self.bridge.log.connect(self.log)
        self.bridge.status.connect(self.set_status_text)
        self.bridge.refresh.connect(self.poll_status)
        self.bridge.device_status.connect(self.apply_status)
        self.bridge.sound_agent_window.connect(self.set_sound_agent_window)
        self.bridge.mcm_retry.connect(self.schedule_mcm_retry)

        self.title_label = QLabel("Philips USB PC Link Activator for Windows")
        self.title_label.setObjectName("titleLabel")
        self.profile_label = QLabel("Perfil: detectando aparelho USB PC Link...")
        self.profile_label.setObjectName("profileLabel")
        self.status_label = QLabel("Verificando Philips Audio Set...")
        self.status_label.setObjectName("statusLabel")

        self.settings = QSettings("RafJaeger", "PhilipsUSBPCLinkActivator")

        self.family_combo = QComboBox()
        self.family_combo.addItem("FWM / FWC (padrao)", "FWM")
        self.family_combo.addItem("MCM (Sound Agent 2)", "MCM")
        saved_family = str(self.settings.value("device_family", "FWM")).upper()
        saved_index = self.family_combo.findData(saved_family)
        self.family_combo.setCurrentIndex(saved_index if saved_index >= 0 else 0)

        self.install_button = QPushButton("Instalar Driver WinUSB")
        self.activate_button = QPushButton("Ativar PC Link Agora")
        self.default_button = QPushButton("Usar Como Saida Padrao")
        self.learn_button = QPushButton("Aprender Controle")
        self.log_button = QPushButton("Gerar Log")
        self.restore_button = QPushButton("Restaurar Controle HID")
        self.about_button = QPushButton("Sobre")
        self.buttons_checkbox = QCheckBox("Ativar botoes do radio")
        self.buttons_checkbox.setChecked(True)
        self.auto_checkbox = QCheckBox("Auto-reativar")
        self.auto_checkbox.setChecked(True)
        self.startup_checkbox = QCheckBox("Iniciar com o Windows")
        self.startup_checkbox.setChecked(get_start_with_windows())
        self.display_checkbox = QCheckBox("Mostrar musica no visor do radio")
        self.display_checkbox.setChecked(self._setting_bool("show_title", True))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(4, MAX_TEXT_CHARS)
        self.width_spin.setSuffix(" caracteres")
        self.width_spin.setToolTip(
            "Quantos caracteres cabem no visor do radio. FWM589: 8. "
            "Textos maiores rolam automaticamente."
        )
        try:
            saved_width = int(self.settings.value("display_width", DEFAULT_DISPLAY_WIDTH))
        except (TypeError, ValueError):
            saved_width = DEFAULT_DISPLAY_WIDTH
        self.width_spin.setValue(max(4, min(MAX_TEXT_CHARS, saved_width)))
        self.volume_combo = QComboBox()
        self.volume_combo.addItem("Volume do Windows (padrao)", "windows")
        self.volume_combo.addItem("Somente a saida Philips", "philips")
        saved_volume = str(self.settings.value("volume_mode", "windows")).lower()
        volume_index = self.volume_combo.findData(saved_volume)
        self.volume_combo.setCurrentIndex(volume_index if volume_index >= 0 else 0)
        set_volume_mode(self.selected_volume_mode())
        self.soft_volume_checkbox = QCheckBox("Corrigir volume do Windows na saida Philips")
        self.soft_volume_checkbox.setToolTip(
            "O chip do radio ignora o volume do USB Audio (so o mudo funciona). "
            "Ligado: o app aplica o volume do Windows no proprio som, como o volume do YouTube."
        )
        self.soft_volume_checkbox.setChecked(self._setting_bool("software_volume", True))
        self.now_playing_label = QLabel("Tocando agora: -")
        self.now_playing_label.setObjectName("nowPlayingLabel")

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        actions = QHBoxLayout()
        actions.addWidget(self.install_button)
        actions.addWidget(self.activate_button)
        actions.addWidget(self.default_button)

        advanced = QHBoxLayout()
        advanced.addWidget(self.learn_button)
        advanced.addWidget(self.log_button)
        advanced.addWidget(self.restore_button)
        advanced.addWidget(self.about_button)
        advanced.addStretch(1)

        options = QHBoxLayout()
        options.addWidget(self.auto_checkbox)
        options.addWidget(self.buttons_checkbox)
        options.addWidget(self.startup_checkbox)
        options.addStretch(1)

        media_options = QHBoxLayout()
        media_options.addWidget(self.display_checkbox)
        media_options.addWidget(QLabel("Visor:"))
        media_options.addWidget(self.width_spin)
        media_options.addSpacing(16)
        media_options.addWidget(QLabel("Botoes de volume:"))
        media_options.addWidget(self.volume_combo)
        media_options.addStretch(1)

        device_box = QGroupBox("Aparelho")
        device_box.setObjectName("panel")
        device_layout = QVBoxLayout()
        device_layout.addWidget(self.profile_label)
        family_row = QHBoxLayout()
        family_row.addWidget(QLabel("Modelo / protocolo:"))
        family_row.addWidget(self.family_combo, 1)
        device_layout.addLayout(family_row)
        device_layout.addWidget(self.status_label)
        device_layout.addLayout(actions)
        device_box.setLayout(device_layout)

        controls_box = QGroupBox("Controles")
        controls_box.setObjectName("panel")
        controls_layout = QVBoxLayout()
        controls_layout.addLayout(options)
        controls_layout.addLayout(media_options)
        controls_layout.addWidget(self.soft_volume_checkbox)
        controls_layout.addWidget(self.now_playing_label)
        controls_layout.addLayout(advanced)
        controls_box.setLayout(controls_layout)

        layout = QVBoxLayout()
        layout.addWidget(self.title_label)
        layout.addWidget(device_box)
        layout.addWidget(controls_box)
        layout.addWidget(self.log_box, 1)

        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.setStyleSheet(
            """
            QMainWindow { background: #111318; color: #f3f4f6; }
            QLabel { color: #f3f4f6; font-size: 14px; }
            #titleLabel {
                font-size: 24px;
                font-weight: 700;
                padding: 6px 2px;
            }
            #profileLabel { color: #cbd5e1; }
            #nowPlayingLabel { color: #93c5fd; padding: 2px 2px; }
            #statusLabel {
                background: #1c212b;
                border: 1px solid #303847;
                border-radius: 6px;
                padding: 12px;
                font-weight: 600;
            }
            QPushButton {
                background: #2563eb;
                color: white;
                border: 0;
                border-radius: 6px;
                padding: 10px 14px;
                font-weight: 600;
            }
            QPushButton:disabled { background: #4b5563; color: #cbd5e1; }
            QCheckBox { color: #e5e7eb; spacing: 8px; }
            QComboBox {
                background: #1c212b;
                color: #f3f4f6;
                border: 1px solid #303847;
                border-radius: 5px;
                padding: 7px 9px;
            }
            QGroupBox#panel {
                color: #cbd5e1;
                border: 1px solid #303847;
                border-radius: 8px;
                margin-top: 12px;
                padding: 14px 10px 10px 10px;
                font-weight: 600;
            }
            QGroupBox#panel::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QTextEdit {
                background: #0b0d12;
                color: #d1d5db;
                border: 1px solid #303847;
                border-radius: 6px;
                padding: 8px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
            """
        )

        self.install_button.clicked.connect(self.install_driver)
        self.activate_button.clicked.connect(self.activate_now)
        self.default_button.clicked.connect(self.set_default_output_now)
        self.learn_button.clicked.connect(self.learn_remote_now)
        self.log_button.clicked.connect(self.export_log_now)
        self.restore_button.clicked.connect(self.restore_control_driver_now)
        self.about_button.clicked.connect(self.show_about)
        self.buttons_checkbox.stateChanged.connect(self.sync_button_listener)
        self.startup_checkbox.stateChanged.connect(self.sync_startup)
        self.display_checkbox.stateChanged.connect(self.on_display_changed)
        self.width_spin.valueChanged.connect(self.on_display_width_changed)
        self.soft_volume_checkbox.stateChanged.connect(self.on_software_volume_changed)
        self.volume_combo.currentIndexChanged.connect(self.on_volume_mode_changed)

        initial_profile = detect_profile()
        # Read from the USB thread; never touch the Qt widget from there.
        self._show_title = self.display_checkbox.isChecked()
        self._display_width = self.width_spin.value()
        self.media_monitor = MediaMonitor()
        self.pc_link_host = WindowsPcLinkHost(self.media_monitor, initial_profile)
        self.pc_link = PcLinkSession(
            self.pc_link_host,
            ensure_mailbox=ensure_pc_link_mailbox,
            display_enabled=lambda: self._show_title,
            display_width=lambda: self._display_width,
        )
        self.radio_listener = RadioButtonListener(
            self.on_radio_button,
            self.on_button_error,
            initial_profile,
            pc_link=self.pc_link,
        )
        self.radio_listener.set_buttons_enabled(self.buttons_checkbox.isChecked())
        self.radio_listener.set_device_family(self.selected_device_family())
        self._learned_map = self._load_learned_map()
        self.radio_listener.set_learned_map(self._learned_map)
        self.last_activation = 0.0
        self.last_ready = False
        self._next_mcm_retry_at = 0.0
        self._last_auto_log = 0.0
        self._operation_lock = threading.Lock()
        self._operation_running = False
        self._pc_link_active = False
        self._buttons_backoff_until = 0.0
        self._last_button_error = ""
        self._status_running = False
        self._control_ready = False
        self._force_quit = False
        self._tray_notice_shown = False
        self._last_usb_connection_key = None
        self.family_combo.currentIndexChanged.connect(self.on_device_family_changed)
        self.setup_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_status)
        self.timer.start(10000)

        self.media_monitor.start()
        if self.media_monitor.last_error:
            self.log(self.media_monitor.last_error)
        self.software_volume = SoftwareVolume(
            find_philips_render_endpoint_fast,
            on_status=self.bridge.log.emit,
        )
        self.software_volume.set_enabled(self.soft_volume_checkbox.isChecked())
        self.now_playing_timer = QTimer(self)
        self.now_playing_timer.timeout.connect(self.refresh_now_playing)
        self.now_playing_timer.start(1500)

        self.poll_status()
        self.sync_button_listener()

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "sim"}
        return bool(value)

    def selected_volume_mode(self) -> str:
        return str(self.volume_combo.currentData() or "windows")

    def on_volume_mode_changed(self, _index: int | None = None) -> None:
        mode = self.selected_volume_mode()
        self.settings.setValue("volume_mode", mode)
        set_volume_mode(mode)
        if mode == "windows":
            self.log("Botoes de volume do radio: volume do Windows (saida padrao).")
        else:
            self.log("Botoes de volume do radio: somente a saida Philips.")

    def on_display_changed(self, _state: int | None = None) -> None:
        enabled = self.display_checkbox.isChecked()
        self._show_title = enabled
        self.settings.setValue("show_title", enabled)
        self.log("Musica no visor do radio: " + ("ligada." if enabled else "desligada."))
        self.sync_button_listener()

    def on_software_volume_changed(self, _state: int | None = None) -> None:
        enabled = self.soft_volume_checkbox.isChecked()
        self.settings.setValue("software_volume", enabled)
        self.software_volume.set_enabled(enabled)

    def on_display_width_changed(self, value: int) -> None:
        self._display_width = int(value)
        self.settings.setValue("display_width", int(value))

    def refresh_now_playing(self) -> None:
        info = self.media_monitor.snapshot()
        if not info.title and not info.artist:
            text = "Tocando agora: -"
        else:
            parts = [display_text(info.title, 60) or "(sem titulo)"]
            if info.artist:
                parts.append(display_text(info.artist, 40))
            state = {"playing": "tocando", "paused": "pausado", "stopped": "parado"}.get(info.status, "")
            text = "Tocando agora: " + " - ".join(parts) + (f"  [{state}]" if state else "")
        if self.now_playing_label.text() != text:
            self.now_playing_label.setText(text)

    def setup_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        if self.app_icon.isNull():
            self.app_icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray_icon.setIcon(self.app_icon)
        self.tray_icon.setToolTip("Philips USB PC Link Activator for Windows")

        menu = QMenu(self)
        open_action = QAction("Abrir", self)
        activate_action = QAction("Ativar PC Link", self)
        quit_action = QAction("Sair", self)

        open_action.triggered.connect(self.restore_from_tray)
        activate_action.triggered.connect(lambda: self.activate_now(auto=False))
        quit_action.triggered.connect(self.quit_from_tray)

        menu.addAction(open_action)
        menu.addAction(activate_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def closeEvent(self, event) -> None:
        if self._force_quit:
            # Real quit (tray "Sair"): stop everything AND end the process.
            try:
                self.radio_listener.stop()
            except Exception:
                pass
            try:
                self.media_monitor.stop()
            except Exception:
                pass
            try:
                # Returns every app to its own mixer level before quitting.
                self.software_volume.set_enabled(False)
                self.software_volume.stop()
            except Exception:
                pass
            try:
                self.tray_icon.hide()
            except Exception:
                pass
            super().closeEvent(event)
            # quitOnLastWindowClosed is False, so without this the event loop
            # would keep running and the process would linger in Task Manager,
            # holding the single-instance lock and blocking re-opening.
            QApplication.quit()
            return

        # Clicking the window's X only hides to the tray; the app keeps
        # reactivating PC Link. Use the tray "Sair" to close it for real.
        event.ignore()
        self.hide()
        if not self._tray_notice_shown and self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Philips USB PC Link Activator for Windows",
                "O app continua na bandeja. Clique com o botao direito no icone e 'Sair' para fechar.",
                QSystemTrayIcon.Information,
                3000,
            )
            self._tray_notice_shown = True

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        self._force_quit = True
        self.close()

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_from_tray()


    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{stamp}] {message}")

    def set_status_text(self, message: str) -> None:
        self.status_label.setText(message)

    def set_sound_agent_window(self, visible: bool) -> None:
        if visible:
            self.restore_from_tray()
        else:
            self.hide()

    def schedule_mcm_retry(self, delay_ms: int) -> None:
        QTimer.singleShot(max(0, int(delay_ms)), self.retry_mcm_activation)

    def retry_mcm_activation(self) -> None:
        if (
            self.selected_device_family() == "MCM"
            and self.auto_checkbox.isChecked()
            and self._control_ready
            and not self._operation_running
            and not self._pc_link_active
        ):
            self.activate_now(auto=True)

    def selected_device_family(self) -> str:
        return str(self.family_combo.currentData() or "FWM").upper()

    def on_device_family_changed(self, _index: int | None = None) -> None:
        family = self.selected_device_family()
        self.settings.setValue("device_family", family)
        self.radio_listener.set_device_family(family)
        self._pc_link_active = False
        self.last_activation = 0.0
        self._next_mcm_retry_at = 0.0
        if family == "MCM":
            self.log("Modo MCM selecionado: sera usada a negociacao do Sound Agent 2.")
        else:
            self.log("Modo FWM/FWC selecionado: sera usada a ativacao PC Link padrao.")
        if self._control_ready and self.auto_checkbox.isChecked() and not self._operation_running:
            self.activate_now(auto=True)
        else:
            self.poll_status()

    def run_bg(self, label: str, fn, log_result: bool = True, exclusive: bool = False) -> None:
        def worker() -> None:
            lock_acquired = False
            if exclusive:
                lock_acquired = self._operation_lock.acquire(blocking=False)
                if not lock_acquired:
                    self.bridge.log.emit("Operacao USB em andamento; aguarde terminar.")
                    return
                self._operation_running = True
                self.bridge.refresh.emit()
            self.bridge.log.emit(label)
            try:
                result = fn()
                if result and log_result:
                    self.bridge.log.emit(str(result))
            except Exception as exc:
                self.bridge.log.emit(f"Erro: {exc}")
            finally:
                if lock_acquired:
                    self._operation_running = False
                    self._operation_lock.release()
                self.bridge.refresh.emit()

        threading.Thread(target=worker, daemon=True).start()

    def poll_status(self) -> None:
        if self._status_running:
            return
        self._status_running = True
        def worker() -> None:
            try:
                status = get_status()
                self.bridge.device_status.emit(status)
            except Exception as exc:
                self.bridge.status.emit(f"Falha ao verificar status: {exc}")
            finally:
                self._status_running = False

        threading.Thread(target=worker, daemon=True).start()

    def apply_status(self, status) -> None:
        try:
            self.bridge.status.emit(status.summary)
        except Exception as exc:
            self.bridge.status.emit(f"Falha ao aplicar status: {exc}")
            return

        if status.profile:
            self.radio_listener.set_profile(status.profile)
            self.pc_link_host.profile = status.profile
            self.profile_label.setText(
                f"Perfil: {status.profile.display_name} ({status.profile.vid_pid_label})"
            )
        else:
            self.profile_label.setText("Perfil: nenhum aparelho suportado detectado")

        driver_installed = status.interface3_service == "WinUSB"
        operation_running = self._operation_running
        can_install = bool(status.profile and status.present and not driver_installed and not operation_running)
        self.install_button.setEnabled(can_install)
        if operation_running:
            self.install_button.setText("Aguarde...")
        elif not status.present:
            self.install_button.setText("Aguardando Aparelho")
        elif driver_installed:
            self.install_button.setText("Driver WinUSB Instalado")
        else:
            self.install_button.setText("Instalar Driver WinUSB")

        self.family_combo.setEnabled(not operation_running)
        self.activate_button.setEnabled(status.interface3_ok and not operation_running and not self._pc_link_active)
        active_text = "Sound Agent Ativo" if self.selected_device_family() == "MCM" else "PC Link Ativo"
        self.activate_button.setText(active_text if status.interface3_ok and self._pc_link_active else "Ativar PC Link Agora")
        self.default_button.setEnabled(status.audio_present and not operation_running)
        self.learn_button.setEnabled(status.interface3_ok and not operation_running)
        self.log_button.setEnabled(not operation_running)
        self.restore_button.setEnabled(bool(status.present and status.interface3_service and not operation_running))
        self.default_button.setText("Saida Padrao Ativa" if status.default_output else "Usar Como Saida Padrao")

        ready = bool(status.interface3_ok)
        connection_changed = bool(
            ready
            and status.usb_connection_key
            and status.usb_connection_key != self._last_usb_connection_key
        )
        if connection_changed:
            self._pc_link_active = False
            self._next_mcm_retry_at = 0.0
            # A reconnected unit lost the setup block and its PC Link state.
            self.pc_link.reset()
        self._last_usb_connection_key = status.usb_connection_key
        self._control_ready = ready
        if not ready:
            self._pc_link_active = False
            self._next_mcm_retry_at = 0.0
        if ready and not operation_running:
            self.sync_button_listener()
        elif self.radio_listener.running:
            self.radio_listener.stop()
            self.bridge.log.emit("Leitor de botoes pausado: aguardando o USB PC Link reconectar.")

        was_ready = self.last_ready
        if ready and not was_ready:
            self.bridge.log.emit("Philips pronto.")
        self.last_ready = ready

        if self.auto_checkbox.isChecked() and ready and not operation_running:
            now = time.time()
            if self.selected_device_family() == "MCM":
                retry_due = self._next_mcm_retry_at > 0 and now >= self._next_mcm_retry_at
                should_activate = not self._pc_link_active and (
                    not was_ready or connection_changed or retry_due
                )
            else:
                should_activate = not was_ready or connection_changed or now - self.last_activation > 25
            if should_activate:
                self.last_activation = now
                self.activate_now(auto=True)

    def install_driver(self) -> None:
        restart_buttons = self.radio_listener.running

        def install_safely() -> str:
            if restart_buttons:
                self.radio_listener.stop()
            install_winusb_driver()
            deadline = time.time() + 30
            last_summary = ""
            while time.time() < deadline:
                status = get_status()
                last_summary = status.summary
                if status.interface3_ok:
                    return "Driver WinUSB instalado e interface de controle pronta."
                time.sleep(1)
            return (
                "Driver instalado, mas o Windows ainda nao liberou a interface para controle. "
                f"Ultimo status: {last_summary or 'sem status'}. "
                "Tire e coloque o cabo USB, selecione PC Link novamente e aguarde o app atualizar."
            )

        self.run_bg(
            "Instalando WinUSB somente na Interface 3. Aceite o UAC.",
            install_safely,
            exclusive=True,
        )

    def activate_now(self, auto: bool = False) -> None:
        label = "Auto-reativando PC Link..." if auto else "Ativando PC Link..."
        family = self.selected_device_family()
        self.last_activation = time.time()
        log_result = True
        if auto and time.time() - self._last_auto_log < 120:
            log_result = False
        if auto and log_result:
            self._last_auto_log = time.time()

        restart_listener = self.radio_listener.running
        resume_listener = self.listener_required()

        def activate_safely() -> str:
            if restart_listener:
                self.radio_listener.stop()
            try:
                result = send_pc_link_enable(device_family=family, pc_link=self.pc_link)
            except Exception:
                self._pc_link_active = False
                if family == "MCM":
                    self._next_mcm_retry_at = time.time() + MCM_RETRY_SECONDS
                    self.bridge.log.emit(
                        f"Sound Agent: nova tentativa automatica em {MCM_RETRY_SECONDS:.0f} segundos."
                    )
                    self.bridge.mcm_retry.emit(int(MCM_RETRY_SECONDS * 1000))
                raise
            else:
                self._pc_link_active = True
                if family == "MCM":
                    self._next_mcm_retry_at = 0.0
                return result
            finally:
                if resume_listener and self._control_ready:
                    self.radio_listener.start()

        self.run_bg(label, activate_safely, log_result=log_result, exclusive=True)

    def set_default_output_now(self) -> None:
        self.run_bg("Definindo Philips como saida padrao do Windows...", set_default_output)

    def export_log_now(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar log como",
            str(default_log_path()),
            "Texto (*.txt);;Todos os arquivos (*)",
        )
        if not chosen:
            self.log("Log cancelado.")
            return

        family = self.selected_device_family()
        session_trace = self.log_box.toPlainText()

        def export_safely() -> str:
            saved = export_log(
                destination=chosen,
                device_family=family,
                app_trace=session_trace,
                pc_link=self.pc_link,
            )
            return f"Log salvo: {saved}"

        self.run_bg("Gerando log...", export_safely)

    # ----- Aprender controle -----

    def _learned_key(self) -> str:
        return f"learned/{self.radio_listener.active_profile.profile_id}"

    def _load_learned_map(self) -> dict:
        raw = self.settings.value(self._learned_key())
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _capture_one(self, timeout_s: float) -> bytes | None:
        """Capture one report from the GUI thread without freezing the UI."""
        result: dict[str, object] = {}
        family = self.selected_device_family()

        def worker() -> None:
            try:
                result["data"] = capture_next_report(
                    timeout_s=timeout_s,
                    device_family=family,
                    pc_link=self.pc_link,
                )
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)
            self.bridge.capture_done.emit()

        loop = QEventLoop()
        self.bridge.capture_done.connect(loop.quit)
        threading.Thread(target=worker, daemon=True).start()
        loop.exec()
        self.bridge.capture_done.disconnect(loop.quit)
        if "error" in result:
            raise RuntimeError(str(result["error"]))
        return result.get("data")  # type: ignore[return-value]

    def learn_remote_now(self) -> None:
        if not self._control_ready:
            QMessageBox.information(
                self,
                "Aprender Controle",
                "Ative o PC Link antes de aprender o controle (o aparelho precisa estar pronto).",
            )
            return
        if not self._operation_lock.acquire(blocking=False):
            self.log("Operacao USB em andamento; aguarde terminar.")
            return

        was_listening = self.radio_listener.running
        self.radio_listener.stop()
        self._operation_running = True
        self.bridge.refresh.emit()
        learned = dict(self._learned_map)
        try:
            for action_id, label, _bit, _vk in LEARNABLE_ACTIONS:
                box = QMessageBox(self)
                box.setWindowTitle("Aprender Controle")
                box.setText(
                    f"Botao: {label}\n\n"
                    "Clique em Capturar e aperte esse botao no controle.\n"
                    "Pular mantem o que ja estava; Encerrar para a captura."
                )
                capture = box.addButton("Capturar", QMessageBox.AcceptRole)
                skip = box.addButton("Pular", QMessageBox.RejectRole)
                stop = box.addButton("Encerrar", QMessageBox.DestructiveRole)
                box.exec()
                clicked = box.clickedButton()
                if clicked is stop:
                    break
                if clicked is skip:
                    continue

                self.log(f"Aprendendo '{label}': aperte o botao no controle agora...")
                try:
                    data = self._capture_one(8.0)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Falha ao capturar '{label}': {exc}")
                    continue
                if data is None:
                    self.log(f"Nada recebido para '{label}' (tempo esgotado).")
                    continue
                key = data.hex()
                # One code maps to one action: drop any earlier binding of it.
                learned = {k: v for k, v in learned.items() if k != key}
                learned[key] = action_id
                self.log(f"'{label}' aprendido: {data.hex(' ')}")

            self._learned_map = learned
            self.settings.setValue(self._learned_key(), json.dumps(learned))
            self.radio_listener.set_learned_map(learned)
            self.log(f"Controle salvo: {len(learned)} botao(oes) mapeado(s).")
        finally:
            self._operation_running = False
            self._operation_lock.release()
            if was_listening and self._control_ready:
                self.radio_listener.start()
            self.bridge.refresh.emit()

    def restore_control_driver_now(self) -> None:
        self.run_bg("Restaurando Interface 3 para HID do Windows. Aceite o UAC.", restore_control_driver, exclusive=True)

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "Sobre",
            (
                "Philips USB PC Link Activator for Windows\n\n"
                "Idealizado e testado originalmente por Rafael Jaeger.\n"
                "Criado para recuperar o USB PC Link de aparelhos Philips antigos "
                "em Windows modernos.\n\n"
                f"Versao {app_version()}\n\n"
                "FWM/FWC: ativacao PC Link padrao.\n"
                "MCM: inicializacao do Sound Agent 2.\n"
                "Visor e controles: protocolo PhilipsRemote (MusicMatch) com a "
                "musica atual do Windows.\n\n"
                "Perfil USB: Philips Audio Set 0471:0111."
            ),
        )

    def sync_startup(self) -> None:
        try:
            set_start_with_windows(self.startup_checkbox.isChecked())
            state = "ligado" if self.startup_checkbox.isChecked() else "desligado"
            self.log(f"Iniciar com o Windows: {state}.")
        except Exception as exc:
            self.log(f"Erro ao alterar inicializacao: {exc}")

    def listener_required(self) -> bool:
        """The control channel must stay open to answer the unit's requests."""
        return (
            self.buttons_checkbox.isChecked()
            or self.display_checkbox.isChecked()
            or self.selected_device_family() == "MCM"
        )

    def sync_button_listener(self) -> None:
        self.radio_listener.set_buttons_enabled(self.buttons_checkbox.isChecked())
        if self.listener_required():
            if not self._control_ready:
                return
            if time.time() < self._buttons_backoff_until:
                return
            if not self.radio_listener.running:
                self.radio_listener.start()
                if self.selected_device_family() == "MCM":
                    self.log("Canal continuo do Sound Agent/PC Link ligado.")
                elif self.buttons_checkbox.isChecked():
                    self.log("Leitor de botoes e PC Link do radio ligado.")
                else:
                    self.log("Canal PC Link ligado (botoes desativados).")
        elif self.radio_listener.running:
            self.radio_listener.stop()
            self.log("Leitor de botoes do radio desligado.")

    def on_radio_button(self, name: str, raw: bytes) -> None:
        if name.startswith("PC Link:") or name.startswith("Setup Philips:"):
            self.bridge.log.emit(name + (f" raw={raw.hex(' ')}" if raw else ""))
            return
        if name.startswith("Sound Agent:"):
            self.bridge.log.emit(f"{name} raw={raw.hex(' ')}")
            if "pedido para abrir a interface" in name:
                self.bridge.sound_agent_window.emit(True)
            elif "pedido para fechar a interface" in name:
                self.bridge.sound_agent_window.emit(False)
            return
        if name.startswith("Status do aparelho"):
            self.bridge.log.emit(f"{name} raw={raw.hex(' ')}")
            return
        self.bridge.log.emit(f"Botao: {name} raw={raw.hex(' ')}")

    def on_button_error(self, message: str) -> None:
        if "LIBUSB_ERROR_ACCESS" in message:
            self._buttons_backoff_until = time.time() + 20
        if message == self._last_button_error and time.time() < self._buttons_backoff_until:
            return
        self._last_button_error = message
        channel = "Sound Agent/controle" if self.selected_device_family() == "MCM" else "Botoes"
        self.bridge.log.emit(f"{channel}: {message}")


def main() -> int:
    app = QApplication(sys.argv)
    QApplication.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(app_root() / "assets" / "app.ico")))

    # Already running? Bring the existing window back instead of refusing.
    if notify_existing_instance():
        return 0

    server = QLocalServer()
    QLocalServer.removeServer(SINGLE_INSTANCE_NAME)  # clear any stale lock from a crash
    server.listen(SINGLE_INSTANCE_NAME)

    window = MainWindow()

    def on_second_instance() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
        window.restore_from_tray()

    server.newConnection.connect(on_second_instance)

    if "--minimized" in sys.argv:
        window.hide()
    else:
        window.show()
    try:
        return app.exec()
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
