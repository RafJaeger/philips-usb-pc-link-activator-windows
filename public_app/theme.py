"""Look of the app, inspired by the early-2000s Philips FWM micro systems:
brushed-aluminium front, graphite body, chrome buttons and a blue
back-lit LCD. No Philips logo or trademark artwork is used.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

# Palette
GRAPHITE = "#15181d"
GRAPHITE_2 = "#1d2128"
PANEL = "#20252d"
PANEL_EDGE = "#353c47"
SILVER_TEXT = "#c7ccd3"
TEXT = "#eef1f4"
MUTED = "#8b94a1"
LCD_BG = "#04122a"
LCD_TEXT = "#62d8ff"
LCD_DIM = "#173556"
ACCENT = "#2f8be6"
LED_ON = "#43e37a"
LED_WAIT = "#f0b13a"
LED_OFF = "#4a525e"

LCD_FONT = '"Consolas", "Lucida Console", "Courier New", monospace'

STYLESHEET = f"""
QMainWindow, QWidget {{ background: {GRAPHITE}; color: {TEXT}; font-size: 15px; }}
QLabel {{ background: transparent; color: {TEXT}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {PANEL_EDGE}; }}

/* ---- brushed aluminium header ---- */
#headerBar {{
    border: 1px solid #8e959e;
    border-radius: 10px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f2f4f6, stop:0.08 #d7dbe0, stop:0.5 #bcc2c9, stop:0.52 #c9ced4, stop:1 #9aa1aa);
}}
#headerTitle {{ color: #1b1f25; font-size: 21px; font-weight: 800; letter-spacing: 1px; }}
#headerSubtitle {{ color: #3b424c; font-size: 12px; letter-spacing: 3px; }}
#headerBadge {{
    color: #e9eef3; background: #2a3039; border: 1px solid #10141a;
    border-radius: 9px; padding: 3px 10px; font-size: 11px; font-weight: 700; letter-spacing: 2px;
}}

/* ---- blue LCD ---- */
#lcdBezel {{
    border-radius: 12px;
    border: 1px solid #0a0d12;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b313a, stop:1 #12161b);
}}
#lcdGlass {{
    border-radius: 8px;
    border: 1px solid #0d2c52;
    background: qradialgradient(cx:0.5, cy:0.45, radius:0.9,
        stop:0 #0a2a55, stop:0.6 {LCD_BG}, stop:1 #020914);
}}
#lcdText {{
    color: {LCD_TEXT}; font-family: {LCD_FONT}; font-size: 34px; font-weight: 700;
    letter-spacing: 6px;
}}
#lcdIndicatorOn {{ color: {LCD_TEXT}; font-family: {LCD_FONT}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
#lcdIndicatorOff {{ color: {LCD_DIM}; font-family: {LCD_FONT}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
#lcdCaption {{ color: #6f8fb0; font-size: 11px; letter-spacing: 2px; }}

/* ---- graphite panels ---- */
QGroupBox#panel {{
    background: {GRAPHITE_2};
    border: 1px solid {PANEL_EDGE};
    border-radius: 9px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
    color: {SILVER_TEXT};
    font-weight: 700;
    letter-spacing: 1px;
}}
QGroupBox#panel::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {SILVER_TEXT}; }}
QGroupBox#panel QWidget {{ background: transparent; }}
#profileLabel {{ color: {MUTED}; }}
#statusLabel {{
    background: {PANEL}; border: 1px solid {PANEL_EDGE}; border-radius: 7px;
    padding: 10px 12px; font-weight: 600; color: {TEXT};
}}
#nowPlayingLabel {{ color: {LCD_TEXT}; padding: 2px; }}
#hintLabel {{ color: {MUTED}; font-size: 12px; }}
#eqStatus {{ color: {TEXT}; font-weight: 600; }}
#bandValue {{ color: {LCD_TEXT}; font-family: {LCD_FONT}; font-size: 12px; }}
#bandLabel {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}

