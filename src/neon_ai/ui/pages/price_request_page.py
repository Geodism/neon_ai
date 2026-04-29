from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.estimates import add_single_material, get_all_estimates, get_estimate_materials
from neon_ai.database.rfq import get_active_rfqs, get_all_vendors, get_quoted_vendors_for_estimate, save_rfq_package


class PriceRequestPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_estimate_id: int | None = None
        self.estimates: list[dict] = []
        self._legacy_refs = (
            save_rfq_package,
            get_all_vendors,
            get_quoted_vendors_for_estimate,
            get_active_rfqs,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 15)
        layout.setSpacing(10)

        header = QLabel("Estimate Material Manager")
        header.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(header, 0, Qt.AlignmentFlag.AlignLeft)

        control_row = QWidget()
        control_layout = QHBoxLayout(control_row)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.addWidget(QLabel("Select Estimate:"))

        self.estimate_combo = QComboBox()
        self.estimate_combo.setMinimumWidth(360)
        self.estimate_combo.currentTextChanged.connect(self.on_estimate_select)
        control_layout.addWidget(self.estimate_combo)
        control_layout.addStretch(1)
        layout.addWidget(control_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        top_panel = QWidget()
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(QLabel("Current Estimate Materials"))
        self.existing_table = QTableWidget(0, 2)
        self.existing_table.setHorizontalHeaderLabels(["Quantity", "Material Description"])
        self.existing_table.setColumnWidth(0, 100)
        self.existing_table.horizontalHeader().setStretchLastSection(True)
        self.existing_table.verticalHeader().setVisible(False)
        self.existing_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        top_layout.addWidget(self.existing_table)
        splitter.addWidget(top_panel)

        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(QLabel("Add Missing Materials"))

        stage_row = QWidget()
        stage_layout = QHBoxLayout(stage_row)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.addWidget(QLabel("Qty:"))
        self.qty_field = QLineEdit()
        self.qty_field.setMaximumWidth(80)
        stage_layout.addWidget(self.qty_field)
        stage_layout.addWidget(QLabel("Description:"))
        self.desc_field = QLineEdit()
        self.desc_field.setMinimumWidth(260)
        stage_layout.addWidget(self.desc_field)
        stage_button = QPushButton("Stage Material")
        stage_button.clicked.connect(self.stage_material)
        stage_layout.addWidget(stage_button)
        bottom_layout.addWidget(stage_row)

        self.staging_table = QTableWidget(0, 2)
        self.staging_table.setHorizontalHeaderLabels(["Quantity", "Material Description"])
        self.staging_table.setColumnWidth(0, 100)
        self.staging_table.horizontalHeader().setStretchLastSection(True)
        self.staging_table.verticalHeader().setVisible(False)
        self.staging_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        bottom_layout.addWidget(self.staging_table)
        splitter.addWidget(bottom_panel)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.addStretch(1)
        self.save_new_button = QPushButton("Save Staged Items to Estimate")
        self.save_new_button.clicked.connect(self.save_staged_materials)
        self.save_new_button.setEnabled(False)
        footer_layout.addWidget(self.save_new_button)
        layout.addWidget(footer)

    def load_estimates(self) -> None:
        try:
            self.estimates = get_all_estimates()
            self.estimate_combo.blockSignals(True)
            self.estimate_combo.clear()
            if self.estimates:
                self.estimate_combo.addItems([f"{row['EstimateID']} - {row['Description']}" for row in self.estimates])
                self.estimate_combo.setCurrentIndex(-1)
            self.estimate_combo.blockSignals(False)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load estimates: {exc}")

    def refresh_data(self) -> None:
        self.current_estimate_id = None
        self.load_estimates()
        self.existing_table.setRowCount(0)
        self.staging_table.setRowCount(0)
        self.qty_field.setText("")
        self.desc_field.setText("")
        self.save_new_button.setEnabled(False)

    def on_estimate_select(self, selection: str) -> None:
        if not selection:
            return
        self.current_estimate_id = int(selection.split(" - ")[0])
        self.refresh_existing_grid()
        self.save_new_button.setEnabled(True)

    def refresh_existing_grid(self) -> None:
        self.existing_table.setRowCount(0)
        if not self.current_estimate_id:
            return

        try:
            materials = get_estimate_materials(self.current_estimate_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load materials: {exc}")
            return

        for row in materials:
            table_row = self.existing_table.rowCount()
            self.existing_table.insertRow(table_row)
            qty = row.get("Quantity") or row.get("quantity") or 0
            desc = row.get("Description") or row.get("description") or "No Description"
            self.existing_table.setItem(table_row, 0, QTableWidgetItem(str(qty)))
            self.existing_table.setItem(table_row, 1, QTableWidgetItem(str(desc)))

    def stage_material(self) -> None:
        if not self.current_estimate_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return

        qty_str = self.qty_field.text().strip()
        desc = self.desc_field.text().strip()
        if not qty_str or not desc:
            return

        try:
            qty = float(qty_str)
        except ValueError:
            QMessageBox.critical(self, "Format Error", "Quantity must be a number.")
            return

        row = self.staging_table.rowCount()
        self.staging_table.insertRow(row)
        self.staging_table.setItem(row, 0, QTableWidgetItem(f"{qty:.2f}"))
        self.staging_table.setItem(row, 1, QTableWidgetItem(desc))
        self.qty_field.setText("")
        self.desc_field.setText("")
        self.qty_field.setFocus()

    def save_staged_materials(self) -> None:
        if self.staging_table.rowCount() == 0:
            QMessageBox.information(self, "Empty", "No new materials staged to save.")
            return

        try:
            for row in range(self.staging_table.rowCount()):
                qty = float(self.staging_table.item(row, 0).text())
                desc = self.staging_table.item(row, 1).text()
                add_single_material(self.current_estimate_id, desc, qty)

            QMessageBox.information(self, "Success", "New materials saved to the database.")
            self.staging_table.setRowCount(0)
            self.refresh_existing_grid()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to save items: {exc}")
