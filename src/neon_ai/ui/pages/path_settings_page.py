from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class PathSettingsPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("File Paths")
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)

        self.container_status_label = QLabel("")
        self.container_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.container_status_label)

        self.path_service_status_label = QLabel("")
        self.path_service_status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.path_service_status_label)

        self.info_text = QPlainTextEdit()
        self.info_text.setReadOnly(True)
        layout.addWidget(self.info_text, 1)

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        if self.container is None:
            self.container_status_label.setText("Container: unavailable")
            self.path_service_status_label.setText("Path rule service: unavailable")
        else:
            self.container_status_label.setText("Container: available")
            path_service = getattr(self.container, "document_path_rule_service", None)
            if path_service is None:
                self.path_service_status_label.setText("Path rule service: unavailable")
            else:
                self.path_service_status_label.setText("Path rule service: available")

        self.info_text.setPlainText(
            "Path rules will be managed here later.\n\n"
            "This section will control:\n"
            "- local_root\n"
            "- fallback_root\n"
            "- relative_pattern\n"
            "- filename_pattern"
        )
