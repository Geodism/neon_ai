from __future__ import annotations

import time
import traceback

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHeaderView,
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

from neon_ai.database.materials import (
    get_carry_source_choices,
    get_material_by_id,
    get_material_pipeline,
    get_material_price_history,
    save_material,
)


class MaterialLoadWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, search_text: str, include_inactive: bool) -> None:
        super().__init__()
        self._search_text = search_text
        self._include_inactive = include_inactive

    def run(self) -> None:
        started = time.perf_counter()
        try:
            rows = get_material_pipeline(
                search_text=self._search_text,
                include_inactive=self._include_inactive,
            )
            self.finished.emit(rows)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"[PERF] area=worker name=material_page.load_data elapsed_ms={elapsed_ms:.2f}")


class MaterialPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_item_id: int | None = None
        self.material_lookup: dict[str, dict] = {}
        self._loading_material = False
        self._refresh_thread: QThread | None = None
        self._refresh_worker: MaterialLoadWorker | None = None
        self._is_refreshing = False
        self._pending_refresh = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh_data)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Material Catalog Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
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

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_pipeline_panel())
        splitter.addWidget(self._build_workspace_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

    def _build_pipeline_panel(self) -> QGroupBox:
        group = QGroupBox("Materials Pipeline")
        layout = QVBoxLayout(group)

        search_row = QFrame()
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.addWidget(QLabel("Search"))
        self.search_field = QLineEdit()
        self.search_field.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self.search_field, 1)
        self.show_inactive_checkbox = QCheckBox("Show Inactive")
        self.show_inactive_checkbox.setChecked(True)
        self.show_inactive_checkbox.toggled.connect(self.refresh_data)
        search_layout.addWidget(self.show_inactive_checkbox)
        layout.addWidget(search_row)

        self.pipeline_table = QTableWidget(0, 7)
        self.pipeline_table.setHorizontalHeaderLabels(["ItemID", "PartNumber", "Description", "Carry", "Price", "Unit", "Active"])
        self.pipeline_table.setColumnHidden(0, True)
        self.pipeline_table.setSortingEnabled(True)
        self.pipeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pipeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pipeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pipeline_table.verticalHeader().setVisible(False)
        header = self.pipeline_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.pipeline_table.setColumnWidth(1, 130)
        self.pipeline_table.setColumnWidth(3, 90)
        self.pipeline_table.setColumnWidth(4, 90)
        self.pipeline_table.setColumnWidth(5, 80)
        self.pipeline_table.setColumnWidth(6, 70)
        self.pipeline_table.itemSelectionChanged.connect(self._on_material_select)
        layout.addWidget(self.pipeline_table)
        return group

    def _build_workspace_panel(self) -> QGroupBox:
        group = QGroupBox("Material Workspace")
        layout = QVBoxLayout(group)

        self.mode_label = QLabel("Mode: New material")
        self.mode_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.mode_label)

        form = QFrame()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)

        self.part_number_field = QLineEdit()
        self.description_field = QLineEdit()
        self.unit_field = QLineEdit()
        self.carry_source_combo = QComboBox()
        try:
            self.carry_source_combo.addItems(get_carry_source_choices())
        except Exception:
            self.carry_source_combo.addItems(["Internal", "Nedco", "Gescan", "Eecol", "Guillevin"])
        self.carry_source_combo.currentTextChanged.connect(self._update_current_price_preview)
        self.is_active_checkbox = QCheckBox("Active material")
        self.is_active_checkbox.setChecked(True)

        row = QFrame()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("Part Number"))
        row_layout.addWidget(self.part_number_field, 1)
        row_layout.addWidget(QLabel("Description *"))
        row_layout.addWidget(self.description_field, 2)
        form_layout.addWidget(row)

        row = QFrame()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("Unit"))
        row_layout.addWidget(self.unit_field, 1)
        row_layout.addWidget(QLabel("Carry Price Source"))
        row_layout.addWidget(self.carry_source_combo, 1)
        row_layout.addStretch(1)
        form_layout.addWidget(row)

        row = QFrame()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.is_active_checkbox)
        self.current_price_label = QLabel("Current carried price: $0.00")
        self.current_price_label.setStyleSheet("color: blue;")
        row_layout.addWidget(self.current_price_label)
        row_layout.addStretch(1)
        form_layout.addWidget(row)
        layout.addWidget(form)

        pricing_group = QGroupBox("Price Grid")
        pricing_layout = QVBoxLayout(pricing_group)

        self.price_fields = {
            "internal_price": QLineEdit(),
            "nedco_price": QLineEdit(),
            "gescan_price": QLineEdit(),
            "eecol_price": QLineEdit(),
            "guillevin_price": QLineEdit(),
        }
        self.vendor_part_fields = {
            "nedco_part_number": QLineEdit(),
            "gescan_part_number": QLineEdit(),
            "eecol_part_number": QLineEdit(),
            "guillevin_part_number": QLineEdit(),
        }
        self.date_labels = {
            "Nedco": QLabel(""),
            "Gescan": QLabel(""),
            "Eecol": QLabel(""),
            "Guillevin": QLabel(""),
        }
        for field in self.price_fields.values():
            field.textChanged.connect(self._update_current_price_preview)

        header_row = QFrame()
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        for text, stretch in (("Source", 1), ("Unit Price", 1), ("Vendor Part #", 1), ("Last Price Date", 1)):
            label = QLabel(text)
            header_layout.addWidget(label, stretch)
        pricing_layout.addWidget(header_row)

        row_defs = [
            ("Internal", "internal_price", None),
            ("Nedco", "nedco_price", "nedco_part_number"),
            ("Gescan", "gescan_price", "gescan_part_number"),
            ("Eecol", "eecol_price", "eecol_part_number"),
            ("Guillevin", "guillevin_price", "guillevin_part_number"),
        ]
        for label_text, price_key, part_key in row_defs:
            row = QFrame()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(QLabel(label_text), 1)
            row_layout.addWidget(self.price_fields[price_key], 1)
            if part_key:
                row_layout.addWidget(self.vendor_part_fields[part_key], 1)
                row_layout.addWidget(self.date_labels[label_text], 1)
            else:
                row_layout.addWidget(QLabel("Internal baseline"), 2)
            pricing_layout.addWidget(row)

        layout.addWidget(pricing_group)

        button_row = QFrame()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        new_button = QPushButton("New Material")
        new_button.clicked.connect(self._prepare_new_material)
        button_layout.addWidget(new_button)
        button_layout.addStretch(1)
        save_button = QPushButton("Save Material")
        save_button.clicked.connect(self._save_material_record)
        button_layout.addWidget(save_button)
        layout.addWidget(button_row)

        history_group = QGroupBox("Recent Price History")
        history_layout = QVBoxLayout(history_group)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["Captured", "Source", "Vendor", "Price", "Quote", "RFQ"])
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        history_header = self.history_table.horizontalHeader()
        history_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.history_table.setColumnWidth(0, 135)
        self.history_table.setColumnWidth(1, 120)
        self.history_table.setColumnWidth(3, 90)
        self.history_table.setColumnWidth(4, 100)
        self.history_table.setColumnWidth(5, 70)
        history_layout.addWidget(self.history_table)
        layout.addWidget(history_group, 1)
        return group

    def _on_search_changed(self) -> None:
        self._search_timer.start(250)

    def refresh_data(self) -> None:
        started = time.perf_counter()
        try:
            if self._is_refreshing:
                self._pending_refresh = True
                return

            self._is_refreshing = True
            self._pending_refresh = False
            self.refresh_button.setEnabled(False)
            self.status_label.setText("Loading materials...")

            search_text = self.search_field.text().strip()
            include_inactive = self.show_inactive_checkbox.isChecked()
            selected_item_id = self.active_item_id

            self._refresh_thread = QThread(self)
            self._refresh_worker = MaterialLoadWorker(search_text, include_inactive)
            self._refresh_worker.moveToThread(self._refresh_thread)
            self._refresh_thread.started.connect(self._refresh_worker.run)
            self._refresh_worker.finished.connect(
                lambda rows, selected_item_id=selected_item_id: self._on_refresh_loaded(rows, selected_item_id)
            )
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
            print(f"[PERF] area=page name=material_page.refresh_data elapsed_ms={elapsed_ms:.2f}")

    def _on_refresh_loaded(self, rows: object, selected_item_id: int | None) -> None:
        pipeline_rows = list(rows) if isinstance(rows, list) else []
        self.material_lookup.clear()
        self.pipeline_table.setSortingEnabled(False)
        self.pipeline_table.setRowCount(0)

        for row in pipeline_rows:
            item_id = int(row["ItemID"])
            self.material_lookup[str(item_id)] = row
            table_row = self.pipeline_table.rowCount()
            self.pipeline_table.insertRow(table_row)
            values = (
                (str(item_id), Qt.AlignmentFlag.AlignCenter),
                (row.get("PartNumber") or "", Qt.AlignmentFlag.AlignLeft),
                (row.get("Description") or "", Qt.AlignmentFlag.AlignLeft),
                (row.get("CarryPriceSource") or "Internal", Qt.AlignmentFlag.AlignCenter),
                (f"${float(row.get('CurrentPrice') or 0):,.2f}", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (row.get("Unit") or "", Qt.AlignmentFlag.AlignCenter),
                ("Yes" if row.get("IsActive", True) else "No", Qt.AlignmentFlag.AlignCenter),
            )
            for col_index, (text, alignment) in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(alignment)
                self.pipeline_table.setItem(table_row, col_index, item)

        self.pipeline_table.setSortingEnabled(True)
        self.status_label.setText("")

        if selected_item_id and str(selected_item_id) in self.material_lookup:
            for row in range(self.pipeline_table.rowCount()):
                current_item = self.pipeline_table.item(row, 0)
                if current_item and current_item.text() == str(selected_item_id):
                    self.pipeline_table.selectRow(row)
                    self._on_material_select()
                    break
        elif not self.active_item_id:
            self._prepare_new_material()

    def _on_refresh_failed(self, error_message: str) -> None:
        print(error_message)
        self._show_pipeline_error_row(f"DB Error: {error_message}")
        self.status_label.setText("Material refresh failed.")

    def _cleanup_refresh_worker(self, thread: QThread | None, worker: MaterialLoadWorker | None) -> None:
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()
        if self._refresh_worker is worker:
            self._refresh_worker = None
        if self._refresh_thread is thread:
            self._refresh_thread = None
        pending_refresh = self._pending_refresh
        self._is_refreshing = False
        self.refresh_button.setEnabled(True)
        if pending_refresh:
            self._pending_refresh = False
            QTimer.singleShot(0, self.refresh_data)

    def _prepare_new_material(self) -> None:
        self.active_item_id = None
        self._loading_material = False
        self.part_number_field.setText("")
        self.description_field.setText("")
        self.unit_field.setText("")
        self.carry_source_combo.setCurrentText("Internal")
        self.is_active_checkbox.setChecked(True)
        for field in self.price_fields.values():
            field.setText("")
        for field in self.vendor_part_fields.values():
            field.setText("")
        for label in self.date_labels.values():
            label.setText("")
        self.mode_label.setText("Mode: New material")
        self.current_price_label.setText("Current carried price: $0.00")
        self.history_table.setRowCount(0)
        self.pipeline_table.clearSelection()

    def _on_material_select(self) -> None:
        selection = self.pipeline_table.selectedItems()
        if not selection:
            return

        item_id = int(self.pipeline_table.item(self.pipeline_table.row(selection[0]), 0).text())
        try:
            material = get_material_by_id(item_id)
            history = get_material_price_history(item_id)
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to load material:\n{exc}")
            return

        if not material:
            return

        self._loading_material = True
        self.active_item_id = item_id
        self.part_number_field.setText(material.get("PartNumber") or "")
        self.description_field.setText(material.get("Description") or "")
        self.unit_field.setText(material.get("Unit") or "")
        self.carry_source_combo.setCurrentText(material.get("CarryPriceSource") or "Internal")
        self.is_active_checkbox.setChecked(bool(material.get("IsActive", True)))

        self.price_fields["internal_price"].setText(self._format_price_value(material.get("InternalPrice")))
        self.price_fields["nedco_price"].setText(self._format_price_value(material.get("NedcoPrice")))
        self.price_fields["gescan_price"].setText(self._format_price_value(material.get("GescanPrice")))
        self.price_fields["eecol_price"].setText(self._format_price_value(material.get("EecolPrice")))
        self.price_fields["guillevin_price"].setText(self._format_price_value(material.get("GuillevinPrice")))

        self.vendor_part_fields["nedco_part_number"].setText(material.get("NedcoPartNumber") or "")
        self.vendor_part_fields["gescan_part_number"].setText(material.get("GescanPartNumber") or "")
        self.vendor_part_fields["eecol_part_number"].setText(material.get("EecolPartNumber") or "")
        self.vendor_part_fields["guillevin_part_number"].setText(material.get("GuillevinPartNumber") or "")

        self.date_labels["Nedco"].setText(self._format_date(material.get("NedcoLastPriceDate")))
        self.date_labels["Gescan"].setText(self._format_date(material.get("GescanLastPriceDate")))
        self.date_labels["Eecol"].setText(self._format_date(material.get("EecolLastPriceDate")))
        self.date_labels["Guillevin"].setText(self._format_date(material.get("GuillevinLastPriceDate")))
        self.mode_label.setText(f"Mode: Editing material #{item_id}")

        self.history_table.setRowCount(0)
        for row in history:
            self._append_row(
                self.history_table,
                [
                    self._format_datetime(row.get("CapturedAt")),
                    str(row.get("SourceType") or ""),
                    str(row.get("VendorName") or row.get("WholesalerName") or ""),
                    f"${float(row.get('UnitPrice') or 0):,.2f}",
                    str(row.get("VendorQuoteNumber") or ""),
                    str(row.get("PriceRequestID") or ""),
                ],
                {0: Qt.AlignmentFlag.AlignCenter, 3: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 4: Qt.AlignmentFlag.AlignCenter, 5: Qt.AlignmentFlag.AlignCenter},
            )

        self._loading_material = False
        self._update_current_price_preview()

    def _save_material_record(self) -> None:
        payload = {
            "part_number": self.part_number_field.text().strip(),
            "description": self.description_field.text().strip(),
            "unit": self.unit_field.text().strip(),
            "carry_source": self.carry_source_combo.currentText().strip(),
            "is_active": self.is_active_checkbox.isChecked(),
            "internal_price": self.price_fields["internal_price"].text().strip(),
            "nedco_price": self.price_fields["nedco_price"].text().strip(),
            "nedco_part_number": self.vendor_part_fields["nedco_part_number"].text().strip(),
            "gescan_price": self.price_fields["gescan_price"].text().strip(),
            "gescan_part_number": self.vendor_part_fields["gescan_part_number"].text().strip(),
            "eecol_price": self.price_fields["eecol_price"].text().strip(),
            "eecol_part_number": self.vendor_part_fields["eecol_part_number"].text().strip(),
            "guillevin_price": self.price_fields["guillevin_price"].text().strip(),
            "guillevin_part_number": self.vendor_part_fields["guillevin_part_number"].text().strip(),
        }

        try:
            saved_item_id = save_material(self.active_item_id, payload)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return

        self.active_item_id = int(saved_item_id)
        self.refresh_data()
        if str(saved_item_id) in self.material_lookup:
            for row in range(self.pipeline_table.rowCount()):
                if self.pipeline_table.item(row, 0).text() == str(saved_item_id):
                    self.pipeline_table.selectRow(row)
                    self._on_material_select()
                    break
        QMessageBox.information(self, "Success", f"Material #{saved_item_id} saved successfully.")

    def _update_current_price_preview(self) -> None:
        if self._loading_material:
            return
        source = self.carry_source_combo.currentText().strip() or "Internal"
        source_to_key = {
            "Internal": "internal_price",
            "Nedco": "nedco_price",
            "Gescan": "gescan_price",
            "Eecol": "eecol_price",
            "Guillevin": "guillevin_price",
        }
        raw_value = self.price_fields[source_to_key.get(source, "internal_price")].text().strip()
        try:
            current_price = float(raw_value) if raw_value else 0.0
        except ValueError:
            current_price = 0.0
        self.current_price_label.setText(f"Current carried price: ${current_price:,.2f}")

    def _append_row(self, table: QTableWidget, values: list[str], alignments: dict[int, Qt.AlignmentFlag] | None = None) -> None:
        alignments = alignments or {}
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(alignments.get(column, Qt.AlignmentFlag.AlignLeft))
            table.setItem(row, column, item)

    def _show_pipeline_error_row(self, message: str) -> None:
        self.pipeline_table.setSortingEnabled(False)
        self.pipeline_table.setRowCount(1)
        self.pipeline_table.setItem(0, 0, QTableWidgetItem(""))
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft)
        self.pipeline_table.setItem(0, 1, item)
        for column in range(2, self.pipeline_table.columnCount()):
            self.pipeline_table.setItem(0, column, QTableWidgetItem(""))

    def _format_price_value(self, value: object) -> str:
        return "" if value is None else f"{float(value):.2f}"

    def _format_date(self, value: object) -> str:
        if not value:
            return ""
        return str(value)[:10]

    def _format_datetime(self, value: object) -> str:
        if not value:
            return ""
        return str(value).replace("T", " ")[:16]
