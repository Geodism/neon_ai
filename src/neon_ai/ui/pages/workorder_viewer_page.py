from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.automation import add_note_to_active_job
from neon_ai.database.folders import get_target_folder
from neon_ai.database.workorders import (
    check_closure_requirements,
    close_work_order,
    get_dashboard_work_orders,
    get_work_order_telemetry,
    get_wo_export_data,
)
from neon_ai.wo_exporter import generate_workorder_docx


class WorkOrderViewerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_wo_id: int | None = None
        self.doc_path: str | None = None
        self.row_cache: dict[int, dict] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("Work Order Command Center")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

    def _build_list_panel(self) -> QGroupBox:
        group = QGroupBox("Active Work Orders")
        layout = QVBoxLayout(group)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["WO #", "Created", "Site", "Status", "Margin"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 90)
        self.table.itemSelectionChanged.connect(self._on_wo_select)
        layout.addWidget(self.table)
        return group

    def _build_detail_panel(self) -> QWidget:
        wrapper = QWidget()
        outer_layout = QVBoxLayout(wrapper)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        self.detail_frame = content

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.id_label = QLabel("Select a Work Order")
        self.id_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        header_layout.addWidget(self.id_label)
        header_layout.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; font-weight: 700;")
        header_layout.addWidget(self.status_label)
        layout.addWidget(header)

        self.desc_label = QLabel("")
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        comp_group = QGroupBox("Sales Authorization")
        comp_layout = QHBoxLayout(comp_group)
        comp_layout.addWidget(QLabel("Customer PO:"))
        self.po_label = QLabel("N/A")
        comp_layout.addWidget(self.po_label)
        comp_layout.addSpacing(30)
        comp_layout.addWidget(QLabel("Acceptance Doc:"))
        self.view_doc_button = QPushButton("View Document")
        self.view_doc_button.clicked.connect(self.view_document)
        self.view_doc_button.setEnabled(False)
        comp_layout.addWidget(self.view_doc_button)
        self.doc_name_label = QLabel("No file attached")
        comp_layout.addWidget(self.doc_name_label, 1)
        layout.addWidget(comp_group)

        self.labor_table = self._make_table(["Date", "Role", "Task", "Hours", "Cost"], [85, 120, 150, 70, 90])
        layout.addWidget(self._wrap_group("Labor Burn", self.labor_table))

        self.mat_table = self._make_table(["PO #", "Date", "Vendor", "Cost"], [80, 100, 150, 90])
        layout.addWidget(self._wrap_group("Material Purchases", self.mat_table))

        self.bill_table = self._make_table(["Inv #", "Date", "Amount"], [80, 100, 90])
        layout.addWidget(self._wrap_group("Billing Ledger", self.bill_table))

        notes_group = QGroupBox("Operations Notes")
        notes_layout = QVBoxLayout(notes_group)
        note_row = QFrame()
        note_row_layout = QHBoxLayout(note_row)
        note_row_layout.setContentsMargins(0, 0, 0, 0)
        self.note_field = QLineEdit()
        note_row_layout.addWidget(self.note_field, 1)
        self.add_note_button = QPushButton("Post Note")
        self.add_note_button.clicked.connect(self.add_note)
        self.add_note_button.setEnabled(False)
        note_row_layout.addWidget(self.add_note_button)
        notes_layout.addWidget(note_row)
        self.notes_log = QPlainTextEdit()
        self.notes_log.setReadOnly(True)
        notes_layout.addWidget(self.notes_log)
        layout.addWidget(notes_group)

        totals = QFrame()
        totals_layout = QHBoxLayout(totals)
        totals_layout.setContentsMargins(0, 0, 0, 0)
        self.est_total_label = QLabel("Estimated Cost: $0.00")
        self.est_total_label.setStyleSheet("color: gray;")
        totals_layout.addWidget(self.est_total_label)
        self.act_total_label = QLabel("Actual Cost: $0.00")
        self.act_total_label.setStyleSheet("color: gray;")
        totals_layout.addWidget(self.act_total_label)
        totals_layout.addStretch(1)
        self.margin_label = QLabel("CURRENT MARGIN: $0.00")
        self.margin_label.setStyleSheet("font-size: 14px; font-weight: 700; color: gray;")
        totals_layout.addWidget(self.margin_label)
        layout.addWidget(totals)

        button_row = QFrame()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        self.export_button = QPushButton("Export WO to Word")
        self.export_button.clicked.connect(self.do_export)
        self.export_button.setEnabled(False)
        button_layout.addWidget(self.export_button)
        self.close_button = QPushButton("Close Work Order")
        self.close_button.clicked.connect(self.do_close)
        self.close_button.setEnabled(False)
        button_layout.addWidget(self.close_button)
        layout.addWidget(button_row)
        layout.addStretch(1)
        return wrapper

    def _make_table(self, headers: list[str], widths: list[int]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for index, width in enumerate(widths):
            table.setColumnWidth(index, width)
        return table

    def _wrap_group(self, title: str, child: QWidget) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(child)
        return group

    def refresh_data(self) -> None:
        self.row_cache.clear()
        self.table.setRowCount(0)
        try:
            records = get_dashboard_work_orders()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to load Work Orders:\n{exc}")
            return

        for record in records:
            wo_id = int(record["WorkOrderID"])
            created = str(record.get("CreatedDate") or "")
            site = str(record.get("SiteName") or "")
            status = str(record.get("Status") or "")
            est_cost = float(record.get("EstCost") or 0)
            act_cost = float(record.get("ActCost") or 0)
            margin = est_cost - act_cost
            self.row_cache[wo_id] = {
                "created": created,
                "site": site,
                "status": status,
                "est_cost": est_cost,
                "act_cost": act_cost,
                "margin": margin,
            }

            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                str(wo_id),
                created,
                site,
                status,
                f"${margin:,.2f}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 1, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if act_cost > est_cost:
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, column, item)

        self.clear_details()

    def clear_details(self) -> None:
        self.current_wo_id = None
        self.doc_path = None
        self.id_label.setText("Select a Work Order")
        self.status_label.setText("")
        self.desc_label.setText("")
        self.po_label.setText("N/A")
        self.doc_name_label.setText("No file attached")
        self.view_doc_button.setEnabled(False)
        self.note_field.setText("")
        self.add_note_button.setEnabled(False)
        self.notes_log.setPlainText("")
        self.labor_table.setRowCount(0)
        self.mat_table.setRowCount(0)
        self.bill_table.setRowCount(0)
        self.est_total_label.setText("Estimated Cost: $0.00")
        self.act_total_label.setText("Actual Cost: $0.00")
        self.margin_label.setText("CURRENT MARGIN: $0.00")
        self.margin_label.setStyleSheet("font-size: 14px; font-weight: 700; color: gray;")
        self.close_button.setEnabled(False)
        self.export_button.setEnabled(False)

    def _on_wo_select(self) -> None:
        selection = self.table.selectedItems()
        if not selection:
            return

        self.current_wo_id = int(self.table.item(self.table.row(selection[0]), 0).text())
        cache = self.row_cache.get(self.current_wo_id, {})
        created_date = cache.get("created", "")
        est_cost = float(cache.get("est_cost", 0))
        act_cost = float(cache.get("act_cost", 0))
        margin = est_cost - act_cost
        self.export_button.setEnabled(True)

        try:
            data = get_work_order_telemetry(self.current_wo_id)
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to pull telemetry:\n{exc}")
            return
        parent = data["parent"]

        self.id_label.setText(f"Work Order #{self.current_wo_id}  (Opened: {created_date})")
        self.desc_label.setText(f"Scope: {parent.get('Description') or 'No scope provided.'}")

        if parent.get("IsClosed"):
            self.status_label.setText("[CLOSED]")
            self.status_label.setStyleSheet("font-size: 12px; font-weight: 700; color: gray;")
            self.close_button.setEnabled(False)
        else:
            self.status_label.setText("[OPEN]")
            self.status_label.setStyleSheet("font-size: 12px; font-weight: 700; color: orange;")
            self.close_button.setEnabled(True)

        self.add_note_button.setEnabled(True)

        po_text = parent.get("CustomerPO")
        self.po_label.setText(po_text or "N/A")
        self.po_label.setStyleSheet(f"color: {'blue' if po_text else 'gray'}; font-weight: 700;")

        doc = parent.get("AcceptanceDocPath")
        if doc:
            self.doc_path = doc
            self.doc_name_label.setText(os.path.basename(doc))
            self.view_doc_button.setEnabled(True)
            self.doc_name_label.setStyleSheet("color: green;")
        else:
            self.doc_path = None
            self.doc_name_label.setText("No file attached")
            self.view_doc_button.setEnabled(False)
            self.doc_name_label.setStyleSheet("color: gray;")

        self._load_table(
            self.labor_table,
            data.get("labor", []),
            lambda row: [
                str(row.get("DateWorked") or ""),
                str(row.get("Role") or ""),
                str(row.get("Task") or ""),
                str(float(row.get("HoursWorked") or 0)),
                f"${float(row.get('Cost') or 0):,.2f}",
            ],
            right_align={4},
        )
        self._load_table(
            self.mat_table,
            data.get("materials", []),
            lambda row: [
                str(row.get("PONum") or ""),
                str(row.get("Date") or ""),
                str(row.get("VendorName") or ""),
                f"${float(row.get('Cost') or 0):,.2f}",
            ],
            right_align={3},
        )
        self._load_table(
            self.bill_table,
            data.get("invoices", []),
            lambda row: [
                f"INV-{row.get('InvNum')}",
                str(row.get("Date") or ""),
                f"${float(row.get('Amount') or 0):,.2f}",
            ],
            right_align={2},
        )

        note_lines = [f"[{note['Date']}] {note['NoteText']}" for note in data.get("notes", [])]
        self.notes_log.setPlainText("\n\n".join(note_lines))

        self.est_total_label.setText(f"Estimated Cost: ${est_cost:,.2f}")
        self.act_total_label.setText(f"Actual Cost: ${act_cost:,.2f}")
        if margin < 0:
            self.margin_label.setText(f"CURRENT MARGIN: -${abs(margin):,.2f}")
            self.margin_label.setStyleSheet("font-size: 14px; font-weight: 700; color: red;")
        else:
            self.margin_label.setText(f"CURRENT MARGIN: +${margin:,.2f}")
            self.margin_label.setStyleSheet("font-size: 14px; font-weight: 700; color: green;")

    def _load_table(self, table: QTableWidget, rows: list[dict], builder, right_align: set[int] | None = None) -> None:
        right_align = right_align or set()
        table.setRowCount(0)
        for data_row in rows:
            row = table.rowCount()
            table.insertRow(row)
            values = builder(data_row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in right_align:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row, column, item)

    def view_document(self) -> None:
        if self.doc_path:
            if os.path.exists(self.doc_path):
                try:
                    os.startfile(self.doc_path)
                except Exception as exc:
                    QMessageBox.critical(self, "System Error", f"Could not launch viewer:\n{exc}")
            else:
                QMessageBox.warning(
                    self,
                    "File Missing",
                    f"The document was not found at the expected path:\n{self.doc_path}\n\n"
                    "Check if the drive is connected or if the file was moved.",
                )
        else:
            QMessageBox.information(self, "No Document", "There is no acceptance document linked to this Work Order.")

    def add_note(self) -> None:
        note_text = self.note_field.text().strip()
        if not self.current_wo_id or not note_text:
            return
        result = add_note_to_active_job(self.current_wo_id, note_text)
        if str(result).startswith("Success:"):
            self.note_field.setText("")
            self._on_wo_select()
        else:
            QMessageBox.critical(self, "Database Error", str(result))

    def do_export(self) -> None:
        if not self.current_wo_id:
            return
        try:
            doc_data = get_wo_export_data(self.current_wo_id)
            target_dir = get_target_folder(
                street_num=doc_data.get("StreetNumber"),
                street_name=doc_data.get("StreetName"),
                site_name=doc_data.get("SiteName"),
                estimate_id=doc_data.get("EstimateID"),
                category=None,
            )
            filename = f"Field_WorkOrder_{self.current_wo_id}.docx"
            full_path = os.path.join(target_dir, filename).replace("\\", "/")
            generate_workorder_docx(doc_data, full_path)
            QMessageBox.information(self, "Export Successful", f"Work Order saved to project bucket:\n{full_path}")
            os.startfile(full_path)
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to save: {exc}")

    def do_close(self) -> None:
        if not self.current_wo_id:
            return
        status = check_closure_requirements(self.current_wo_id)
        if not status["is_closable"]:
            error_msg = "CANNOT CLOSE WORK ORDER:\n\n"
            if not status["customer_clear"]:
                error_msg += f"Customer Balance: {status['billing_status']}\n"
            if not status["vendor_clear"]:
                error_msg += f"Vendor Balance: {status['vendor_status']} POs missing bills.\n"
            QMessageBox.critical(self, "Closure Denied", error_msg)
            return

        confirmed = QMessageBox.question(
            self,
            "Final Confirmation",
            "All financials are cleared. Close Work Order permanently?",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            close_work_order(self.current_wo_id)
            QMessageBox.information(self, "Success", "Work Order Closed and Archived.")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to close: {exc}")
