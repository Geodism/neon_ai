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


class PathSettingsPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)
        self._path_rules_by_id: dict[int, object] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("File Paths")
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

        list_group = QGroupBox("Path Rules")
        list_layout = QVBoxLayout(list_group)
        self.path_rule_list = QListWidget()
        self.path_rule_list.currentItemChanged.connect(self._on_path_rule_selected)
        list_layout.addWidget(self.path_rule_list)
        splitter.addWidget(list_group)

        details_group = QGroupBox("Details")
        details_layout = QVBoxLayout(details_group)
        details_form = QFormLayout()
        details_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.rule_name_value = QLabel("")
        self.document_type_value = QLabel("")
        self.local_root_value = QLabel("")
        self.fallback_root_value = QLabel("")
        self.relative_pattern_value = QLabel("")
        self.filename_pattern_value = QLabel("")
        self.storage_bucket_value = QLabel("")
        self.output_format_value = QLabel("")
        self.active_status_value = QLabel("")
        self.notes_value = QLabel("")

        for value_widget in (
            self.rule_name_value,
            self.document_type_value,
            self.local_root_value,
            self.fallback_root_value,
            self.relative_pattern_value,
            self.filename_pattern_value,
            self.storage_bucket_value,
            self.output_format_value,
            self.active_status_value,
            self.notes_value,
        ):
            value_widget.setWordWrap(True)
            value_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        details_form.addRow("Rule Name", self.rule_name_value)
        details_form.addRow("Document Type", self.document_type_value)
        details_form.addRow("Local Root", self.local_root_value)
        details_form.addRow("Fallback Root", self.fallback_root_value)
        details_form.addRow("Relative Pattern", self.relative_pattern_value)
        details_form.addRow("Filename Pattern", self.filename_pattern_value)
        details_form.addRow("Storage Bucket", self.storage_bucket_value)
        details_form.addRow("Output Format", self.output_format_value)
        details_form.addRow("Active", self.active_status_value)
        details_form.addRow("Notes", self.notes_value)
        details_layout.addLayout(details_form)

        preview_heading = QLabel("Resolved Sample Path")
        preview_heading.setStyleSheet("font-weight: 700;")
        details_layout.addWidget(preview_heading)

        self.sample_path_text = QPlainTextEdit()
        self.sample_path_text.setReadOnly(True)
        details_layout.addWidget(self.sample_path_text, 1)
        splitter.addWidget(details_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        if self.container is None:
            self.status_label.setText("Container unavailable. Path rules cannot be loaded yet.")
            self._clear_document_types()
            self._clear_path_rules("Path rules will appear here once the app container is available.")
            self._clear_details("Select a path rule to inspect its settings.")
            return

        catalog_service = getattr(self.container, "document_catalog_service", None)
        path_rule_service = getattr(self.container, "document_path_rule_service", None)
        if catalog_service is None or path_rule_service is None:
            self.status_label.setText("Document control services unavailable. Path rules cannot be loaded yet.")
            self._clear_document_types()
            self._clear_path_rules("Path rules will appear here once document control services are wired.")
            self._clear_details("Select a path rule to inspect its settings.")
            return

        try:
            document_types = catalog_service.list_document_types()
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_document_types()
            self._clear_path_rules("Could not load path rules right now.")
            self._clear_details("Select a path rule to inspect its settings.")
            return

        if not document_types:
            self.status_label.setText("No document types are available yet. Verify schema and seeded data.")
            self._clear_document_types()
            self._clear_path_rules("No path rules are available yet.")
            self._clear_details("Select a path rule to inspect its settings.")
            return

        self.status_label.setText("Document path rule service available.")
        self._populate_document_types(document_types)
        self._refresh_path_rules_for_current_type()

    def _on_document_type_changed(self) -> None:
        if self.document_type_combo.count() == 0:
            return
        self._refresh_path_rules_for_current_type()

    def _refresh_path_rules_for_current_type(self) -> None:
        path_rule_service = getattr(self.container, "document_path_rule_service", None) if self.container else None
        document_type_code = self.document_type_combo.currentData()
        if path_rule_service is None or not document_type_code:
            self._clear_path_rules("No path rules are available yet.")
            self._clear_details("Select a path rule to inspect its settings.")
            return

        try:
            path_rules = path_rule_service.list_path_rules(document_type_code=document_type_code)
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_path_rules("Could not load path rules right now.")
            self._clear_details("Select a path rule to inspect its settings.")
            return

        self._populate_path_rule_list(path_rules)

    def _on_path_rule_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None:
            self._clear_details("Select a path rule to inspect its settings.")
            return

        path_rule_id = current.data(Qt.ItemDataRole.UserRole)
        if path_rule_id is None:
            self._clear_details("Select a path rule to inspect its settings.")
            return

        rule = self._path_rules_by_id.get(int(path_rule_id))
        if rule is None:
            self._clear_details("Could not load path rule details.")
            return

        self._show_path_rule_details(rule)

    def _show_path_rule_details(self, rule) -> None:
        self.rule_name_value.setText(rule.rule_name or "")
        self.document_type_value.setText(rule.document_type_code or "")
        self.local_root_value.setText(rule.local_root or "(none)")
        self.fallback_root_value.setText(rule.fallback_root or "(none)")
        self.relative_pattern_value.setText(rule.relative_pattern or "")
        self.filename_pattern_value.setText(rule.filename_pattern or "")
        self.storage_bucket_value.setText(rule.storage_bucket or "(none)")
        self.output_format_value.setText(str(rule.output_format.value))
        self.active_status_value.setText("Yes" if rule.is_active else "No")
        self.notes_value.setText(rule.notes or "(none)")
        self.sample_path_text.setPlainText(self._build_sample_path_preview(rule))

    def _build_sample_path_preview(self, rule) -> str:
        path_rule_service = getattr(self.container, "document_path_rule_service", None) if self.container else None
        if path_rule_service is None:
            return (
                "Path rule service unavailable.\n\n"
                f"relative_pattern: {rule.relative_pattern}\n"
                f"filename_pattern: {rule.filename_pattern}"
            )

        sample_context = {
            "CustomerName": "ABC Property Management",
            "WorkOrderID": "WO-1042",
            "InvoiceNumber": "INV-2026-0031",
            "InvoiceDate": "2026-04-30",
            "TotalAmount": "$4,812.50",
        }
        try:
            resolved_path = path_rule_service.resolve_rule_path(
                rule=rule,
                context=sample_context,
                create_folders=False,
            )
            return str(resolved_path).replace("\\", "/")
        except Exception as exc:
            return (
                "Could not resolve a sample path right now.\n\n"
                f"relative_pattern: {rule.relative_pattern}\n"
                f"filename_pattern: {rule.filename_pattern}\n\n"
                f"Details: {exc}"
            )

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

    def _populate_path_rule_list(self, path_rules: list[object]) -> None:
        self.path_rule_list.blockSignals(True)
        self.path_rule_list.clear()
        self._path_rules_by_id = {}

        if not path_rules:
            self._clear_path_rules("No path rules were found for the selected document type.")
            self.path_rule_list.blockSignals(False)
            return

        first_item = None
        for rule in path_rules:
            label = rule.rule_name
            if rule.is_active:
                label = f"{label} [active]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, rule.path_rule_id)
            self.path_rule_list.addItem(item)
            self._path_rules_by_id[rule.path_rule_id] = rule
            if first_item is None:
                first_item = item

        self.path_rule_list.blockSignals(False)
        if first_item is not None:
            self.path_rule_list.setCurrentItem(first_item)
        else:
            self._clear_details("Select a path rule to inspect its settings.")

    def _clear_document_types(self) -> None:
        self.document_type_combo.blockSignals(True)
        self.document_type_combo.clear()
        self.document_type_combo.blockSignals(False)

    def _clear_path_rules(self, message: str) -> None:
        self.path_rule_list.blockSignals(True)
        self.path_rule_list.clear()
        if message:
            placeholder = QListWidgetItem(message)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.path_rule_list.addItem(placeholder)
        self.path_rule_list.blockSignals(False)

    def _clear_details(self, message: str) -> None:
        self.rule_name_value.setText("")
        self.document_type_value.setText("")
        self.local_root_value.setText("")
        self.fallback_root_value.setText("")
        self.relative_pattern_value.setText("")
        self.filename_pattern_value.setText("")
        self.storage_bucket_value.setText("")
        self.output_format_value.setText("")
        self.active_status_value.setText("")
        self.notes_value.setText("")
        self.sample_path_text.setPlainText(message)

    def _friendly_error_message(self, exc: Exception) -> str:
        details = str(exc)
        lowered = details.lower()
        if "database connection is unavailable" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        if "schema is not available" in lowered or "database_schema.sql" in lowered:
            return "Document control schema is missing. Apply database_schema.sql first."
        if "connection" in lowered and "failed" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        return f"Could not load path rules right now. Details: {details}"
