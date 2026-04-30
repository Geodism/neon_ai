from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit, QVBoxLayout, QWidget


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

        self.container_status_label = QLabel("")
        self.container_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.container_status_label)

        self.catalog_status_label = QLabel("")
        self.catalog_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.catalog_status_label)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_button)

        self.token_help_text = QPlainTextEdit()
        self.token_help_text.setReadOnly(True)
        layout.addWidget(self.token_help_text, 1)

    def refresh(self) -> None:
        self.container = getattr(self.main_window, "container", None)
        if self.container is None:
            self.container_status_label.setText("Container: unavailable")
            self.catalog_status_label.setText("Document catalog service: unavailable")
            self.token_help_text.setPlainText(
                "Template & Document Control is not wired to an app container yet."
            )
            return

        self.container_status_label.setText("Container: available")
        catalog_service = getattr(self.container, "document_catalog_service", None)
        if catalog_service is None:
            self.catalog_status_label.setText("Document catalog service: unavailable")
            self.token_help_text.setPlainText(
                "The app container is available, but document_catalog_service is missing."
            )
            return

        self.catalog_status_label.setText("Document catalog service: available")
        try:
            token_help = catalog_service.get_token_help()
            if not token_help:
                self.token_help_text.setPlainText("No token help is available yet.")
                return

            lines = ["Available Tokens", ""]
            for token_name, description in sorted(token_help.items()):
                lines.append(token_name)
                lines.append(f"  {description}")
                lines.append("")
            self.token_help_text.setPlainText("\n".join(lines).strip())
        except Exception as exc:
            self.catalog_status_label.setText("Document catalog service: error")
            self.token_help_text.setPlainText(
                f"Could not load token help right now.\n\nDetails: {exc}"
            )

    def refresh_data(self) -> None:
        self.refresh()
