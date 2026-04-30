from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class GeneratedDocumentsPage(QWidget):
    def __init__(self, main_window=None, container=None) -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self.container = container or getattr(main_window, "container", None)
        self._records: list[object] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Generated History")
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

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        table_group = QGroupBox("Generated Documents")
        table_layout = QVBoxLayout(table_group)
        self.history_table = QTableWidget(0, 9)
        self.history_table.setHorizontalHeaderLabels(
            [
                "Created",
                "Type",
                "Format",
                "Filename",
                "Relative Path",
                "Absolute Path",
                "Source Type",
                "Source ID",
                "Created By",
            ]
        )
        self.history_table.setSelectionBehavior(self.history_table.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(self.history_table.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(self.history_table.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.itemSelectionChanged.connect(self._on_row_selected)
        self.history_table.setColumnWidth(0, 140)
        self.history_table.setColumnWidth(1, 120)
        self.history_table.setColumnWidth(2, 80)
        self.history_table.setColumnWidth(3, 220)
        self.history_table.setColumnWidth(4, 240)
        self.history_table.setColumnWidth(5, 300)
        self.history_table.setColumnWidth(6, 100)
        self.history_table.setColumnWidth(7, 120)
        self.history_table.setColumnWidth(8, 100)
        table_layout.addWidget(self.history_table)
        splitter.addWidget(table_group)

        details_group = QGroupBox("Details")
        details_layout = QVBoxLayout(details_group)
        details_form = QFormLayout()
        details_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.absolute_path_value = QLabel("")
        self.template_version_ids_value = QLabel("")
        self.source_record_type_value = QLabel("")
        self.source_record_id_value = QLabel("")

        for value_widget in (
            self.absolute_path_value,
            self.template_version_ids_value,
            self.source_record_type_value,
            self.source_record_id_value,
        ):
            value_widget.setWordWrap(True)
            value_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        details_form.addRow("Absolute Path", self.absolute_path_value)
        details_form.addRow("Template Version IDs", self.template_version_ids_value)
        details_form.addRow("Source Record Type", self.source_record_type_value)
        details_form.addRow("Source Record ID", self.source_record_id_value)
        details_layout.addLayout(details_form)

        context_heading = QLabel("Context Snapshot")
        context_heading.setStyleSheet("font-weight: 700;")
        details_layout.addWidget(context_heading)

        self.context_snapshot_text = QPlainTextEdit()
        self.context_snapshot_text.setReadOnly(True)
        details_layout.addWidget(self.context_snapshot_text, 1)
        splitter.addWidget(details_group)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

    def refresh(self) -> None:
        self.container = self.container or getattr(self.main_window, "container", None)
        if self.container is None:
            self.status_label.setText("Container unavailable. Generated history cannot be loaded yet.")
            self._clear_document_types()
            self._populate_empty_table("Generated document history will appear here once the app container is available.")
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        catalog_service = getattr(self.container, "document_catalog_service", None)
        generated_service = getattr(self.container, "generated_document_service", None)
        if catalog_service is None or generated_service is None:
            self.status_label.setText("Document control services unavailable. Generated history cannot be loaded yet.")
            self._clear_document_types()
            self._populate_empty_table("Generated document history will appear here once document control services are wired.")
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        try:
            document_types = catalog_service.list_document_types()
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._clear_document_types()
            self._populate_empty_table("Could not load generated document history right now.")
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        if not document_types:
            self.status_label.setText("No document types are available yet. Verify schema and seeded data.")
            self._clear_document_types()
            self._populate_empty_table("No generated document history is available yet.")
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        self.status_label.setText("Generated document service available.")
        self._populate_document_types(document_types)
        self._refresh_generated_documents_for_current_type()

    def _on_document_type_changed(self) -> None:
        if self.document_type_combo.count() == 0:
            return
        self._refresh_generated_documents_for_current_type()

    def _refresh_generated_documents_for_current_type(self) -> None:
        generated_service = getattr(self.container, "generated_document_service", None) if self.container else None
        document_type_code = self.document_type_combo.currentData()
        if generated_service is None or not document_type_code:
            self._populate_empty_table("No generated document history is available yet.")
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        try:
            records = generated_service.list_generated_documents(document_type_code=document_type_code)
        except Exception as exc:
            self.status_label.setText(self._friendly_error_message(exc))
            self._populate_empty_table("Could not load generated document history right now.")
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        self._populate_history_table(records)

    def _on_row_selected(self) -> None:
        selection = self.history_table.selectedItems()
        if not selection:
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        row_index = self.history_table.row(selection[0])
        if row_index < 0 or row_index >= len(self._records):
            self._clear_details("Could not load generated document details.")
            return

        record = self._records[row_index]
        self.absolute_path_value.setText(record.absolute_path or "(none)")
        self.template_version_ids_value.setText(
            ", ".join(str(value) for value in record.template_version_ids) if record.template_version_ids else "(none)"
        )
        self.source_record_type_value.setText(record.source_record_type or "(none)")
        self.source_record_id_value.setText(record.source_record_id or "(none)")
        self.context_snapshot_text.setPlainText(
            json.dumps(record.context_snapshot or {}, indent=2, sort_keys=True)
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

    def _populate_history_table(self, records: list[object]) -> None:
        self._records = list(records)
        self.history_table.clearContents()
        self.history_table.setRowCount(0)

        if not self._records:
            self._populate_empty_table("No generated document history was found for the selected document type.")
            self._clear_details("Select a generated document row to inspect its stored details.")
            return

        self.history_table.setRowCount(len(self._records))
        for row_index, record in enumerate(self._records):
            values = [
                self._format_datetime(record.created_at),
                record.document_type_code or "",
                record.output_format.value if record.output_format else "",
                record.rendered_filename or "",
                record.relative_path or "",
                record.absolute_path or "",
                record.source_record_type or "",
                record.source_record_id or "",
                record.created_by or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (0, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.history_table.setItem(row_index, column, item)

        self.history_table.selectRow(0)
        self._on_row_selected()

    def _populate_empty_table(self, message: str) -> None:
        self._records = []
        self.history_table.clearContents()
        self.history_table.setRowCount(1)
        placeholder = QTableWidgetItem(message)
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        self.history_table.setItem(0, 0, placeholder)
        for column in range(1, self.history_table.columnCount()):
            self.history_table.setItem(column // self.history_table.columnCount(), column, QTableWidgetItem(""))

    def _clear_document_types(self) -> None:
        self.document_type_combo.blockSignals(True)
        self.document_type_combo.clear()
        self.document_type_combo.blockSignals(False)

    def _clear_details(self, message: str) -> None:
        self.absolute_path_value.setText("")
        self.template_version_ids_value.setText("")
        self.source_record_type_value.setText("")
        self.source_record_id_value.setText("")
        self.context_snapshot_text.setPlainText(message)

    def _friendly_error_message(self, exc: Exception) -> str:
        details = str(exc)
        lowered = details.lower()
        if "database connection is unavailable" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        if "schema is not available" in lowered or "database_schema.sql" in lowered:
            return "Document control schema is missing. Apply database_schema.sql first."
        if "connection" in lowered and "failed" in lowered:
            return "Database connection unavailable. Please verify the app database settings."
        return f"Could not load generated document history right now. Details: {details}"

    def _format_datetime(self, value) -> str:
        if value is None:
            return ""
        try:
            return value.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)
