from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.timer import get_employees, get_tasks, get_todays_task_time, insert_time
from neon_ai.database.workorders import get_open_workorders, get_open_workorders_dict


class TiberDialog(QDialog):
    def __init__(self, parent=None, on_time_logged_callback=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tiber Time Tracker")
        self.resize(600, 720)
        self.setModal(True)

        self.on_time_logged = on_time_logged_callback or self._resolve_dashboard_refresh(parent)
        self.current_task_name: str | None = None
        self.start_time: float | None = None
        self.active_wo_id: int | None = None
        self.previous_seconds_today = 0

        self.wo_dict: dict[str, int] = {}
        self.emp_dict: dict[str, int] = {}
        self.emp_rate_dict: dict[int, float] = {}
        self.slot_dropdowns: list[QComboBox] = []

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_clock)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(self._build_settings_group())
        layout.addWidget(self._build_tasks_group())
        layout.addWidget(self._build_status_group(), 1)

        self.refresh_employees()
        self.refresh_workorders()

    def _resolve_dashboard_refresh(self, parent):
        if parent is None:
            return None
        pages = getattr(parent, "pages", None)
        if isinstance(pages, dict):
            dashboard = pages.get("DashboardFrame")
            callback = getattr(dashboard, "refresh_data", None)
            if callable(callback):
                return callback
        return None

    def _build_settings_group(self) -> QWidget:
        group = QWidget()
        layout = QGridLayout(group)

        layout.addWidget(QLabel("Employee:"), 0, 0)
        self.emp_combo = QComboBox()
        self.emp_combo.setMinimumWidth(320)
        layout.addWidget(self.emp_combo, 0, 1)

        layout.addWidget(QLabel("Billing Mode:"), 1, 0)
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        self.project_radio = QRadioButton("Project Work")
        self.ops_radio = QRadioButton("Office Ops")
        self.project_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.project_radio)
        self.mode_group.addButton(self.ops_radio)
        self.project_radio.toggled.connect(self.refresh_workorders)
        self.ops_radio.toggled.connect(self.refresh_workorders)
        mode_layout.addWidget(self.project_radio)
        mode_layout.addWidget(self.ops_radio)
        mode_layout.addStretch(1)
        layout.addWidget(mode_row, 1, 1)

        self.wo_label = QLabel("Work Order:")
        layout.addWidget(self.wo_label, 2, 0)
        self.wo_combo = QComboBox()
        self.wo_combo.setMinimumWidth(320)
        layout.addWidget(self.wo_combo, 2, 1)
        return group

    def _build_tasks_group(self) -> QWidget:
        group = QWidget()
        layout = QVBoxLayout(group)
        available_tasks = get_tasks()
        for index in range(4):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            combo = QComboBox()
            combo.setMinimumWidth(220)
            combo.addItems(available_tasks)
            if index < len(available_tasks):
                combo.setCurrentText(available_tasks[index])
            row_layout.addWidget(combo)
            self.slot_dropdowns.append(combo)

            start_button = QPushButton("START TASK")
            start_button.clicked.connect(lambda checked=False, widget=combo: self.switch_task(widget.currentText()))
            row_layout.addWidget(start_button, 1)
            layout.addWidget(row)
        return group

    def _build_status_group(self) -> QWidget:
        group = QWidget()
        layout = QVBoxLayout(group)
        self.status_label = QLabel("Status: IDLE")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #2980b9;")
        layout.addWidget(self.status_label)

        self.timer_label = QLabel("00:00:00")
        self.timer_label.setStyleSheet("font-size: 36px; font-weight: 700;")
        layout.addWidget(self.timer_label)

        stop_button = QPushButton("STOP CLOCK & SAVE")
        stop_button.clicked.connect(self.stop_and_save)
        layout.addWidget(stop_button)
        layout.addStretch(1)
        return group

    def refresh_employees(self) -> None:
        self.emp_dict.clear()
        self.emp_rate_dict.clear()
        for employee in get_employees():
            self.emp_dict[employee["EmployeeName"]] = employee["EmployeeID"]
            self.emp_rate_dict[employee["EmployeeID"]] = float(employee.get("EmployeeRate") or 0.0)
        self.emp_combo.clear()
        self.emp_combo.addItems(self.emp_dict.keys())
        self.emp_combo.setCurrentIndex(-1)

    def refresh_workorders(self) -> None:
        self.wo_dict.clear()
        current_mode = "Project" if self.project_radio.isChecked() else "Ops"
        get_open_workorders(current_mode)
        workorder_data = get_open_workorders_dict(billing_mode=current_mode)
        for workorder in workorder_data:
            display_name = f"WO #{workorder['WorkOrderID']} - {workorder['SiteName']}"
            self.wo_dict[display_name] = workorder["WorkOrderID"]
        self.wo_combo.clear()
        self.wo_combo.addItems(self.wo_dict.keys())
        self.wo_combo.setCurrentIndex(-1)

    def update_clock(self) -> None:
        if self.current_task_name and self.start_time is not None:
            elapsed_seconds = int(time.time() - self.start_time) + int(self.previous_seconds_today)
            hours, remainder = divmod(elapsed_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.timer_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def switch_task(self, new_task_name: str) -> None:
        emp_label = self.emp_combo.currentText()
        wo_label = self.wo_combo.currentText()

        if not emp_label:
            QMessageBox.critical(self, "Wait!", "Please select an Employee first.")
            return
        if not wo_label:
            QMessageBox.critical(self, "Wait!", "Please select a Work Order.")
            return
        if not new_task_name:
            QMessageBox.critical(self, "Wait!", "Please select a task from the dropdown before starting.")
            return

        if self.current_task_name is not None:
            self.stop_and_save(silent=True)

        self.current_task_name = new_task_name
        self.start_time = time.time()
        self.active_wo_id = self.wo_dict[wo_label]

        emp_id = self.emp_dict[emp_label]
        date_worked = int(datetime.now().strftime("%Y%m%d"))
        self.previous_seconds_today = get_todays_task_time(emp_id, new_task_name, date_worked)

        self.status_label.setText(f"Status: TRACKING '{new_task_name}'")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #27ae60;")
        self.timer.stop()
        self.update_clock()
        self.timer.start(1000)

    def stop_and_save(self, silent: bool = False) -> None:
        if self.current_task_name is None or self.start_time is None:
            if not silent:
                QMessageBox.information(self, "Idle", "No task is currently running.")
            return

        self.timer.stop()
        end_time = time.time()
        hours_worked = (end_time - self.start_time) / 3600.0
        date_worked = int(datetime.now().strftime("%Y%m%d"))
        emp_id = self.emp_dict[self.emp_combo.currentText()]
        hourly_rate = self.emp_rate_dict[emp_id]

        try:
            insert_time(
                worker_id=emp_id,
                workorder_id=self.active_wo_id,
                date_worked=date_worked,
                hours_worked=hours_worked,
                task_id=self.current_task_name,
                start_time=self.start_time,
                end_time=end_time,
                hourly_rate=hourly_rate,
            )

            if callable(self.on_time_logged):
                self.on_time_logged()

            self.current_task_name = None
            self.start_time = None
            self.previous_seconds_today = 0
            self.status_label.setText("Status: IDLE")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #2980b9;")
            self.timer_label.setText("00:00:00")
            if not silent:
                QMessageBox.information(
                    self,
                    "Saved",
                    f"Time logged successfully.\nElapsed Session: {hours_worked:.4f} hours.",
                )
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to save time:\n{exc}")


def launch_tiber(parent, on_time_logged_callback):
    TiberDialog(parent, on_time_logged_callback).exec()
