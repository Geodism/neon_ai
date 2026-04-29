from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.purchases import get_next_po_id, get_vendors, insert_purchase_order
from neon_ai.database.timer import get_employees
from neon_ai.database.workorders import get_open_workorders_dict


class PurchaseOrderFormPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.wo_dict: dict[str, int] = {}
        self.vendor_dict: dict[str, int] = {}
        self.employee_dict: dict[str, int] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Log Material Costs (PO)")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

        form_layout = QFormLayout()
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(10)

        self.po_id_label = QLabel("")
        self.po_id_label.setStyleSheet("font-weight: 700;")
        form_layout.addRow("Purchase Order ID:", self.po_id_label)

        self.wo_combo = QComboBox()
        form_layout.addRow("Work Order *", self.wo_combo)

        self.vendor_combo = QComboBox()
        form_layout.addRow("Vendor *", self.vendor_combo)

        self.emp_combo = QComboBox()
        form_layout.addRow("Purchaser (Employee) *", self.emp_combo)

        self.date_field = QLineEdit(date.today().isoformat())
        form_layout.addRow("Date *", self.date_field)

        self.total_field = QLineEdit()
        form_layout.addRow("Total Amount ($) *", self.total_field)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setFixedHeight(90)
        form_layout.addRow("Material Description *", self.description_edit)

        layout.addLayout(form_layout)

        save_button = QPushButton("Save Purchase Order")
        save_button.clicked.connect(self._save_po)
        layout.addWidget(save_button)
        layout.addStretch(1)

    def refresh_data(self) -> None:
        self.po_id_label.setText(str(get_next_po_id()))

        self.wo_dict.clear()
        for workorder in get_open_workorders_dict(billing_mode="Project"):
            self.wo_dict[f"WO #{workorder['WorkOrderID']} - {workorder['SiteName']}"] = workorder["WorkOrderID"]
        self.wo_combo.clear()
        self.wo_combo.addItems(self.wo_dict.keys())
        self.wo_combo.setCurrentIndex(-1)

        self.vendor_dict.clear()
        for vendor in get_vendors():
            self.vendor_dict[vendor["VendorName"]] = vendor["VendorID"]
        self.vendor_combo.clear()
        self.vendor_combo.addItems(self.vendor_dict.keys())
        self.vendor_combo.setCurrentIndex(-1)

        self.employee_dict.clear()
        for employee in get_employees():
            self.employee_dict[employee["EmployeeName"]] = employee["EmployeeID"]
        self.emp_combo.clear()
        self.emp_combo.addItems(self.employee_dict.keys())
        self.emp_combo.setCurrentIndex(-1)

        self.date_field.setText(date.today().isoformat())

    def _save_po(self) -> None:
        wo_label = self.wo_combo.currentText().strip()
        vendor_label = self.vendor_combo.currentText().strip()
        emp_label = self.emp_combo.currentText().strip()
        po_date = self.date_field.text().strip()
        total_str = self.total_field.text().strip()
        description = self.description_edit.toPlainText().strip()

        if not all([wo_label, vendor_label, emp_label, po_date, total_str, description]):
            QMessageBox.critical(self, "Validation Error", "All fields are required.")
            return

        try:
            total_float = float(total_str.replace("$", "").replace(",", ""))
        except ValueError:
            QMessageBox.critical(self, "Validation Error", "Total Amount must be a valid number.")
            return

        wo_id = self.wo_dict[wo_label]
        vendor_id = self.vendor_dict[vendor_label]
        emp_id = self.employee_dict[emp_label]

        try:
            insert_purchase_order(wo_id, vendor_id, emp_id, total_float, description, po_date)
            QMessageBox.information(self, "Success", "Material costs logged successfully.")
            self.total_field.setText("")
            self.description_edit.setPlainText("")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))
