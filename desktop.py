import sys
from html import escape
from pathlib import Path

from PySide6.QtCore import QEvent, QIODevice, QObject, QRectF, QRunnable, QSaveFile, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from hardware_usage import DEVICES, HardwareUsage
from local_ai import APP_NAME, data_home
from notebook_store import Notebook, is_summary_request


IMPORTING = "資料を取り込み中"
SUMMARIZING = "資料を要約中"
ANSWERING = "回答を生成中"
REREADING = "スキャンしたページを読み取り中"
SOURCE_LABELS = {
    "text": "本文あり",
    "image": "画像を要約できます",
    "ocr": "文字認識済み",
    "scan": "スキャンPDF · 要約時に文字認識",
    "attachment": "元ファイルのみ",
}
SOURCE_NAME_ROLE = Qt.ItemDataRole.UserRole + 1
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
        self.set_example("要約して")

    def set_example(self, example):
        self.example = example
        self.setPlaceholderText(f"Tab で例文を入力：{example}")
        self.setAccessibleDescription(f"空欄で Tab を押すと「{example}」を入力します。")

    def event(self, event):
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Tab
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self.isEnabled() and not self.isReadOnly() and not self.toPlainText()
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


class JobSignals(QObject):
    finished = Signal(int, str, object, str)


class Job(QRunnable):
    def __init__(self, notebook, label, action):
        super().__init__()
        self.notebook = notebook
        self.label = label
        self.progress_text = "要約を生成中…" if label == SUMMARIZING else label + "…"
        self.action = action
        self.signals = JobSignals()

    def run(self):
        try:
            result = self.action()
        except Exception as error:
            self.signals.finished.emit(self.notebook, self.label, None, str(error) or type(error).__name__)
        else:
            self.signals.finished.emit(self.notebook, self.label, result, "")


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


