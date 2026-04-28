from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.bootstrap import ensure_legacy_import_paths

ensure_legacy_import_paths()

from database.customers import get_all_customers, get_sites_for_customer
from database.estimates import (
    get_all_estimate_summaries,
    get_detailed_estimate_data,
    insert_full_estimate,
    sync_estimate_pricing_from_sources,
    update_draft_estimate,
)
from database.materials import get_carried_price, search_materials
from database.roles import get_standard_roles


class LedgerTable(QTableWidget):
    def __init__(self, on_delete_callback, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._on_delete_callback = on_delete_callback

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._on_delete_callback()
            return
        super().keyPressEvent(event)


class EstimateEntryPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_estimate_id: int | None = None
        self.is_loading = False
        self.form_locked = False

        self.customer_dict: dict[str, int] = {}
        self.site_dict: dict[str, int] = {}
        self.draft_dict: dict[str, int] = {}
        self.role_data_dict: dict[str, float] = {}
        self.search_results_dict: dict[str, dict] = {}

        self.material_search_timer = QTimer(self)
        self.material_search_timer.setSingleShot(True)
        self.material_search_timer.timeout.connect(self._perform_material_search)

        self._build_ui()
        self.load_customers()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 15)
        layout.setSpacing(5)

        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_label = QLabel("NEW ESTIMATE ENTRY")
        self.header_label.setStyleSheet("font-size: 16px; font-weight: 700; color: blue;")
        header_layout.addWidget(self.header_label)
        header_layout.addStretch(1)
        load_row = QFrame()
        load_layout = QHBoxLayout(load_row)
        load_layout.setContentsMargins(0, 0, 0, 0)
        load_layout.addWidget(QLabel("Load Draft:"))
        self.draft_combo = QComboBox()
        self.draft_combo.setMinimumWidth(260)
        self.draft_combo.currentTextChanged.connect(self.load_selected_draft)
        load_layout.addWidget(self.draft_combo)
        header_layout.addWidget(load_row)
        layout.addWidget(header_frame)

        id_group = QGroupBox("Job Identification")
        id_layout = QGridLayout(id_group)
        self.customer_combo = QComboBox()
        self.customer_combo.setEditable(True)
        self.customer_combo.setMinimumWidth(240)
        self.customer_combo.lineEdit().textEdited.connect(self.autocomplete_customer)
        self.customer_combo.currentTextChanged.connect(self.load_sites)
        id_layout.addWidget(QLabel("Customer:"), 0, 0)
        id_layout.addWidget(self.customer_combo, 0, 1)

        self.site_combo = QComboBox()
        self.site_combo.setEditable(True)
        self.site_combo.setMinimumWidth(240)
        self.site_combo.lineEdit().textEdited.connect(self.autocomplete_site)
        id_layout.addWidget(QLabel("Site:"), 0, 2)
        id_layout.addWidget(self.site_combo, 0, 3)

        self.billing_combo = QComboBox()
        self.billing_combo.addItems(["Time and Materials", "Fixed Price"])
        id_layout.addWidget(QLabel("Billing Type:"), 1, 0)
        id_layout.addWidget(self.billing_combo, 1, 1)

        self.mat_markup_edit = QLineEdit("20.0")
        self.lab_markup_edit = QLineEdit("20.0")
        self.mat_markup_edit.textChanged.connect(self.recalculate_all_sell_prices)
        self.lab_markup_edit.textChanged.connect(self.recalculate_all_sell_prices)
        id_layout.addWidget(QLabel("Mat. Markup %:"), 1, 2)
        id_layout.addWidget(self.mat_markup_edit, 1, 3)
        id_layout.addWidget(QLabel("Lab. Markup %:"), 2, 2)
        id_layout.addWidget(self.lab_markup_edit, 2, 3)
        self.lbl_combined_margin = QLabel("Total Job Margin: 0.00%")
        self.lbl_combined_margin.setStyleSheet("font-weight: 700; color: blue;")
        id_layout.addWidget(self.lbl_combined_margin, 3, 2, 1, 2)
        layout.addWidget(id_group)

        scope_group = QGroupBox("Scope of Work")
        scope_layout = QVBoxLayout(scope_group)
        self.scope_text = QPlainTextEdit()
        self.scope_text.setFixedHeight(70)
        scope_layout.addWidget(self.scope_text)
        layout.addWidget(scope_group)

        labor_group = QGroupBox("Estimated Labor")
        labor_layout = QVBoxLayout(labor_group)
        labor_stage = QFrame()
        labor_stage_layout = QHBoxLayout(labor_stage)
        labor_stage_layout.setContentsMargins(0, 0, 0, 0)
        labor_stage_layout.addWidget(QLabel("Role:"))
        self.l_role = QComboBox()
        self.l_role.setMinimumWidth(160)
        self.l_role.currentTextChanged.connect(self.on_role_select)
        labor_stage_layout.addWidget(self.l_role)
        labor_stage_layout.addWidget(QLabel("Hours:"))
        self.l_hours = QLineEdit()
        self.l_hours.setMaximumWidth(70)
        labor_stage_layout.addWidget(self.l_hours)
        labor_stage_layout.addWidget(QLabel("Rate ($):"))
        self.l_rate = QLineEdit()
        self.l_rate.setMaximumWidth(80)
        labor_stage_layout.addWidget(self.l_rate)
        self.add_labor_button = QPushButton("Add Labor")
        self.add_labor_button.clicked.connect(self.add_labor)
        labor_stage_layout.addStretch(1)
        labor_stage_layout.addWidget(self.add_labor_button)
        labor_layout.addWidget(labor_stage)

        self.labor_table = LedgerTable(self.remove_labor_line, 0, 5)
        self.labor_table.setHorizontalHeaderLabels(["Role", "Hours", "Rate", "Cost Total", "Sell Total"])
        self.labor_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.labor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.labor_table.verticalHeader().setVisible(False)
        self.labor_table.setColumnWidth(0, 180)
        self.labor_table.setColumnWidth(1, 70)
        self.labor_table.setColumnWidth(2, 80)
        self.labor_table.setColumnWidth(3, 90)
        self.labor_table.setColumnWidth(4, 90)
        labor_layout.addWidget(self.labor_table)
        layout.addWidget(labor_group)

        mat_group = QGroupBox("Estimated Materials")
        mat_layout = QVBoxLayout(mat_group)
        mat_stage = QFrame()
        mat_stage_layout = QHBoxLayout(mat_stage)
        mat_stage_layout.setContentsMargins(0, 0, 0, 0)
        mat_stage_layout.addWidget(QLabel("Description:"))
        self.m_desc = QComboBox()
        self.m_desc.setEditable(True)
        self.m_desc.setMinimumWidth(300)
        self.m_desc.lineEdit().textEdited.connect(self.on_material_search)
        self.m_desc.currentTextChanged.connect(self.on_material_select)
        self.m_desc.current_item_id = None
        self.m_desc.current_part_no = None
        mat_stage_layout.addWidget(self.m_desc)
        mat_stage_layout.addWidget(QLabel("Qty:"))
        self.m_qty = QLineEdit()
        self.m_qty.setMaximumWidth(70)
        mat_stage_layout.addWidget(self.m_qty)
        mat_stage_layout.addWidget(QLabel("Unit Cost ($):"))
        self.m_cost = QLineEdit()
        self.m_cost.setMaximumWidth(80)
        mat_stage_layout.addWidget(self.m_cost)
        self.add_material_button = QPushButton("Add Material")
        self.add_material_button.clicked.connect(self.add_material)
        mat_stage_layout.addStretch(1)
        mat_stage_layout.addWidget(self.add_material_button)
        mat_layout.addWidget(mat_stage)

        self.mat_table = LedgerTable(self.remove_material_line, 0, 8)
        self.mat_table.setHorizontalHeaderLabels(
            ["Description", "Qty", "Unit Cost", "Cost Total", "Sell Total", "ItemID", "PartNumber", "EstimateMaterialID"]
        )
        self.mat_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mat_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mat_table.verticalHeader().setVisible(False)
        self.mat_table.setColumnWidth(0, 220)
        self.mat_table.setColumnWidth(1, 70)
        self.mat_table.setColumnWidth(2, 80)
        self.mat_table.setColumnWidth(3, 90)
        self.mat_table.setColumnWidth(4, 90)
        self.mat_table.setColumnHidden(5, True)
        self.mat_table.setColumnHidden(6, True)
        self.mat_table.setColumnHidden(7, True)
        mat_layout.addWidget(self.mat_table)
        layout.addWidget(mat_group)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.total_label = QLabel("ESTIMATED TOTAL: $0.00")
        self.total_label.setStyleSheet("font-size: 16px; font-weight: 700; color: green;")
        footer_layout.addWidget(self.total_label)
        footer_layout.addStretch(1)
        self.save_button = QPushButton("Save / Update Estimate")
        self.save_button.clicked.connect(self.save_data)
        footer_layout.addWidget(self.save_button)
        self.clear_button = QPushButton("Clear Form")
        self.clear_button.clicked.connect(self.clear_form)
        footer_layout.addWidget(self.clear_button)
        layout.addWidget(footer)

    def refresh_data(self) -> None:
        self.load_customers()
        self.refresh_draft_list()
        self.role_data_dict.clear()
        roles = get_standard_roles()
        for row in roles:
            burdened_rate = float(row["BaseRate"]) * (1 + (float(row["BurdenPercent"]) / 100))
            self.role_data_dict[row["RoleName"]] = burdened_rate
        self.l_role.clear()
        self.l_role.addItems(self.role_data_dict.keys())

    def on_material_search(self, _text: str) -> None:
        self.m_desc.current_item_id = None
        self.m_desc.current_part_no = None
        self.material_search_timer.start(250)

    def _perform_material_search(self) -> None:
        search_text = self.m_desc.currentText().strip()
        if len(search_text) < 2:
            self.search_results_dict.clear()
            self.m_desc.blockSignals(True)
            self.m_desc.clear()
            self.m_desc.setEditText(search_text)
            self.m_desc.blockSignals(False)
            return

        results = search_materials(search_text)
        self.search_results_dict.clear()
        display_list = []
        for row in results:
            part_number = row.get("PartNumber") or "No Part #"
            display_str = f"{part_number} | {row['Description']}"
            display_list.append(display_str)
            self.search_results_dict[display_str] = row

        self.m_desc.blockSignals(True)
        self.m_desc.clear()
        self.m_desc.addItems(display_list)
        self.m_desc.setEditText(search_text)
        self.m_desc.blockSignals(False)
        if display_list and self.m_desc.hasFocus():
            self.m_desc.showPopup()

    def on_material_select(self, _value: str | None = None) -> None:
        selection = self.m_desc.currentText()
        if selection in self.search_results_dict:
            material = self.search_results_dict[selection]
            price = get_carried_price(material)
            self.m_cost.setText(f"{price:.2f}")
            self.m_desc.current_item_id = material["ItemID"]
            self.m_desc.current_part_no = material["PartNumber"]

    def on_role_select(self, selected_role: str) -> None:
        if selected_role in self.role_data_dict:
            self.l_rate.setText(f"{self.role_data_dict[selected_role]:.2f}")

    def load_customers(self) -> None:
        records = get_all_customers()
        self.customer_dict = {row["CustomerName"]: row["CustomerID"] for row in records}
        current_text = self.customer_combo.currentText()
        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        self.customer_combo.addItems(self.customer_dict.keys())
        self.customer_combo.setEditText(current_text)
        self.customer_combo.blockSignals(False)

    def refresh_draft_list(self) -> None:
        all_estimates = get_all_estimate_summaries()
        self.draft_dict = {
            f"{row['EstimateID']} - {row['SiteName']} [{row['Status']}]": row["EstimateID"] for row in all_estimates
        }
        current_text = self.draft_combo.currentText()
        self.draft_combo.blockSignals(True)
        self.draft_combo.clear()
        self.draft_combo.addItems(self.draft_dict.keys())
        self.draft_combo.setCurrentText(current_text)
        self.draft_combo.blockSignals(False)

    def load_selected_draft(self, selection: str) -> None:
        if selection not in self.draft_dict:
            return

        self.is_loading = True
        try:
            est_id = self.draft_dict[selection]
            self.current_estimate_id = est_id
            sync_estimate_pricing_from_sources(est_id)
            data = get_detailed_estimate_data(est_id)
            parent = data["parent"]

            self.customer_combo.setCurrentText(parent.get("CustomerName") or "")
            self.load_sites()
            self.site_combo.setCurrentText(parent.get("SiteName") or "")
            self.billing_combo.setCurrentText(parent.get("BillingType") or "Time and Materials")
            self.scope_text.setPlainText(parent.get("Description") or "")

            lab_mu_val = parent.get("LaborMarkUp") or 20.0
            mat_mu_val = parent.get("MaterialMarkUp") or 20.0
            self.lab_markup_edit.setText(str(lab_mu_val))
            self.mat_markup_edit.setText(str(mat_mu_val))

            l_mu = float(lab_mu_val) / 100
            m_mu = float(mat_mu_val) / 100

            self.labor_table.setRowCount(0)
            for row in data["labor"]:
                role = row.get("RoleDescription") or "Labor"
                hours = float(row.get("Hours") or 0)
                rate = float(row.get("Rate") or 0)
                cost = float(row.get("LineTotal") or (hours * rate))
                sell = cost * (1 + l_mu)
                self._append_labor_row(role, hours, rate, cost, sell)

            self.mat_table.setRowCount(0)
            for row in data["materials"]:
                desc = row.get("Description") or "Material"
                qty = float(row.get("Quantity") or 0)
                unit_cost = float(row.get("UnitCost") or 0)
                cost = float(row.get("LineTotal") or (qty * unit_cost))
                sell = cost * (1 + m_mu)
                self._append_material_row(
                    desc,
                    qty,
                    unit_cost,
                    cost,
                    sell,
                    row.get("ItemID"),
                    row.get("PartNumber"),
                    row.get("EstimateMaterialID"),
                )

            self.update_grand_total()
            status = parent.get("Status", "Draft")
            display_text = f"ESTIMATE #{est_id}  [{str(status).upper()}]"
            normalized = str(status).strip().lower()
            if normalized in ["submitted", "locked", "sent", "accepted"]:
                self.toggle_form_lock(True)
                self.header_label.setText(display_text)
                self.header_label.setStyleSheet("font-size: 16px; font-weight: 700; color: red;")
            else:
                self.toggle_form_lock(False)
                self.header_label.setText(display_text)
                self.header_label.setStyleSheet("font-size: 16px; font-weight: 700; color: orange;")
        finally:
            self.is_loading = False

    def toggle_form_lock(self, lock: bool = True) -> None:
        self.form_locked = lock
        enabled = not lock
        for widget in [
            self.customer_combo,
            self.site_combo,
            self.billing_combo,
            self.mat_markup_edit,
            self.lab_markup_edit,
            self.scope_text,
            self.l_role,
            self.l_hours,
            self.l_rate,
            self.m_desc,
            self.m_qty,
            self.m_cost,
            self.add_labor_button,
            self.add_material_button,
            self.save_button,
        ]:
            widget.setEnabled(enabled)

    def load_sites(self, _value: str | None = None) -> None:
        customer_name = self.customer_combo.currentText()
        self.site_dict.clear()
        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        if customer_name in self.customer_dict:
            customer_id = self.customer_dict[customer_name]
            records = get_sites_for_customer(customer_id)
            self.site_dict = {row["SiteName"]: row["SiteID"] for row in records}
            self.site_combo.addItems(self.site_dict.keys())
        self.site_combo.blockSignals(False)

    def autocomplete_customer(self, typed_text: str) -> None:
        hits = list(self.customer_dict.keys()) if not typed_text else [
            name for name in self.customer_dict.keys() if typed_text.lower() in name.lower()
        ]
        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        self.customer_combo.addItems(hits)
        self.customer_combo.setEditText(typed_text)
        self.customer_combo.blockSignals(False)
        if hits:
            self.customer_combo.showPopup()

    def autocomplete_site(self, typed_text: str) -> None:
        hits = list(self.site_dict.keys()) if not typed_text else [
            name for name in self.site_dict.keys() if typed_text.lower() in name.lower()
        ]
        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        self.site_combo.addItems(hits)
        self.site_combo.setEditText(typed_text)
        self.site_combo.blockSignals(False)
        if hits:
            self.site_combo.showPopup()

    def _append_labor_row(self, role: str, hours: float, rate: float, cost_total: float, sell_total: float) -> None:
        row = self.labor_table.rowCount()
        self.labor_table.insertRow(row)
        values = [role, f"{hours:.2f}", f"${rate:.2f}", f"${cost_total:,.2f}", f"${sell_total:,.2f}"]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in (1, 2, 3, 4):
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter if column == 1 else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            self.labor_table.setItem(row, column, item)

    def _append_material_row(
        self,
        desc: str,
        qty: float,
        unit_cost: float,
        cost_total: float,
        sell_total: float,
        item_id,
        part_no,
        est_mat_id,
    ) -> None:
        row = self.mat_table.rowCount()
        self.mat_table.insertRow(row)
        values = [
            desc,
            f"{qty:.2f}",
            f"${unit_cost:.2f}",
            f"${cost_total:,.2f}",
            f"${sell_total:,.2f}",
            "" if item_id is None else str(item_id),
            "" if part_no is None else str(part_no),
            "" if est_mat_id is None else str(est_mat_id),
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in (1, 2, 3, 4):
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter if column == 1 else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            self.mat_table.setItem(row, column, item)

    def _parse_currency(self, text: str) -> float:
        return float(str(text).replace("$", "").replace(",", "").strip() or 0)

    def recalculate_all_sell_prices(self, *_args) -> None:
        if self.is_loading or self.form_locked:
            return
        try:
            l_mu = float(self.lab_markup_edit.text() or 0) / 100
            m_mu = float(self.mat_markup_edit.text() or 0) / 100

            for row in range(self.labor_table.rowCount()):
                cost = self._parse_currency(self.labor_table.item(row, 3).text())
                self.labor_table.item(row, 4).setText(f"${(cost * (1 + l_mu)):,.2f}")

            for row in range(self.mat_table.rowCount()):
                cost = self._parse_currency(self.mat_table.item(row, 3).text())
                self.mat_table.item(row, 4).setText(f"${(cost * (1 + m_mu)):,.2f}")

            self.update_grand_total()
        except ValueError:
            pass

    def update_margin_display(self) -> None:
        try:
            total_mat_cost = sum(self._parse_currency(self.mat_table.item(row, 3).text()) for row in range(self.mat_table.rowCount()))
            total_lab_cost = sum(self._parse_currency(self.labor_table.item(row, 3).text()) for row in range(self.labor_table.rowCount()))
            mat_mu = float(self.mat_markup_edit.text() or 0) / 100
            lab_mu = float(self.lab_markup_edit.text() or 0) / 100
            mat_profit = total_mat_cost * mat_mu
            lab_profit = total_lab_cost * lab_mu
            total_sell = (total_mat_cost + mat_profit) + (total_lab_cost + lab_profit)
            total_profit = mat_profit + lab_profit
            margin = (total_profit / total_sell) * 100 if total_sell > 0 else 0
            self.lbl_combined_margin.setText(f"Total Job Margin: {margin:.2f}%")
        except ValueError:
            pass

    def update_grand_total(self) -> None:
        total = 0.0
        for row in range(self.labor_table.rowCount()):
            total += self._parse_currency(self.labor_table.item(row, 4).text())
        for row in range(self.mat_table.rowCount()):
            total += self._parse_currency(self.mat_table.item(row, 4).text())
        self.total_label.setText(f"ESTIMATED TOTAL: ${total:,.2f}")
        self.update_margin_display()

    def add_labor(self) -> None:
        if self.form_locked:
            return
        try:
            role = self.l_role.currentText()
            hours = float(self.l_hours.text() or 0)
            rate = float(self.l_rate.text() or 0)
            if not role or hours <= 0:
                return
            cost_total = hours * rate
            sell_total = cost_total * (1 + (float(self.lab_markup_edit.text() or 0) / 100))
            self._append_labor_row(role, hours, rate, cost_total, sell_total)
            self.update_grand_total()
        except ValueError:
            QMessageBox.critical(self, "Error", "Check your numbers.")

    def add_material(self) -> None:
        if self.form_locked:
            return
        try:
            desc = self.m_desc.currentText()
            qty = float(self.m_qty.text() or 0)
            cost = float(self.m_cost.text() or 0)
            if not desc or qty <= 0:
                return
            cost_total = qty * cost
            mat_markup = float(self.mat_markup_edit.text() or 0)
            sell_total = cost_total * (1 + (mat_markup / 100))
            item_id = getattr(self.m_desc, "current_item_id", None)
            part_no = getattr(self.m_desc, "current_part_no", None)
            self._append_material_row(desc, qty, cost, cost_total, sell_total, item_id, part_no, None)
            self.m_desc.setCurrentText("")
            self.m_qty.setText("")
            self.m_cost.setText("")
            self.m_desc.current_item_id = None
            self.m_desc.current_part_no = None
            self.update_grand_total()
        except ValueError:
            QMessageBox.critical(self, "Error", "Quantity and Unit Cost must be numbers.")

    def remove_labor_line(self) -> None:
        if self.form_locked:
            return
        rows = sorted({item.row() for item in self.labor_table.selectedItems()}, reverse=True)
        for row in rows:
            self.labor_table.removeRow(row)
        self.update_grand_total()

    def remove_material_line(self) -> None:
        if self.form_locked:
            return
        rows = sorted({item.row() for item in self.mat_table.selectedItems()}, reverse=True)
        for row in rows:
            self.mat_table.removeRow(row)
        self.update_grand_total()

    def save_data(self) -> None:
        if self.form_locked:
            QMessageBox.information(self, "Locked", "This estimate is locked and cannot be changed.")
            return
        try:
            site_name = self.site_combo.currentText()
            desc = self.scope_text.toPlainText().strip()
            billing_type = self.billing_combo.currentText()
            mat_markup = float(self.mat_markup_edit.text() or 0.0)
            lab_markup = float(self.lab_markup_edit.text() or 0.0)

            site_id = self.site_dict.get(site_name)
            if not site_id:
                QMessageBox.critical(self, "Error", "Please select a valid Site from the list.")
                return

            labor_lines = []
            for row in range(self.labor_table.rowCount()):
                labor_lines.append(
                    [
                        self.labor_table.item(row, 0).text(),
                        float(self.labor_table.item(row, 1).text()),
                        self._parse_currency(self.labor_table.item(row, 2).text()),
                        self._parse_currency(self.labor_table.item(row, 3).text()),
                    ]
                )

            material_lines = []
            for row in range(self.mat_table.rowCount()):
                item_id_text = self.mat_table.item(row, 5).text()
                part_no_text = self.mat_table.item(row, 6).text()
                est_mat_id_text = self.mat_table.item(row, 7).text()
                material_lines.append(
                    [
                        self.mat_table.item(row, 0).text(),
                        float(self.mat_table.item(row, 1).text()),
                        self._parse_currency(self.mat_table.item(row, 2).text()),
                        self._parse_currency(self.mat_table.item(row, 3).text()),
                        int(item_id_text) if item_id_text not in ("", "None") else None,
                        part_no_text if part_no_text not in ("", "None") else None,
                        int(est_mat_id_text) if est_mat_id_text not in ("", "None") else None,
                    ]
                )

            if self.current_estimate_id:
                update_draft_estimate(
                    self.current_estimate_id,
                    site_id,
                    desc,
                    billing_type,
                    lab_markup,
                    mat_markup,
                    labor_lines,
                    material_lines,
                )
                QMessageBox.information(self, "Success", f"Draft #{self.current_estimate_id} successfully updated!")
                saved_estimate_id = self.current_estimate_id
            else:
                new_id = insert_full_estimate(
                    site_id,
                    desc,
                    billing_type,
                    lab_markup,
                    mat_markup,
                    labor_lines,
                    material_lines,
                )
                QMessageBox.information(self, "Success", f"Estimate #{new_id} saved perfectly to the database!")
                saved_estimate_id = new_id

            self.refresh_draft_list()
            self.load_saved_estimate(saved_estimate_id)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Failed to save: {exc}")
            print(f"CRITICAL SAVE ERROR: {exc}")

    def clear_form(self) -> None:
        self.labor_table.setRowCount(0)
        self.mat_table.setRowCount(0)
        self.customer_combo.setCurrentText("")
        self.site_combo.setCurrentText("")
        self.scope_text.setPlainText("")
        self.mat_markup_edit.setText("20.0")
        self.lab_markup_edit.setText("20.0")
        self.update_grand_total()
        self.current_estimate_id = None
        self.header_label.setText("NEW ESTIMATE ENTRY")
        self.header_label.setStyleSheet("font-size: 16px; font-weight: 700; color: blue;")
        self.toggle_form_lock(False)

    def load_saved_estimate(self, estimate_id: int) -> None:
        for label, est_id in self.draft_dict.items():
            if est_id == estimate_id:
                self.draft_combo.setCurrentText(label)
                self.load_selected_draft(label)
                return
