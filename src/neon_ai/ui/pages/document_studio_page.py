from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class DocumentStudioPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)
        self._template_summaries_by_id: dict[int, object] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Templates")
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Document Type"))
        self.document_type_combo = QComboBox()
        self.document_type_combo.currentIndexChanged.connect(self._on_document_type_changed)
        controls.addWidget(self.document_type_combo, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)
        layout.addLayout(controls)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.status_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        list_group = QGroupBox("Templates")
        list_layout = QVBoxLayout(list_group)
        self.template_list = QListWidget()
        self.template_list.currentItemChanged.connect(self._on_template_selected)
        list_layout.addWidget(self.template_list)
        splitter.addWidget(list_group)

        details_group = QGroupBox("Details")
        details_layout = QVBoxLayout(details_group)
        details_form = QFormLayout()
        details_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.template_name_value = QLabel("")
        self.document_type_value = QLabel("")
        self.template_kind_value = QLabel("")
        self.content_format_value = QLabel("")
        self.output_format_value = QLabel("")
        self.subject_line_value = QLabel("")
        self.version_number_value = QLabel("")
        self.active_status_value = QLabel("")
        self.notes_value = QLabel("")
        self.change_summary_value = QLabel("")

        for value_widget in (
            self.template_name_value,
            self.document_type_value,
            self.template_kind_value,
            self.content_format_value,
            self.output_format_value,
            self.subject_line_value,
            self.version_number_value,
            self.active_status_value,
            self.notes_value,
            self.change_summary_value,
        ):
            value_widget.setWordWrap(True)
            value_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        details_form.addRow("Template Name", self.template_name_value)
        details_form.addRow("Document Type", self.document_type_value)
        details_form.addRow("Template Kind", self.template_kind_value)
        details_form.addRow("Content Format", self.content_format_value)
        details_form.addRow("Output Format", self.output_format_value)
        details_form.addRow("Subject Line", self.subject_line_value)
        details_form.addRow("Current Version", self.version_number_value)
        details_form.addRow("Active", self.active_status_value)
        details_form.addRow("Notes", self.notes_value)
        details_form.addRow("Change Summary", self.change_summary_value)
        details_layout.addLayout(details_form)

        body_heading = QLabel("Body Content")
        body_heading.setStyleSheet("font-weight: 700;")
        details_layout.addWidget(body_heading)

        self.body_content_text = QPlainTextEdit()
        self.body_content_text.setReadOnly(True)
        details_layout.addWidget(self.body_content_text, 1)
        splitter.addWidget(details_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        token_group = QGroupBox("Token Help")
        token_layout = QVBoxLayout(token_group)
        self.token_help_text = QPlainTextEdit()
        self.token_help_text.setReadOnly(True)
        token_layout.addWidget(self.token_help_text)
        layout.addWidget(token_group)

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        self._populate_token_help()
        if self.container is None:
            self.status_label.setText("Container unavailable. Templates cannot be loaded yet.")
            self._clear_document_types()
            self._clear_templates("Templates will appear here once the app container is available.")
            self._clear_details("Select a template to inspect its current version.")
            return

        catalog_service = getattr(self.container, "document_catalog_service", None)
        if catalog_service is None:
            self.status_label.setText("Document catalog service unavailable. Templates cannot be loaded yet.")
            self._clear_document_types()
            self._clear_templates("Templates will appear here once document_catalog_service is wired.")
            self._clear_details("Select a template to inspect its current version.")
            return

        try:
            document_types = catalog_service.list_document_types()
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_document_types()
            self._clear_templates("Could not load templates right now.")
            self._clear_details("Select a template to inspect its current version.")
            return

        if not document_types:
            self.status_label.setText("No document types are available yet. Verify schema and seeded data.")
            self._clear_document_types()
            self._clear_templates("No templates are available yet.")
            self._clear_details("Select a template to inspect its current version.")
            return

        self.status_label.setText("Document catalog service available.")
        self._populate_document_types(document_types)
        self._refresh_templates_for_current_type()

    def _on_document_type_changed(self) -> None:
        if self.document_type_combo.count() == 0:
            return
        self._refresh_templates_for_current_type()

    def _refresh_templates_for_current_type(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        document_type_code = self.document_type_combo.currentData()
        if catalog_service is None or not document_type_code:
            self._clear_templates("No templates are available yet.")
            self._clear_details("Select a template to inspect its current version.")
            return

        try:
            template_summaries = catalog_service.list_templates(document_type_code=document_type_code)
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_templates("Could not load templates right now.")
            self._clear_details("Select a template to inspect its current version.")
            return

        self._populate_template_list(template_summaries)

    def _on_template_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None:
            self._clear_details("Select a template to inspect its current version.")
            return

        template_id = current.data(Qt.ItemDataRole.UserRole)
        if template_id is None:
            self._clear_details("Select a template to inspect its current version.")
            return

        self._load_template_version(int(template_id))

    def _load_template_version(self, template_id: int) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self._clear_details("Document catalog service unavailable.")
            return

        try:
            version = catalog_service.get_template_version(template_id=template_id)
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_details("Could not load template details right now.")
            return

        summary = self._template_summaries_by_id.get(template_id)
        if version is None or summary is None:
            self._clear_details("No active template version is available for this template.")
            return

        self.template_name_value.setText(version.template_name)
        self.document_type_value.setText(version.document_type_code)
        self.template_kind_value.setText(str(version.kind.value))
        self.content_format_value.setText(version.content_format or summary.content_format or "(not set)")
        self.output_format_value.setText(str(version.output_format.value))
        self.subject_line_value.setText(version.subject_line or "(none)")
        self.version_number_value.setText(str(version.version_number))
        self.active_status_value.setText("Yes" if version.is_active else "No")
        self.notes_value.setText(version.notes or summary.notes or "(none)")
        self.change_summary_value.setText(version.change_summary or "(none)")
        self.body_content_text.setPlainText(version.body_content or "")

    def _populate_document_types(self, document_types: list[object]) -> None:
        selected_code = self.document_type_combo.currentData()
        self.document_type_combo.blockSignals(True)
        self.document_type_combo.clear()
        selected_index = 0
        customer_invoice_index = 0

        for index, document_type in enumerate(document_types):
            label = f"{document_type.display_name} ({document_type.document_type_code})"
            self.document_type_combo.addItem(label, document_type.document_type_code)
            if document_type.document_type_code == selected_code:
                selected_index = index
            if document_type.document_type_code == "CUSTOMER_INVOICE":
                customer_invoice_index = index

        target_index = customer_invoice_index if any(
            item.document_type_code == "CUSTOMER_INVOICE" for item in document_types
        ) else selected_index
        self.document_type_combo.setCurrentIndex(target_index)
        self.document_type_combo.blockSignals(False)

    def _populate_template_list(self, template_summaries: list[object]) -> None:
        self.template_list.blockSignals(True)
        self.template_list.clear()
        self._template_summaries_by_id = {}

        if not template_summaries:
            self._clear_templates("No templates were found for the selected document type.")
            self.template_list.blockSignals(False)
            return

        first_item = None
        for summary in template_summaries:
            item = QListWidgetItem(f"{summary.template_name} [{summary.kind.value}]")
            item.setData(Qt.ItemDataRole.UserRole, summary.template_id)
            self.template_list.addItem(item)
            self._template_summaries_by_id[summary.template_id] = summary
            if first_item is None:
                first_item = item

        self.template_list.blockSignals(False)
        if first_item is not None:
            self.template_list.setCurrentItem(first_item)
        else:
            self._clear_details("Select a template to inspect its current version.")

    def _populate_token_help(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self.token_help_text.setPlainText(
                "Token help will appear here once document_catalog_service is available."
            )
            return

        try:
            token_help = catalog_service.get_token_help()
        except Exception as exc:
            self.token_help_text.setPlainText(
                f"Could not load token help right now.\n\nDetails: {exc}"
            )
            return

        if not token_help:
            self.token_help_text.setPlainText("No token help is available yet.")
            return

        lines = ["Available Tokens", ""]
        for token_name, description in sorted(token_help.items()):
            lines.append(token_name)
            lines.append(f"  {description}")
            lines.append("")
        self.token_help_text.setPlainText("\n".join(lines).strip())

    def _clear_document_types(self) -> None:
        self.document_type_combo.blockSignals(True)
        self.document_type_combo.clear()
        self.document_type_combo.blockSignals(False)

    def _clear_templates(self, message: str) -> None:
        self.template_list.blockSignals(True)
        self.template_list.clear()
        if message:
            placeholder = QListWidgetItem(message)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.template_list.addItem(placeholder)
        self.template_list.blockSignals(False)

    def _clear_details(self, message: str) -> None:
        self.template_name_value.setText("")
        self.document_type_value.setText("")
        self.template_kind_value.setText("")
        self.content_format_value.setText("")
        self.output_format_value.setText("")
        self.subject_line_value.setText("")
        self.version_number_value.setText("")
        self.active_status_value.setText("")
        self.notes_value.setText("")
        self.change_summary_value.setText("")
        self.body_content_text.setPlainText(message)

    def _friendly_error_message(self, exc: Exception) -> str:
        details = str(exc)
        lowered = details.lower()
        if "database connection is unavailable" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        if "schema is not available" in lowered or "database_schema.sql" in lowered:
            return "Document control schema is missing. Apply database_schema.sql first."
        if "connection" in lowered and "failed" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        return f"Could not load document templates right now. Details: {details}"
