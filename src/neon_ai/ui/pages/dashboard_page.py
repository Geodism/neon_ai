from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.bootstrap import ensure_legacy_import_paths

ensure_legacy_import_paths()

from database.estimates import get_dashboard_estimates
from database.metrics import get_dashboard_metrics


class SortableTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value: float | str | None = None) -> None:
        super().__init__(text)
        self._sort_value = text if sort_value is None else sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        other_value = getattr(other, "_sort_value", other.text())
        return self._sort_value < other_value


class DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("Argon Operations Command")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

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
        self._load_workorders()
        self._load_estimates()

    def _load_workorders(self) -> None:
        try:
            rows = get_dashboard_metrics()
        except Exception as exc:
            self._show_error_row(self.wo_table, ["DB Error", str(exc), "", "", "", "", ""])
            return
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

    def _load_estimates(self) -> None:
        try:
            rows = get_dashboard_estimates()
        except Exception as exc:
            self._show_error_row(self.est_table, ["DB Error", str(exc), "", ""])
            return
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
