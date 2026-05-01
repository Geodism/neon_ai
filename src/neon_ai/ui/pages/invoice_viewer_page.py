from __future__ import annotations

import datetime
import json

from psycopg2.extras import Json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.connection import get_connection
from neon_ai.database.invoices import get_accounts_receivable, get_invoice_detail, mark_invoice_paid, mark_invoice_sent
from neon_ai.database.customer_invoice_document_drafts import get_active_customer_invoice_document_draft
from neon_ai.services.customer_invoice_document_send_service import (
    can_send_customer_invoice_document_draft,
    prepare_customer_invoice_delivery_message,
    send_customer_invoice_document_draft,
)
from neon_ai.services.outbound_message_log_service import (
    ensure_outbound_message_log_table,
    record_failed_message,
    record_prepared_message,
    record_sent_message,
)


class InvoiceViewerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_inv_id: int | None = None
        self.current_detail: dict | None = None
        self.current_active_draft: dict | None = None

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
        self.resend_button = QPushButton("Legacy Resend from CustomerInvoiceDocPath")
        self.resend_button.clicked.connect(self._on_resend_email)
        button_layout.addWidget(self.resend_button)
        button_layout.addStretch(1)
        layout.addWidget(button_row)

        self.send_path_hint_label = QLabel(
            "Historical invoices can still resend from CustomerInvoiceDocPath. Modern invoice draft delivery should be preferred when a locked/exported draft is available."
        )
        self.send_path_hint_label.setWordWrap(True)
        self.send_path_hint_label.setStyleSheet("color: #7a5c00;")
        layout.addWidget(self.send_path_hint_label)

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
        self.current_active_draft = None
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
        self._refresh_send_path_presentation()

    def _current_invoice_send_state(self) -> dict:
        detail = self.current_detail or {}
        invoice = detail.get("invoice") or {}
        header = detail.get("header") or {}
        active_draft = self.current_active_draft
        draft_id = active_draft.get("CustomerInvoiceDocumentDraftID") if active_draft else None
        try:
            normalized_draft_id = int(draft_id) if draft_id is not None else None
        except (TypeError, ValueError):
            normalized_draft_id = None

        modern_can_send = False
        modern_reason = ""
        if normalized_draft_id is not None:
            modern_can_send, modern_reason, _context = can_send_customer_invoice_document_draft(normalized_draft_id)

        legacy_doc_path = str(invoice.get("CustomerInvoiceDocPath") or header.get("CustomerInvoiceDocPath") or "").strip()
        return {
            "invoice": invoice,
            "header": header,
            "active_draft": active_draft,
            "active_draft_id": normalized_draft_id,
            "active_draft_status": str(active_draft.get("DraftStatus") or "").strip() if active_draft else "",
            "active_draft_final_file_path": str(active_draft.get("FinalFilePath") or "").strip() if active_draft else "",
            "modern_can_send": modern_can_send,
            "modern_reason": modern_reason,
            "legacy_doc_path": legacy_doc_path,
        }

    def _refresh_send_path_presentation(self) -> None:
        state = self._current_invoice_send_state()
        self.resend_button.setText("Legacy Resend from CustomerInvoiceDocPath")
        if state["modern_can_send"]:
            self.send_path_hint_label.setText(
                "A locked/exported invoice document draft is send-ready. Prefer the guarded draft send path. "
                "Legacy resend remains available, but it will use CustomerInvoiceDocPath."
            )
            self.resend_button.setToolTip(
                "A send-ready invoice draft exists. Legacy resend will bypass the newer draft controls."
            )
        elif state["active_draft"]:
            self.send_path_hint_label.setText(
                "A modern invoice document draft record exists for this invoice. "
                "Legacy resend remains available for historical compatibility, but it will use CustomerInvoiceDocPath."
            )
            self.resend_button.setToolTip(
                "This resend action uses CustomerInvoiceDocPath instead of the invoice document draft artifact."
            )
        else:
            self.send_path_hint_label.setText(
                "This invoice has no modern invoice document draft record available here. "
                "Historical resend will use CustomerInvoiceDocPath."
            )
            self.resend_button.setToolTip(
                "Historical resend uses CustomerInvoiceDocPath."
            )

    def _show_modern_draft_send_confirmation_dialog(self, preview: dict) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Invoice Draft Send")
        dialog.resize(780, 700)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "This will send the locked/exported invoice draft attachment.\n"
            "It will mark the invoice draft as Sent if successful.\n"
            "It will update invoice sent state only after success."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(intro)

        meta = QLabel(
            f"Customer: {str(preview.get('CustomerName') or 'Unknown Customer')}\n"
            f"Invoice ID: {preview.get('CustomerInvoiceId') or preview.get('InvoiceID') or 'Unknown'}\n"
            f"Draft ID: {preview.get('CustomerInvoiceDocumentDraftID') or 'Unknown'}"
        )
        meta.setWordWrap(True)
        layout.addWidget(meta)

        layout.addWidget(QLabel("To"))
        to_field = QLineEdit(str(preview.get("CustomerEmail") or ""))
        to_field.setReadOnly(True)
        layout.addWidget(to_field)

        layout.addWidget(QLabel("Test Recipient Override (optional)"))
        override_field = QLineEdit("")
        override_field.setPlaceholderText("test@example.com")
        layout.addWidget(override_field)

        layout.addWidget(QLabel("Subject"))
        subject_field = QLineEdit(str(preview.get("Subject") or ""))
        subject_field.setReadOnly(True)
        layout.addWidget(subject_field)

        layout.addWidget(QLabel("Body"))
        body_viewer = QTextEdit(dialog)
        body_viewer.setReadOnly(True)
        body_viewer.setPlainText(str(preview.get("Body") or ""))
        body_viewer.setMinimumHeight(220)
        layout.addWidget(body_viewer, 1)

        template_used = str(preview.get("TemplateUsed") or "fallback")
        template_version = preview.get("TemplateVersionID")
        template_summary = f"{template_used} / version {template_version}" if template_version else template_used
        attachment_label = QLabel(
            f"Attachment Path:\n{str(preview.get('FinalFilePath') or preview.get('AttachmentPath') or 'Unknown')}\n\n"
            f"Template used: {template_summary}"
        )
        attachment_label.setWordWrap(True)
        attachment_label.setStyleSheet("color: #444;")
        layout.addWidget(attachment_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_button = QPushButton("Cancel")
        send_button = QPushButton("Send Invoice Draft")
        send_button.setStyleSheet("font-weight: 700; padding: 6px 14px;")
        footer.addWidget(cancel_button)
        footer.addWidget(send_button)
        layout.addLayout(footer)

        result: dict | None = None

        def _cancel() -> None:
            dialog.reject()

        def _send() -> None:
            nonlocal result
            result = {
                "override_recipient": str(override_field.text() or "").strip() or None,
            }
            dialog.accept()

        cancel_button.clicked.connect(_cancel)
        send_button.clicked.connect(_send)
        dialog.exec()
        return result

    def _run_modern_invoice_draft_send(self, draft_id: int) -> bool:
        preview = prepare_customer_invoice_delivery_message(draft_id)
        if not preview.get("success"):
            QMessageBox.warning(
                self,
                "Invoice Draft Send Preview Unavailable",
                str(preview.get("reason") or "Invoice draft send preview could not be prepared."),
            )
            return False

        confirmation = self._show_modern_draft_send_confirmation_dialog(preview)
        if not confirmation:
            return False

        send_result = send_customer_invoice_document_draft(
            draft_id,
            sent_by="InvoiceViewerPage",
            override_recipient=confirmation.get("override_recipient"),
        )
        if not send_result.get("success"):
            delivery_confirmed = bool(send_result.get("DeliveryConfirmed"))
            failure_message = str(send_result.get("reason") or "Invoice draft send failed.")
            if delivery_confirmed:
                failure_message += (
                    "\n\nThe email gateway appears to have accepted the send, but draft or invoice status updates did not complete."
                    "\nReview the outbound log before retrying to avoid a duplicate delivery."
                )
            message_box = QMessageBox(self)
            message_box.setWindowTitle("Invoice Draft Send Failed")
            message_box.setText(failure_message)
            message_box.setIcon(QMessageBox.Icon.Warning if delivery_confirmed else QMessageBox.Icon.Critical)
            message_box.exec()
            return False

        resolved_recipient = str(send_result.get("ResolvedRecipientEmail") or preview.get("CustomerEmail") or "").strip()
        QMessageBox.information(
            self,
            "Invoice Draft Sent",
            "The locked/exported invoice draft was sent successfully."
            f"\n\nRecipient: {resolved_recipient}"
            f"\nAttachment: {send_result.get('FinalFilePath') or send_result.get('AttachmentPath') or 'Unknown'}",
        )
        self.refresh_data()
        return True

    def _confirm_legacy_resend_action(self, state: dict) -> str | None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Legacy Invoice Resend")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(
            "Legacy resend uses Invoice.CustomerInvoiceDocPath and bypasses some newer draft controls."
        )
        informative_lines = []
        if state.get("legacy_doc_path"):
            informative_lines.append(f"Legacy attachment path:\n{state['legacy_doc_path']}")
        if state.get("active_draft"):
            informative_lines.append(
                "Modern draft record: "
                f"Draft #{state.get('active_draft_id') or 'Unknown'} | "
                f"Status: {state.get('active_draft_status') or 'Unknown'}"
            )
            draft_file_path = str(state.get("active_draft_final_file_path") or "").strip()
            if draft_file_path:
                informative_lines.append(f"Modern draft artifact:\n{draft_file_path}")
        if state.get("modern_can_send"):
            informative_lines.append(
                "A locked/exported invoice document draft is send-ready. Use the guarded draft send path unless you specifically need the legacy resend."
            )
        elif state.get("active_draft"):
            informative_lines.append(
                "A modern invoice document draft record exists, but it is not send-ready. Legacy resend will continue from CustomerInvoiceDocPath."
            )
        else:
            informative_lines.append(
                "This invoice appears to rely on the historical CustomerInvoiceDocPath resend path."
            )
        dialog.setInformativeText("\n\n".join(informative_lines))

        use_modern_button = None
        if state.get("modern_can_send"):
            use_modern_button = dialog.addButton("Use Draft Send Instead", QMessageBox.ButtonRole.AcceptRole)
        continue_button = dialog.addButton("Continue Legacy Resend", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked == cancel_button:
            return None
        if use_modern_button is not None and clicked == use_modern_button:
            return "modern"
        if clicked == continue_button:
            return "legacy"
        return None

    def _on_inv_select(self) -> None:
        selection = self.ar_table.selectedItems()
        if not selection:
            return
        self.current_inv_id = int(self.ar_table.item(self.ar_table.row(selection[0]), 0).text())
        self.current_detail = get_invoice_detail(self.current_inv_id)
        self.current_active_draft = get_active_customer_invoice_document_draft(self.current_inv_id)
        if not self.current_detail:
            self.detail_label.setText("Could not load invoice detail.")
            return

        invoice = self.current_detail["invoice"]
        header = self.current_detail["header"] or {}
        memory = self.current_detail["memory"] or {}
        draft = self.current_active_draft or {}
        draft_id = draft.get("CustomerInvoiceDocumentDraftID")
        draft_status = str(draft.get("DraftStatus") or "").strip()
        draft_final_path = str(draft.get("FinalFilePath") or "").strip()
        detail_lines = [
            f"Invoice #{invoice.get('CustomerInvoiceId')} | Customer: {header.get('CustomerName') or 'Unknown'}",
            f"Work Order: {header.get('WorkOrderID') or invoice.get('WorkOrderID') or 'Unknown'}",
            f"Date: {invoice.get('CustomerInvoiceDate') or 'Unknown'}",
            f"Amount: ${float(invoice.get('CustomerInvoiceAmount') or 0):,.2f}",
            f"Status: {invoice.get('InvoiceStatus') or 'Exported'}",
            f"Email: {header.get('CustomerEmail') or 'Not on file'}",
            f"Legacy Document Path: {invoice.get('CustomerInvoiceDocPath') or 'Not generated'}",
        ]
        if draft_id:
            detail_lines.append(
                f"Modern Draft Record: Draft #{draft_id} | Status: {draft_status or 'Unknown'}"
            )
            detail_lines.append(
                f"Modern Draft Artifact: {draft_final_path or 'No exported draft artifact recorded'}"
            )
        else:
            detail_lines.append("Modern Draft Record: None")
        if memory.get("review_summary"):
            detail_lines.append("")
            detail_lines.append(str(memory.get("review_summary")))
        self.detail_label.setText("\n".join(detail_lines))
        self._render_history(memory)
        self._refresh_send_path_presentation()

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
        try:
            mark_invoice_paid(self.current_inv_id)
            QMessageBox.information(self, "Success", "Invoice marked as paid.")
            self.refresh_data()
        except ValueError as exc:
            QMessageBox.warning(self, "Payment Blocked", str(exc))

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
        delivery_confirmed = False
        try:
            from neon_ai.gateway import send_to_user

            detail = get_invoice_detail(self.current_inv_id)
            self.current_detail = detail
            self.current_active_draft = get_active_customer_invoice_document_draft(self.current_inv_id)
            header = detail["header"] or {}
            state = self._current_invoice_send_state()
            guard_result = self._confirm_legacy_resend_action(state)
            if guard_result == "modern":
                draft_id = state.get("active_draft_id")
                if draft_id is None:
                    QMessageBox.information(
                        self,
                        "Draft Send Unavailable",
                        "A modern invoice draft was expected, but it could not be loaded.",
                    )
                    return
                self._run_modern_invoice_draft_send(int(draft_id))
                return
            if guard_result != "legacy":
                return

            recipient = header.get("CustomerEmail")
            doc_path = state.get("legacy_doc_path")
            if not recipient:
                raise ValueError("This customer does not have an email on file yet.")
            if not doc_path:
                raise ValueError("This invoice does not have a generated document path yet.")
            subject = f"Invoice #{self.current_inv_id} - Work Order #{header.get('WorkOrderID')}"
            body = (
                f"Hello {header.get('CustomerName')},\n\n"
                f"Please find attached invoice #{self.current_inv_id}.\n\n"
                "Thank you,\nArgon Electrical"
            )

            ensure_outbound_message_log_table()
            record_prepared_message(
                entity_type="CustomerInvoice",
                entity_id=int(self.current_inv_id),
                related_draft_type="LegacyCustomerInvoiceDocPath",
                related_draft_id=None,
                template_code="CustomerInvoiceLegacyDocPath",
                template_version_id=None,
                recipient_email=recipient,
                original_recipient_email=recipient,
                subject=subject,
                body=body,
                attachment_path=doc_path,
                created_by="InvoiceViewerPageLegacy",
            )
            sent = send_to_user(
                subject=subject,
                content=body,
                recipient=recipient,
                attachment_path=doc_path,
            )
            if not sent:
                record_failed_message(
                    entity_type="CustomerInvoice",
                    entity_id=int(self.current_inv_id),
                    related_draft_type="LegacyCustomerInvoiceDocPath",
                    related_draft_id=None,
                    template_code="CustomerInvoiceLegacyDocPath",
                    template_version_id=None,
                    recipient_email=recipient,
                    original_recipient_email=recipient,
                    subject=subject,
                    body=body,
                    attachment_path=doc_path,
                    error_message="The email gateway did not confirm the legacy resend.",
                    created_by="InvoiceViewerPageLegacy",
                )
                raise RuntimeError("The email gateway did not confirm the resend.")
            delivery_confirmed = True
            record_sent_message(
                entity_type="CustomerInvoice",
                entity_id=int(self.current_inv_id),
                related_draft_type="LegacyCustomerInvoiceDocPath",
                related_draft_id=None,
                template_code="CustomerInvoiceLegacyDocPath",
                template_version_id=None,
                recipient_email=recipient,
                original_recipient_email=recipient,
                subject=subject,
                body=body,
                attachment_path=doc_path,
                created_by="InvoiceViewerPageLegacy",
            )
            mark_invoice_sent(self.current_inv_id, doc_path=doc_path)
            QMessageBox.information(self, "Sent", f"Invoice #{self.current_inv_id} resent to {recipient}.")
            self.refresh_data()
        except Exception as exc:
            if delivery_confirmed:
                QMessageBox.warning(
                    self,
                    "Legacy Resend Logging Error",
                    f"{exc}\n\nThe email gateway may already have accepted delivery. Do not retry blindly. Review OutboundMessageLog before sending again.",
                )
            else:
                QMessageBox.critical(self, "Gateway", str(exc))
