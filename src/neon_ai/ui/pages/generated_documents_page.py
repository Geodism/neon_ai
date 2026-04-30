from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class GeneratedDocumentsPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Generated History")
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)

        self.container_status_label = QLabel("")
        self.container_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.container_status_label)

        self.generated_service_status_label = QLabel("")
        self.generated_service_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.generated_service_status_label)

        self.info_text = QPlainTextEdit()
        self.info_text.setReadOnly(True)
        layout.addWidget(self.info_text, 1)

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        if self.container is None:
            self.container_status_label.setText("Container: unavailable")
            self.generated_service_status_label.setText("Generated document service: unavailable")
        else:
            self.container_status_label.setText("Container: available")
            generated_service = getattr(self.container, "generated_document_service", None)
            if generated_service is None:
                self.generated_service_status_label.setText("Generated document service: unavailable")
            else:
                self.generated_service_status_label.setText("Generated document service: available")

        self.info_text.setPlainText(
            "Generated files will be listed here later.\n\n"
            "This section will eventually show generated-document history, output paths, and source context."
        )
