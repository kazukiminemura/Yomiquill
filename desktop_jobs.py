"""Run desktop jobs and report their results back to the UI thread."""

from PySide6.QtCore import QObject, QRunnable, Signal


IMPORTING = "資料を取り込み中"
SUMMARIZING = "資料を要約中"
ANSWERING = "回答を生成中"
REREADING = "スキャンしたページを読み取り中"


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
