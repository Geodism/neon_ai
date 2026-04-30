from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from neon_ai.ui.pages.document_studio_page import DocumentStudioPage
from neon_ai.ui.pages.generated_documents_page import GeneratedDocumentsPage
from neon_ai.ui.pages.path_settings_page import PathSettingsPage


class DocumentControlPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.container = getattr(main_window, "container", None)
        self._generated_history_dialog: QDialog | None = None
        self._generated_history_page: GeneratedDocumentsPage | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel("Template & Document Control")
        heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Manage templates and file path rules in one place. Generated history remains "
            "available as a secondary debug/audit view."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        utility_row = QHBoxLayout()
        utility_row.addStretch(1)
        self.open_generated_history_button = QPushButton("Open Generated History (Debug)")
        self.open_generated_history_button.setToolTip(
            "Generated history remains available for audit/debug use, but is no longer a primary workspace tab."
        )
        self.open_generated_history_button.clicked.connect(self.open_generated_history_dialog)
        utility_row.addWidget(self.open_generated_history_button)
        layout.addLayout(utility_row)

        self.tabs = QTabWidget()
        self.templates_page = DocumentStudioPage(main_window)
        self.path_settings_page = PathSettingsPage(main_window)
        self.tabs.addTab(self.templates_page, "Templates")
        self.tabs.addTab(self.path_settings_page, "File Paths")
        self.tabs.setCurrentWidget(self.path_settings_page)
        layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.container = getattr(self.main_window, "container", None)
        for page in (self.templates_page, self.path_settings_page):
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()
        if self._generated_history_page is not None:
            refresh = getattr(self._generated_history_page, "refresh", None)
            if callable(refresh):
                refresh()

    def refresh_data(self) -> None:
        self.refresh()

    def open_generated_history_dialog(self) -> None:
        if self._generated_history_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Generated History")
            dialog.resize(1200, 800)
            dialog_layout = QVBoxLayout(dialog)
            helper = QLabel(
                "Generated document history remains available for audit/debug review. "
                "Primary day-to-day document control now focuses on Templates and File Paths."
            )
            helper.setWordWrap(True)
            helper.setStyleSheet("color: #555;")
            dialog_layout.addWidget(helper)

            self._generated_history_page = GeneratedDocumentsPage(self.main_window)
            dialog_layout.addWidget(self._generated_history_page, 1)

            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            footer = QHBoxLayout()
            footer.addStretch(1)
            footer.addWidget(close_button)
            dialog_layout.addLayout(footer)

            self._generated_history_dialog = dialog

        if self._generated_history_page is not None:
            refresh = getattr(self._generated_history_page, "refresh", None)
            if callable(refresh):
                refresh()

        self._generated_history_dialog.show()
        self._generated_history_dialog.raise_()
        self._generated_history_dialog.activateWindow()
