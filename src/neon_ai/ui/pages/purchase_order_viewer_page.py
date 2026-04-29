from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.purchases import (
    archive_purchase_order_docx,
    get_po_export_data,
    get_po_followup_status,
    get_po_items,
    get_purchase_orders,
    send_purchase_order_now,
)


class PurchaseOrderViewerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_po_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("Purchase Order Ledger")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_table_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    def _build_table_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["PO #", "Date", "Vendor", "Work Order #", "Total Amount", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 110)
        self.table.itemSelectionChanged.connect(self._on_po_select)
        layout.addWidget(self.table)
        return panel

    def _build_detail_panel(self) -> QGroupBox:
        group = QGroupBox("ETA / Follow-Up")
        layout = QVBoxLayout(group)

        self.followup_labels: dict[str, QLabel] = {}
        rows = [
            ("PO Status", "po_status"),
            ("ETA", "eta"),
            ("Next Follow-Up", "next_followup"),
            ("Weekly Follow-Up", "weekly_active"),
            ("Vendor Email", "recipient_email"),
            ("Ready For Pickup", "ready_for_pickup"),
            ("Last Weekly Email", "last_weekly"),
            ("ETA Reminder Sent", "eta_reminder"),
        ]
        for label_text, key in rows:
            row = QFrame()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            title = QLabel(f"{label_text}:")
            title.setMinimumWidth(120)
            row_layout.addWidget(title)
            value = QLabel("Select a PO")
            value.setWordWrap(True)
            row_layout.addWidget(value, 1)
            self.followup_labels[key] = value
            layout.addWidget(row)

        action_row = QFrame()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        review_button = QPushButton("Review PO")
        review_button.clicked.connect(self._on_review_po)
        action_layout.addWidget(review_button)
        docx_button = QPushButton("Generate DOCX")
        docx_button.clicked.connect(self._on_generate_docx)
        action_layout.addWidget(docx_button)
        send_button = QPushButton("Send to Vendor")
        send_button.clicked.connect(self._on_send_po)
        action_layout.addWidget(send_button)
        action_layout.addStretch(1)
        layout.addWidget(action_row)
        layout.addStretch(1)
        return group

    def _on_po_select(self) -> None:
        selection = self.table.selectedItems()
        if not selection:
            return
        self.active_po_id = int(self.table.item(self.table.row(selection[0]), 0).text())
        self._refresh_followup_panel()

    def _refresh_followup_panel(self) -> None:
        if not self.active_po_id:
            return
        try:
            snapshot = get_po_followup_status(self.active_po_id)
        except Exception as exc:
            QMessageBox.critical(self, "PO follow-up panel error", str(exc))
            return

        self.followup_labels["po_status"].setText(str(snapshot.get("po_status") or "N/A"))
        eta_text = snapshot.get("eta_date") or snapshot.get("eta_text") or "No ETA logged"
        self.followup_labels["eta"].setText(str(eta_text))
        self.followup_labels["next_followup"].setText(str(snapshot.get("next_followup") or "None"))
        self.followup_labels["weekly_active"].setText("Yes" if snapshot.get("weekly_followup_active") else "No")
        self.followup_labels["recipient_email"].setText(str(snapshot.get("recipient_email") or "Unknown"))
        self.followup_labels["ready_for_pickup"].setText(str(snapshot.get("ready_for_pickup_at") or "No"))
        self.followup_labels["last_weekly"].setText(str(snapshot.get("last_weekly_followup_sent_at") or "Not sent"))
        self.followup_labels["eta_reminder"].setText(str(snapshot.get("eta_followup_sent_at") or "Not sent"))

    def _on_review_po(self) -> None:
        if not self.active_po_id:
            QMessageBox.warning(self, "Select PO", "Select a purchase order first.")
            return
        try:
            header = get_po_export_data(self.active_po_id)
            items = get_po_items(self.active_po_id)
        except Exception as exc:
            QMessageBox.critical(self, "Review Error", str(exc))
            return

        summary_lines = [
            f"PO #{self.active_po_id}",
            f"Vendor: {header.get('VendorName') or 'N/A'}",
            f"Site: {header.get('SiteName') or 'N/A'}",
            f"Status: {header.get('Status') or 'N/A'}",
            "",
        ]
        for item in items:
            qty = float(item.get("QuantityOrdered") or 0)
            price = float(item.get("UnitPriceAtOrder") or 0)
            summary_lines.append(f"{qty:.2f} x {item.get('Description') or ''} @ ${price:,.2f}")
        QMessageBox.information(self, "PO Review", "\n".join(summary_lines[:25]))

    def _on_generate_docx(self) -> None:
        if not self.active_po_id:
            QMessageBox.warning(self, "Select PO", "Select a purchase order first.")
            return
        try:
            path = archive_purchase_order_docx(self.active_po_id)
            self._refresh_followup_panel()
            folder = os.path.dirname(path)
            try:
                os.startfile(folder)
            except Exception:
                pass
            QMessageBox.information(self, "DOCX Ready", f"PO #{self.active_po_id} was saved to:\n{path}\n\nFolder:\n{folder}")
        except Exception as exc:
            QMessageBox.critical(self, "DOCX Error", str(exc))

    def _on_send_po(self) -> None:
        if not self.active_po_id:
            QMessageBox.warning(self, "Select PO", "Select a purchase order first.")
            return
        try:
            result = send_purchase_order_now(self.active_po_id)
            self.refresh_data()
            QMessageBox.information(
                self,
                "PO Sent",
                f"PO #{self.active_po_id} sent to {result['recipient_email']}.\n\n"
                f"Attachment:\n{result.get('attachment_path') or result.get('pdf_path') or result.get('docx_path') or 'Generated during send'}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def refresh_data(self) -> None:
        selected_po_id = self.active_po_id
        self.table.setRowCount(0)
        try:
            rows = get_purchase_orders()
        except Exception as exc:
            QMessageBox.critical(self, "Ledger Error", str(exc))
            return

        for po in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                str(po["PurchaseOrderID"]),
                str(po.get("Date") or "N/A"),
                str(po.get("VendorName") or ""),
                str(po.get("WorkOrderID") or "N/A"),
                f"${float(po.get('PurchaseOrderTotal') or 0):,.2f}",
                str(po.get("Status") or "Open"),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 1, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column == 5 and value.strip() == "BackOrdered":
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, column, item)

        if selected_po_id:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == str(selected_po_id):
                    self.table.selectRow(row)
                    self._on_po_select()
                    break
        else:
            self._refresh_followup_panel()
