from __future__ import annotations

import datetime
from html import escape
import os
from typing import Any, Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QTextBrowser,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.estimates import (
    get_all_estimates,
    get_estimate_materials,
    list_available_draft_estimates_for_material_request,
)
from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.models import DocumentTemplateKind
from neon_ai.document_control.repository import DocumentControlRepository
from neon_ai.database.materials import (
    get_material_price_for_vendor,
    record_purchase_order_price,
    search_materials_by_part_number,
)
from neon_ai.database.purchases import (
    archive_purchase_order_docx,
    get_carried_items_for_po_builder,
    get_carried_items_for_po_center,
    get_existing_po_details,
    get_po_export_data,
    get_po_followup_status,
    get_po_items,
    get_po_items_with_receiving,
    get_purchase_order_for_material_request,
    get_purchase_order_edit_items,
    get_purchase_order_items_for_material_request,
    get_purchase_orders_for_pipeline,
    get_purchase_order_source_links,
    get_vendor_email_for_po,
    get_vendors,
    lock_purchase_order_record,
    log_material_receipt_batch,
    save_new_purchase_order,
    save_purchase_order_draft,
)
from neon_ai.database.rfq import (
    build_rfq_preview_text,
    create_manual_rfq_draft,
    duplicate_rfq_for_vendor,
    get_active_rfqs,
    get_all_vendors,
    get_bid_compare_data,
    get_quoted_vendors_for_estimate,
    get_rfq_for_material_request,
    get_rfq_header_data,
    get_rfq_items_for_material_request,
    get_rfq_items_for_matrix,
    get_wo_resolution_for_rfq,
    save_bid_compare_carried_selections,
    save_quote_and_carry_items,
    save_rfq_package,
    send_rfq_by_id,
    update_manual_rfq_draft,
    update_rfq_item_price,
)
from neon_ai.database.timesheets import (
    get_open_workorder_choices,
    list_open_work_orders_for_material_request,
)
from neon_ai.services.purchase_order_send_service import (
    prepare_purchase_order_delivery_message,
    send_purchase_order,
)
from neon_ai.services.rfq_send_service import (
    prepare_rfq_batch_preview,
    prepare_rfq_delivery_message,
    render_rfq_delivery_preview_context,
    send_rfq,
)


