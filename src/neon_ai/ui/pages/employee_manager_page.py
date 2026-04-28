from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.bootstrap import ensure_legacy_import_paths

ensure_legacy_import_paths()

from database.employees import get_all_employees, save_employee
from database.roles import add_standard_role, get_standard_roles
from database.timesheets import add_standard_task, delete_standard_task, get_standard_tasks


class EmployeeManagerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_emp_id: int | None = None
        self.cached_employees: list[dict] = []
        self.role_name_choices: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Employee Management Center")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_pipeline_panel())
        splitter.addWidget(self._build_profile_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self.refresh_data()
        self._refresh_global_tasks()

    def _build_pipeline_panel(self) -> QGroupBox:
        group = QGroupBox("Active Staff")
        layout = QVBoxLayout(group)

        self.emp_table = QTableWidget(0, 3)
        self.emp_table.setHorizontalHeaderLabels(["ID", "Name", "Rate"])
        self.emp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.emp_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.emp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.emp_table.verticalHeader().setVisible(False)
        self.emp_table.setColumnWidth(0, 40)
        self.emp_table.setColumnWidth(2, 90)
        self.emp_table.itemSelectionChanged.connect(self._on_emp_select)
        layout.addWidget(self.emp_table)

        new_button = QPushButton("Create New Employee")
        new_button.clicked.connect(self._clear_form)
        layout.addWidget(new_button)
        return group

    def _build_profile_panel(self) -> QGroupBox:
        group = QGroupBox("Employee Profile")
        layout = QVBoxLayout(group)

        self.name_field = QLineEdit()
        self.rate_field = QLineEdit("0.0")
        self.burden_field = QLineEdit("30.0")
        self.phone_field = QLineEdit()
        self.email_field = QLineEdit()
        self.address_field = QLineEdit()
        self.city_field = QLineEdit()
        self.employee_class_combo = QComboBox()

        form_layout = QFormLayout()
        form_layout.addRow("Full Name:", self.name_field)

        rate_row = QFrame()
        rate_layout = QHBoxLayout(rate_row)
        rate_layout.setContentsMargins(0, 0, 0, 0)
        rate_layout.addWidget(self.rate_field)
        rate_layout.addWidget(QLabel("Labor Burden (%):"))
        rate_layout.addWidget(self.burden_field)
        form_layout.addRow("Nominal Hourly Rate ($):", rate_row)

        form_layout.addRow("Worker Class:", self.employee_class_combo)
        form_layout.addRow("Phone:", self.phone_field)
        form_layout.addRow("Email:", self.email_field)
        form_layout.addRow("Street Address:", self.address_field)
        form_layout.addRow("City:", self.city_field)
        layout.addLayout(form_layout)

        task_group = QGroupBox("Global Task Dictator")
        task_layout = QVBoxLayout(task_group)
        task_layout.addWidget(QLabel("Standard Tasks for Tiber & Web Applet:"))
        task_input_row = QFrame()
        task_input_layout = QHBoxLayout(task_input_row)
        task_input_layout.setContentsMargins(0, 0, 0, 0)
        self.new_task_field = QLineEdit()
        task_input_layout.addWidget(self.new_task_field)
        add_task_button = QPushButton("Add Task")
        add_task_button.clicked.connect(self._add_global_task)
        task_input_layout.addWidget(add_task_button)
        task_layout.addWidget(task_input_row)
        task_list_row = QFrame()
        task_list_layout = QHBoxLayout(task_list_row)
        task_list_layout.setContentsMargins(0, 0, 0, 0)
        self.task_list = QListWidget()
        task_list_layout.addWidget(self.task_list, 1)
        delete_task_button = QPushButton("Delete Selected")
        delete_task_button.clicked.connect(self._delete_global_task)
        task_list_layout.addWidget(delete_task_button)
        task_layout.addWidget(task_list_row)
        layout.addWidget(task_group)

        role_group = QGroupBox("Estimating Roles & Standard Rates")
        role_layout = QVBoxLayout(role_group)
        self.role_table = QTableWidget(0, 3)
        self.role_table.setHorizontalHeaderLabels(["Role Name", "Base Rate", "Burden %"])
        self.role_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.role_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.role_table.verticalHeader().setVisible(False)
        self.role_table.setColumnWidth(0, 150)
        self.role_table.setColumnWidth(1, 80)
        self.role_table.setColumnWidth(2, 80)
        role_layout.addWidget(self.role_table)

        self.new_role_field = QLineEdit()
        self.new_rate_field = QLineEdit("0.0")
        self.new_burden_field = QLineEdit("30.0")
        role_form = QFormLayout()
        role_form.addRow("Role:", self.new_role_field)
        role_form.addRow("Base Rate $", self.new_rate_field)
        role_form.addRow("Burden %", self.new_burden_field)
        role_layout.addLayout(role_form)
        save_role_button = QPushButton("Save Estimating Role")
        save_role_button.clicked.connect(self._save_standard_role)
        role_layout.addWidget(save_role_button)
        layout.addWidget(role_group)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addStretch(1)
        save_profile_button = QPushButton("Save Profile")
        save_profile_button.clicked.connect(self._on_save)
        footer_layout.addWidget(save_profile_button)
        layout.addWidget(footer)
        return group

    def _clear_form(self) -> None:
        self.current_emp_id = None
        self.name_field.setText("")
        self.rate_field.setText("0.0")
        self.burden_field.setText("30.0")
        self.phone_field.setText("")
        self.email_field.setText("")
        self.address_field.setText("")
        self.city_field.setText("")
        self.employee_class_combo.setCurrentIndex(-1)
        self.emp_table.clearSelection()

    def _on_emp_select(self) -> None:
        selection = self.emp_table.selectedItems()
        if not selection:
            return
        emp_id = int(self.emp_table.item(self.emp_table.row(selection[0]), 0).text())
        self.current_emp_id = emp_id
        for emp in self.cached_employees:
            if emp["EmployeeID"] == emp_id:
                self.name_field.setText(emp.get("EmployeeName") or "")
                self.rate_field.setText(str(float(emp.get("EmployeeRate") or 0)))
                self.burden_field.setText(str(float(emp.get("EmployeeBurden") or 30)))
                self.phone_field.setText(emp.get("EmployeePhone") or "")
                self.email_field.setText(emp.get("EmployeeEmail") or "")
                self.address_field.setText(emp.get("EmployeeAddress") or "")
                self.city_field.setText(emp.get("EmployeeCity") or "")
                self.employee_class_combo.setCurrentText(emp.get("EmployeeClass") or "")
                break

    def _on_save(self) -> None:
        name = self.name_field.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Employee Name is required.")
            return

        try:
            data = {
                "emp_id": self.current_emp_id,
                "name": name,
                "rate": float(self.rate_field.text()),
                "burden": float(self.burden_field.text()),
                "phone": self.phone_field.text(),
                "email": self.email_field.text(),
                "address": self.address_field.text(),
                "city": self.city_field.text(),
                "employee_class": self.employee_class_combo.currentText().strip() or None,
            }
        except ValueError:
            QMessageBox.warning(self, "Error", "Rate and Burden must be numbers.")
            return

        try:
            if save_employee(data):
                QMessageBox.information(self, "Success", "Employee profile saved.")
                self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def refresh_data(self) -> None:
        self.emp_table.setRowCount(0)
        try:
            roles = get_standard_roles()
            self.role_name_choices = [r["RoleName"] for r in roles]
            self.employee_class_combo.clear()
            self.employee_class_combo.addItems(self.role_name_choices)
            self.cached_employees = get_all_employees()
        except Exception as exc:
            QMessageBox.critical(self, "Refresh Error", str(exc))
            return

        for emp in self.cached_employees:
            row = self.emp_table.rowCount()
            self.emp_table.insertRow(row)
            values = [
                str(emp["EmployeeID"]),
                str(emp.get("EmployeeName") or ""),
                f"${float(emp.get('EmployeeRate') or 0):.2f}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.emp_table.setItem(row, column, item)

        self._refresh_roles()

    def _add_global_task(self) -> None:
        task_name = self.new_task_field.text().strip()
        if not task_name:
            return
        try:
            add_standard_task(task_name)
            self.new_task_field.setText("")
            self._refresh_global_tasks()
            QMessageBox.information(self, "Success", f"'{task_name}' is now active across all Argon apps.")
        except Exception as exc:
            QMessageBox.critical(self, "Task Error", str(exc))

    def _refresh_global_tasks(self) -> None:
        self.task_list.clear()
        try:
            tasks = get_standard_tasks()
        except Exception:
            return
        for task in tasks:
            self.task_list.addItem(f" • {task}")

    def _delete_global_task(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Selection", "Please select a task to delete.")
            return
        task_name = item.text().replace(" • ", "").strip()
        confirmed = QMessageBox.question(
            self,
            "Confirm",
            f"Are you sure you want to delete '{task_name}'?\n\n(This will not affect historical timesheets.)",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            if delete_standard_task(task_name):
                self._refresh_global_tasks()
                QMessageBox.information(self, "Success", f"'{task_name}' has been removed from all apps.")
            else:
                QMessageBox.critical(self, "Error", "Could not delete task from the database.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _refresh_roles(self) -> None:
        self.role_table.setRowCount(0)
        try:
            roles = get_standard_roles()
        except Exception:
            return
        for role in roles:
            row = self.role_table.rowCount()
            self.role_table.insertRow(row)
            values = [
                role["RoleName"],
                f"${float(role['BaseRate']):.2f}",
                f"{float(role['BurdenPercent'])}%",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.role_table.setItem(row, column, item)

    def _save_standard_role(self) -> None:
        name = self.new_role_field.text().strip()
        if not name:
            QMessageBox.warning(self, "Input Error", "Please enter a Role Name (e.g., 'Journeyman 1').")
            return
        try:
            rate = float(self.new_rate_field.text())
            burden = float(self.new_burden_field.text())
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Rate and Burden must be numbers.")
            return
        try:
            if add_standard_role(name, rate, burden):
                self.new_role_field.setText("")
                self.new_rate_field.setText("0.0")
                self.new_burden_field.setText("30.0")
                self._refresh_roles()
                self.refresh_data()
                QMessageBox.information(self, "Success", f"Role '{name}' is now available for estimating.")
            else:
                QMessageBox.critical(self, "Database Error", "Failed to save the role to PostgreSQL.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
