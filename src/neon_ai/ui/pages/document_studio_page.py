from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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

from neon_ai.document_control.models import (
    DocumentOutputFormat,
    DocumentTemplateKind,
    DocumentTemplateVersion,
)


class DocumentStudioPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)
        self._template_summaries_by_id: dict[int, object] = {}
        self._current_template_id: int | None = None
        self._current_loaded_version_id: int | None = None
        self._current_template_is_active: bool = False
        self._suspend_dirty_tracking = False
        self._is_dirty = False

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

        action_row = QHBoxLayout()
        self.new_template_button = QPushButton("New Template")
        self.new_template_button.clicked.connect(self._on_new_template)
        action_row.addWidget(self.new_template_button)
        self.save_version_button = QPushButton("Save Version")
        self.save_version_button.clicked.connect(self._on_save_version)
        action_row.addWidget(self.save_version_button)
        self.activate_button = QPushButton("Activate Selected Template")
        self.activate_button.clicked.connect(self._on_activate_selected)
        action_row.addWidget(self.activate_button)
        self.reload_button = QPushButton("Revert/Reload Selected")
        self.reload_button.clicked.connect(self._on_reload_selected)
        action_row.addWidget(self.reload_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        list_group = QGroupBox("Templates")
        list_layout = QVBoxLayout(list_group)
        self.template_list = QListWidget()
        self.template_list.currentItemChanged.connect(self._on_template_selected)
        list_layout.addWidget(self.template_list)
        splitter.addWidget(list_group)

        details_group = QGroupBox("Template Details")
        details_layout = QVBoxLayout(details_group)
        details_form = QFormLayout()
        details_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.template_name_edit = QLineEdit()
        self.template_kind_combo = QComboBox()
        self.template_kind_combo.addItem("BODY", DocumentTemplateKind.BODY)
        self.template_kind_combo.addItem("HEADER", DocumentTemplateKind.HEADER)
        self.template_kind_combo.addItem("FOOTER", DocumentTemplateKind.FOOTER)

        self.content_format_combo = QComboBox()
        self.content_format_combo.addItem("html", "html")
        self.content_format_combo.addItem("text", "text")

        self.output_format_combo = QComboBox()
        self.output_format_combo.addItem("HTML", DocumentOutputFormat.HTML)
        self.output_format_combo.addItem("TEXT", DocumentOutputFormat.TEXT)
        self.output_format_combo.addItem("DOCX", DocumentOutputFormat.DOCX)
        self.output_format_combo.addItem("PDF", DocumentOutputFormat.PDF)

        self.subject_line_edit = QLineEdit()

        self.version_number_value = QLabel("")
        self.version_number_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.active_status_value = QLabel("")
        self.active_status_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setFixedHeight(70)
        self.change_summary_edit = QPlainTextEdit()
        self.change_summary_edit.setFixedHeight(70)
        self.body_content_edit = QPlainTextEdit()

        details_form.addRow("Template Name", self.template_name_edit)
        details_form.addRow("Template Kind", self.template_kind_combo)
        details_form.addRow("Content Format", self.content_format_combo)
        details_form.addRow("Output Format", self.output_format_combo)
        details_form.addRow("Subject Line", self.subject_line_edit)
        details_form.addRow("Version Number", self.version_number_value)
        details_form.addRow("Active Template", self.active_status_value)
        details_form.addRow("Notes", self.notes_edit)
        details_form.addRow("Change Summary", self.change_summary_edit)
        details_layout.addLayout(details_form)

        body_heading = QLabel("Body Content")
        body_heading.setStyleSheet("font-weight: 700;")
        details_layout.addWidget(body_heading)
        details_layout.addWidget(self.body_content_edit, 1)
        splitter.addWidget(details_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        token_group = QGroupBox("Token Help")
        token_layout = QVBoxLayout(token_group)
        self.token_help_text = QPlainTextEdit()
        self.token_help_text.setReadOnly(True)
        token_layout.addWidget(self.token_help_text)
        layout.addWidget(token_group)

        self._connect_dirty_tracking()

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        self._populate_token_help()
        if self.container is None:
            self.status_label.setText("Container unavailable. Templates cannot be loaded yet.")
            self._clear_document_types()
            self._clear_templates("Templates will appear here once the app container is available.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        catalog_service = getattr(self.container, "document_catalog_service", None)
        if catalog_service is None:
            self.status_label.setText("Document catalog service unavailable. Templates cannot be loaded yet.")
            self._clear_document_types()
            self._clear_templates("Templates will appear here once document_catalog_service is wired.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        try:
            document_types = catalog_service.list_document_types()
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_document_types()
            self._clear_templates("Could not load templates right now.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        if not document_types:
            self.status_label.setText("No document types are available yet. Verify schema and seeded data.")
            self._clear_document_types()
            self._clear_templates("No templates are available yet.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
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
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        try:
            template_summaries = catalog_service.list_templates(document_type_code=document_type_code)
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_templates("Could not load templates right now.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        self._populate_template_list(template_summaries)

    def _on_template_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None:
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        template_id = current.data(Qt.ItemDataRole.UserRole)
        if template_id is None:
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        self._load_template_version(int(template_id))

    def _load_template_version(self, template_id: int) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self._reset_editor_for_empty_state("Document catalog service unavailable.")
            return

        try:
            version = catalog_service.get_template_version(template_id=template_id)
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._reset_editor_for_empty_state("Could not load template details right now.")
            return

        summary = self._template_summaries_by_id.get(template_id)
        if version is None or summary is None:
            self._reset_editor_for_empty_state("No current template version is available for this template.")
            return

        self._populate_editor_from_version(version, summary)

    def _populate_editor_from_version(self, version: DocumentTemplateVersion, summary: object | None) -> None:
        self._suspend_dirty_tracking = True
        try:
            self._current_template_id = version.template_id
            self._current_loaded_version_id = version.template_version_id
            self._current_template_is_active = bool(summary.is_active) if summary is not None else bool(version.is_active)

            self.template_name_edit.setText(version.template_name)
            self._set_combo_value(self.template_kind_combo, version.kind)
            self._set_combo_value(self.content_format_combo, version.content_format)
            self._set_combo_value(self.output_format_combo, version.output_format)
            self.subject_line_edit.setText(version.subject_line or "")
            self.version_number_value.setText(str(version.version_number))
            self.active_status_value.setText("Yes" if self._current_template_is_active else "No")
            self.notes_edit.setPlainText(version.notes or (summary.notes if summary is not None and summary.notes else ""))
            self.change_summary_edit.setPlainText(version.change_summary or "")
            self.body_content_edit.setPlainText(version.body_content or "")
            self._is_dirty = False
        finally:
            self._suspend_dirty_tracking = False

    def _on_new_template(self) -> None:
        if not self._confirm_discard_changes_if_needed("Discard current edits and start a new template?"):
            return

        document_type_code = self.document_type_combo.currentData()
        self._suspend_dirty_tracking = True
        try:
            self._current_template_id = None
            self._current_loaded_version_id = None
            self._current_template_is_active = False
            self.template_list.clearSelection()
            self.template_name_edit.setText("")
            self._set_combo_value(self.template_kind_combo, DocumentTemplateKind.BODY)
            self._set_combo_value(self.content_format_combo, "html")
            self._set_combo_value(self.output_format_combo, DocumentOutputFormat.HTML)
            self.subject_line_edit.setText("")
            self.version_number_value.setText("1")
            self.active_status_value.setText("No")
            self.notes_edit.setPlainText("")
            self.change_summary_edit.setPlainText("Initial version")
            self.body_content_edit.setPlainText("")
            self._is_dirty = False
        finally:
            self._suspend_dirty_tracking = False

        if document_type_code:
            self.status_label.setText(
                f"Creating a new template for {document_type_code}. Fill in the fields and choose Save Version."
            )
        else:
            self.status_label.setText("Creating a new template. Fill in the fields and choose Save Version.")

    def _on_save_version(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self.status_label.setText("Document catalog service unavailable. Template could not be saved.")
            return

        document_type_code = self.document_type_combo.currentData()
        template_name = self.template_name_edit.text().strip()
        body_content = self.body_content_edit.toPlainText().strip()
        if not document_type_code:
            self.status_label.setText("Choose a document type before saving.")
            return
        if not template_name:
            self.status_label.setText("Template name cannot be blank.")
            return
        if not body_content:
            self.status_label.setText("Body content cannot be blank.")
            return

        template_kind = self._current_template_kind()
        output_format = self._current_output_format()
        version = DocumentTemplateVersion(
            template_version_id=None,
            template_id=self._current_template_id,
            document_type_code=str(document_type_code),
            kind=template_kind,
            template_name=template_name,
            version_number=1,
            subject_line=self.subject_line_edit.text().strip() or None,
            body_content=self.body_content_edit.toPlainText(),
            content_format=str(self.content_format_combo.currentData() or "html"),
            output_format=output_format,
            token_schema=tuple(),
            change_summary=self.change_summary_edit.toPlainText().strip() or None,
            notes=self.notes_edit.toPlainText().strip() or None,
            is_active=False,
            created_by="ui",
        )

        try:
            saved_version = catalog_service.save_template(version)
        except Exception as exc:
            self.status_label.setText(self._friendly_save_error_message(exc))
            return

        saved_template_id = saved_version.template_id
        self.status_label.setText(
            f"Saved version {saved_version.version_number} for '{saved_version.template_name}'. "
            "Active template status was left unchanged."
        )
        self._refresh_templates_for_current_type()
        if saved_template_id is not None:
            self._reselect_template(saved_template_id)
            self._populate_editor_from_version(saved_version, self._template_summaries_by_id.get(saved_template_id))
        else:
            self._populate_editor_from_version(saved_version, None)
        self._is_dirty = False

    def _on_reload_selected(self) -> None:
        if not self._confirm_discard_changes_if_needed("Discard unsaved edits and reload the selected template?"):
            return

        current_item = self.template_list.currentItem()
        template_id = current_item.data(Qt.ItemDataRole.UserRole) if current_item is not None else None
        if template_id is None:
            self._on_new_template()
            return

        self._load_template_version(int(template_id))
        self.status_label.setText("Reloaded the selected template from the database.")

    def _on_activate_selected(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self.status_label.setText("Document catalog service unavailable. Template could not be activated.")
            return

        template_id = self._current_template_id
        template_name = self.template_name_edit.text().strip() or "selected template"
        if template_id is None:
            self.status_label.setText("Select a saved template before activating it.")
            return

        prompt = (
            f"Activate '{template_name}' for the selected document type and template kind?\n\n"
            "This will deactivate any other active template in the same document type and kind."
        )
        if self._is_dirty:
            prompt += "\n\nUnsaved edits are not included. Save a new version first if you want those changes activated."

        choice = QMessageBox.question(self, "Activate Template?", prompt)
        if choice != QMessageBox.StandardButton.Yes:
            self.status_label.setText("Template activation cancelled.")
            return

        try:
            catalog_service.activate_template(template_id)
        except Exception as exc:
            self.status_label.setText(self._friendly_activation_error_message(exc))
            return

        self._refresh_templates_for_current_type()
        self._reselect_template(template_id)
        self.status_label.setText(
            f"Activated '{template_name}' for the selected document type and template kind."
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

    def _populate_template_list(self, template_summaries: list[object]) -> None:
        self.template_list.blockSignals(True)
        self.template_list.clear()
        self._template_summaries_by_id = {}

        if not template_summaries:
            self._clear_templates("No templates were found for the selected document type.")
            self.template_list.blockSignals(False)
            self._on_new_template()
            return

        first_item = None
        for summary in template_summaries:
            label = f"{summary.template_name} [{summary.kind.value}]"
            if summary.is_active:
                label = f"{label} [active]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, summary.template_id)
            self.template_list.addItem(item)
            self._template_summaries_by_id[summary.template_id] = summary
            if first_item is None:
                first_item = item

        self.template_list.blockSignals(False)
        if first_item is not None:
            self.template_list.setCurrentItem(first_item)
        else:
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")

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

    def _reset_editor_for_empty_state(self, body_message: str) -> None:
        self._suspend_dirty_tracking = True
        try:
            self._current_template_id = None
            self._current_loaded_version_id = None
            self._current_template_is_active = False
            self.template_name_edit.setText("")
            self._set_combo_value(self.template_kind_combo, DocumentTemplateKind.BODY)
            self._set_combo_value(self.content_format_combo, "html")
            self._set_combo_value(self.output_format_combo, DocumentOutputFormat.HTML)
            self.subject_line_edit.setText("")
            self.version_number_value.setText("")
            self.active_status_value.setText("")
            self.notes_edit.setPlainText("")
            self.change_summary_edit.setPlainText("")
            self.body_content_edit.setPlainText(body_message)
            self._is_dirty = False
        finally:
            self._suspend_dirty_tracking = False

    def _connect_dirty_tracking(self) -> None:
        self.template_name_edit.textChanged.connect(self._mark_dirty)
        self.template_kind_combo.currentIndexChanged.connect(self._mark_dirty)
        self.content_format_combo.currentIndexChanged.connect(self._mark_dirty)
        self.output_format_combo.currentIndexChanged.connect(self._mark_dirty)
        self.subject_line_edit.textChanged.connect(self._mark_dirty)
        self.notes_edit.textChanged.connect(self._mark_dirty)
        self.change_summary_edit.textChanged.connect(self._mark_dirty)
        self.body_content_edit.textChanged.connect(self._mark_dirty)

    def _mark_dirty(self) -> None:
        if self._suspend_dirty_tracking:
            return
        self._is_dirty = True

    def _confirm_discard_changes_if_needed(self, prompt: str) -> bool:
        if not self._is_dirty:
            return True
        choice = QMessageBox.question(self, "Discard Edits?", prompt)
        return choice == QMessageBox.StandardButton.Yes

    def _set_combo_value(self, combo: QComboBox, value) -> None:
        for index in range(combo.count()):
            combo_value = combo.itemData(index)
            if combo_value == value:
                combo.setCurrentIndex(index)
                return
            if hasattr(combo_value, "value") and combo_value.value == value:
                combo.setCurrentIndex(index)
                return
            if hasattr(value, "value") and combo_value == value.value:
                combo.setCurrentIndex(index)
                return
            if isinstance(value, str) and str(combo_value) == value:
                combo.setCurrentIndex(index)
                return

    def _current_template_kind(self) -> DocumentTemplateKind:
        raw_value = self.template_kind_combo.currentData() or DocumentTemplateKind.BODY
        if isinstance(raw_value, DocumentTemplateKind):
            return raw_value
        return DocumentTemplateKind(str(raw_value))

    def _current_output_format(self) -> DocumentOutputFormat:
        raw_value = self.output_format_combo.currentData() or DocumentOutputFormat.HTML
        if isinstance(raw_value, DocumentOutputFormat):
            return raw_value
        return DocumentOutputFormat(str(raw_value))

    def _reselect_template(self, template_id: int) -> None:
        for index in range(self.template_list.count()):
            item = self.template_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == template_id:
                self.template_list.setCurrentItem(item)
                return

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

    def _friendly_save_error_message(self, exc: Exception) -> str:
        details = str(exc)
        lowered = details.lower()
        if "duplicate key value" in lowered or "unique" in lowered:
            return "A template with this name and kind already exists for the selected document type."
        if "database connection is unavailable" in lowered:
            return "Database connection unavailable. Template could not be saved."
        if "schema is not available" in lowered or "database_schema.sql" in lowered:
            return "Document control schema is missing. Apply database_schema.sql first."
        return f"Template could not be saved. Details: {details}"

    def _friendly_activation_error_message(self, exc: Exception) -> str:
        details = str(exc)
        lowered = details.lower()
        if "does not have a saved version" in lowered:
            return "This template must have at least one saved version before it can be activated."
        if "database connection is unavailable" in lowered:
            return "Database connection unavailable. Template could not be activated."
        if "schema is not available" in lowered or "database_schema.sql" in lowered:
            return "Document control schema is missing. Apply database_schema.sql first."
        return f"Template could not be activated. Details: {details}"
