from __future__ import annotations

import time
import traceback

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.estimates import get_dashboard_estimates
from neon_ai.database.metrics import get_dashboard_metrics


class SortableTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value: float | str | None = None) -> None:
        super().__init__(text)
        self._sort_value = text if sort_value is None else sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        other_value = getattr(other, "_sort_value", other.text())
        return self._sort_value < other_value


class DashboardLoadWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        started = time.perf_counter()
        try:
            payload = {
                "workorders": {"rows": None, "error": None},
                "estimates": {"rows": None, "error": None},
            }

            try:
                payload["workorders"]["rows"] = get_dashboard_metrics()
            except Exception as exc:
                payload["workorders"]["error"] = str(exc)
                traceback.print_exc()

            try:
                payload["estimates"]["rows"] = get_dashboard_estimates()
            except Exception as exc:
                payload["estimates"]["error"] = str(exc)
                traceback.print_exc()

            self.finished.emit(payload)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"[PERF] area=worker name=dashboard_page.load_data elapsed_ms={elapsed_ms:.2f}")


class DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._refresh_thread: QThread | None = None
        self._refresh_worker: DashboardLoadWorker | None = None
        self._is_refreshing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("Argon Operations Command")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_data)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.status_label)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        projects_group = QGroupBox("Active Projects (Financial Health)")
        projects_layout = QVBoxLayout(projects_group)
        self.wo_table = QTableWidget(0, 7)
        self.wo_table.setHorizontalHeaderLabels(
            ["ID", "Project", "Value", "Labor", "Materials", "Total Cost", "Difference"]
        )
        self.wo_table.setSortingEnabled(True)
        self.wo_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.wo_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.wo_table.verticalHeader().setVisible(False)
        self.wo_table.horizontalHeader().setStretchLastSection(False)
        self.wo_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.wo_table.setColumnWidth(0, 50)
        self.wo_table.setColumnWidth(2, 100)
        self.wo_table.setColumnWidth(3, 100)
        self.wo_table.setColumnWidth(4, 100)
        self.wo_table.setColumnWidth(5, 100)
        self.wo_table.setColumnWidth(6, 100)
        projects_layout.addWidget(self.wo_table)
        splitter.addWidget(projects_group)

        estimates_group = QGroupBox("Estimates Pipeline")
        estimates_layout = QVBoxLayout(estimates_group)
        self.est_table = QTableWidget(0, 4)
        self.est_table.setHorizontalHeaderLabels(["ID", "Customer", "Site", "Scope of Work"])
        self.est_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.est_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.est_table.verticalHeader().setVisible(False)
        self.est_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.est_table.setColumnWidth(0, 60)
        self.est_table.setColumnWidth(1, 150)
        self.est_table.setColumnWidth(2, 150)
        estimates_layout.addWidget(self.est_table)
        splitter.addWidget(estimates_group)

        splitter.setSizes([450, 250])

    def refresh_data(self) -> None:
        started = time.perf_counter()
        try:
            if self._is_refreshing:
                return

            self._is_refreshing = True
            self.refresh_button.setEnabled(False)
            self.status_label.setText("Loading dashboard...")

            self._refresh_thread = QThread(self)
            self._refresh_worker = DashboardLoadWorker()
            self._refresh_worker.moveToThread(self._refresh_thread)
            self._refresh_thread.started.connect(self._refresh_worker.run)
            self._refresh_worker.finished.connect(self._on_refresh_loaded)
            self._refresh_worker.failed.connect(self._on_refresh_failed)
            self._refresh_worker.finished.connect(self._refresh_thread.quit)
            self._refresh_worker.failed.connect(self._refresh_thread.quit)
            self._refresh_thread.finished.connect(
                lambda thread=self._refresh_thread, worker=self._refresh_worker: self._cleanup_refresh_worker(
                    thread, worker
                )
            )
            self._refresh_thread.start()
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"[PERF] area=page name=dashboard_page.refresh_data elapsed_ms={elapsed_ms:.2f}")

    def _on_refresh_loaded(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}

        workorders = data.get("workorders", {})
        workorder_error = workorders.get("error")
        if workorder_error:
            self._show_error_row(self.wo_table, ["DB Error", str(workorder_error), "", "", "", "", ""])
        else:
            self._populate_workorders(workorders.get("rows") or [])

        estimates = data.get("estimates", {})
        estimate_error = estimates.get("error")
        if estimate_error:
            self._show_error_row(self.est_table, ["DB Error", str(estimate_error), "", ""])
        else:
            self._populate_estimates(estimates.get("rows") or [])

        status_parts: list[str] = []
        if workorder_error:
            status_parts.append("work orders failed")
        if estimate_error:
            status_parts.append("estimates failed")
        self.status_label.setText("; ".join(status_parts) if status_parts else "")

    def _on_refresh_failed(self, error_message: str) -> None:
        print(error_message)
        self._show_error_row(self.wo_table, ["DB Error", error_message, "", "", "", "", ""])
        self._show_error_row(self.est_table, ["DB Error", error_message, "", ""])
        self.status_label.setText("Dashboard refresh failed.")

    def _cleanup_refresh_worker(self, thread: QThread | None, worker: DashboardLoadWorker | None) -> None:
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()
        if self._refresh_worker is worker:
            self._refresh_worker = None
        if self._refresh_thread is thread:
            self._refresh_thread = None
        self._is_refreshing = False
        self.refresh_button.setEnabled(True)

    def _populate_workorders(self, rows: list[dict]) -> None:
        self.wo_table.setSortingEnabled(False)
        self.wo_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            value_num = float(row.get("WorkOrderValue") or 0.0)
            labor_num = float(row.get("LaborCost") or 0.0)
            mat_num = float(row.get("MaterialCost") or 0.0)
            total_cost = labor_num + mat_num
            difference = value_num - total_cost
            values = [
                (str(row["WorkOrderID"]), int(row["WorkOrderID"]), Qt.AlignmentFlag.AlignCenter),
                (f"{row['CustomerName']} ({row['SiteName']})", None, Qt.AlignmentFlag.AlignLeft),
                (f"${value_num:,.2f}", value_num, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (f"${labor_num:,.2f}", labor_num, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (f"${mat_num:,.2f}", mat_num, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (f"${total_cost:,.2f}", total_cost, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (f"${difference:,.2f}", difference, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            ]
            for col_index, (text, sort_value, alignment) in enumerate(values):
                item = SortableTableWidgetItem(text, text if sort_value is None else sort_value)
                item.setTextAlignment(alignment)
                self.wo_table.setItem(row_index, col_index, item)
        self.wo_table.setSortingEnabled(True)

    def _populate_estimates(self, rows: list[dict]) -> None:
        self.est_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                (str(row["EstimateID"]), Qt.AlignmentFlag.AlignCenter),
                (str(row["CustomerName"]), Qt.AlignmentFlag.AlignLeft),
                (str(row["SiteName"]), Qt.AlignmentFlag.AlignLeft),
                (str(row["Description"]), Qt.AlignmentFlag.AlignLeft),
            )
            for col_index, (value, alignment) in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(alignment)
                self.est_table.setItem(row_index, col_index, item)

    def _show_error_row(self, table: QTableWidget, values: list[str]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(1)
        for col_index, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col_index == 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(0, col_index, item)
