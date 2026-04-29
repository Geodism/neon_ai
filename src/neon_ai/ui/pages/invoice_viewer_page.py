from __future__ import annotations

import datetime
import json

from psycopg2.extras import Json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.connection import get_connection
from neon_ai.database.invoices import get_accounts_receivable, get_invoice_detail, mark_invoice_sent


class InvoiceViewerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_inv_id: int | None = None
        self.current_detail: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Accounts Receivable Tracking")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_pipeline_panel())
        splitter.addWidget(self._build_tracking_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    def _build_pipeline_panel(self) -> QGroupBox:
        group = QGroupBox("A/R Pipeline")
        layout = QVBoxLayout(group)

        self.ar_total_label = QLabel("Total Receivables: $0.00")
        self.ar_total_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #d35400;")
        layout.addWidget(self.ar_total_label)

        self.ar_table = QTableWidget(0, 5)
        self.ar_table.setHorizontalHeaderLabels(["Inv #", "Customer", "Due Date", "Amount", "Status"])
        self.ar_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ar_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.ar_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ar_table.verticalHeader().setVisible(False)
        self.ar_table.setColumnWidth(0, 60)
        self.ar_table.setColumnWidth(2, 100)
        self.ar_table.setColumnWidth(3, 90)
        self.ar_table.setColumnWidth(4, 90)
        self.ar_table.itemSelectionChanged.connect(self._on_inv_select)
        layout.addWidget(self.ar_table, 1)
        return group

    def _build_tracking_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        info_group = QGroupBox("Invoice Details")
        info_layout = QVBoxLayout(info_group)
        self.detail_label = QLabel("Select an invoice from the pipeline.")
        self.detail_label.setWordWrap(True)
        info_layout.addWidget(self.detail_label)
        layout.addWidget(info_group)

        button_row = QFrame()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        paid_button = QPushButton("Mark as PAID")
        paid_button.clicked.connect(self._on_mark_paid)
        button_layout.addWidget(paid_button)
        resend_button = QPushButton("Resend via Gateway")
        resend_button.clicked.connect(self._on_resend_email)
        button_layout.addWidget(resend_button)
        button_layout.addStretch(1)
        layout.addWidget(button_row)

        history_group = QGroupBox("Invoice History")
        history_layout = QVBoxLayout(history_group)
        self.history_log = QPlainTextEdit()
        self.history_log.setReadOnly(True)
        history_layout.addWidget(self.history_log)
        layout.addWidget(history_group, 1)

        note_group = QGroupBox("Add Audit Note")
        note_layout = QVBoxLayout(note_group)
        self.audit_note_text = QPlainTextEdit()
        self.audit_note_text.setFixedHeight(90)
        note_layout.addWidget(self.audit_note_text)
        add_note_button = QPushButton("Post Note to History")
        add_note_button.clicked.connect(self._on_add_note)
        note_layout.addWidget(add_note_button)
        layout.addWidget(note_group)
        return panel

    def refresh_data(self) -> None:
        self.current_inv_id = None
        self.current_detail = None
        self.ar_table.setRowCount(0)
        total_ar = 0.0
        try:
            invoices = get_accounts_receivable()
        except Exception as exc:
            QMessageBox.critical(self, "A/R Refresh Error", str(exc))
            return

        for inv in invoices:
            status = inv.get("InvoiceStatus") or "Exported"
            amt = float(inv.get("CustomerInvoiceAmount") or 0)
            if status not in ["Paid", "Draft"]:
                total_ar += amt

            due_date_str = "Unknown"
            overdue = False
            date_str = inv.get("CustomerInvoiceDate")
            if date_str:
                try:
                    created = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    due = created + datetime.timedelta(days=30)
                    due_date_str = due.strftime("%Y-%m-%d")
                    overdue = due < datetime.date.today() and status not in ["Paid", "Draft"]
                except ValueError:
                    pass

            row = self.ar_table.rowCount()
            self.ar_table.insertRow(row)
            values = [
                str(inv["CustomerInvoiceId"]),
                str(inv.get("CustomerName") or ""),
                due_date_str,
                f"${amt:,.2f}",
                str(status),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if overdue:
                    item.setForeground(Qt.GlobalColor.red)
                self.ar_table.setItem(row, column, item)

        self.ar_total_label.setText(f"Total Receivables: ${total_ar:,.2f}")
        self.detail_label.setText("Select an invoice from the pipeline.")
        self.history_log.setPlainText("")

    def _on_inv_select(self) -> None:
        selection = self.ar_table.selectedItems()
        if not selection:
            return
        self.current_inv_id = int(self.ar_table.item(self.ar_table.row(selection[0]), 0).text())
        self.current_detail = get_invoice_detail(self.current_inv_id)
        if not self.current_detail:
            self.detail_label.setText("Could not load invoice detail.")
            return

        invoice = self.current_detail["invoice"]
        header = self.current_detail["header"] or {}
        memory = self.current_detail["memory"] or {}
        detail_lines = [
            f"Invoice #{invoice.get('CustomerInvoiceId')} | Customer: {header.get('CustomerName') or 'Unknown'}",
            f"Work Order: {header.get('WorkOrderID') or invoice.get('WorkOrderID') or 'Unknown'}",
            f"Date: {invoice.get('CustomerInvoiceDate') or 'Unknown'}",
            f"Amount: ${float(invoice.get('CustomerInvoiceAmount') or 0):,.2f}",
            f"Status: {invoice.get('InvoiceStatus') or 'Exported'}",
            f"Email: {header.get('CustomerEmail') or 'Not on file'}",
            f"Document: {invoice.get('CustomerInvoiceDocPath') or 'Not generated'}",
        ]
        if memory.get("review_summary"):
            detail_lines.append("")
            detail_lines.append(str(memory.get("review_summary")))
        self.detail_label.setText("\n".join(detail_lines))
        self._render_history(memory)

    def _render_history(self, memory: dict) -> None:
        history = memory.get("audit_history") or []
        lines = []
        for item in history:
            timestamp = item.get("timestamp") or ""
            note = item.get("note") or ""
            lines.append(f"[{timestamp}] {note}")
        self.history_log.setPlainText("\n\n".join(lines))

    def _on_mark_paid(self) -> None:
        if not self.current_inv_id:
            return
        confirmed = QMessageBox.question(self, "Confirm", f"Mark Invoice #{self.current_inv_id} as Paid?")
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                'UPDATE "Invoice" SET "InvoiceStatus" = \'Paid\', "InvDatePaid" = CURRENT_DATE::text WHERE "CustomerInvoiceId" = %s',
                (self.current_inv_id,),
            )
            conn.commit()
            QMessageBox.information(self, "Success", "Invoice marked as paid.")
            self.refresh_data()
        finally:
            conn.close()

    def _on_add_note(self) -> None:
        if not self.current_inv_id:
            return
        note = self.audit_note_text.toPlainText().strip()
        if not note:
            return

        detail = self.current_detail or get_invoice_detail(self.current_inv_id) or {}
        memory = dict(detail.get("memory") or {})
        history = list(memory.get("audit_history") or [])
        history.append(
            {
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "note": note,
            }
        )
        memory["audit_history"] = history

        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                'UPDATE "Invoice" SET "CustomerInvoiceMemory" = %s WHERE "CustomerInvoiceId" = %s',
                (Json(memory), self.current_inv_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.audit_note_text.setPlainText("")
        self.current_detail = get_invoice_detail(self.current_inv_id)
        self._render_history(self.current_detail.get("memory") or {})
        QMessageBox.information(self, "Saved", f"Note saved for Invoice #{self.current_inv_id}.")

    def _on_resend_email(self) -> None:
        if not self.current_inv_id:
            return
        try:
            from neon_ai.gateway import send_to_user

            detail = get_invoice_detail(self.current_inv_id)
            header = detail["header"] or {}
            recipient = header.get("CustomerEmail")
            doc_path = detail["invoice"].get("CustomerInvoiceDocPath")
            if not recipient:
                raise ValueError("This customer does not have an email on file yet.")
            if not doc_path:
                raise ValueError("This invoice does not have a generated document path yet.")

            sent = send_to_user(
                subject=f"Invoice #{self.current_inv_id} - Work Order #{header.get('WorkOrderID')}",
                content=(
                    f"Hello {header.get('CustomerName')},\n\n"
                    f"Please find attached invoice #{self.current_inv_id}.\n\n"
                    "Thank you,\nArgon Electrical"
                ),
                recipient=recipient,
                attachment_path=doc_path,
            )
            if not sent:
                raise RuntimeError("The email gateway did not confirm the resend.")
            mark_invoice_sent(self.current_inv_id, doc_path=doc_path)
            QMessageBox.information(self, "Sent", f"Invoice #{self.current_inv_id} resent to {recipient}.")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Gateway", str(exc))
