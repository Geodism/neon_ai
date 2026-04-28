from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.bootstrap import ensure_legacy_import_paths

ensure_legacy_import_paths()

from database.estimates import (
    convert_estimate_to_workorder,
    get_dashboard_estimates,
    get_estimate_notes,
    get_full_estimate,
    insert_estimate_note,
)


class SortableValueItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value: float | str | None = None) -> None:
        super().__init__(text)
        self.sort_value = text if sort_value is None else sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        other_value = getattr(other, "sort_value", other.text())
        return self.sort_value < other_value


class EstimateViewerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_est_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("Estimate Pipeline Review")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

    def _build_list_panel(self) -> QGroupBox:
        group = QGroupBox("Pending Estimates")
        layout = QVBoxLayout(group)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Est #", "Date", "Customer", "Site", "Total Value", "Status"])
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 130)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 100)
        self.table.itemSelectionChanged.connect(self._on_est_select)
        layout.addWidget(self.table)
        return group

    def _build_detail_panel(self) -> QGroupBox:
        group = QGroupBox("Estimate Details")
        layout = QVBoxLayout(group)

        self.id_label = QLabel("Select an Estimate to review")
        self.id_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(self.id_label)

        self.billing_label = QLabel("")
        self.billing_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self.billing_label)

        self.desc_label = QLabel("")
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        self.detail_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.detail_splitter, 1)

        labor_group = QGroupBox("Labor Breakdown")
        labor_layout = QVBoxLayout(labor_group)
        self.labor_table = QTableWidget(0, 3)
        self.labor_table.setHorizontalHeaderLabels(["Role", "Hours", "Total"])
        self.labor_table.horizontalHeader().setStretchLastSection(True)
        self.labor_table.setColumnWidth(1, 90)
        labor_layout.addWidget(self.labor_table)
        self.detail_splitter.addWidget(labor_group)

        material_group = QGroupBox("Material Breakdown")
        material_layout = QVBoxLayout(material_group)
        self.mat_table = QTableWidget(0, 3)
        self.mat_table.setHorizontalHeaderLabels(["Desc", "Qty", "Total"])
        self.mat_table.horizontalHeader().setStretchLastSection(True)
        self.mat_table.setColumnWidth(1, 90)
        material_layout.addWidget(self.mat_table)
        self.detail_splitter.addWidget(material_group)

        notes_group = QGroupBox("Sales & Activity Notes")
        notes_layout = QVBoxLayout(notes_group)
        note_row = QFrame()
        note_row_layout = QHBoxLayout(note_row)
        note_row_layout.setContentsMargins(0, 0, 0, 0)
        self.note_field = QLineEdit()
        self.note_field.textChanged.connect(self._limit_note_length)
        note_row_layout.addWidget(self.note_field, 1)
        self.add_note_button = QPushButton("Post Note")
        self.add_note_button.clicked.connect(self._add_note)
        self.add_note_button.setEnabled(False)
        note_row_layout.addWidget(self.add_note_button)
        notes_layout.addWidget(note_row)
        self.notes_log = QPlainTextEdit()
        self.notes_log.setReadOnly(True)
        notes_layout.addWidget(self.notes_log)
        self.detail_splitter.addWidget(notes_group)

        totals_row = QFrame()
        totals_layout = QHBoxLayout(totals_row)
        totals_layout.setContentsMargins(0, 0, 0, 0)
        self.lab_total_label = QLabel("Labor: $0.00")
        self.lab_total_label.setStyleSheet("color: gray;")
        totals_layout.addWidget(self.lab_total_label)
        self.mat_total_label = QLabel("Material: $0.00")
        self.mat_total_label.setStyleSheet("color: gray;")
        totals_layout.addWidget(self.mat_total_label)
        totals_layout.addStretch(1)
        self.grand_total_label = QLabel("GRAND TOTAL: $0.00")
        self.grand_total_label.setStyleSheet("font-size: 14px; font-weight: 700; color: green;")
        totals_layout.addWidget(self.grand_total_label)
        layout.addWidget(totals_row)

        self.approve_button = QPushButton("Approve & Convert to Work Order")
        self.approve_button.clicked.connect(self._do_approve)
        self.approve_button.setEnabled(False)
        layout.addWidget(self.approve_button)
        return group

    def refresh_data(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        records = get_dashboard_estimates()
        for record in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            total_value = float(record.get("TotalValue") or 0)
            values = [
                SortableValueItem(str(record["EstimateID"]), int(record["EstimateID"])),
                SortableValueItem(str(record.get("CreatedDate") or "N/A")),
                SortableValueItem(str(record.get("CustomerName") or "")),
                SortableValueItem(str(record.get("SiteName") or "")),
                SortableValueItem(f"${total_value:,.2f}", total_value),
                SortableValueItem(str(record.get("Status") or "Draft")),
            ]
            for column, item in enumerate(values):
                if column in (0, 1, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self._clear_details()

    def _clear_details(self) -> None:
        self.current_est_id = None
        self.id_label.setText("Select an Estimate to review")
        self.billing_label.setText("")
        self.desc_label.setText("")
        self.note_field.setText("")
        self.add_note_button.setEnabled(False)
        self.approve_button.setEnabled(False)
        self.lab_total_label.setText("Labor: $0.00")
        self.mat_total_label.setText("Material: $0.00")
        self.grand_total_label.setText("GRAND TOTAL: $0.00")
        self.notes_log.setPlainText("")
        self.labor_table.setRowCount(0)
        self.mat_table.setRowCount(0)

    def _load_notes_for_estimate(self, est_id: int) -> None:
        notes = get_estimate_notes(est_id)
        lines = [f"[{note['Date']}] {note['NoteText']}" for note in notes]
        self.notes_log.setPlainText("\n\n".join(lines))

    def _on_est_select(self) -> None:
        selection = self.table.selectedItems()
        if not selection:
            return

        self.current_est_id = int(self.table.item(self.table.row(selection[0]), 0).text())
        data = get_full_estimate(self.current_est_id)
        parent = data["parent"]

        self.id_label.setText(
            f"Estimate #{parent['EstimateID']} - {parent['CustomerName']} ({parent['SiteName']})"
        )
        self.billing_label.setText(f"Type: {parent.get('BillingType', 'N/A')}")
        self.desc_label.setText(f"Scope: {parent.get('Description', '')}")

        labor_markup = float(parent.get("LaborMarkUp") or 0) / 100
        material_markup = float(parent.get("MaterialMarkUp") or 0) / 100

        labor_total = 0.0
        self.labor_table.setRowCount(0)
        for labor in data.get("labor", []):
            sell = float(labor.get("LineTotal", 0)) * (1 + labor_markup)
            labor_total += sell
            self._append_row(
                self.labor_table,
                [
                    str(labor.get("RoleDescription", "")),
                    str(float(labor.get("Hours", 0))),
                    f"${sell:,.2f}",
                ],
                {2: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter},
            )

        material_total = 0.0
        self.mat_table.setRowCount(0)
        for material in data.get("materials", []):
            sell = float(material.get("LineTotal", 0)) * (1 + material_markup)
            material_total += sell
            self._append_row(
                self.mat_table,
                [
                    str(material.get("Description", "")),
                    str(float(material.get("Quantity", 0))),
                    f"${sell:,.2f}",
                ],
                {2: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter},
            )

        self.lab_total_label.setText(f"Labor: ${labor_total:,.2f}")
        self.mat_total_label.setText(f"Material: ${material_total:,.2f}")
        self.grand_total_label.setText(f"GRAND TOTAL: ${(labor_total + material_total):,.2f}")

        self._load_notes_for_estimate(self.current_est_id)
        self.add_note_button.setEnabled(True)
        self.approve_button.setEnabled(True)

    def _append_row(
        self,
        table: QTableWidget,
        values: list[str],
        alignments: dict[int, Qt.AlignmentFlag] | None = None,
    ) -> None:
        alignments = alignments or {}
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(alignments.get(column, Qt.AlignmentFlag.AlignLeft))
            table.setItem(row, column, item)

    def _limit_note_length(self, text: str) -> None:
        if len(text) > 140:
            self.note_field.setText(text[:140])

    def _add_note(self) -> None:
        note_text = self.note_field.text().strip()
        if not note_text or not self.current_est_id:
            return

        try:
            insert_estimate_note(self.current_est_id, note_text)
            self.note_field.setText("")
            self._load_notes_for_estimate(self.current_est_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save note: {exc}")

    def _do_approve(self) -> None:
        if not self.current_est_id:
            return

        popup = QDialog(self)
        popup.setWindowTitle("Authorization Required")
        popup.resize(450, 250)
        popup.setModal(True)

        layout = QVBoxLayout(popup)
        title = QLabel("Job Authorization")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(title)
        layout.addWidget(
            QLabel(
                "A Customer PO and Acceptance Document are required\n"
                "to generate a Work Order."
            )
        )

        po_row = QFrame()
        po_layout = QHBoxLayout(po_row)
        po_layout.setContentsMargins(0, 0, 0, 0)
        po_layout.addWidget(QLabel("Customer PO *:"))
        po_field = QLineEdit()
        po_layout.addWidget(po_field, 1)
        layout.addWidget(po_row)

        doc_row = QFrame()
        doc_layout = QHBoxLayout(doc_row)
        doc_layout.setContentsMargins(0, 0, 0, 0)
        doc_layout.addWidget(QLabel("Auth Document *:"))
        doc_label = QLabel("No file selected")
        doc_label.setStyleSheet("color: gray;")
        doc_layout.addWidget(doc_label, 1)
        layout.addWidget(doc_row)

        selected_path = {"value": ""}

        def browse_file() -> None:
            path, _ = QFileDialog.getOpenFileName(
                popup,
                "Browse",
                "",
                "PDF & Images (*.pdf *.jpg *.png);;All Files (*.*)",
            )
            if path:
                selected_path["value"] = path
                doc_label.setText(path)
                doc_label.setStyleSheet("color: green;")

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(browse_file)
        doc_layout.addWidget(browse_button)

        def confirm_approval() -> None:
            po_number = po_field.text().strip()
            doc_path = selected_path["value"]
            if not po_number or not doc_path:
                QMessageBox.warning(
                    popup,
                    "Missing Data",
                    "You must provide both a PO number and an Acceptance Document.",
                )
                return

            try:
                convert_estimate_to_workorder(self.current_est_id, po_number, doc_path)
                QMessageBox.information(popup, "Success", "Work Order officially generated!")
                popup.accept()
                self.refresh_data()
            except Exception as exc:
                QMessageBox.critical(popup, "Error", f"Failed to convert: {exc}")

        confirm_button = QPushButton("Confirm & Generate Work Order")
        confirm_button.clicked.connect(confirm_approval)
        layout.addWidget(confirm_button)

        popup.exec()