/* ---- chrome buttons ---- */
QPushButton {{
    color: #14181d; font-weight: 700; padding: 10px 16px; min-height: 22px; font-size: 15px;
    border: 1px solid #6f7781; border-radius: 7px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f4f6f8, stop:0.5 #cfd4da, stop:0.51 #bfc5cc, stop:1 #e1e5e9);
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #b9bfc6, stop:1 #dde1e5);
}}
QPushButton:disabled {{ color: #9aa3ad; border-color: #3d444e; background: #2a3039; }}
QPushButton#secondaryButton {{
    color: {TEXT}; border: 1px solid {PANEL_EDGE};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3a414b, stop:1 #262b33);
}}
QPushButton#secondaryButton:disabled {{ color: #5d6570; background: #22272e; }}

/* ---- inputs ---- */
QCheckBox {{ color: {TEXT}; spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #6f7781; background: #12161b; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: #8cc6ff; }}
QComboBox, QSpinBox, QLineEdit {{
    background: {PANEL}; color: {TEXT}; border: 1px solid {PANEL_EDGE};
    border-radius: 6px; padding: 6px 8px; selection-background-color: {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {TEXT}; selection-background-color: {ACCENT}; }}
QTextEdit {{
    background: #0b0e12; color: #c9d1d9; border: 1px solid {PANEL_EDGE}; border-radius: 7px;
    padding: 8px; font-family: Consolas, monospace; font-size: 12px;
}}

/* ---- tabs ---- */
QTabWidget::pane {{ border: 1px solid {PANEL_EDGE}; border-radius: 9px; top: -1px; background: {GRAPHITE}; }}
QTabBar::tab {{
    color: {SILVER_TEXT}; padding: 8px 20px; margin-right: 3px; font-weight: 700; letter-spacing: 1px;
    border: 1px solid {PANEL_EDGE}; border-bottom: 0; border-top-left-radius: 8px; border-top-right-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2c323b, stop:1 #1e2228);
}}
QTabBar::tab:selected {{
    color: #14181d;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #eef1f4, stop:1 #b9c0c8);
}}

/* ---- menu bar ---- */
QMenuBar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2a3039, stop:1 #1b1f25);
    color: {SILVER_TEXT}; font-size: 15px; padding: 2px 6px; border-bottom: 1px solid #0d1014;
}}
QMenuBar::item {{ padding: 6px 14px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {ACCENT}; color: white; }}
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {PANEL_EDGE}; padding: 6px; font-size: 15px; }}
QMenu::item {{ padding: 8px 34px 8px 30px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::item:disabled {{ color: #6b737d; }}
QMenu::separator {{ height: 1px; background: {PANEL_EDGE}; margin: 6px 8px; }}
QMenu::indicator {{ width: 16px; height: 16px; left: 8px; }}

/* ---- sidebar navigation ---- */
#sidebar {{
    border: 1px solid {PANEL_EDGE}; border-radius: 12px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1f242c, stop:1 #171a20);
}}
QPushButton#navButton {{
    text-align: left; padding: 14px 18px; min-height: 26px; font-size: 17px; font-weight: 700;
    color: {SILVER_TEXT}; border: 1px solid transparent; border-radius: 10px; background: transparent;
}}
QPushButton#navButton:hover {{ background: #262c35; border-color: {PANEL_EDGE}; }}
QPushButton#navButton[compact="true"] {{ text-align: center; padding: 12px 4px; font-size: 20px; }}
QPushButton#navButton[short="true"] {{ padding-top: 8px; padding-bottom: 8px; }}
QPushButton#navButton:checked {{
    color: #101318; border: 1px solid #8e959e;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f2f4f6, stop:0.5 #c9ced4, stop:1 #aab1b9);
}}

