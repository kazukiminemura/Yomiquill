"""Qt widgets, styling, and window decoration used by the desktop UI."""

import sys

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


STYLE = """
QWidget { background: #191b20; color: #ecedf2; font-size: 14px; }
QFrame#appHeader { background: #202329; border: 1px solid #30343d; border-radius: 12px; }
QFrame#hardwareUsage { background: #202329; border: 1px solid #30343d; border-radius: 8px; }
QLabel#appName { font-size: 17px; font-weight: 600; }
QPushButton[panelToggle="true"] {
    min-height: 34px; padding: 0 12px; background: transparent;
    border: 1px solid transparent; border-radius: 7px; color: #c8d0de;
}
QPushButton[panelToggle="true"]:checked { background: #303c53; color: #e3ebff; }
QPushButton[panelToggle="true"]:hover { background: #343f53; }
QPushButton[panelToggle="true"]:pressed { background: #41516b; }
QPushButton[panelToggle="true"]:focus { border-color: #9cb7ff; }
QFrame[panel="true"] { background: #202329; border: 1px solid #30343d; border-radius: 12px; }
QLabel, QCheckBox { background: transparent; }
QLabel[heading="true"] { font-size: 19px; font-weight: bold; }
QLabel[muted="true"] { color: #abb4c3; }
QListWidget, QTextBrowser, QPlainTextEdit {
    background: #191b20; border: 1px solid #383d47; border-radius: 8px; padding: 6px;
    selection-background-color: #33486e;
}
QListWidget::item { padding: 7px 4px; }
QListWidget::item:selected { background: #33486e; color: #fff; border-radius: 4px; }
QListWidget#sourceList::item { padding: 4px; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px 0; }
QScrollBar::handle:vertical { min-height: 28px; background: #555e6f; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #758099; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QPushButton {
    background: #282d36; border: 1px solid #3b4352; border-radius: 7px;
    padding: 7px 10px;
}
QPushButton:hover { background: #323b4b; }
QPushButton:focus, QListWidget:focus, QPlainTextEdit:focus { border-color: #9cb7ff; }
QPushButton:disabled { color: #808a9b; }
QPushButton[primary="true"] { background: #a5bdff; color: #151b2c; font-weight: bold; }
QPushButton[primary="true"]:hover { background: #bfd0ff; }
QPushButton[primary="true"]:disabled { background: #373e50; color: #a1a8b8; }
QSplitter::handle { background: #191b20; }
"""


class ExampleComposer(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setTabChangesFocus(True)
        self.set_example(None, "質問に使う資料を追加してください。")

    def set_example(self, example, hint=""):
        self.example = example
        self.setPlaceholderText(f"Tab で例文を入力：{example}" if example else hint)
        self.setAccessibleDescription(f"空欄で Tab を押すと「{example}」を入力します。" if example else hint)

    def event(self, event):
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Tab
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self.example and self.isEnabled() and not self.isReadOnly() and not self.toPlainText()
        ):
            self.insertPlainText(self.example)
            return True
        return super().event(event)


class UsageBar(QWidget):
    def __init__(self, device, color):
        super().__init__()
        self.device = device
        self.color = QColor(color)
        self.level = 0
        self.setFixedSize(88, 11)
        self.set_usage(None)

    def set_usage(self, value):
        text = "取得不可" if value is None else f"{value}%"
        self.setAccessibleName(f"{self.device} 使用率 {text}")
        self.level = 0 if value is None else (value + 9) // 10
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            step = (self.width() + 2) / 10
            for index in range(10):
                painter.setBrush(self.color if index < self.level else QColor("#596374"))
                painter.drawRoundedRect(QRectF(index * step, 1, step - 2, self.height() - 2), 2, 2)


def heading(text):
    label = QLabel(text)
    label.setProperty("heading", True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def button(text, callback, primary=False):
    control = QPushButton(text)
    control.setProperty("primary", primary)
    control.clicked.connect(callback)
    return control


def panel():
    frame = QFrame()
    frame.setProperty("panel", True)
    box = QVBoxLayout(frame)
    box.setContentsMargins(14, 14, 14, 14)
    box.setSpacing(10)
    return frame, box


def app_icon():
    mark = QPixmap(64, 64)
    mark.fill(Qt.GlobalColor.transparent)
    with QPainter(mark) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#a5bdff"))
        painter.drawRoundedRect(QRectF(0, 0, 32, 32), 8, 8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#202b47"), 1.6))
        painter.drawRoundedRect(QRectF(9, 7, 14, 18), 2, 2)
        painter.drawLine(13, 7, 13, 25)
        painter.drawLine(16, 12, 20, 12)
        painter.drawLine(16, 16, 20, 16)
    return QIcon(mark)


def dark_caption(window):
    if sys.platform != "win32":
        return
    from ctypes import byref, sizeof, windll
    from ctypes.wintypes import BOOL, DWORD, HWND

    hwnd = HWND(int(window.winId()))
    set_attribute = windll.dwmapi.DwmSetWindowAttribute
    # Keep native caption controls, window snapping and the system menu.
    for attribute, value in (
        (20, BOOL(True)),       # DWMWA_USE_IMMERSIVE_DARK_MODE
        (35, DWORD(0x292320)),  # DWMWA_CAPTION_COLOR, 0x00BBGGRR
    ):
        set_attribute(hwnd, attribute, byref(value), sizeof(value))
