from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.services.document_token_catalog import (
    TOKEN_SYNTAX_NOTE,
    grouped_document_tokens,
)
from neon_ai.services.setup_readiness_service import (
    check_database_config,
    check_email_config,
    check_llm_config,
    check_storage_paths,
    get_setup_readiness_summary,
)
from neon_ai.ui.pages.document_studio_page import DocumentStudioPage
from neon_ai.ui.pages.generated_documents_page import GeneratedDocumentsPage
from neon_ai.ui.pages.path_settings_page import PathSettingsPage


class TemplateInstructionsPage(QWidget):
    def __init__(self, main_window=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self._category_items: list[tuple[str, QTreeWidgetItem]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Template Instructions")
        heading.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(heading)

        intro = QLabel(
            "Tokens are placeholders used in templates. When a document is generated, "
            "Argon replaces each token with real project, customer, vendor, estimate, RFQ, "
            "purchase order, or invoice data."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        example = QLabel(
            "{VendorName} becomes TEST_3MO_Eecol\n"
            "{RFQNumber} becomes 10\n"
            "{RFQRequestedMaterialTable} becomes a material table"
        )
        example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        example.setStyleSheet(
            "background: #f5f5f5; border: 1px solid #d8d8d8; border-radius: 6px; "
            "padding: 10px; font-family: Consolas, 'Courier New', monospace;"
        )
        layout.addWidget(example)

        syntax_note = QLabel(TOKEN_SYNTAX_NOTE)
        syntax_note.setWordWrap(True)
        syntax_note.setStyleSheet("color: #555;")
        layout.addWidget(syntax_note)

        rfq_note = QLabel(
            "For RFQ material placement, include {RFQRequestedMaterialTable} in the RFQ "
            "Delivery Body template where the requested material table should appear. "
            "Legacy RFQ templates that already use {{RFQRequestedMaterialTable}} still render."
        )
        rfq_note.setWordWrap(True)
        rfq_note.setStyleSheet("color: #555;")
        layout.addWidget(rfq_note)

        search_label = QLabel("Search tokens...")
        search_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(search_label)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search tokens...")
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.token_tree = QTreeWidget()
        self.token_tree.setColumnCount(4)
        self.token_tree.setHeaderLabels(["Token", "Description", "Example Output", "Used In"])
        self.token_tree.setAlternatingRowColors(True)
        self.token_tree.setRootIsDecorated(True)
        self.token_tree.setUniformRowHeights(True)
        self.token_tree.setSortingEnabled(False)
        self.token_tree.header().setStretchLastSection(True)
        self.token_tree.header().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.token_tree, 1)

        self._populate_token_tree()

    def refresh(self) -> None:
        self._apply_filter(self.search_edit.text())

    def _populate_token_tree(self) -> None:
        self.token_tree.clear()
        self._category_items.clear()

        group_font = QFont()
        group_font.setBold(True)

        for category, token_records in grouped_document_tokens():
            category_item = QTreeWidgetItem([category, "", "", ""])
            for column in range(4):
                category_item.setFont(column, group_font)
            category_item.setData(0, Qt.ItemDataRole.UserRole, category.lower())
            self.token_tree.addTopLevelItem(category_item)
            self._category_items.append((category, category_item))

            for entry in token_records:
                child = QTreeWidgetItem(
                    [
                        entry.token,
                        entry.description,
                        entry.example_output,
                        entry.used_in,
                    ]
                )
                search_blob = " ".join(
                    [
                        entry.category,
                        entry.token,
                        entry.description,
                        entry.example_output,
                        entry.used_in,
                    ]
                ).lower()
                child.setData(0, Qt.ItemDataRole.UserRole, search_blob)
                category_item.addChild(child)
            category_item.setExpanded(True)

        for column in range(4):
            self.token_tree.resizeColumnToContents(column)

    def _apply_filter(self, raw_query: str) -> None:
        query = str(raw_query or "").strip().lower()
        has_query = bool(query)

        for category_name, category_item in self._category_items:
            category_match = query in category_name.lower() if has_query else True
            visible_child_count = 0

            for child_index in range(category_item.childCount()):
                child = category_item.child(child_index)
                search_blob = str(child.data(0, Qt.ItemDataRole.UserRole) or "")
                child_match = not has_query or category_match or query in search_blob
                child.setHidden(not child_match)
                if child_match:
                    visible_child_count += 1

            category_visible = not has_query or category_match or visible_child_count > 0
            category_item.setHidden(not category_visible)
            category_item.setExpanded(category_visible and (has_query or visible_child_count > 0))


class SetupReadinessPage(QWidget):
    def __init__(self, main_window=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Setup / Readiness")
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)

        intro = QLabel(
            "Review private-test readiness for identity, email outbox, LLM lanes, database, "
            "storage paths, and safety mode. This tab is read-only and never displays secrets."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        layout.addWidget(intro)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        button_row.addWidget(self.refresh_button)

        self.check_database_button = QPushButton("Check Database")
        self.check_database_button.clicked.connect(lambda: self._show_single_check("Database", check_database_config()))
        button_row.addWidget(self.check_database_button)

        self.check_email_button = QPushButton("Check Email Config")
        self.check_email_button.clicked.connect(lambda: self._show_single_check("Email Outbox", check_email_config()))
        button_row.addWidget(self.check_email_button)

        self.check_llm_button = QPushButton("Check LLM Config")
        self.check_llm_button.clicked.connect(lambda: self._show_single_check("LLM Lanes", check_llm_config()))
        button_row.addWidget(self.check_llm_button)

        self.check_storage_button = QPushButton("Check Storage Paths")
        self.check_storage_button.clicked.connect(lambda: self._show_single_check("Storage / Document Paths", check_storage_paths()))
        button_row.addWidget(self.check_storage_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #444;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.summary_text = QPlainTextEdit()
        self.summary_text.setObjectName("setup_readiness_summary_text")
        self.summary_text.setReadOnly(True)
        self.summary_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.summary_text.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 12px;")
        layout.addWidget(self.summary_text, 1)

        self._set_initial_message()

    def refresh(self) -> None:
        try:
            summary = get_setup_readiness_summary()
        except Exception as exc:
            self.status_label.setText("Setup readiness could not be loaded.")
            self.summary_text.setPlainText(f"Error: {exc}")
            return

        ready = bool(summary.get("ready_for_private_test"))
        self.status_label.setText(
            "Private-test readiness: READY" if ready else "Private-test readiness: needs attention"
        )
        self.summary_text.setPlainText(self._format_summary(summary))

    def refresh_data(self) -> None:
        self.refresh()

    def _set_initial_message(self) -> None:
        self.status_label.setText("Click Refresh to check readiness.")
        self.summary_text.setPlainText(
            "Setup / Readiness is read-only.\n"
            "It does not send email, call live LLM providers, mutate business records, or print secrets."
        )

    def _show_single_check(self, title: str, payload: dict) -> None:
        self.status_label.setText(f"{title} checked.")
        lines = [title, "=" * len(title), ""]
        lines.extend(self._format_mapping(payload, indent=0))
        self.summary_text.setPlainText("\n".join(lines))

    def _format_summary(self, summary: dict) -> str:
        lines: list[str] = []
        lines.append("Setup / Readiness Summary")
        lines.append("=========================")
        lines.append("")
        lines.append(f"Ready for private test: {'Yes' if summary.get('ready_for_private_test') else 'No'}")
        lines.append("")
        lines.extend(self._format_section("Company / Operator Identity", summary.get("company_operator") or {}))
        lines.extend(self._format_section("Email Outbox", summary.get("email") or {}))
        lines.extend(self._format_llm_section(summary.get("llm") or {}))
        lines.extend(self._format_section("Database Connection", summary.get("database") or {}))
        lines.extend(self._format_storage_section(summary.get("storage") or {}))
        lines.extend(self._format_section("Safety Mode", summary.get("safety") or {}))
        warnings = list(summary.get("warnings") or [])
        lines.append("Warnings")
        lines.append("--------")
        if warnings:
            lines.extend(f"- {warning}" for warning in warnings)
        else:
            lines.append("- None")
        lines.append("")
        lines.append("Safety Notes")
        lines.append("------------")
        lines.append("- This tab is status-only and read-only.")
        lines.append("- API keys, SMTP passwords, and app passwords are never displayed.")
        lines.append("- Live email and live LLM checks remain explicit actions outside this tab.")
        return "\n".join(lines)

    def _format_section(self, title: str, payload: dict) -> list[str]:
        lines = [title, "-" * len(title)]
        lines.extend(self._format_mapping(payload, indent=0, skip_keys={"warnings"}))
        section_warnings = list(payload.get("warnings") or [])
        if section_warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in section_warnings)
        lines.append("")
        return lines

    def _format_llm_section(self, payload: dict) -> list[str]:
        lines = ["LLM Lanes", "---------"]
        lines.append(f"Configured: {'Yes' if payload.get('configured') else 'No'}")
        lines.append(f"Safe mode: {'Yes' if payload.get('safe_mode') else 'No'}")
        lines.append(f"Remote escalation only: {'Yes' if payload.get('remote_escalation_only') else 'No'}")
        lines.append(f"Max remote calls/run: {payload.get('max_remote_calls_per_run')}")
        lines.append(f"Max remote strong calls/run: {payload.get('max_remote_strong_calls_per_run')}")
        lines.append(f"Max remote input chars: {payload.get('max_remote_input_chars')}")
        lanes = payload.get("lanes") or {}
        for lane_name in ("local", "remote_fast", "remote_strong"):
            lane = lanes.get(lane_name) or {}
            lines.append(f"{lane_name}:")
            lines.append(f"  provider: {lane.get('provider') or 'Not configured'}")
            lines.append(f"  model: {lane.get('model') or 'Not configured'}")
            lines.append(f"  available: {'Yes' if lane.get('available') else 'No'}")
            lines.append(f"  allow_live: {'Yes' if lane.get('allow_live') else 'No'}")
            if "key_present" in lane:
                lines.append(f"  key_present: {'Yes' if lane.get('key_present') else 'No'}")
        section_warnings = list(payload.get("warnings") or [])
        if section_warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in section_warnings)
        lines.append("")
        return lines

    def _format_storage_section(self, payload: dict) -> list[str]:
        lines = ["Storage / Document Paths", "------------------------"]
        env_paths = list(payload.get("env_paths") or [])
        path_rules = list(payload.get("document_path_rules") or [])
        if env_paths:
            lines.append("Environment paths:")
            lines.extend(self._format_path_records(env_paths))
        else:
            lines.append("Environment paths: none configured")
        if path_rules:
            lines.append("Document path rules:")
            lines.extend(self._format_path_records(path_rules))
        else:
            lines.append("Document path rules: none loaded")
        lines.append(f"Obvious missing path: {'Yes' if payload.get('obvious_missing_path') else 'No'}")
        section_warnings = list(payload.get("warnings") or [])
        if section_warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in section_warnings)
        lines.append("")
        return lines

    def _format_path_records(self, records: list[dict]) -> list[str]:
        lines: list[str] = []
        for record in records:
            lines.append(f"- {record.get('label')}:")
            lines.append(f"  path: {record.get('path') or 'Not configured'}")
            lines.append(f"  source: {record.get('source') or 'Unknown'}")
            lines.append(f"  exists: {'Yes' if record.get('exists') else 'No'}")
            lines.append(f"  parent exists: {'Yes' if record.get('parent_exists') else 'No'}")
            lines.append(f"  can create: {'Yes' if record.get('can_create') else 'No'}")
        return lines

    def _format_mapping(
        self,
        payload: dict,
        *,
        indent: int = 0,
        skip_keys: set[str] | None = None,
    ) -> list[str]:
        skip_keys = skip_keys or set()
        prefix = " " * indent
        lines: list[str] = []
        for key, value in payload.items():
            if key in skip_keys:
                continue
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.extend(self._format_mapping(value, indent=indent + 2, skip_keys=skip_keys))
            elif isinstance(value, list):
                lines.append(f"{prefix}{key}:")
                if value:
                    for item in value:
                        lines.append(f"{prefix}- {item}")
                else:
                    lines.append(f"{prefix}- None")
            else:
                lines.append(f"{prefix}{key}: {value}")
        return lines


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
            "Manage templates, token instructions, and file path rules in one place. "
            "Generated history remains available as a secondary debug/audit view."
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
        self.instructions_page = TemplateInstructionsPage(main_window)
        self.path_settings_page = PathSettingsPage(main_window)
        self.setup_readiness_page = SetupReadinessPage(main_window)
        self.tabs.addTab(self.templates_page, "Templates")
        self.tabs.addTab(self.instructions_page, "Instructions")
        self.tabs.addTab(self.path_settings_page, "File Paths")
        self.tabs.addTab(self.setup_readiness_page, "Setup / Readiness")
        self.tabs.setCurrentWidget(self.templates_page)
        layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.container = getattr(self.main_window, "container", None)
        for page in (
            self.templates_page,
            self.instructions_page,
            self.path_settings_page,
            self.setup_readiness_page,
        ):
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
                "Primary day-to-day document control now focuses on Templates, Instructions, File Paths, "
                "and Setup / Readiness."
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
