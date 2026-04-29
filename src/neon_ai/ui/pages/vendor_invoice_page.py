from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.vendor_invoices import (
    apply_vendor_invoice_adjustment,
    create_vendor_invoice_draft,
    generate_vendor_invoice_report,
    get_received_purchase_order_choices,
    get_vendor_invoice_creation_context,
    get_vendor_invoice_pipeline,
    get_vendor_invoice_workspace_by_invoice_id,
    get_vendor_payables_summary,
    lock_vendor_invoice_ready_to_pay,
    mark_vendor_invoice_paid,
    save_vendor_invoice_draft,
)


class VendorInvoicePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_po_id: int | None = None
        self.active_invoice_id: int | None = None
        self.current_rows: list[dict] = []
        self.report_path: str | None = None
        self.current_invoice_status = ""
        self.current_receipt_key = "all_received"
        self.current_receipt_label = "All received material on this PO"
        self.current_po_total = 0.0
        self.current_po_rollup_base = 0.0
        self.po_choice_map: dict[str, int] = {}
        self.receipt_choice_map: dict[str, str] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Vendor Invoice Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_data)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left_group = QGroupBox("Vendor Invoices")
        left_layout = QVBoxLayout(left_group)
        self.pipeline_table = QTableWidget(0, 9)
        self.pipeline_table.setHorizontalHeaderLabels(
            ["PO_ID", "Inv_ID", "Inv #", "Vendor", "Site", "Date", "PO Status", "Invoice Status", "Amount"]
        )
        self.pipeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pipeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pipeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pipeline_table.verticalHeader().setVisible(False)
        self.pipeline_table.setColumnHidden(0, True)
        self.pipeline_table.setColumnHidden(1, True)
        self.pipeline_table.setColumnWidth(2, 90)
        self.pipeline_table.setColumnWidth(3, 120)
        self.pipeline_table.setColumnWidth(4, 120)
        self.pipeline_table.setColumnWidth(5, 85)
        self.pipeline_table.setColumnWidth(6, 90)
        self.pipeline_table.setColumnWidth(7, 95)
        self.pipeline_table.setColumnWidth(8, 90)
        self.pipeline_table.itemSelectionChanged.connect(self.on_invoice_select)
        left_layout.addWidget(self.pipeline_table)
        splitter.addWidget(left_group)

        right_group = QGroupBox("Invoice Reconciliation Workspace")
        right_layout = QVBoxLayout(right_group)
        splitter.addWidget(right_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        header = QWidget()
        header_layout = QGridLayout(header)
        header_layout.setHorizontalSpacing(8)
        header_layout.setVerticalSpacing(6)
        self.lbl_context = QLabel("Select a purchase order to start the bill.")
        self.lbl_context.setStyleSheet("font-size: 11px; font-weight: 700;")
        header_layout.addWidget(self.lbl_context, 0, 0, 1, 6)

        header_layout.addWidget(QLabel("Purchase Order"), 1, 0)
        self.po_select_combo = QComboBox()
        self.po_select_combo.setMinimumWidth(280)
        self.po_select_combo.currentTextChanged.connect(self.on_po_choice_select)
        header_layout.addWidget(self.po_select_combo, 1, 1)

        header_layout.addWidget(QLabel("Receiving Paperwork"), 1, 2)
        self.receipt_select_combo = QComboBox()
        self.receipt_select_combo.setMinimumWidth(300)
        self.receipt_select_combo.currentTextChanged.connect(self.on_receipt_choice_select)
        header_layout.addWidget(self.receipt_select_combo, 1, 3)

        self.btn_new_invoice = QPushButton("New Invoice")
        self.btn_new_invoice.clicked.connect(self.create_new_invoice)
        self.btn_new_invoice.setEnabled(False)
        header_layout.addWidget(self.btn_new_invoice, 1, 4)

        header_layout.addWidget(QLabel("Invoice #"), 2, 0)
        self.invoice_number_edit = QLineEdit()
        header_layout.addWidget(self.invoice_number_edit, 2, 1)
        header_layout.addWidget(QLabel("Invoice Date"), 2, 2)
        self.invoice_date_edit = QLineEdit()
        header_layout.addWidget(self.invoice_date_edit, 2, 3)
        header_layout.addWidget(QLabel("Due Date"), 2, 4)
        self.due_date_edit = QLineEdit()
        header_layout.addWidget(self.due_date_edit, 2, 5)

        header_layout.addWidget(QLabel("Notes"), 3, 0)
        self.description_edit = QLineEdit()
        header_layout.addWidget(self.description_edit, 3, 1, 1, 5)
        right_layout.addWidget(header)

        self.detail_table = QTableWidget(0, 8)
        self.detail_table.setHorizontalHeaderLabels(
            ["Desc", "Est Price", "Delivered", "Invoiced", "Invoice Price", "Subtotal", "Backordered", "Match"]
        )
        self.detail_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.detail_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.detail_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setColumnWidth(0, 240)
        self.detail_table.setColumnWidth(1, 80)
        self.detail_table.setColumnWidth(2, 70)
        self.detail_table.setColumnWidth(3, 70)
        self.detail_table.setColumnWidth(4, 90)
        self.detail_table.setColumnWidth(5, 90)
        self.detail_table.setColumnWidth(6, 80)
        self.detail_table.setColumnWidth(7, 70)
        self.detail_table.cellDoubleClicked.connect(self.on_detail_double_click)
        right_layout.addWidget(self.detail_table, 1)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_total = QLabel("Total: $0.00")
        self.lbl_total.setStyleSheet("font-size: 11px; font-weight: 700;")
        footer_layout.addWidget(self.lbl_total)
        self.lbl_po_rollup = QLabel("PO Coverage: $0.00 of $0.00 | Remaining: $0.00")
        self.lbl_po_rollup.setStyleSheet("font-size: 11px; font-weight: 700;")
        footer_layout.addWidget(self.lbl_po_rollup)
        self.lbl_owed = QLabel("Currently Owed: $0.00")
        self.lbl_owed.setStyleSheet("font-size: 11px; font-weight: 700; color: #c0392b;")
        footer_layout.addWidget(self.lbl_owed)
        footer_layout.addStretch(1)
        right_layout.addWidget(footer)

        button_bar = QFrame()
        button_layout = QHBoxLayout(button_bar)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)
        self.btn_resend = QPushButton("Re-Send Report to Folder")
        self.btn_resend.clicked.connect(self.resend_report)
        self.btn_resend.setEnabled(False)
        button_layout.addWidget(self.btn_resend)
        self.btn_view = QPushButton("View Report")
        self.btn_view.clicked.connect(self.view_report)
        self.btn_view.setEnabled(False)
        button_layout.addWidget(self.btn_view)
        self.btn_lock = QPushButton("Lock Ready to Pay")
        self.btn_lock.clicked.connect(self.lock_invoice)
        self.btn_lock.setEnabled(False)
        button_layout.addWidget(self.btn_lock)
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save_invoice)
        self.btn_save.setEnabled(False)
        button_layout.addWidget(self.btn_save)
        self.btn_paid = QPushButton("Mark Paid")
        self.btn_paid.clicked.connect(self.mark_paid)
        self.btn_paid.setEnabled(False)
        button_layout.addWidget(self.btn_paid)
        right_layout.addWidget(button_bar)

    def refresh_data(self) -> None:
        selected_invoice_id = self.active_invoice_id
        self.pipeline_table.setRowCount(0)
        try:
            selected_row = None
            for invoice in get_vendor_invoice_pipeline():
                invoice_status = invoice.get("VendorInvoiceStatus") or "Draft"
                row = self.pipeline_table.rowCount()
                self.pipeline_table.insertRow(row)
                values = [
                    str(invoice["PurchaseOrderID"]),
                    str(invoice["VendorInvoiceID"]),
                    str(invoice.get("VendorInvoiceNumber") or f"Draft-{invoice['VendorInvoiceID']}"),
                    str(invoice.get("VendorName") or ""),
                    str(invoice.get("SiteName") or ""),
                    str(invoice.get("VendorInvoiceDate") or "N/A"),
                    str(invoice.get("POStatus") or "N/A"),
                    str(invoice_status),
                    f"${float(invoice.get('VendorInvoiceAmount') or 0):,.2f}",
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column in (2, 5, 6, 7):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if column == 8:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    if str(invoice.get("POStatus") or "") == "BackOrdered" and column >= 2:
                        item.setForeground(Qt.GlobalColor.red)
                    self.pipeline_table.setItem(row, column, item)
                if selected_invoice_id and int(invoice["VendorInvoiceID"]) == int(selected_invoice_id):
                    selected_row = row

            choices = get_received_purchase_order_choices()
            self.po_choice_map = {row["Label"]: row["PurchaseOrderID"] for row in choices}
            current_po_label = self.po_select_combo.currentText()
            self.po_select_combo.blockSignals(True)
            self.po_select_combo.clear()
            self.po_select_combo.addItems(self.po_choice_map.keys())
            if self.active_po_id:
                for label, po_id in self.po_choice_map.items():
                    if int(po_id) == int(self.active_po_id):
                        self.po_select_combo.setCurrentText(label)
                        break
            elif current_po_label in self.po_choice_map:
                self.po_select_combo.setCurrentText(current_po_label)
            self.po_select_combo.blockSignals(False)

            if selected_row is not None:
                self.pipeline_table.selectRow(selected_row)
                self.load_workspace()
            elif self.active_po_id:
                self.load_po_context()
            else:
                self.reset_workspace(clear_po=False)

            summary = get_vendor_payables_summary()
            self.lbl_owed.setText(
                f"Currently Owed: ${summary['total_owed']:,.2f} across {summary['invoice_count']} invoice(s)"
            )
        except Exception as exc:
            print(f"Vendor invoice pipeline error: {exc}")

    def reset_workspace(self, clear_po: bool = False) -> None:
        if clear_po:
            self.active_po_id = None
            self.po_select_combo.setCurrentIndex(-1)
        self.active_invoice_id = None
        self.current_rows = []
        self.report_path = None
        self.current_invoice_status = ""
        self.current_receipt_key = "all_received"
        self.current_receipt_label = "All received material on this PO"
        self.current_po_total = 0.0
        self.current_po_rollup_base = 0.0
        self.invoice_number_edit.setText("")
        self.invoice_date_edit.setText("")
        self.due_date_edit.setText("")
        self.description_edit.setText("")
        self.receipt_select_combo.blockSignals(True)
        self.receipt_select_combo.clear()
        self.receipt_select_combo.blockSignals(False)
        self.receipt_choice_map = {}
        self.detail_table.setRowCount(0)
        self.lbl_context.setText("Select a purchase order to start the bill.")
        self.lbl_total.setText("Total: $0.00")
        self.update_po_rollup_label(0.0)
        self.btn_new_invoice.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_lock.setEnabled(False)
        self.btn_view.setEnabled(False)
        self.btn_resend.setEnabled(False)
        self.btn_paid.setEnabled(False)

    def update_po_rollup_label(self, current_invoice_total: float = 0.0) -> None:
        live_total = float(self.current_po_rollup_base or 0) + float(current_invoice_total or 0)
        remaining = float(self.current_po_total or 0) - live_total
        self.lbl_po_rollup.setText(
            f"PO Coverage: ${live_total:,.2f} of ${float(self.current_po_total or 0):,.2f} | Remaining: ${remaining:,.2f}"
        )
        self.lbl_po_rollup.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #c0392b;" if remaining < -0.009 else "font-size: 11px; font-weight: 700;"
        )

    def load_po_context(self) -> None:
        if not self.active_po_id:
            self.reset_workspace(clear_po=False)
            return
        try:
            payload = get_vendor_invoice_creation_context(self.active_po_id)
            if not payload:
                self.reset_workspace(clear_po=False)
                return
            po = payload["po"]
            receipt_choices = payload.get("receipt_choices") or []
            self.receipt_choice_map = {row["Label"]: row["Key"] for row in receipt_choices}
            self.receipt_select_combo.blockSignals(True)
            self.receipt_select_combo.clear()
            self.receipt_select_combo.addItems(self.receipt_choice_map.keys())
            if receipt_choices:
                keys = [row["Key"] for row in receipt_choices]
                existing_key = self.current_receipt_key if self.current_receipt_key in keys else receipt_choices[0]["Key"]
                selected_choice = next((row for row in receipt_choices if row["Key"] == existing_key), receipt_choices[0])
                self.current_receipt_key = selected_choice["Key"]
                self.current_receipt_label = selected_choice["Label"]
                self.receipt_select_combo.setCurrentText(selected_choice["Label"])
            else:
                self.current_receipt_key = "all_received"
                self.current_receipt_label = "All received material on this PO"
            self.receipt_select_combo.blockSignals(False)

            rollup = payload.get("rollup") or {}
            self.current_po_total = float(rollup.get("po_total") or 0)
            self.current_po_rollup_base = float(rollup.get("invoiced_total") or 0)
            self.current_rows = []
            self.report_path = None
            self.current_invoice_status = ""
            self.invoice_number_edit.setText("")
            self.invoice_date_edit.setText("")
            self.due_date_edit.setText("")
            self.description_edit.setText("")
            self.detail_table.setRowCount(0)
            self.lbl_context.setText(
                f"PO #{po['PurchaseOrderID']} - {po.get('VendorName')} - {po.get('SiteName')} | "
                f"PO Status: {po.get('Status')} | Select receiving paperwork, then click New Invoice"
            )
            self.lbl_total.setText("Total: $0.00")
            self.update_po_rollup_label(0.0)
            self.btn_new_invoice.setEnabled(True)
            self.btn_save.setEnabled(False)
            self.btn_lock.setEnabled(False)
            self.btn_view.setEnabled(False)
            self.btn_resend.setEnabled(False)
            self.btn_paid.setEnabled(False)
        except Exception as exc:
            QMessageBox.critical(self, "PO Context", str(exc))

    def on_invoice_select(self) -> None:
        selection = self.pipeline_table.selectedItems()
        if not selection:
            return
        row = self.pipeline_table.row(selection[0])
        self.active_po_id = int(self.pipeline_table.item(row, 0).text())
        self.active_invoice_id = int(self.pipeline_table.item(row, 1).text())
        self.load_workspace()

    def on_po_choice_select(self, selected_label: str) -> None:
        po_id = self.po_choice_map.get(selected_label)
        if not po_id:
            return
        self.active_po_id = int(po_id)
        self.active_invoice_id = None
        self.load_po_context()

    def on_receipt_choice_select(self, selected_label: str) -> None:
        selected_key = self.receipt_choice_map.get(selected_label)
        if selected_key:
            self.current_receipt_key = selected_key
            self.current_receipt_label = selected_label

    def create_new_invoice(self) -> None:
        if not self.active_po_id:
            QMessageBox.warning(self, "Missing PO", "Select a purchase order first.")
            return
        selected_receipt = self.receipt_select_combo.currentText().strip()
        receipt_key = self.receipt_choice_map.get(selected_receipt)
        if not receipt_key:
            QMessageBox.warning(self, "Missing Paperwork", "Select the receiving paperwork for this new invoice first.")
            return
        try:
            invoice = create_vendor_invoice_draft(self.active_po_id, receipt_key=receipt_key)
            self.active_invoice_id = int(invoice["VendorInvoiceID"])
            self.load_workspace()
        except Exception as exc:
            QMessageBox.critical(self, "New Invoice", str(exc))

    def load_workspace(self) -> None:
        if not self.active_invoice_id:
            return
        try:
            payload = get_vendor_invoice_workspace_by_invoice_id(self.active_invoice_id)
            if not payload:
                return
            self.active_po_id = int(payload["po"]["PurchaseOrderID"])
            self.active_invoice_id = int(payload["invoice"]["VendorInvoiceID"])
            self.current_rows = payload["rows"]
            self.report_path = payload.get("report_path")
            self.current_receipt_key = payload.get("receipt_key") or "all_received"
            self.current_receipt_label = payload.get("receipt_label") or "All received material on this PO"
            invoice = payload["invoice"]
            po = payload["po"]
            self.current_invoice_status = str(invoice.get("VendorInvoiceStatus") or "")
            self.current_po_total = float((payload.get("rollup_excluding_current") or {}).get("po_total") or 0)
            self.current_po_rollup_base = float((payload.get("rollup_excluding_current") or {}).get("invoiced_total") or 0)

            receipt_choices = payload.get("receipt_choices") or []
            self.receipt_choice_map = {row["Label"]: row["Key"] for row in receipt_choices}
            self.receipt_select_combo.blockSignals(True)
            self.receipt_select_combo.clear()
            self.receipt_select_combo.addItems(self.receipt_choice_map.keys())
            selected_receipt_label = next(
                (row["Label"] for row in receipt_choices if row["Key"] == self.current_receipt_key),
                self.current_receipt_label,
            )
            self.receipt_select_combo.setCurrentText(selected_receipt_label)
            self.receipt_select_combo.blockSignals(False)
            self.current_receipt_label = selected_receipt_label

            self.lbl_context.setText(
                f"PO #{po['PurchaseOrderID']} - {po.get('VendorName')} - {po.get('SiteName')} | "
                f"PO Status: {po.get('Status')} | Invoice Status: {invoice.get('VendorInvoiceStatus')} | "
                f"Paperwork: {selected_receipt_label}"
            )
            for label, po_id in self.po_choice_map.items():
                if int(po_id) == int(po["PurchaseOrderID"]):
                    self.po_select_combo.blockSignals(True)
                    self.po_select_combo.setCurrentText(label)
                    self.po_select_combo.blockSignals(False)
                    break
            self.invoice_number_edit.setText(str(invoice.get("VendorInvoiceNumber") or ""))
            self.invoice_date_edit.setText(str(invoice.get("VendorInvoiceDate") or ""))
            self.due_date_edit.setText(str(invoice.get("VendorInvoiceDueDate") or ""))
            self.description_edit.setText(str(invoice.get("Description") or ""))

            self.render_rows()
            status = str(invoice.get("VendorInvoiceStatus") or "")
            locked = status in {"ReadyToPay", "Paid"}
            paid = status == "Paid"
            self.btn_new_invoice.setEnabled(True)
            self.btn_save.setEnabled(not locked)
            self.btn_lock.setEnabled(not locked)
            self.btn_view.setEnabled(True)
            self.btn_resend.setEnabled(True)
            self.btn_paid.setEnabled((not paid) and bool(self.active_invoice_id))
        except Exception as exc:
            QMessageBox.critical(self, "Invoice Workspace", str(exc))

    def render_rows(self) -> None:
        self.detail_table.setRowCount(0)
        total = 0.0
        for row_data in self.current_rows:
            subtotal = float(row_data.get("subtotal") or 0)
            total += subtotal
            row = self.detail_table.rowCount()
            self.detail_table.insertRow(row)
            values = [
                str(row_data.get("description") or ""),
                f"${float(row_data.get('estimate_unit_price') or 0):,.2f}",
                f"{float(row_data.get('delivered_qty') or 0):.2f}",
                f"{float(row_data.get('invoiced_qty') or 0):.2f}",
                f"${float(row_data.get('invoice_unit_price') or 0):,.2f}",
                f"${subtotal:,.2f}",
                f"{float(row_data.get('backordered_qty') or 0):.2f}",
                str(row_data.get("match_status") or "OK"),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (1, 2, 3, 4, 5, 6, 7):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignCenter if column in (2, 3, 6, 7) else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if row_data.get("match_status") != "OK":
                    item.setForeground(Qt.GlobalColor.red)
                self.detail_table.setItem(row, column, item)
        self.lbl_total.setText(f"Total: ${total:,.2f}")
        self.update_po_rollup_label(total)

    def _prompt_adjustment_note(self, kind: str) -> str | None:
        proceed = QMessageBox.question(
            self,
            "Adjustment Warning",
            f"Changing the {kind} will update project costs and the receiving record. Continue?",
        )
        if proceed != QMessageBox.StandardButton.Yes:
            return None
        note, accepted = QInputDialog.getText(
            self,
            "Mandatory Note",
            f"Enter the reason for changing the {kind}. This note will be written back to the estimate audit trail.",
        )
        if not accepted or not note.strip():
            QMessageBox.warning(self, "Note Required", "A note is required for this adjustment.")
            return None
        return note.strip()

    def on_detail_double_click(self, row: int, column: int) -> None:
        if not self.active_invoice_id:
            return
        if self.current_invoice_status in {"ReadyToPay", "Paid"}:
            QMessageBox.warning(self, "Locked", "This vendor invoice is locked and cannot be edited.")
            return
        if column not in (3, 4):
            return

        row_data = self.current_rows[row]
        field = "qty" if column == 3 else "price"
        current_value = float(row_data.get("invoiced_qty") or 0) if field == "qty" else float(row_data.get("invoice_unit_price") or 0)
        prompt = "invoice quantity" if field == "qty" else "invoice price"
        new_value, accepted = QInputDialog.getDouble(
            self,
            "Edit Line",
            f"Enter the new {prompt}:",
            current_value,
            0.0,
            100000000.0,
            2,
        )
        if not accepted:
            return

        note = self._prompt_adjustment_note(prompt)
        if not note:
            return

        try:
            apply_vendor_invoice_adjustment(
                self.active_invoice_id,
                int(row_data["po_item_id"]),
                field,
                new_value,
                note,
            )
            payload = get_vendor_invoice_workspace_by_invoice_id(self.active_invoice_id)
            self.current_rows = payload["rows"]
            self.report_path = payload.get("report_path")
            self.current_po_total = float((payload.get("rollup_excluding_current") or {}).get("po_total") or 0)
            self.current_po_rollup_base = float((payload.get("rollup_excluding_current") or {}).get("invoiced_total") or 0)
            self.render_rows()
        except Exception as exc:
            QMessageBox.critical(self, "Adjustment Error", str(exc))

    def save_invoice(self) -> None:
        if not self.active_po_id or not self.active_invoice_id:
            return
        if not self.invoice_number_edit.text().strip() or not self.invoice_date_edit.text().strip():
            QMessageBox.warning(self, "Missing Data", "Invoice number and invoice date are required.")
            return
        try:
            invoice = save_vendor_invoice_draft(
                self.active_po_id,
                self.active_invoice_id,
                self.invoice_number_edit.text().strip(),
                self.invoice_date_edit.text().strip(),
                self.due_date_edit.text().strip(),
                self.description_edit.text().strip(),
                self.current_rows,
                status="Draft",
                receipt_key=self.current_receipt_key,
                receipt_label=self.receipt_select_combo.currentText().strip() or self.current_receipt_label,
            )
            self.active_invoice_id = int(invoice["VendorInvoiceID"])
            self.report_path = generate_vendor_invoice_report(self.active_invoice_id, force=True)
            QMessageBox.information(self, "Saved", "Vendor invoice draft saved.")
            self.refresh_data()
            self.load_workspace()
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def view_report(self) -> None:
        if not self.active_invoice_id:
            return
        try:
            path = generate_vendor_invoice_report(self.active_invoice_id, force=False)
            self.report_path = path
            if os.path.exists(path):
                os.startfile(path)
        except Exception as exc:
            QMessageBox.critical(self, "Report Error", str(exc))

    def resend_report(self) -> None:
        if not self.active_invoice_id:
            return
        try:
            path = generate_vendor_invoice_report(self.active_invoice_id, force=True)
            self.report_path = path
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                os.startfile(folder)
        except Exception as exc:
            QMessageBox.critical(self, "Report Error", str(exc))

    def lock_invoice(self) -> None:
        if not self.active_invoice_id:
            return
        if not self.invoice_number_edit.text().strip() or not self.invoice_date_edit.text().strip():
            QMessageBox.warning(self, "Missing Data", "Invoice number and invoice date are required before locking.")
            return
        try:
            save_vendor_invoice_draft(
                self.active_po_id,
                self.active_invoice_id,
                self.invoice_number_edit.text().strip(),
                self.invoice_date_edit.text().strip(),
                self.due_date_edit.text().strip(),
                self.description_edit.text().strip(),
                self.current_rows,
                status="Draft",
                receipt_key=self.current_receipt_key,
                receipt_label=self.receipt_select_combo.currentText().strip() or self.current_receipt_label,
            )
            self.report_path = lock_vendor_invoice_ready_to_pay(self.active_invoice_id)
            QMessageBox.information(self, "Locked", "Vendor invoice locked and emailed as ready to pay.")
            self.refresh_data()
            self.load_workspace()
        except Exception as exc:
            QMessageBox.critical(self, "Lock Error", str(exc))

    def mark_paid(self) -> None:
        if not self.active_invoice_id:
            return
        confirmed = QMessageBox.question(
            self,
            "Confirm Payment",
            f"Mark vendor invoice #{self.active_invoice_id} as paid?",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            mark_vendor_invoice_paid(self.active_invoice_id)
            QMessageBox.information(self, "Paid", "Vendor invoice marked as paid.")
            self.refresh_data()
            self.load_workspace()
        except Exception as exc:
            QMessageBox.critical(self, "Payment Error", str(exc))