class RFQViewerPage(QWidget):
    RFQ_DELIVERY_DOCUMENT_TYPE_CODE = "RFQ_DELIVERY"
    RFQ_SEND_USAGE_CONTEXT = "RFQ_SEND"

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
        self.direct_estimate_lookup: dict[str, int] = {}
        self.direct_source_lookup: dict[str, int] = {}
        self.direct_workorder_lookup: dict[str, int] = {}
        self.direct_vendor_lookup: dict[str, int] = {}
        self.direct_active_rfq_id: int | None = None
        self.direct_active_rfq_source_type: str | None = None
        self.direct_active_rfq_source_id: int | None = None
        self.direct_active_rfq_vendor_id: int | None = None
        self.direct_active_po_id: int | None = None
        self.direct_active_po_source_id: int | None = None
        self.direct_active_po_vendor_id: int | None = None
        self.direct_current_status = "Draft"
        self.direct_workspace_readonly = False
        self.build_po_readonly = False
        self.procurement_center = "RFQ"
        self.bid_compare_current_rfq_id: int | None = None
        self.bid_compare_vendor_rows: list[dict] = []
        self.bid_compare_vendor_column_map: dict[int, tuple[int, int]] = {}
        self.po_carried_source_rows: list[dict] = []
        self.rfq_preview_dirty = False
        self.rfq_preview_is_html = False
        self.rfq_preview_refresh_pending = False
        self.rfq_preview_refresh_reason = ""
        self._suspend_rfq_preview_dirty_tracking = False
        self.selected_vendor_name = ""
        self.vendor_buttons: dict[str, QRadioButton] = {}
        self._rfq_template_choices_by_kind: dict[str, list[object]] = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        self._rfq_template_defaults_by_kind: dict[str, int | None] = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }

        self.direct_material_search_timer = QTimer(self)
        self.direct_material_search_timer.setSingleShot(True)
        self.direct_material_search_timer.timeout.connect(self._perform_direct_material_search)

        self._build_ui()

    def _get_estimate_id_from_rfq_context(self, row: dict[str, Any] | None) -> int | None:
        if not isinstance(row, dict):
            return None
        # Accept legacy aliases from older RFQ context payloads, but keep EstimateID canonical.
        for key in ("EstimateID", "EstimateId", "estimate_id", "EstimatedID"):
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _require_estimate_id_from_rfq_context(
        self,
        row: dict[str, Any] | None,
        *,
        rfq_id: int | None = None,
        action_label: str = "continue",
    ) -> int:
        estimate_id = self._get_estimate_id_from_rfq_context(row)
        if estimate_id is not None:
            return estimate_id
        rfq_label = f"RFQ #{rfq_id}" if rfq_id else "This RFQ"
        raise ValueError(
            f"{rfq_label} is missing estimate context. Reload the RFQ draft or save it again before trying to {action_label}."
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Material Procurement Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_data)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_label = QLabel("Active Center:")
        badge_label.setStyleSheet("color: #6c757d; font-size: 11px;")
        self.procurement_center_badge = QLabel("RFQ Center")
        self.procurement_center_badge.setStyleSheet(
            "background-color: #eaf7ee; color: #1e8449; border: 1px solid #b7e1c1; "
            "border-radius: 10px; padding: 3px 10px; font-weight: 700;"
        )
        badge_row.addWidget(badge_label)
        badge_row.addWidget(self.procurement_center_badge)
        badge_row.addStretch(1)
        layout.addLayout(badge_row)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(main_splitter, 1)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(self.left_splitter)

        self.rfq_group = QGroupBox("Active RFQs")
        rfq_layout = QVBoxLayout(self.rfq_group)
        self.rfq_table = QTableWidget(0, 9)
        self.rfq_table.setHorizontalHeaderLabels(["RFQ_ID", "Est #", "RFQ #", "Site", "Vendor", "Sent", "Due", "Status", "VendorID"])
        self.rfq_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rfq_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rfq_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rfq_table.verticalHeader().setVisible(False)
        self.rfq_table.setColumnHidden(0, True)
        self.rfq_table.setColumnHidden(8, True)
        self.rfq_table.setColumnWidth(1, 50)
        self.rfq_table.setColumnWidth(2, 55)
        self.rfq_table.setColumnWidth(3, 120)
        self.rfq_table.setColumnWidth(4, 100)
        self.rfq_table.setColumnWidth(5, 90)
        self.rfq_table.setColumnWidth(6, 80)
        self.rfq_table.setColumnWidth(7, 80)
        self.rfq_table.itemSelectionChanged.connect(self.on_rfq_select)
        rfq_layout.addWidget(self.rfq_table)
        build_po_button = QPushButton("↳ Build PO from selected RFQ")
        build_po_button.clicked.connect(lambda: self._set_procurement_tab(self.tab_build_po, center="PO"))
        rfq_layout.addWidget(build_po_button)
        preview_batch_button = QPushButton("Preview RFQ Batch")
        preview_batch_button.clicked.connect(self.preview_selected_rfq_batch)
        rfq_layout.addWidget(preview_batch_button)
        self.left_splitter.addWidget(self.rfq_group)

        self.po_group = QGroupBox("Purchase Orders")
        po_layout = QVBoxLayout(self.po_group)
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
        self.left_splitter.addWidget(self.po_group)

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
        self.tab_bid_compare = QWidget()
        self.tab_build_po = QWidget()
        self.tab_receiving = QWidget()
        self.tab_document = QWidget()
        self.notebook.addTab(self.tab_direct_po, "0. Material Request")
        self.notebook.addTab(self.tab_build_rfq, "📦 1. Build RFQ")
        self.notebook.addTab(self.tab_matrix, "📊 2. Bid Matrix")
        self.notebook.addTab(self.tab_bid_compare, "📊 3. Bid Compare")
        self.notebook.addTab(self.tab_build_po, "🛒 3. Build PO")
        self.notebook.addTab(self.tab_receiving, "🚚 4. Receiving")
        self.notebook.addTab(self.tab_document, "📄 5. PO Document")

        self.build_direct_po_tab_ui()
        self.build_package_ui()
        self.build_matrix_ui()
        self.build_bid_compare_shell_ui()
        self.build_po_tab_ui()
        self.build_receiving_tab_ui()
        self.build_document_tab_ui()
        self.load_rfq_template_options()
        self.set_procurement_center("RFQ")

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
                    str(row_data.get("PriceRequestID") or ""),
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
                        center=column in (1, 2, 5, 6, 7),
                        color=Qt.GlobalColor.red if overdue and column not in (0, 8) else None,
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
        header = QGroupBox("Material Request Workspace")
        header_layout = QGridLayout(header)
        layout.addWidget(header)

        header_layout.addWidget(QLabel("Action Type:"), 0, 0)
        self.direct_action_type_combo = QComboBox()
        self.direct_action_type_combo.addItem("RFQ", "RFQ")
        self.direct_action_type_combo.addItem("Purchase Order", "PurchaseOrder")
        self.direct_action_type_combo.setCurrentIndex(1)
        header_layout.addWidget(self.direct_action_type_combo, 0, 1)

        header_layout.addWidget(QLabel("Source Type:"), 0, 2)
        self.direct_source_type_combo = QComboBox()
        self.direct_source_type_combo.addItem("Estimate", "Estimate")
        self.direct_source_type_combo.addItem("Work Order", "WorkOrder")
        self.direct_source_type_combo.setCurrentIndex(1)
        header_layout.addWidget(self.direct_source_type_combo, 0, 3)

        self.direct_new_request_button = QPushButton("New RFQ/PO")
        self.direct_new_request_button.clicked.connect(self.start_new_material_request)
        header_layout.addWidget(self.direct_new_request_button, 0, 4)

        self.direct_dup_rfq_button = QPushButton("Dup RFQ")
        self.direct_dup_rfq_button.clicked.connect(self.duplicate_material_request_rfq)
        header_layout.addWidget(self.direct_dup_rfq_button, 1, 4)

        header_layout.addWidget(QLabel("Vendor:"), 1, 0)
        self.direct_vendor_combo = QComboBox()
        self.direct_vendor_combo.setMinimumWidth(240)
        self.direct_vendor_combo.currentTextChanged.connect(self.on_direct_vendor_select)
        header_layout.addWidget(self.direct_vendor_combo, 1, 1)

        self.direct_date_label = QLabel("Expected Arrival:")
        header_layout.addWidget(self.direct_date_label, 2, 0)
        self.direct_eta_edit = QLineEdit()
        header_layout.addWidget(self.direct_eta_edit, 2, 1)

        self.direct_source_label = QLabel("Work Order #:")
        header_layout.addWidget(self.direct_source_label, 2, 2)
        self.direct_source_combo = QComboBox()
        self.direct_source_combo.setMinimumWidth(320)
        self.direct_source_combo.currentTextChanged.connect(self.on_direct_material_request_context_changed)
        header_layout.addWidget(self.direct_source_combo, 2, 3)

        self.direct_request_note_label = QLabel("ETA Note:")
        header_layout.addWidget(self.direct_request_note_label, 3, 0)
        self.direct_eta_note_edit = QLineEdit()
        header_layout.addWidget(self.direct_eta_note_edit, 3, 1, 1, 3)

        self.direct_mode_hint_label = QLabel("")
        self.direct_mode_hint_label.setWordWrap(True)
        self.direct_mode_hint_label.setStyleSheet("color: #8a5a00;")
        header_layout.addWidget(self.direct_mode_hint_label, 4, 0, 1, 4)

        self.direct_status_label = QLabel("Material request draft workspace not saved yet")
        self.direct_status_label.setStyleSheet("font-weight: 700; color: blue;")
        header_layout.addWidget(self.direct_status_label, 5, 0, 1, 4)

        staging = QGroupBox("Material Entry")
        staging_layout = QGridLayout(staging)
        layout.addWidget(staging)

        staging_layout.addWidget(QLabel("Qty:"), 0, 0)
        self.direct_qty = QLineEdit()
        self.direct_qty.setMaximumWidth(80)
        staging_layout.addWidget(self.direct_qty, 0, 1)

        staging_layout.addWidget(QLabel("Part Number:"), 0, 2)
        self.direct_part_number_edit = QComboBox()
        self.direct_part_number_edit.setEditable(True)
        self.direct_part_number_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.direct_part_number_edit.setMinimumWidth(260)
        self.direct_part_number_edit.setPlaceholderText("Required part number")
        self.direct_part_number_edit.lineEdit().textEdited.connect(self.on_direct_part_number_search)
        self.direct_part_number_edit.activated.connect(self.on_direct_part_number_select)
        self.direct_part_number_edit.textActivated.connect(self.on_direct_part_number_select)
        self.direct_part_number_edit.current_item_id = None
        self.direct_part_number_edit.current_material_row = None
        staging_layout.addWidget(self.direct_part_number_edit, 0, 3)

        staging_layout.addWidget(QLabel("Description:"), 1, 0)
        self.direct_desc = QLineEdit()
        self.direct_desc.setMinimumWidth(420)
        self.direct_desc.setPlaceholderText("Required for unknown part numbers")
        staging_layout.addWidget(self.direct_desc, 1, 1, 1, 3)

        staging_layout.addWidget(QLabel("Unit Cost:"), 2, 0)
        self.direct_price = QLineEdit()
        self.direct_price.setMaximumWidth(120)
        staging_layout.addWidget(self.direct_price, 2, 1)

        staging_layout.addWidget(QLabel("Notes:"), 2, 2)
        self.direct_line_note_edit = QLineEdit()
        self.direct_line_note_edit.setPlaceholderText("Optional planning note")
        staging_layout.addWidget(self.direct_line_note_edit, 2, 3)

        self.direct_add_material_button = QPushButton("Add Material")
        self.direct_add_material_button.clicked.connect(self.add_direct_po_line)
        staging_layout.addWidget(self.direct_add_material_button, 0, 4, 3, 1)

        self.direct_po_table = QTableWidget(0, 7)
        self.direct_po_table.setHorizontalHeaderLabels(["Qty", "Part Number", "Description", "Unit Cost", "Line Total", "Notes", "MaterialID"])
        self.direct_po_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.direct_po_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.direct_po_table.verticalHeader().setVisible(False)
        self.direct_po_table.setColumnWidth(0, 70)
        self.direct_po_table.setColumnWidth(1, 140)
        self.direct_po_table.setColumnWidth(2, 260)
        self.direct_po_table.setColumnWidth(3, 100)
        self.direct_po_table.setColumnWidth(4, 110)
        self.direct_po_table.setColumnWidth(5, 220)
        self.direct_po_table.setColumnHidden(6, True)
        self.direct_po_table.cellDoubleClicked.connect(self.on_direct_po_double_click)
        layout.addWidget(self.direct_po_table, 1)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.direct_total_label = QLabel("MATERIAL REQUEST TOTAL: $0.00")
        self.direct_total_label.setStyleSheet("font-size: 12px; font-weight: 700; color: blue;")
        footer_layout.addWidget(self.direct_total_label)
        footer_layout.addStretch(1)
        self.direct_save_rfq_button = QPushButton("Save RFQ Draft")
        self.direct_save_rfq_button.clicked.connect(self.save_material_request_rfq_draft)
        footer_layout.addWidget(self.direct_save_rfq_button)
        self.direct_preview_rfq_button = QPushButton("Preview RFQ")
        self.direct_preview_rfq_button.clicked.connect(self.preview_material_request_rfq)
        footer_layout.addWidget(self.direct_preview_rfq_button)
        self.direct_preview_rfq_send_button = QPushButton("Preview RFQ Send")
        self.direct_preview_rfq_send_button.clicked.connect(self.preview_material_request_rfq_send)
        footer_layout.addWidget(self.direct_preview_rfq_send_button)
        self.direct_send_rfq_button = QPushButton("Send RFQ")
        self.direct_send_rfq_button.clicked.connect(self.send_material_request_rfq)
        footer_layout.addWidget(self.direct_send_rfq_button)

        self.direct_save_po_button = QPushButton("Save PO Draft")
        self.direct_save_po_button.clicked.connect(self.save_direct_purchase_order_draft)
        footer_layout.addWidget(self.direct_save_po_button)
        self.direct_lock_po_button = QPushButton("Lock PO")
        self.direct_lock_po_button.clicked.connect(self.lock_direct_purchase_order)
        footer_layout.addWidget(self.direct_lock_po_button)
        self.direct_preview_po_button = QPushButton("Preview PO")
        self.direct_preview_po_button.clicked.connect(self.preview_direct_purchase_order)
        footer_layout.addWidget(self.direct_preview_po_button)
        self.direct_export_po_button = QPushButton("Export PO")
        self.direct_export_po_button.clicked.connect(self.export_direct_purchase_order)
        footer_layout.addWidget(self.direct_export_po_button)
        self.direct_send_po_button = QPushButton("Send PO")
        self.direct_send_po_button.clicked.connect(self.send_direct_purchase_order)
        footer_layout.addWidget(self.direct_send_po_button)

        self.direct_remove_button = QPushButton("Remove Selected")
        self.direct_remove_button.clicked.connect(self.remove_direct_po_line)
        footer_layout.addWidget(self.direct_remove_button)
        self.direct_clear_button = QPushButton("Clear Draft")
        self.direct_clear_button.clicked.connect(self.clear_direct_po_draft)
        footer_layout.addWidget(self.direct_clear_button)
        layout.addWidget(footer)

        self.direct_action_type_combo.currentIndexChanged.connect(self.on_material_request_mode_changed)
        self.direct_source_type_combo.currentIndexChanged.connect(self.on_material_request_source_type_changed)
        self.refresh_direct_po_choices()
        self.on_material_request_mode_changed()

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
        action_layout.setSpacing(8)
        action_layout.addWidget(QLabel("Header:"))
        self.rfq_header_template_combo = QComboBox()
        self.rfq_header_template_combo.setMinimumWidth(170)
        self.rfq_header_template_combo.currentIndexChanged.connect(
            lambda _index: self._on_rfq_template_selection_changed("Header template changed.")
        )
        action_layout.addWidget(self.rfq_header_template_combo)
        action_layout.addWidget(QLabel("Body:"))
        self.rfq_body_template_combo = QComboBox()
        self.rfq_body_template_combo.setMinimumWidth(190)
        self.rfq_body_template_combo.currentIndexChanged.connect(
            lambda _index: self._on_rfq_template_selection_changed("Body template changed.")
        )
        action_layout.addWidget(self.rfq_body_template_combo)
        action_layout.addWidget(QLabel("Footer:"))
        self.rfq_footer_template_combo = QComboBox()
        self.rfq_footer_template_combo.setMinimumWidth(170)
        self.rfq_footer_template_combo.currentIndexChanged.connect(
            lambda _index: self._on_rfq_template_selection_changed("Footer template changed.")
        )
        action_layout.addWidget(self.rfq_footer_template_combo)
        self.rfq_rerender_preview_button = QPushButton("Re-render From Templates")
        self.rfq_rerender_preview_button.clicked.connect(self.rerender_rfq_preview_from_templates)
        action_layout.addWidget(self.rfq_rerender_preview_button)
        action_layout.addStretch(1)
        preview_button = QPushButton("Preview RFQ")
        preview_button.clicked.connect(self.preview_current_rfq)
        action_layout.addWidget(preview_button)
        preview_send_button = QPushButton("Preview RFQ Send")
        preview_send_button.clicked.connect(self.preview_rfq_send)
        action_layout.addWidget(preview_send_button)
        self.btn_generate_rfq = QPushButton("Save RFQ Draft")
        self.btn_generate_rfq.clicked.connect(self.generate_and_save_rfq)
        action_layout.addWidget(self.btn_generate_rfq)
        self.btn_send_rfq = QPushButton("Send RFQ")
        self.btn_send_rfq.clicked.connect(self.send_current_rfq)
        action_layout.addWidget(self.btn_send_rfq)
        layout.addWidget(action_row)

        preview_group = QGroupBox("RFQ Email Body Draft")
        preview_layout = QVBoxLayout(preview_group)
        self.rfq_preview_note_label = QLabel(
            "This editable body will be used for Preview RFQ Send and Send RFQ."
        )
        self.rfq_preview_note_label.setWordWrap(True)
        self.rfq_preview_note_label.setStyleSheet("color: #6c757d;")
        preview_layout.addWidget(self.rfq_preview_note_label)
        self.rfq_preview_status_label = QLabel(
            "Select an estimate or RFQ and package materials to render the email body draft."
        )
        self.rfq_preview_status_label.setWordWrap(True)
        self.rfq_preview_status_label.setStyleSheet("color: #1f4f99; font-weight: 600;")
        preview_layout.addWidget(self.rfq_preview_status_label)
        self.rfq_preview = QTextEdit()
        self.rfq_preview.setAcceptRichText(True)
        self.rfq_preview.textChanged.connect(self._on_rfq_preview_text_changed)
        preview_layout.addWidget(self.rfq_preview)
        layout.addWidget(preview_group)

        self.due_date_edit.textChanged.connect(
            lambda _text: self.request_rfq_preview_refresh("Due date changed.")
        )

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
        info_layout.addWidget(QLabel("Select Work Order:"), 0, 0)
        self.po_work_order_combo = QComboBox()
        self.po_work_order_combo.currentIndexChanged.connect(self.on_po_work_order_selected)
        info_layout.addWidget(self.po_work_order_combo, 0, 1, 1, 2)
        info_layout.addWidget(QLabel("Work Order #:"), 1, 0)
        self.wo_edit = QLineEdit()
        self.wo_edit.textChanged.connect(self.check_wo_lock)
        self.wo_edit.setReadOnly(True)
        info_layout.addWidget(self.wo_edit, 1, 1)
        info_layout.addWidget(QLabel("Vendor:"), 1, 2)
        self.po_vendor_label = QLabel("")
        self.po_vendor_label.setStyleSheet("font-weight: 700;")
        info_layout.addWidget(self.po_vendor_label, 1, 3)
        self.lbl_po_status = QLabel("")
        self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
        info_layout.addWidget(self.lbl_po_status, 1, 4)
        info_layout.addWidget(QLabel("Sent To:"), 2, 0)
        self.po_sent_to_label = QLabel("")
        info_layout.addWidget(self.po_sent_to_label, 2, 1)
        info_layout.addWidget(QLabel("Sent On:"), 2, 2)
        self.po_sent_on_label = QLabel("")
        info_layout.addWidget(self.po_sent_on_label, 2, 3)
        info_layout.addWidget(QLabel("ETA:"), 3, 0)
        self.po_eta_label = QLabel("")
        self.po_eta_label.setWordWrap(True)
        info_layout.addWidget(self.po_eta_label, 3, 1, 1, 4)
        layout.addWidget(info_group)

        source_group = QGroupBox("Carried Items Source List")
        source_layout = QVBoxLayout(source_group)
        source_filter_row = QFrame()
        source_filter_layout = QHBoxLayout(source_filter_row)
        source_filter_layout.setContentsMargins(0, 0, 0, 0)
        source_filter_layout.addWidget(QLabel("Estimate:"))
        self.po_carried_estimate_filter = QComboBox()
        self.po_carried_estimate_filter.currentIndexChanged.connect(self.reload_po_carried_source_list)
        source_filter_layout.addWidget(self.po_carried_estimate_filter)
        source_filter_layout.addWidget(QLabel("RFQ:"))
        self.po_carried_rfq_filter = QComboBox()
        self.po_carried_rfq_filter.currentIndexChanged.connect(self.reload_po_carried_source_list)
        source_filter_layout.addWidget(self.po_carried_rfq_filter)
        source_filter_layout.addWidget(QLabel("Vendor:"))
        self.po_carried_vendor_filter = QComboBox()
        self.po_carried_vendor_filter.currentIndexChanged.connect(self.reload_po_carried_source_list)
        source_filter_layout.addWidget(self.po_carried_vendor_filter)
        self.po_load_carried_button = QPushButton("Load Carried Items")
        self.po_load_carried_button.clicked.connect(self.reload_po_carried_source_list)
        source_filter_layout.addWidget(self.po_load_carried_button)
        source_filter_layout.addStretch(1)
        source_layout.addWidget(source_filter_row)

        self.po_carried_source_table = QTableWidget(0, 16)
        self.po_carried_source_table.setHorizontalHeaderLabels(
            [
                "CarryID",
                "PRItemID",
                "VendorID",
                "EstimateID",
                "MaterialID",
                "RFQ #",
                "Est #",
                "WO #",
                "Vendor",
                "Part #",
                "Description",
                "Qty",
                "Unit",
                "Unit Price",
                "Ext Price",
                "Source Type",
            ]
        )
        self.po_carried_source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.po_carried_source_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.po_carried_source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.po_carried_source_table.verticalHeader().setVisible(False)
        for hidden_col in (0, 1, 2, 3, 4):
            self.po_carried_source_table.setColumnHidden(hidden_col, True)
        self.po_carried_source_table.setColumnWidth(5, 60)
        self.po_carried_source_table.setColumnWidth(6, 60)
        self.po_carried_source_table.setColumnWidth(7, 60)
        self.po_carried_source_table.setColumnWidth(8, 140)
        self.po_carried_source_table.setColumnWidth(9, 120)
        self.po_carried_source_table.setColumnWidth(10, 260)
        self.po_carried_source_table.setColumnWidth(11, 75)
        self.po_carried_source_table.setColumnWidth(12, 60)
        self.po_carried_source_table.setColumnWidth(13, 90)
        self.po_carried_source_table.setColumnWidth(14, 90)
        self.po_carried_source_table.setColumnWidth(15, 100)
        source_layout.addWidget(self.po_carried_source_table, 1)

        source_action_row = QFrame()
        source_action_layout = QHBoxLayout(source_action_row)
        source_action_layout.setContentsMargins(0, 0, 0, 0)
        self.po_add_vendor_package_button = QPushButton("Add Filtered Vendor Package")
        self.po_add_vendor_package_button.clicked.connect(self.add_filtered_vendor_package_to_po_draft)
        source_action_layout.addWidget(self.po_add_vendor_package_button)
        self.po_add_selected_button = QPushButton("Add Selected to Current PO Draft")
        self.po_add_selected_button.clicked.connect(self.add_selected_carried_items_to_po_draft)
        source_action_layout.addWidget(self.po_add_selected_button)
        self.po_carried_status_label = QLabel("Select a work order to load carried material decisions.")
        self.po_carried_status_label.setWordWrap(True)
        source_action_layout.addWidget(self.po_carried_status_label, 1)
        source_layout.addWidget(source_action_row)
        layout.addWidget(source_group)

        draft_group = QGroupBox("Current PO Draft")
        draft_layout = QVBoxLayout(draft_group)
        self.po_item_table = QTableWidget(0, 12)
        self.po_item_table.setHorizontalHeaderLabels(
            ["MaterialID", "Desc", "Remaining", "OrderQty", "Price", "CatalogItemID", "RFQCarryID", "RFQID", "PRItemID", "VendorID", "EstimateID", "PartNumber"]
        )
        self.po_item_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.po_item_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.po_item_table.verticalHeader().setVisible(False)
        for hidden_col in (0, 5, 6, 7, 8, 9, 10, 11):
            self.po_item_table.setColumnHidden(hidden_col, True)
        self.po_item_table.setColumnWidth(1, 320)
        self.po_item_table.setColumnWidth(2, 90)
        self.po_item_table.setColumnWidth(3, 90)
        self.po_item_table.setColumnWidth(4, 90)
        self.po_item_table.cellDoubleClicked.connect(self.on_po_item_double_click)
        draft_layout.addWidget(self.po_item_table, 1)
        layout.addWidget(draft_group, 1)

        button_row = QFrame()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)
        self.btn_save_po_draft = QPushButton("Save PO Draft")
        self.btn_save_po_draft.clicked.connect(self.save_build_po_draft)
        self.btn_save_po_draft.setEnabled(False)
        button_layout.addWidget(self.btn_save_po_draft)
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
        self.load_po_work_order_choices()

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

    def build_bid_compare_shell_ui(self) -> None:
        layout = QVBoxLayout(self.tab_bid_compare)
        title = QLabel("Bid Compare")
        title.setStyleSheet("font-size: 12px; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)

        summary_group = QGroupBox("RFQ Compare Context")
        summary_layout = QGridLayout(summary_group)
        summary_layout.addWidget(QLabel("RFQ #:"), 0, 0)
        self.bid_compare_rfq_label = QLabel("No RFQ selected")
        summary_layout.addWidget(self.bid_compare_rfq_label, 0, 1)
        summary_layout.addWidget(QLabel("Estimate #:"), 0, 2)
        self.bid_compare_estimate_label = QLabel("—")
        summary_layout.addWidget(self.bid_compare_estimate_label, 0, 3)
        summary_layout.addWidget(QLabel("Work Order #:"), 0, 4)
        self.bid_compare_work_order_label = QLabel("—")
        summary_layout.addWidget(self.bid_compare_work_order_label, 0, 5)
        summary_layout.addWidget(QLabel("Status:"), 1, 0)
        self.bid_compare_status_label = QLabel("—")
        summary_layout.addWidget(self.bid_compare_status_label, 1, 1)
        summary_layout.addWidget(QLabel("Compared Vendors:"), 1, 2)
        self.bid_compare_vendor_summary_label = QLabel("Select an RFQ to compare vendor pricing.")
        self.bid_compare_vendor_summary_label.setWordWrap(True)
        summary_layout.addWidget(self.bid_compare_vendor_summary_label, 1, 3, 1, 3)
        layout.addWidget(summary_group)

        action_row = QFrame()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(QLabel("Full Package Vendor:"))
        self.bid_compare_full_vendor_combo = QComboBox()
        self.bid_compare_full_vendor_combo.addItem("Select vendor package…", None)
        self.bid_compare_full_vendor_combo.setMinimumWidth(240)
        action_layout.addWidget(self.bid_compare_full_vendor_combo)
        self.bid_compare_apply_full_vendor_btn = QPushButton("Apply Vendor to All Lines")
        self.bid_compare_apply_full_vendor_btn.clicked.connect(self.apply_bid_compare_full_package_vendor)
        action_layout.addWidget(self.bid_compare_apply_full_vendor_btn)
        self.bid_compare_save_btn = QPushButton("Save Carried Selections")
        self.bid_compare_save_btn.clicked.connect(self.save_bid_compare_selections)
        action_layout.addWidget(self.bid_compare_save_btn)
        self.bid_compare_refresh_btn = QPushButton("Refresh Compare")
        self.bid_compare_refresh_btn.clicked.connect(self.refresh_bid_compare_for_current_rfq)
        action_layout.addWidget(self.bid_compare_refresh_btn)
        action_layout.addStretch(1)
        layout.addWidget(action_row)

        note = QLabel(
            "Bid Compare v1 is decision-oriented: vendor prices come from existing RFQ quote-entry data, "
            "and this tab is used to choose which vendor price is carried per line or across the whole package."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.bid_compare_table = QTableWidget(0, 10)
        self.bid_compare_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.bid_compare_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bid_compare_table.verticalHeader().setVisible(False)
        self.bid_compare_table.setHorizontalHeaderLabels(
            [
                "MaterialID",
                "BasePRItemID",
                "Part #",
                "Description",
                "Qty",
                "Unit",
                "Carry Vendor",
                "Carry Unit",
                "Carry Ext",
                "Flags / Notes",
            ]
        )
        self.bid_compare_table.setColumnHidden(0, True)
        self.bid_compare_table.setColumnHidden(1, True)
        self.bid_compare_table.setColumnWidth(2, 120)
        self.bid_compare_table.setColumnWidth(3, 260)
        self.bid_compare_table.setColumnWidth(4, 75)
        self.bid_compare_table.setColumnWidth(5, 65)
        self.bid_compare_table.setColumnWidth(6, 180)
        self.bid_compare_table.setColumnWidth(7, 90)
        self.bid_compare_table.setColumnWidth(8, 90)
        self.bid_compare_table.setColumnWidth(9, 220)
        layout.addWidget(self.bid_compare_table, 1)

        self.clear_bid_compare_workspace()

    def clear_bid_compare_workspace(self, message: str = "Select an RFQ in RFQ Center to compare vendor pricing.") -> None:
        self.bid_compare_current_rfq_id = None
        self.bid_compare_vendor_rows = []
        self.bid_compare_vendor_column_map = {}
        self.bid_compare_rfq_label.setText("No RFQ selected")
        self.bid_compare_estimate_label.setText("—")
        self.bid_compare_work_order_label.setText("—")
        self.bid_compare_status_label.setText("—")
        self.bid_compare_vendor_summary_label.setText(message)
        self.bid_compare_full_vendor_combo.blockSignals(True)
        self.bid_compare_full_vendor_combo.clear()
        self.bid_compare_full_vendor_combo.addItem("Select vendor package…", None)
        self.bid_compare_full_vendor_combo.blockSignals(False)
        self.bid_compare_table.setRowCount(0)
        self.bid_compare_table.setColumnCount(10)
        self.bid_compare_apply_full_vendor_btn.setEnabled(False)
        self.bid_compare_save_btn.setEnabled(False)
        self.bid_compare_refresh_btn.setEnabled(False)

    def load_po_work_order_choices(self) -> None:
        current_value = self.po_work_order_combo.currentData() if hasattr(self, "po_work_order_combo") else None
        choices = []
        try:
            choices = list(get_open_workorder_choices() or [])
        except Exception as exc:
            print(f"PO Work Order Choice Load Error: {exc}")
        if not hasattr(self, "po_work_order_combo"):
            return
        self.po_work_order_combo.blockSignals(True)
        self.po_work_order_combo.clear()
        self.po_work_order_combo.addItem("Select an open work order…", None)
        for row in choices:
            label = str(row.get("Label") or f"WO #{row.get('WorkOrderID')}")
            self.po_work_order_combo.addItem(label, int(row["WorkOrderID"]))
        if current_value:
            idx = self.po_work_order_combo.findData(current_value)
            if idx >= 0:
                self.po_work_order_combo.setCurrentIndex(idx)
        self.po_work_order_combo.blockSignals(False)

    def on_po_work_order_selected(self) -> None:
        if self.build_po_readonly:
            return
        work_order_id = self.po_work_order_combo.currentData()
        if not work_order_id:
            self.wo_edit.setText("")
            self.wo_resolution = None
            self.current_vendor_id = None if self.po_item_table.rowCount() == 0 else self.current_vendor_id
            self.po_vendor_label.setText("")
            self.po_carried_source_rows = []
            self._populate_po_carried_source_filters([])
            self._populate_po_carried_source_table([])
            self.po_carried_status_label.setText("Select a work order to load carried material decisions.")
            self.refresh_po_state_for_current_selection()
            return
        self.wo_edit.setText(str(work_order_id))
        self.wo_resolution = {
            "can_create_po": True,
            "open_work_order_id": int(work_order_id),
            "latest_work_order_id": int(work_order_id),
            "latest_status": "Open",
            "message": f"Work Order #{work_order_id} is open and ready for PO drafting.",
        }
        self.reload_po_carried_source_list()
        self.refresh_po_state_for_current_selection()

    def _populate_po_carried_source_filters(self, rows: list[dict]) -> None:
        filter_defs = [
            (self.po_carried_estimate_filter, "All Estimates", "EstimateID"),
            (self.po_carried_rfq_filter, "All RFQs", "RFQID"),
            (self.po_carried_vendor_filter, "All Vendors", "VendorID"),
        ]
        for combo, default_label, key in filter_defs:
            current_value = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(default_label, None)
            seen = set()
            for row in rows:
                value = row.get(key)
                if value in (None, "", 0):
                    continue
                if value in seen:
                    continue
                seen.add(value)
                if key == "VendorID":
                    label = str(row.get("VendorName") or f"Vendor #{value}")
                elif key == "RFQID":
                    label = f"RFQ #{value}"
                else:
                    label = f"Estimate #{value}"
                combo.addItem(label, int(value))
            if current_value is not None:
                idx = combo.findData(current_value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def reload_po_carried_source_list(self) -> None:
        if self.build_po_readonly:
            return
        work_order_id = self.po_work_order_combo.currentData()
        if not work_order_id:
            self.po_carried_source_rows = []
            self._populate_po_carried_source_filters([])
            self._populate_po_carried_source_table([])
            self.po_carried_status_label.setText("Build PO from Carried Items requires an open work order.")
            return

        estimate_filter = self.po_carried_estimate_filter.currentData()
        rfq_filter = self.po_carried_rfq_filter.currentData()
        vendor_filter = self.po_carried_vendor_filter.currentData()
        try:
            rows = get_carried_items_for_po_center(
                int(work_order_id),
                estimate_id=int(estimate_filter) if estimate_filter else None,
                rfq_id=int(rfq_filter) if rfq_filter else None,
                vendor_id=int(vendor_filter) if vendor_filter else None,
            )
        except Exception as exc:
            self.po_carried_source_rows = []
            self._populate_po_carried_source_table([])
            self.po_carried_status_label.setText(f"Failed to load carried items: {exc}")
            return

        self.po_carried_source_rows = rows
        self._populate_po_carried_source_filters(rows)
        self._populate_po_carried_source_table(rows)
        if rows:
            self.po_carried_status_label.setText(
                f"Loaded {len(rows)} carried item source row(s) for Work Order #{work_order_id}."
            )
        else:
            self.po_carried_status_label.setText(
                f"No carried selections found yet for Work Order #{work_order_id}. Save carried choices in RFQ Center Bid Compare first."
            )

    def _populate_po_carried_source_table(self, rows: list[dict]) -> None:
        self.po_carried_source_table.setRowCount(0)
        for row_data in rows:
            row = self.po_carried_source_table.rowCount()
            self.po_carried_source_table.insertRow(row)
            values = [
                str(row_data.get("RFQCarriedSelectionID") or ""),
                str(row_data.get("PRItemID") or ""),
                str(row_data.get("VendorID") or ""),
                str(row_data.get("EstimateID") or ""),
                str(row_data.get("MaterialID") or ""),
                str(row_data.get("RFQID") or row_data.get("PriceRequestID") or ""),
                str(row_data.get("EstimateID") or ""),
                str(row_data.get("WorkOrderID") or ""),
                str(row_data.get("VendorName") or ""),
                str(row_data.get("PartNumber") or ""),
                str(row_data.get("Description") or ""),
                f"{float(row_data.get('CarriedQuantity') or 0):.2f}",
                str(row_data.get("Unit") or "—"),
                f"${float(row_data.get('CarriedUnitPrice') or 0):.2f}",
                f"${float(row_data.get('CarriedExtendedPrice') or 0):.2f}",
                str(row_data.get("SelectionType") or ""),
            ]
            for col, value in enumerate(values):
                self._set_item(
                    self.po_carried_source_table,
                    row,
                    col,
                    value,
                    center=col in (5, 6, 7, 11, 12, 15),
                    align_right=col in (13, 14),
                )

    def _collect_current_po_items(self) -> list[dict]:
        items = []
        for row in range(self.po_item_table.rowCount()):
            items.append(
                {
                    "mat_id": self.po_item_table.item(row, 0).text(),
                    "desc": self.po_item_table.item(row, 1).text(),
                    "qty": float(self.po_item_table.item(row, 3).text()),
                    "price": float(str(self.po_item_table.item(row, 4).text()).replace("$", "").replace(",", "").strip() or 0),
                    "catalog_item_id": self.po_item_table.item(row, 5).text() if self.po_item_table.item(row, 5) else "",
                    "rfq_carried_selection_id": self.po_item_table.item(row, 6).text() if self.po_item_table.item(row, 6) else "",
                    "source_price_request_id": self.po_item_table.item(row, 7).text() if self.po_item_table.item(row, 7) else "",
                    "source_pr_item_id": self.po_item_table.item(row, 8).text() if self.po_item_table.item(row, 8) else "",
                    "source_vendor_id": self.po_item_table.item(row, 9).text() if self.po_item_table.item(row, 9) else "",
                    "source_estimate_id": self.po_item_table.item(row, 10).text() if self.po_item_table.item(row, 10) else "",
                    "part_number": self.po_item_table.item(row, 11).text() if self.po_item_table.item(row, 11) else "",
                }
            )
        return items

    def _persist_build_po_record(self, status: str = "Draft") -> int:
        wo_id = self.wo_edit.text().strip()
        if not wo_id:
            raise ValueError("Build PO from Carried Items requires an open work order.")
        if not self.wo_resolution or not self.wo_resolution.get("can_create_po"):
            raise ValueError(
                (self.wo_resolution or {}).get("message") or "An open work order is required before ordering materials."
            )
        if not self.current_vendor_id:
            raise ValueError("Select or add carried items from one vendor before saving the PO draft.")
        items = self._collect_current_po_items()
        if not items:
            raise ValueError("Add at least one carried item to the current PO draft first.")
        saver = lock_purchase_order_record if status == "Locked" else save_purchase_order_draft
        po_id = saver(
            work_order_id=int(wo_id),
            items=items,
            vendor_id=self.current_vendor_id,
            po_id=self.active_po_id,
        )
        self.active_po_id = int(po_id)
        self.load_pipeline()
        if status == "Locked":
            self.load_existing_purchase_order(self.active_po_id, select_tab=False)
            self.lbl_po_status.setText(f"PO #{self.active_po_id} saved as {status}")
        else:
            self.build_po_readonly = False
            self.lbl_po_status.setText(f"PO #{self.active_po_id} saved as Draft")
            self.refresh_po_state_for_current_selection()
        self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
        return int(po_id)

    def save_build_po_draft(self) -> None:
        try:
            po_id = self._persist_build_po_record(status="Draft")
            QMessageBox.information(self, "PO Draft Saved", f"Purchase Order #{po_id} draft saved from carried items.")
        except Exception as exc:
            QMessageBox.critical(self, "Save PO Draft Error", str(exc))

    def _current_po_source_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for row in range(self.po_item_table.rowCount()):
            carry_id = str(self.po_item_table.item(row, 6).text() if self.po_item_table.item(row, 6) else "").strip()
            pr_item_id = str(self.po_item_table.item(row, 8).text() if self.po_item_table.item(row, 8) else "").strip()
            material_id = str(self.po_item_table.item(row, 0).text() if self.po_item_table.item(row, 0) else "").strip()
            if carry_id:
                keys.add(("carry", carry_id))
            elif pr_item_id:
                keys.add(("pritem", pr_item_id))
            elif material_id:
                keys.add(("material", material_id))
        if self.active_po_id:
            try:
                for link in get_purchase_order_source_links(self.active_po_id):
                    if link.get("RFQCarriedSelectionID"):
                        keys.add(("carry", str(link["RFQCarriedSelectionID"])))
                    elif link.get("PRItemID"):
                        keys.add(("pritem", str(link["PRItemID"])))
                    elif link.get("MaterialID"):
                        keys.add(("material", str(link["MaterialID"])))
            except Exception as exc:
                print(f"PO Source Link Load Error: {exc}")
        return keys

    def _append_po_draft_row(self, source_row: dict) -> None:
        row = self.po_item_table.rowCount()
        self.po_item_table.insertRow(row)
        material_id = str(source_row.get("MaterialID") or "")
        description = str(source_row.get("Description") or "")
        qty = float(source_row.get("CarriedQuantity") or 0)
        price = float(source_row.get("CarriedUnitPrice") or 0)
        catalog_item_id = str(source_row.get("CatalogItemID") or source_row.get("ItemID") or "")
        values = [
            material_id,
            description,
            f"{qty:.2f}",
            f"{qty:.2f}",
            f"${price:.2f}",
            catalog_item_id,
            str(source_row.get("RFQCarriedSelectionID") or ""),
            str(source_row.get("RFQID") or source_row.get("PriceRequestID") or ""),
            str(source_row.get("PRItemID") or ""),
            str(source_row.get("VendorID") or ""),
            str(source_row.get("EstimateID") or ""),
            str(source_row.get("PartNumber") or ""),
        ]
        for col, value in enumerate(values):
            self._set_item(
                self.po_item_table,
                row,
                col,
                value,
                center=col in (2, 3),
                align_right=col == 4,
            )

    def _add_carried_rows_to_po_draft(self, rows: list[dict]) -> None:
        work_order_id = self.po_work_order_combo.currentData()
        if not work_order_id:
            raise ValueError("Build PO from Carried Items requires an open work order.")
        if not rows:
            raise ValueError("No carried items were selected.")

        vendor_ids = {int(row["VendorID"]) for row in rows if row.get("VendorID") not in (None, "", 0)}
        if len(vendor_ids) != 1:
            raise ValueError("Select carried items from one vendor at a time when building a PO draft.")
        selected_vendor_id = next(iter(vendor_ids))
        selected_vendor_name = str(rows[0].get("VendorName") or f"Vendor #{selected_vendor_id}")
        if self.current_vendor_id and self.po_item_table.rowCount() > 0 and int(self.current_vendor_id) != selected_vendor_id:
            raise ValueError(
                f"The current PO draft is already set to {self.po_vendor_label.text() or 'another vendor'}. "
                "Use New RFQ/PO or finish this draft before adding another vendor package."
            )

        existing_keys = self._current_po_source_keys()
        added = 0
        skipped = 0
        for row_data in rows:
            carry_id = str(row_data.get("RFQCarriedSelectionID") or "").strip()
            pr_item_id = str(row_data.get("PRItemID") or "").strip()
            material_id = str(row_data.get("MaterialID") or "").strip()
            if carry_id:
                key = ("carry", carry_id)
            elif pr_item_id:
                key = ("pritem", pr_item_id)
            else:
                key = ("material", material_id)
            if key in existing_keys:
                skipped += 1
                continue
            self._append_po_draft_row(row_data)
            existing_keys.add(key)
            added += 1

        if added == 0:
            QMessageBox.information(
                self,
                "No New Carried Items",
                "The selected carried items are already on the current PO draft.",
            )
            return

        self.current_vendor_id = selected_vendor_id
        self.po_vendor_label.setText(selected_vendor_name)
        self.wo_edit.setText(str(work_order_id))
        self.wo_resolution = {
            "can_create_po": True,
            "open_work_order_id": int(work_order_id),
            "latest_work_order_id": int(work_order_id),
            "latest_status": "Open",
            "message": f"Work Order #{work_order_id} is open and ready for PO drafting.",
        }
        po_id = self._persist_build_po_record(status="Draft")
        self.lbl_po_status.setText(f"PO #{po_id} draft updated from carried selections ({added} added, {skipped} skipped).")
        self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")

    def add_selected_carried_items_to_po_draft(self) -> None:
        selected_rows = sorted({item.row() for item in self.po_carried_source_table.selectedItems()})
        rows = [self.po_carried_source_rows[row] for row in selected_rows if 0 <= row < len(self.po_carried_source_rows)]
        try:
            self._add_carried_rows_to_po_draft(rows)
        except Exception as exc:
            QMessageBox.critical(self, "Add Carried Items Error", str(exc))

    def add_filtered_vendor_package_to_po_draft(self) -> None:
        vendor_id = self.po_carried_vendor_filter.currentData()
        if not vendor_id:
            QMessageBox.information(self, "Select Vendor", "Choose a vendor filter first.")
            return
        rows = [row for row in self.po_carried_source_rows if int(row.get("VendorID") or 0) == int(vendor_id)]
        try:
            self._add_carried_rows_to_po_draft(rows)
        except Exception as exc:
            QMessageBox.critical(self, "Add Vendor Package Error", str(exc))

    def refresh_bid_compare_for_current_rfq(self) -> None:
        if not self.bid_compare_current_rfq_id:
            self.clear_bid_compare_workspace()
            return
        self.load_bid_compare_for_rfq(self.bid_compare_current_rfq_id)

    def load_bid_compare_for_rfq(self, rfq_id: int) -> None:
        try:
            compare_data = get_bid_compare_data(rfq_id)
        except Exception as exc:
            self.clear_bid_compare_workspace(f"Bid Compare load failed: {exc}")
            return

        header = compare_data.get("header") or {}
        vendor_rows = list(compare_data.get("vendors") or [])
        self.bid_compare_current_rfq_id = int(header.get("rfq_id") or rfq_id)
        self.bid_compare_vendor_rows = vendor_rows
        self.bid_compare_rfq_label.setText(str(header.get("rfq_id") or rfq_id))
        self.bid_compare_estimate_label.setText(str(header.get("estimate_id") or "—"))
        self.bid_compare_work_order_label.setText(str(header.get("work_order_id") or "—"))
        self.bid_compare_status_label.setText(str(header.get("status") or "—"))

        vendor_summary = []
        for vendor_row in vendor_rows:
            vendor_name = str(vendor_row.get("VendorName") or f"RFQ #{vendor_row.get('PriceRequestID')}")
            rfq_label = f"RFQ #{vendor_row.get('PriceRequestID')}"
            quote_label = str(vendor_row.get("VendorQuoteNumber") or "").strip() or "No quote #"
            vendor_summary.append(f"{vendor_name} ({rfq_label}, {quote_label})")
        self.bid_compare_vendor_summary_label.setText(", ".join(vendor_summary) if vendor_summary else "No related vendor RFQs found.")

        self.bid_compare_full_vendor_combo.blockSignals(True)
        self.bid_compare_full_vendor_combo.clear()
        self.bid_compare_full_vendor_combo.addItem("Select vendor package…", None)
        for vendor_row in vendor_rows:
            priced_line_count = 0
            for line in compare_data.get("lines") or []:
                vendor_quote = (line.get("vendor_quotes") or {}).get(int(vendor_row["PriceRequestID"]))
                if vendor_quote and vendor_quote.get("quoted_unit_price") is not None:
                    priced_line_count += 1
            label = f"{vendor_row.get('VendorName') or ('RFQ #' + str(vendor_row.get('PriceRequestID')))} ({priced_line_count} priced line(s))"
            self.bid_compare_full_vendor_combo.addItem(label, int(vendor_row["PriceRequestID"]))
        self.bid_compare_full_vendor_combo.blockSignals(False)

        self._populate_bid_compare_table(compare_data)
        has_lines = bool(compare_data.get("lines"))
        self.bid_compare_apply_full_vendor_btn.setEnabled(has_lines and len(vendor_rows) > 0)
        self.bid_compare_save_btn.setEnabled(has_lines)
        self.bid_compare_refresh_btn.setEnabled(True)

    def _populate_bid_compare_table(self, compare_data: dict) -> None:
        vendor_rows = list(compare_data.get("vendors") or [])
        lines = list(compare_data.get("lines") or [])
        static_headers = [
            "MaterialID",
            "BasePRItemID",
            "Part #",
            "Description",
            "Qty",
            "Unit",
            "Carry Vendor",
            "Carry Unit",
            "Carry Ext",
            "Flags / Notes",
        ]
        dynamic_headers = []
        self.bid_compare_vendor_column_map = {}
        next_col = len(static_headers)
        for vendor_row in vendor_rows:
            vendor_name = str(vendor_row.get("VendorName") or f"RFQ #{vendor_row.get('PriceRequestID')}")
            dynamic_headers.extend([f"{vendor_name} Unit", f"{vendor_name} Ext"])
            self.bid_compare_vendor_column_map[int(vendor_row["PriceRequestID"])] = (next_col, next_col + 1)
            next_col += 2

        self.bid_compare_table.clearContents()
        self.bid_compare_table.setColumnCount(len(static_headers) + len(dynamic_headers))
        self.bid_compare_table.setHorizontalHeaderLabels(static_headers + dynamic_headers)
        self.bid_compare_table.setRowCount(0)
        self.bid_compare_table.setColumnHidden(0, True)
        self.bid_compare_table.setColumnHidden(1, True)

        for row_index, line in enumerate(lines):
            self.bid_compare_table.insertRow(row_index)
            self._set_item(self.bid_compare_table, row_index, 0, str(line.get("material_id") or ""))
            self._set_item(self.bid_compare_table, row_index, 1, str(line.get("base_pr_item_id") or ""))
            self._set_item(self.bid_compare_table, row_index, 2, str(line.get("part_number") or ""))
            self._set_item(self.bid_compare_table, row_index, 3, str(line.get("description") or ""))
            self._set_item(self.bid_compare_table, row_index, 4, f"{float(line.get('requested_quantity') or 0):.2f}", center=True)
            self._set_item(self.bid_compare_table, row_index, 5, str(line.get("unit") or "—"), center=True)
            self._set_item(self.bid_compare_table, row_index, 7, "—", align_right=True)
            self._set_item(self.bid_compare_table, row_index, 8, "—", align_right=True)
            self._set_item(self.bid_compare_table, row_index, 9, str(line.get("notes_summary") or "—"))

            selector = QComboBox()
            selector.addItem("-- No carry --", None)
            vendor_quotes = line.get("vendor_quotes") or {}
            for vendor_row in vendor_rows:
                quote = vendor_quotes.get(int(vendor_row["PriceRequestID"]))
                if not quote or quote.get("quoted_unit_price") is None:
                    continue
                unit_price = float(quote["quoted_unit_price"])
                extended_price = float(quote.get("quoted_extended_price") or 0)
                selector.addItem(
                    f"{vendor_row.get('VendorName') or ('RFQ #' + str(vendor_row.get('PriceRequestID')))} | ${unit_price:.2f}",
                    {
                        "material_id": int(line["material_id"]),
                        "source_pr_item_id": int(quote["source_pr_item_id"]),
                        "source_price_request_id": int(quote["source_price_request_id"]),
                        "vendor_id": int(quote["vendor_id"]) if quote.get("vendor_id") is not None else None,
                        "carried_unit_price": unit_price,
                        "carried_extended_price": extended_price,
                        "carried_quantity": float(line.get("requested_quantity") or 0),
                    },
                )
            saved_selection = line.get("saved_selection") or {}
            if saved_selection:
                target_pr_item_id = int(saved_selection.get("SourcePRItemID") or 0)
                for option_index in range(1, selector.count()):
                    option = selector.itemData(option_index)
                    if option and int(option.get("source_pr_item_id") or 0) == target_pr_item_id:
                        selector.setCurrentIndex(option_index)
                        break
            selector.currentIndexChanged.connect(lambda _idx, r=row_index: self._on_bid_compare_selector_changed(r))
            self.bid_compare_table.setCellWidget(row_index, 6, selector)

            for vendor_row in vendor_rows:
                unit_col, ext_col = self.bid_compare_vendor_column_map[int(vendor_row["PriceRequestID"])]
                quote = vendor_quotes.get(int(vendor_row["PriceRequestID"]))
                if not quote or quote.get("quoted_unit_price") is None:
                    self._set_item(self.bid_compare_table, row_index, unit_col, "No Quote", center=True, color=Qt.GlobalColor.darkGray)
                    self._set_item(self.bid_compare_table, row_index, ext_col, "—", center=True, color=Qt.GlobalColor.darkGray)
                    continue
                unit_text = f"${float(quote['quoted_unit_price']):.2f}"
                ext_text = f"${float(quote.get('quoted_extended_price') or 0):.2f}"
                if quote.get("is_substitute"):
                    unit_text += " *"
                unit_item = QTableWidgetItem(unit_text)
                unit_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                ext_item = QTableWidgetItem(ext_text)
                ext_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tooltip_parts = [
                    f"RFQ #{quote.get('source_price_request_id')}",
                    f"Quote #: {quote.get('vendor_quote_number') or '—'}",
                ]
                if quote.get("vendor_quote_date"):
                    tooltip_parts.append(f"Quote Date: {quote.get('vendor_quote_date')}")
                if quote.get("is_substitute"):
                    tooltip_parts.append("Substitution Offered")
                if quote.get("substitute_notes"):
                    tooltip_parts.append(str(quote.get("substitute_notes")))
                tooltip_text = "\n".join(tooltip_parts)
                unit_item.setToolTip(tooltip_text)
                ext_item.setToolTip(tooltip_text)
                self.bid_compare_table.setItem(row_index, unit_col, unit_item)
                self.bid_compare_table.setItem(row_index, ext_col, ext_item)

            self._on_bid_compare_selector_changed(row_index)

    def _on_bid_compare_selector_changed(self, row: int) -> None:
        selector = self.bid_compare_table.cellWidget(row, 6)
        if not isinstance(selector, QComboBox):
            return
        option = selector.currentData()
        if not option:
            if self.bid_compare_table.item(row, 7):
                self.bid_compare_table.item(row, 7).setText("—")
            if self.bid_compare_table.item(row, 8):
                self.bid_compare_table.item(row, 8).setText("—")
            return
        carried_unit_price = float(option.get("carried_unit_price") or 0)
        carried_extended_price = float(option.get("carried_extended_price") or 0)
        if self.bid_compare_table.item(row, 7):
            self.bid_compare_table.item(row, 7).setText(f"${carried_unit_price:.2f}")
        if self.bid_compare_table.item(row, 8):
            self.bid_compare_table.item(row, 8).setText(f"${carried_extended_price:.2f}")

    def apply_bid_compare_full_package_vendor(self) -> None:
        source_price_request_id = self.bid_compare_full_vendor_combo.currentData()
        if not source_price_request_id:
            QMessageBox.information(self, "Select Vendor", "Choose a vendor package first.")
            return
        matched_rows = 0
        for row in range(self.bid_compare_table.rowCount()):
            selector = self.bid_compare_table.cellWidget(row, 6)
            if not isinstance(selector, QComboBox):
                continue
            for option_index in range(1, selector.count()):
                option = selector.itemData(option_index)
                if option and int(option.get("source_price_request_id") or 0) == int(source_price_request_id):
                    selector.setCurrentIndex(option_index)
                    matched_rows += 1
                    break
        QMessageBox.information(
            self,
            "Vendor Package Applied",
            f"Applied the selected vendor package to {matched_rows} line(s) with available pricing.",
        )

    def save_bid_compare_selections(self) -> None:
        if not self.bid_compare_current_rfq_id:
            QMessageBox.warning(self, "No RFQ", "Select an RFQ in RFQ Center first.")
            return
        selections = []
        for row in range(self.bid_compare_table.rowCount()):
            selector = self.bid_compare_table.cellWidget(row, 6)
            if not isinstance(selector, QComboBox):
                continue
            option = selector.currentData()
            if option:
                selections.append(option)
        if not selections:
            QMessageBox.warning(self, "No Carry Selection", "Choose at least one carried vendor price before saving.")
            return
        try:
            result = save_bid_compare_carried_selections(self.bid_compare_current_rfq_id, selections)
            self.load_bid_compare_for_rfq(self.bid_compare_current_rfq_id)
            QMessageBox.information(
                self,
                "Carried Selections Saved",
                f"Saved {result.get('selection_count', 0)} carried selection(s) "
                f"using {result.get('selection_type', 'LineItem')} mode.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Bid Compare Save Error", str(exc))

    def _set_procurement_tab(self, widget: QWidget, *, center: str | None = None) -> None:
        if center:
            self.set_procurement_center(center)
        self.notebook.setCurrentWidget(widget)

    def set_procurement_center(self, center: str) -> None:
        center = "PO" if str(center or "").strip().upper() == "PO" else "RFQ"
        self.procurement_center = center
        is_rfq = center == "RFQ"

        self.procurement_center_badge.setText("RFQ Center" if is_rfq else "PO Center")
        self.procurement_center_badge.setStyleSheet(
            "background-color: #eaf7ee; color: #1e8449; border: 1px solid #b7e1c1; "
            "border-radius: 10px; padding: 3px 10px; font-weight: 700;"
            if is_rfq
            else
            "background-color: #eef3fb; color: #1f4f99; border: 1px solid #c8d8f0; "
            "border-radius: 10px; padding: 3px 10px; font-weight: 700;"
        )
        self.rfq_group.setVisible(is_rfq)
        self.po_group.setVisible(not is_rfq)

        self.direct_action_type_combo.blockSignals(True)
        self.direct_source_type_combo.blockSignals(True)
        if is_rfq:
            self.direct_action_type_combo.setCurrentText("RFQ")
            self.direct_source_type_combo.setEnabled(True)
        else:
            self.direct_action_type_combo.setCurrentText("Purchase Order")
            self.direct_source_type_combo.setCurrentText("Work Order")
            self.direct_source_type_combo.setEnabled(False)
            self.load_po_work_order_choices()
        self.direct_action_type_combo.setEnabled(False)
        self.direct_action_type_combo.blockSignals(False)
        self.direct_source_type_combo.blockSignals(False)

        tab_visibility = {
            self.tab_direct_po: True,
            self.tab_build_rfq: is_rfq,
            self.tab_matrix: is_rfq,
            self.tab_bid_compare: is_rfq,
            self.tab_build_po: not is_rfq,
            self.tab_receiving: not is_rfq,
            self.tab_document: not is_rfq,
        }
        tab_titles = {
            self.tab_direct_po: "1. Material Request" if is_rfq else "1. Build PO",
            self.tab_build_rfq: "2. Build RFQ from Estimate",
            self.tab_matrix: "3. Receive Quotes",
            self.tab_bid_compare: "4. Bid Compare",
            self.tab_build_po: "2. Build PO from Carried Items",
            self.tab_receiving: "3. Receive Goods",
            self.tab_document: "4. PO Document",
        }
        first_visible_widget = None
        for widget, visible in tab_visibility.items():
            index = self.notebook.indexOf(widget)
            if index < 0:
                continue
            self.notebook.setTabVisible(index, visible)
            self.notebook.setTabText(index, tab_titles[widget])
            if visible and first_visible_widget is None:
                first_visible_widget = widget

        current_widget = self.notebook.currentWidget()
        if current_widget is None or not tab_visibility.get(current_widget, False):
            if first_visible_widget is not None:
                self.notebook.setCurrentWidget(first_visible_widget)

        self.refresh_direct_po_choices()
        self.on_material_request_mode_changed()
        main_window = self.window()
        if main_window is not None and hasattr(main_window, "refresh_materials_subnav_selection"):
            main_window.refresh_materials_subnav_selection(center)

    def on_rfq_select(self) -> None:
        if self.po_table.selectedItems():
            self.po_table.clearSelection()
        row = self._selected_row(self.rfq_table)
        if row is None:
            return

        rfq_id = int(self.rfq_table.item(row, 0).text())
        self.active_rfq_id = rfq_id
        self.current_estimate_id = int(self.rfq_table.item(row, 1).text())
        self._set_vendor_selection(self.rfq_table.item(row, 4).text())
        self.due_date_edit.setText(self.rfq_table.item(row, 6).text())

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
        self.current_vendor_id = int(self.rfq_table.item(row, 8).text() or 0)
        self.active_po_id = None
        self.po_vendor_label.setText(self.rfq_table.item(row, 4).text())
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
        self.render_rfq_preview_for_selection(force=True, reason=f"RFQ #{rfq_id} loaded.")
        try:
            self.load_material_request_rfq(rfq_id, select_tab=False)
        except Exception as exc:
            QMessageBox.critical(self, "Material Request Load Error", str(exc))
        self.load_bid_compare_for_rfq(rfq_id)

    def on_po_select(self) -> None:
        if self.rfq_table.selectedItems():
            self.rfq_table.clearSelection()
        row = self._selected_row(self.po_table)
        if row is None:
            return
        po_id = int(self.po_table.item(row, 0).text())
        status_text = str(self.po_table.item(row, 4).text() or "").strip()
        try:
            self.load_material_request_purchase_order(po_id, select_tab=False)
        except Exception as exc:
            QMessageBox.critical(self, "Material Request Load Error", str(exc))
            return
        if status_text != "Draft":
            self.load_existing_purchase_order(po_id, select_tab=True)

    def refresh_data(self) -> None:
        self.load_estimates()
        self.load_pipeline()
        self.refresh_direct_po_choices()
        self.on_material_request_mode_changed()
        self.load_rfq_template_options()
        self.load_po_work_order_choices()
        if self.current_estimate_id or self.active_rfq_id:
            self.request_rfq_preview_refresh("Template list refreshed.")

    def _get_document_catalog_service(self) -> DocumentCatalogService:
        catalog_service = getattr(getattr(self.parent(), "container", None), "document_catalog_service", None)
        if catalog_service is None and hasattr(self.parent(), "main_window"):
            catalog_service = getattr(getattr(self.parent().main_window, "container", None), "document_catalog_service", None)
        if catalog_service is None:
            main_window = self.window()
            catalog_service = getattr(getattr(main_window, "container", None), "document_catalog_service", None)
        if catalog_service is not None:
            return catalog_service
        return DocumentCatalogService(DocumentControlRepository())

    def load_rfq_template_options(self) -> None:
        try:
            catalog_service = self._get_document_catalog_service()
            summaries = catalog_service.list_current_templates(
                document_type_code=self.RFQ_DELIVERY_DOCUMENT_TYPE_CODE
            )
        except Exception:
            summaries = []
            catalog_service = None

        choices_by_kind: dict[str, list[object]] = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        defaults_by_kind: dict[str, int | None] = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }

        if catalog_service is not None:
            for kind in (DocumentTemplateKind.HEADER, DocumentTemplateKind.BODY, DocumentTemplateKind.FOOTER):
                try:
                    default_mapping = catalog_service.get_template_default(
                        document_type_code=self.RFQ_DELIVERY_DOCUMENT_TYPE_CODE,
                        template_kind=kind,
                        usage_context=self.RFQ_SEND_USAGE_CONTEXT,
                    )
                    defaults_by_kind[kind.value] = (
                        int(default_mapping.template_id) if default_mapping is not None else None
                    )
                except Exception:
                    defaults_by_kind[kind.value] = None

        for summary in summaries:
            try:
                kind_value = summary.kind.value if hasattr(summary.kind, "value") else str(summary.kind)
            except Exception:
                kind_value = str(getattr(summary, "kind", "") or "")
            normalized_kind = str(kind_value or "").strip().lower()
            if normalized_kind not in choices_by_kind:
                continue
            if not bool(getattr(summary, "is_active", False)):
                continue
            choices_by_kind[normalized_kind].append(summary)

        for kind_value in choices_by_kind:
            choices_by_kind[kind_value].sort(
                key=lambda summary: (
                    0 if int(getattr(summary, "template_id", 0) or 0) == (defaults_by_kind.get(kind_value) or -1) else 1,
                    str(getattr(summary, "template_name", "") or "").lower(),
                    int(getattr(summary, "template_id", 0) or 0),
                )
            )

        self._rfq_template_choices_by_kind = choices_by_kind
        self._rfq_template_defaults_by_kind = defaults_by_kind

        self.populate_rfq_template_combo(
            self.rfq_header_template_combo,
            DocumentTemplateKind.HEADER,
            "No active RFQ header templates",
        )
        self.populate_rfq_template_combo(
            self.rfq_body_template_combo,
            DocumentTemplateKind.BODY,
            "No active RFQ body templates",
        )
        self.populate_rfq_template_combo(
            self.rfq_footer_template_combo,
            DocumentTemplateKind.FOOTER,
            "No active RFQ footer templates",
        )

    def populate_rfq_template_combo(
        self,
        combo: QComboBox,
        template_kind: DocumentTemplateKind,
        empty_label: str,
    ) -> None:
        kind_value = template_kind.value
        previous_template_id = combo.currentData()
        previous_template_id = int(previous_template_id) if previous_template_id not in (None, "") else None

        combo.blockSignals(True)
        combo.clear()

        choices = list(self._rfq_template_choices_by_kind.get(kind_value) or [])
        if not choices:
            combo.addItem(empty_label, None)
            combo.setEnabled(False)
            combo.blockSignals(False)
            return

        combo.setEnabled(True)
        for summary in choices:
            combo.addItem(str(getattr(summary, "template_name", "") or f"Template {summary.template_id}"), int(summary.template_id))

        preferred_id = previous_template_id
        if preferred_id is None:
            preferred_id = self._rfq_template_defaults_by_kind.get(kind_value)
        if preferred_id is None and choices:
            preferred_id = int(choices[0].template_id)

        target_index = 0
        if preferred_id is not None:
            for index in range(combo.count()):
                if combo.itemData(index) == preferred_id:
                    target_index = index
                    break
        combo.setCurrentIndex(target_index)
        combo.blockSignals(False)

    def get_selected_rfq_template_ids(self) -> dict[str, int | None]:
        return {
            "header_template_id": (
                int(self.rfq_header_template_combo.currentData())
                if self.rfq_header_template_combo.currentData() not in (None, "")
                else None
            ),
            "body_template_id": (
                int(self.rfq_body_template_combo.currentData())
                if self.rfq_body_template_combo.currentData() not in (None, "")
                else None
            ),
            "footer_template_id": (
                int(self.rfq_footer_template_combo.currentData())
                if self.rfq_footer_template_combo.currentData() not in (None, "")
                else None
            ),
        }

    def _current_material_request_action_type(self) -> str:
        value = self.direct_action_type_combo.currentData()
        return str(value or "PurchaseOrder")

    def _current_material_request_source_type(self) -> str:
        value = self.direct_source_type_combo.currentData()
        return str(value or "WorkOrder")

    def _material_request_source_selector_label(self, source_type: str | None = None) -> str:
        normalized = self._current_material_request_source_type() if source_type is None else str(source_type or "")
        return "Work Order #:" if normalized == "WorkOrder" else "Estimate #:"

    def _set_direct_source_selection(self, source_id: int | None, *, fallback_label: str | None = None) -> None:
        self.direct_source_combo.blockSignals(True)
        try:
            if not source_id:
                if self.direct_source_combo.count() > 0:
                    self.direct_source_combo.setCurrentIndex(0)
                return
            target_index = self.direct_source_combo.findData(int(source_id))
            if target_index < 0 and fallback_label:
                label = str(fallback_label).strip()
                if label:
                    self.direct_source_combo.addItem(label, int(source_id))
                    self.direct_source_lookup[label] = int(source_id)
                    target_index = self.direct_source_combo.findData(int(source_id))
            if target_index >= 0:
                self.direct_source_combo.setCurrentIndex(target_index)
            elif self.direct_source_combo.count() > 0:
                self.direct_source_combo.setCurrentIndex(0)
        finally:
            self.direct_source_combo.blockSignals(False)

    def on_material_request_source_type_changed(self) -> None:
        if hasattr(self, "direct_source_combo"):
            self.direct_source_combo.blockSignals(True)
            try:
                if self.direct_source_combo.count() > 0:
                    self.direct_source_combo.setCurrentIndex(0)
            finally:
                self.direct_source_combo.blockSignals(False)
        self.refresh_direct_po_choices()
        self.on_material_request_mode_changed()

    def on_material_request_mode_changed(self) -> None:
        action_type = self._current_material_request_action_type()
        source_type = self._current_material_request_source_type()
        is_rfq = action_type == "RFQ"
        po_ready = action_type == "PurchaseOrder" and source_type == "WorkOrder"

        self.direct_date_label.setText("Due Date:" if is_rfq else "Expected Arrival:")
        self.direct_source_label.setText(self._material_request_source_selector_label(source_type))
        self.direct_request_note_label.setText("Request Notes:" if is_rfq else "ETA Note:")

        for button in (
            self.direct_save_rfq_button,
            self.direct_preview_rfq_button,
            self.direct_preview_rfq_send_button,
            self.direct_send_rfq_button,
        ):
            button.setVisible(is_rfq)
        self.direct_dup_rfq_button.setVisible(is_rfq)
        self.direct_dup_rfq_button.setEnabled(bool(self.direct_active_rfq_id))

        for button in (
            self.direct_save_po_button,
            self.direct_lock_po_button,
            self.direct_preview_po_button,
            self.direct_export_po_button,
            self.direct_send_po_button,
        ):
            button.setVisible(not is_rfq)
            button.setEnabled(po_ready and not self.direct_workspace_readonly)

        self.direct_eta_edit.setEnabled(not self.direct_workspace_readonly)
        self.direct_eta_note_edit.setEnabled(not self.direct_workspace_readonly)

        notes = []
        if is_rfq:
            notes.append(
                "Material Request RFQ mode saves real RFQ drafts and reuses the guarded RFQ preview/send path."
            )
        elif source_type != "WorkOrder":
            notes.append(
                "Purchase Order compatibility actions currently require Work Order as the source in this workspace."
            )
        if self.direct_workspace_readonly:
            notes.append("This RFQ/PO is locked or no longer editable.")
        notes.append(
            "Material row notes are collected in this shell, but the existing PO compatibility draft path does not persist them yet."
        )
        self.direct_mode_hint_label.setText(" ".join(notes))

    def _is_material_request_rfq_editable(self, status_text: str | None) -> bool:
        normalized = str(status_text or "").strip().lower()
        if not normalized:
            return True
        return normalized in {"draft", "pending review"}

    def _is_material_request_po_editable(self, status_text: str | None) -> bool:
        normalized = str(status_text or "").strip().lower()
        if not normalized:
            return True
        return normalized in {"draft", "pending review"}

    def _set_material_request_readonly(self, readonly: bool) -> None:
        self.direct_workspace_readonly = bool(readonly)
        editable = not self.direct_workspace_readonly
        for widget in (
            self.direct_action_type_combo,
            self.direct_source_type_combo,
            self.direct_source_combo,
            self.direct_vendor_combo,
            self.direct_qty,
            self.direct_part_number_edit,
            self.direct_desc,
            self.direct_price,
            self.direct_line_note_edit,
        ):
            widget.setEnabled(editable)
        self.direct_add_material_button.setEnabled(editable)
        self.direct_remove_button.setEnabled(editable)
        self.direct_clear_button.setEnabled(editable)
        self.direct_save_rfq_button.setEnabled(editable)
        self.direct_save_po_button.setEnabled(editable)
        self.direct_lock_po_button.setEnabled(editable and self._current_material_request_source_type() == "WorkOrder")
        self.on_material_request_mode_changed()

    def _clear_material_request_entry_fields(self) -> None:
        self.direct_desc.setText("")
        self.direct_qty.setText("")
        self.direct_price.setText("")
        self.direct_part_number_edit.setCurrentText("")
        self.direct_part_number_edit.current_item_id = None
        self.direct_part_number_edit.current_material_row = None
        self.direct_line_note_edit.setText("")

    def _append_material_request_row(
        self,
        *,
        qty: float,
        part_number: str,
        description: str,
        unit_cost: float,
        notes: str = "",
        catalog_item_id: int | str | None = None,
    ) -> None:
        row = self.direct_po_table.rowCount()
        self.direct_po_table.insertRow(row)
        values = [
            f"{float(qty or 0):.2f}",
            str(part_number or ""),
            str(description or ""),
            f"${float(unit_cost or 0):.2f}",
            f"${float(qty or 0) * float(unit_cost or 0):,.2f}",
            str(notes or ""),
            "" if catalog_item_id in (None, "") else str(catalog_item_id),
        ]
        for column, value in enumerate(values):
            self._set_item(
                self.direct_po_table,
                row,
                column,
                value,
                center=column == 0,
                align_right=column in (3, 4),
            )

    def start_new_material_request(self) -> None:
        self.direct_active_rfq_id = None
        self.direct_active_rfq_source_type = None
        self.direct_active_rfq_source_id = None
        self.direct_active_rfq_vendor_id = None
        self.direct_active_po_id = None
        self.direct_active_po_source_id = None
        self.direct_active_po_vendor_id = None
        self.direct_current_status = "Draft"
        if self.direct_source_combo.count() > 0:
            self.direct_source_combo.setCurrentIndex(0)
        self.direct_vendor_combo.setCurrentText("")
        self.direct_eta_edit.setText("")
        self.direct_eta_note_edit.setText("")
        self._clear_material_request_entry_fields()
        self.direct_po_table.setRowCount(0)
        self.refresh_direct_po_total()
        self.rfq_table.clearSelection()
        self.po_table.clearSelection()
        self._set_material_request_readonly(False)
        self.direct_status_label.setText("New material request mode.")
        self._set_procurement_tab(self.tab_direct_po, center=self.procurement_center)

    def load_material_request_rfq(self, rfq_id: int, select_tab: bool = False) -> None:
        rfq_id = int(rfq_id)
        header = get_rfq_for_material_request(rfq_id)
        if not header:
            raise ValueError(f"RFQ #{rfq_id} was not found.")

        source_type = str(header.get("MaterialRequestSourceType") or "Estimate")
        estimate_id = self._get_estimate_id_from_rfq_context(header)
        source_id = int(header.get("MaterialRequestSourceID") or (estimate_id if source_type == "Estimate" else 0) or 0)
        status_text = str(header.get("Status") or "Draft")
        editable = self._is_material_request_rfq_editable(status_text)

        self.direct_action_type_combo.setCurrentText("RFQ")
        self.direct_source_type_combo.setCurrentText("Work Order" if source_type == "WorkOrder" else "Estimate")
        self.refresh_direct_po_choices()
        self._set_direct_source_selection(
            source_id or None,
            fallback_label=str(header.get("MaterialRequestSourceLabel") or "").strip() or None,
        )
        self.direct_vendor_combo.setCurrentText(str(header.get("VendorName") or ""))
        due_value = header.get("DueDate")
        self.direct_eta_edit.setText(due_value.isoformat() if hasattr(due_value, "isoformat") else str(due_value or ""))
        self.direct_eta_note_edit.setText(str(header.get("MaterialRequestNotes") or ""))
        self.direct_po_table.setRowCount(0)
        for row_data in get_rfq_items_for_material_request(rfq_id):
            self._append_material_request_row(
                qty=float(row_data.get("Quantity") or 0),
                part_number=str(row_data.get("PartNumber") or ""),
                description=str(row_data.get("Description") or ""),
                unit_cost=float(row_data.get("UnitCost") or 0),
                notes=str(row_data.get("Notes") or ""),
                catalog_item_id=row_data.get("CatalogItemID"),
            )
        self.refresh_direct_po_total()

        self.direct_active_rfq_id = rfq_id
        self.direct_active_rfq_source_type = source_type
        self.direct_active_rfq_source_id = source_id or None
        self.direct_active_rfq_vendor_id = int(header.get("VendorID") or 0) or None
        self.active_rfq_id = rfq_id
        self.current_estimate_id = estimate_id
        self.current_vendor_id = int(header.get("VendorID") or 0) or None
        self.direct_active_po_id = None
        self.direct_active_po_source_id = None
        self.direct_active_po_vendor_id = None
        self.direct_current_status = status_text or "Draft"
        self._clear_material_request_entry_fields()
        self._set_material_request_readonly(not editable)
        self.direct_status_label.setText(
            f"RFQ #{rfq_id} loaded." if editable else f"RFQ #{rfq_id} loaded read-only."
        )
        if select_tab:
            self._set_procurement_tab(self.tab_direct_po, center="RFQ")

    def load_material_request_purchase_order(self, po_id: int, select_tab: bool = False) -> None:
        po_id = int(po_id)
        header = get_purchase_order_for_material_request(po_id)
        if not header:
            raise ValueError(f"Purchase Order #{po_id} was not found.")

        status_text = str(header.get("Status") or "Draft")
        editable = self._is_material_request_po_editable(status_text)
        work_order_id = int(header.get("WorkOrderID") or 0)
        vendor_id = int(header.get("VendorID") or 0)
        vendor_name = str(header.get("VendorName") or "").strip()

        self.direct_action_type_combo.setCurrentText("Purchase Order")
        self.direct_source_type_combo.setCurrentText("Work Order")
        self.refresh_direct_po_choices()
        self._set_direct_source_selection(
            work_order_id or None,
            fallback_label=f"Work Order #{work_order_id}" if work_order_id else None,
        )
        self.direct_vendor_combo.setCurrentText(vendor_name)
        eta_value = header.get("ExpectedArrivalDate")
        self.direct_eta_edit.setText(eta_value.isoformat() if hasattr(eta_value, "isoformat") else str(eta_value or ""))
        self.direct_eta_note_edit.setText(str(header.get("ExpectedArrivalNote") or ""))
        self.direct_po_table.setRowCount(0)
        for row_data in get_purchase_order_items_for_material_request(po_id):
            self._append_material_request_row(
                qty=float(row_data.get("QuantityOrdered") or 0),
                part_number=str(row_data.get("PartNumber") or ""),
                description=str(row_data.get("Description") or ""),
                unit_cost=float(row_data.get("UnitPriceAtOrder") or 0),
                notes=str(row_data.get("Notes") or ""),
                catalog_item_id=row_data.get("CatalogItemID"),
            )
        self.refresh_direct_po_total()

        self.direct_active_po_id = po_id
        self.direct_active_po_source_id = work_order_id or None
        self.direct_active_po_vendor_id = vendor_id or None
        self.active_po_id = po_id
        self.current_vendor_id = vendor_id or None
        self.direct_active_rfq_id = None
        self.direct_active_rfq_source_type = None
        self.direct_active_rfq_source_id = None
        self.direct_active_rfq_vendor_id = None
        self.direct_current_status = status_text or "Draft"
        self._clear_material_request_entry_fields()
        self._set_material_request_readonly(not editable)
        self.direct_status_label.setText(
            f"PO #{po_id} loaded." if editable else f"PO #{po_id} loaded read-only."
        )
        if select_tab:
            self._set_procurement_tab(self.tab_direct_po, center="PO")

    def duplicate_material_request_rfq(self) -> None:
        if not self.direct_active_rfq_id:
            QMessageBox.information(self, "Dup RFQ", "Select an RFQ first.")
            return

        self.refresh_direct_po_choices()
        vendor_names = list(self.direct_vendor_lookup.keys())
        if not vendor_names:
            QMessageBox.warning(self, "Dup RFQ", "No vendors are available to receive a duplicated RFQ.")
            return

        current_vendor_name = self.direct_vendor_combo.currentText().strip()
        target_vendor_name, accepted = QInputDialog.getItem(
            self,
            "Duplicate RFQ",
            "Select target vendor:",
            vendor_names,
            max(vendor_names.index(current_vendor_name), 0) if current_vendor_name in vendor_names else 0,
            False,
        )
        if not accepted:
            return
        target_vendor_name = str(target_vendor_name or "").strip()
        target_vendor_id = self.direct_vendor_lookup.get(target_vendor_name)
        if not target_vendor_id:
            QMessageBox.warning(self, "Dup RFQ", "Select a target vendor first.")
            return
        if current_vendor_name and target_vendor_name == current_vendor_name:
            proceed = QMessageBox.question(
                self,
                "Duplicate RFQ",
                "The selected target vendor matches the original RFQ vendor.\n\nDo you want to duplicate it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        try:
            new_rfq_id = duplicate_rfq_for_vendor(int(self.direct_active_rfq_id), int(target_vendor_id))
            self.load_pipeline()
            self._select_rfq_in_pipeline(int(new_rfq_id))
            self.load_material_request_rfq(int(new_rfq_id), select_tab=True)
            QMessageBox.information(
                self,
                "Dup RFQ",
                f"RFQ #{new_rfq_id} was created as a new draft for {target_vendor_name}.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Dup RFQ Failed", str(exc))

    def refresh_direct_po_choices(self) -> None:
        try:
            selected_source_id = self.direct_source_combo.currentData() if hasattr(self, "direct_source_combo") else None
            selected_vendor = self.direct_vendor_combo.currentText().strip()
            source_type = self._current_material_request_source_type()
            if source_type == "Estimate":
                estimate_rows = list(list_available_draft_estimates_for_material_request() or [])
                self.direct_estimate_lookup = {
                    str(row.get("Label") or f"Estimate #{row['EstimateID']}"): int(row["EstimateID"])
                    for row in estimate_rows
                }
                self.direct_source_lookup = dict(self.direct_estimate_lookup)
            else:
                work_order_rows = list(list_open_work_orders_for_material_request() or [])
                self.direct_workorder_lookup = {
                    str(row.get("Label") or f"Work Order #{row['WorkOrderID']}"): int(row["WorkOrderID"])
                    for row in work_order_rows
                }
                self.direct_source_lookup = dict(self.direct_workorder_lookup)

            self.direct_source_combo.blockSignals(True)
            self.direct_source_combo.clear()
            if self.direct_source_lookup:
                empty_label = (
                    "Select a saved draft estimate..."
                    if source_type == "Estimate"
                    else "Select an open work order..."
                )
                self.direct_source_combo.addItem(empty_label, None)
                for label, source_id in self.direct_source_lookup.items():
                    self.direct_source_combo.addItem(label, int(source_id))
                if selected_source_id not in (None, ""):
                    selected_index = self.direct_source_combo.findData(int(selected_source_id))
                    self.direct_source_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
                else:
                    self.direct_source_combo.setCurrentIndex(0)
            else:
                empty_label = (
                    "No saved draft estimates available"
                    if source_type == "Estimate"
                    else "No open work orders available"
                )
                self.direct_source_combo.addItem(empty_label, None)
                self.direct_source_combo.setCurrentIndex(0)
            self.direct_source_combo.blockSignals(False)

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
            print(f"Material request choices error: {exc}")

    def on_direct_vendor_select(self, _value: str | None = None) -> None:
        self.on_direct_material_request_context_changed()
        current_material = getattr(self.direct_part_number_edit, "current_material_row", None)
        if current_material:
            self._apply_direct_material_selection(current_material)

    def on_direct_material_request_context_changed(self, _value: str | None = None) -> None:
        if self._current_material_request_action_type() != "RFQ":
            return
        if not self.direct_active_rfq_id:
            return
        current_source_id = self.direct_source_combo.currentData()
        current_vendor_label = self.direct_vendor_combo.currentText().strip()
        current_vendor_id = self.direct_vendor_lookup.get(current_vendor_label)
        context_changed = (
            str(self.direct_active_rfq_source_type or "") != self._current_material_request_source_type()
            or int(self.direct_active_rfq_source_id or 0) != int(current_source_id or 0)
            or int(self.direct_active_rfq_vendor_id or 0) != int(current_vendor_id or 0)
        )
        if context_changed:
            self.direct_status_label.setText(
                "RFQ draft context changed. Use New RFQ/PO before saving a different RFQ header context."
            )

    def on_direct_part_number_search(self, _text: str) -> None:
        previously_selected = getattr(self.direct_part_number_edit, "current_item_id", None)
        self.direct_part_number_edit.current_item_id = None
        self.direct_part_number_edit.current_material_row = None
        if previously_selected:
            self.direct_desc.setText("")
            self.direct_price.setText("")
        self.direct_material_search_timer.start(250)

    def _perform_direct_material_search(self) -> None:
        search_text = self.direct_part_number_edit.currentText().strip()
        if len(search_text) < 1:
            self.direct_search_results.clear()
            self.direct_part_number_edit.blockSignals(True)
            self.direct_part_number_edit.clear()
            self.direct_part_number_edit.setEditText(search_text)
            self.direct_part_number_edit.blockSignals(False)
            return

        results = search_materials_by_part_number(search_text)
        self.direct_search_results.clear()
        display_values = []
        for row in results:
            unit_price = get_material_price_for_vendor(row, self.direct_vendor_combo.currentText().strip())
            display_text = (
                f"{row.get('PartNumber') or 'No Part #'} | "
                f"{row.get('Description') or ''} | "
                f"${unit_price:.2f}"
            )
            self.direct_search_results[display_text] = row
            display_values.append(display_text)
        self.direct_part_number_edit.blockSignals(True)
        self.direct_part_number_edit.clear()
        for display_text in display_values:
            self.direct_part_number_edit.addItem(display_text, self.direct_search_results[display_text])
        self.direct_part_number_edit.setEditText(search_text)
        self.direct_part_number_edit.blockSignals(False)
        if display_values and self.direct_part_number_edit.hasFocus():
            self.direct_part_number_edit.showPopup()

    def on_direct_part_number_select(self, _value: str | int | None = None) -> None:
        selection = self.direct_part_number_edit.currentText().strip()
        material = self.direct_search_results.get(selection)
        if not material and self.direct_part_number_edit.currentIndex() >= 0:
            current_data = self.direct_part_number_edit.currentData()
            if isinstance(current_data, dict):
                material = current_data
        if not material:
            return
        self._apply_direct_material_selection(material)

    def _apply_direct_material_selection(self, material: dict) -> None:
        price = get_material_price_for_vendor(material, self.direct_vendor_combo.currentText().strip())
        self.direct_price.setText(f"{price:.2f}")
        self.direct_part_number_edit.current_item_id = material.get("ItemID")
        self.direct_part_number_edit.current_material_row = dict(material)
        self.direct_part_number_edit.setEditText(str(material.get("PartNumber") or ""))
        self.direct_desc.setText(str(material.get("Description") or ""))

    def add_direct_po_line(self) -> None:
        if self.direct_workspace_readonly:
            QMessageBox.information(self, "Material Request", "This RFQ/PO is locked or no longer editable.")
            return
        try:
            part_no = self.direct_part_number_edit.currentText().strip()
            desc = self.direct_desc.text().strip()
            qty = float(self.direct_qty.text().strip() or 0)
            price = float(self.direct_price.text().strip() or 0)
            if not part_no or qty <= 0:
                QMessageBox.warning(self, "Material Request", "Qty and Part Number are required.")
                return
            item_id = getattr(self.direct_part_number_edit, "current_item_id", None)
            if not item_id and not desc:
                QMessageBox.warning(self, "Material Request", "Description is required for unknown part numbers.")
                return
            note_text = self.direct_line_note_edit.text().strip()
            line_total = qty * price
            row = self.direct_po_table.rowCount()
            self.direct_po_table.insertRow(row)
            values = [
                f"{qty:.2f}",
                "" if part_no is None else str(part_no),
                desc,
                f"${price:.2f}",
                f"${line_total:,.2f}",
                note_text,
                "" if item_id is None else str(item_id),
            ]
            for column, value in enumerate(values):
                self._set_item(
                    self.direct_po_table,
                    row,
                    column,
                    value,
                    center=column == 0,
                    align_right=column in (3, 4),
                )
            self.direct_part_number_edit.setCurrentText("")
            self.direct_part_number_edit.current_item_id = None
            self.direct_part_number_edit.current_material_row = None
            self.direct_desc.setText("")
            self.direct_qty.setText("")
            self.direct_price.setText("")
            self.direct_line_note_edit.setText("")
            self.refresh_direct_po_total()
        except ValueError:
            QMessageBox.critical(self, "Error", "Quantity and price must be valid numbers.")

    def remove_direct_po_line(self, _event=None) -> None:
        if self.direct_workspace_readonly:
            QMessageBox.information(self, "Material Request", "This RFQ/PO is locked or no longer editable.")
            return
        rows = sorted({item.row() for item in self.direct_po_table.selectedItems()}, reverse=True)
        for row in rows:
            self.direct_po_table.removeRow(row)
        self.refresh_direct_po_total()

    def clear_direct_po_draft(self) -> None:
        self.start_new_material_request()

    def refresh_direct_po_total(self) -> None:
        total = 0.0
        for row in range(self.direct_po_table.rowCount()):
            total += float(str(self.direct_po_table.item(row, 4).text()).replace("$", "").replace(",", "").strip() or 0)
        self.direct_total_label.setText(f"MATERIAL REQUEST TOTAL: ${total:,.2f}")

    def on_direct_po_double_click(self, row: int, column: int) -> None:
        if self.direct_workspace_readonly:
            return
        if column not in (0, 3):
            return
        values = [self.direct_po_table.item(row, col).text() for col in range(7)]
        current_text = str(values[0 if column == 0 else 3]).replace("$", "").replace(",", "").strip()
        def commit(new_text: str) -> None:
            try:
                new_value = max(0.0, float(new_text.strip() or 0))
                if column == 0:
                    values[0] = f"{new_value:.2f}"
                else:
                    values[3] = f"${new_value:.2f}"
                qty = float(str(values[0]).replace("$", "").replace(",", "").strip() or 0)
                price = float(str(values[3]).replace("$", "").replace(",", "").strip() or 0)
                values[4] = f"${qty * price:,.2f}"
                for col, value in enumerate(values):
                    self.direct_po_table.item(row, col).setText(value)
                self.refresh_direct_po_total()
            except ValueError:
                return

        self._show_inline_editor(self.direct_po_table, row, column, current_text, commit)

    def _harvest_direct_po_payload(self):
        if self.direct_workspace_readonly:
            raise ValueError("This RFQ/PO is locked or no longer editable.")
        if self._current_material_request_action_type() != "PurchaseOrder":
            raise ValueError("Manual RFQ draft save is not wired in this workspace yet.")
        if self._current_material_request_source_type() != "WorkOrder":
            raise ValueError("Purchase Order compatibility actions currently require Work Order as the source.")

        vendor_label = self.direct_vendor_combo.currentText().strip()
        work_order_id = self.direct_source_combo.currentData()
        vendor_id = self.direct_vendor_lookup.get(vendor_label)
        if not work_order_id or not vendor_id:
            raise ValueError("Select both an open work order and a vendor.")
        if self.direct_po_table.rowCount() == 0:
            raise ValueError("Add at least one material before saving the purchase order.")

        items = []
        price_updates = []
        for row in range(self.direct_po_table.rowCount()):
            qty = float(self.direct_po_table.item(row, 0).text())
            part_number = self.direct_po_table.item(row, 1).text() or None
            desc = self.direct_po_table.item(row, 2).text().strip()
            price = float(str(self.direct_po_table.item(row, 3).text()).replace("$", "").replace(",", "").strip() or 0)
            catalog_item_id = self.direct_po_table.item(row, 6).text() or None

            normalized_catalog_item_id = int(catalog_item_id) if catalog_item_id not in (None, "") else None
            if not part_number:
                raise ValueError("Part Number is required for every material request row.")
            if not desc and normalized_catalog_item_id is None:
                raise ValueError("Description is required for manual rows that are not linked to a known catalog material.")

            items.append(
                {
                    "catalog_item_id": normalized_catalog_item_id,
                    "desc": desc,
                    "qty": qty,
                    "price": price,
                }
            )
            if normalized_catalog_item_id is not None:
                price_updates.append((normalized_catalog_item_id, price, part_number))

        return {
            "work_order_id": work_order_id,
            "vendor_id": vendor_id,
            "vendor_label": vendor_label,
            "items": items,
            "price_updates": price_updates,
            "expected_date": self.direct_eta_edit.text().strip() or None,
            "expected_note": self.direct_eta_note_edit.text().strip() or None,
        }

    def _harvest_material_request_rfq_payload(self) -> dict:
        if self.direct_workspace_readonly:
            raise ValueError("This RFQ/PO is locked or no longer editable.")
        if self._current_material_request_action_type() != "RFQ":
            raise ValueError("Switch Action Type to RFQ first.")

        source_label = self.direct_source_combo.currentText().strip()
        source_type = self._current_material_request_source_type()
        source_id = self.direct_source_combo.currentData()
        vendor_label = self.direct_vendor_combo.currentText().strip()
        vendor_id = self.direct_vendor_lookup.get(vendor_label)

        if not source_id:
            raise ValueError(
                f"Select a valid {self._material_request_source_selector_label(source_type).rstrip(':').lower()} first."
            )
        if not vendor_id:
            raise ValueError("Select a vendor first.")
        if self.direct_po_table.rowCount() == 0:
            raise ValueError("Add at least one material row.")

        material_rows: list[dict] = []
        for row in range(self.direct_po_table.rowCount()):
            qty = float(str(self.direct_po_table.item(row, 0).text() or "0").strip())
            part_number = str(self.direct_po_table.item(row, 1).text() or "").strip() or None
            description = str(self.direct_po_table.item(row, 2).text() or "").strip()
            unit_cost = float(
                str(self.direct_po_table.item(row, 3).text() or "")
                .replace("$", "")
                .replace(",", "")
                .strip()
                or 0
            )
            notes = str(self.direct_po_table.item(row, 5).text() or "").strip() or None
            catalog_item_id = str(self.direct_po_table.item(row, 6).text() or "").strip() or None
            if not part_number:
                raise ValueError("Part Number is required for every material request row.")
            if not description and catalog_item_id in (None, ""):
                raise ValueError("Description is required for manual rows that are not linked to a known catalog material.")
            material_rows.append(
                {
                    "qty": qty,
                    "part_number": part_number,
                    "description": description,
                    "unit_cost": unit_cost,
                    "notes": notes,
                    "catalog_item_id": int(catalog_item_id) if catalog_item_id not in (None, "") else None,
                }
            )

        return {
            "source_type": source_type,
            "source_id": int(source_id),
            "source_label": source_label,
            "vendor_id": int(vendor_id),
            "vendor_label": vendor_label,
            "due_date": self.direct_eta_edit.text().strip() or None,
            "rfq_notes": self.direct_eta_note_edit.text().strip() or None,
            "material_rows": material_rows,
        }

    def save_material_request_rfq_draft(self) -> None:
        try:
            payload = self._harvest_material_request_rfq_payload()
            if self.direct_active_rfq_id and (
                str(self.direct_active_rfq_source_type or "") != str(payload["source_type"])
                or int(self.direct_active_rfq_source_id or 0) != int(payload["source_id"])
                or int(self.direct_active_rfq_vendor_id or 0) != int(payload["vendor_id"])
            ):
                raise ValueError(
                    "The loaded RFQ source/vendor context changed. Use New RFQ/PO before saving a different material request."
                )
            update_existing = (
                bool(self.direct_active_rfq_id)
                and str(self.direct_active_rfq_source_type or "") == str(payload["source_type"])
                and int(self.direct_active_rfq_source_id or 0) == int(payload["source_id"])
                and int(self.direct_active_rfq_vendor_id or 0) == int(payload["vendor_id"])
            )
            if update_existing:
                update_manual_rfq_draft(
                    int(self.direct_active_rfq_id),
                    payload["material_rows"],
                    due_date=payload["due_date"],
                    rfq_notes=payload["rfq_notes"],
                )
                rfq_id = int(self.direct_active_rfq_id)
                success_message = f"RFQ #{rfq_id} was updated as a draft."
            else:
                rfq_id = create_manual_rfq_draft(
                    payload["source_type"],
                    int(payload["source_id"]),
                    int(payload["vendor_id"]),
                    payload["material_rows"],
                    due_date=payload["due_date"],
                    rfq_notes=payload["rfq_notes"],
                )
                success_message = f"RFQ #{rfq_id} was created as a draft."

            self.direct_active_rfq_id = int(rfq_id)
            self.direct_active_rfq_source_type = str(payload["source_type"])
            self.direct_active_rfq_source_id = int(payload["source_id"])
            self.direct_active_rfq_vendor_id = int(payload["vendor_id"])
            self.active_rfq_id = int(rfq_id)
            self.current_vendor_id = int(payload["vendor_id"])
            self.selected_vendor_name = str(payload["vendor_label"] or "")
            self.direct_current_status = "Draft"
            self.direct_status_label.setText(f"RFQ #{rfq_id} saved as Draft")
            self.load_pipeline()
            self._select_rfq_in_pipeline(rfq_id)
            QMessageBox.information(self, "Save RFQ Draft Succeeded", success_message)
        except Exception as exc:
            QMessageBox.critical(self, "Save RFQ Draft Failed", f"Save RFQ Draft failed: {exc}")

    def preview_material_request_rfq(self) -> None:
        if not self.direct_active_rfq_id:
            try:
                self._harvest_material_request_rfq_payload()
            except Exception as exc:
                QMessageBox.warning(self, "Preview RFQ", str(exc))
                return
            QMessageBox.information(self, "Preview RFQ", "Save RFQ Draft first.")
            return
        try:
            header = get_rfq_header_data(int(self.direct_active_rfq_id))
            if not header:
                raise ValueError(f"RFQ #{self.direct_active_rfq_id} could not be loaded.")
            estimate_id = self._require_estimate_id_from_rfq_context(
                header,
                rfq_id=int(self.direct_active_rfq_id),
                action_label="preview this RFQ",
            )
            material_ids = []
            for item in get_rfq_items_for_matrix(int(self.direct_active_rfq_id)):
                material_id = item.get("MaterialID")
                if material_id in (None, ""):
                    continue
                material_ids.append(material_id)
            if not material_ids:
                raise ValueError(
                    f"RFQ #{self.direct_active_rfq_id} has no packaged material lines to preview."
                )
            preview_text = build_rfq_preview_text(
                estimate_id,
                str(header.get("VendorName") or self.direct_vendor_combo.currentText().strip() or "Vendor"),
                due_date=str(header.get("DueDate") or "") or None,
                material_ids=material_ids,
                rfq_id=int(self.direct_active_rfq_id),
            )
            source_lines: list[str] = []
            source_label = str(header.get("MaterialRequestSourceLabel") or "").strip()
            if source_label:
                source_lines.append(f"Source: {source_label}")
            request_notes = str(header.get("MaterialRequestNotes") or "").strip()
            if request_notes:
                source_lines.append(f"Request Notes: {request_notes}")
            if source_lines:
                preview_parts = preview_text.splitlines()
                insert_at = 2 if len(preview_parts) >= 2 else len(preview_parts)
                for line in reversed(source_lines):
                    preview_parts.insert(insert_at, line)
                preview_text = "\n".join(preview_parts)
            dialog = QDialog(self)
            dialog.setWindowTitle("Preview RFQ")
            dialog.resize(760, 560)
            layout = QVBoxLayout(dialog)
            preview_box = QPlainTextEdit()
            preview_box.setReadOnly(True)
            preview_box.setPlainText(preview_text)
            layout.addWidget(preview_box, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            buttons.accepted.connect(dialog.accept)
            layout.addWidget(buttons)
            dialog.exec()
        except Exception as exc:
            QMessageBox.critical(self, "Preview RFQ Failed", str(exc))

    def preview_material_request_rfq_send(self) -> None:
        if not self.direct_active_rfq_id:
            try:
                self._harvest_material_request_rfq_payload()
            except Exception as exc:
                QMessageBox.warning(self, "Preview RFQ Send", str(exc))
                return
            QMessageBox.information(self, "Preview RFQ Send", "Save RFQ Draft first.")
            return
        try:
            selected_template_ids = self.get_selected_rfq_template_ids()
            preview = prepare_rfq_delivery_message(
                int(self.direct_active_rfq_id),
                header_template_id=selected_template_ids["header_template_id"],
                body_template_id=selected_template_ids["body_template_id"],
                footer_template_id=selected_template_ids["footer_template_id"],
            )
            if not preview.get("success"):
                QMessageBox.warning(
                    self,
                    "RFQ Send Preview Unavailable",
                    str(preview.get("reason") or "RFQ send preview could not be prepared."),
                )
                return
            self._show_rfq_send_preview_dialog(preview)
        except Exception as exc:
            QMessageBox.critical(self, "Preview RFQ Send Failed", str(exc))

    def send_material_request_rfq(self) -> None:
        if not self.direct_active_rfq_id:
            try:
                self._harvest_material_request_rfq_payload()
            except Exception as exc:
                QMessageBox.warning(self, "Send RFQ", str(exc))
                return
            QMessageBox.information(self, "Send RFQ", "Save RFQ Draft first.")
            return
        try:
            self._execute_guarded_rfq_send(
                int(self.direct_active_rfq_id),
                success_callback=self._after_material_request_rfq_send_success,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def _select_rfq_in_pipeline(self, rfq_id: int) -> None:
        target = str(rfq_id)
        for row in range(self.rfq_table.rowCount()):
            if str(self.rfq_table.item(row, 0).text()) == target:
                self.rfq_table.selectRow(row)
                return

    def _after_material_request_rfq_send_success(self, rfq_id: int, result: dict, preview: dict) -> None:
        self.direct_active_rfq_id = int(rfq_id)
        self.direct_current_status = "Sent"
        self._set_material_request_readonly(True)
        self.direct_status_label.setText(f"RFQ #{rfq_id} sent successfully")
        self._select_rfq_in_pipeline(int(rfq_id))

    def _execute_guarded_rfq_send(
        self,
        rfq_id: int,
        *,
        success_callback: Callable[[int, dict, dict], None] | None = None,
        use_current_preview_body: bool = False,
    ) -> dict | None:
        selected_template_ids = self.get_selected_rfq_template_ids()
        preview = prepare_rfq_delivery_message(
            int(rfq_id),
            header_template_id=selected_template_ids["header_template_id"],
            body_template_id=selected_template_ids["body_template_id"],
            footer_template_id=selected_template_ids["footer_template_id"],
        )
        if not preview.get("success"):
            QMessageBox.warning(
                self,
                "RFQ Send Unavailable",
                str(preview.get("reason") or "RFQ send preview could not be prepared."),
            )
            return None
        if use_current_preview_body:
            preview = self._apply_current_rfq_editor_body(preview)

        confirmation = self._show_rfq_send_confirmation_dialog(preview)
        if not confirmation:
            return None

        result = send_rfq(
            int(rfq_id),
            sent_by="UI",
            override_recipient=confirmation.get("override_recipient"),
            header_template_id=selected_template_ids["header_template_id"],
            body_template_id=selected_template_ids["body_template_id"],
            footer_template_id=selected_template_ids["footer_template_id"],
            body_override=(str(preview.get("Body") or "") if use_current_preview_body else None),
        )
        if not result.get("success"):
            delivery_confirmed = bool(result.get("DeliveryConfirmed"))
            failure_message = str(result.get("reason") or "RFQ send failed.")
            if delivery_confirmed:
                failure_message += (
                    "\n\nThe email gateway appears to have accepted the send, but RFQ status updates did not complete."
                    "\nReview the outbound log before retrying to avoid a duplicate delivery."
                )
            message_box = QMessageBox(self)
            message_box.setWindowTitle("RFQ Send Failed")
            message_box.setText(failure_message)
            message_box.setIcon(
                QMessageBox.Icon.Warning if delivery_confirmed else QMessageBox.Icon.Critical
            )
            message_box.exec()
            return result

        resolved_recipient = str(
            result.get("ResolvedRecipientEmail") or preview.get("VendorEmail") or ""
        ).strip()
        QMessageBox.information(
            self,
            "RFQ Sent",
            "The RFQ was sent successfully."
            f"\n\nRecipient: {resolved_recipient}"
            f"\nAttachment: {result.get('AttachmentPath') or 'Unknown'}",
        )
        self.load_pipeline()
        self._select_rfq_in_pipeline(int(rfq_id))
        if success_callback:
            success_callback(int(rfq_id), result, preview)
        return result

    def _persist_direct_purchase_order(self, status: str = "Draft") -> int:
        payload = self._harvest_direct_po_payload()
        if self.direct_active_po_id and (
            int(self.direct_active_po_source_id or 0) != int(payload["work_order_id"])
            or int(self.direct_active_po_vendor_id or 0) != int(payload["vendor_id"])
        ):
            raise ValueError(
                "The loaded purchase order source/vendor context changed. Use New RFQ/PO before saving a different request."
            )
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
        self.direct_active_po_source_id = int(payload["work_order_id"])
        self.direct_active_po_vendor_id = int(payload["vendor_id"])
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
            self._set_procurement_tab(self.tab_document, center="PO")
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
            self.direct_status_label.setText(f"PO #{po_id} sent through the guarded vendor send flow")
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def select_purchase_order_in_pipeline(self, po_id: int, prefer_direct: bool = False) -> None:
        target = str(po_id)
        for row in range(self.po_table.rowCount()):
            if self.po_table.item(row, 0).text() == target:
                self.po_table.selectRow(row)
                status_text = str(self.po_table.item(row, 4).text() or "").strip()
                self.load_material_request_purchase_order(po_id, select_tab=True)
                if not prefer_direct and status_text != "Draft":
                    self.load_existing_purchase_order(po_id, select_tab=False)
                return

    def load_direct_purchase_order(self, po_id: int, select_tab: bool = True) -> None:
        try:
            self.load_material_request_purchase_order(po_id, select_tab=select_tab)
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
            work_order_id = int(header.get("WorkOrderID") or 0)
            if work_order_id:
                combo_index = self.po_work_order_combo.findData(work_order_id)
                if combo_index >= 0:
                    self.po_work_order_combo.blockSignals(True)
                    self.po_work_order_combo.setCurrentIndex(combo_index)
                    self.po_work_order_combo.blockSignals(False)
            source_link_map = {
                int(link["POItemID"]): link
                for link in get_purchase_order_source_links(po_id)
                if link.get("POItemID") is not None
            }

            self.po_item_table.setRowCount(0)
            for row_data in get_po_items_with_receiving(po_id):
                source_link = source_link_map.get(int(row_data["POItemID"]), {})
                row = self.po_item_table.rowCount()
                self.po_item_table.insertRow(row)
                values = [
                    str(row_data.get("MaterialID") or ""),
                    str(row_data["Description"]),
                    f"{float(row_data.get('Remaining') or 0):.2f}",
                    f"{float(row_data.get('QuantityOrdered') or 0):.2f}",
                    f"${float(row_data.get('UnitPriceAtOrder') or 0):.2f}",
                    str(row_data.get("CatalogItemID") or ""),
                    str(source_link.get("RFQCarriedSelectionID") or ""),
                    str(source_link.get("PriceRequestID") or ""),
                    str(source_link.get("PRItemID") or ""),
                    str(source_link.get("VendorID") or ""),
                    str(source_link.get("EstimateID") or ""),
                    "",
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
            self.btn_save_po_draft.setEnabled(False)
            self.po_load_carried_button.setEnabled(False)
            self.po_add_vendor_package_button.setEnabled(False)
            self.po_add_selected_button.setEnabled(False)
            self.render_review_document()
            if select_tab:
                self._set_procurement_tab(self.tab_build_po, center="PO")
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
        self.request_rfq_preview_refresh("Vendor selection changed.")

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
        self.render_rfq_preview_for_selection(force=True, reason="Estimate context changed.")

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
        self.request_rfq_preview_refresh("RFQ package changed.")

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
        self.request_rfq_preview_refresh("RFQ package changed.")

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
            self.render_rfq_preview_for_selection(force=True, reason="RFQ draft saved.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _set_rfq_preview_status(self, message: str, *, warning: bool = False) -> None:
        self.rfq_preview_status_label.setText(str(message or ""))
        self.rfq_preview_status_label.setStyleSheet(
            "color: #8a5a00; font-weight: 600;" if warning else "color: #1f4f99; font-weight: 600;"
        )

    def _set_rfq_preview_content(self, body_text: str, *, is_html: bool) -> None:
        self._suspend_rfq_preview_dirty_tracking = True
        try:
            self.rfq_preview_is_html = bool(is_html)
            if is_html:
                self.rfq_preview.setHtml(str(body_text or ""))
            else:
                self.rfq_preview.setPlainText(str(body_text or ""))
        finally:
            self._suspend_rfq_preview_dirty_tracking = False
        self.rfq_preview_dirty = False
        self.rfq_preview_refresh_pending = False
        self.rfq_preview_refresh_reason = ""

    def _current_rfq_preview_body(self) -> str:
        return self.rfq_preview.toHtml() if self.rfq_preview_is_html else self.rfq_preview.toPlainText()

    def _build_current_rfq_package_lines_text(self) -> str:
        lines: list[str] = []
        for row in range(self.package_table.rowCount()):
            qty = str(self.package_table.item(row, 1).text() if self.package_table.item(row, 1) else "").strip()
            description = str(self.package_table.item(row, 2).text() if self.package_table.item(row, 2) else "").strip()
            if not description:
                continue
            prefix = f"{qty} x " if qty else ""
            lines.append(f"- {prefix}{description}")
        return "\n".join(lines)

    def _build_current_rfq_package_lines_html(self) -> str:
        items: list[str] = []
        for row in range(self.package_table.rowCount()):
            qty = str(self.package_table.item(row, 1).text() if self.package_table.item(row, 1) else "").strip()
            description = str(self.package_table.item(row, 2).text() if self.package_table.item(row, 2) else "").strip()
            if not description:
                continue
            prefix = f"{qty} x " if qty else ""
            items.append(f"<li>{escape(prefix + description)}</li>")
        if not items:
            return ""
        return "<ul>\n" + "\n".join(items) + "\n</ul>"

    def _merge_rfq_preview_body_with_package_lines(self, body_text: str, *, is_html: bool) -> str:
        return str(body_text or "")

    def _render_rfq_preview_payload(self) -> dict:
        try:
            vendor_name = self.selected_vendor_name.strip()
            material_ids = [self.package_table.item(row, 0).text() for row in range(self.package_table.rowCount())]
            if not self.current_estimate_id or not vendor_name or not material_ids:
                return {
                    "success": False,
                    "reason": "Select an estimate, choose a vendor, and package materials to review the RFQ.",
                    "Body": "Select an estimate, choose a vendor, and package materials to review the RFQ.",
                    "TemplateUsed": "fallback",
                    "TemplateRenderWarning": None,
                }
            preview = render_rfq_delivery_preview_context(
                estimate_id=int(self.current_estimate_id),
                vendor_name=vendor_name,
                due_date=self.due_date_edit.text().strip() or None,
                material_ids=material_ids,
                rfq_id=self.active_rfq_id,
                header_template_id=self.get_selected_rfq_template_ids()["header_template_id"],
                body_template_id=self.get_selected_rfq_template_ids()["body_template_id"],
                footer_template_id=self.get_selected_rfq_template_ids()["footer_template_id"],
            )
            if not preview.get("success"):
                preview_text = build_rfq_preview_text(
                    self.current_estimate_id,
                    vendor_name,
                    due_date=self.due_date_edit.text().strip() or None,
                    material_ids=material_ids,
                    rfq_id=self.active_rfq_id,
                )
                preview["Body"] = preview_text
                preview["TemplateUsed"] = preview.get("TemplateUsed") or "fallback"
                return preview
            return preview
        except Exception as exc:
            return {
                "success": False,
                "reason": f"Preview unavailable: {exc}",
                "Body": f"Preview unavailable:\n{exc}",
                "TemplateUsed": "fallback",
                "TemplateRenderWarning": str(exc),
            }

    def request_rfq_preview_refresh(self, reason: str) -> None:
        if self.rfq_preview_dirty:
            self.rfq_preview_refresh_pending = True
            self.rfq_preview_refresh_reason = reason
            self._set_rfq_preview_status(
                f"{reason} Click Re-render From Templates to apply it. Manual edits are preserved.",
                warning=True,
            )
            return
        self.render_rfq_preview_for_selection(force=False, reason=reason)

    def rerender_rfq_preview_from_templates(self) -> None:
        if self.rfq_preview_dirty:
            answer = QMessageBox.question(
                self,
                "Overwrite Manual RFQ Edits?",
                "Re-rendering from templates will overwrite the current manual edits in the RFQ email body draft.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.render_rfq_preview_for_selection(force=True, reason="Preview re-rendered from selected templates.")

    def _on_rfq_preview_text_changed(self) -> None:
        if self._suspend_rfq_preview_dirty_tracking:
            return
        self.rfq_preview_dirty = True
        self._set_rfq_preview_status(
            "Manual edits detected. This editable body will be used for Preview RFQ Send and Send RFQ."
        )

    def _on_rfq_template_selection_changed(self, reason: str) -> None:
        self.request_rfq_preview_refresh(reason)

    def render_rfq_preview_for_selection(self, *, force: bool = False, reason: str = "") -> None:
        if self.rfq_preview_dirty and not force:
            self.request_rfq_preview_refresh(reason or "Preview context changed.")
            return
        preview = self._render_rfq_preview_payload()
        body_text = str(preview.get("Body") or "")
        is_html = self._rfq_body_looks_like_html(body_text)
        body_text = self._merge_rfq_preview_body_with_package_lines(body_text, is_html=is_html)
        self._set_rfq_preview_content(body_text, is_html=is_html)
        if preview.get("TemplateRenderWarning"):
            self._set_rfq_preview_status(str(preview.get("TemplateRenderWarning")), warning=True)
        elif reason:
            self._set_rfq_preview_status(reason)
        else:
            self._set_rfq_preview_status(
                f"Editable RFQ email body draft rendered from {preview.get('TemplateUsed') or 'fallback'}."
            )

    def preview_current_rfq(self) -> None:
        self.render_rfq_preview_for_selection(force=False, reason="Preview refreshed.")

    def _apply_current_rfq_editor_body(self, preview: dict) -> dict:
        if not isinstance(preview, dict):
            return preview
        editor_body = str(self._current_rfq_preview_body() or "").strip()
        if editor_body:
            preview["Body"] = editor_body
        return preview

    def send_current_rfq(self) -> None:
        if not self.current_estimate_id:
            QMessageBox.warning(self, "Stop", "Select an estimate and prepare an RFQ first.")
            return
        try:
            if not self.active_rfq_id:
                self.generate_and_save_rfq()
                if not self.active_rfq_id:
                    return
            self._execute_guarded_rfq_send(int(self.active_rfq_id), use_current_preview_body=True)
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def preview_rfq_send(self) -> None:
        if not self.active_rfq_id:
            QMessageBox.information(
                self,
                "RFQ Send Preview Unavailable",
                "Select an existing RFQ first. Preview does not auto-create or auto-save RFQs.",
            )
            return

        try:
            selected_template_ids = self.get_selected_rfq_template_ids()
            preview = prepare_rfq_delivery_message(
                int(self.active_rfq_id),
                header_template_id=selected_template_ids["header_template_id"],
                body_template_id=selected_template_ids["body_template_id"],
                footer_template_id=selected_template_ids["footer_template_id"],
            )
            if not preview.get("success"):
                QMessageBox.warning(
                    self,
                    "RFQ Send Preview Unavailable",
                    str(preview.get("reason") or "RFQ send preview could not be prepared."),
                )
                return
            preview = self._apply_current_rfq_editor_body(preview)
            self._show_rfq_send_preview_dialog(preview)
        except Exception as exc:
            QMessageBox.critical(self, "RFQ Send Preview Error", str(exc))

    def _selected_rfq_ids(self) -> list[int]:
        rows = sorted({item.row() for item in self.rfq_table.selectedItems()})
        rfq_ids: list[int] = []
        for row in rows:
            try:
                rfq_ids.append(int(self.rfq_table.item(row, 0).text()))
            except (AttributeError, TypeError, ValueError):
                continue
        return rfq_ids

    def preview_selected_rfq_batch(self) -> None:
        rfq_ids = self._selected_rfq_ids()
        if not rfq_ids:
            QMessageBox.information(
                self,
                "Batch Preview Unavailable",
                "Select one or more existing RFQs in the Active RFQs list first.",
            )
            return

        try:
            selected_template_ids = self.get_selected_rfq_template_ids()
            preview = prepare_rfq_batch_preview(
                rfq_ids,
                header_template_id=selected_template_ids["header_template_id"],
                body_template_id=selected_template_ids["body_template_id"],
                footer_template_id=selected_template_ids["footer_template_id"],
            )
            if not preview.get("success"):
                QMessageBox.warning(
                    self,
                    "Batch Preview Unavailable",
                    str(preview.get("reason") or "RFQ batch preview could not be prepared."),
                )
                return
            self._show_rfq_batch_preview_dialog(preview)
        except Exception as exc:
            QMessageBox.critical(self, "Batch Preview Error", str(exc))

    def _rfq_body_looks_like_html(self, body: str) -> bool:
        lowered = str(body or "").strip().lower()
        if not lowered:
            return False
        html_markers = (
            "<html",
            "<body",
            "<section",
            "<header",
            "<footer",
            "<p",
            "<h1",
            "<h2",
            "<h3",
            "<strong",
            "<em",
            "<ul",
            "<ol",
            "<li",
            "<div",
            "<br",
            "<table",
        )
        return any(marker in lowered for marker in html_markers)

    def _show_rfq_html_source_dialog(self, title: str, body_html: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        source_box = QPlainTextEdit()
        source_box.setReadOnly(True)
        source_box.setPlainText(str(body_html or ""))
        layout.addWidget(source_box, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _add_rfq_body_preview_section(
        self,
        layout: QVBoxLayout,
        *,
        preview: dict,
        source_title: str,
    ) -> None:
        body_text = str(preview.get("Body") or "")
        body_is_html = self._rfq_body_looks_like_html(body_text)

        body_header = QHBoxLayout()
        body_label = QLabel("Body")
        body_header.addWidget(body_label)
        body_header.addStretch(1)
        if body_is_html:
            source_button = QPushButton("Show HTML Source")
            source_button.clicked.connect(
                lambda: self._show_rfq_html_source_dialog(source_title, body_text)
            )
            body_header.addWidget(source_button)
        layout.addLayout(body_header)

        body_field = QTextBrowser()
        body_field.setOpenExternalLinks(False)
        if body_is_html:
            body_field.setHtml(body_text)
        else:
            body_field.setPlainText(body_text)
        layout.addWidget(body_field, 1)

    def _show_rfq_send_preview_dialog(self, preview: dict) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Preview RFQ Send")
        dialog.resize(760, 620)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        warning = QLabel("Preview only. Email is not sent in this workflow slice.")
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(warning)

        source_document_label = str(
            preview.get("SourceDocumentLabel")
            or f"Estimate #{preview.get('EstimateID') or 'Unknown'}"
        ).strip()
        meta = QLabel(
            f"RFQ ID: {preview.get('PriceRequestID') or 'Unknown'}\n"
            f"Estimate ID: {preview.get('EstimateID') or 'Unknown'}\n"
            f"Source: {source_document_label}\n"
            f"Vendor: {preview.get('VendorName') or 'Unknown Vendor'}"
        )
        meta.setWordWrap(True)
        layout.addWidget(meta)

        to_label = QLabel("To")
        layout.addWidget(to_label)
        to_field = QLineEdit(str(preview.get("VendorEmail") or ""))
        to_field.setReadOnly(True)
        layout.addWidget(to_field)

        subject_label = QLabel("Subject")
        layout.addWidget(subject_label)
        subject_field = QLineEdit(str(preview.get("Subject") or ""))
        subject_field.setReadOnly(True)
        layout.addWidget(subject_field)

        attachment_label = QLabel("Attachment Path")
        layout.addWidget(attachment_label)
        attachment_field = QLineEdit(str(preview.get("AttachmentPath") or ""))
        attachment_field.setReadOnly(True)
        layout.addWidget(attachment_field)

        self._add_rfq_body_preview_section(
            layout,
            preview=preview,
            source_title="RFQ Preview HTML Source",
        )

        template_label = QLabel(f"Template used: {preview.get('TemplateUsed') or 'fallback'}")
        template_label.setWordWrap(True)
        layout.addWidget(template_label)
        if preview.get("TemplateRenderWarning"):
            warning_label = QLabel(str(preview.get("TemplateRenderWarning")))
            warning_label.setWordWrap(True)
            warning_label.setStyleSheet("color: #8a5a00;")
            layout.addWidget(warning_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _show_rfq_batch_preview_dialog(self, preview: dict) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Preview RFQ Batch")
        dialog.resize(980, 620)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        warning = QLabel(
            "Preview only. Email is not sent in this workflow.\n"
            f"Selected RFQs: {len(preview.get('Rows') or [])} | "
            f"Ready: {preview.get('ReadyCount') or 0} | "
            f"Errors: {preview.get('ErrorCount') or 0}"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(warning)

        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["RFQ", "Vendor", "Email", "Status", "Attachment", "Subject"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setColumnWidth(0, 70)
        table.setColumnWidth(1, 170)
        table.setColumnWidth(2, 220)
        table.setColumnWidth(3, 110)
        table.setColumnWidth(4, 250)
        table.setColumnWidth(5, 240)

        rows = preview.get("Rows") or []
        for row_data in rows:
            row = table.rowCount()
            table.insertRow(row)
            values = [
                str(row_data.get("PriceRequestID") or row_data.get("RFQID") or ""),
                str(row_data.get("VendorName") or ""),
                str(row_data.get("ResolvedRecipientEmail") or row_data.get("VendorEmail") or ""),
                "Ready" if row_data.get("PreviewReady") else "Blocked",
                str(row_data.get("AttachmentPath") or ""),
                str(row_data.get("Subject") or ""),
            ]
            for column, value in enumerate(values):
                self._set_item(table, row, column, value)
        layout.addWidget(table, 1)

        detail_label = QLabel("Body Preview / Validation Detail")
        layout.addWidget(detail_label)
        detail_box = QPlainTextEdit()
        detail_box.setReadOnly(True)
        layout.addWidget(detail_box, 1)

        def refresh_detail() -> None:
            selected_row = table.currentRow()
            if selected_row < 0 or selected_row >= len(rows):
                detail_box.setPlainText("")
                return
            row_data = rows[selected_row]
            source_label = str(
                row_data.get("SourceDocumentLabel")
                or f"Estimate #{row_data.get('EstimateID') or ''}"
            ).strip()
            detail_box.setPlainText(
                f"RFQ ID: {row_data.get('PriceRequestID') or row_data.get('RFQID') or ''}\n"
                f"Source: {source_label}\n"
                f"Vendor: {row_data.get('VendorName') or ''}\n"
                f"Status: {'Ready' if row_data.get('PreviewReady') else 'Blocked'}\n"
                f"Reason: {row_data.get('PreviewReason') or ''}\n"
                f"Template: {row_data.get('TemplateUsed') or 'fallback'}\n"
                f"Attachment: {row_data.get('AttachmentPath') or ''}\n\n"
                f"{row_data.get('Body') or ''}"
            )

        table.itemSelectionChanged.connect(refresh_detail)
        if rows:
            table.selectRow(0)
            refresh_detail()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _show_rfq_send_confirmation_dialog(self, preview: dict) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm RFQ Send")
        dialog.resize(760, 680)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        warning = QLabel(
            "This will send the RFQ attachment to the vendor.\n"
            "It will mark the RFQ as Sent only after successful delivery."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(warning)

        source_document_label = str(
            preview.get("SourceDocumentLabel")
            or f"Estimate #{preview.get('EstimateID') or 'Unknown'}"
        ).strip()
        meta = QLabel(
            f"RFQ ID: {preview.get('PriceRequestID') or 'Unknown'}\n"
            f"Estimate ID: {preview.get('EstimateID') or 'Unknown'}\n"
            f"Source: {source_document_label}\n"
            f"Vendor: {preview.get('VendorName') or 'Unknown Vendor'}"
        )
        meta.setWordWrap(True)
        layout.addWidget(meta)

        to_label = QLabel("To")
        layout.addWidget(to_label)
        to_field = QLineEdit(str(preview.get("VendorEmail") or ""))
        to_field.setReadOnly(True)
        layout.addWidget(to_field)

        override_label = QLabel("Test Recipient Override (optional)")
        layout.addWidget(override_label)
        override_field = QLineEdit("")
        override_field.setPlaceholderText("Leave blank to send to the vendor email above.")
        layout.addWidget(override_field)

        original_label = QLabel("Original Vendor Email")
        layout.addWidget(original_label)
        original_field = QLineEdit(str(preview.get("VendorEmail") or ""))
        original_field.setReadOnly(True)
        layout.addWidget(original_field)

        subject_label = QLabel("Subject")
        layout.addWidget(subject_label)
        subject_field = QLineEdit(str(preview.get("Subject") or ""))
        subject_field.setReadOnly(True)
        layout.addWidget(subject_field)

        attachment_label = QLabel("Attachment Path")
        layout.addWidget(attachment_label)
        attachment_field = QLineEdit(str(preview.get("AttachmentPath") or ""))
        attachment_field.setReadOnly(True)
        layout.addWidget(attachment_field)

        self._add_rfq_body_preview_section(
            layout,
            preview=preview,
            source_title="RFQ Send HTML Source",
        )

        template_label = QLabel(f"Template used: {preview.get('TemplateUsed') or 'fallback'}")
        template_label.setWordWrap(True)
        layout.addWidget(template_label)
        if preview.get("TemplateRenderWarning"):
            warning_label = QLabel(str(preview.get("TemplateRenderWarning")))
            warning_label.setWordWrap(True)
            warning_label.setStyleSheet("color: #8a5a00;")
            layout.addWidget(warning_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        send_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if send_button:
            send_button.setText("Send RFQ")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return {
            "override_recipient": str(override_field.text() or "").strip() or None,
        }

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
            self.load_bid_compare_for_rfq(rfq_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def check_wo_lock(self, *_args) -> None:
        if self.build_po_readonly:
            return
        self.refresh_po_state_for_current_selection()

    def refresh_po_state_for_current_selection(self) -> None:
        if self.build_po_readonly:
            self.btn_save_po_draft.setEnabled(False)
            self.po_load_carried_button.setEnabled(False)
            self.po_add_vendor_package_button.setEnabled(False)
            self.po_add_selected_button.setEnabled(False)
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
            existing_status = str(existing_po.get("Status") or "").strip()
            is_draft = existing_status == "Draft"
            has_rows = self.po_item_table.rowCount() > 0
            self.btn_save_po_draft.setEnabled(is_draft and has_work_order and has_rows)
            self.btn_create_po.setEnabled(is_draft and has_work_order and has_rows)
            self.btn_view_po.setEnabled(not is_draft)
            self.btn_export_po.setEnabled(not is_draft)
            self.btn_send_po.setEnabled(not is_draft)
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
            self.btn_save_po_draft.setEnabled(has_work_order and self.po_item_table.rowCount() > 0)
            self.btn_create_po.setEnabled(has_work_order)
            self.btn_view_po.setEnabled(False)
            self.btn_export_po.setEnabled(False)
            self.btn_send_po.setEnabled(False)
        self.po_load_carried_button.setEnabled(has_work_order)
        self.po_add_vendor_package_button.setEnabled(has_work_order and self.po_carried_source_table.rowCount() > 0)
        self.po_add_selected_button.setEnabled(has_work_order and self.po_carried_source_table.rowCount() > 0)

    def on_create_po(self) -> None:
        try:
            self.active_po_id = self._persist_build_po_record(status="Locked")
            self._set_procurement_tab(self.tab_document, center="PO")
            self.lbl_po_status.setText(f"PO #{self.active_po_id} locked and materials committed")
            self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def on_view_po(self) -> None:
        if not self.active_po_id:
            return
        self.render_review_document()
        self._set_procurement_tab(self.tab_document, center="PO")

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
            preview = prepare_purchase_order_delivery_message(int(self.active_po_id))
            if not preview.get("success"):
                QMessageBox.warning(
                    self,
                    "PO Send Unavailable",
                    str(preview.get("reason") or "Purchase order send preview could not be prepared."),
                )
                return

            confirmation = self._show_po_send_confirmation_dialog(preview)
            if not confirmation:
                return

            result = send_purchase_order(
                int(self.active_po_id),
                sent_by="UI",
                override_recipient=confirmation.get("override_recipient"),
            )
            if not result.get("success"):
                delivery_confirmed = bool(result.get("DeliveryConfirmed"))
                failure_message = str(result.get("reason") or "Purchase order send failed.")
                if delivery_confirmed:
                    failure_message += (
                        "\n\nThe email gateway appears to have accepted the send, but PO status updates did not complete."
                        "\nReview the outbound log before retrying to avoid a duplicate delivery."
                    )
                message_box = QMessageBox(self)
                message_box.setWindowTitle("PO Send Failed")
                message_box.setText(failure_message)
                message_box.setIcon(
                    QMessageBox.Icon.Warning if delivery_confirmed else QMessageBox.Icon.Critical
                )
                message_box.exec()
                return

            resolved_recipient = result.get("ResolvedRecipientEmail") or result.get("VendorEmail")
            self.lbl_po_status.setText(f"PO #{self.active_po_id} sent to {resolved_recipient}")
            self.lbl_po_status.setStyleSheet("font-weight: 700; color: green;")
            self.load_pipeline()
            self.load_existing_purchase_order(self.active_po_id, select_tab=False)
            QMessageBox.information(
                self,
                "PO Sent",
                f"Purchase Order #{self.active_po_id} was sent to {resolved_recipient}.\n\n"
                f"Attachment:\n{result.get('AttachmentPath') or result.get('PDFPath') or result.get('DOCXPath') or 'Generated during send'}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def _show_po_send_confirmation_dialog(self, preview: dict) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Purchase Order Send")
        dialog.resize(760, 680)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        warning = QLabel(
            "This will send the purchase order attachment to the vendor.\n"
            "It will mark the purchase order as Sent only after successful delivery."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(warning)

        meta = QLabel(
            f"PO ID: {preview.get('PurchaseOrderID') or 'Unknown'}\n"
            f"Work Order ID: {preview.get('WorkOrderID') or 'Unknown'}\n"
            f"Vendor: {preview.get('VendorName') or 'Unknown Vendor'}"
        )
        meta.setWordWrap(True)
        layout.addWidget(meta)

        to_label = QLabel("To")
        layout.addWidget(to_label)
        to_field = QLineEdit(str(preview.get("VendorEmail") or ""))
        to_field.setReadOnly(True)
        layout.addWidget(to_field)

        override_label = QLabel("Test Recipient Override (optional)")
        layout.addWidget(override_label)
        override_field = QLineEdit("")
        override_field.setPlaceholderText("Leave blank to send to the vendor email above.")
        layout.addWidget(override_field)

        original_label = QLabel("Original Vendor Email")
        layout.addWidget(original_label)
        original_field = QLineEdit(str(preview.get("VendorEmail") or ""))
        original_field.setReadOnly(True)
        layout.addWidget(original_field)

        subject_label = QLabel("Subject")
        layout.addWidget(subject_label)
        subject_field = QLineEdit(str(preview.get("Subject") or ""))
        subject_field.setReadOnly(True)
        layout.addWidget(subject_field)

        attachment_label = QLabel("Attachment Path")
        layout.addWidget(attachment_label)
        attachment_field = QLineEdit(str(preview.get("AttachmentPath") or ""))
        attachment_field.setReadOnly(True)
        layout.addWidget(attachment_field)

        body_label = QLabel("Body")
        layout.addWidget(body_label)
        body_field = QPlainTextEdit()
        body_field.setReadOnly(True)
        body_field.setPlainText(str(preview.get("Body") or ""))
        layout.addWidget(body_field, 1)

        template_label = QLabel(f"Template used: {preview.get('TemplateUsed') or 'fallback'}")
        template_label.setWordWrap(True)
        layout.addWidget(template_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        send_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if send_button:
            send_button.setText("Send Purchase Order")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return {
            "override_recipient": str(override_field.text() or "").strip() or None,
        }

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
