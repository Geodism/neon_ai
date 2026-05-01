from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
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
)
from neon_ai.services.purchase_order_send_service import (
    prepare_purchase_order_delivery_message,
    send_purchase_order,
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
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_data)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh_button)
        layout.addLayout(header)

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
            preview = prepare_purchase_order_delivery_message(int(self.active_po_id))
            if not preview.get("success"):
                QMessageBox.warning(
                    self,
                    "PO Send Unavailable",
                    str(preview.get("reason") or "Purchase order send preview could not be prepared."),
                )
                return
            confirmation = self._show_po_send_confirmation_dialog(preview)
            if not confirmation:
                return

            result = send_purchase_order(
                int(self.active_po_id),
                sent_by="UI",
                override_recipient=confirmation.get("override_recipient"),
            )
            if not result.get("success"):
                delivery_confirmed = bool(result.get("DeliveryConfirmed"))
                failure_message = str(result.get("reason") or "Purchase order send failed.")
                if delivery_confirmed:
                    failure_message += (
                        "\n\nThe email gateway appears to have accepted the send, but PO status updates did not complete."
                        "\nReview the outbound log before retrying to avoid a duplicate delivery."
                    )
                message_box = QMessageBox(self)
                message_box.setWindowTitle("PO Send Failed")
                message_box.setText(failure_message)
                message_box.setIcon(
                    QMessageBox.Icon.Warning if delivery_confirmed else QMessageBox.Icon.Critical
                )
                message_box.exec()
                return

            self.refresh_data()
            QMessageBox.information(
                self,
                "PO Sent",
                f"PO #{self.active_po_id} sent to {result.get('ResolvedRecipientEmail') or result.get('VendorEmail')}.\n\n"
                f"Attachment:\n{result.get('AttachmentPath') or result.get('PDFPath') or result.get('DOCXPath') or 'Generated during send'}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def _show_po_send_confirmation_dialog(self, preview: dict) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Purchase Order Send")
        dialog.resize(760, 680)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        warning = QLabel(
            "This will send the purchase order attachment to the vendor.\n"
            "It will mark the purchase order as Sent only after successful delivery."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(warning)

        meta = QLabel(
            f"PO ID: {preview.get('PurchaseOrderID') or 'Unknown'}\n"
            f"Work Order ID: {preview.get('WorkOrderID') or 'Unknown'}\n"
            f"Vendor: {preview.get('VendorName') or 'Unknown Vendor'}"
        )
        meta.setWordWrap(True)
        layout.addWidget(meta)

        to_label = QLabel("To")
        layout.addWidget(to_label)
        to_field = QLineEdit(str(preview.get("VendorEmail") or ""))
        to_field.setReadOnly(True)
        layout.addWidget(to_field)

        override_label = QLabel("Test Recipient Override (optional)")
        layout.addWidget(override_label)
        override_field = QLineEdit("")
        override_field.setPlaceholderText("Leave blank to send to the vendor email above.")
        layout.addWidget(override_field)

        original_label = QLabel("Original Vendor Email")
        layout.addWidget(original_label)
        original_field = QLineEdit(str(preview.get("VendorEmail") or ""))
        original_field.setReadOnly(True)
        layout.addWidget(original_field)

        subject_label = QLabel("Subject")
        layout.addWidget(subject_label)
        subject_field = QLineEdit(str(preview.get("Subject") or ""))
        subject_field.setReadOnly(True)
        layout.addWidget(subject_field)

        attachment_label = QLabel("Attachment Path")
        layout.addWidget(attachment_label)
        attachment_field = QLineEdit(str(preview.get("AttachmentPath") or ""))
        attachment_field.setReadOnly(True)
        layout.addWidget(attachment_field)

        body_label = QLabel("Body")
        layout.addWidget(body_label)
        body_field = QPlainTextEdit()
        body_field.setReadOnly(True)
        body_field.setPlainText(str(preview.get("Body") or ""))
        layout.addWidget(body_field, 1)

        template_label = QLabel(f"Template used: {preview.get('TemplateUsed') or 'fallback'}")
        template_label.setWordWrap(True)
        layout.addWidget(template_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        send_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if send_button:
            send_button.setText("Send Purchase Order")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return {
            "override_recipient": str(override_field.text() or "").strip() or None,
        }

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