/* ---- pages ---- */
#pageTitle {{ font-size: 24px; font-weight: 800; color: {TEXT}; }}
#pageSubtitle {{ font-size: 14px; color: {MUTED}; }}
#fieldLabel {{ font-size: 15px; font-weight: 700; color: {SILVER_TEXT}; }}
#fieldHelp {{ font-size: 13px; color: {MUTED}; }}
#stepRow {{ background: {PANEL}; border: 1px solid {PANEL_EDGE}; border-radius: 12px; }}
#stepTitle {{ font-size: 17px; font-weight: 800; color: {TEXT}; }}
#stepDetail {{ font-size: 13px; color: {MUTED}; }}
QPushButton#bigButton {{
    font-size: 19px; font-weight: 800; min-height: 40px; padding: 14px 26px; color: white;
    border: 1px solid #8cc6ff; border-radius: 12px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5aaeff, stop:0.5 #2f8be6, stop:1 #1d64b0);
}}
QPushButton#bigButton:hover {{ border-color: white; }}
QPushButton#bigButton:disabled {{ color: #c5d8ec; border-color: #2c5d8f; background: #1d4a78; }}
QComboBox, QSpinBox, QLineEdit {{ min-height: 24px; font-size: 15px; }}
QCheckBox {{ font-size: 15px; }}
QCheckBox::indicator {{ width: 20px; height: 20px; }}

