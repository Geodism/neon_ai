from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class DocumentStudioPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Templates")
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)

        self.container_status_label = QLabel("")
        self.container_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.container_status_label)

        self.catalog_status_label = QLabel("")
        self.catalog_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.catalog_status_label)

        self.token_help_text = QPlainTextEdit()
        self.token_help_text.setReadOnly(True)
        layout.addWidget(self.token_help_text, 1)

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        if self.container is None:
            self.container_status_label.setText("Container: unavailable")
            self.catalog_status_label.setText("Document catalog service: unavailable")
            self.token_help_text.setPlainText(
                "The Templates area will show template definitions once the app container is available."
            )
            return

        self.container_status_label.setText("Container: available")
        catalog_service = getattr(self.container, "document_catalog_service", None)
        if catalog_service is None:
            self.catalog_status_label.setText("Document catalog service: unavailable")
            self.token_help_text.setPlainText(
                "Template tools will appear here once document_catalog_service is wired."
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
