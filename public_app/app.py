from __future__ import annotations

import ctypes
import sys
import threading
import time
from datetime import datetime

from PySide6.QtCore import QTimer, Signal, QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from philips_pc_link_core import (
    RadioButtonListener,
    app_root,
    capture_button_reports,
    detect_profile,
    export_diagnostics,
    get_start_with_windows,
    get_status,
    install_winusb_driver,
    restore_control_driver,
    set_default_output,
    set_start_with_windows,
    send_pc_link_enable,
    try_radio_power_on,
)

ERROR_ALREADY_EXISTS = 183
_SINGLE_INSTANCE_MUTEX = None


def acquire_single_instance() -> bool:
    global _SINGLE_INSTANCE_MUTEX
    if sys.platform != "win32":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _SINGLE_INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "Local\\PhilipsUSBPCLinkActivator")
    if not _SINGLE_INSTANCE_MUTEX:
        return True
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


class Bridge(QObject):
    log = Signal(str)
    status = Signal(str)
    refresh = Signal()
    device_status = Signal(object)


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

        self.title_label = QLabel("Philips USB PC Link Activator for Windows")
        self.title_label.setObjectName("titleLabel")
        self.profile_label = QLabel("Perfil: detectando aparelho USB PC Link...")
        self.profile_label.setObjectName("profileLabel")
        self.status_label = QLabel("Verificando Philips Audio Set...")
        self.status_label.setObjectName("statusLabel")

        self.install_button = QPushButton("Instalar Driver WinUSB")
        self.activate_button = QPushButton("Ativar PC Link Agora")
        self.power_button = QPushButton("Ligar Radio")
        self.default_button = QPushButton("Usar Como Saida Padrao")
        self.diagnostics_button = QPushButton("Gerar Diagnostico")
        self.capture_button = QPushButton("Capturar Controle")
        self.restore_button = QPushButton("Restaurar Controle HID")
        self.about_button = QPushButton("Sobre")
        self.buttons_checkbox = QCheckBox("Ativar botoes do radio")
        self.buttons_checkbox.setChecked(True)
        self.auto_checkbox = QCheckBox("Auto-reativar")
        self.auto_checkbox.setChecked(True)
        self.startup_checkbox = QCheckBox("Iniciar com o Windows")
        self.startup_checkbox.setChecked(get_start_with_windows())

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        actions = QHBoxLayout()
        actions.addWidget(self.install_button)
        actions.addWidget(self.activate_button)
        actions.addWidget(self.power_button)
        actions.addWidget(self.default_button)
        actions.addWidget(self.diagnostics_button)

        advanced = QHBoxLayout()
        advanced.addWidget(self.capture_button)
        advanced.addWidget(self.restore_button)
        advanced.addWidget(self.about_button)
        advanced.addStretch(1)

        options = QHBoxLayout()
        options.addWidget(self.auto_checkbox)
        options.addWidget(self.buttons_checkbox)
        options.addWidget(self.startup_checkbox)
        options.addStretch(1)

        device_box = QGroupBox("Aparelho")
        device_box.setObjectName("panel")
        device_layout = QVBoxLayout()
        device_layout.addWidget(self.profile_label)
        device_layout.addWidget(self.status_label)
        device_layout.addLayout(actions)
        device_box.setLayout(device_layout)

        controls_box = QGroupBox("Controles")
        controls_box.setObjectName("panel")
        controls_layout = QVBoxLayout()
        controls_layout.addLayout(options)
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
        self.power_button.clicked.connect(self.power_on_now)
        self.default_button.clicked.connect(self.set_default_output_now)
        self.diagnostics_button.clicked.connect(self.export_diagnostics_now)
        self.capture_button.clicked.connect(self.capture_remote_buttons_now)
        self.restore_button.clicked.connect(self.restore_control_driver_now)
        self.about_button.clicked.connect(self.show_about)
        self.buttons_checkbox.stateChanged.connect(self.sync_button_listener)
        self.startup_checkbox.stateChanged.connect(self.sync_startup)

        self.radio_listener = RadioButtonListener(self.on_radio_button, self.on_button_error, detect_profile())
        self.last_activation = 0.0
        self.last_ready = False
        self._last_auto_log = 0.0
        self._status_running = False
        self._control_ready = False
        self._force_quit = False
        self._tray_notice_shown = False
        self.setup_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_status)
        self.timer.start(10000)

        self.poll_status()
        self.sync_button_listener()

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
            self.radio_listener.stop()
            self.tray_icon.hide()
            super().closeEvent(event)
            return

        event.ignore()
        self.hide()
        if not self._tray_notice_shown and self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Philips USB PC Link Activator for Windows",
                "O app continua ativo na bandeja para reativar o PC Link.",
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

    def run_bg(self, label: str, fn, log_result: bool = True) -> None:
        def worker() -> None:
            self.bridge.log.emit(label)
            try:
                result = fn()
                if result and log_result:
                    self.bridge.log.emit(str(result))
            except Exception as exc:
                self.bridge.log.emit(f"Erro: {exc}")
            finally:
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
            models = ", ".join(status.profile.known_models[:4])
            self.profile_label.setText(
                f"Perfil: {status.profile.display_name} ({status.profile.vid_pid_label}) | Modelos: {models}..."
            )
        else:
            self.profile_label.setText("Perfil: nenhum aparelho suportado detectado")

        driver_installed = status.interface3_service == "WinUSB"
        can_install = bool(status.profile and status.present and not driver_installed)
        self.install_button.setEnabled(can_install)
        if not status.present:
            self.install_button.setText("Aguardando Aparelho")
        elif driver_installed:
            self.install_button.setText("Driver WinUSB Instalado")
        else:
            self.install_button.setText("Instalar Driver WinUSB")

        self.activate_button.setEnabled(status.interface3_ok)
        self.power_button.setEnabled(status.interface3_ok)
        self.default_button.setEnabled(status.audio_present)
        self.capture_button.setEnabled(status.interface3_ok)
        self.restore_button.setEnabled(bool(status.present and status.interface3_service))
        self.default_button.setText("Saida Padrao Ativa" if status.default_output else "Usar Como Saida Padrao")

        ready = bool(status.interface3_ok)
        self._control_ready = ready
        if ready:
            self.sync_button_listener()
        elif self.radio_listener.running:
            self.radio_listener.stop()
            self.bridge.log.emit("Leitor de botoes pausado: aguardando o USB PC Link reconectar.")

        was_ready = self.last_ready
        if ready and not was_ready:
            self.bridge.log.emit("Philips pronto.")
        self.last_ready = ready

        if self.auto_checkbox.isChecked() and ready:
            now = time.time()
            if not was_ready or now - self.last_activation > 25:
                self.last_activation = now
                self.activate_now(auto=True)

    def install_driver(self) -> None:
        self.run_bg("Instalando WinUSB somente na Interface 3. Aceite o UAC.", install_winusb_driver)

    def activate_now(self, auto: bool = False) -> None:
        label = "Auto-reativando PC Link..." if auto else "Ativando PC Link..."
        self.last_activation = time.time()
        log_result = True
        if auto and time.time() - self._last_auto_log < 120:
            log_result = False
        if auto and log_result:
            self._last_auto_log = time.time()

        restart_buttons = self.buttons_checkbox.isChecked() and self.radio_listener.running

        def activate_safely() -> str:
            if restart_buttons:
                self.radio_listener.stop()
            try:
                return send_pc_link_enable()
            finally:
                if restart_buttons:
                    self.radio_listener.start()

        self.run_bg(label, activate_safely, log_result=log_result)

    def power_on_now(self) -> None:
        restart_buttons = self.buttons_checkbox.isChecked() and self.radio_listener.running

        def power_safely() -> str:
            if restart_buttons:
                self.radio_listener.stop()
            try:
                return try_radio_power_on()
            finally:
                if restart_buttons and self._control_ready:
                    self.radio_listener.start()

        self.run_bg("Tentando ligar/reativar o radio pelo PC Link...", power_safely)

    def set_default_output_now(self) -> None:
        self.run_bg("Definindo Philips como saida padrao do Windows...", set_default_output)

    def export_diagnostics_now(self) -> None:
        self.run_bg("Gerando diagnostico para suporte/novos modelos...", export_diagnostics)

    def capture_remote_buttons_now(self) -> None:
        restart_buttons = self.buttons_checkbox.isChecked() and self.radio_listener.running

        def capture_safely() -> str:
            if restart_buttons:
                self.radio_listener.stop()
            try:
                return capture_button_reports(duration_seconds=15.0)
            finally:
                if restart_buttons and self._control_ready:
                    self.radio_listener.start()

        self.run_bg(
            "Capturando botoes por 15s. Aperte Play/Pause, Next e Previous no controle remoto agora...",
            capture_safely,
        )

    def restore_control_driver_now(self) -> None:
        self.run_bg("Restaurando Interface 3 para HID do Windows. Aceite o UAC.", restore_control_driver)

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "Sobre",
            (
                "Philips USB PC Link Activator for Windows\n\n"
                "Idealizado e testado originalmente por Rafael Jaeger.\n"
                "Criado para recuperar o USB PC Link de aparelhos Philips antigos "
                "em Windows modernos.\n\n"
                "Primeiro perfil validado: Philips Audio Set 0471:0111."
            ),
        )

    def sync_startup(self) -> None:
        try:
            set_start_with_windows(self.startup_checkbox.isChecked())
            state = "ligado" if self.startup_checkbox.isChecked() else "desligado"
            self.log(f"Iniciar com o Windows: {state}.")
        except Exception as exc:
            self.log(f"Erro ao alterar inicializacao: {exc}")

    def sync_button_listener(self) -> None:
        if self.buttons_checkbox.isChecked():
            if not self._control_ready:
                return
            if not self.radio_listener.running:
                self.radio_listener.start()
                self.log("Leitor de botoes do radio ligado.")
        else:
            self.radio_listener.stop()
            self.log("Leitor de botoes do radio desligado.")

    def on_radio_button(self, name: str, raw: bytes) -> None:
        self.bridge.log.emit(f"Botao: {name} raw={raw.hex(' ')}")

    def on_button_error(self, message: str) -> None:
        self.bridge.log.emit(f"Botoes: {message}")


def main() -> int:
    is_first_instance = acquire_single_instance()
    app = QApplication(sys.argv)
    QApplication.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(app_root() / "assets" / "app.ico")))
    if not is_first_instance:
        if "--minimized" not in sys.argv:
            QMessageBox.information(
                None,
                "Philips USB PC Link Activator for Windows",
                "O app ja esta aberto. Procure o icone de caixinha na bandeja do Windows.",
            )
        return 0

    window = MainWindow()
    if "--minimized" in sys.argv:
        window.hide()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
