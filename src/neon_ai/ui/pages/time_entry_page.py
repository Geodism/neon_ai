from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.timer import get_employees, get_tasks, insert_time
from neon_ai.database.timesheets import get_open_workorder_choices


class TimeEntryPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.emp_dict: dict[str, int] = {}
        self.emp_rate_dict: dict[int, float] = {}
        self.wo_dict: dict[str, int] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Manual Time Entry")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

        form_layout = QFormLayout()
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(10)

        self.employee_combo = QComboBox()
        self.employee_combo.setMinimumWidth(320)
        form_layout.addRow("Employee:", self.employee_combo)

        self.workorder_combo = QComboBox()
        self.workorder_combo.setMinimumWidth(320)
        form_layout.addRow("Work Order:", self.workorder_combo)

        self.task_combo = QComboBox()
        self.task_combo.setEditable(False)
        self.task_combo.setMinimumWidth(320)
        form_layout.addRow("Task:", self.task_combo)

        self.hours_field = QLineEdit()
        self.hours_field.setMinimumWidth(320)
        form_layout.addRow("Hours Worked:", self.hours_field)

        self.date_field = QLineEdit(datetime.now().strftime("%Y%m%d"))
        self.date_field.setMinimumWidth(320)
        form_layout.addRow("Date (YYYYMMDD):", self.date_field)

        layout.addLayout(form_layout)

        save_button = QPushButton("Save Time Entry")
        save_button.clicked.connect(self._save_time)
        layout.addWidget(save_button)
        layout.addStretch(1)

    def refresh_data(self) -> None:
        self.emp_dict.clear()
        self.emp_rate_dict.clear()
        try:
            employees = get_employees()
            workorders = get_open_workorder_choices()
            tasks = get_tasks()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to load time entry choices:\n{exc}")
            return

        employee_labels: list[str] = []
        for emp in employees:
            label = emp["EmployeeName"]
            self.emp_dict[label] = emp["EmployeeID"]
            self.emp_rate_dict[emp["EmployeeID"]] = float(emp.get("EmployeeRate") or 0.0)
            employee_labels.append(label)
        self.employee_combo.clear()
        self.employee_combo.addItems(employee_labels)

        self.wo_dict.clear()
        wo_labels: list[str] = []
        for wo in workorders:
            label_text = str(wo["Label"])
            suffix = label_text.split(" - ", 1)[1] if " - " in label_text else label_text
            label = f"WO #{wo['WorkOrderID']} - {suffix}"
            self.wo_dict[label] = wo["WorkOrderID"]
            wo_labels.append(label)
        self.workorder_combo.clear()
        self.workorder_combo.addItems(wo_labels)

        self.task_combo.clear()
        self.task_combo.addItems(tasks)

    def _save_time(self) -> None:
        try:
            emp_id = self.emp_dict[self.employee_combo.currentText()]
            wo_id = self.wo_dict[self.workorder_combo.currentText()]
            task_name = self.task_combo.currentText()
            hours = float(self.hours_field.text())
            date_worked = int(self.date_field.text())
            hourly_rate = self.emp_rate_dict[emp_id]

            insert_time(
                worker_id=emp_id,
                workorder_id=wo_id,
                date_worked=date_worked,
                hours_worked=hours,
                task_id=task_name,
                hourly_rate=hourly_rate,
            )
            QMessageBox.information(self, "Success", "Manual time entry saved successfully!")
            self.hours_field.setText("")
            self.task_combo.setCurrentIndex(-1)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not save time. Check inputs.\n{exc}")
