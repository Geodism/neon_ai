from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.bootstrap import ensure_legacy_import_paths

ensure_legacy_import_paths()

from database.invoices import (
    get_invoice_detail,
    get_invoice_header_data,
    get_unbilled_labor,
    get_unbilled_materials,
    get_unbilled_workorders,
    lock_and_export_invoice,
    mark_invoice_sent,
    save_invoice_draft,
)
from invoice_generator import generate_invoice_docx


def normalize_billing_mode(raw_value):
    text = str(raw_value or "").strip().lower()
    if text in {"t&m", "time and materials", "time & materials"}:
        return "T&M"
    if text in {"stipulated", "stipulated price", "fixed price"}:
        return "Stipulated"
    if "time" in text and "material" in text:
        return "T&M"
    if text:
        return "Stipulated"
    return raw_value


class InvoiceCreatorPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_wo_id: int | None = None
        self.current_invoice_id: int | None = None
        self.current_doc_path: str | None = None
        self.tm_summary_label: QLabel | None = None
        self.labor_table: QTableWidget | None = None
        self.mat_table: QTableWidget | None = None
        self.stip_summary_label: QLabel | None = None
        self.l_perc_field: QLineEdit | None = None
        self.m_perc_field: QLineEdit | None = None
        self.l_note_text: QPlainTextEdit | None = None
        self.m_note_text: QPlainTextEdit | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Create Invoice")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        pipeline_group = QGroupBox("Step 1: Select Unbilled Work Order")
        pipeline_layout = QVBoxLayout(pipeline_group)
        self.pipeline_table = QTableWidget(0, 3)
        self.pipeline_table.setHorizontalHeaderLabels(["WO #", "Customer", "Site"])
        self.pipeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pipeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pipeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pipeline_table.verticalHeader().setVisible(False)
        self.pipeline_table.setColumnWidth(0, 60)
        self.pipeline_table.horizontalHeader().setStretchLastSection(True)
        self.pipeline_table.itemSelectionChanged.connect(self.on_wo_select)
        pipeline_layout.addWidget(self.pipeline_table)
        splitter.addWidget(pipeline_group)

        self.crafting_frame = QWidget()
        crafting_layout = QVBoxLayout(self.crafting_frame)
        crafting_layout.setContentsMargins(10, 10, 10, 10)
        splitter.addWidget(self.crafting_frame)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        gate_group = QGroupBox("2. Billing Gatekeeper")
        gate_layout = QVBoxLayout(gate_group)
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(QLabel("Select Billing Mode:"))
        self.rb_tm = QRadioButton("Time & Materials")
        self.rb_stip = QRadioButton("Stipulated Price")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_tm)
        self.mode_group.addButton(self.rb_stip)
        self.rb_tm.toggled.connect(self.on_mode_change)
        self.rb_stip.toggled.connect(self.on_mode_change)
        mode_layout.addWidget(self.rb_tm)
        mode_layout.addWidget(self.rb_stip)
        mode_layout.addStretch(1)
        gate_layout.addWidget(mode_row)
        self.progress_label = QLabel("Select a work order to begin.")
        gate_layout.addWidget(self.progress_label)
        crafting_layout.addWidget(gate_group)

        scope_group = QGroupBox("3. Living Scope of Work")
        scope_layout = QVBoxLayout(scope_group)
        self.scope_text = QPlainTextEdit()
        self.scope_text.setFixedHeight(120)
        scope_layout.addWidget(self.scope_text)
        crafting_layout.addWidget(scope_group)

        self.workspace_container = QWidget()
        self.workspace_layout = QVBoxLayout(self.workspace_container)
        self.workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.setSpacing(0)
        crafting_layout.addWidget(self.workspace_container, 1)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addStretch(1)
        self.btn_export = QPushButton("Export & Lock")
        self.btn_export.clicked.connect(self.on_export_final)
        footer_layout.addWidget(self.btn_export)
        self.btn_send = QPushButton("Send Invoice")
        self.btn_send.clicked.connect(self.on_send_invoice)
        footer_layout.addWidget(self.btn_send)
        self.btn_review = QPushButton("Review Invoice")
        self.btn_review.clicked.connect(self.on_review_invoice)
        footer_layout.addWidget(self.btn_review)
        self.btn_save_draft = QPushButton("Save Draft")
        self.btn_save_draft.clicked.connect(self.on_save_draft)
        footer_layout.addWidget(self.btn_save_draft)
        crafting_layout.addWidget(footer)

    def _clear_workspace(self) -> None:
        while self.workspace_layout.count():
            item = self.workspace_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.tm_summary_label = None
        self.labor_table = None
        self.mat_table = None
        self.stip_summary_label = None
        self.l_perc_field = None
        self.m_perc_field = None
        self.l_note_text = None
        self.m_note_text = None

    def _clear_mode_selection(self) -> None:
        self.mode_group.setExclusive(False)
        self.rb_tm.setChecked(False)
        self.rb_stip.setChecked(False)
        self.mode_group.setExclusive(True)

    def _current_mode(self) -> str:
        if self.rb_tm.isChecked():
            return "T&M"
        if self.rb_stip.isChecked():
            return "Stipulated"
        return ""

    def _set_mode(self, mode: str) -> None:
        if mode == "T&M":
            self.rb_tm.setChecked(True)
        elif mode == "Stipulated":
            self.rb_stip.setChecked(True)
        else:
            self._clear_mode_selection()

    def on_mode_change(self) -> None:
        mode = self._current_mode()
        self._clear_workspace()
        if mode == "T&M":
            self.build_tm_ui()
        elif mode == "Stipulated":
            self.build_stipulated_ui()

    def build_tm_ui(self) -> None:
        container = QGroupBox("Labor & Material Crafting Table")
        layout = QVBoxLayout(container)
        self.tm_summary_label = QLabel("Unbilled labor and received materials will appear below.")
        self.tm_summary_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.tm_summary_label)

        layout.addWidget(QLabel("Labor Logs"))
        self.labor_table = QTableWidget(0, 4)
        self.labor_table.setHorizontalHeaderLabels(["Desc", "Hours", "Rate", "Total"])
        self.labor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.labor_table.verticalHeader().setVisible(False)
        self.labor_table.setColumnWidth(0, 340)
        self.labor_table.setColumnWidth(1, 80)
        self.labor_table.setColumnWidth(2, 90)
        self.labor_table.setColumnWidth(3, 100)
        layout.addWidget(self.labor_table)

        layout.addWidget(QLabel("Received Materials"))
        self.mat_table = QTableWidget(0, 5)
        self.mat_table.setHorizontalHeaderLabels(["Desc", "Qty", "Cost", "Source", "Total"])
        self.mat_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mat_table.verticalHeader().setVisible(False)
        self.mat_table.setColumnWidth(0, 340)
        self.mat_table.setColumnWidth(1, 80)
        self.mat_table.setColumnWidth(2, 90)
        self.mat_table.setColumnWidth(3, 90)
        self.mat_table.setColumnWidth(4, 100)
        layout.addWidget(self.mat_table)

        self.workspace_layout.addWidget(container)

    def build_stipulated_ui(self) -> None:
        container = QGroupBox("Stipulated Progress Claim")
        layout = QVBoxLayout(container)

        self.stip_summary_label = QLabel("Previously invoiced on this contract: $0.00 (0.0%)")
        self.stip_summary_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.stip_summary_label)

        labor_block = QWidget()
        labor_layout = QVBoxLayout(labor_block)
        labor_row = QWidget()
        labor_row_layout = QHBoxLayout(labor_row)
        labor_row_layout.setContentsMargins(0, 0, 0, 0)
        labor_row_layout.addWidget(QLabel("Labor %:"))
        self.l_perc_field = QLineEdit("0.0")
        self.l_perc_field.setMaximumWidth(80)
        labor_row_layout.addWidget(self.l_perc_field)
        labor_row_layout.addStretch(1)
        labor_layout.addWidget(labor_row)
        labor_layout.addWidget(QLabel("Labor Milestone Notes:"))
        self.l_note_text = QPlainTextEdit()
        self.l_note_text.setFixedHeight(70)
        labor_layout.addWidget(self.l_note_text)
        layout.addWidget(labor_block)

        mat_block = QWidget()
        mat_layout = QVBoxLayout(mat_block)
        mat_row = QWidget()
        mat_row_layout = QHBoxLayout(mat_row)
        mat_row_layout.setContentsMargins(0, 0, 0, 0)
        mat_row_layout.addWidget(QLabel("Material %:"))
        self.m_perc_field = QLineEdit("0.0")
        self.m_perc_field.setMaximumWidth(80)
        mat_row_layout.addWidget(self.m_perc_field)
        mat_row_layout.addStretch(1)
        mat_layout.addWidget(mat_row)
        mat_layout.addWidget(QLabel("Material Milestone Notes:"))
        self.m_note_text = QPlainTextEdit()
        self.m_note_text.setFixedHeight(70)
        mat_layout.addWidget(self.m_note_text)
        layout.addWidget(mat_block)

        self.workspace_layout.addWidget(container)

    def on_wo_select(self) -> None:
        selection = self.pipeline_table.selectedItems()
        if not selection:
            return

        row = self.pipeline_table.row(selection[0])
        self.current_wo_id = int(self.pipeline_table.item(row, 0).text())
        self.current_invoice_id = None
        self.current_doc_path = None
        self.scope_text.setPlainText("")
        self._clear_workspace()

        try:
            header = get_invoice_header_data(self.current_wo_id)
            if not header:
                return

            self.scope_text.setPlainText(str(header.get("Scope") or ""))
            billing_mode = normalize_billing_mode(header.get("BillingType"))
            self.current_invoice_id = header.get("CustomerInvoiceId")
            self.current_doc_path = header.get("CustomerInvoiceDocPath")
            self.progress_label.setText(
                "Previously invoiced: "
                f"${float(header.get('PreviouslyInvoicedAmount') or 0):,.2f} | "
                f"Stipulated billed to date: {float(header.get('PreviouslyInvoicedStipulatedPercent') or 0):.1f}%"
            )

            if billing_mode in ("T&M", "Stipulated"):
                self.rb_tm.setEnabled(False)
                self.rb_stip.setEnabled(False)
                self._set_mode(billing_mode)
                if billing_mode == "T&M":
                    self.load_tm_workspace()
                else:
                    self.load_stipulated_workspace(header)
            else:
                self.rb_tm.setEnabled(True)
                self.rb_stip.setEnabled(True)
                self._clear_mode_selection()
        except Exception as exc:
            QMessageBox.critical(self, "Data Error", str(exc))

    def load_tm_workspace(self) -> None:
        if self.labor_table is None or self.mat_table is None or self.tm_summary_label is None:
            self.build_tm_ui()

        labor_rows = get_unbilled_labor(self.current_wo_id)
        material_rows = get_unbilled_materials(self.current_wo_id)
        self.labor_table.setRowCount(0)
        self.mat_table.setRowCount(0)

        labor_total = 0.0
        for row in labor_rows:
            total = float(row.get("LineTotal") or 0)
            labor_total += total
            desc = f"{row.get('DateWorked')} | {row.get('EmployeeName')} | {row.get('TaskName') or 'Task'}"
            table_row = self.labor_table.rowCount()
            self.labor_table.insertRow(table_row)
            values = [
                desc,
                f"{float(row.get('HoursWorked') or 0):.2f}",
                f"${float(row.get('HourlyRate') or 0):,.2f}",
                f"${total:,.2f}",
            ]
            for column, value in enumerate(values):
                self.labor_table.setItem(table_row, column, QTableWidgetItem(value))

        material_total = 0.0
        for row in material_rows:
            total = float(row.get("LineTotal") or 0)
            material_total += total
            desc = f"PO #{row.get('PurchaseOrderID')} | {row.get('Description') or ''}"
            table_row = self.mat_table.rowCount()
            self.mat_table.insertRow(table_row)
            values = [
                desc,
                f"{float(row.get('Qty') or 0):.2f}",
                f"${float(row.get('Cost') or 0):,.2f}",
                "Received",
                f"${total:,.2f}",
            ]
            for column, value in enumerate(values):
                self.mat_table.setItem(table_row, column, QTableWidgetItem(value))

        self.tm_summary_label.setText(
            "Labor ready: "
            f"${labor_total:,.2f} | Materials ready: ${material_total:,.2f} | "
            f"Draft total: ${(labor_total + material_total):,.2f}"
        )

    def load_stipulated_workspace(self, header) -> None:
        if self.l_perc_field is None or self.m_perc_field is None or self.stip_summary_label is None:
            self.build_stipulated_ui()

        self.l_perc_field.setText(str(float(header.get("LaborPercent") or 0)))
        self.m_perc_field.setText(str(float(header.get("MaterialPercent") or 0)))
        self.l_note_text.setPlainText(str(header.get("LaborMilestoneNote") or ""))
        self.m_note_text.setPlainText(str(header.get("MaterialMilestoneNote") or ""))
        self.stip_summary_label.setText(
            "Previously invoiced on this contract: "
            f"${float(header.get('PreviouslyInvoicedAmount') or 0):,.2f} "
            f"({float(header.get('PreviouslyInvoicedStipulatedPercent') or 0):.1f}%)"
        )

    def build_current_invoice_payload(self):
        mode = self._current_mode()
        data = {
            "mode": mode,
            "scope": self.scope_text.toPlainText().strip(),
            "total": 0.0,
            "l_perc": 0.0,
            "m_perc": 0.0,
            "l_note": "",
            "m_note": "",
            "labor_lines": [],
            "material_lines": [],
            "review_summary": "",
        }

        if mode == "Stipulated":
            header = get_invoice_header_data(self.current_wo_id)
            data["l_perc"] = float((self.l_perc_field.text() if self.l_perc_field else "0") or 0)
            data["m_perc"] = float((self.m_perc_field.text() if self.m_perc_field else "0") or 0)
            data["l_note"] = self.l_note_text.toPlainText().strip() if self.l_note_text else ""
            data["m_note"] = self.m_note_text.toPlainText().strip() if self.m_note_text else ""
            est_total = float(header.get("EstimateTotal") or 0)
            data["total"] = (est_total * data["l_perc"] / 100.0) + (est_total * data["m_perc"] / 100.0)
        elif mode == "T&M":
            data["labor_lines"] = get_unbilled_labor(self.current_wo_id)
            data["material_lines"] = get_unbilled_materials(self.current_wo_id)
            data["total"] = sum(float(row.get("LineTotal") or 0) for row in data["labor_lines"]) + sum(
                float(row.get("LineTotal") or 0) for row in data["material_lines"]
            )
            data["review_summary"] = (
                f"{len(data['labor_lines'])} labor rows and {len(data['material_lines'])} material rows prepared for billing."
            )
        else:
            raise ValueError("Select a billing mode first.")
        return data

    def on_save_draft(self) -> None:
        if not self.current_wo_id:
            return
        try:
            data = self.build_current_invoice_payload()
            invoice = save_invoice_draft(self.current_wo_id, data)
            self.current_invoice_id = invoice["CustomerInvoiceId"]
            QMessageBox.information(self, "Success", "Draft saved successfully.")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def on_export_final(self) -> None:
        if not self.current_wo_id:
            return
        confirmed = QMessageBox.question(self, "Finalize", "Lock the invoice and generate the document?")
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            data = self.build_current_invoice_payload()
            invoice = save_invoice_draft(self.current_wo_id, data)
            self.current_invoice_id = invoice["CustomerInvoiceId"]
            header = get_invoice_header_data(self.current_wo_id)
            self.current_doc_path = generate_invoice_docx(header, self._current_mode())
            self.current_invoice_id = lock_and_export_invoice(self.current_wo_id, doc_path=self.current_doc_path)
            QMessageBox.information(self, "Export Success", f"Invoice saved to:\n{self.current_doc_path}")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def on_review_invoice(self) -> None:
        if not self.current_wo_id:
            return
        try:
            header = get_invoice_header_data(self.current_wo_id)
            lines = [
                f"WO #{self.current_wo_id}",
                f"Customer: {header.get('CustomerName') or 'N/A'}",
                f"Billing mode: {self._current_mode()}",
                f"Previously invoiced: ${float(header.get('PreviouslyInvoicedAmount') or 0):,.2f}",
            ]
            if self._current_mode() == "T&M":
                lines.append(f"Labor rows ready: {len(get_unbilled_labor(self.current_wo_id))}")
                lines.append(f"Material rows ready: {len(get_unbilled_materials(self.current_wo_id))}")
            else:
                lines.append(
                    "Stipulated percent billed to date: "
                    f"{float(header.get('PreviouslyInvoicedStipulatedPercent') or 0):.1f}%"
                )
            QMessageBox.information(self, "Invoice Review", "\n".join(lines))
        except Exception as exc:
            QMessageBox.critical(self, "Review Error", str(exc))

    def on_send_invoice(self) -> None:
        if not self.current_invoice_id:
            QMessageBox.warning(self, "Missing Invoice", "Export and lock the invoice before sending it.")
            return
        try:
            from gateway import send_to_user

            detail = get_invoice_detail(self.current_invoice_id)
            header = detail["header"]
            doc_path = self.current_doc_path or header.get("CustomerInvoiceDocPath")
            recipient = header.get("CustomerEmail")
            if not recipient:
                raise ValueError(
                    "This customer does not have an email on file yet. Add the customer email, then send the invoice."
                )
            if not doc_path:
                raise ValueError("Export the invoice document before sending it.")
            sent = send_to_user(
                subject=f"Invoice #{self.current_invoice_id} - Work Order #{self.current_wo_id}",
                content=(
                    f"Hello {header.get('CustomerName')},\n\n"
                    f"Please find attached invoice #{self.current_invoice_id} for work order #{self.current_wo_id}.\n\n"
                    "Thank you,\nArgon Electrical"
                ),
                recipient=recipient,
                attachment_path=doc_path,
            )
            if not sent:
                raise RuntimeError("The email gateway did not confirm the send.")
            mark_invoice_sent(self.current_invoice_id, doc_path=doc_path)
            QMessageBox.information(self, "Invoice Sent", f"Invoice #{self.current_invoice_id} sent to {recipient}.")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def refresh_data(self) -> None:
        self.current_wo_id = None
        self.current_invoice_id = None
        self.current_doc_path = None
        self.progress_label.setText("Select a work order to begin.")
        self.scope_text.setPlainText("")
        self._clear_workspace()
        self.rb_tm.setEnabled(True)
        self.rb_stip.setEnabled(True)
        self._clear_mode_selection()
        self.pipeline_table.setRowCount(0)
        try:
            for row in get_unbilled_workorders():
                table_row = self.pipeline_table.rowCount()
                self.pipeline_table.insertRow(table_row)
                values = [
                    str(row["WorkOrderID"]),
                    str(row.get("CustomerName") or ""),
                    str(row.get("SiteName") or ""),
                ]
                for column, value in enumerate(values):
                    self.pipeline_table.setItem(table_row, column, QTableWidgetItem(value))
        except Exception as exc:
            print(f"Refresh Error: {exc}")
