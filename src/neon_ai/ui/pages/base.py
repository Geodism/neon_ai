from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class LegacyParityPage(QWidget):
    def __init__(self, title: str, legacy_source: str, db_modules: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._legacy_source = legacy_source
        self._db_modules = db_modules

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(heading)

        source = QLabel(f"Legacy source: {legacy_source}")
        source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(source)

        modules = QLabel(f"Legacy DB modules: {', '.join(db_modules) if db_modules else 'None'}")
        modules.setWordWrap(True)
        modules.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(modules)

        note = QLabel(
            "This page target exists so Neon_ai preserves the legacy page boundary. "
            "Port the Tkinter controls and handlers here without redesigning the workflow."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addStretch(1)

    def refresh_data(self) -> None:
        return None
