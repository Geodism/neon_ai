from __future__ import annotations

import datetime
import os
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.bootstrap import ensure_legacy_import_paths

ensure_legacy_import_paths()

from neon_ai.database.estimates import get_all_estimates, get_estimate_materials
from neon_ai.database.materials import (
    get_material_price_for_vendor,
    get_or_create_material_from_estimate,
    record_purchase_order_price,
    search_materials,
)
from neon_ai.database.purchases import (
    archive_purchase_order_docx,
    get_carried_items_for_po_builder,
    get_existing_po_details,
    get_po_export_data,
    get_po_followup_status,
    get_po_items,
    get_po_items_with_receiving,
    get_purchase_order_edit_items,
    get_purchase_orders_for_pipeline,
    get_vendor_email_for_po,
    get_vendors,
    lock_purchase_order_record,
    log_material_receipt_batch,
    save_new_purchase_order,
    save_purchase_order_draft,
    send_purchase_order_now,
)
from neon_ai.database.rfq import (
    build_rfq_preview_text,
    get_active_rfqs,
    get_all_vendors,
    get_quoted_vendors_for_estimate,
    get_rfq_header_data,
    get_rfq_items_for_matrix,
    get_wo_resolution_for_rfq,
    save_quote_and_carry_items,
    save_rfq_package,
    send_rfq_by_id,
    update_rfq_item_price,
)
from neon_ai.database.timesheets import get_open_workorder_choices


class RFQViewerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_estimate_id: int | None = None
        self.selected_quote_path: str | None = None
        self.current_vendor_id: int | None = None
        self.active_rfq_id: int | None = None
        self.active_po_id: int | None = None
        self.wo_resolution: dict | None = None
        self.is_receiving_mode = False
        self.direct_search_results: dict[str, dict] = {}
        self.direct_workorder_lookup: dict[str, int] = {}
        self.direct_vendor_lookup: dict[str, int] = {}
        self.direct_active_po_id: int | None = None
        self.direct_current_status = "Draft"
        self.build_po_readonly = False
        self.selected_vendor_name = ""
        self.vendor_buttons: dict[str, QRadioButton] = {}

        self.direct_material_search_timer = QTimer(self)
        self.direct_material_search_timer.setSingleShot(True)
        self.direct_material_search_timer.timeout.connect(self._perform_direct_material_search)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Material Procurement Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(main_splitter, 1)

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(left_splitter)

        rfq_group = QGroupBox("Active RFQs")
        rfq_layout = QVBoxLayout(rfq_group)
        self.rfq_table = QTableWidget(0, 8)
        self.rfq_table.setHorizontalHeaderLabels(["RFQ_ID", "Est #", "Site", "Vendor", "Sent", "Due", "Status", "VendorID"])
        self.rfq_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rfq_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rfq_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rfq_table.verticalHeader().setVisible(False)
        self.rfq_table.setColumnHidden(0, True)
        self.rfq_table.setColumnHidden(7, True)
        self.rfq_table.setColumnWidth(1, 50)
        self.rfq_table.setColumnWidth(2, 120)
        self.rfq_table.setColumnWidth(3, 100)
        self.rfq_table.setColumnWidth(4, 90)
        self.rfq_table.setColumnWidth(5, 80)
        self.rfq_table.setColumnWidth(6, 70)
        self.rfq_table.itemSelectionChanged.connect(self.on_rfq_select)
        rfq_layout.addWidget(self.rfq_table)
        build_po_button = QPushButton("↳ Build PO from selected RFQ")
        build_po_button.clicked.connect(lambda: self.notebook.setCurrentWidget(self.tab_build_po))
        rfq_layout.addWidget(build_po_button)
        left_splitter.addWidget(rfq_group)

        po_group = QGroupBox("Purchase Orders")
        po_layout = QVBoxLayout(po_group)
        self.po_table = QTableWidget(0, 5)
        self.po_table.setHorizontalHeaderLabels(["PO_ID", "WO #", "Site", "Date", "Status"])
        self.po_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.po_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.po_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.po_table.verticalHeader().setVisible(False)
        self.po_table.setColumnWidth(0, 50)
        self.po_table.setColumnWidth(1, 50)
        self.po_table.setColumnWidth(2, 150)
        self.po_table.setColumnWidth(3, 80)
        self.po_table.setColumnWidth(4, 70)
        self.po_table.itemSelectionChanged.connect(self.on_po_select)
        po_layout.addWidget(self.po_table)
        left_splitter.addWidget(po_group)

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(10, 0, 0, 0)
        self.notebook = QTabWidget()
        workspace_layout.addWidget(self.notebook)
        main_splitter.addWidget(workspace)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)

        self.tab_direct_po = QWidget()
        self.tab_build_rfq = QWidget()
        self.tab_matrix = QWidget()
        self.tab_build_po = QWidget()
        self.tab_receiving = QWidget()
        self.tab_document = QWidget()
        self.notebook.addTab(self.tab_direct_po, "0. Direct PO")
        self.notebook.addTab(self.tab_build_rfq, "📦 1. Build RFQ")
        self.notebook.addTab(self.tab_matrix, "📊 2. Bid Matrix")
        self.notebook.addTab(self.tab_build_po, "🛒 3. Build PO")
        self.notebook.addTab(self.tab_receiving, "🚚 4. Receiving")
        self.notebook.addTab(self.tab_document, "📄 5. PO Document")

        self.build_direct_po_tab_ui()
        self.build_package_ui()
        self.build_matrix_ui()
        self.build_po_tab_ui()
        self.build_receiving_tab_ui()
        self.build_document_tab_ui()

    def _set_item(self, table: QTableWidget, row: int, column: int, value: str, align_right: bool = False, center: bool = False, color=None) -> None:
        item = QTableWidgetItem(value)
        if align_right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        elif center:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color is not None:
            item.setForeground(color)
        table.setItem(row, column, item)

    def _selected_row(self, table: QTableWidget) -> int | None:
        items = table.selectedItems()
        if not items:
            return None
        return table.row(items[0])

    def _show_inline_editor(
        self,
        table: QTableWidget,
        row: int,
        column: int,
        current_text: str,
        on_commit: Callable[[str], None],
    ) -> None:
        item = table.item(row, column)
        if item is None:
            return
        rect = table.visualItemRect(item)
        editor = QLineEdit(table.viewport())
        editor.setText(current_text)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        editor.setGeometry(rect)
        editor.selectAll()
        editor.show()
        editor.setFocus()

        def commit() -> None:
            try:
                on_commit(editor.text())
            finally:
                editor.deleteLater()

        editor.returnPressed.connect(commit)

        original_focus_out = editor.focusOutEvent

        def focus_out(event) -> None:
            editor.deleteLater()
            original_focus_out(event)

        editor.focusOutEvent = focus_out

    def load_pipeline(self) -> None:
        self.rfq_table.setRowCount(0)
        try:
            for row_data in get_active_rfqs():
                due = row_data.get("DueDate")
                status = row_data.get("Status")
                overdue = bool(due) and str(due) < str(datetime.date.today()) and status != "Awarded"
                row = self.rfq_table.rowCount()
                self.rfq_table.insertRow(row)
                values = [
                    str(row_data.get("PriceRequestID") or ""),
                    str(row_data.get("EstimateID") or ""),
                    str(row_data.get("SiteName") or ""),
                    str(row_data.get("VendorName") or ""),
                    str(row_data.get("DateSent") or ""),
                    str(due or ""),
                    str(status or ""),
                    str(row_data.get("VendorID") or ""),
                ]
                for column, value in enumerate(values):
                    self._set_item(
                        self.rfq_table,
                        row,
                        column,
                        value,
                        center=column in (1, 4, 5, 6),
                        color=Qt.GlobalColor.red if overdue and column not in (0, 7) else None,
                    )
        except Exception as exc:
            print(f"RFQ Pipeline error: {exc}")

        self.po_table.setRowCount(0)
        try:
            for row_data in get_purchase_orders_for_pipeline():
                row = self.po_table.rowCount()
                self.po_table.insertRow(row)
                values = [
                    str(row_data.get("PurchaseOrderID") or ""),
                    str(row_data.get("WorkOrderID") or ""),
                    str(row_data.get("SiteName") or ""),
                    str(row_data.get("Date") or ""),
                    str(row_data.get("Status") or ""),
                ]
                for column, value in enumerate(values):
                    self._set_item(self.po_table, row, column, value, center=column in (0, 1, 3, 4))
        except Exception as exc:
            print(f"PO Pipeline error: {exc}")

    def build_direct_po_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_direct_po)
        header = QGroupBox("Build Direct Purchase Order")
        header_layout = QGridLayout(header)
        layout.addWidget(header)

        header_layout.addWidget(QLabel("Work Order:"), 0, 0)
        self.direct_wo_combo = QComboBox()
        self.direct_wo_combo.setMinimumWidth(260)
        header_layout.addWidget(self.direct_wo_combo, 0, 1)
        header_layout.addWidget(QLabel("Vendor:"), 0, 2)
        self.direct_vendor_combo = QComboBox()
        self.direct_vendor_combo.setMinimumWidth(240)
        self.direct_vendor_combo.currentTextChanged.connect(self.on_direct_vendor_select)
        header_layout.addWidget(self.direct_vendor_combo, 0, 3)
        header_layout.addWidget(QLabel("Expected Arrival:"), 1, 0)
        self.direct_eta_edit = QLineEdit()
        header_layout.addWidget(self.direct_eta_edit, 1, 1)
        header_layout.addWidget(QLabel("ETA Note:"), 1, 2)
        self.direct_eta_note_edit = QLineEdit()
        header_layout.addWidget(self.direct_eta_note_edit, 1, 3)
        self.direct_status_label = QLabel("Draft workspace not saved yet")
        self.direct_status_label.setStyleSheet("font-weight: 700; color: blue;")
        header_layout.addWidget(self.direct_status_label, 2, 0, 1, 4)

        staging = QGroupBox("Add Materials")
        staging_layout = QHBoxLayout(staging)
        layout.addWidget(staging)
        staging_layout.addWidget(QLabel("Description:"))
        self.direct_desc = QComboBox()
        self.direct_desc.setEditable(True)
        self.direct_desc.setMinimumWidth(320)
        self.direct_desc.lineEdit().textEdited.connect(self.on_direct_material_search)
        self.direct_desc.currentTextChanged.connect(self.on_direct_material_select)
        self.direct_desc.current_item_id = None
        self.direct_desc.current_part_no = None
        staging_layout.addWidget(self.direct_desc)
        staging_layout.addWidget(QLabel("Qty:"))
        self.direct_qty = QLineEdit()
        self.direct_qty.setMaximumWidth(70)
        staging_layout.addWidget(self.direct_qty)
        staging_layout.addWidget(QLabel("Unit Price:"))
        self.direct_price = QLineEdit()
        self.direct_price.setMaximumWidth(80)
        staging_layout.addWidget(self.direct_price)
        add_material_button = QPushButton("Add Material")
        add_material_button.clicked.connect(self.add_direct_po_line)
        staging_layout.addWidget(add_material_button)

        self.direct_po_table = QTableWidget(0, 6)
        self.direct_po_table.setHorizontalHeaderLabels(["Description", "Qty", "UnitPrice", "LineTotal", "ItemID", "PartNumber"])
        self.direct_po_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.direct_po_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.direct_po_table.verticalHeader().setVisible(False)
        self.direct_po_table.setColumnWidth(0, 340)
        self.direct_po_table.setColumnWidth(1, 90)
        self.direct_po_table.setColumnWidth(2, 110)
        self.direct_po_table.setColumnWidth(3, 120)
        self.direct_po_table.setColumnHidden(4, True)
        self.direct_po_table.setColumnHidden(5, True)
        self.direct_po_table.cellDoubleClicked.connect(self.on_direct_po_double_click)
        layout.addWidget(self.direct_po_table, 1)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.direct_total_label = QLabel("DIRECT PO TOTAL: $0.00")
        self.direct_total_label.setStyleSheet("font-size: 12px; font-weight: 700; color: blue;")
        footer_layout.addWidget(self.direct_total_label)
        footer_layout.addStretch(1)
        for label, handler in [
            ("Save Draft", self.save_direct_purchase_order_draft),
            ("Lock PO", self.lock_direct_purchase_order),
            ("View Document", self.preview_direct_purchase_order),
            ("Send to Folder", self.export_direct_purchase_order),
            ("Send to Wholesaler", self.send_direct_purchase_order),
            ("Remove Selected", self.remove_direct_po_line),
            ("Clear Draft", self.clear_direct_po_draft),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            footer_layout.addWidget(button)
        layout.addWidget(footer)

    def build_package_ui(self) -> None:
        layout = QVBoxLayout(self.tab_build_rfq)
        logistics = QGroupBox("1. Logistics & Target Vendor")
        logistics_layout = QGridLayout(logistics)
        layout.addWidget(logistics)

        logistics_layout.addWidget(QLabel("Source Estimate:"), 0, 0)
        self.est_combo = QComboBox()
        self.est_combo.setMinimumWidth(240)
        self.est_combo.currentTextChanged.connect(self.on_estimate_select)
        logistics_layout.addWidget(self.est_combo, 0, 1)
        logistics_layout.addWidget(QLabel("Date Sent:"), 0, 2)
        self.date_sent_edit = QLineEdit(datetime.date.today().strftime("%Y-%m-%d"))
        self.date_sent_edit.setReadOnly(True)
        logistics_layout.addWidget(self.date_sent_edit, 0, 3)
        logistics_layout.addWidget(QLabel("Due Date:"), 1, 2)
        self.due_date_edit = QLineEdit("")
        logistics_layout.addWidget(self.due_date_edit, 1, 3)

        vendor_group = QGroupBox("Select Target Vendor")
        vendor_layout = QVBoxLayout(vendor_group)
        self.vendor_inner = QWidget()
        self.vendor_inner_layout = QVBoxLayout(self.vendor_inner)
        self.vendor_inner_layout.setContentsMargins(0, 0, 0, 0)
        vendor_layout.addWidget(self.vendor_inner)
        logistics_layout.addWidget(vendor_group, 0, 4, 2, 1)
        self.vendor_button_group = QButtonGroup(self)
        self.vendor_button_group.setExclusive(True)

        basket_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(basket_splitter, 1)
        left = QGroupBox("Master Estimate List")
        left_layout = QVBoxLayout(left)
        self.master_table = QTableWidget(0, 3)
        self.master_table.setHorizontalHeaderLabels(["ID", "Qty", "Desc"])
        self.master_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.master_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.master_table.verticalHeader().setVisible(False)
        self.master_table.setColumnHidden(0, True)
        self.master_table.setColumnWidth(1, 50)
        self.master_table.setColumnWidth(2, 360)
        left_layout.addWidget(self.master_table)
        basket_splitter.addWidget(left)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.addStretch(1)
        for label, handler in [
            ("Add All ⏭", self.mock_add_all),
            ("Add ➔", self.mock_add),
            ("⬅ Remove", self.mock_remove),
            ("⏮ Remove All", self.mock_remove_all),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            middle_layout.addWidget(button)
        middle_layout.addStretch(1)
        basket_splitter.addWidget(middle)

        right = QGroupBox("Current RFQ Package")
        right_layout = QVBoxLayout(right)
        self.package_table = QTableWidget(0, 3)
        self.package_table.setHorizontalHeaderLabels(["ID", "Qty", "Desc"])
        self.package_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.package_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.package_table.verticalHeader().setVisible(False)
        self.package_table.setColumnHidden(0, True)
        self.package_table.setColumnWidth(1, 50)
        self.package_table.setColumnWidth(2, 360)
        right_layout.addWidget(self.package_table)
        basket_splitter.addWidget(right)

        action_row = QFrame()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addStretch(1)
        preview_button = QPushButton("Preview RFQ")
        preview_button.clicked.connect(self.preview_current_rfq)
        action_layout.addWidget(preview_button)
        self.btn_generate_rfq = QPushButton("Save RFQ Draft")
        self.btn_generate_rfq.clicked.connect(self.generate_and_save_rfq)
        action_layout.addWidget(self.btn_generate_rfq)
        self.btn_send_rfq = QPushButton("Send RFQ")
        self.btn_send_rfq.clicked.connect(self.send_current_rfq)
        action_layout.addWidget(self.btn_send_rfq)
        layout.addWidget(action_row)

        preview_group = QGroupBox("RFQ Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.rfq_preview = QPlainTextEdit()
        self.rfq_preview.setReadOnly(True)
        preview_layout.addWidget(self.rfq_preview)
        layout.addWidget(preview_group)

    def build_matrix_ui(self) -> None:
        layout = QVBoxLayout(self.tab_matrix)
        title = QLabel("Reviewing Responses")
        title.setStyleSheet("font-size: 12px; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)

        matrix_group = QGroupBox("Vendor Pricing Matrix")
        matrix_layout = QVBoxLayout(matrix_group)
        self.matrix_table = QTableWidget(0, 8)
        self.matrix_table.setHorizontalHeaderLabels(
            ["ItemID", "MaterialID", "Carry", "Qty", "Description", "Est Unit", "Quote Unit", "Quote Ext"]
        )
        self.matrix_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.matrix_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix_table.verticalHeader().setVisible(False)
        self.matrix_table.setColumnHidden(0, True)
        self.matrix_table.setColumnHidden(1, True)
        self.matrix_table.setColumnWidth(2, 50)
        self.matrix_table.setColumnWidth(3, 50)
        self.matrix_table.setColumnWidth(4, 300)
        self.matrix_table.setColumnWidth(5, 80)
        self.matrix_table.setColumnWidth(6, 80)
        self.matrix_table.setColumnWidth(7, 80)
        self.matrix_table.cellDoubleClicked.connect(self.on_matrix_double_click)
        self.matrix_table.cellClicked.connect(self.on_matrix_click)
        matrix_layout.addWidget(self.matrix_table)
        layout.addWidget(matrix_group, 1)

        lot_group = QGroupBox("Vendor Quote Details")
        lot_layout = QGridLayout(lot_group)
        self.quote_no_edit = QLineEdit()
        self.quote_date_edit = QLineEdit()
        lot_layout.addWidget(QLabel("Quote #:"), 0, 0)
        lot_layout.addWidget(self.quote_no_edit, 0, 1)
        lot_layout.addWidget(QLabel("Quote Date:"), 0, 2)
        lot_layout.addWidget(self.quote_date_edit, 0, 3)
        self.attach_label = QLabel("No file attached")
        self.attach_label.setStyleSheet("color: gray;")
        lot_layout.addWidget(self.attach_label, 1, 0, 1, 3)
        attach_button = QPushButton("📎 Attach Quote")
        attach_button.clicked.connect(self.on_attach_file)
        lot_layout.addWidget(attach_button, 1, 3)
        layout.addWidget(lot_group)

        action_row = QFrame()
        action_layout = QHBoxLayout(action_row)
        action_layout.addStretch(1)
        self.carry_btn = QPushButton("➡️ Carry Selected Items to Estimate")
        self.carry_btn.clicked.connect(self.on_carry_click)
        action_layout.addWidget(self.carry_btn)
        layout.addWidget(action_row)

    def build_po_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_build_po)
        info_group = QGroupBox("PO Details")
        info_layout = QGridLayout(info_group)
        info_layout.addWidget(QLabel("Work Order #:"), 0, 0)
        self.wo_edit = QLineEdit()
        self.wo_edit.textChanged.connect(self.check_wo_lock)
        info_layout.addWidget(self.wo_edit, 0, 1)
        info_layout.addWidget(QLabel("Vendor:"), 0, 2)
        self.po_vendor_label = QLabel("")
        self.po_vendor_label.setStyleSheet("font-weight: 700;")
        info_layout.addWidget(self.po_vendor_label, 0, 3)
        self.lbl_po_status = QLabel("")
        self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
        info_layout.addWidget(self.lbl_po_status, 0, 4)
        info_layout.addWidget(QLabel("Sent To:"), 1, 0)
        self.po_sent_to_label = QLabel("")
        info_layout.addWidget(self.po_sent_to_label, 1, 1)
        info_layout.addWidget(QLabel("Sent On:"), 1, 2)
        self.po_sent_on_label = QLabel("")
        info_layout.addWidget(self.po_sent_on_label, 1, 3)
        info_layout.addWidget(QLabel("ETA:"), 2, 0)
        self.po_eta_label = QLabel("")
        self.po_eta_label.setWordWrap(True)
        info_layout.addWidget(self.po_eta_label, 2, 1, 1, 4)
        layout.addWidget(info_group)

        self.po_item_table = QTableWidget(0, 5)
        self.po_item_table.setHorizontalHeaderLabels(["ID", "Desc", "Remaining", "OrderQty", "Price"])
        self.po_item_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.po_item_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.po_item_table.verticalHeader().setVisible(False)
        self.po_item_table.setColumnHidden(0, True)
        self.po_item_table.setColumnWidth(1, 320)
        self.po_item_table.setColumnWidth(2, 90)
        self.po_item_table.setColumnWidth(3, 90)
        self.po_item_table.setColumnWidth(4, 90)
        self.po_item_table.cellDoubleClicked.connect(self.on_po_item_double_click)
        layout.addWidget(self.po_item_table, 1)

        button_row = QFrame()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)
        self.btn_create_po = QPushButton("Create & Lock PO")
        self.btn_create_po.clicked.connect(self.on_create_po)
        self.btn_create_po.setEnabled(False)
        button_layout.addWidget(self.btn_create_po)
        self.btn_view_po = QPushButton("Preview PO")
        self.btn_view_po.clicked.connect(self.on_view_po)
        self.btn_view_po.setEnabled(False)
        button_layout.addWidget(self.btn_view_po)
        self.btn_send_po = QPushButton("Send PO to Vendor")
        self.btn_send_po.clicked.connect(self.on_send_po)
        self.btn_send_po.setEnabled(False)
        button_layout.addWidget(self.btn_send_po)
        self.btn_export_po = QPushButton("Generate DOCX to Folder")
        self.btn_export_po.clicked.connect(self.on_export_to_folder)
        self.btn_export_po.setEnabled(False)
        button_layout.addWidget(self.btn_export_po)
        layout.addWidget(button_row)

    def build_receiving_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_receiving)
        title = QLabel("Receiving & Inventory Tracking")
        title.setStyleSheet("font-size: 12px; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)
        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(QLabel("Packing Slip #:"))
        self.receiving_slip_edit = QLineEdit()
        self.receiving_slip_edit.setMaximumWidth(140)
        header_layout.addWidget(self.receiving_slip_edit)
        header_layout.addWidget(QLabel("Date Arrived:"))
        self.receiving_date_edit = QLineEdit(datetime.date.today().strftime("%Y-%m-%d"))
        self.receiving_date_edit.setMaximumWidth(120)
        header_layout.addWidget(self.receiving_date_edit)
        self.btn_start_receiving = QPushButton("Start Receiving")
        self.btn_start_receiving.clicked.connect(self.start_receiving_session)
        header_layout.addWidget(self.btn_start_receiving)
        header_layout.addStretch(1)
        layout.addWidget(header)

        self.receiving_table = QTableWidget(0, 7)
        self.receiving_table.setHorizontalHeaderLabels(["ItemID", "Desc", "Ordered", "Received", "Left", "Arrived", "ArrivingNow"])
        self.receiving_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.receiving_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.receiving_table.verticalHeader().setVisible(False)
        self.receiving_table.setColumnHidden(0, True)
        self.receiving_table.setColumnWidth(1, 320)
        self.receiving_table.setColumnWidth(2, 80)
        self.receiving_table.setColumnWidth(3, 80)
        self.receiving_table.setColumnWidth(4, 80)
        self.receiving_table.setColumnWidth(5, 120)
        self.receiving_table.setColumnWidth(6, 100)
        self.receiving_table.cellDoubleClicked.connect(self.on_receiving_double_click)
        layout.addWidget(self.receiving_table, 1)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel("* Double-click 'Arriving Now' to enter received quantities. *")
        note.setStyleSheet("color: gray;")
        footer_layout.addWidget(note)
        footer_layout.addStretch(1)
        self.btn_save_receiving = QPushButton("Save Receipt to Database")
        self.btn_save_receiving.clicked.connect(self.save_receiving_batch)
        self.btn_save_receiving.setEnabled(False)
        footer_layout.addWidget(self.btn_save_receiving)
        layout.addWidget(footer)

    def build_document_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_document)
        self.doc_view = QPlainTextEdit()
        self.doc_view.setReadOnly(True)
        layout.addWidget(self.doc_view)

    def on_rfq_select(self) -> None:
        if self.po_table.selectedItems():
            self.po_table.clearSelection()
        row = self._selected_row(self.rfq_table)
        if row is None:
            return

        rfq_id = int(self.rfq_table.item(row, 0).text())
        self.active_rfq_id = rfq_id
        self.current_estimate_id = int(self.rfq_table.item(row, 1).text())
        self._set_vendor_selection(self.rfq_table.item(row, 3).text())
        self.due_date_edit.setText(self.rfq_table.item(row, 5).text())

        self.selected_quote_path = None
        self.quote_no_edit.setText("")
        self.quote_date_edit.setText("")
        self.attach_label.setText("No file attached")
        self.attach_label.setStyleSheet("color: gray;")
        self.matrix_table.setRowCount(0)
        self.package_table.setRowCount(0)
        self.master_table.setRowCount(0)

        try:
            header = get_rfq_header_data(rfq_id)
            if header:
                self.quote_no_edit.setText(str(header.get("VendorQuoteNumber") or ""))
                self.quote_date_edit.setText(str(header.get("VendorQuoteDate") or ""))
                path = header.get("QuoteFilePath")
                if path and os.path.exists(path):
                    self.selected_quote_path = path
                    self.attach_label.setText(f"📎 {os.path.basename(path)}")
                    self.attach_label.setStyleSheet("color: green;")
        except Exception as exc:
            print(f"Hydration Error: {exc}")

        try:
            for item in get_rfq_items_for_matrix(rfq_id):
                est_unit = float(item.get("UnitCost") or item.get("unitcost") or 0.0)
                quote_unit = float(item.get("QuotedUnitPrice") or 0.0)
                carry_box = "[✓]" if item.get("IsCarried") else "[ ]"
                row_index = self.matrix_table.rowCount()
                self.matrix_table.insertRow(row_index)
                values = [
                    str(item["PRItemID"]),
                    str(item["MaterialID"]),
                    carry_box,
                    str(item["Quantity"]),
                    str(item["Description"]),
                    f"${est_unit:.2f}",
                    f"${quote_unit:.2f}",
                    f"${quote_unit * float(item['Quantity'] or 0.0):.2f}",
                ]
                for column, value in enumerate(values):
                    self._set_item(
                        self.matrix_table,
                        row_index,
                        column,
                        value,
                        center=column in (2, 3),
                        align_right=column in (5, 6, 7),
                    )
                pkg_row = self.package_table.rowCount()
                self.package_table.insertRow(pkg_row)
                self._set_item(self.package_table, pkg_row, 0, str(item["MaterialID"]))
                self._set_item(self.package_table, pkg_row, 1, str(item["Quantity"]), center=True)
                self._set_item(self.package_table, pkg_row, 2, str(item["Description"]))
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load RFQ items: {exc}")

        self.build_po_readonly = False
        self.current_vendor_id = int(self.rfq_table.item(row, 7).text() or 0)
        self.active_po_id = None
        self.po_vendor_label.setText(self.rfq_table.item(row, 3).text())
        self.po_sent_to_label.setText("")
        self.po_sent_on_label.setText("")
        self.po_eta_label.setText("")
        self.po_item_table.setRowCount(0)

        try:
            self.wo_resolution = get_wo_resolution_for_rfq(rfq_id)
            wo_id = self.wo_resolution.get("open_work_order_id")
            self.wo_edit.setText(str(wo_id) if wo_id else "NO OPEN WO")
            self.wo_edit.setReadOnly(True)
        except Exception as exc:
            print(f"WO Fetch Error: {exc}")

        try:
            for item in get_carried_items_for_po_builder(rfq_id):
                remaining = float(item.get("QuotedQty") or 0) - float(item.get("PreviouslyOrdered") or 0)
                if remaining > 0:
                    row_index = self.po_item_table.rowCount()
                    self.po_item_table.insertRow(row_index)
                    values = [
                        str(item["mat_id"]),
                        str(item["Description"]),
                        f"{remaining:.2f}",
                        f"{remaining:.2f}",
                        f"${float(item.get('price') or 0.0):.2f}",
                    ]
                    for column, value in enumerate(values):
                        self._set_item(
                            self.po_item_table,
                            row_index,
                            column,
                            value,
                            center=column in (2, 3),
                            align_right=column == 4,
                        )
        except Exception as exc:
            print(f"PO Items Error: {exc}")

        self.refresh_po_state_for_current_selection()
        self.render_rfq_preview_for_selection()

    def on_po_select(self) -> None:
        if self.rfq_table.selectedItems():
            self.rfq_table.clearSelection()
        row = self._selected_row(self.po_table)
        if row is None:
            return
        po_id = int(self.po_table.item(row, 0).text())
        status_text = str(self.po_table.item(row, 4).text() or "").strip()
        if status_text == "Draft":
            self.load_direct_purchase_order(po_id, select_tab=True)
        else:
            self.load_existing_purchase_order(po_id, select_tab=True)

    def refresh_data(self) -> None:
        self.load_estimates()
        self.load_pipeline()
        self.refresh_direct_po_choices()

    def refresh_direct_po_choices(self) -> None:
        try:
            selected_wo = self.direct_wo_combo.currentText().strip()
            selected_vendor = self.direct_vendor_combo.currentText().strip()
            self.direct_workorder_lookup = {row["Label"]: int(row["WorkOrderID"]) for row in get_open_workorder_choices()}
            self.direct_wo_combo.blockSignals(True)
            self.direct_wo_combo.clear()
            self.direct_wo_combo.addItems(self.direct_workorder_lookup.keys())
            self.direct_wo_combo.setCurrentText(selected_wo if selected_wo in self.direct_workorder_lookup else "")
            self.direct_wo_combo.blockSignals(False)

            self.direct_vendor_lookup = {
                str(row.get("VendorName") or "").strip(): int(row["VendorID"])
                for row in get_vendors()
                if str(row.get("VendorName") or "").strip()
            }
            self.direct_vendor_combo.blockSignals(True)
            self.direct_vendor_combo.clear()
            self.direct_vendor_combo.addItems(self.direct_vendor_lookup.keys())
            self.direct_vendor_combo.setCurrentText(selected_vendor if selected_vendor in self.direct_vendor_lookup else "")
            self.direct_vendor_combo.blockSignals(False)
        except Exception as exc:
            print(f"Direct PO choices error: {exc}")

    def on_direct_vendor_select(self, _value: str | None = None) -> None:
        if self.direct_desc.currentText().strip() in self.direct_search_results:
            self.on_direct_material_select()

    def on_direct_material_search(self, _text: str) -> None:
        self.direct_desc.current_item_id = None
        self.direct_desc.current_part_no = None
        self.direct_material_search_timer.start(250)

    def _perform_direct_material_search(self) -> None:
        search_text = self.direct_desc.currentText().strip()
        if len(search_text) < 2:
            self.direct_search_results.clear()
            self.direct_desc.blockSignals(True)
            self.direct_desc.clear()
            self.direct_desc.setEditText(search_text)
            self.direct_desc.blockSignals(False)
            return

        results = search_materials(search_text)
        self.direct_search_results.clear()
        display_values = []
        for row in results:
            display_text = f"{row.get('PartNumber') or 'No Part #'} | {row.get('Description') or ''}"
            self.direct_search_results[display_text] = row
            display_values.append(display_text)
        self.direct_desc.blockSignals(True)
        self.direct_desc.clear()
        self.direct_desc.addItems(display_values)
        self.direct_desc.setEditText(search_text)
        self.direct_desc.blockSignals(False)
        if display_values and self.direct_desc.hasFocus():
            self.direct_desc.showPopup()

    def on_direct_material_select(self, _value: str | None = None) -> None:
        selection = self.direct_desc.currentText().strip()
        material = self.direct_search_results.get(selection)
        if not material:
            return
        price = get_material_price_for_vendor(material, self.direct_vendor_combo.currentText().strip())
        self.direct_price.setText(f"{price:.2f}")
        self.direct_desc.current_item_id = material.get("ItemID")
        self.direct_desc.current_part_no = material.get("PartNumber")

    def add_direct_po_line(self) -> None:
        try:
            desc = self.direct_desc.currentText().strip()
            qty = float(self.direct_qty.text().strip() or 0)
            price = float(self.direct_price.text().strip() or 0)
            if not desc or qty <= 0:
                return
            item_id = getattr(self.direct_desc, "current_item_id", None)
            part_no = getattr(self.direct_desc, "current_part_no", None)
            line_total = qty * price
            row = self.direct_po_table.rowCount()
            self.direct_po_table.insertRow(row)
            values = [
                desc,
                f"{qty:.2f}",
                f"${price:.2f}",
                f"${line_total:,.2f}",
                "" if item_id is None else str(item_id),
                "" if part_no is None else str(part_no),
            ]
            for column, value in enumerate(values):
                self._set_item(
                    self.direct_po_table,
                    row,
                    column,
                    value,
                    center=column == 1,
                    align_right=column in (2, 3),
                )
            self.direct_desc.setCurrentText("")
            self.direct_qty.setText("")
            self.direct_price.setText("")
            self.direct_desc.current_item_id = None
            self.direct_desc.current_part_no = None
            self.refresh_direct_po_total()
        except ValueError:
            QMessageBox.critical(self, "Error", "Quantity and price must be valid numbers.")

    def remove_direct_po_line(self, _event=None) -> None:
        rows = sorted({item.row() for item in self.direct_po_table.selectedItems()}, reverse=True)
        for row in rows:
            self.direct_po_table.removeRow(row)
        self.refresh_direct_po_total()

    def clear_direct_po_draft(self) -> None:
        self.direct_active_po_id = None
        self.direct_current_status = "Draft"
        self.direct_status_label.setText("Draft workspace not saved yet")
        self.direct_wo_combo.setCurrentText("")
        self.direct_vendor_combo.setCurrentText("")
        self.direct_desc.setCurrentText("")
        self.direct_qty.setText("")
        self.direct_price.setText("")
        self.direct_desc.current_item_id = None
        self.direct_desc.current_part_no = None
        self.direct_eta_edit.setText("")
        self.direct_eta_note_edit.setText("")
        self.direct_po_table.setRowCount(0)
        self.refresh_direct_po_total()

    def refresh_direct_po_total(self) -> None:
        total = 0.0
        for row in range(self.direct_po_table.rowCount()):
            total += float(str(self.direct_po_table.item(row, 3).text()).replace("$", "").replace(",", "").strip() or 0)
        self.direct_total_label.setText(f"DIRECT PO TOTAL: ${total:,.2f}")

    def on_direct_po_double_click(self, row: int, column: int) -> None:
        if column not in (1, 2):
            return
        values = [self.direct_po_table.item(row, col).text() for col in range(6)]
        current_text = str(values[1 if column == 1 else 2]).replace("$", "").replace(",", "").strip()
        def commit(new_text: str) -> None:
            try:
                new_value = max(0.0, float(new_text.strip() or 0))
                if column == 1:
                    values[1] = f"{new_value:.2f}"
                else:
                    values[2] = f"${new_value:.2f}"
                qty = float(str(values[1]).replace("$", "").replace(",", "").strip() or 0)
                price = float(str(values[2]).replace("$", "").replace(",", "").strip() or 0)
                values[3] = f"${qty * price:,.2f}"
                for col, value in enumerate(values):
                    self.direct_po_table.item(row, col).setText(value)
                self.refresh_direct_po_total()
            except ValueError:
                return

        self._show_inline_editor(self.direct_po_table, row, column, current_text, commit)

    def _harvest_direct_po_payload(self):
        work_order_label = self.direct_wo_combo.currentText().strip()
        vendor_label = self.direct_vendor_combo.currentText().strip()
        work_order_id = self.direct_workorder_lookup.get(work_order_label)
        vendor_id = self.direct_vendor_lookup.get(vendor_label)
        if not work_order_id or not vendor_id:
            raise ValueError("Select both an open work order and a vendor.")
        if self.direct_po_table.rowCount() == 0:
            raise ValueError("Add at least one material before saving the purchase order.")

        items = []
        price_updates = []
        for row in range(self.direct_po_table.rowCount()):
            desc = self.direct_po_table.item(row, 0).text().strip()
            qty = float(self.direct_po_table.item(row, 1).text())
            price = float(str(self.direct_po_table.item(row, 2).text()).replace("$", "").replace(",", "").strip() or 0)
            catalog_item_id = self.direct_po_table.item(row, 4).text() or None
            part_number = self.direct_po_table.item(row, 5).text() or None

            if not catalog_item_id:
                material = get_or_create_material_from_estimate(desc, part_number)
                catalog_item_id = material.get("ItemID")
                part_number = material.get("PartNumber") or part_number
                self.direct_po_table.item(row, 4).setText("" if catalog_item_id is None else str(catalog_item_id))
                self.direct_po_table.item(row, 5).setText("" if part_number is None else str(part_number))

            items.append({"catalog_item_id": int(catalog_item_id), "desc": desc, "qty": qty, "price": price})
            price_updates.append((int(catalog_item_id), price, part_number))

        return {
            "work_order_id": work_order_id,
            "vendor_id": vendor_id,
            "vendor_label": vendor_label,
            "items": items,
            "price_updates": price_updates,
            "expected_date": self.direct_eta_edit.text().strip() or None,
            "expected_note": self.direct_eta_note_edit.text().strip() or None,
        }

    def _persist_direct_purchase_order(self, status: str = "Draft") -> int:
        payload = self._harvest_direct_po_payload()
        saver = lock_purchase_order_record if status == "Locked" else save_purchase_order_draft
        po_id = saver(
            work_order_id=payload["work_order_id"],
            items=payload["items"],
            vendor_id=payload["vendor_id"],
            po_id=self.direct_active_po_id,
            expected_date=payload["expected_date"],
            expected_note=payload["expected_note"],
        )
        for catalog_item_id, price, part_number in payload["price_updates"]:
            record_purchase_order_price(
                catalog_item_id,
                price,
                vendor_name=payload["vendor_label"],
                vendor_id=payload["vendor_id"],
                vendor_part_number=part_number,
            )
        self.direct_active_po_id = int(po_id)
        self.direct_current_status = status
        self.direct_status_label.setText(f"PO #{po_id} saved as {status}")
        self.load_pipeline()
        self.refresh_direct_po_choices()
        return int(po_id)

    def save_direct_purchase_order_draft(self) -> None:
        try:
            po_id = self._persist_direct_purchase_order(status="Draft")
            self.select_purchase_order_in_pipeline(po_id, prefer_direct=True)
            QMessageBox.information(self, "Draft Saved", f"Purchase Order #{po_id} draft saved to the database.")
        except Exception as exc:
            QMessageBox.critical(self, "Save Draft Error", str(exc))

    def _get_direct_preview_status(self) -> str:
        return "Draft" if not self.direct_active_po_id or self.direct_current_status == "Draft" else "Locked"

    def lock_direct_purchase_order(self) -> None:
        try:
            po_id = self._persist_direct_purchase_order(status="Locked")
            self.select_purchase_order_in_pipeline(po_id, prefer_direct=False)
            QMessageBox.information(self, "PO Locked", f"Purchase Order #{po_id} was locked and is ready for release.")
        except Exception as exc:
            QMessageBox.critical(self, "Lock Error", str(exc))

    def preview_direct_purchase_order(self) -> None:
        try:
            po_id = self._persist_direct_purchase_order(status=self._get_direct_preview_status())
            self.active_po_id = po_id
            self.render_review_document()
            self.notebook.setCurrentWidget(self.tab_document)
        except Exception as exc:
            QMessageBox.critical(self, "Preview Error", str(exc))

    def export_direct_purchase_order(self) -> None:
        try:
            po_id = self._persist_direct_purchase_order(status=self._get_direct_preview_status())
            self.active_po_id = po_id
            self.on_export_to_folder()
            self.direct_status_label.setText(f"PO #{po_id} document archived to the project folder")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def send_direct_purchase_order(self) -> None:
        try:
            po_id = self._persist_direct_purchase_order(status="Locked")
            self.active_po_id = po_id
            self.on_send_po()
            self.direct_current_status = "Sent"
            self.direct_status_label.setText(f"PO #{po_id} sent to wholesaler")
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def select_purchase_order_in_pipeline(self, po_id: int, prefer_direct: bool = False) -> None:
        target = str(po_id)
        for row in range(self.po_table.rowCount()):
            if self.po_table.item(row, 0).text() == target:
                self.po_table.selectRow(row)
                status_text = str(self.po_table.item(row, 4).text() or "").strip()
                if prefer_direct or status_text == "Draft":
                    self.load_direct_purchase_order(po_id, select_tab=True)
                else:
                    self.load_existing_purchase_order(po_id, select_tab=True)
                return

    def load_direct_purchase_order(self, po_id: int, select_tab: bool = True) -> None:
        try:
            po_id = int(po_id)
            header = get_po_export_data(po_id)
            if not header:
                raise ValueError(f"Purchase Order #{po_id} was not found.")
            self.direct_active_po_id = po_id
            self.direct_current_status = str(header.get("Status") or "Draft")
            self.direct_status_label.setText(f"Editing PO #{po_id} [{self.direct_current_status}]")
            work_order_id = int(header.get("WorkOrderID") or 0)
            vendor_name = str(header.get("VendorName") or "").strip()
            self.direct_wo_combo.setCurrentText(
                next((label for label, value in self.direct_workorder_lookup.items() if value == work_order_id), "")
            )
            self.direct_vendor_combo.setCurrentText(vendor_name)
            eta_value = header.get("ExpectedArrivalDate")
            self.direct_eta_edit.setText(eta_value.isoformat() if hasattr(eta_value, "isoformat") else (eta_value or ""))
            self.direct_eta_note_edit.setText(str(header.get("ExpectedArrivalNote") or ""))
            self.direct_po_table.setRowCount(0)
            for row_data in get_purchase_order_edit_items(po_id):
                qty = float(row_data.get("QuantityOrdered") or 0)
                price = float(row_data.get("UnitPriceAtOrder") or 0)
                row = self.direct_po_table.rowCount()
                self.direct_po_table.insertRow(row)
                values = [
                    str(row_data.get("Description") or ""),
                    f"{qty:.2f}",
                    f"${price:.2f}",
                    f"${qty * price:,.2f}",
                    str(row_data.get("CatalogItemID") or ""),
                    str(row_data.get("PartNumber") or ""),
                ]
                for column, value in enumerate(values):
                    self._set_item(self.direct_po_table, row, column, value, center=column == 1, align_right=column in (2, 3))
            self.refresh_direct_po_total()
            if select_tab:
                self.notebook.setCurrentWidget(self.tab_direct_po)
        except Exception as exc:
            QMessageBox.critical(self, "Draft Load Error", str(exc))

    def load_existing_purchase_order(self, po_id: int, select_tab: bool = True) -> None:
        try:
            po_id = int(po_id)
            header = get_po_export_data(po_id)
            if not header:
                raise ValueError(f"Purchase Order #{po_id} was not found.")
            followup = get_po_followup_status(po_id)
            self.active_po_id = po_id
            self.active_rfq_id = None
            self.wo_resolution = None
            self.current_vendor_id = header.get("VendorID")
            self.build_po_readonly = True
            self.wo_edit.setReadOnly(True)
            self.wo_edit.setText(str(header.get("WorkOrderID") or ""))
            self.po_vendor_label.setText(str(header.get("VendorName") or ""))
            self.po_sent_to_label.setText(str(followup.get("recipient_email") or get_vendor_email_for_po(po_id) or "No vendor email on file"))
            sent_on = str(followup.get("sent_at") or "").replace("T", " ")[:16]
            self.po_sent_on_label.setText(sent_on or "Not sent yet")
            eta_parts = []
            if followup.get("eta_date"):
                eta_parts.append(str(followup.get("eta_date")))
            eta_text = str(followup.get("eta_text") or "").strip()
            if eta_text and eta_text not in eta_parts:
                eta_parts.append(eta_text)
            self.po_eta_label.setText(" | ".join(eta_parts) if eta_parts else "No ETA logged")
            status_text = header.get("Status") or followup.get("po_status") or "Open"
            self.lbl_po_status.setText(f"PO #{po_id} is {status_text}")
            self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")

            self.po_item_table.setRowCount(0)
            for row_data in get_po_items_with_receiving(po_id):
                row = self.po_item_table.rowCount()
                self.po_item_table.insertRow(row)
                values = [
                    str(row_data["POItemID"]),
                    str(row_data["Description"]),
                    f"{float(row_data.get('Remaining') or 0):.2f}",
                    f"{float(row_data.get('QuantityOrdered') or 0):.2f}",
                    f"${float(row_data.get('UnitPriceAtOrder') or 0):.2f}",
                ]
                for column, value in enumerate(values):
                    self._set_item(self.po_item_table, row, column, value, center=column in (2, 3), align_right=column == 4)

            self.reset_receiving_session()
            self.receiving_table.setRowCount(0)
            for row_data in get_po_items_with_receiving(po_id):
                row = self.receiving_table.rowCount()
                self.receiving_table.insertRow(row)
                values = [
                    str(row_data["POItemID"]),
                    str(row_data["Description"]),
                    str(row_data["QuantityOrdered"]),
                    str(row_data.get("QuantityReceived") or 0),
                    str(row_data["Remaining"]),
                    str(row_data.get("LastReceivedDate") or "Not Arrived"),
                    "0.00",
                ]
                for column, value in enumerate(values):
                    self._set_item(self.receiving_table, row, column, value, center=column in (2, 3, 4, 5, 6))

            self.btn_create_po.setEnabled(False)
            self.btn_view_po.setEnabled(True)
            self.btn_export_po.setEnabled(True)
            self.btn_send_po.setEnabled(True)
            self.render_review_document()
            if select_tab:
                self.notebook.setCurrentWidget(self.tab_build_po)
        except Exception as exc:
            QMessageBox.critical(self, "PO Load Error", str(exc))

    def on_attach_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Quote",
            "",
            "Documents (*.pdf *.docx *.jpg *.png);;All Files (*.*)",
        )
        if file_path:
            self.selected_quote_path = file_path
            self.attach_label.setText(f"📎 {os.path.basename(file_path)}")
            self.attach_label.setStyleSheet("color: green;")

    def load_estimates(self) -> None:
        try:
            self.estimates = get_all_estimates()
            self.est_combo.blockSignals(True)
            self.est_combo.clear()
            self.est_combo.addItems([f"{est['EstimateID']} - {est['Description']}" for est in self.estimates])
            self.est_combo.blockSignals(False)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _set_vendor_selection(self, vendor_name: str) -> None:
        self.selected_vendor_name = vendor_name or ""
        button = self.vendor_buttons.get(self.selected_vendor_name)
        if button:
            button.setChecked(True)

    def _rebuild_vendor_buttons(self, quoted: list[str]) -> None:
        while self.vendor_inner_layout.count():
            item = self.vendor_inner_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.vendor_buttons.clear()
        self.vendor_button_group = QButtonGroup(self)
        self.vendor_button_group.setExclusive(True)

        for vendor_name in get_all_vendors():
            button = QRadioButton(vendor_name + (" (Sent)" if vendor_name in quoted else ""))
            if vendor_name in quoted:
                button.setStyleSheet("color: gray;")
            button.clicked.connect(lambda checked=False, name=vendor_name: self._on_vendor_button_clicked(name))
            self.vendor_buttons[vendor_name] = button
            self.vendor_button_group.addButton(button)
            self.vendor_inner_layout.addWidget(button)

    def _on_vendor_button_clicked(self, vendor_name: str) -> None:
        self.selected_vendor_name = vendor_name
        self.render_rfq_preview_for_selection()

    def on_estimate_select(self, selection: str) -> None:
        if not selection:
            return
        self.active_rfq_id = None
        self.current_estimate_id = int(selection.split(" - ")[0])
        self.master_table.setRowCount(0)
        self.package_table.setRowCount(0)
        try:
            quoted = get_quoted_vendors_for_estimate(self.current_estimate_id)
            self._rebuild_vendor_buttons(quoted)
            for mat in get_estimate_materials(self.current_estimate_id):
                material_id = (
                    mat.get("EstimateMaterialID")
                    or mat.get("estimate_material_id")
                    or mat.get("MaterialID")
                    or mat.get("materialid")
                    or 0
                )
                row = self.master_table.rowCount()
                self.master_table.insertRow(row)
                self._set_item(self.master_table, row, 0, str(material_id))
                self._set_item(self.master_table, row, 1, str(mat["Quantity"]), center=True)
                self._set_item(self.master_table, row, 2, str(mat["Description"]))
        except Exception as exc:
            print(exc)
        self.render_rfq_preview_for_selection()

    def _move_selected_rows(self, source: QTableWidget, target: QTableWidget) -> None:
        rows = sorted({item.row() for item in source.selectedItems()}, reverse=True)
        for row in reversed(rows):
            values = [source.item(row, col).text() for col in range(3)]
            target_row = target.rowCount()
            target.insertRow(target_row)
            for col, value in enumerate(values):
                self._set_item(target, target_row, col, value, center=col == 1)
        for row in rows:
            source.removeRow(row)
        self.render_rfq_preview_for_selection()

    def mock_add(self) -> None:
        self._move_selected_rows(self.master_table, self.package_table)

    def mock_remove(self) -> None:
        self._move_selected_rows(self.package_table, self.master_table)

    def _move_all_rows(self, source: QTableWidget, target: QTableWidget) -> None:
        while source.rowCount():
            values = [source.item(0, col).text() for col in range(3)]
            row = target.rowCount()
            target.insertRow(row)
            for col, value in enumerate(values):
                self._set_item(target, row, col, value, center=col == 1)
            source.removeRow(0)
        self.render_rfq_preview_for_selection()

    def mock_add_all(self) -> None:
        self._move_all_rows(self.master_table, self.package_table)

    def mock_remove_all(self) -> None:
        self._move_all_rows(self.package_table, self.master_table)

    def generate_and_save_rfq(self) -> None:
        vendor_name = self.selected_vendor_name.strip()
        if not self.current_estimate_id or not vendor_name or not self.due_date_edit.text().strip() or self.package_table.rowCount() == 0:
            QMessageBox.warning(self, "Stop", "Complete all fields and add materials.")
            return
        try:
            material_ids = [self.package_table.item(row, 0).text() for row in range(self.package_table.rowCount())]
            rfq_id = save_rfq_package(self.current_estimate_id, vendor_name, self.due_date_edit.text().strip(), material_ids)
            self.active_rfq_id = int(rfq_id)
            QMessageBox.information(self, "Success", f"RFQ #{rfq_id} draft saved. Review it, then send it to the vendor.")
            self.load_pipeline()
            self.render_rfq_preview_for_selection()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def render_rfq_preview_for_selection(self) -> None:
        try:
            vendor_name = self.selected_vendor_name.strip()
            material_ids = [self.package_table.item(row, 0).text() for row in range(self.package_table.rowCount())]
            if not self.current_estimate_id or not vendor_name or not material_ids:
                preview_text = "Select an estimate, choose a vendor, and package materials to review the RFQ."
            else:
                preview_text = build_rfq_preview_text(
                    self.current_estimate_id,
                    vendor_name,
                    due_date=self.due_date_edit.text().strip() or None,
                    material_ids=material_ids,
                    rfq_id=self.active_rfq_id,
                )
        except Exception as exc:
            preview_text = f"Preview unavailable:\n{exc}"
        self.rfq_preview.setPlainText(preview_text)

    def preview_current_rfq(self) -> None:
        self.render_rfq_preview_for_selection()

    def send_current_rfq(self) -> None:
        if not self.current_estimate_id:
            QMessageBox.warning(self, "Stop", "Select an estimate and prepare an RFQ first.")
            return
        try:
            if not self.active_rfq_id:
                self.generate_and_save_rfq()
                if not self.active_rfq_id:
                    return
            result = send_rfq_by_id(self.active_rfq_id)
            QMessageBox.information(self, "RFQ Sent", f"RFQ #{self.active_rfq_id} was sent to {result['recipient_email']}.")
            self.load_pipeline()
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def on_matrix_double_click(self, row: int, column: int) -> None:
        if column != 6:
            return
        current_price = float(str(self.matrix_table.item(row, 6).text()).replace("$", "").strip() or 0)
        def commit(new_text: str) -> None:
            try:
                new_price = float(new_text.strip() or 0)
                pr_item_id = int(self.matrix_table.item(row, 0).text())
                qty = float(self.matrix_table.item(row, 3).text())
                update_rfq_item_price(pr_item_id, new_price)
                self.matrix_table.item(row, 6).setText(f"${new_price:.2f}")
                self.matrix_table.item(row, 7).setText(f"${new_price * qty:.2f}")
            except ValueError:
                return

        self._show_inline_editor(self.matrix_table, row, column, f"{current_price:.2f}", commit)

    def on_matrix_click(self, row: int, column: int) -> None:
        if column != 2:
            return
        item = self.matrix_table.item(row, 2)
        item.setText("[ ]" if item.text() == "[✓]" else "[✓]")

    def on_carry_click(self) -> None:
        try:
            rfq_row = self._selected_row(self.rfq_table)
            if rfq_row is None or not self.quote_no_edit.text().strip():
                QMessageBox.warning(self, "Stop", "Missing Quote Info.")
                return
            all_items = []
            carried = []
            for row in range(self.matrix_table.rowCount()):
                pr_item_id = int(self.matrix_table.item(row, 0).text())
                material_id = int(self.matrix_table.item(row, 1).text())
                quote_unit = float(str(self.matrix_table.item(row, 6).text()).replace("$", "").strip() or 0)
                all_items.append((pr_item_id, material_id, quote_unit))
                if self.matrix_table.item(row, 2).text() == "[✓]":
                    carried.append(material_id)
            rfq_id = int(self.rfq_table.item(rfq_row, 0).text())
            result = save_quote_and_carry_items(
                self.current_estimate_id,
                rfq_id,
                self.quote_no_edit.text().strip(),
                self.quote_date_edit.text().strip(),
                all_items,
                carried,
                self.selected_quote_path,
            )
            if result.get("saved") and result.get("estimate_pricing_updated"):
                QMessageBox.information(self, "Success", "Quote saved. Draft estimate pricing was updated.")
            elif result.get("saved"):
                QMessageBox.information(
                    self,
                    "Saved",
                    f"Quote saved. Estimate snapshot stayed frozen because the estimate status is {result.get('estimate_status')}.",
                )
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def check_wo_lock(self, *_args) -> None:
        if self.build_po_readonly:
            return
        self.refresh_po_state_for_current_selection()

    def refresh_po_state_for_current_selection(self) -> None:
        if self.build_po_readonly:
            return
        wo_value = self.wo_edit.text().strip()
        can_create_po = bool(self.wo_resolution and self.wo_resolution.get("can_create_po"))
        has_work_order = can_create_po and wo_value not in ["", "NO OPEN WO"]
        existing_po = None
        if has_work_order and self.current_vendor_id:
            try:
                existing_po = get_existing_po_details(int(wo_value), self.current_vendor_id)
            except Exception as exc:
                print(f"PO State Error: {exc}")

        if existing_po:
            self.active_po_id = existing_po["PurchaseOrderID"]
            self.lbl_po_status.setText(f"PO #{self.active_po_id} is {existing_po['Status']}")
            self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
            self.btn_create_po.setEnabled(False)
            self.btn_view_po.setEnabled(True)
            self.btn_export_po.setEnabled(True)
            self.btn_send_po.setEnabled(True)
            if existing_po.get("ShippingDocuments"):
                self.lbl_po_status.setText(f"PO #{self.active_po_id} is {existing_po['Status']} and DOCX is archived")
                self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
        else:
            self.active_po_id = None
            if self.wo_resolution and not self.wo_resolution.get("can_create_po"):
                self.lbl_po_status.setText(self.wo_resolution.get("message") or "No open work order available.")
                self.lbl_po_status.setStyleSheet("font-weight: 700; color: red;")
            else:
                self.lbl_po_status.setText("No locked PO yet")
                self.lbl_po_status.setStyleSheet("font-weight: 700; color: gray;")
            self.btn_create_po.setEnabled(has_work_order)
            self.btn_view_po.setEnabled(False)
            self.btn_export_po.setEnabled(False)
            self.btn_send_po.setEnabled(False)

    def on_create_po(self) -> None:
        wo_id = self.wo_edit.text().strip()
        if not self.wo_resolution or not self.wo_resolution.get("can_create_po"):
            QMessageBox.critical(
                self,
                "Cannot Create Purchase Order",
                (self.wo_resolution or {}).get("message") or "An open work order is required before ordering materials.",
            )
            return
        items = []
        for row in range(self.po_item_table.rowCount()):
            items.append(
                {
                    "mat_id": self.po_item_table.item(row, 0).text(),
                    "desc": self.po_item_table.item(row, 1).text(),
                    "qty": float(self.po_item_table.item(row, 3).text()),
                    "price": float(str(self.po_item_table.item(row, 4).text()).replace("$", "").replace(",", "").strip() or 0),
                }
            )
        if not items:
            return
        try:
            self.active_po_id = save_new_purchase_order(int(wo_id), items, vendor_id=self.current_vendor_id, status="Locked")
            self.load_pipeline()
            self.load_existing_purchase_order(self.active_po_id, select_tab=False)
            self.notebook.setCurrentWidget(self.tab_document)
            self.lbl_po_status.setText(f"PO #{self.active_po_id} locked and materials committed")
            self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def on_view_po(self) -> None:
        if not self.active_po_id:
            return
        self.render_review_document()
        self.notebook.setCurrentWidget(self.tab_document)

    def render_review_document(self) -> None:
        try:
            header = get_po_export_data(self.active_po_id)
            items = get_po_items(self.active_po_id)
            vendor_name = header.get("VendorName", "N/A")
            vendor_acct = header.get("AccountNumber", "N/A")
            site_name = header.get("SiteName", "N/A")
            site_addr = f"{header.get('StreetNumber', '')} {header.get('StreetName', '')}".strip()
            lines = [
                f"{'PURCHASE ORDER - INTERNAL REVIEW':^80}",
                "",
                "=" * 80,
                f"{'VENDOR:':<40}{'SHIP TO SITE:':<40}",
                f"{vendor_name:<40}{site_name:<40}",
                f"Acct: {vendor_acct:<34}{site_addr:<40}",
                "-" * 80,
                f"PO NUMBER: {str(header.get('PONumber')):<29}DATE: {str(header.get('Date')):<40}",
                "=" * 80,
                f"\n{'QTY':<10}{'DESCRIPTION':<40}{'UNIT PRICE':>14}{'TOTAL':>16}\n" + "-" * 80,
            ]
            total = 0.0
            for item in items:
                qty = float(item.get("QuantityOrdered") or 0)
                price = float(item.get("UnitPriceAtOrder") or 0)
                line_total = float(item.get("LineTotal") or 0)
                total += line_total
                desc = str(item.get("Description") or "")
                if len(desc) > 38:
                    desc = desc[:35] + "..."
                lines.append(f"{qty:<10.2f}{desc:<40}${price:>13,.2f}${line_total:>15,.2f}")
            lines.extend(["", "=" * 80, f"{'GRAND TOTAL:':<64}${total:>15,.2f}", "=" * 80])
            self.doc_view.setPlainText("\n".join(lines))
        except Exception as exc:
            print(f"Render Error: {exc}")

    def on_export_to_folder(self) -> None:
        if not self.active_po_id:
            return
        try:
            path = archive_purchase_order_docx(self.active_po_id)
            target_dir = os.path.dirname(path)
            self.lbl_po_status.setText(f"PO #{self.active_po_id} DOCX archived for sweeper send")
            self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
            try:
                os.startfile(target_dir)
            except Exception:
                pass
            QMessageBox.information(
                self,
                "PO Archived",
                f"Purchase Order #{self.active_po_id} was saved to:\n{path}\n\nFolder:\n{target_dir}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def on_send_po(self) -> None:
        if not self.active_po_id:
            return
        try:
            result = send_purchase_order_now(self.active_po_id)
            self.lbl_po_status.setText(f"PO #{self.active_po_id} sent to {result['recipient_email']}")
            self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
            self.load_pipeline()
            self.load_existing_purchase_order(self.active_po_id, select_tab=False)
            QMessageBox.information(
                self,
                "PO Sent",
                f"Purchase Order #{self.active_po_id} was sent to {result['recipient_email']}.\n\n"
                f"Attachment:\n{result.get('attachment_path') or result.get('pdf_path') or result.get('docx_path') or 'Generated during send'}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def reset_receiving_session(self) -> None:
        self.is_receiving_mode = False
        self.receiving_slip_edit.setText("")
        self.receiving_date_edit.setText(datetime.date.today().strftime("%Y-%m-%d"))
        self.btn_start_receiving.setText("Start Receiving")
        self.btn_start_receiving.setEnabled(True)
        self.btn_save_receiving.setEnabled(False)
        self.receiving_slip_edit.setEnabled(True)

    def start_receiving_session(self) -> None:
        if not self.active_po_id:
            QMessageBox.warning(self, "Stop", "Select a purchase order first.")
            return
        if not self.receiving_slip_edit.text().strip():
            QMessageBox.warning(self, "Stop", "Enter a packing slip number before receiving materials.")
            return
        self.is_receiving_mode = True
        self.btn_start_receiving.setText("Receiving Session Active")
        self.btn_start_receiving.setEnabled(False)
        self.btn_save_receiving.setEnabled(True)
        self.receiving_slip_edit.setEnabled(False)
        QMessageBox.information(self, "Ready", "Receiving session started. Double-click 'Arriving Now' to enter quantities.")

    def on_receiving_double_click(self, row: int, column: int) -> None:
        if not self.is_receiving_mode:
            QMessageBox.warning(self, "Locked", "Start a receiving session before editing quantities.")
            return
        if column != 6:
            return
        left_to_arrive = float(self.receiving_table.item(row, 4).text() or 0)
        current_value = float(self.receiving_table.item(row, 6).text() or 0)
        def commit(new_text: str) -> None:
            try:
                arriving_now = max(0.0, float(new_text.strip() or 0))
            except ValueError:
                return
            if left_to_arrive <= 0 and arriving_now > 0:
                confirm = QMessageBox.question(
                    self,
                    "Complete",
                    "This item is already fully received. Save an overage anyway?",
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
            self.receiving_table.item(row, 6).setText(f"{arriving_now:.2f}")

        self._show_inline_editor(self.receiving_table, row, column, f"{current_value:.2f}", commit)

    def save_receiving_batch(self) -> None:
        if not self.active_po_id:
            QMessageBox.warning(self, "Stop", "Select a purchase order first.")
            return
        items_to_save = []
        for row in range(self.receiving_table.rowCount()):
            arriving_now = float(self.receiving_table.item(row, 6).text() or 0)
            left_to_arrive = float(self.receiving_table.item(row, 4).text() or 0)
            if arriving_now <= 0:
                continue
            if arriving_now > left_to_arrive:
                confirm = QMessageBox.question(
                    self,
                    "Overage Detected",
                    f"You are receiving {arriving_now} of '{self.receiving_table.item(row, 1).text()}', but only {left_to_arrive} are outstanding. Save anyway?",
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
            items_to_save.append((int(self.receiving_table.item(row, 0).text()), arriving_now))
        if not items_to_save:
            QMessageBox.information(self, "Empty", "No items were marked as arriving now.")
            return
        try:
            log_material_receipt_batch(
                packing_slip=self.receiving_slip_edit.text().strip(),
                receive_date=self.receiving_date_edit.text().strip(),
                items_arriving=items_to_save,
            )
            QMessageBox.information(self, "Success", f"Receipt saved for Packing Slip #{self.receiving_slip_edit.text().strip()}.")
            self.receiving_table.setRowCount(0)
            for row_data in get_po_items_with_receiving(self.active_po_id):
                row = self.receiving_table.rowCount()
                self.receiving_table.insertRow(row)
                values = [
                    str(row_data["POItemID"]),
                    str(row_data["Description"]),
                    str(row_data["QuantityOrdered"]),
                    str(row_data.get("QuantityReceived") or 0),
                    str(row_data["Remaining"]),
                    str(row_data.get("LastReceivedDate") or "Not Arrived"),
                    "0.00",
                ]
                for column, value in enumerate(values):
                    self._set_item(self.receiving_table, row, column, value, center=column in (2, 3, 4, 5, 6))
            self.load_pipeline()
            self.reset_receiving_session()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to save receipt: {exc}")

    def on_po_item_double_click(self, row: int, column: int) -> None:
        if self.build_po_readonly:
            return
        if column != 3:
            return
        remaining = float(self.po_item_table.item(row, 2).text())
        current_qty = float(self.po_item_table.item(row, 3).text())
        def commit(new_text: str) -> None:
            try:
                new_qty = float(new_text.strip() or 0)
            except ValueError:
                return
            clamped = min(max(new_qty, 0.0), remaining)
            self.po_item_table.item(row, 3).setText(f"{clamped:.2f}")

        self._show_inline_editor(self.po_item_table, row, column, f"{current_qty:.2f}", commit)
