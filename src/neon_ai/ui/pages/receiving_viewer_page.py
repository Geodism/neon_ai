from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
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

from neon_ai.database.purchases import (
    get_po_items_with_receiving,
    get_purchase_orders_for_pipeline,
    log_material_receipt_batch,
    search_po_items_by_part,
)


class ReceivingViewerPage(QWidget):
    """Hidden parallel receiving surface pending ownership decision.

    Do not expose in main navigation until receiving ownership is explicitly
    reaffirmed. Current primary procurement workflow is RFQ Center / PO Center.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_po_id: int | None = None
        self.is_receiving_mode = False
        self._editing_loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Receiving & Inventory Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        search_group = QGroupBox("Search All Parts")
        search_layout = QHBoxLayout(search_group)
        self.search_field = QLineEdit()
        self.search_field.returnPressed.connect(self._on_search)
        search_layout.addWidget(self.search_field, 1)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self._on_search)
        search_layout.addWidget(search_button)
        layout.addWidget(search_group)

        pipeline_group = QGroupBox("Active Purchase Orders")
        pipeline_layout = QVBoxLayout(pipeline_group)
        self.po_table = QTableWidget(0, 4)
        self.po_table.setHorizontalHeaderLabels(["PO_ID", "WO #", "Site", "Date"])
        self.po_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.po_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.po_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.po_table.verticalHeader().setVisible(False)
        self.po_table.setColumnWidth(0, 50)
        self.po_table.setColumnWidth(1, 50)
        self.po_table.setColumnWidth(2, 150)
        self.po_table.setColumnWidth(3, 80)
        self.po_table.itemSelectionChanged.connect(self._on_po_select)
        pipeline_layout.addWidget(self.po_table)
        layout.addWidget(pipeline_group, 1)
        return panel

    def _build_right_panel(self) -> QGroupBox:
        group = QGroupBox("Receiving Workspace")
        layout = QVBoxLayout(group)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(QLabel("Packing Slip #:"))
        self.slip_field = QLineEdit()
        self.slip_field.setMaximumWidth(140)
        header_layout.addWidget(self.slip_field)
        header_layout.addWidget(QLabel("Date Arrived:"))
        self.date_field = QLineEdit(datetime.date.today().strftime("%Y-%m-%d"))
        self.date_field.setMaximumWidth(110)
        header_layout.addWidget(self.date_field)
        self.start_button = QPushButton("Start Receiving")
        self.start_button.clicked.connect(self._start_receiving_session)
        header_layout.addWidget(self.start_button)
        header_layout.addStretch(1)
        layout.addWidget(header)

        self.receiving_table = QTableWidget(0, 8)
        self.receiving_table.setHorizontalHeaderLabels(
            ["ItemID", "PO #", "Site", "Desc", "Ordered", "Prev Rcvd", "Left", "Arriving Now"]
        )
        self.receiving_table.setColumnHidden(0, True)
        self.receiving_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.receiving_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.receiving_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.receiving_table.verticalHeader().setVisible(False)
        self.receiving_table.setColumnWidth(1, 50)
        self.receiving_table.setColumnWidth(2, 100)
        self.receiving_table.setColumnWidth(3, 220)
        self.receiving_table.setColumnWidth(4, 60)
        self.receiving_table.setColumnWidth(5, 70)
        self.receiving_table.setColumnWidth(6, 60)
        self.receiving_table.setColumnWidth(7, 90)
        self.receiving_table.cellDoubleClicked.connect(self._on_receiving_double_click)
        self.receiving_table.itemChanged.connect(self._on_receiving_item_changed)
        layout.addWidget(self.receiving_table, 1)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addWidget(QLabel("* Double-click 'Arriving Now' to enter quantities *"))
        footer_layout.addStretch(1)
        self.save_button = QPushButton("Save Receipt to Database")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save_batch)
        footer_layout.addWidget(self.save_button)
        layout.addWidget(footer)
        return group

    def refresh_data(self) -> None:
        self.po_table.setRowCount(0)
        try:
            purchase_orders = get_purchase_orders_for_pipeline()
        except Exception as exc:
            QMessageBox.critical(self, "PO Pipeline error", str(exc))
            return

        for po in purchase_orders:
            row = self.po_table.rowCount()
            self.po_table.insertRow(row)
            values = [
                str(po.get("PurchaseOrderID") or ""),
                str(po.get("WorkOrderID") or ""),
                str(po.get("SiteName") or ""),
                str(po.get("Date") or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 1, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.po_table.setItem(row, column, item)

    def _reset_session(self) -> None:
        self.is_receiving_mode = False
        self.slip_field.setText("")
        self.date_field.setText(datetime.date.today().strftime("%Y-%m-%d"))
        self.start_button.setText("Start Receiving")
        self.start_button.setEnabled(True)
        self.save_button.setEnabled(False)
        self.slip_field.setEnabled(True)
        self._set_arriving_column_editable(False)

    def _on_po_select(self) -> None:
        selection = self.po_table.selectedItems()
        if not selection:
            return

        self._reset_session()
        row_index = self.po_table.row(selection[0])
        self.active_po_id = int(self.po_table.item(row_index, 0).text())
        site_name = self.po_table.item(row_index, 2).text()

        self.receiving_table.setRowCount(0)
        try:
            items = get_po_items_with_receiving(self.active_po_id)
        except Exception as exc:
            QMessageBox.critical(self, "Logic Error", f"Failed to load items: {exc}")
            return

        for item in items:
            row = self.receiving_table.rowCount()
            self.receiving_table.insertRow(row)
            values = [
                str(item.get("POItemID") or ""),
                str(self.active_po_id),
                site_name,
                str(item.get("Description") or ""),
                str(item.get("QuantityOrdered") or 0),
                str(item.get("QuantityReceived") or 0),
                str(item.get("Remaining") or 0),
                "0.00",
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                if column in (1, 4, 5, 6, 7):
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.receiving_table.setItem(row, column, table_item)

    def _on_search(self) -> None:
        term = self.search_field.text().strip()
        if not term:
            self.refresh_data()
            return

        self._reset_session()
        self.receiving_table.setRowCount(0)
        try:
            results = search_po_items_by_part(term)
        except Exception as exc:
            QMessageBox.critical(self, "Search Error", str(exc))
            return

        if not results:
            QMessageBox.information(self, "Search", f"No parts found matching '{term}'")
            return

        for item in results:
            row = self.receiving_table.rowCount()
            self.receiving_table.insertRow(row)
            values = [
                str(item.get("POItemID") or ""),
                str(item.get("PurchaseOrderID") or ""),
                str(item.get("SiteName") or ""),
                str(item.get("Description") or ""),
                str(item.get("QuantityOrdered") or 0),
                str(item.get("QuantityReceived") or 0),
                str(item.get("Remaining") or 0),
                "0.00",
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                if column in (1, 4, 5, 6, 7):
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.receiving_table.setItem(row, column, table_item)

    def _start_receiving_session(self) -> None:
        if not self.slip_field.text().strip():
            QMessageBox.warning(self, "Stop", "Please enter a Packing Slip Number to start.")
            return

        self.is_receiving_mode = True
        self.start_button.setText("Receiving Session Active")
        self.start_button.setEnabled(False)
        self.slip_field.setEnabled(False)
        self.save_button.setEnabled(True)
        self._set_arriving_column_editable(True)
        QMessageBox.information(
            self,
            "Ready",
            "Session started! Double-click the 'Arriving Now' column to enter quantities.",
        )

    def _set_arriving_column_editable(self, editable: bool) -> None:
        for row in range(self.receiving_table.rowCount()):
            item = self.receiving_table.item(row, 7)
            if item is None:
                continue
            flags = item.flags() & ~Qt.ItemFlag.ItemIsEditable
            if editable:
                flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)

    def _on_receiving_double_click(self, row: int, column: int) -> None:
        if not self.is_receiving_mode:
            QMessageBox.warning(self, "Locked", "Please enter a Packing Slip and click 'Start Receiving' first.")
            return
        if column != 7:
            return

        left_to_arrive = float(self.receiving_table.item(row, 6).text())
        if left_to_arrive <= 0:
            confirmed = QMessageBox.question(
                self,
                "Complete",
                "This item is already fully received! Enter an overage?",
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return

        self.receiving_table.editItem(self.receiving_table.item(row, column))

    def _on_receiving_item_changed(self, item: QTableWidgetItem) -> None:
        if self._editing_loading or item.column() != 7:
            return
        try:
            arriving_now = float(item.text().strip())
            if arriving_now < 0:
                arriving_now = 0.0
        except ValueError:
            arriving_now = 0.0

        self._editing_loading = True
        item.setText(f"{arriving_now:.2f}")
        self._editing_loading = False

    def _save_batch(self) -> None:
        items_to_save: list[tuple[int, float]] = []
        for row in range(self.receiving_table.rowCount()):
            po_item_id = int(self.receiving_table.item(row, 0).text())
            arriving_now = float(self.receiving_table.item(row, 7).text())
            left_to_arrive = float(self.receiving_table.item(row, 6).text())

            if arriving_now > 0:
                if arriving_now > left_to_arrive:
                    confirmed = QMessageBox.question(
                        self,
                        "Overage Detected",
                        f"You are receiving {arriving_now} of '{self.receiving_table.item(row, 3).text()}', "
                        f"but only {left_to_arrive} are missing. Save anyway?",
                    )
                    if confirmed != QMessageBox.StandardButton.Yes:
                        return
                items_to_save.append((po_item_id, arriving_now))

        if not items_to_save:
            QMessageBox.information(self, "Empty", "No items were marked as 'Arriving Now'.")
            return

        try:
            log_material_receipt_batch(
                packing_slip=self.slip_field.text().strip(),
                receive_date=self.date_field.text().strip(),
                items_arriving=items_to_save,
            )
            QMessageBox.information(
                self,
                "Success",
                f"Successfully logged {len(items_to_save)} items to Packing Slip #{self.slip_field.text().strip()}!",
            )
            if self.search_field.text().strip():
                self._on_search()
            elif self.active_po_id:
                self._reload_active_po()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to save batch: {exc}")

    def _reload_active_po(self) -> None:
        self.receiving_table.setRowCount(0)
        if not self.active_po_id:
            return
        self._reset_session()
        try:
            items = get_po_items_with_receiving(self.active_po_id)
        except Exception as exc:
            QMessageBox.critical(self, "Logic Error", f"Failed to load items: {exc}")
            return

        site_name = ""
        for row in range(self.po_table.rowCount()):
            if self.po_table.item(row, 0).text() == str(self.active_po_id):
                site_name = self.po_table.item(row, 2).text()
                self.po_table.selectRow(row)
                break

        for item in items:
            row = self.receiving_table.rowCount()
            self.receiving_table.insertRow(row)
            values = [
                str(item.get("POItemID") or ""),
                str(self.active_po_id),
                site_name,
                str(item.get("Description") or ""),
                str(item.get("QuantityOrdered") or 0),
                str(item.get("QuantityReceived") or 0),
                str(item.get("Remaining") or 0),
                "0.00",
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                if column in (1, 4, 5, 6, 7):
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.receiving_table.setItem(row, column, table_item)
