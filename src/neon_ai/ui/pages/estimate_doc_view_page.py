from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
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

from neon_ai.database.automation import build_client_estimate_doc, get_client_estimate_doc_path
from neon_ai.database.estimates import (
    get_dashboard_estimates,
    get_detailed_estimate_data,
    recalculate_estimate_totals,
    update_estimate_status,
)
from neon_ai.database.rfq import create_and_send_rfq_batch, get_estimate_rfq_status, get_vendor_choices

RFQ_EMAIL_MEMORY_PATH = Path(__file__).resolve().parents[4] / "resources" / "rfq_vendor_email_memory.json"


def load_rfq_email_memory() -> dict:
    if not RFQ_EMAIL_MEMORY_PATH.exists():
        return {}
    try:
        data = json.loads(RFQ_EMAIL_MEMORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_rfq_email_memory(memory: dict) -> None:
    try:
        RFQ_EMAIL_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        RFQ_EMAIL_MEMORY_PATH.write_text(json.dumps(memory, indent=2), encoding="utf-8")
    except Exception:
        pass


class CreateRFQDialog(QDialog):
    def __init__(self, parent, vendors: list[dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create RFQ")
        self.setModal(True)
        self.result: dict | None = None
        self.vendors = vendors or []
        self.vendor_widgets: dict[int, tuple[QCheckBox, QLineEdit, str]] = {}
        self.email_memory = load_rfq_email_memory()

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Select wholesalers and enter the recipient email for each RFQ.")
        )

        due_row = QFrame()
        due_layout = QHBoxLayout(due_row)
        due_layout.setContentsMargins(0, 0, 0, 0)
        due_layout.addWidget(QLabel("Due Date:"))
        self.due_date_field = QLineEdit((datetime.date.today() + datetime.timedelta(days=7)).isoformat())
        self.due_date_field.setMaximumWidth(120)
        due_layout.addWidget(self.due_date_field)
        due_layout.addStretch(1)
        layout.addWidget(due_row)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        for vendor in self.vendors:
            row = QFrame()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            enabled = QCheckBox(vendor["VendorName"])
            row_layout.addWidget(enabled)

            contact_bits = []
            if vendor.get("VendorContactName"):
                contact_bits.append(vendor["VendorContactName"])
            if vendor.get("VendorContactNumber"):
                contact_bits.append(vendor["VendorContactNumber"])
            if contact_bits:
                row_layout.addWidget(QLabel(f"({', '.join(contact_bits)})"))

            remembered_email = self.email_memory.get(str(vendor["VendorID"])) or self.email_memory.get(vendor["VendorName"], "")
            email_field = QLineEdit(remembered_email)
            row_layout.addWidget(email_field, 1)
            body_layout.addWidget(row)
            self.vendor_widgets[vendor["VendorID"]] = (enabled, email_field, vendor["VendorName"])
        layout.addWidget(body)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_button)
        submit_button = QPushButton("Create & Send RFQs")
        submit_button.clicked.connect(self.on_submit)
        footer_layout.addWidget(submit_button)
        layout.addWidget(footer)

    def on_submit(self) -> None:
        selected: list[dict] = []
        for vendor_id, (enabled, email_field, vendor_name) in self.vendor_widgets.items():
            if enabled.isChecked():
                email_value = email_field.text().strip()
                if not email_value:
                    QMessageBox.warning(self, "Missing Email", f"Please enter a recipient email for {vendor_name}.")
                    return
                selected.append(
                    {
                        "vendor_id": vendor_id,
                        "vendor_name": vendor_name,
                        "recipient_email": email_value,
                    }
                )

        if not selected:
            QMessageBox.warning(self, "No Vendors Selected", "Select at least one wholesaler to create RFQs.")
            return

        self.result = {
            "due_date": self.due_date_field.text().strip(),
            "vendors": selected,
        }
        updated_memory = dict(self.email_memory)
        for vendor in selected:
            updated_memory[str(vendor["vendor_id"])] = vendor["recipient_email"]
            updated_memory[vendor["vendor_name"]] = vendor["recipient_email"]
        save_rfq_email_memory(updated_memory)
        self.accept()


class EstimateDocViewPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_loaded_id: int | None = None
        self.current_data: dict | None = None
        self.current_customer_copy_path: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_doc_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

    def _build_list_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.tree = QTableWidget(0, 3)
        self.tree.setHorizontalHeaderLabels(["ID", "Project Location", "Status"])
        self.tree.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tree.verticalHeader().setVisible(False)
        self.tree.setColumnWidth(0, 80)
        self.tree.setColumnWidth(1, 220)
        self.tree.setColumnWidth(2, 100)
        self.tree.itemSelectionChanged.connect(self.on_select)
        layout.addWidget(self.tree)
        return panel

    def _build_doc_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        layout.addWidget(self.viewer, 1)

        button_row = QFrame()
        button_layout = QGridLayout(button_row)
        self.btn_lock = QPushButton("Lock For Submission")
        self.btn_lock.clicked.connect(self.lock_for_submission)
        self.btn_lock.setEnabled(False)
        button_layout.addWidget(self.btn_lock, 0, 0)

        self.btn_create_copy = QPushButton("Create Customer Copy")
        self.btn_create_copy.clicked.connect(self.create_customer_copy)
        self.btn_create_copy.setEnabled(False)
        button_layout.addWidget(self.btn_create_copy, 0, 1)

        self.btn_view_copy = QPushButton("View Customer Copy")
        self.btn_view_copy.clicked.connect(self.view_customer_copy)
        self.btn_view_copy.setEnabled(False)
        button_layout.addWidget(self.btn_view_copy, 0, 2)

        self.btn_create_rfq = QPushButton("Create RFQ")
        self.btn_create_rfq.clicked.connect(self.create_rfq)
        self.btn_create_rfq.setEnabled(False)
        button_layout.addWidget(self.btn_create_rfq, 0, 3)
        layout.addWidget(button_row)
        return panel

    def refresh_data(self) -> None:
        self.tree.setRowCount(0)
        for est in get_dashboard_estimates():
            status = str(est.get("Status", "Draft"))
            row = self.tree.rowCount()
            self.tree.insertRow(row)
            values = [str(est["EstimateID"]), str(est["SiteName"]), status]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if status.lower() in {"locked", "sent"}:
                    item.setForeground(Qt.GlobalColor.red)
                self.tree.setItem(row, column, item)

        self.current_loaded_id = None
        self.current_data = None
        self.current_customer_copy_path = None
        self.viewer.setPlainText("")
        self._update_button_states()

    def on_select(self) -> None:
        selection = self.tree.selectedItems()
        if not selection:
            return
        self.current_loaded_id = int(self.tree.item(self.tree.row(selection[0]), 0).text())
        self.current_data = get_detailed_estimate_data(self.current_loaded_id)
        self.current_customer_copy_path = self._get_customer_copy_path()
        self.render_doc(self.current_data)

    def render_doc(self, data: dict) -> None:
        parent = data["parent"]
        width = 85
        labor_markup = float(parent.get("LaborMarkUp") or 20.0) / 100
        material_markup = float(parent.get("MaterialMarkUp") or 20.0) / 100

        doc = f"{'ESTIMATE - INTERNAL REVIEW':^{width}}\n"
        doc += f"{'=' * width}\n\n"
        doc += f"{'BILL TO:':<42} {'PROJECT SITE:':<40}\n"
        doc += f"{parent['CustomerName']:<42} {parent['SiteName']:<40}\n"
        doc += (
            f"{str(parent.get('CustAddr') or '') + ' ' + (parent.get('CustStreet') or ''):<42} "
            f"{str(parent.get('SiteNum') or '') + ' ' + (parent.get('SiteStreet') or ''):<40}\n"
        )
        doc += f"\nDATE: {parent['CreatedDate']}\n"
        doc += f"STATUS: {parent.get('Status') or 'DRAFT'}\n"
        doc += f"{'-' * width}\n"

        doc += f"\nLABOUR (Markup: {labor_markup * 100:.0f}%)\n"
        labor_total = 0.0
        for line in data["labor"]:
            base = float(line["Hours"]) * float(line["Rate"])
            sell = base * (1 + labor_markup)
            labor_total += sell
            doc += f"{line['RoleDescription']:<50} ${sell:>15.2f}\n"

        doc += f"\nMATERIALS (Markup: {material_markup * 100:.0f}%)\n"
        material_total = 0.0
        for line in data["materials"]:
            base = float(line["Quantity"]) * float(line["UnitCost"])
            sell = base * (1 + material_markup)
            material_total += sell
            doc += f"{line['Description']:<50} ${sell:>15.2f}\n"

        doc += f"\n{'=' * width}\n"
        doc += f"{'TOTAL ESTIMATED PRICE:':<50} ${(labor_total + material_total):>15.2f}\n"
        doc += f"{'=' * width}\n"

        self.viewer.setPlainText(doc)
        self._update_button_states()

    def lock_for_submission(self) -> None:
        if not self.current_loaded_id or not self.current_data:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return

        current_status = str(self.current_data["parent"].get("Status") or "").strip().lower()
        if current_status in {"locked", "sent"}:
            QMessageBox.information(self, "Already Locked", "This estimate is already locked for submission.")
            return

        rfq_status = get_estimate_rfq_status(self.current_loaded_id)
        if rfq_status["has_materials"] and not rfq_status["has_rfq"]:
            proceed = QMessageBox.question(
                self,
                "No RFQ Created",
                "No material RFQ has been created for this estimate yet.\n\n"
                "Are you sure you want to lock and submit this pricing?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return
        elif rfq_status["has_materials"] and rfq_status["has_rfq"] and not rfq_status["has_returned_quotes"]:
            proceed = QMessageBox.question(
                self,
                "Quotes Not Returned",
                "RFQs have been created for this estimate, but no vendor quotes have been logged yet.\n\n"
                "Are you sure you want to lock and submit this pricing?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        confirm = QMessageBox.question(
            self,
            "Lock For Submission",
            "This will lock the estimate for AI submission to the customer. Proceed?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        estimate_id = self.current_loaded_id
        if update_estimate_status(estimate_id, "Locked"):
            recalculate_estimate_totals(estimate_id)
            self.refresh_data()
            for row in range(self.tree.rowCount()):
                if int(self.tree.item(row, 0).text()) == estimate_id:
                    self.tree.selectRow(row)
                    self.on_select()
                    break
            QMessageBox.information(self, "Locked", "Estimate locked for submission.")
        else:
            QMessageBox.critical(self, "Error", "Failed to lock estimate for submission.")

    def create_customer_copy(self) -> None:
        if not self.current_data or not self.current_loaded_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return
        try:
            self.current_customer_copy_path = build_client_estimate_doc(self.current_loaded_id)
            self._update_button_states()
            QMessageBox.information(self, "Saved", f"Customer copy saved to:\n{self.current_customer_copy_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create customer copy:\n{exc}")

    def view_customer_copy(self) -> None:
        if not self.current_data or not self.current_loaded_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return
        if not self.current_customer_copy_path or not os.path.exists(self.current_customer_copy_path):
            self.current_customer_copy_path = self._get_customer_copy_path()
        if not self.current_customer_copy_path or not os.path.exists(self.current_customer_copy_path):
            QMessageBox.warning(
                self,
                "No Customer Copy",
                "No saved customer copy was found yet. Please create it first.",
            )
            self._update_button_states()
            return
        try:
            os.startfile(self.current_customer_copy_path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Error", f"Could not open the customer copy:\n{exc}")

    def create_rfq(self) -> None:
        if not self.current_loaded_id or not self.current_data:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return

        vendors = get_vendor_choices()
        if not vendors:
            QMessageBox.warning(self, "No Vendors", "No wholesalers are set up yet in the vendor list.")
            return

        dialog = CreateRFQDialog(self, vendors)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result:
            return

        try:
            results = create_and_send_rfq_batch(
                self.current_loaded_id,
                dialog.result["vendors"],
                dialog.result["due_date"] or None,
            )
        except Exception as exc:
            QMessageBox.critical(self, "RFQ Error", f"Failed to create/send RFQ package:\n{exc}")
            return

        sent_count = sum(1 for row in results if row["sent"])
        total_count = len(results)
        QMessageBox.information(
            self,
            "RFQ Complete",
            f"Created {total_count} RFQ(s).\nSent successfully: {sent_count}.",
        )

    def _get_customer_copy_path(self) -> str | None:
        if not self.current_loaded_id:
            return None
        try:
            return get_client_estimate_doc_path(self.current_loaded_id)
        except Exception:
            return None

    def _update_button_states(self) -> None:
        has_selection = self.current_data is not None
        status_text = str(self.current_data["parent"].get("Status") or "") if has_selection else ""
        is_locked = status_text.strip().lower() in {"locked", "sent", "accepted"}
        has_copy = bool(self.current_customer_copy_path and os.path.exists(self.current_customer_copy_path))

        self.btn_lock.setEnabled(bool(has_selection and not is_locked))
        self.btn_create_copy.setEnabled(bool(has_selection))
        self.btn_view_copy.setEnabled(has_copy)
        self.btn_create_rfq.setEnabled(bool(has_selection and not is_locked))