class Window(QMainWindow):
    import_progress = Signal(int, int, int)

    def __init__(self, store):
        super().__init__()
        self.import_progress.connect(self.show_import_progress)
        self.store = store
        self.active = None
        self.job = None
        self.import_job = None
        self.source_states = {}
        self.preview_has_original = False
        self.drafts = {}
        self.excluded = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1220, 780)
        self.setMinimumSize(860, 590)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)

        base = QWidget()
        root = QVBoxLayout(base)
        root.setContentsMargins(16, 12, 16, 12)
        self.setCentralWidget(base)
        header = QFrame()
        header.setObjectName("appHeader")
        toolbar = QHBoxLayout(header)
        toolbar.setContentsMargins(12, 8, 10, 8)
        toolbar.setSpacing(8)
        mark = QLabel()
        mark.setPixmap(self.windowIcon().pixmap(28))
        toolbar.addWidget(mark)
        name = QLabel(self.windowTitle())
        name.setObjectName("appName")
        toolbar.addWidget(name)
        toolbar.addStretch()
        self.books_toggle = QPushButton("ノートブック一覧")
        self.sources_toggle = QPushButton("資料パネル")
        for toggle in (self.books_toggle, self.sources_toggle):
            toggle.setProperty("panelToggle", True)
            toggle.setCheckable(True)
            toggle.setChecked(True)
            toolbar.addWidget(toggle)
        root.addWidget(header)
        areas = QSplitter(Qt.Orientation.Horizontal)
        areas.setChildrenCollapsible(False)
        root.addWidget(areas, 1)

        self.left, left = panel()
        self.left.setMinimumWidth(190)
        left.addWidget(heading("ノートブック"))
        left.addWidget(button("＋ 新規ノートブック", self.create))
        self.books = QListWidget()
        self.books.setAccessibleName("ノートブック一覧")
        self.books.currentItemChanged.connect(self.change_notebook)
        left.addWidget(self.books, 1)
        management = QHBoxLayout()
        self.rename_button = button("名前変更", self.rename)
        self.delete_button = button("削除", self.delete)
        management.addWidget(self.rename_button)
        management.addWidget(self.delete_button)
        left.addLayout(management)
        areas.addWidget(self.left)

        center, chat = panel()
        self.title = heading("")
        chat.addWidget(self.title)
        self.transcript = QTextBrowser()
        self.transcript.setAccessibleName("会話と引用")
        self.transcript.setOpenLinks(False)
        self.transcript.anchorClicked.connect(self.show_citation)
        chat.addWidget(self.transcript, 1)
        self.composer = ExampleComposer()
        self.composer.setAccessibleName("質問")
        self.composer.setMaximumHeight(90)
        self.composer.textChanged.connect(self.update_controls)
        chat.addWidget(self.composer)
        compose_row = QHBoxLayout()
        compose_row.addStretch()
        self.send_button = button("送信  Ctrl+Enter", self.ask, primary=True)
        compose_row.addWidget(self.send_button)
        chat.addLayout(compose_row)
        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.composer)
        self.send_shortcut.activated.connect(self.ask)
        areas.addWidget(center)

        self.right, right = panel()
        self.right.setMinimumWidth(250)
        right.setSpacing(8)
        right.addWidget(heading("資料"))
        self.add_button = button("＋ ファイルを追加", self.add_document)
        right.addWidget(self.add_button)
        drop_hint = QLabel("ファイルをここにドロップできます")
        drop_hint.setProperty("muted", True)
        right.addWidget(drop_hint)
        self.docs = QListWidget()
        self.docs.setObjectName("sourceList")
        self.docs.setMinimumHeight(116)
        self.docs.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.docs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.docs.setAccessibleName("資料一覧。チェックした資料を質問に使います。")
        self.docs.itemChanged.connect(self.toggle_document)
        self.docs.currentItemChanged.connect(self.show_document)
        right.addWidget(self.docs, 3)
        self.summary_button = button("選択中の資料を要約", self.summarize, primary=True)
        right.addWidget(self.summary_button)
        self.source_hint = QLabel()
        self.source_hint.setWordWrap(True)
        self.source_hint.setProperty("muted", True)
        self.source_hint.setTextFormat(Qt.TextFormat.PlainText)
        right.addWidget(self.source_hint)
        self.reread_button = button("文字を読み取る", self.reread)
        right.addWidget(self.reread_button)
        document_actions = QHBoxLayout()
        self.save_button = button("元ファイルを保存", self.save_original)
        self.remove_button = button("削除", self.remove_document)
        document_actions.addWidget(self.save_button)
        document_actions.addWidget(self.remove_button)
        right.addLayout(document_actions)
        self.preview_title = heading("資料・引用プレビュー")
        self.preview_title.setWordWrap(True)
        right.addWidget(self.preview_title)
        self.preview_image = QLabel()
        self.preview_image.setAccessibleName("画像のプレビュー")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.hide()
        right.addWidget(self.preview_image)
        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("資料と引用の原文")
        self.preview.setReadOnly(True)
        right.addWidget(self.preview, 2)
        for editor in (self.transcript, self.composer, self.preview):
            editor.setAcceptDrops(False)
            editor.viewport().setAcceptDrops(False)
        areas.addWidget(self.right)
        self.books_toggle.toggled.connect(self.left.setVisible)
        self.sources_toggle.toggled.connect(self.right.setVisible)
        areas.setSizes([215, 640, 320])
        areas.setStretchFactor(1, 1)

        self.status = QLabel("OpenVINO · ローカル")
        self.status.setProperty("muted", True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        hardware = QFrame()
        hardware.setObjectName("hardwareUsage")
        meters = QHBoxLayout(hardware)
        meters.setContentsMargins(10, 6, 10, 6)
        meters.setSpacing(8)
        label = QLabel("PC全体の使用率")
        label.setProperty("muted", True)
        meters.addWidget(label)
        meters.addStretch(1)
        self.usage_meters = {}
        for device, color in zip(DEVICES, ("#a5bdff", "#88d9c9", "#d7b4fa"), strict=True):
            bar = UsageBar(device, color)
            value = QLabel("測定中")
            value.setMinimumWidth(value.fontMetrics().horizontalAdvance("取得不可"))
            meters.addWidget(QLabel(device))
            meters.addWidget(bar)
            meters.addWidget(value)
            self.usage_meters[device] = bar, value
            meters.addStretch(1)
        root.addWidget(hardware)
        self.hardware_usage = HardwareUsage()
        self.destroyed.connect(self.hardware_usage.close)
        self.usage_timer = QTimer(self)
        self.usage_timer.timeout.connect(self.update_hardware_usage)
        self.usage_timer.start(1000)
        self.load_books()
        dark_caption(self)

    @staticmethod
    def current_id(widget):
        item = widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def load_books(self, selected=None):
        self.books.blockSignals(True)
        self.books.clear()
        row = 0
        for index, (key, name) in enumerate(self.store.list()):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.books.addItem(item)
            if key == selected:
                row = index
        self.books.setCurrentRow(row)
        self.books.blockSignals(False)
        self.change_notebook(self.books.currentItem())

    def change_notebook(self, current, previous=None):
        if self.active is not None:
            self.drafts[self.active] = self.composer.toPlainText()
        self.active = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.title.setText(current.text() if current else "ノートブック")
        self.composer.setPlainText(self.drafts.get(self.active, ""))
        self.load_documents()
        self.render_history()
        self.update_controls()

    def load_documents(self, added=()):
        selected = self.current_id(self.docs)
        self.source_states = self.store.document_states(self.active)
        if added:
            selected = next(
                (key for key in reversed(added) if self.source_states[key] != "attachment"), added[-1]
            )
        self.docs.blockSignals(True)
        self.docs.clear()
        selected_item = None
        first_usable = None
        for key, name in self.store.documents(self.active):
            state = self.source_states[key]
            item = QListWidgetItem(f"{name}\n{SOURCE_LABELS[state]}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setData(SOURCE_NAME_ROLE, name)
            item.setToolTip(f"{name}\n{SOURCE_LABELS[state]}")
            if state != "attachment":
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Unchecked if key in self.excluded else Qt.CheckState.Checked
                )
                if first_usable is None:
                    first_usable = item
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.docs.addItem(item)
            if key == selected:
                selected_item = item
        self.docs.setCurrentItem(selected_item or first_usable or self.docs.item(0))
        self.docs.blockSignals(False)
        self.show_document()

    def render_history(self):
        rows = self.store.history(self.active)
        progress = [
            job.progress_text for job in (self.job, self.import_job)
            if job is not None and job.notebook == self.active
        ]
        entries = []
        if not rows and not progress:
            hint = (
                "資料を追加したら、右側の「選択中の資料を要約」を押すか、下に質問を入力してください。"
                if self.active is not None else "左のサイドバーからノートブックを作成してください。"
            )
            entries.append(f'<p style="color:#abb4c3">{hint}</p>')
        for key, question, answer, refs in rows:
            question = escape(question).replace("\n", "<br>")
            answer = escape(answer).replace("\n", "<br>")
            links = "　".join(
                f'<a style="color:#a5bdff" href="citation:{ref}">[{index}]</a>'
                for index, ref in enumerate(refs, 1)
            )
            if refs:
                links += f'　<a style="color:#a5bdff" href="report:{key}">根拠レポートを保存</a>'
            entries.append(
                f'<p style="color:#a5bdff"><b>{question}</b></p>'
                f"<p>{answer}</p><p>{links}</p>"
            )
        for message in progress:
            entries.append(f'<p style="color:#a5bdff"><b>{escape(message)}</b></p>')
        self.transcript.setHtml("".join(entries))
        self.transcript.moveCursor(QTextCursor.MoveOperation.End)

    def update_controls(self):
        active = self.active is not None
        free = self.job is None and self.import_job is None
        can_import = self.import_job is None and (
            self.job is None or self.job.label in (ANSWERING, SUMMARIZING)
        )
        state = self.source_states.get(self.current_id(self.docs))
        document = state is not None
        self.composer.setEnabled(active)
        self.composer.set_example("画像について説明して" if state == "image" else "要約して")
        self.rename_button.setEnabled(active)
        self.delete_button.setEnabled(active and free)
        self.add_button.setEnabled(active and can_import)
        self.remove_button.setEnabled(document and free)
        self.summary_button.setEnabled(document and free and state != "attachment")
        self.summary_button.setText(
            "資料を要約中…" if self.job is not None and self.job.label == SUMMARIZING else "選択中の資料を要約"
        )
        self.reread_button.setVisible(state == "scan")
        self.reread_button.setEnabled(free and state == "scan")
        self.save_button.setEnabled(document and free and self.preview_has_original)
        self.send_button.setEnabled(active and free and bool(self.composer.toPlainText().strip()))

    def create(self):
        name, accepted = QInputDialog.getText(self, "新規ノートブック", "名前")
        if accepted and name.strip():
            self.load_books(self.store.create(name.strip()))

    def rename(self):
        item = self.books.currentItem()
        if item:
            name, accepted = QInputDialog.getText(self, "名前変更", "名前", text=item.text())
            if accepted and name.strip():
                self.store.rename(self.active, name.strip())
                self.load_books(self.active)

    def delete(self):
        answer = QMessageBox.question(self, "ノートブックを削除", "資料と会話履歴も削除しますか？")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete(self.active)
            self.load_books()

    def toggle_document(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        if self.source_states.get(key) == "attachment":
            return
        if item.checkState() == Qt.CheckState.Checked:
            self.excluded.discard(key)
        else:
            self.excluded.add(key)

    def show_document(self, current=None, previous=None):
        selected = self.docs.currentItem()
        self.preview_has_original = False
        self.preview_image.clear()
        self.preview_image.hide()
        self.preview.hide()
        if selected is None:
            self.preview_title.setText("資料・引用プレビュー")
            self.preview_title.hide()
            self.preview.clear()
            self.source_hint.setText("要約する資料を追加してください。")
        else:
            key = selected.data(Qt.ItemDataRole.UserRole)
            state = self.source_states[key]
            self.preview_title.setText(selected.data(SOURCE_NAME_ROLE))
            self.preview_title.show()
            rows = self.store.document_text(self.active, key, limit=13)
            self.preview_has_original = self.store.has_original(self.active, key)
            excerpt = "\n\n".join(f"{loc}\n{body}" for loc, body in rows[:12])
            if state == "image":
                image = self.store.image_preview(self.active, key)
                pixmap = QPixmap()
                if image and pixmap.loadFromData(image):
                    self.preview_image.setPixmap(pixmap.scaled(
                        210, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    ))
                    self.preview_image.show()
            if state == "attachment":
                hint = "本文を読み取れません。PDF・画像・テキストに変換して追加してください。"
            elif state == "scan":
                hint = "要約するときにページの文字を読み取ります。"
            elif state == "image":
                hint = "要約すると画像の内容を説明します。"
            else:
                hint = "チェックした資料を質問に使います。"
            if len(rows) > 12:
                excerpt += "\n\n… プレビューは先頭のみ表示しています。"
            self.preview.setPlainText(excerpt)
            self.preview.setVisible(bool(excerpt))
            self.source_hint.setText(hint)
        self.update_controls()

    def show_citation(self, url):
        raw = url.path()
        if not raw.isascii() or not raw.isdecimal():
            return
        if url.scheme() == "report":
            self.save_evidence_report(int(raw))
            return
        if url.scheme() != "citation":
            return
        self.sources_toggle.setChecked(True)
        self.preview_image.hide()
        self.preview_title.show()
        result = self.store.citation(self.active, int(raw))
        if result is None:
            self.preview_title.setText("資料が削除されました")
            self.preview.clear()
        else:
            name, location, body = result
            self.preview_title.setText(f"{name} · {location}")
            self.preview.setPlainText(body)
        self.preview.setVisible(result is not None)

    def add_document(self):
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "資料を追加", "", "すべてのファイル (*)"
        )
        self.import_documents([Path(filename) for filename in filenames])

    def dragEnterEvent(self, event):
        if self.add_button.isEnabled() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self.add_button.isEnabled():
            event.ignore()
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.import_documents(paths)
        else:
            event.ignore()

    def import_documents(self, paths):
        if not paths or not self.add_button.isEnabled():
            return
        notebook = self.active
        defer_index = self.job is not None

        def import_batch():
            added = []
            errors = []
            total = len(paths)
            for index, path in enumerate(paths, 1):
                if total > 1:
                    self.import_progress.emit(notebook, index, total)
                try:
                    if not path.is_file():
                        raise ValueError("ファイルを選択してください。")
                    added.append(self.store.import_file(notebook, path, defer_index=defer_index))
                except Exception as error:
                    errors.append(f"{path.name}\n{error}")
            return added, "\n\n".join(errors)

        self.start_job(IMPORTING, import_batch)

    @Slot(int, int, int)
    def show_import_progress(self, notebook, index, total):
        if self.import_job is None or self.import_job.notebook != notebook:
            return
        self.import_job.progress_text = f"{IMPORTING}…（{index} / {total} 件目）"
        if self.active == notebook:
            self.render_history()

    def save_original(self):
        item = self.docs.currentItem()
        notebook = self.active
        document = self.current_id(self.docs)
        filename, _ = QFileDialog.getSaveFileName(
            self, "元ファイルを保存", str(Path.home() / item.data(SOURCE_NAME_ROLE)), "すべてのファイル (*)"
        )
        if not filename:
            return

        def save():
            data = self.store.original(notebook, document)
            if data is None:
                raise ValueError("元ファイルが見つかりません。")
            self.write_output(filename, data)

        self.start_job("元ファイルを保存中", save)

    def save_evidence_report(self, message):
        if self.job is not None or self.import_job is not None:
            self.status.setText("処理が完了してから根拠レポートを保存してください。")
            return
        notebook = self.active
        filename, _ = QFileDialog.getSaveFileName(
            self, "根拠レポートを保存", "yomiquill-evidence.html", "HTML (*.html)"
        )
        if filename:
            def save():
                report = self.store.evidence_report(notebook, message)
                self.write_output(filename, report.encode("utf-8"))

            self.start_job("根拠レポートを保存中", save)

    @staticmethod
    def write_output(filename, data):
        target = QSaveFile(filename)
        if not target.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(target.errorString())
        if target.write(data) != len(data) or not target.commit():
            raise OSError(target.errorString())

    def remove_document(self):
        document = self.current_id(self.docs)
        answer = QMessageBox.question(self, "資料を削除", "この資料を削除しますか？")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete_document(self.active, document)
            self.excluded.discard(document)
            self.load_documents()

    def summarize(self):
        document = self.current_id(self.docs)
        notebook = self.active
        if self.summary_button.isEnabled():
            self.start_job(SUMMARIZING, lambda: self.store.summarize(notebook, document))

    def reread(self):
        notebook = self.active
        document = self.current_id(self.docs)
        if self.reread_button.isEnabled():
            self.start_job(REREADING, lambda: self.store.reprocess_document(notebook, document))

    def ask(self):
        if not self.send_button.isEnabled():
            return
        question = self.composer.toPlainText().strip()
        if len(question) > 2000:
            QMessageBox.information(self, "質問", "質問は2,000文字以内で入力してください。")
            return
        notebook = self.active
        selected = [
            key for key, state in self.source_states.items()
            if key not in self.excluded and state != "attachment"
        ]
        wants_summary = is_summary_request(question)
        document = self.current_id(self.docs)
        if wants_summary and document in selected:
            self.start_job(SUMMARIZING, lambda: self.store.summarize(notebook, document, question=question))
        elif selected:
            label = SUMMARIZING if wants_summary else ANSWERING
            self.start_job(label, lambda: self.store.ask(notebook, question, selected))
        else:
            self.sources_toggle.setChecked(True)
            message = (
                "質問に使う資料にチェックしてください。"
                if any(state != "attachment" for state in self.source_states.values())
                else "質問・要約に使う資料を追加してください。"
            )
            self.status.setText(message)
            return
        self.composer.clear()
        self.pending_question = question

    def start_job(self, label, action):
        if label == IMPORTING:
            if not self.add_button.isEnabled():
                return
            job = self.import_job = Job(self.active, label, action)
        else:
            if self.job is not None or self.import_job is not None:
                return
            self.pending_question = None
            job = self.job = Job(self.active, label, action)
        job.signals.finished.connect(self.finish_job)
        self.status.setText(label + "…")
        self.update_controls()
        self.render_history()
        self.pool.start(job)

    @Slot(int, str, object, str)
    def finish_job(self, notebook, label, result, error):
        pending_question = None
        if label == IMPORTING:
            self.import_job = None
        else:
            self.job = None
            pending_question, self.pending_question = self.pending_question, None
        added, import_error = result if label == IMPORTING and result is not None else ([], "")
        error = error or import_error
        if self.active == notebook:
            self.load_documents(added)
            self.render_history()
        devices = getattr(self.store.model, "devices", {})
        device = " / ".join(
            f"{label}: {devices[key]}"
            for key, label in (("embedding", "検索"), ("chat", "生成"))
            if key in devices
        )
        status = f"OpenVINO · {device}" if device else "OpenVINO · ローカル"
        if self.active == notebook:
            if added:
                ready = sum(self.source_states[key] != "attachment" for key in added)
                status = f"{len(added)} 件を追加 · {ready} 件が要約できます"
            elif not error and label == SUMMARIZING:
                status = "要約を会話に表示しました"
            elif not error and label == REREADING:
                status = "読み取った本文をプレビューに表示しました"
        remaining = self.job or self.import_job
        self.status.setText(remaining.progress_text if remaining is not None else status)
        self.update_controls()
        if error:
            if pending_question:
                if self.active == notebook and not self.composer.toPlainText():
                    self.composer.setPlainText(pending_question)
                elif self.active != notebook and not self.drafts.get(notebook):
                    self.drafts[notebook] = pending_question
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle(label)
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText(error)
            dialog.exec()

    def update_hardware_usage(self):
        for device, sample in self.hardware_usage.sample().items():
            bar, label = self.usage_meters[device]
            value = None if sample is None else round(sample)
            bar.set_usage(value)
            label.setText("取得不可" if value is None else f"{value}%")

    def closeEvent(self, event):
        if self.job is not None or self.import_job is not None:
            self.status.setText("処理が完了してからウィンドウを閉じてください。")
            event.ignore()
        else:
            self.usage_timer.stop()
            self.hardware_usage.close()
            super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = Window(Notebook(data_home() / "notebook.sqlite3"))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
