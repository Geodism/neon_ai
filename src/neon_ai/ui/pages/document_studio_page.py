from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from neon_ai.document_control.models import (
    DocumentOutputFormat,
    DocumentTemplateKind,
    DocumentTemplateVersion,
)
from neon_ai.ui.widgets.rich_text_toolbar import RichTextToolbar


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
        self._token_help_map: dict[str, str] = {}
        self._estimate_default_usage_context = "ESTIMATE_DRAFT_WORKSPACE"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Template Assembly")
        heading.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Manage headers, bodies, signatures, footers, and active versions for every "
            "company document in one shared library."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #7a1f1f;")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(main_splitter, 1)

        left_pane = QWidget()
        left_pane.setMinimumWidth(380)
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        doc_type_label = QLabel("Document Type")
        doc_type_label.setStyleSheet("font-weight: 600;")
        left_layout.addWidget(doc_type_label)

        self.document_type_combo = QComboBox()
        self.document_type_combo.setMinimumWidth(360)
        self.document_type_combo.currentIndexChanged.connect(self._on_document_type_changed)
        self.document_type_filter = self.document_type_combo
        left_layout.addWidget(self.document_type_combo)

        self.template_list = QListWidget()
        self.template_list.currentItemChanged.connect(self._on_template_selected)
        left_layout.addWidget(self.template_list, 1)

        left_buttons = QHBoxLayout()
        self.new_template_button = QPushButton("New Template")
        self.new_template_button.clicked.connect(self._on_new_template)
        left_buttons.addWidget(self.new_template_button)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        left_buttons.addWidget(self.refresh_button)
        left_layout.addLayout(left_buttons)

        right_pane = QWidget()
        right_layout = QVBoxLayout(right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        right_layout.addWidget(workspace_splitter, 1)

        editor_column = QWidget()
        editor_layout = QVBoxLayout(editor_column)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(10)

        metadata_group = QGroupBox("Template Metadata")
        metadata_grid = QGridLayout(metadata_group)
        metadata_grid.setHorizontalSpacing(12)
        metadata_grid.setVerticalSpacing(6)

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

        self.external_asset_edit = QLineEdit()
        self.external_asset_edit.setPlaceholderText("Optional future asset path")
        external_asset_row = QWidget()
        external_asset_layout = QHBoxLayout(external_asset_row)
        external_asset_layout.setContentsMargins(0, 0, 0, 0)
        external_asset_layout.setSpacing(6)
        external_asset_layout.addWidget(self.external_asset_edit, 1)
        self.browse_asset_button = QPushButton("Browse")
        self.browse_asset_button.clicked.connect(self._on_browse_external_asset)
        external_asset_layout.addWidget(self.browse_asset_button)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setFixedHeight(64)
        self.change_summary_edit = QPlainTextEdit()
        self.change_summary_edit.setFixedHeight(64)

        self.version_number_value = QLabel("")
        self.version_number_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.template_status_value = QLabel("")
        self.template_status_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        metadata_grid.addWidget(self._field_label("Name"), 0, 0)
        metadata_grid.addWidget(self.template_name_edit, 0, 1)
        metadata_grid.addWidget(self._field_label("External Asset"), 0, 2)
        metadata_grid.addWidget(external_asset_row, 0, 3)

        metadata_grid.addWidget(self._field_label("Kind"), 1, 0)
        metadata_grid.addWidget(self.template_kind_combo, 1, 1)
        metadata_grid.addWidget(self._field_label("Notes"), 1, 2)
        metadata_grid.addWidget(self.notes_edit, 1, 3, 2, 1)

        metadata_grid.addWidget(self._field_label("Content Format"), 2, 0)
        metadata_grid.addWidget(self.content_format_combo, 2, 1)
        metadata_grid.addWidget(self._field_label("Change Summary"), 3, 2)
        metadata_grid.addWidget(self.change_summary_edit, 3, 3, 2, 1)

        metadata_grid.addWidget(self._field_label("Output Format"), 3, 0)
        metadata_grid.addWidget(self.output_format_combo, 3, 1)
        metadata_grid.addWidget(self._field_label("Status"), 5, 2)
        metadata_grid.addWidget(self.template_status_value, 5, 3)

        metadata_grid.addWidget(self._field_label("Subject Line"), 4, 0)
        metadata_grid.addWidget(self.subject_line_edit, 4, 1)
        metadata_grid.addWidget(self._field_label("Current Version"), 5, 0)
        metadata_grid.addWidget(self.version_number_value, 5, 1)

        metadata_grid.setColumnStretch(1, 1)
        metadata_grid.setColumnStretch(3, 1)
        editor_layout.addWidget(metadata_group)

        self.body_content_edit = QTextEdit()
        self.body_content_edit.setAcceptRichText(True)
        self.body_content_edit.setMinimumHeight(420)
        self.rich_text_toolbar = RichTextToolbar(
            self.body_content_edit,
            token_provider=self._current_selected_token,
            parent=self,
        )
        self.bold_button = self.rich_text_toolbar.bold_button
        self.italic_button = self.rich_text_toolbar.italic_button
        self.underline_button = self.rich_text_toolbar.underline_button
        self.align_left_button = self.rich_text_toolbar.align_left_button
        self.align_center_button = self.rich_text_toolbar.align_center_button
        self.align_right_button = self.rich_text_toolbar.align_right_button
        self.bullets_button = self.rich_text_toolbar.bullets_button
        self.numbered_button = self.rich_text_toolbar.numbered_button
        self.toolbar_insert_token_button = self.rich_text_toolbar.insert_token_button
        editor_layout.addWidget(self.rich_text_toolbar)
        editor_layout.addWidget(self.body_content_edit, 1)

        self.default_template_label = QLabel("Estimate draft default: not applicable for this template.")
        self.default_template_label.setWordWrap(True)
        self.default_template_label.setStyleSheet("color: #555;")
        self.default_template_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        editor_layout.addWidget(self.default_template_label)

        action_row = QHBoxLayout()
        self.save_version_button = QPushButton("Save Version")
        self.save_version_button.clicked.connect(self._on_save_version)
        action_row.addWidget(self.save_version_button)
        self.activate_button = QPushButton("Activate")
        self.activate_button.clicked.connect(self._on_activate_selected)
        action_row.addWidget(self.activate_button)
        self.set_default_button = QPushButton("Set as Default for Estimate Draft Workspace")
        self.set_default_button.clicked.connect(self._on_set_estimate_default)
        self.set_default_button.setEnabled(False)
        action_row.addWidget(self.set_default_button)
        self.preview_button = QPushButton("Preview Active Document")
        self.preview_button.setEnabled(False)
        self.preview_button.setToolTip("Preview is not wired yet.")
        action_row.addWidget(self.preview_button)
        self.generate_docx_button = QPushButton("Generate Sample DOCX")
        self.generate_docx_button.setEnabled(False)
        self.generate_docx_button.setToolTip("DOCX generation is not wired yet.")
        action_row.addWidget(self.generate_docx_button)
        action_row.addStretch(1)
        editor_layout.addLayout(action_row)

        token_group = QGroupBox("Token Guide")
        token_group.setMinimumWidth(300)
        token_layout = QVBoxLayout(token_group)
        self.token_list = QListWidget()
        self.token_list.currentItemChanged.connect(self._on_token_selected)
        self.token_list.itemDoubleClicked.connect(self._insert_selected_token)
        token_layout.addWidget(self.token_list)
        token_actions = QHBoxLayout()
        self.insert_token_button = QPushButton("Insert Token")
        self.insert_token_button.clicked.connect(self._insert_selected_token)
        token_actions.addWidget(self.insert_token_button)
        token_actions.addStretch(1)
        token_layout.addLayout(token_actions)
        self.token_help_label = QLabel("Select a token to see its description.")
        self.token_help_label.setWordWrap(True)
        self.token_help_label.setStyleSheet("color: #555;")
        self.token_help_label.setMinimumHeight(48)
        self.token_help_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        token_layout.addWidget(self.token_help_label)

        workspace_splitter.addWidget(editor_column)
        workspace_splitter.addWidget(token_group)
        workspace_splitter.setSizes([980, 320])
        workspace_splitter.setStretchFactor(0, 1)
        workspace_splitter.setStretchFactor(1, 0)

        main_splitter.addWidget(left_pane)
        main_splitter.addWidget(right_pane)
        main_splitter.setSizes([380, 1000])
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)

        self._connect_dirty_tracking()
        self._update_default_template_controls()

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        self._populate_token_help()
        if self.container is None:
            self._set_status_message("")
            self._clear_document_types()
            self._clear_templates("Templates will appear here once the app container is available.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        catalog_service = getattr(self.container, "document_catalog_service", None)
        if catalog_service is None:
            self._set_status_message("")
            self._clear_document_types()
            self._clear_templates("Templates will appear here once document_catalog_service is wired.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        try:
            document_types = catalog_service.list_document_types()
        except Exception as exc:
            self._set_status_message(self._friendly_error_message(exc), is_error=True)
            self._clear_document_types()
            self._clear_templates("Could not load templates right now.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        if not document_types:
            self._set_status_message("No document types are available yet. Verify schema and seeded data.", is_error=True)
            self._clear_document_types()
            self._clear_templates("No templates are available yet.")
            self._reset_editor_for_empty_state("Select a template to inspect or edit it.")
            return

        self._set_status_message("")
        self._populate_document_types(document_types)
        self._refresh_templates_for_current_type()
        self._update_default_template_controls()

    def _on_document_type_changed(self) -> None:
        if self.document_type_combo.count() == 0:
            return
        self._refresh_templates_for_current_type()
        self._update_default_template_controls()

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
            self._set_status_message(self._friendly_error_message(exc), is_error=True)
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
            self._set_status_message(self._friendly_error_message(exc), is_error=True)
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
            self.external_asset_edit.setText("")
            self.notes_edit.setPlainText(version.notes or (summary.notes if summary is not None and summary.notes else ""))
            self.change_summary_edit.setPlainText(version.change_summary or "")
            self.version_number_value.setText(str(version.version_number))
            self._set_template_status(self._current_template_is_active)
            if str(version.content_format).lower() == "html":
                self.body_content_edit.setHtml(version.body_content or "")
            else:
                self.body_content_edit.setPlainText(version.body_content or "")
            self._is_dirty = False
        finally:
            self._suspend_dirty_tracking = False
        self._update_default_template_controls()

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
            self.external_asset_edit.setText("")
            self.notes_edit.setPlainText("")
            self.change_summary_edit.setPlainText("Initial version")
            self.version_number_value.setText("1")
            self._set_template_status(False)
            self.body_content_edit.clear()
            self._is_dirty = False
        finally:
            self._suspend_dirty_tracking = False
        self._update_default_template_controls()

        if document_type_code:
            self._set_status_message(
                f"Creating a new template for {document_type_code}. Fill in the fields and choose Save Version."
            )
        else:
            self._set_status_message("Creating a new template. Fill in the fields and choose Save Version.")

    def _on_save_version(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self._set_status_message("Document catalog service unavailable. Template could not be saved.", is_error=True)
            return

        document_type_code = self.document_type_combo.currentData()
        template_name = self.template_name_edit.text().strip()
        body_plain_text = self.body_content_edit.toPlainText().strip()
        if not document_type_code:
            self._set_status_message("Choose a document type before saving.", is_error=True)
            return
        if not template_name:
            self._set_status_message("Template name cannot be blank.", is_error=True)
            return
        if not body_plain_text:
            self._set_status_message("Body content cannot be blank.", is_error=True)
            return

        content_format = str(self.content_format_combo.currentData() or "html")
        version = DocumentTemplateVersion(
            template_version_id=None,
            template_id=self._current_template_id,
            document_type_code=str(document_type_code),
            kind=self._current_template_kind(),
            template_name=template_name,
            version_number=1,
            subject_line=self.subject_line_edit.text().strip() or None,
            body_content=self._editor_content_for_save(content_format),
            content_format=content_format,
            output_format=self._current_output_format(),
            token_schema=tuple(),
            change_summary=self.change_summary_edit.toPlainText().strip() or None,
            notes=self.notes_edit.toPlainText().strip() or None,
            is_active=False,
            created_by="ui",
        )

        try:
            saved_version = catalog_service.save_template(version)
        except Exception as exc:
            self._set_status_message(self._friendly_save_error_message(exc), is_error=True)
            return

        saved_template_id = saved_version.template_id
        self._set_status_message(
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

    def _on_activate_selected(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self._set_status_message("Document catalog service unavailable. Template could not be activated.", is_error=True)
            return

        template_id = self._current_template_id
        template_name = self.template_name_edit.text().strip() or "selected template"
        if template_id is None:
            self._set_status_message("Select a saved template before activating it.", is_error=True)
            return

        prompt = (
            f"Activate '{template_name}' for the selected document type and template kind?\n\n"
            "This will deactivate any other active template in the same document type and kind."
        )
        if self._is_dirty:
            prompt += "\n\nUnsaved edits are not included. Save a new version first if you want those changes activated."

        choice = QMessageBox.question(self, "Activate Template?", prompt)
        if choice != QMessageBox.StandardButton.Yes:
            self._set_status_message("Template activation cancelled.")
            return

        try:
            catalog_service.activate_template(template_id)
        except Exception as exc:
            self._set_status_message(self._friendly_activation_error_message(exc), is_error=True)
            return

        self._refresh_templates_for_current_type()
        self._reselect_template(template_id)
        self._set_status_message(
            f"Activated '{template_name}' for the selected document type and template kind."
        )
        self._update_default_template_controls()

    def _on_set_estimate_default(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        if catalog_service is None:
            self._set_status_message("Document catalog service unavailable. Default could not be updated.", is_error=True)
            return

        template_id = self._current_template_id
        document_type_code = str(self.document_type_combo.currentData() or "")
        kind = self._current_template_kind()
        if template_id is None or document_type_code != "ESTIMATE_DOCUMENT":
            self._set_status_message(
                "Select a saved ESTIMATE_DOCUMENT template before setting an estimate draft default.",
                is_error=True,
            )
            return
        if kind not in {DocumentTemplateKind.HEADER, DocumentTemplateKind.BODY, DocumentTemplateKind.FOOTER}:
            self._set_status_message(
                "Only estimate header, body, and footer templates can be marked as estimate draft defaults.",
                is_error=True,
            )
            return
        if not self._current_template_is_active:
            self._set_status_message(
                "Activate this template before marking it as the estimate draft default.",
                is_error=True,
            )
            return
        if self._is_dirty:
            self._set_status_message(
                "Save the current template changes first so the default points at a saved version.",
                is_error=True,
            )
            return

        try:
            catalog_service.set_template_default(
                document_type_code=document_type_code,
                template_kind=kind,
                usage_context=self._estimate_default_usage_context,
                template_id=int(template_id),
                updated_by="UI",
            )
        except Exception as exc:
            self._set_status_message(
                f"Estimate draft default could not be updated. Details: {exc}",
                is_error=True,
            )
            return

        QMessageBox.information(
            self,
            "Default Template Updated",
            f"'{self.template_name_edit.text().strip() or 'Selected template'}' is now the default "
            f"{kind.value} template for the estimate draft workspace.",
        )
        self._set_status_message(
            f"Updated the default {kind.value} template for the estimate draft workspace."
        )
        self._update_default_template_controls()

    def _on_browse_external_asset(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(self, "Choose External Asset")
        if not selected_path:
            return
        self.external_asset_edit.setText(selected_path)

    def _insert_selected_token(self, item: QListWidgetItem | None = None) -> None:
        token_item = item or self.token_list.currentItem()
        if token_item is None:
            QMessageBox.information(
                self,
                "No Token Selected",
                "Select a token in the Token Guide before using Insert Token.",
            )
            return
        token_text = token_item.data(Qt.ItemDataRole.UserRole) or token_item.text()
        target = self._current_token_insert_target()
        self._insert_text_into_widget(target, str(token_text))

    def _current_token_insert_target(self):
        focused = QApplication.focusWidget()
        if isinstance(focused, (QTextEdit, QPlainTextEdit, QLineEdit)):
            return focused
        return self.body_content_edit

    def _insert_text_into_widget(self, widget, text: str) -> None:
        if isinstance(widget, QTextEdit):
            cursor = widget.textCursor()
            cursor.insertText(text)
            widget.setTextCursor(cursor)
            widget.setFocus()
            return
        if isinstance(widget, QPlainTextEdit):
            cursor = widget.textCursor()
            cursor.insertText(text)
            widget.setTextCursor(cursor)
            widget.setFocus()
            return
        if isinstance(widget, QLineEdit):
            widget.insert(text)
            widget.setFocus()
            return

        cursor = self.body_content_edit.textCursor()
        cursor.insertText(text)
        self.body_content_edit.setTextCursor(cursor)
        self.body_content_edit.setFocus()

    def _populate_document_types(self, document_types: list[object]) -> None:
        selected_code = self.document_type_combo.currentData()
        self.document_type_combo.blockSignals(True)
        self.document_type_combo.clear()
        selected_index = 0
        customer_invoice_index = 0

        for index, document_type in enumerate(document_types):
            label = f"{document_type.display_name} [{document_type.document_type_code}]"
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
            label = f"{summary.kind.value.upper()} | {summary.template_name}"
            if summary.is_active:
                label = f"{label} [ACTIVE]"
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
            self._token_help_map = {}
            self.token_help_label.setText("Token guide will appear here once document_catalog_service is available.")
            self._populate_token_list({})
            return

        try:
            token_help = catalog_service.get_token_help()
        except Exception as exc:
            self._token_help_map = {}
            self.token_help_label.setText(f"Could not load token guide right now. Details: {exc}")
            self._populate_token_list({})
            return

        token_help_map = {
            "{CustomerName}": token_help.get("CustomerName", "Customer or account name."),
            "{Name}": "General person or company display name.",
            "{Price}": "Single line-item or quoted price text.",
            "{InvoiceNumber}": token_help.get("InvoiceNumber", "Customer-facing invoice number."),
            "{InvoiceDate}": token_help.get("InvoiceDate", "Formatted invoice date."),
            "{WorkOrderID}": token_help.get("WorkOrderID", "Work order identifier."),
            "{TotalAmount}": token_help.get("TotalAmount", "Rendered total amount text."),
        }

        self._token_help_map = token_help_map
        self._populate_token_list(token_help_map)

    def _populate_token_list(self, token_help_map: dict[str, str]) -> None:
        self.token_list.blockSignals(True)
        self.token_list.clear()
        for token_name in token_help_map:
            item = QListWidgetItem(token_name)
            item.setData(Qt.ItemDataRole.UserRole, token_name)
            self.token_list.addItem(item)
        self.token_list.blockSignals(False)
        if self.token_list.count() > 0:
            self.token_list.setCurrentRow(0)
            self._on_token_selected(self.token_list.currentItem(), None)
        else:
            self._on_token_selected(None, None)

    def _on_token_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            self.token_help_label.setText("Select a token to see its description.")
            return
        token_name = str(current.data(Qt.ItemDataRole.UserRole) or current.text())
        description = self._token_help_map.get(token_name, "No description is available for this token yet.")
        self.token_help_label.setText(f"{token_name}: {description}")

    def _current_selected_token(self) -> str | None:
        current_item = self.token_list.currentItem()
        if current_item is None:
            return None
        token_value = current_item.data(Qt.ItemDataRole.UserRole) or current_item.text()
        return str(token_value) if token_value else None

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
            self.external_asset_edit.setText("")
            self.notes_edit.setPlainText("")
            self.change_summary_edit.setPlainText("")
            self.version_number_value.setText("")
            self._set_template_status(False)
            self.body_content_edit.setPlainText(body_message)
            self._is_dirty = False
        finally:
            self._suspend_dirty_tracking = False
        self._update_default_template_controls()

    def _connect_dirty_tracking(self) -> None:
        self.template_name_edit.textChanged.connect(self._mark_dirty)
        self.template_kind_combo.currentIndexChanged.connect(self._mark_dirty)
        self.content_format_combo.currentIndexChanged.connect(self._mark_dirty)
        self.output_format_combo.currentIndexChanged.connect(self._mark_dirty)
        self.subject_line_edit.textChanged.connect(self._mark_dirty)
        self.external_asset_edit.textChanged.connect(self._mark_dirty)
        self.notes_edit.textChanged.connect(self._mark_dirty)
        self.change_summary_edit.textChanged.connect(self._mark_dirty)
        self.body_content_edit.textChanged.connect(self._mark_dirty)

    def _mark_dirty(self) -> None:
        if self._suspend_dirty_tracking:
            return
        self._is_dirty = True
        self._update_default_template_controls()

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

    def _editor_content_for_save(self, content_format: str) -> str:
        if content_format.lower() == "html":
            return self.body_content_edit.toHtml()
        return self.body_content_edit.toPlainText()

    def _reselect_template(self, template_id: int) -> None:
        for index in range(self.template_list.count()):
            item = self.template_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == template_id:
                self.template_list.setCurrentItem(item)
                return

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600;")
        return label

    def _set_template_status(self, is_active: bool) -> None:
        if is_active:
            self.template_status_value.setText("Active")
            self.template_status_value.setStyleSheet("color: #2e7d32; font-weight: 700;")
        else:
            self.template_status_value.setText("Inactive")
            self.template_status_value.setStyleSheet("color: #555; font-weight: 600;")

    def _set_status_message(self, message: str, is_error: bool = False) -> None:
        if not message:
            self.status_label.clear()
            self.status_label.hide()
            return
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #7a1f1f;" if is_error else "color: #345a2a;")
        self.status_label.show()

    def _update_default_template_controls(self) -> None:
        catalog_service = getattr(self.container, "document_catalog_service", None) if self.container else None
        template_id = self._current_template_id
        document_type_code = str(self.document_type_combo.currentData() or "")
        kind = self._current_template_kind()
        applicable = (
            template_id is not None
            and document_type_code == "ESTIMATE_DOCUMENT"
            and kind in {DocumentTemplateKind.HEADER, DocumentTemplateKind.BODY, DocumentTemplateKind.FOOTER}
        )

        if not applicable:
            self.set_default_button.setText("Set as Default for Estimate Draft Workspace")
            self.set_default_button.setEnabled(False)
            self.default_template_label.setText("Estimate draft default: not applicable for this template.")
            return

        kind_label = kind.value.capitalize()
        self.set_default_button.setText(f"Set as Default Estimate {kind_label}")
        self.set_default_button.setEnabled(bool(self._current_template_is_active and not self._is_dirty))

        default_text = f"Estimate draft default {kind.value}: not set."
        if catalog_service is not None:
            try:
                default_mapping = catalog_service.get_template_default(
                    document_type_code="ESTIMATE_DOCUMENT",
                    template_kind=kind,
                    usage_context=self._estimate_default_usage_context,
                )
                if default_mapping is not None:
                    matching_summary = self._template_summaries_by_id.get(int(default_mapping.template_id))
                    if matching_summary is not None:
                        default_text = f"Estimate draft default {kind.value}: {matching_summary.template_name}"
                    else:
                        default_text = (
                            f"Estimate draft default {kind.value}: template #{default_mapping.template_id}"
                        )
                    if int(default_mapping.template_id) == int(template_id):
                        default_text += " [CURRENT]"
            except Exception as exc:
                default_text = f"Estimate draft default {kind.value}: unavailable ({exc})"

        self.default_template_label.setText(default_text)

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
