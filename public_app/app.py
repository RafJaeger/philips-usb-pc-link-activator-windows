from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime

from PySide6.QtCore import QTimer, Signal, QObject, QSettings, QEventLoop, Qt
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QButtonGroup,
    QFrame,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from display_modes import EFFECT_NAMES, EFFECTS, MODE_NAMES, MODES, DisplayComposer
from media_session import MediaMonitor
from notifications import NotificationWatcher
from software_volume import SoftwareVolume
from tone_control import ChipTone
from ui_equalizer import EqualizerPanel
from pc_link_protocol import DEFAULT_DISPLAY_WIDTH, MAX_TEXT_CHARS, PcLinkSession, display_text
from theme import STYLESHEET, HeaderBar, LcdPanel, StepRow, page_title
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
    install_equalizer,
    install_winusb_driver,
    remove_equalizer,
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
    display_mode = Signal(str)
    equalizer_done = Signal()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Philips USB PC Link Activator for Windows")
        self._density = None
        self.app_icon = QIcon(str(app_root() / "assets" / "app.ico"))
        self.setWindowIcon(self.app_icon)

        self.bridge = Bridge()
        self.bridge.log.connect(self.log)
        self.bridge.status.connect(self.set_status_text)
        self.bridge.refresh.connect(self.poll_status)
        self.bridge.device_status.connect(self.apply_status)
        self.bridge.sound_agent_window.connect(self.set_sound_agent_window)
        self.bridge.mcm_retry.connect(self.schedule_mcm_retry)

        self.header = HeaderBar(
            "USB PC Link Activator",
            "MICRO HI-FI  ·  FWM / FWC / MCM  ·  WINDOWS 10 / 11",
            f"v{app_version()}",
        )
        self.title_label = self.header.title
        self.lcd = LcdPanel()
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
        self.display_checkbox = QCheckBox("Usar o visor do radio")
        self.display_checkbox.setChecked(self._setting_bool("show_title", True))
        self.mode_combo = QComboBox()
        for mode in MODES:
            self.mode_combo.addItem(MODE_NAMES[mode], mode)
        self.effect_combo = QComboBox()
        for effect in EFFECTS:
            self.effect_combo.addItem(EFFECT_NAMES[effect], effect)
        saved_effect = str(self.settings.value("display_effect", "scroll"))
        effect_index = self.effect_combo.findData(saved_effect)
        self.effect_combo.setCurrentIndex(effect_index if effect_index >= 0 else 0)
        self.free_text_edit = QLineEdit(str(self.settings.value("display_text", "") or ""))
        self.free_text_edit.setMaxLength(80)
        self.free_text_edit.setPlaceholderText("Texto livre para o modo Texto (ex.: BOA NOITE)")
        self.notify_checkbox = QCheckBox("Mostrar notificacoes do Windows no visor")
        self.notify_checkbox.setChecked(self._setting_bool("windows_notifications", True))
        self.notify_body_checkbox = QCheckBox("Mostrar tambem o texto das mensagens")
        self.notify_body_checkbox.setChecked(self._setting_bool("notification_body", False))
        self.notices_checkbox = QCheckBox("Avisos no visor (volume, mudo, pausa)")
        self.notices_checkbox.setChecked(self._setting_bool("display_notices", True))
        self.display_hint = QLabel(
            "Os 2 botoes de playlist do radio (proxima/anterior) trocam o modo do visor."
        )
        self.display_hint.setObjectName("hintLabel")
        self.display_preview = QLabel("-" * 8)
        self.display_preview.setToolTip("O que o visor do radio esta mostrando agora")
        self.display_preview.setObjectName("displayPreview")
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

        # ===== Pages =====
        self.equalizer_panel = EqualizerPanel(self.settings, chip_tone_factory=self._chip_tone)
        self.equalizer_panel.install_requested.connect(self.install_equalizer_now)
        self.equalizer_panel.remove_requested.connect(self.remove_equalizer_now)
        self.equalizer_panel.message.connect(self.log)
        self.bridge.equalizer_done.connect(self.equalizer_panel.refresh_status)

        self.activate_button.setObjectName("bigButton")
        self.step_driver = StepRow(1, "Driver de controle (WinUSB)", self.install_button)
        self.step_link = StepRow(2, "USB PC Link ativo", self.activate_button)
        self.step_output = StepRow(3, "Philips como saida de som do Windows", self.default_button)

        home = self._page(
            "Inicio",
            "Coloque o radio em PC LINK e siga os 3 passos. Quando todos estiverem verdes, e so tocar.",
            self._card(
                "Aparelho",
                self.status_label,
                self.profile_label,
                self._field(
                    "Modelo do aparelho", self.family_combo,
                    "FWM/FWC: ativacao padrao. MCM: Sound Agent 2 (MCM530/570/590/595).",
                ),
            ),
            self.step_driver,
            self.step_link,
            self.step_output,
        )

        display = self._page(
            "Visor do radio",
            "O que aparece no visor do aparelho. Os 2 botoes de playlist do radio tambem trocam o modo.",
            self._card(
                "Conteudo",
                self.display_checkbox,
                self._field(
                    "Modo", self.mode_combo,
                    "Musica (titulo e artista), relogio, CPU/RAM, internet, texto livre ou tudo alternando.",
                ),
                self._field(
                    "Efeito da musica", self.effect_combo,
                    "Como o titulo entra quando nao cabe no visor. Relogio, PC e avisos ficam parados.",
                ),
                self._field("Texto livre", self.free_text_edit, "Usado no modo Texto livre."),
            ),
            self._card(
                "Notificacoes do Windows",
                self.notify_checkbox,
                self._hint(
                    "Mensagens do WhatsApp, Discord, e-mail etc. aparecem na hora: primeiro o nome "
                    "do app, depois o titulo. O Windows pode pedir permissao na primeira vez."
                ),
                self.notify_body_checkbox,
                self._hint("Desligado por privacidade: so o app e o titulo (remetente) aparecem."),
            ),
            self._card(
                "Ajustes",
                self._field(
                    "Largura do visor", self.width_spin,
                    "FWM589: 8 caracteres. Acima de 16 o visor do aparelho trava, por isso o limite.",
                ),
                self.notices_checkbox,
                self.now_playing_label,
            ),
        )

        sound = self._page(
            "Som",
            "Volume do Windows na saida Philips, equalizador do Sound Agent e o tom do proprio radio.",
            self._card(
                "Volume",
                self.soft_volume_checkbox,
                self._field(
                    "Botoes de volume do radio", self.volume_combo,
                    "Volume do Windows mostra o indicador na tela, como uma tecla de volume.",
                ),
            ),
            self.equalizer_panel,
        )

        self.learn_button.setMinimumWidth(240)
        self.restore_button.setMinimumWidth(240)
        control = self._page(
            "Controle",
            "Botoes do painel e do controle remoto do radio no PC.",
            self._card(
                "Botoes do radio",
                self.buttons_checkbox,
                self._hint("Play/pause, parar, proxima, anterior, mudo e volume viram comandos do Windows."),
            ),
            self._card(
                "Controle remoto",
                self._row(self.learn_button, self._hint("Ensina ao app os codigos do seu controle, botao por botao.")),
            ),
            self._card(
                "Avancado",
                self._row(self.restore_button, self._hint("Devolve a interface de controle ao driver HID do Windows.")),
            ),
        )

        self.log_button.setMinimumWidth(200)
        self.about_button.setMinimumWidth(200)
        settings_page = self._page(
            "Configuracoes",
            "Como o app se comporta no dia a dia.",
            self._card(
                "Inicializacao",
                self.auto_checkbox,
                self._hint("Ativa o PC Link de novo sozinho quando o radio reconecta ou volta ao PC LINK."),
                self.startup_checkbox,
                self._hint("Abre minimizado na bandeja quando o Windows inicia."),
            ),
            self._card(
                "Suporte",
                self._row(self.log_button, self._hint("Salva um arquivo com tudo que o app sabe do aparelho.")),
                self._row(self.about_button, self._hint("Versao, creditos e licencas.")),
            ),
        )

        log_page = self._page("Log", "Tudo o que o app fez nesta sessao.", self.log_box)

        self.pages = QStackedWidget()
        self.nav_buttons: list[QPushButton] = []
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        self.sidebar = sidebar
        self._nav_labels: list[tuple[str, str]] = []
        nav_layout = QVBoxLayout()
        nav_layout.setContentsMargins(10, 12, 10, 12)
        nav_layout.setSpacing(6)
        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        pages = (
            ("⌂   Inicio", home),
            ("▣   Visor", display),
            ("♫   Som", sound),
            ("◎   Controle", control),
            ("⚙   Configuracoes", settings_page),
            ("☰   Log", log_page),
        )
        for index, (label, page) in enumerate(pages):
            self.pages.addWidget(page)
            button = QPushButton(label)
            button.setObjectName("navButton")
            icon, _, name = label.partition("   ")
            self._nav_labels.append((icon, label))
            button.setToolTip(name)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            nav_group.addButton(button)
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
        nav_layout.addStretch(1)
        version = QLabel(f"versao {app_version()}")
        self.sidebar_version = version
        version.setObjectName("fieldHelp")
        version.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(version)
        sidebar.setLayout(nav_layout)
        self.nav_buttons[0].setChecked(True)

        content = QVBoxLayout()
        content.setSpacing(10)
        content.addWidget(self.lcd)
        content.addWidget(self.pages, 1)
        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(sidebar)
        body.addLayout(content, 1)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self.header)
        layout.addLayout(body, 1)

        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(640, 460)
        self._fit_to_screen()

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
        self.display = DisplayComposer()
        self.display.set_free_text(self.free_text_edit.text(), time.monotonic())
        self.display.set_effect(self.selected_effect())
        self.display.notices_enabled = self.notices_checkbox.isChecked()
        saved_mode = str(self.settings.value("display_mode", "music"))
        self.display.set_mode(saved_mode, time.monotonic(), announce=False)
        self._select_mode_in_combo(self.display.mode)
        # The stereo's playlist buttons change the mode from the USB thread.
        self.display.on_mode_change = self.bridge.display_mode.emit
        self.bridge.display_mode.connect(self.on_display_mode_from_radio)
        self.pc_link = PcLinkSession(
            self.pc_link_host,
            ensure_mailbox=ensure_pc_link_mailbox,
            display_enabled=lambda: self._show_title,
            display_width=lambda: self._display_width,
            composer=self.display,
        )
        self.mode_combo.currentIndexChanged.connect(self.on_display_mode_changed)
        self.effect_combo.currentIndexChanged.connect(self.on_display_effect_changed)
        self.free_text_edit.textChanged.connect(self.on_free_text_changed)
        self.notices_checkbox.stateChanged.connect(self.on_notices_changed)
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
            on_level=self.on_windows_volume_level,
        )
        self.software_volume.set_enabled(self.soft_volume_checkbox.isChecked())
        self.notification_watcher = NotificationWatcher(
            self.on_windows_notification, on_status=self.bridge.log.emit
        )
        self.notify_checkbox.stateChanged.connect(self.on_notifications_changed)
        self.notify_body_checkbox.stateChanged.connect(
            lambda _state: self.settings.setValue("notification_body", self.notify_body_checkbox.isChecked())
        )
        if self.notify_checkbox.isChecked():
            self.notification_watcher.start()
        self.now_playing_timer = QTimer(self)
        self.now_playing_timer.timeout.connect(self.refresh_now_playing)
        self.now_playing_timer.start(300)

        self.setup_menus()
        self.poll_status()
        self.sync_button_listener()

    # ----- menu bar -----

    def _menu_check(self, menu, text: str, checkbox: QCheckBox) -> QAction:
        action = QAction(text, self)
        action.setCheckable(True)
        action.triggered.connect(lambda checked: checkbox.setChecked(checked))
        menu.aboutToShow.connect(lambda: action.setChecked(checkbox.isChecked()))
        menu.addAction(action)
        return action

    def _menu_button(self, menu, text: str, slot, button: QWidget | None = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: slot())
        if button is not None:
            menu.aboutToShow.connect(lambda: action.setEnabled(button.isEnabled()))
        menu.addAction(action)
        return action

    def _menu_choices(self, menu, title: str, combo: QComboBox) -> None:
        """Submenu mirroring a combo box (rebuilt when opened, so always current)."""
        submenu = menu.addMenu(title)

        def rebuild() -> None:
            submenu.clear()
            group = QActionGroup(submenu)
            for index in range(combo.count()):
                action = QAction(combo.itemText(index), submenu)
                action.setCheckable(True)
                action.setChecked(index == combo.currentIndex())
                action.triggered.connect(lambda _checked=False, i=index: combo.setCurrentIndex(i))
                group.addAction(action)
                submenu.addAction(action)

        submenu.aboutToShow.connect(rebuild)
        rebuild()

    def setup_menus(self) -> None:
        bar = self.menuBar()
        bar.clear()

        file_menu = bar.addMenu("&Arquivo")
        for index, name in enumerate(("Inicio", "Visor", "Som", "Controle", "Configuracoes", "Log")):
            self._menu_button(file_menu, f"Ir para {name}", lambda i=index: self.show_page(i))
        file_menu.addSeparator()
        self._menu_button(file_menu, "Gerar log de suporte...", self.export_log_now, self.log_button)
        file_menu.addSeparator()
        self._menu_button(file_menu, "Minimizar para a bandeja", self.hide)
        self._menu_button(file_menu, "Sair", self.quit_from_tray)

        device_menu = bar.addMenu("A&parelho")
        self._menu_button(device_menu, "Ativar PC Link agora", self.activate_now, self.activate_button)
        self._menu_button(device_menu, "Instalar driver WinUSB", self.install_driver, self.install_button)
        self._menu_button(device_menu, "Usar o radio como saida de som", self.set_default_output_now, self.default_button)
        device_menu.addSeparator()
        self._menu_choices(device_menu, "Modelo do aparelho", self.family_combo)
        self._menu_check(device_menu, "Reativar automaticamente", self.auto_checkbox)
        device_menu.addSeparator()
        self._menu_button(device_menu, "Restaurar controle HID do Windows", self.restore_control_driver_now, self.restore_button)

        display_menu = bar.addMenu("&Visor")
        self._menu_check(display_menu, "Usar o visor do radio", self.display_checkbox)
        self._menu_choices(display_menu, "Modo", self.mode_combo)
        self._menu_choices(display_menu, "Efeito da musica", self.effect_combo)
        width_menu = display_menu.addMenu("Largura do visor")

        def rebuild_widths() -> None:
            width_menu.clear()
            group = QActionGroup(width_menu)
            for width in (8, 10, 12, 14, 16):
                action = QAction(f"{width} caracteres", width_menu)
                action.setCheckable(True)
                action.setChecked(self.width_spin.value() == width)
                action.triggered.connect(lambda _checked=False, w=width: self.width_spin.setValue(w))
                group.addAction(action)
                width_menu.addAction(action)

        width_menu.aboutToShow.connect(rebuild_widths)
        rebuild_widths()
        self._menu_check(display_menu, "Avisos no visor (volume, mudo, pausa)", self.notices_checkbox)
        display_menu.addSeparator()
        self._menu_check(display_menu, "Notificacoes do Windows no visor", self.notify_checkbox)
        self._menu_check(display_menu, "Mostrar o texto das mensagens", self.notify_body_checkbox)
        self._menu_button(display_menu, "Editar texto livre...", self.edit_free_text)

        sound_menu = bar.addMenu("&Som")
        self._menu_check(sound_menu, "Corrigir volume do Windows na saida Philips", self.soft_volume_checkbox)
        self._menu_choices(sound_menu, "Botoes de volume do radio", self.volume_combo)
        sound_menu.addSeparator()
        panel = self.equalizer_panel
        self._menu_check(sound_menu, "Equalizador ligado", panel.enabled_checkbox)
        self._menu_choices(sound_menu, "Predefinicao do equalizador", panel.preset_combo)
        self._menu_button(sound_menu, "Ativar equalizador", panel.install_requested.emit, panel.install_button)
        self._menu_button(sound_menu, "Remover equalizador da saida Philips", panel._confirm_remove, panel.remove_button)
        sound_menu.addSeparator()
        self._menu_button(sound_menu, "Zerar graves e agudos do radio", panel._reset_tone, panel.tone_reset)

        control_menu = bar.addMenu("&Controle")
        self._menu_check(control_menu, "Ativar botoes do radio", self.buttons_checkbox)
        self._menu_button(control_menu, "Aprender controle remoto...", self.learn_remote_now, self.learn_button)

        help_menu = bar.addMenu("Aj&uda")
        self._menu_button(help_menu, "Como usar", self.show_help)
        self._menu_button(help_menu, "Abrir o log", lambda: self.show_page(5))
        help_menu.addSeparator()
        self._menu_button(help_menu, "Sobre", self.show_about)

    def edit_free_text(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Texto livre", "Texto para o modo Texto livre do visor:", text=self.free_text_edit.text()
        )
        if ok:
            self.free_text_edit.setText(text)

    def show_help(self) -> None:
        QMessageBox.information(
            self,
            "Como usar",
            "1. Ligue o radio, conecte o cabo USB e escolha PC LINK no aparelho.\n"
            "2. Na pagina Inicio, siga os 3 passos ate todos ficarem verdes.\n"
            "3. Toque musica no PC: ela sai pelo radio e o titulo aparece no visor.\n\n"
            "Visor: escolha musica, relogio, PC, internet ou texto. Os 2 botoes de playlist "
            "do radio trocam o modo.\n"
            "Som: o volume do Windows funciona na saida Philips; o equalizador do Sound Agent "
            "e os graves/agudos do radio ficam na pagina Som.\n"
            "Controle: botoes do radio e do controle remoto viram comandos do Windows.",
        )

    # ----- size and density -----

    def _fit_to_screen(self) -> None:
        """Open at a comfortable size that always fits the screen."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1120, 860)
            return
        area = screen.availableGeometry()
        width = min(1120, int(area.width() * 0.92))
        height = min(880, int(area.height() * 0.9))
        self.resize(max(640, width), max(460, height))
        self.move(area.x() + (area.width() - self.width()) // 2, area.y() + (area.height() - self.height()) // 2)
        self._apply_density()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt name
        super().resizeEvent(event)
        self._apply_density()

    def _apply_density(self) -> None:
        """Narrow window: icon-only sidebar. Short window: compact header and LCD."""
        if not hasattr(self, "sidebar"):
            return
        narrow = self.width() < 980
        short = self.height() < 740
        density = (narrow, short)
        if density == self._density:
            return
        self._density = density
        self.sidebar.setFixedWidth(76 if narrow else 220)
        self.sidebar_version.setVisible(not narrow)
        for button, (icon, label) in zip(self.nav_buttons, self._nav_labels):
            button.setText(icon if narrow else label)
            button.setProperty("compact", narrow)
            button.setProperty("short", short)
            button.style().unpolish(button)
            button.style().polish(button)
        self.header.set_compact(short)
        self.lcd.set_compact(short)

    # ----- page building blocks -----

    def _page(self, title: str, subtitle: str, *widgets) -> QWidget:
        """A scrollable page: big title, one-line explanation, then the content."""
        inner = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 10, 6)
        layout.setSpacing(12)
        layout.addWidget(page_title(title, subtitle))
        for widget in widgets:
            layout.addWidget(widget, 1 if widget is self.log_box else 0)
        if not any(widget is self.log_box for widget in widgets):
            layout.addStretch(1)
        inner.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Never cut a page: scroll sideways only if the window is too narrow.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(inner)
        return scroll

    def _card(self, title: str, *widgets) -> QWidget:
        box = QGroupBox(title)
        box.setObjectName("panel")
        layout = QVBoxLayout()
        layout.setSpacing(10)
        for widget in widgets:
            layout.addWidget(widget)
        box.setLayout(layout)
        return box

    def _field(self, label: str, widget: QWidget, help_text: str = "") -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)
        title = QLabel(label)
        title.setObjectName("fieldLabel")
        layout.addWidget(title)
        layout.addWidget(widget)
        if help_text:
            layout.addWidget(self._hint(help_text))
        holder.setLayout(layout)
        return holder

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldHelp")
        label.setWordWrap(True)
        return label

    def _row(self, button: QWidget, hint: QWidget) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(button)
        layout.addWidget(hint, 1)
        holder.setLayout(layout)
        return holder

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)

    def _update_steps(self, status) -> None:
        if not status.present:
            self.step_driver.set_state("idle", "Conecte o cabo USB e coloque o radio em PC LINK.")
        elif status.interface3_service == "WinUSB":
            self.step_driver.set_state("done", "Instalado somente na interface de controle (MI_03).")
        else:
            self.step_driver.set_state("todo", "Clique para instalar. O Windows vai pedir permissao.")
        if self._pc_link_active and status.interface3_ok:
            self.step_link.set_state("done", "O radio esta recebendo o som do PC.")
        elif status.interface3_ok:
            self.step_link.set_state("todo", "Clique para ativar (o app tambem reativa sozinho).")
        else:
            self.step_link.set_state("idle", "Disponivel depois do passo 1.")
        if status.default_output:
            self.step_output.set_state("done", "O som do Windows sai pelo radio.")
        elif status.audio_present:
            self.step_output.set_state("todo", "Clique para mandar o som do Windows para o radio.")
        else:
            self.step_output.set_state("idle", "A saida de som do radio ainda nao apareceu no Windows.")

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
        self.log("Visor do radio: " + ("ligado." if enabled else "desligado."))
        self.sync_button_listener()

    # ----- Visor -----

    def selected_effect(self) -> str:
        return str(self.effect_combo.currentData() or "scroll")

    def _select_mode_in_combo(self, mode: str) -> None:
        index = self.mode_combo.findData(mode)
        if index >= 0 and index != self.mode_combo.currentIndex():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(index)
            self.mode_combo.blockSignals(False)

    def on_display_mode_changed(self, _index: int | None = None) -> None:
        mode = str(self.mode_combo.currentData() or "music")
        self.display.set_mode(mode, time.monotonic())
        if self.display.mode != mode:  # e.g. Texto livre without text
            self.log("Modo Texto livre precisa de um texto; escreva no campo abaixo.")
            self._select_mode_in_combo(self.display.mode)
        self.settings.setValue("display_mode", self.display.mode)

    def on_display_mode_from_radio(self, mode: str) -> None:
        self._select_mode_in_combo(mode)
        self.settings.setValue("display_mode", mode)
        self.log(f"Visor: modo {MODE_NAMES.get(mode, mode)} (botao do radio).")

    def on_display_effect_changed(self, _index: int | None = None) -> None:
        effect = self.selected_effect()
        self.display.set_effect(effect)
        self.settings.setValue("display_effect", effect)

    def on_free_text_changed(self, text: str) -> None:
        self.display.set_free_text(text, time.monotonic())
        self.settings.setValue("display_text", text)
        self._select_mode_in_combo(self.display.mode)

    def on_notices_changed(self, _state: int | None = None) -> None:
        enabled = self.notices_checkbox.isChecked()
        self.display.notices_enabled = enabled
        self.settings.setValue("display_notices", enabled)

    # ----- Equalizador -----

    def _chip_tone(self):
        endpoint = find_philips_render_endpoint_fast()
        return None if endpoint is None else ChipTone.from_endpoint(endpoint)

    def install_equalizer_now(self) -> None:
        def install_safely() -> str:
            try:
                return install_equalizer()
            finally:
                self.bridge.equalizer_done.emit()

        self.run_bg(
            "Ativando o equalizador (instala o Equalizer APO so na saida Philips). Aceite o UAC.",
            install_safely,
        )

    def remove_equalizer_now(self) -> None:
        def remove_safely() -> str:
            try:
                return remove_equalizer()
            finally:
                self.bridge.equalizer_done.emit()

        self.run_bg("Removendo o equalizador da saida Philips. Aceite o UAC.", remove_safely)

    def on_notifications_changed(self, _state: int | None = None) -> None:
        enabled = self.notify_checkbox.isChecked()
        self.settings.setValue("windows_notifications", enabled)
        if enabled:
            self.notification_watcher.start()
        else:
            self.notification_watcher.stop()
            self.log("Notificacoes do Windows no visor: desligadas.")

    def on_windows_notification(self, notification) -> None:
        """Called from the watcher thread: show it on the display at once."""
        pages = [notification.app, notification.title]
        if self.notify_body_checkbox.isChecked() and notification.body:
            pages.append(notification.body)
        self.display.notify_pages(pages, time.monotonic())
        self.bridge.log.emit(f"Notificacao no visor: {notification.app}")

    def on_windows_volume_level(self, percent: int, muted: bool) -> None:
        """Called from the volume thread: show VOL/MUDO on the display at once."""
        text = "MUDO" if muted else f"VOL {percent:3d}%"
        self.display.notify(text, time.monotonic(), 1.8)

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
        # Same characters as the stereo display (spaces kept, upper case).
        window = self.pc_link.state.last_window if self.pc_link.state.unit_active else None
        self.lcd.set_text(window, self._display_width)
        self.lcd.set_indicator("PC LINK", bool(self._pc_link_active and self.pc_link.state.unit_active))
        self.lcd.set_indicator("EQ", bool(self.equalizer_panel.last_ready))
        self.lcd.set_indicator("PLAY", info.status == "playing")
        self.lcd.set_indicator("PAUSA", info.status == "paused")

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
                self.notification_watcher.stop()
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

        if status.present and status.interface3_ok:
            self.header.set_led("on", "CONECTADO")
        elif status.present:
            self.header.set_led("wait", "PREPARANDO")
        else:
            self.header.set_led("off", "DESCONECTADO")
        self.lcd.set_indicator("USB", bool(status.present))
        self._last_status = status

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
        self._update_steps(status)

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
