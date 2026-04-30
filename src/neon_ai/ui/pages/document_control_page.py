from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from neon_ai.ui.pages.document_studio_page import DocumentStudioPage
from neon_ai.ui.pages.generated_documents_page import GeneratedDocumentsPage
from neon_ai.ui.pages.path_settings_page import PathSettingsPage


class DocumentControlPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.container = getattr(main_window, "container", None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel("Template & Document Control")
        heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(heading)

        intro = QLabel(
            "Use this area to manage document templates, file path rules, and generated-document history."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #444;")
        layout.addWidget(intro)

        self.container_status_label = QLabel("")
        self.container_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.container_status_label)

        self.catalog_status_label = QLabel("")
        self.catalog_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.catalog_status_label)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_button)

        self.tabs = QTabWidget()
        self.templates_page = DocumentStudioPage(main_window)
        self.path_settings_page = PathSettingsPage(main_window)
        self.generated_documents_page = GeneratedDocumentsPage(main_window)
        self.tabs.addTab(self.templates_page, "Templates")
        self.tabs.addTab(self.path_settings_page, "File Paths")
        self.tabs.addTab(self.generated_documents_page, "Generated History")
        layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.container = getattr(self.main_window, "container", None)
        if self.container is None:
            self.container_status_label.setText("Container: unavailable")
            self.catalog_status_label.setText("Document catalog service: unavailable")
        else:
            self.container_status_label.setText("Container: available")
            catalog_service = getattr(self.container, "document_catalog_service", None)
            if catalog_service is None:
                self.catalog_status_label.setText("Document catalog service: unavailable")
            else:
                self.catalog_status_label.setText("Document catalog service: available")

        for page in (
            self.templates_page,
            self.path_settings_page,
            self.generated_documents_page,
        ):
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()

    def refresh_data(self) -> None:
        self.refresh()