/* ---- hi-fi faders ---- */
QSlider {{ background: transparent; }}
QSlider::groove:vertical {{ background: #07090c; width: 6px; border-radius: 3px; border: 1px solid #2c323b; }}
QSlider::groove:horizontal {{ background: #07090c; height: 6px; border-radius: 3px; border: 1px solid #2c323b; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:vertical {{
    height: 12px; margin: 0 -9px; border-radius: 3px; border: 1px solid #5b636d;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f6f7f9, stop:0.5 #bcc3ca, stop:1 #e7eaee);
}}
QSlider::handle:horizontal {{
    width: 14px; margin: -6px 0; border-radius: 4px; border: 1px solid #5b636d;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f6f7f9, stop:0.5 #bcc3ca, stop:1 #e7eaee);
}}
QSlider::handle:disabled {{ background: #3b424c; border-color: #2c323b; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: {GRAPHITE}; width: 12px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background: #3b424c; border-radius: 5px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: #56606c; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; background: none; }}
"""


class HeaderBar(QFrame):
    """Brushed-aluminium front panel with the app name and a status LED."""

    def __init__(self, title: str, subtitle: str, badge: str) -> None:
        super().__init__()
        self.setObjectName("headerBar")
        self.title = QLabel(title)
        self.title.setObjectName("headerTitle")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("headerSubtitle")
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        self.led = QLabel()
        self.led.setFixedSize(14, 14)
        self.led_text = QLabel("")
        self.led_text.setObjectName("headerSubtitle")
        self.badge = QLabel(badge)
        self.badge.setObjectName("headerBadge")
        layout = QHBoxLayout()
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addLayout(texts, 1)
        layout.addWidget(self.led_text)
        layout.addWidget(self.led)
        layout.addSpacing(10)
        layout.addWidget(self.badge)
        self.setLayout(layout)
        self.set_led("off", "AGUARDANDO")

    def set_compact(self, compact: bool) -> None:
        """Short windows: hide the subtitle line."""
        self.subtitle.setVisible(not compact)

    def set_led(self, state: str, text: str) -> None:
        color = {"on": LED_ON, "wait": LED_WAIT}.get(state, LED_OFF)
        glow = color if state in ("on", "wait") else "#2a3039"
        self.led.setStyleSheet(
            f"background: qradialgradient(cx:0.35, cy:0.35, radius:0.8, stop:0 #ffffff, stop:0.25 {color}, stop:1 {glow});"
            "border: 1px solid #20252c; border-radius: 7px;"
        )
        self.led_text.setText(text)


class LcdPanel(QFrame):
    """Blue back-lit display: the same characters the stereo shows."""

    INDICATORS = ("USB", "PC LINK", "EQ", "PLAY", "PAUSA")

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("lcdBezel")
        glass = QFrame()
        glass.setObjectName("lcdGlass")
        self.text = QLabel("--------")
        self.text.setObjectName("lcdText")
        self.text.setAlignment(Qt.AlignCenter)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.text.setFont(font)
        self._indicators: dict[str, QLabel] = {}
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addStretch(1)
        for name in self.INDICATORS:
            label = QLabel(name)
            label.setObjectName("lcdIndicatorOff")
            self._indicators[name] = label
            row.addWidget(label)
        row.addStretch(1)
        glass_layout = QVBoxLayout()
        glass_layout.setContentsMargins(18, 10, 18, 10)
        glass_layout.addWidget(self.text)
        glass_layout.addLayout(row)
        glass.setLayout(glass_layout)
        self.caption = QLabel("VISOR DO RADIO")
        self.caption.setObjectName("lcdCaption")
        self.caption.setAlignment(Qt.AlignCenter)
        self._glass_layout = glass_layout
        self._outer = QVBoxLayout()
        self._outer.setContentsMargins(10, 8, 10, 6)
        self._outer.addWidget(glass)
        self._outer.addWidget(self.caption)
        self.setLayout(self._outer)
        self._compact = False

    def set_compact(self, compact: bool) -> None:
        """Short windows: smaller digits, no caption, tighter margins."""
        if compact == self._compact:
            return
        self._compact = compact
        self.caption.setVisible(not compact)
        size = 24 if compact else 34
        self.text.setStyleSheet(f"font-size: {size}px;")
        self._glass_layout.setContentsMargins(18, 4 if compact else 10, 18, 4 if compact else 10)
        self._outer.setContentsMargins(8, 5 if compact else 8, 8, 5 if compact else 6)

    def set_text(self, window: str | None, width: int) -> None:
        shown = (window or "").upper().ljust(width)[:width] if window else "-" * width
        shown = shown.replace(" ", " ")
        if self.text.text() != shown:
            self.text.setText(shown)

    def set_indicator(self, name: str, on: bool) -> None:
        label = self._indicators.get(name)
        if label is None:
            return
        wanted = "lcdIndicatorOn" if on else "lcdIndicatorOff"
        if label.objectName() != wanted:
            label.setObjectName(wanted)
            label.style().unpolish(label)
            label.style().polish(label)


class StepRow(QFrame):
    """One step of the home checklist: round status mark, text and its button."""

    MARKS = {"done": ("✓", LED_ON), "todo": ("!", LED_WAIT), "idle": ("•", LED_OFF)}

    def __init__(self, number: int, title: str, button: QWidget | None = None) -> None:
        super().__init__()
        self.setObjectName("stepRow")
        self.mark = QLabel(str(number))
        self.mark.setFixedSize(38, 38)
        self.mark.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title)
        self.title.setObjectName("stepTitle")
        self.detail = QLabel("")
        self.detail.setObjectName("stepDetail")
        self.detail.setWordWrap(True)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(self.title)
        texts.addWidget(self.detail)
        layout = QHBoxLayout()
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)
        layout.addWidget(self.mark)
        layout.addLayout(texts, 1)
        if button is not None:
            button.setMinimumWidth(190)
            layout.addWidget(button)
        self.setLayout(layout)
        self.set_state("idle", "")

    def set_state(self, state: str, detail: str) -> None:
        symbol, color = self.MARKS.get(state, self.MARKS["idle"])
        self.mark.setText(symbol)
        self.mark.setStyleSheet(
            f"background: {color}; color: #0f1216; border-radius: 19px; font-size: 20px; font-weight: 900;"
        )
        self.detail.setText(detail)


def page_title(title: str, subtitle: str) -> QWidget:
    """Big page heading with a one-line explanation."""
    holder = QWidget()
    layout = QVBoxLayout()
    layout.setContentsMargins(4, 0, 4, 6)
    layout.setSpacing(2)
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    text = QLabel(subtitle)
    text.setObjectName("pageSubtitle")
    text.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(text)
    holder.setLayout(layout)
    return holder


def panel(widget: QWidget) -> QWidget:
    """Small helper for pages that should not stretch their content."""
    holder = QWidget()
    layout = QVBoxLayout()
    layout.addWidget(widget)
    layout.addStretch(1)
    holder.setLayout(layout)
    return holder
