from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from neon_ai.document_control.models import DocumentOutputFormat, DocumentPathRule
from neon_ai.document_control.seed import seed_document_control_defaults
from neon_ai.document_control.token_engine import extract_tokens


_SEEDED_DEFAULT_RULES: dict[str, str] = {
    "CUSTOMER_INVOICE": "Default Local Generated Documents",
    "ESTIMATE_DOCUMENT": "Default Local Estimate Documents",
}


class PathSettingsPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)
        self._path_rules_by_id: dict[int, DocumentPathRule] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("File Path Rules")
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Choose where generated documents and artifacts are saved. "
            "Generated history remains available separately for audit/debug review."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

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

        editor_group = QGroupBox("Rule Editor")
        editor_layout = QVBoxLayout(editor_group)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.rule_name_edit = QLineEdit()
        self.document_type_value = QLabel("Select a document type")
        self.document_type_value.setWordWrap(True)
        self.local_root_edit = QLineEdit()
        self.fallback_root_edit = QLineEdit()
        self.relative_pattern_edit = QLineEdit()
        self.filename_pattern_edit = QLineEdit()
        self.output_format_combo = QComboBox()
        for output_format in DocumentOutputFormat:
            self.output_format_combo.addItem(output_format.value, output_format)
        self.active_rule_checkbox = QCheckBox("Use this as the active rule for the selected document type")
        self.create_folder_if_missing_checkbox = QCheckBox("Create folder if missing")
        self.create_folder_if_missing_checkbox.setToolTip(
            "When enabled, document generation may create the destination folder automatically."
        )
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setFixedHeight(90)

        form.addRow("Rule Name", self.rule_name_edit)
        form.addRow("Document Type", self.document_type_value)
        form.addRow("Local Root Folder", self.local_root_edit)
        form.addRow("Fallback Folder", self.fallback_root_edit)
        form.addRow("Relative Folder Pattern", self.relative_pattern_edit)
        form.addRow("Filename Pattern", self.filename_pattern_edit)
        form.addRow("Output Format", self.output_format_combo)
        form.addRow("Active Rule", self.active_rule_checkbox)
        form.addRow("Create Folder If Missing", self.create_folder_if_missing_checkbox)
        form.addRow("Notes", self.notes_edit)
        editor_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.preview_button = QPushButton("Preview Resolved Path")
        self.preview_button.clicked.connect(self._preview_current_rule)
        button_row.addWidget(self.preview_button)

        self.save_button = QPushButton("Save Rule")
        self.save_button.clicked.connect(self._save_current_rule)
        button_row.addWidget(self.save_button)

        self.restore_default_button = QPushButton("Restore Default Path Rule")
        self.restore_default_button.clicked.connect(self._restore_default_path_rule)
        button_row.addWidget(self.restore_default_button)
        button_row.addStretch(1)
        editor_layout.addLayout(button_row)

        preview_heading = QLabel("Resolved Sample Path")
        preview_heading.setStyleSheet("font-weight: 700;")
        editor_layout.addWidget(preview_heading)

        self.sample_path_text = QPlainTextEdit()
        self.sample_path_text.setReadOnly(True)
        editor_layout.addWidget(self.sample_path_text, 1)
        splitter.addWidget(editor_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self._set_editor_enabled(False)

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        if self.container is None:
            self.status_label.setText("Container unavailable. Path rules cannot be loaded yet.")
            self._clear_document_types()
            self._clear_path_rules("Path rules will appear here once the app container is available.")
            self._clear_details("Select a path rule to edit its settings.")
            return

        catalog_service = getattr(self.container, "document_catalog_service", None)
        path_rule_service = getattr(self.container, "document_path_rule_service", None)
        repository = getattr(self.container, "document_repository", None)
        if catalog_service is None or path_rule_service is None or repository is None:
            self.status_label.setText("Document control services unavailable. Path rules cannot be loaded yet.")
            self._clear_document_types()
            self._clear_path_rules("Path rules will appear here once document control services are wired.")
            self._clear_details("Select a path rule to edit its settings.")
            return

        try:
            document_types = catalog_service.list_document_types()
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_document_types()
            self._clear_path_rules("Could not load path rules right now.")
            self._clear_details("Select a path rule to edit its settings.")
            return

        if not document_types:
            self.status_label.setText("No document types are available yet. Verify schema and seeded data.")
            self._clear_document_types()
            self._clear_path_rules("No path rules are available yet.")
            self._clear_details("Select a path rule to edit its settings.")
            return

        self.status_label.setText(
            "File path rules are editable here. Generated history remains available from the debug view."
        )
        self._populate_document_types(document_types)
        self._refresh_path_rules_for_current_type()

    def _on_document_type_changed(self) -> None:
        self.document_type_value.setText(self.document_type_combo.currentData() or "Select a document type")
        if self.document_type_combo.count() == 0:
            return
        self._refresh_path_rules_for_current_type()

    def _refresh_path_rules_for_current_type(self) -> None:
        path_rule_service = getattr(self.container, "document_path_rule_service", None) if self.container else None
        document_type_code = self.document_type_combo.currentData()
        self.document_type_value.setText(document_type_code or "Select a document type")
        if path_rule_service is None or not document_type_code:
            self._clear_path_rules("No path rules are available yet.")
            self._clear_details("Select a path rule to edit its settings.")
            return

        try:
            path_rules = path_rule_service.list_path_rules(document_type_code=document_type_code)
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_path_rules("Could not load path rules right now.")
            self._clear_details("Select a path rule to edit its settings.")
            return

        self._populate_path_rule_list(path_rules)

    def _on_path_rule_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None:
            self._clear_details("Select a path rule to edit its settings.")
            return

        path_rule_id = current.data(Qt.ItemDataRole.UserRole)
        if path_rule_id is None:
            self._clear_details("Select a path rule to edit its settings.")
            return

        rule = self._path_rules_by_id.get(int(path_rule_id))
        if rule is None:
            self._clear_details("Could not load path rule details.")
            return

        self._show_path_rule_details(rule)

    def _show_path_rule_details(self, rule: DocumentPathRule) -> None:
        self.rule_name_edit.setText(rule.rule_name or "")
        self.document_type_value.setText(rule.document_type_code or "")
        self.local_root_edit.setText(rule.local_root or "")
        self.fallback_root_edit.setText(rule.fallback_root or "")
        self.relative_pattern_edit.setText(rule.relative_pattern or "")
        self.filename_pattern_edit.setText(rule.filename_pattern or "")
        self.notes_edit.setPlainText(rule.notes or "")
        output_index = self.output_format_combo.findData(rule.output_format)
        self.output_format_combo.setCurrentIndex(max(output_index, 0))
        self.active_rule_checkbox.setChecked(bool(rule.is_active))
        self.create_folder_if_missing_checkbox.setChecked(bool(rule.create_folder_if_missing))
        self.sample_path_text.setPlainText(self._build_sample_path_preview(rule))
        self._set_editor_enabled(True)

    def _build_sample_path_preview(self, rule: DocumentPathRule) -> str:
        path_rule_service = getattr(self.container, "document_path_rule_service", None) if self.container else None
        document_type_code = rule.document_type_code or self.document_type_combo.currentData() or ""
        sample_context = self._build_sample_context(document_type_code)
        required_tokens = extract_tokens(rule.relative_pattern or "") | extract_tokens(rule.filename_pattern or "")
        missing_tokens = sorted(token for token in required_tokens if token not in sample_context)

        header_lines: list[str] = []
        if missing_tokens:
            header_lines.append("Missing sample tokens:")
            header_lines.extend(f"- {token}" for token in missing_tokens)
            header_lines.append("")

        if path_rule_service is None:
            header_lines.append("Path rule service unavailable.")
            header_lines.append("")
            header_lines.append(f"relative_pattern: {rule.relative_pattern}")
            header_lines.append(f"filename_pattern: {rule.filename_pattern}")
            return "\n".join(header_lines)

        try:
            resolved_path = path_rule_service.resolve_rule_path(
                rule=rule,
                context=sample_context,
                create_folders=False,
            )
            header_lines.append(f"Create folder if missing: {'Yes' if rule.create_folder_if_missing else 'No'}")
            header_lines.append(f"Resolved path: {str(resolved_path).replace('\\', '/')}")
            if not rule.create_folder_if_missing and not resolved_path.parent.exists():
                header_lines.append("")
                header_lines.append(
                    "Note: the resolved target folder does not exist and generation will fail until it is created."
                )
            return "\n".join(header_lines)
        except Exception as exc:
            header_lines.append(f"Create folder if missing: {'Yes' if rule.create_folder_if_missing else 'No'}")
            header_lines.append("Could not resolve a sample path right now.")
            header_lines.append("")
            header_lines.append(f"relative_pattern: {rule.relative_pattern}")
            header_lines.append(f"filename_pattern: {rule.filename_pattern}")
            header_lines.append("")
            header_lines.append(f"Details: {exc}")
            return "\n".join(header_lines)

    def _build_sample_context(self, document_type_code: str) -> dict[str, object]:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        context = (
            catalog_service.build_sample_context(document_type_code)
            if catalog_service is not None
            else {"DocumentTypeCode": document_type_code}
        )
        context.update(
            {
                "EstimateID": "EST-2048",
                "EstimateNumber": "EST-2048",
                "PurchaseOrderID": "PO-108",
                "VendorName": "Northline Supply",
                "VendorInvoiceID": "VIN-331",
                "RFQID": "RFQ-77",
                "LeadID": "LEAD-22",
                "SiteName": "Lakeside Tower",
                "SiteAddress": "2500 Example Ave, Vancouver",
                "AttachmentName": "packing_slip_2048",
                "ReportDate": "2026-04-30",
                "ArtifactType": document_type_code,
            }
        )
        return context

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
        self.document_type_value.setText(self.document_type_combo.currentData() or "Select a document type")

    def _populate_path_rule_list(self, path_rules: list[DocumentPathRule]) -> None:
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
            self._path_rules_by_id[int(rule.path_rule_id)] = rule
            if first_item is None:
                first_item = item

        self.path_rule_list.blockSignals(False)
        if first_item is not None:
            self.path_rule_list.setCurrentItem(first_item)
        else:
            self._clear_details("Select a path rule to edit its settings.")

    def _preview_current_rule(self) -> None:
        rule = self._build_rule_from_form()
        if rule is None:
            QMessageBox.warning(self, "No Rule Selected", "Select a path rule first.")
            return
        self.sample_path_text.setPlainText(self._build_sample_path_preview(rule))

    def _save_current_rule(self) -> None:
        repository = getattr(self.container, "document_repository", None) if self.container else None
        current_rule = self._selected_rule()
        if repository is None or current_rule is None:
            QMessageBox.warning(self, "No Rule Selected", "Select a path rule first.")
            return

        updated_rule = self._build_rule_from_form(existing_rule=current_rule)
        if updated_rule is None:
            QMessageBox.warning(self, "Invalid Rule", "A valid path rule could not be built from the current form.")
            return

        if not updated_rule.rule_name.strip():
            QMessageBox.warning(self, "Missing Rule Name", "Enter a rule name before saving.")
            return
        if not updated_rule.relative_pattern.strip():
            QMessageBox.warning(self, "Missing Relative Pattern", "Enter a relative folder pattern before saving.")
            return
        if not updated_rule.filename_pattern.strip():
            QMessageBox.warning(self, "Missing Filename Pattern", "Enter a filename pattern before saving.")
            return

        if current_rule.is_active and not updated_rule.is_active and not self._has_other_active_rule(current_rule):
            QMessageBox.warning(
                self,
                "Active Rule Required",
                "This document type would be left without an active path rule.\n\n"
                "Keep this rule active or set another rule active first.",
            )
            return

        try:
            saved = repository.save_path_rule(updated_rule)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", self._friendly_error_message(exc))
            return

        self.refresh()
        self._select_rule_by_id(int(saved.path_rule_id))
        QMessageBox.information(
            self,
            "Path Rule Saved",
            f"Saved path rule '{saved.rule_name}' for {saved.document_type_code}.",
        )

    def _restore_default_path_rule(self) -> None:
        document_type_code = self.document_type_combo.currentData()
        if not document_type_code:
            QMessageBox.warning(self, "No Document Type", "Select a document type first.")
            return

        default_rule_name = _SEEDED_DEFAULT_RULES.get(str(document_type_code))
        if not default_rule_name:
            QMessageBox.information(
                self,
                "Default Rule Not Available",
                "Restore Default Path Rule is currently available only for seeded document types.\n\n"
                "No seeded default path rule is defined yet for this document type.",
            )
            return

        confirm = QMessageBox.question(
            self,
            "Restore Default Path Rule",
            "This will re-apply the seeded default path rule for the selected document type.\n\n"
            "Existing generated history records will not be deleted. Continue?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            seed_document_control_defaults()
        except Exception as exc:
            QMessageBox.critical(self, "Restore Failed", self._friendly_error_message(exc))
            return

        self.refresh()
        self._select_rule_by_name(default_rule_name)
        QMessageBox.information(
            self,
            "Default Restored",
            f"Restored the seeded default path rule '{default_rule_name}' for {document_type_code}.",
        )

    def _build_rule_from_form(self, existing_rule: DocumentPathRule | None = None) -> DocumentPathRule | None:
        document_type_code = (
            (existing_rule.document_type_code if existing_rule is not None else None)
            or self.document_type_combo.currentData()
        )
        if not document_type_code:
            return None

        output_format = self.output_format_combo.currentData()
        if not isinstance(output_format, DocumentOutputFormat):
            output_format = DocumentOutputFormat.HTML

        return DocumentPathRule(
            path_rule_id=(existing_rule.path_rule_id if existing_rule is not None else None),
            document_type_code=str(document_type_code),
            local_root=self.local_root_edit.text().strip() or None,
            fallback_root=self.fallback_root_edit.text().strip() or None,
            relative_pattern=self.relative_pattern_edit.text().strip(),
            filename_pattern=self.filename_pattern_edit.text().strip(),
            storage_bucket=(existing_rule.storage_bucket if existing_rule is not None else ""),
            output_format=output_format,
            rule_name=self.rule_name_edit.text().strip() or "Unnamed Rule",
            is_active=self.active_rule_checkbox.isChecked(),
            create_folder_if_missing=self.create_folder_if_missing_checkbox.isChecked(),
            notes=self.notes_edit.toPlainText().strip() or None,
            created_at=(existing_rule.created_at if existing_rule is not None else None),
            created_by=(existing_rule.created_by if existing_rule is not None else "UI"),
        )

    def _selected_rule(self) -> DocumentPathRule | None:
        item = self.path_rule_list.currentItem()
        if item is None:
            return None
        rule_id = item.data(Qt.ItemDataRole.UserRole)
        if rule_id is None:
            return None
        return self._path_rules_by_id.get(int(rule_id))

    def _has_other_active_rule(self, current_rule: DocumentPathRule) -> bool:
        for rule in self._path_rules_by_id.values():
            if int(rule.path_rule_id) == int(current_rule.path_rule_id):
                continue
            if rule.is_active:
                return True
        return False

    def _select_rule_by_id(self, path_rule_id: int) -> None:
        for index in range(self.path_rule_list.count()):
            item = self.path_rule_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == path_rule_id:
                self.path_rule_list.setCurrentItem(item)
                return

    def _select_rule_by_name(self, rule_name: str) -> None:
        for index in range(self.path_rule_list.count()):
            item = self.path_rule_list.item(index)
            if item.text().replace(" [active]", "") == rule_name:
                self.path_rule_list.setCurrentItem(item)
                return

    def _clear_document_types(self) -> None:
        self.document_type_combo.blockSignals(True)
        self.document_type_combo.clear()
        self.document_type_combo.blockSignals(False)

    def _clear_path_rules(self, message: str) -> None:
        self.path_rule_list.blockSignals(True)
        self.path_rule_list.clear()
        self._path_rules_by_id = {}
        if message:
            placeholder = QListWidgetItem(message)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.path_rule_list.addItem(placeholder)
        self.path_rule_list.blockSignals(False)

    def _clear_details(self, message: str) -> None:
        self.rule_name_edit.clear()
        self.document_type_value.setText(self.document_type_combo.currentData() or "Select a document type")
        self.local_root_edit.clear()
        self.fallback_root_edit.clear()
        self.relative_pattern_edit.clear()
        self.filename_pattern_edit.clear()
        self.output_format_combo.setCurrentIndex(0)
        self.active_rule_checkbox.setChecked(False)
        self.create_folder_if_missing_checkbox.setChecked(True)
        self.notes_edit.clear()
        self.sample_path_text.setPlainText(message)
        self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.rule_name_edit,
            self.local_root_edit,
            self.fallback_root_edit,
            self.relative_pattern_edit,
            self.filename_pattern_edit,
            self.output_format_combo,
            self.active_rule_checkbox,
            self.notes_edit,
            self.preview_button,
            self.save_button,
            self.restore_default_button,
            self.create_folder_if_missing_checkbox,
        ):
            widget.setEnabled(enabled)

    def _friendly_error_message(self, exc: Exception) -> str:
        details = str(exc)
        lowered = details.lower()
        if "database connection is unavailable" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        if "schema is not available" in lowered or "database_schema.sql" in lowered:
            return "Document control schema is missing. Apply database_schema.sql first."
        if "connection" in lowered and "failed" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        return f"Could not complete the path-rule action. Details: {details}"
