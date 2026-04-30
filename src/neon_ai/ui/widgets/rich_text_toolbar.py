from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextListFormat
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QToolButton,
    QTextEdit,
    QWidget,
)


class RichTextToolbar(QFrame):
    def __init__(
        self,
        editor: QTextEdit,
        token_provider: Callable[[], str | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.editor = editor
        self.token_provider = token_provider

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #f3f4f6; border: 1px solid #d6d9de; border-radius: 4px; }"
            "QToolButton { border: 1px solid transparent; border-radius: 3px; padding: 1px 4px; }"
            "QToolButton:hover { background: #e6e9ee; border-color: #c8ccd3; }"
            "QToolButton:checked { background: #dde7f7; border-color: #9bb7df; }"
        )
        self.setMinimumHeight(30)
        self.setMaximumHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        self.bold_button = self._make_button("B", 28, "Bold", checkable=True)
        bold_font = self.bold_button.font()
        bold_font.setBold(True)
        self.bold_button.setFont(bold_font)
        self.bold_button.clicked.connect(self._toggle_bold)
        layout.addWidget(self.bold_button)

        self.italic_button = self._make_button("I", 28, "Italic", checkable=True)
        italic_font = self.italic_button.font()
        italic_font.setItalic(True)
        self.italic_button.setFont(italic_font)
        self.italic_button.clicked.connect(self._toggle_italic)
        layout.addWidget(self.italic_button)

        self.underline_button = self._make_button("U", 28, "Underline", checkable=True)
        underline_font = self.underline_button.font()
        underline_font.setUnderline(True)
        self.underline_button.setFont(underline_font)
        self.underline_button.clicked.connect(self._toggle_underline)
        layout.addWidget(self.underline_button)

        layout.addWidget(self._make_separator())

        self.align_left_button = self._make_button("L", 28, "Align Left")
        self.align_left_button.clicked.connect(self._align_left)
        layout.addWidget(self.align_left_button)

        self.align_center_button = self._make_button("C", 28, "Align Center")
        self.align_center_button.clicked.connect(self._align_center)
        layout.addWidget(self.align_center_button)

        self.align_right_button = self._make_button("R", 28, "Align Right")
        self.align_right_button.clicked.connect(self._align_right)
        layout.addWidget(self.align_right_button)

        layout.addWidget(self._make_separator())

        self.bullets_button = self._make_button("\u2022", 28, "Bullet List")
        self.bullets_button.clicked.connect(self._insert_bullet_list)
        layout.addWidget(self.bullets_button)

        self.numbered_button = self._make_button("1.", 30, "Numbered List")
        self.numbered_button.clicked.connect(self._insert_numbered_list)
        layout.addWidget(self.numbered_button)

        layout.addWidget(self._make_separator())

        self.insert_token_button = self._make_button("{}", 34, "Insert Token")
        self.insert_token_button.clicked.connect(self.insert_token)
        layout.addWidget(self.insert_token_button)

        layout.addStretch(1)

        self.editor.cursorPositionChanged.connect(self._sync_state)
        self.editor.currentCharFormatChanged.connect(self._sync_state)
        self._sync_state()

    def insert_token(self) -> None:
        if self.token_provider is None:
            return
        token_text = self.token_provider()
        if not token_text:
            return
        cursor = self.editor.textCursor()
        cursor.insertText(str(token_text))
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def _toggle_bold(self) -> None:
        self.editor.setFocus()
        fmt = QTextCharFormat()
        fmt.setFontWeight(
            QFont.Weight.Normal if self.editor.fontWeight() > QFont.Weight.Normal else QFont.Weight.Bold
        )
        self._merge_char_format(fmt)
        self._sync_state()

    def _toggle_italic(self) -> None:
        self.editor.setFocus()
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.fontItalic())
        self._merge_char_format(fmt)
        self._sync_state()

    def _toggle_underline(self) -> None:
        self.editor.setFocus()
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self._merge_char_format(fmt)
        self._sync_state()

    def _align_left(self) -> None:
        self.editor.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.editor.setFocus()

    def _align_center(self) -> None:
        self.editor.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.editor.setFocus()

    def _align_right(self) -> None:
        self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.editor.setFocus()

    def _insert_bullet_list(self) -> None:
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.insertList(QTextListFormat.Style.ListDisc)

    def _insert_numbered_list(self) -> None:
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.insertList(QTextListFormat.Style.ListDecimal)

    def _make_button(self, text: str, width: int, tooltip: str, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setFixedSize(width, 24)
        return button

    def _make_separator(self) -> QFrame:
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setStyleSheet("QFrame { color: #c8ccd3; }")
        return separator

    def _merge_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def _sync_state(self, *_args) -> None:
        self.bold_button.setChecked(self.editor.fontWeight() > QFont.Weight.Normal)
        self.italic_button.setChecked(self.editor.fontItalic())
        self.underline_button.setChecked(self.editor.fontUnderline())
