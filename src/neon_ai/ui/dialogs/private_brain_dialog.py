from __future__ import annotations

import os
import threading

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class PrivateBrainDialog(QDialog):
    append_text = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Argon-Prime Executive Chat")
        self.resize(500, 600)
        self.setModal(True)
        self.private_brain_disabled = os.environ.get("NEON_DISABLE_PRIVATE_BRAIN") == "1"
        self._brain_instance = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Executive Chat"))

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        layout.addWidget(self.chat_display, 1)

        input_frame = QWidget()
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(0, 0, 0, 0)

        self.user_input = QLineEdit()
        self.user_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.user_input, 1)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_button)
        layout.addWidget(input_frame)

        self.append_text.connect(self.update_display)

        if self.private_brain_disabled:
            self.chat_display.setPlainText("Private Brain is disabled by NEON_DISABLE_PRIVATE_BRAIN=1.\n")
            self.user_input.setPlaceholderText("Private Brain is disabled.")
            self.user_input.setEnabled(False)
            self.send_button.setEnabled(False)
        else:
            self.user_input.setPlaceholderText("Ask Argon-Prime a question...")

    def _get_brain_instance(self):
        if self._brain_instance is None:
            from neon_ai.automation.local_brain import get_brain_instance

            self._brain_instance = get_brain_instance()
        return self._brain_instance

    def send_message(self) -> None:
        if self.private_brain_disabled:
            self.update_display("ARGON: Private Brain is disabled by NEON_DISABLE_PRIVATE_BRAIN=1.\n\n")
            return

        prompt = self.user_input.text().strip()
        if not prompt:
            return

        self.update_display(f"YOU: {prompt}\n")
        self.user_input.setText("")
        threading.Thread(target=self.get_ai_response, args=(prompt,), daemon=True).start()

    def get_ai_response(self, prompt: str) -> None:
        try:
            self.append_text.emit("ARGON: (Thinking locally...)\n")
            result = self._get_brain_instance().chat_with_context(prompt)
            debug_lines = result.get("debug") or []
            if debug_lines:
                self.append_text.emit(f"[Resolver] {' | '.join(debug_lines)}\n")
            self.append_text.emit(f"ARGON: {result.get('reply')}\n\n")
        except Exception as exc:
            self.append_text.emit(f"[ERROR]: {exc}\n")

    def update_display(self, text: str) -> None:
        self.chat_display.moveCursor(QTextCursor.MoveOperation.End)
        self.chat_display.insertPlainText(text)
        self.chat_display.moveCursor(QTextCursor.MoveOperation.End)
