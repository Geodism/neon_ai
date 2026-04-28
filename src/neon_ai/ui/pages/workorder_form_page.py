from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from neon_ai.bootstrap import ensure_legacy_import_paths

ensure_legacy_import_paths()

from database.customers import get_customers, get_sites
from database.workorders import approve_work_order, get_next_workorder_id, insert_workorder


class WorkOrderFormPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.customer_dict: dict[str, int] = {}
        self.all_sites: list[dict] = []
        self.current_site_dict: dict[str, int] = {}
        self.current_work_order_id: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 30, 30, 30)
        outer.setSpacing(0)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        outer.addLayout(grid)

        title = QLabel("New Work Order")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        grid.addWidget(title, 0, 0, 1, 2)

        grid.addWidget(QLabel("Work Order ID:"), 1, 0)
        self.workorder_id_label = QLabel("")
        self.workorder_id_label.setStyleSheet("font-weight: 700;")
        grid.addWidget(self.workorder_id_label, 1, 1)

        grid.addWidget(QLabel("Billing Mode:"), 2, 0)
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(10)
        self.project_radio = QRadioButton("Project Work")
        self.ops_radio = QRadioButton("Office Ops")
        self.project_radio.setChecked(True)
        self.billing_group = QButtonGroup(self)
        self.billing_group.addButton(self.project_radio)
        self.billing_group.addButton(self.ops_radio)
        mode_layout.addWidget(self.project_radio)
        mode_layout.addWidget(self.ops_radio)
        mode_layout.addStretch(1)
        grid.addWidget(mode_row, 2, 1)

        grid.addWidget(QLabel("Customer *"), 3, 0)
        self.customer_combo = QComboBox()
        self.customer_combo.setMinimumWidth(320)
        self.customer_combo.currentTextChanged.connect(self.on_customer_change)
        grid.addWidget(self.customer_combo, 3, 1)

        grid.addWidget(QLabel("Site *"), 4, 0)
        self.site_combo = QComboBox()
        self.site_combo.setMinimumWidth(320)
        grid.addWidget(self.site_combo, 4, 1)

        grid.addWidget(QLabel("Description *"), 5, 0)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setFixedSize(360, 120)
        grid.addWidget(self.description_edit, 5, 1)

        save_row = QWidget()
        save_layout = QHBoxLayout(save_row)
        save_layout.setContentsMargins(0, 20, 0, 0)
        save_layout.addStretch(1)
        save_button = QPushButton("Save Work Order")
        save_button.clicked.connect(self.save_workorder)
        save_layout.addWidget(save_button)
        grid.addWidget(save_row, 6, 1)

        grid.setColumnStretch(1, 1)
        outer.addStretch(1)

    def refresh_data(self) -> None:
        self.workorder_id_label.setText(str(get_next_workorder_id()))

        customers = get_customers()
        self.customer_dict = {f"{row['CustomerName']} (ID {row['CustomerID']})": row["CustomerID"] for row in customers}
        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        self.customer_combo.addItems(self.customer_dict.keys())
        self.customer_combo.setCurrentIndex(-1)
        self.customer_combo.blockSignals(False)

        self.all_sites = get_sites()
        self.clear_form()

    def on_customer_change(self, _selection: str | None = None) -> None:
        selected_customer = self.customer_combo.currentText()
        if not selected_customer:
            self.site_combo.clear()
            self.current_site_dict.clear()
            return

        customer_id = self.customer_dict[selected_customer]
        filtered_sites = [row for row in self.all_sites if row["CustomerID"] == customer_id]

        self.current_site_dict.clear()
        for row in filtered_sites:
            self.current_site_dict[f"{row['SiteName']} (ID {row['SiteID']})"] = row["SiteID"]

        self.site_combo.clear()
        self.site_combo.addItems(self.current_site_dict.keys())
        self.site_combo.setCurrentIndex(-1)

    def save_workorder(self) -> None:
        customer_label = self.customer_combo.currentText().strip()
        site_label = self.site_combo.currentText().strip()
        description = self.description_edit.toPlainText().strip()
        billing_mode = "Project" if self.project_radio.isChecked() else "Ops"
        created_date = date.today().strftime("%Y-%m-%d")

        if not customer_label or not site_label:
            QMessageBox.critical(self, "Validation Error", "Customer and Site are required.")
            return
        if not description:
            QMessageBox.critical(self, "Validation Error", "Description is required.")
            return

        site_id = self.current_site_dict.get(site_label)
        try:
            insert_workorder(
                site_id=site_id,
                created_date=created_date,
                job_status="OPEN",
                description=description,
                billing_type=billing_mode,
            )
            QMessageBox.information(self, "Success", "Work Order saved successfully.")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Could not save Work Order:\n{exc}")

    def clear_form(self) -> None:
        self.current_work_order_id = None
        self.customer_combo.setCurrentIndex(-1)
        self.site_combo.clear()
        self.current_site_dict.clear()
        self.description_edit.setPlainText("")
        self.project_radio.setChecked(True)

    def prompt_for_approval(self) -> None:
        if not self.current_work_order_id:
            QMessageBox.warning(self, "No Selection", "Please select a Work Order to approve.")
            return

        confirmed = QMessageBox.question(
            self,
            "Confirm Manager Approval",
            "Do you officially approve this Work Order?\n\n"
            "Approving this will unlock the ability to purchase materials.",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            approve_work_order(self.current_work_order_id)
            QMessageBox.information(
                self,
                "Success",
                "Work order approved! You may now generate Purchase Orders.",
            )
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to approve: {exc}")
