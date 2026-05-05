from __future__ import annotations

import datetime
from html import escape
import os
import time
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
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.estimates import (
    get_estimate_materials,
    list_available_draft_estimates_for_material_request,
)
from neon_ai.database.material_calls import (
    create_material_call_from_manual_rows,
    create_material_call_from_estimate_items,
    create_vendor_rfq_for_material_call,
    get_material_call,
    get_material_call_items,
    get_next_material_call_number_preview,
    get_material_call_rfq_pipeline,
    legacy_loose_rfq_debug_enabled,
    update_material_call_status_and_notes,
)
from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.models import DocumentTemplateKind
from neon_ai.document_control.render_service import DocumentRenderService
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
    get_purchase_order_receiving_metadata,
    get_purchase_order_for_material_request,
    get_purchase_order_edit_items,
    get_purchase_order_items_for_material_request,
    get_purchase_orders_grouped_for_pipeline,
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
    get_all_vendors,
    get_bid_compare_data,
    get_rfq_for_material_request,
    get_rfq_header_data,
    get_rfq_items_for_matrix,
    get_wo_resolution_for_rfq,
    lock_quote_response,
    lock_rfq_draft,
    save_bid_compare_carried_selections,
    save_quote_response,
    send_rfq_by_id,
    update_material_call_backed_rfq_draft_header,
    update_manual_rfq_draft,
)
from neon_ai.database.timesheets import (
    get_open_workorder_choices,
    list_open_work_orders_for_material_request,
)
from neon_ai.services.purchase_order_send_service import (
    can_send_purchase_order,
    prepare_purchase_order_delivery_message,
    send_purchase_order,
)
from neon_ai.services.rfq_send_service import (
    prepare_rfq_batch_preview,
    prepare_rfq_delivery_message,
    render_rfq_delivery_preview_context,
    send_rfq,
)
from neon_ai.services.template_token_service import get_purchase_order_document_tokens


def _perf_log(area: str, name: str, started_at: float, **fields: Any) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    extras: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            normalized = "true" if value else "false"
        else:
            normalized = str(value).replace(" ", "_")
        extras.append(f"{key}={normalized}")
    suffix = f" {' '.join(extras)}" if extras else ""
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}{suffix}")


def _legacy_po_document_tab_enabled() -> bool:
    return str(os.getenv("NEON_ENABLE_LEGACY_PO_DOCUMENT_TAB", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _legacy_loose_rfq_debug_path_enabled() -> bool:
    return legacy_loose_rfq_debug_enabled()


class RFQViewerPage(QWidget):
    RFQ_DELIVERY_DOCUMENT_TYPE_CODE = "RFQ_DELIVERY"
    RFQ_SEND_USAGE_CONTEXT = "RFQ_SEND"
    PO_DRAFT_USAGE_CONTEXT = "PURCHASE_ORDER_DRAFT_WORKSPACE"
    PO_DOCUMENT_TYPE_CODE_CANDIDATES = (
        "PURCHASE_ORDER",
        "PURCHASE_ORDER_DOCUMENT",
        "PO_DOCUMENT",
        "PURCHASE_ORDER_DELIVERY",
        "PO_DELIVERY",
        "PurchaseOrder",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        started_at = time.perf_counter()
        super().__init__(parent)
        self.current_estimate_id: int | None = None
        self.selected_quote_path: str | None = None
        self.current_vendor_id: int | None = None
        self.active_rfq_id: int | None = None
        self.active_material_call_id: int | None = None
        self.active_material_call_number = ""
        self.active_material_call_row: dict[str, Any] | None = None
        self.active_material_call_rfq_rows: list[dict[str, Any]] = []
        self.active_work_order_id: int | None = None
        self.active_work_order_number = ""
        self.active_purchase_order_id: int | None = None
        self.active_purchase_order_row: dict[str, Any] | None = None
        self.active_vendor_id: int | None = None
        self.active_po_id: int | None = None
        self.wo_resolution: dict | None = None
        self.is_receiving_mode = False
        self._receiving_loaded_po_id: int | None = None
        self.direct_search_results: dict[str, dict] = {}
        self.direct_estimate_lookup: dict[str, int] = {}
        self.direct_source_lookup: dict[str, int] = {}
        self.direct_workorder_lookup: dict[str, int] = {}
        self.direct_vendor_lookup: dict[str, int] = {}
        self.build_mc_estimate_lookup: dict[str, int] = {}
        self.build_mc_workorder_lookup: dict[str, int] = {}
        self.build_mc_source_lookup: dict[str, int] = {}
        self.direct_active_rfq_id: int | None = None
        self.direct_active_rfq_source_type: str | None = None
        self.direct_active_rfq_source_id: int | None = None
        self.direct_active_rfq_vendor_id: int | None = None
        self.direct_active_rfq_material_call_id: int | None = None
        self.direct_active_rfq_material_call_number = ""
        self.receive_quotes_material_call_id: int | None = None
        self.receive_quotes_material_call_number = ""
        self.receive_quotes_carry_warning_shown = False
        self.receive_quotes_edit_locked = False
        self.receive_quotes_current_status = ""
        self.direct_active_po_id: int | None = None
        self.direct_active_po_source_id: int | None = None
        self.direct_active_po_vendor_id: int | None = None
        self.direct_current_status = "Draft"
        self.direct_workspace_readonly = False
        self.direct_material_call_id: int | None = None
        self.direct_material_call_number = ""
        self.direct_material_call_source_type: str | None = None
        self.direct_material_call_source_id: int | None = None
        self.direct_material_call_signature: tuple[tuple[str, ...], ...] = ()
        self.direct_material_call_is_stale = False
        self.build_po_readonly = False
        self.procurement_center = "RFQ"
        self.bid_compare_current_rfq_id: int | None = None
        self.bid_compare_vendor_rows: list[dict] = []
        self.bid_compare_vendor_column_map: dict[int, tuple[int, int]] = {}
        self.bid_compare_save_supported = True
        self.bid_compare_save_message = ""
        self.po_carried_source_rows: list[dict] = []
        self._po_tree_selection_suppressed = False
        self._rfq_tree_selection_suppressed = False
        self.rfq_preview_dirty = False
        self.rfq_preview_is_html = False
        self.rfq_preview_stale = False
        self.rfq_preview_stale_reason = ""
        self.rfq_preview_refresh_pending = False
        self.rfq_preview_refresh_reason = ""
        self._suspend_rfq_preview_dirty_tracking = False
        self.create_po_document_dirty = False
        self.create_po_document_is_html = False
        self.create_po_document_stale = False
        self.create_po_document_stale_reason = ""
        self._suspend_create_po_dirty_tracking = False
        self._create_po_loaded_po_id: int | None = None
        self.selected_vendor_name = ""
        self.build_rfq_material_call_id: int | None = None
        self.build_rfq_material_call_number = ""
        self.build_rfq_material_call_estimate_id: int | None = None
        self.build_rfq_material_call_signature: tuple[int, ...] = ()
        self.build_rfq_material_call_is_stale = False
        self.build_rfq_material_call_source_type = "Estimate"
        self.build_rfq_material_call_source_id: int | None = None
        self.build_rfq_material_call_unsaved = False
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
        self._create_po_template_choices_by_kind: dict[str, list[object]] = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        self._create_po_template_defaults_by_kind: dict[str, int | None] = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }
        self._vendor_choices_cache: list[dict[str, Any]] | None = None
        self._draft_estimate_choices_cache: list[dict[str, Any]] | None = None
        self._material_request_work_order_choices_cache: list[dict[str, Any]] | None = None
        self._po_work_order_choices_cache: list[dict[str, Any]] | None = None
        self._rfq_template_cache_loaded = False
        self._create_po_template_cache_loaded = False
        self._create_po_template_document_type_code: str | None = None
        self._create_rfq_tab_loaded = False
        self._material_call_tab_loaded = False
        self._build_mc_tab_loaded = False
        self._po_build_tab_loaded = False
        self._create_rfq_context_key: tuple[int | None, int | None] | None = None
        self._receive_quotes_loaded_rfq_id: int | None = None
        self._bid_compare_loaded_rfq_id: int | None = None
        self._document_catalog_service: DocumentCatalogService | None = None

        self.direct_material_search_timer = QTimer(self)
        self.direct_material_search_timer.setSingleShot(True)
        self.direct_material_search_timer.timeout.connect(self._perform_direct_material_search)

        widgets_only_started_at = time.perf_counter()
        try:
            self._build_ui()
        finally:
            _perf_log(
                "ui",
                "rfq_viewer.__init__.widgets_only",
                widgets_only_started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
            )
        post_defaults_started_at = time.perf_counter()
        try:
            self._apply_initial_procurement_shell_defaults()
        finally:
            _perf_log(
                "ui",
                "rfq_viewer.__init__.post_defaults",
                post_defaults_started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
            )
            _perf_log(
                "ui",
                "rfq_viewer.__init__.total",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
            )

    def _active_procurement_tab_name(self) -> str:
        notebook = getattr(self, "notebook", None)
        if notebook is None:
            return "unknown"
        current_widget = notebook.currentWidget()
        tab_names = {
            getattr(self, "tab_direct_po", None): "material_call",
            getattr(self, "tab_build_rfq", None): "build_mc",
            getattr(self, "tab_create_rfq", None): "create_rfq",
            getattr(self, "tab_matrix", None): "receive_quotes",
            getattr(self, "tab_bid_compare", None): "bid_compare",
            getattr(self, "tab_build_po", None): "build_po",
            getattr(self, "tab_create_po", None): "create_po",
            getattr(self, "tab_receiving", None): "receiving",
            getattr(self, "tab_document", None): "po_document",
        }
        return tab_names.get(current_widget, "unknown")

    def _is_create_rfq_tab_active(self) -> bool:
        return getattr(self, "notebook", None) is not None and self.notebook.currentWidget() is self.tab_create_rfq

    def _is_receive_quotes_tab_active(self) -> bool:
        return getattr(self, "notebook", None) is not None and self.notebook.currentWidget() is self.tab_matrix

    def _is_bid_compare_tab_active(self) -> bool:
        return getattr(self, "notebook", None) is not None and self.notebook.currentWidget() is self.tab_bid_compare

    def _is_material_call_tab_active(self) -> bool:
        return getattr(self, "notebook", None) is not None and self.notebook.currentWidget() is self.tab_direct_po

    def _is_build_mc_tab_active(self) -> bool:
        return getattr(self, "notebook", None) is not None and self.notebook.currentWidget() is self.tab_build_rfq

    def _is_create_po_tab_active(self) -> bool:
        return getattr(self, "notebook", None) is not None and self.notebook.currentWidget() is self.tab_create_po

    def _legacy_po_document_tab_visible_in_normal_runtime(self) -> bool:
        return _legacy_po_document_tab_enabled()

    def _clear_page_session_caches(self) -> None:
        self._vendor_choices_cache = None
        self._draft_estimate_choices_cache = None
        self._material_request_work_order_choices_cache = None
        self._po_work_order_choices_cache = None
        self._rfq_template_cache_loaded = False
        self._create_rfq_tab_loaded = False
        self._material_call_tab_loaded = False
        self._build_mc_tab_loaded = False
        self._po_build_tab_loaded = False
        self._document_catalog_service = None

    def _apply_initial_procurement_shell_defaults(self) -> None:
        self._initialize_build_mc_shell()
        self.set_procurement_center("RFQ", hydrate_choices=False)

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

    def _get_cached_vendors(self, *, force_reload: bool = False) -> tuple[list[dict[str, Any]], bool]:
        if not force_reload and self._vendor_choices_cache is not None:
            return list(self._vendor_choices_cache), True
        rows = list(get_vendors() or [])
        self._vendor_choices_cache = rows
        return list(rows), False

    def _get_cached_draft_estimates(self, *, force_reload: bool = False) -> tuple[list[dict[str, Any]], bool]:
        if not force_reload and self._draft_estimate_choices_cache is not None:
            return list(self._draft_estimate_choices_cache), True
        rows = list(list_available_draft_estimates_for_material_request() or [])
        self._draft_estimate_choices_cache = rows
        return list(rows), False

    def _get_cached_material_request_work_orders(
        self,
        *,
        force_reload: bool = False,
    ) -> tuple[list[dict[str, Any]], bool]:
        if not force_reload and self._material_request_work_order_choices_cache is not None:
            return list(self._material_request_work_order_choices_cache), True
        rows = list(list_open_work_orders_for_material_request() or [])
        self._material_request_work_order_choices_cache = rows
        return list(rows), False

    def _get_cached_po_work_orders(self, *, force_reload: bool = False) -> tuple[list[dict[str, Any]], bool]:
        if not force_reload and self._po_work_order_choices_cache is not None:
            return list(self._po_work_order_choices_cache), True
        rows = list(get_open_workorder_choices() or [])
        self._po_work_order_choices_cache = rows
        return list(rows), False

    def _build_ui(self) -> None:
        started_at = time.perf_counter()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Material Procurement Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(lambda: self.refresh_data(clear_caches=True))
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

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.main_splitter, 1)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(self.left_splitter)

        self.rfq_group = QGroupBox("RFQ Packages")
        self.rfq_group.setMinimumWidth(280)
        self.rfq_group.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Expanding)
        rfq_layout = QVBoxLayout(self.rfq_group)
        self.rfq_tree = QTreeWidget()
        self.rfq_tree.setMinimumWidth(260)
        self.rfq_tree.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Expanding)
        self.rfq_tree.setColumnCount(7)
        self.rfq_tree.setHeaderLabels(["Package / RFQ", "Estimate", "Site", "Customer / Vendor", "Created / Sent", "Status", "Count"])
        self.rfq_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rfq_tree.setAlternatingRowColors(True)
        self.rfq_tree.setRootIsDecorated(True)
        self.rfq_tree.setItemsExpandable(True)
        self.rfq_tree.itemSelectionChanged.connect(self.on_rfq_select)
        self.rfq_tree.setColumnWidth(0, 240)
        self.rfq_tree.setColumnWidth(1, 90)
        self.rfq_tree.setColumnWidth(2, 160)
        self.rfq_tree.setColumnWidth(3, 180)
        self.rfq_tree.setColumnWidth(4, 110)
        self.rfq_tree.setColumnWidth(5, 100)
        self.rfq_tree.setColumnWidth(6, 70)
        rfq_layout.addWidget(self.rfq_tree)
        build_po_button = QPushButton("↳ Build PO from selected RFQ")
        build_po_button.clicked.connect(lambda: self._set_procurement_tab(self.tab_build_po, center="PO"))
        rfq_layout.addWidget(build_po_button)
        preview_batch_button = QPushButton("Preview RFQ Batch")
        preview_batch_button.clicked.connect(self.preview_selected_rfq_batch)
        rfq_layout.addWidget(preview_batch_button)
        self.left_splitter.addWidget(self.rfq_group)

        self.po_group = QGroupBox("Purchase Orders")
        po_layout = QVBoxLayout(self.po_group)
        self.po_tree = QTreeWidget()
        self.po_tree.setColumnCount(6)
        self.po_tree.setHeaderLabels(["Work Order / PO", "Customer / Vendor", "Site", "Date", "Status", "Count / Total"])
        self.po_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.po_tree.setAlternatingRowColors(True)
        self.po_tree.setRootIsDecorated(True)
        self.po_tree.setItemsExpandable(True)
        self.po_tree.itemSelectionChanged.connect(self.on_po_select)
        self.po_tree.setColumnWidth(0, 220)
        self.po_tree.setColumnWidth(1, 180)
        self.po_tree.setColumnWidth(2, 150)
        self.po_tree.setColumnWidth(3, 100)
        self.po_tree.setColumnWidth(4, 100)
        self.po_tree.setColumnWidth(5, 90)
        po_layout.addWidget(self.po_tree)
        self.left_splitter.addWidget(self.po_group)
        self.left_splitter.setCollapsible(0, False)
        self.left_splitter.setSizes([520, 360])
        self.left_splitter.setMinimumWidth(280)
        self.left_splitter.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Expanding)

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(10, 0, 0, 0)
        self.notebook = QTabWidget()
        workspace_layout.addWidget(self.notebook)
        self.main_splitter.addWidget(workspace)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setCollapsible(0, False)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([320, 1040])

        self.tab_direct_po = QWidget()
        self.tab_build_rfq = QWidget()
        self.tab_create_rfq = QWidget()
        self.tab_matrix = QWidget()
        self.tab_bid_compare = QWidget()
        self.tab_build_po = QWidget()
        self.tab_create_po = QWidget()
        self.tab_receiving = QWidget()
        self.tab_document = QWidget()
        self.notebook.addTab(self.tab_direct_po, "0. Material Call")
        self.notebook.addTab(self.tab_create_rfq, "Create RFQ")
        self.notebook.addTab(self.tab_build_rfq, "📦 1. Build MC from Estimate")
        self.notebook.addTab(self.tab_matrix, "📊 2. Bid Matrix")
        self.notebook.addTab(self.tab_bid_compare, "📊 3. Bid Compare")
        self.notebook.addTab(self.tab_build_po, "🛒 3. Build PO")
        self.notebook.addTab(self.tab_create_po, "Create Purchase Order")
        self.notebook.addTab(self.tab_receiving, "🚚 4. Receiving")
        self.notebook.addTab(self.tab_document, "📄 5. PO Document")

        material_call_tab_started_at = time.perf_counter()
        self.build_direct_po_tab_ui()
        _perf_log("ui", "build_material_call_tab", material_call_tab_started_at)

        build_mc_tab_started_at = time.perf_counter()
        self.build_package_ui()
        _perf_log("ui", "build_build_mc_tab", build_mc_tab_started_at)

        create_rfq_tab_started_at = time.perf_counter()
        self.build_create_rfq_tab_ui()
        _perf_log("ui", "build_create_rfq_tab", create_rfq_tab_started_at)

        receive_quotes_tab_started_at = time.perf_counter()
        self.build_matrix_ui()
        _perf_log("ui", "build_receive_quotes_tab", receive_quotes_tab_started_at)

        bid_compare_tab_started_at = time.perf_counter()
        self.build_bid_compare_shell_ui()
        _perf_log("ui", "build_bid_compare_tab", bid_compare_tab_started_at)

        po_tabs_started_at = time.perf_counter()
        self.build_po_tab_ui()
        self.build_create_po_tab_ui()
        self.build_receiving_tab_ui()
        self.build_document_tab_ui()
        _perf_log("ui", "build_po_tabs", po_tabs_started_at)
        create_index = self.notebook.indexOf(self.tab_create_rfq)
        build_index = self.notebook.indexOf(self.tab_build_rfq)
        if create_index >= 0 and build_index >= 0 and create_index < build_index:
            self.notebook.removeTab(create_index)
            build_index = self.notebook.indexOf(self.tab_build_rfq)
            self.notebook.insertTab(build_index + 1, self.tab_create_rfq, "Create RFQ")
        self.notebook.currentChanged.connect(self._on_procurement_tab_changed)
        _perf_log(
            "ui",
            "rfq_viewer._build_ui",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
        )

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
        self.refresh_rfq_pipeline()
        self.refresh_po_pipeline()

    def refresh_rfq_pipeline(self) -> None:
        started_at = time.perf_counter()
        if self.procurement_center != "RFQ":
            _perf_log(
                "ui",
                "rfq_viewer.refresh_rfq_pipeline",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                skipped=True,
                reason="inactive_center",
            )
            return
        selected_material_call_id = int(self.active_material_call_id or 0) or None
        selected_rfq_id = int(self.active_rfq_id or 0) or None
        material_call_count = 0
        rfq_count = 0
        legacy_rfq_count = 0
        include_legacy_loose_rfqs = False
        legacy_section_built = False
        restored_item: QTreeWidgetItem | None = None
        signal_block_started_at = time.perf_counter()
        clear_populate_started_at = time.perf_counter()
        self._rfq_tree_selection_suppressed = True
        self.rfq_tree.blockSignals(True)
        try:
            self.rfq_tree.clear()
            pipeline = get_material_call_rfq_pipeline()
            include_legacy_loose_rfqs = bool(pipeline.get("include_legacy_loose_rfqs"))
            material_calls = list(pipeline.get("material_calls") or [])
            material_call_count = len(material_calls)
            for material_call in material_calls:
                self._append_material_call_pipeline_row(material_call)
                rfq_count += len(list(material_call.get("rfqs") or []))
            legacy_rfqs = list(pipeline.get("legacy_rfqs") or []) if include_legacy_loose_rfqs else []
            legacy_rfq_count = len(legacy_rfqs)
            rfq_count += legacy_rfq_count
            if include_legacy_loose_rfqs and legacy_rfqs:
                legacy_section_built = True
                legacy_parent = QTreeWidgetItem(
                    [
                        "Legacy Loose RFQs",
                        "—",
                        "—",
                        "Compatibility Records",
                        "—",
                        "Legacy",
                        str(len(legacy_rfqs)),
                    ]
                )
                font = legacy_parent.font(0)
                font.setBold(True)
                legacy_parent.setFont(0, font)
                legacy_parent.setData(0, Qt.ItemDataRole.UserRole, {"kind": "legacy_section"})
                self.rfq_tree.addTopLevelItem(legacy_parent)
                for row_data in legacy_rfqs:
                    child = self._build_rfq_pipeline_child_item(row_data, legacy=True)
                    legacy_parent.addChild(child)
                legacy_parent.setExpanded(False)
        except Exception as exc:
            print(f"RFQ Pipeline error: {exc}")
        _perf_log(
            "ui",
            "rfq_tree.clear_populate",
            clear_populate_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=rfq_count,
            material_call_count=material_call_count,
            legacy_rfq_count=legacy_rfq_count,
            include_legacy_loose_rfqs=include_legacy_loose_rfqs,
            legacy_loading_skipped=not include_legacy_loose_rfqs,
            legacy_section_built=legacy_section_built,
        )

        if selected_rfq_id:
            restored_item = self._select_rfq_in_pipeline(int(selected_rfq_id))
        elif selected_material_call_id:
            restored_item = self._select_material_call_in_pipeline(int(selected_material_call_id))
        self.rfq_tree.blockSignals(False)
        self._rfq_tree_selection_suppressed = False
        _perf_log(
            "ui",
            "rfq_tree.signal_blocked",
            signal_block_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
        )
        selection_restore_started_at = time.perf_counter()
        if restored_item is not None:
            light_context_started_at = time.perf_counter()
            light_context = self._apply_rfq_tree_selection_light_context(
                restored_item,
                preview_reason="Pipeline refresh restored selection.",
            )
            _perf_log(
                "ui",
                "rfq_tree.selection_restore_light_context",
                light_context_started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                restored_kind=str(light_context.get("kind") or ""),
                material_call_id=light_context.get("material_call_id"),
                rfq_id=light_context.get("rfq_id"),
                skipped=bool(light_context.get("skipped")),
                reason=str(light_context.get("reason") or "") or None,
            )
            if not light_context.get("skipped"):
                self._hydrate_active_rfq_tree_selection_context(force_reload=False)
        _perf_log(
            "ui",
            "rfq_tree.selection_restore",
            selection_restore_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            restored_kind=str((restored_item.data(0, Qt.ItemDataRole.UserRole) or {}).get("kind") or "")
            if restored_item is not None
            else "none",
            skipped=restored_item is None,
        )
        _perf_log(
            "ui",
            "rfq_viewer.refresh_rfq_pipeline",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=rfq_count,
            material_call_count=material_call_count,
            legacy_rfq_count=legacy_rfq_count,
            include_legacy_loose_rfqs=include_legacy_loose_rfqs,
            legacy_loading_skipped=not include_legacy_loose_rfqs,
            legacy_section_built=legacy_section_built,
            skipped=False,
        )

    def refresh_po_pipeline(self) -> None:
        started_at = time.perf_counter()
        if self.procurement_center != "PO":
            _perf_log(
                "ui",
                "rfq_viewer.refresh_po_pipeline",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                skipped=True,
                reason="inactive_center",
            )
            return
        selected_work_order_id = int(self.active_work_order_id or 0) or None
        selected_purchase_order_id = int(self.active_purchase_order_id or self.active_po_id or 0) or None
        work_order_count = 0
        child_row_count = 0
        parent_row_count = 0
        restored_item: QTreeWidgetItem | None = None
        self._po_tree_selection_suppressed = True
        self.po_tree.blockSignals(True)
        try:
            self.po_tree.clear()
            for work_order_row in get_purchase_orders_grouped_for_pipeline():
                self._append_po_pipeline_parent_row(work_order_row)
                work_order_count += 1
                parent_row_count += 1
                child_row_count += len(list(work_order_row.get("purchase_orders") or []))
        except Exception as exc:
            print(f"PO Pipeline error: {exc}")
        if selected_purchase_order_id:
            restored_item = self._select_purchase_order_in_pipeline_item(int(selected_purchase_order_id))
        elif selected_work_order_id:
            restored_item = self._select_work_order_in_pipeline_item(int(selected_work_order_id))
        self.po_tree.blockSignals(False)
        self._po_tree_selection_suppressed = False
        if restored_item is not None:
            self.po_tree.setCurrentItem(restored_item)
        _perf_log(
            "ui",
            "rfq_viewer.refresh_po_pipeline",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=child_row_count,
            work_order_count=work_order_count,
            parent_row_count=parent_row_count,
            child_row_count=child_row_count,
            empty_reason="no_parent_rows" if parent_row_count == 0 else None,
            skipped=False,
        )

    def _append_po_pipeline_parent_row(self, work_order_row: dict[str, Any]) -> None:
        work_order_id = int(work_order_row.get("WorkOrderID") or 0) or None
        work_order_number = str(
            work_order_row.get("WorkOrderNumber") or (f"WO #{work_order_id}" if work_order_id is not None else "No Work Order")
        ).strip()
        customer_name = str(work_order_row.get("CustomerName") or "-")
        site_name = str(work_order_row.get("SiteName") or "-")
        status_text = str(work_order_row.get("Status") or "Open")
        po_count_text = str(work_order_row.get("POCount") or 0)
        parent = QTreeWidgetItem(
            [
                work_order_number,
                customer_name,
                site_name,
                "-",
                status_text,
                po_count_text,
            ]
        )
        font = parent.font(0)
        font.setBold(True)
        parent.setFont(0, font)
        parent.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {
                "kind": "work_order",
                "row": dict(work_order_row),
            },
        )
        self.po_tree.addTopLevelItem(parent)
        for po_row in list(work_order_row.get("purchase_orders") or []):
            parent.addChild(self._build_po_pipeline_child_item(po_row))
        parent.setExpanded(False)

    def _build_po_pipeline_child_item(self, row_data: dict[str, Any]) -> QTreeWidgetItem:
        po_id = int(row_data.get("PurchaseOrderID") or 0)
        vendor_name = str(row_data.get("VendorName") or "-")
        site_name = str(row_data.get("SiteName") or "-")
        date_text = str(row_data.get("Date") or "-")
        status_text = str(row_data.get("Status") or "Draft")
        total_value = float(row_data.get("PurchaseOrderTotal") or 0)
        child = QTreeWidgetItem(
            [
                f"PO #{po_id}",
                vendor_name,
                site_name,
                date_text,
                status_text,
                f"${total_value:,.2f}",
            ]
        )
        child.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {
                "kind": "purchase_order",
                "row": dict(row_data),
            },
        )
        return child

    def _iter_po_pipeline_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        for index in range(self.po_tree.topLevelItemCount()):
            parent = self.po_tree.topLevelItem(index)
            items.append(parent)
            for child_index in range(parent.childCount()):
                items.append(parent.child(child_index))
        return items

    def _select_work_order_in_pipeline_item(self, work_order_id: int) -> QTreeWidgetItem | None:
        target = int(work_order_id)
        for item in self._iter_po_pipeline_items():
            payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if str(payload.get("kind") or "") != "work_order":
                continue
            row = dict(payload.get("row") or {})
            if int(row.get("WorkOrderID") or 0) == target:
                item.setSelected(True)
                return item
        return None

    def _select_purchase_order_in_pipeline_item(self, po_id: int) -> QTreeWidgetItem | None:
        target = int(po_id)
        for item in self._iter_po_pipeline_items():
            payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if str(payload.get("kind") or "") != "purchase_order":
                continue
            row = dict(payload.get("row") or {})
            if int(row.get("PurchaseOrderID") or 0) == target:
                item.setSelected(True)
                return item
        return None

    def _append_material_call_pipeline_row(self, material_call: dict[str, Any]) -> None:
        material_call_id = int(material_call.get("MaterialCallID") or 0)
        material_call_number = str(material_call.get("MaterialCallNumber") or f"MC#{material_call_id}")
        estimate_text = f"Estimate #{material_call.get('EstimateID')}" if material_call.get("EstimateID") else "—"
        site_text = str(material_call.get("SiteName") or "—")
        customer_or_vendor = str(material_call.get("CustomerName") or "—")
        created_text = str(material_call.get("CreatedAt") or "").replace("T", " ")[:16] or "—"
        status_text = str(material_call.get("Status") or "Draft")
        count_text = str(material_call.get("RFQCount") or 0)
        parent = QTreeWidgetItem(
            [
                material_call_number,
                estimate_text,
                site_text,
                customer_or_vendor,
                created_text,
                status_text,
                count_text,
            ]
        )
        font = parent.font(0)
        font.setBold(True)
        parent.setFont(0, font)
        parent.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {
                "kind": "material_call",
                "row": material_call,
            },
        )
        self.rfq_tree.addTopLevelItem(parent)
        for row_data in list(material_call.get("rfqs") or []):
            child_row = dict(row_data)
            if child_row.get("EstimateID") in (None, "", 0, "0") and material_call.get("EstimateID") not in (None, "", 0, "0"):
                child_row["EstimateID"] = material_call.get("EstimateID")
            if not str(child_row.get("SiteName") or "").strip():
                child_row["SiteName"] = material_call.get("SiteName")
            parent.addChild(self._build_rfq_pipeline_child_item(child_row))
        parent.setExpanded(False)

    def _build_rfq_pipeline_child_item(self, row_data: dict[str, Any], *, legacy: bool = False) -> QTreeWidgetItem:
        rfq_id = int(row_data.get("PriceRequestID") or row_data.get("RFQID") or 0)
        estimate_text = f"Estimate #{row_data.get('EstimateID')}" if row_data.get("EstimateID") else "—"
        site_text = str(row_data.get("SiteName") or "—")
        vendor_text = str(row_data.get("VendorName") or "—")
        sent_text = str(row_data.get("DateSent") or row_data.get("DueDate") or "—")
        status_text = str(row_data.get("Status") or "Draft")
        child = QTreeWidgetItem(
            [
                f"RFQ #{rfq_id}",
                estimate_text,
                site_text,
                vendor_text,
                sent_text,
                status_text,
                "",
            ]
        )
        child.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {
                "kind": "legacy_rfq" if legacy else "rfq",
                "row": row_data,
            },
        )
        return child

    def _clear_active_material_call_context(self) -> None:
        self.active_material_call_id = None
        self.active_material_call_number = ""
        self.active_material_call_row = None
        self.active_material_call_rfq_rows = []

    def _clear_active_po_context(self) -> None:
        self.active_work_order_id = None
        self.active_work_order_number = ""
        self.active_purchase_order_id = None
        self.active_purchase_order_row = None
        self.active_vendor_id = None
        self.active_po_id = None

    def _apply_active_work_order_context(self, work_order_row: dict[str, Any] | None) -> None:
        if not work_order_row:
            self._clear_active_po_context()
            return
        work_order_id = int(work_order_row.get("WorkOrderID") or 0) or None
        self.active_work_order_id = work_order_id
        self.active_work_order_number = str(
            work_order_row.get("WorkOrderNumber") or (f"WO #{work_order_id}" if work_order_id is not None else "")
        ).strip()
        self.active_purchase_order_id = None
        self.active_purchase_order_row = None
        self.active_vendor_id = None
        self.active_po_id = None

    def _apply_active_purchase_order_context(self, po_row: dict[str, Any] | None) -> None:
        if not po_row:
            self.active_purchase_order_id = None
            self.active_purchase_order_row = None
            self.active_vendor_id = None
            self.active_po_id = None
            return
        work_order_id = int(po_row.get("WorkOrderID") or 0) or None
        self.active_work_order_id = work_order_id
        self.active_work_order_number = f"WO #{work_order_id}" if work_order_id is not None else ""
        self.active_purchase_order_id = int(po_row.get("PurchaseOrderID") or 0) or None
        self.active_purchase_order_row = dict(po_row)
        self.active_vendor_id = int(po_row.get("VendorID") or 0) or None
        self.active_po_id = self.active_purchase_order_id

    def _apply_active_material_call_context(self, material_call_row: dict[str, Any] | None) -> None:
        if not material_call_row:
            self._clear_active_material_call_context()
            self._refresh_create_rfq_context_panel()
            return
        self.active_material_call_row = dict(material_call_row)
        self.active_material_call_id = int(material_call_row.get("MaterialCallID") or 0) or None
        self.active_material_call_number = str(
            material_call_row.get("MaterialCallNumber") or f"MC#{self.active_material_call_id or ''}"
        ).strip()
        self.active_material_call_rfq_rows = list(material_call_row.get("rfqs") or [])
        self._refresh_create_rfq_context_panel()

    # CLEANUP_CANDIDATE_SHADOWED:
    # This earlier definition is shadowed by a later _load_build_rfq_from_material_call()
    # definition in this class. Keep it only until RFQ Center smokes cover the active path.
    def _load_build_rfq_from_material_call(self, material_call_row: dict[str, Any] | None) -> None:
        if not material_call_row:
            return
        estimate_id = int(material_call_row.get("EstimateID") or 0) or None
        if not estimate_id:
            return
        selected_ids: set[int] = set()
        material_call_items = list(get_material_call_items(int(material_call_row["MaterialCallID"])) or [])
        for item in material_call_items:
            if item.get("EstimateItemID") not in (None, "", 0):
                selected_ids.add(int(item["EstimateItemID"]))

        self.current_estimate_id = int(estimate_id)
        self.master_table.setRowCount(0)
        self.package_table.setRowCount(0)
        try:
            if hasattr(self, "est_combo"):
                self._set_build_mc_source_selection(int(estimate_id), fallback_label=f"Estimate #{estimate_id}")
            for mat in get_estimate_materials(int(estimate_id)):
                material_id = (
                    mat.get("EstimateMaterialID")
                    or mat.get("estimate_material_id")
                    or mat.get("MaterialID")
                    or mat.get("materialid")
                    or 0
                )
                target_table = self.package_table if int(material_id or 0) in selected_ids else self.master_table
                row = target_table.rowCount()
                target_table.insertRow(row)
                self._set_item(target_table, row, 0, str(material_id))
                self._set_item(target_table, row, 1, str(mat.get("Quantity") or ""), center=True)
                self._set_item(target_table, row, 2, str(mat.get("Description") or ""))
        except Exception as exc:
            print(f"Build MC material call hydration error: {exc}")

        self.build_rfq_material_call_id = int(material_call_row["MaterialCallID"])
        self.build_rfq_material_call_number = str(
            material_call_row.get("MaterialCallNumber") or f"MC#{material_call_row['MaterialCallID']}"
        )
        self.build_rfq_material_call_estimate_id = int(estimate_id)
        self.build_rfq_material_call_signature = self._current_build_rfq_material_signature()
        self.build_rfq_material_call_is_stale = False
        self._refresh_build_rfq_material_call_context_status()

    def _load_material_request_material_call(self, material_call_row: dict[str, Any] | None) -> None:
        if not material_call_row:
            return
        material_call_id = int(material_call_row.get("MaterialCallID") or 0)
        if not material_call_id:
            return
        self.load_material_call_workspace(int(material_call_id), material_call_row=material_call_row)

    def _load_receive_quotes_material_call_summary(self, material_call_row: dict[str, Any] | None) -> None:
        self.matrix_table.setRowCount(0)
        self.quote_no_edit.setText("")
        self.quote_date_edit.setText("")
        self.attach_label.setText("No file attached")
        self.attach_label.setStyleSheet("color: gray;")
        self._clear_receive_quotes_material_call_context()
        if not material_call_row:
            return
        material_call_id = int(material_call_row.get("MaterialCallID") or 0) or None
        material_call_number = str(
            material_call_row.get("MaterialCallNumber") or (f"MC#{material_call_id}" if material_call_id else "")
        ).strip()
        self._set_receive_quotes_metadata_summary(
            material_call_number=material_call_number or "—",
            estimate_number=(
                f"Estimate #{material_call_row.get('EstimateID')}"
                if material_call_row.get("EstimateID")
                else "—"
            ),
            customer_name=str(material_call_row.get("CustomerName") or "—"),
            site_name=str(material_call_row.get("SiteName") or "—"),
        )
        vendor_rows = list(material_call_row.get("rfqs") or [])
        vendor_summary = ", ".join(
            f"{str(row.get('VendorName') or 'Vendor')} (RFQ #{row.get('PriceRequestID') or row.get('RFQID') or ''})"
            for row in vendor_rows
        ) or "No vendor RFQs created under this Material Call yet."
        self.receive_quotes_context_label.setText(
            f"Material Call: {material_call_number or 'Current package'}. "
            f"Select a child RFQ to edit one vendor quote directly. "
            "Receive Quotes saves quote data only. Use Bid Compare to carry pricing. "
            f"Current vendor RFQs: {vendor_summary}"
        )
        self.carry_btn.setText("Save Quote Response")
        self.carry_btn.setToolTip(
            "Select a child RFQ to edit one vendor quote directly. Bid Compare owns carried selection."
        )
        self._refresh_receive_quotes_edit_state()

    def _receive_quotes_status_is_edit_locked(self, status_text: str | None) -> bool:
        return str(status_text or "").strip().lower() in {"locked", "quote locked"}

    def _refresh_receive_quotes_edit_state(self) -> None:
        has_selected_rfq = bool(int(self.active_rfq_id or 0))
        editable = has_selected_rfq and not self.receive_quotes_edit_locked
        if hasattr(self, "quote_no_edit"):
            self.quote_no_edit.setReadOnly(not editable)
        if hasattr(self, "quote_date_edit"):
            self.quote_date_edit.setReadOnly(not editable)
        if hasattr(self, "quote_attach_button"):
            self.quote_attach_button.setEnabled(editable)
        if hasattr(self, "carry_btn"):
            self.carry_btn.setEnabled(editable)
        if hasattr(self, "lock_quote_btn"):
            self.lock_quote_btn.setEnabled(has_selected_rfq and not self.receive_quotes_edit_locked)
            self.lock_quote_btn.setText("Locked" if self.receive_quotes_edit_locked else "Lock Quote")

    def _load_create_rfq_vendor_choices(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        selected_vendor = str(self.selected_vendor_name or "").strip()
        selected_vendor_id = int(self.current_vendor_id or 0) or None
        rows, cache_hit = self._get_cached_vendors(force_reload=force_reload)
        self.create_rfq_vendor_combo.blockSignals(True)
        self.create_rfq_vendor_combo.clear()
        self.create_rfq_vendor_combo.addItem("", None)
        row_count = 0
        for row in rows:
            vendor_name = str(row.get("VendorName") or "").strip()
            if not vendor_name:
                continue
            row_count += 1
            self.create_rfq_vendor_combo.addItem(vendor_name, int(row["VendorID"]))
        if selected_vendor_id:
            combo_index = self.create_rfq_vendor_combo.findData(selected_vendor_id)
            if combo_index >= 0:
                self.create_rfq_vendor_combo.setCurrentIndex(combo_index)
            elif selected_vendor:
                self.create_rfq_vendor_combo.setCurrentText(selected_vendor)
        elif selected_vendor:
            self.create_rfq_vendor_combo.setCurrentText(selected_vendor)
        self.create_rfq_vendor_combo.blockSignals(False)
        _perf_log(
            "ui",
            "create_rfq.vendor_dropdown_reload",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=row_count,
            cache_hit=cache_hit,
            skipped=False,
        )

    def _on_create_rfq_vendor_changed(self, vendor_name: str) -> None:
        vendor_name = str(vendor_name or "").strip()
        if self.active_rfq_id:
            matching_row = self._current_create_rfq_child_row()
            locked_status = str((matching_row or {}).get("Status") or "").strip() or "Draft"
            preserved_vendor_name = str((matching_row or {}).get("VendorName") or self.selected_vendor_name or "").strip()
            self.create_rfq_vendor_combo.blockSignals(True)
            if self.current_vendor_id:
                preserved_index = self.create_rfq_vendor_combo.findData(int(self.current_vendor_id))
                if preserved_index >= 0:
                    self.create_rfq_vendor_combo.setCurrentIndex(preserved_index)
                else:
                    self.create_rfq_vendor_combo.setCurrentText(preserved_vendor_name)
            else:
                self.create_rfq_vendor_combo.setCurrentText(preserved_vendor_name)
            self.create_rfq_vendor_combo.blockSignals(False)
            QMessageBox.information(
                self,
                "Vendor Fixed",
                f"Vendor is fixed for the selected RFQ. {locked_status} RFQs keep their vendor identity. Select the Material Call parent to create a new vendor RFQ.",
            )
            return
        self.selected_vendor_name = vendor_name
        self.current_vendor_id = self.create_rfq_vendor_combo.currentData()
        self._set_vendor_selection(vendor_name)
        self._refresh_create_rfq_context_panel()
        self.request_rfq_preview_refresh("Vendor selection changed.")

    def _current_create_rfq_child_row(self) -> dict[str, Any] | None:
        rfq_rows = list(self.active_material_call_rfq_rows or [])
        if not self.active_rfq_id:
            return None
        target_rfq_id = int(self.active_rfq_id)
        for row in rfq_rows:
            if int(row.get("PriceRequestID") or row.get("RFQID") or 0) == target_rfq_id:
                return dict(row)
        return None

    def _current_create_rfq_material_call_due_date(self) -> str:
        if not self.active_material_call_row:
            return ""
        due_date_text, _note_text = self._parse_material_call_notes_payload(self.active_material_call_row.get("Notes"))
        return str(due_date_text or "").strip()

    def _set_create_rfq_vendor_combo_value(self, vendor_id: int | None, vendor_name: str = "") -> None:
        self.create_rfq_vendor_combo.blockSignals(True)
        if vendor_id:
            combo_index = self.create_rfq_vendor_combo.findData(int(vendor_id))
            if combo_index >= 0:
                self.create_rfq_vendor_combo.setCurrentIndex(combo_index)
            elif vendor_name:
                self.create_rfq_vendor_combo.setCurrentText(vendor_name)
            else:
                self.create_rfq_vendor_combo.setCurrentIndex(0)
        elif vendor_name:
            self.create_rfq_vendor_combo.setCurrentText(vendor_name)
        else:
            self.create_rfq_vendor_combo.setCurrentIndex(0)
        self.create_rfq_vendor_combo.blockSignals(False)

    def _set_create_rfq_due_date_value(self, due_date_text: str) -> None:
        self.due_date_edit.blockSignals(True)
        self.due_date_edit.setText(str(due_date_text or ""))
        self.due_date_edit.blockSignals(False)

    def _current_create_rfq_is_edit_locked(self) -> bool:
        matching_row = self._current_create_rfq_child_row()
        status_text = str((matching_row or {}).get("Status") or "").strip().lower()
        return status_text in {"locked", "sent"}

    def _refresh_create_rfq_editor_state(self) -> None:
        if not hasattr(self, "create_rfq_vendor_combo"):
            return
        has_material_call = bool(self.active_material_call_id and self.active_material_call_row)
        has_rfq = bool(self.active_rfq_id)
        is_edit_locked = self._current_create_rfq_is_edit_locked()
        is_existing_rfq = bool(has_material_call and has_rfq)

        self.create_rfq_vendor_combo.setEnabled(has_material_call and not is_existing_rfq and not is_edit_locked)
        self.due_date_edit.setEnabled(has_material_call and not is_edit_locked)
        self.rfq_preview.setReadOnly(is_edit_locked or not has_material_call)
        self.rfq_header_template_combo.setEnabled(has_material_call and not is_edit_locked)
        self.rfq_body_template_combo.setEnabled(has_material_call and not is_edit_locked)
        self.rfq_footer_template_combo.setEnabled(has_material_call and not is_edit_locked)
        self.rfq_rerender_preview_button.setEnabled(has_material_call and not is_edit_locked)
        self.btn_generate_rfq.setEnabled(has_material_call and not is_edit_locked)
        self.btn_lock_rfq.setEnabled(has_rfq and has_material_call and not is_edit_locked)
        self.create_rfq_preview_button.setEnabled(has_material_call)
        self.create_rfq_preview_send_button.setEnabled(has_rfq)
        self.btn_send_rfq.setEnabled(has_rfq)

        if not has_material_call:
            self.create_rfq_vendor_combo.setToolTip("Select or create a Material Call first.")
        elif is_existing_rfq:
            self.create_rfq_vendor_combo.setToolTip(
                "Vendor is fixed for an existing child RFQ. Select the Material Call parent to create a new vendor RFQ."
            )
        else:
            self.create_rfq_vendor_combo.setToolTip("Choose a vendor for the new child RFQ.")

    def _refresh_create_rfq_context_panel(self) -> None:
        if not hasattr(self, "create_rfq_material_call_value"):
            return
        material_call_row = dict(self.active_material_call_row or {})
        rfq_rows = list(self.active_material_call_rfq_rows or material_call_row.get("rfqs") or [])
        parent_due_date = self._current_create_rfq_material_call_due_date()
        current_context_key = (
            int(self.active_material_call_id or 0) or None,
            int(self.active_rfq_id or 0) or None,
        )
        context_changed = current_context_key != self._create_rfq_context_key
        self._create_rfq_context_key = current_context_key
        if not material_call_row:
            self.selected_vendor_name = ""
            self.current_vendor_id = None
            self.create_rfq_material_call_value.setText("none")
            self.create_rfq_estimate_value.setText("—")
            self.create_rfq_customer_value.setText("—")
            self.create_rfq_site_value.setText("—")
            self.create_rfq_date_sent_value.setText("—")
            self.create_rfq_status_value.setText("Draft")
            self.create_rfq_source_value.setText("Select a Material Call parent or RFQ child from the pipeline.")
            self.create_rfq_line_count_value.setText("0")
            self.create_rfq_context_status_label.setText("Select or create a Material Call first.")
            self._set_create_rfq_vendor_combo_value(None, "")
            self._set_create_rfq_due_date_value("")
            self._refresh_create_rfq_editor_state()
            return

        material_call_number = str(
            material_call_row.get("MaterialCallNumber") or f"MC#{material_call_row.get('MaterialCallID')}"
        ).strip()
        source_label = str(material_call_row.get("SourceDocumentLabel") or "").strip()
        created_from = (
            "Build MC from Estimate"
            if str(material_call_row.get("SourceType") or "") == "Estimate"
            else "Material Call"
        )
        self.create_rfq_material_call_value.setText(material_call_number)
        self.create_rfq_estimate_value.setText(
            f"Estimate #{material_call_row.get('EstimateID')}" if material_call_row.get("EstimateID") else "—"
        )
        self.create_rfq_customer_value.setText(str(material_call_row.get("CustomerName") or "—"))
        self.create_rfq_site_value.setText(str(material_call_row.get("SiteName") or "—"))
        self.create_rfq_source_value.setText(
            f"{created_from} | {source_label or str(material_call_row.get('SourceType') or 'Source')}"
        )
        self.create_rfq_line_count_value.setText(str(material_call_row.get("ItemCount") or 0))

        matching_row = self._current_create_rfq_child_row() if self.active_rfq_id else None

        status_text = str((matching_row or {}).get("Status") or ("New Draft" if material_call_row else "Draft"))
        date_sent_text = str((matching_row or {}).get("DateSent") or "—")
        self.create_rfq_status_value.setText(status_text)
        self.create_rfq_date_sent_value.setText(date_sent_text)

        if matching_row:
            vendor_name = str(matching_row.get("VendorName") or "").strip()
            vendor_id = int(matching_row.get("VendorID") or 0) or None
            due_value = str(matching_row.get("DueDate") or parent_due_date or "").strip()
            self.selected_vendor_name = vendor_name
            self.current_vendor_id = vendor_id
            self._set_create_rfq_vendor_combo_value(vendor_id, vendor_name)
            if context_changed or not self.due_date_edit.text().strip():
                self._set_create_rfq_due_date_value(due_value)
            if self._current_create_rfq_is_edit_locked():
                self.create_rfq_context_status_label.setText(
                    f"RFQ #{matching_row.get('PriceRequestID') or matching_row.get('RFQID')} is {status_text}. Preview and send remain available, but editing is locked."
                )
            else:
                self.create_rfq_context_status_label.setText(
                    f"Editing RFQ #{matching_row.get('PriceRequestID') or matching_row.get('RFQID')} for {vendor_name or 'the selected vendor'} under Material Call {material_call_number}."
                )
        else:
            if context_changed:
                self.selected_vendor_name = ""
                self.current_vendor_id = None
                self._set_create_rfq_vendor_combo_value(None, "")
            if context_changed or not self.due_date_edit.text().strip():
                self._set_create_rfq_due_date_value(parent_due_date)
            self.create_rfq_context_status_label.setText(
                "New vendor RFQ mode. Choose a vendor, review the due date, and save a draft under the active Material Call."
            )

        self._refresh_create_rfq_editor_state()

    def build_direct_po_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_direct_po)
        header = QGroupBox("Material Call Workspace")
        header_layout = QGridLayout(header)
        layout.addWidget(header)

        self.direct_action_type_label = QLabel("Action Type:")
        header_layout.addWidget(self.direct_action_type_label, 0, 0)
        self.direct_action_type_combo = QComboBox()
        self.direct_action_type_combo.addItem("RFQ", "RFQ")
        self.direct_action_type_combo.addItem("Purchase Order", "PurchaseOrder")
        self.direct_action_type_combo.setCurrentIndex(0)
        header_layout.addWidget(self.direct_action_type_combo, 0, 1)

        self.direct_source_type_static_label = QLabel("Source Type:")
        header_layout.addWidget(self.direct_source_type_static_label, 0, 2)
        self.direct_source_type_combo = QComboBox()
        self.direct_source_type_combo.addItem("Estimate", "Estimate")
        self.direct_source_type_combo.addItem("Work Order", "WorkOrder")
        self.direct_source_type_combo.setCurrentIndex(0)
        header_layout.addWidget(self.direct_source_type_combo, 0, 3)

        self.direct_new_request_button = QPushButton("New Material Call")
        self.direct_new_request_button.clicked.connect(self.start_new_material_call)
        header_layout.addWidget(self.direct_new_request_button, 0, 4)

        self.direct_add_vendor_rfq_button = QPushButton("Add Vendor RFQ")
        self.direct_add_vendor_rfq_button.clicked.connect(self.create_vendor_rfq_from_current_material_call)
        self.direct_add_vendor_rfq_button.setEnabled(False)
        header_layout.addWidget(self.direct_add_vendor_rfq_button, 1, 4)

        self.direct_vendor_label = QLabel("Vendor:")
        header_layout.addWidget(self.direct_vendor_label, 1, 0)
        self.direct_vendor_combo = QComboBox()
        self.direct_vendor_combo.setMinimumWidth(240)
        self.direct_vendor_combo.currentTextChanged.connect(self.on_direct_vendor_select)
        header_layout.addWidget(self.direct_vendor_combo, 1, 1)

        self.direct_date_label = QLabel("Due Date:")
        header_layout.addWidget(self.direct_date_label, 2, 0)
        self.direct_eta_edit = QLineEdit()
        header_layout.addWidget(self.direct_eta_edit, 2, 1)

        self.direct_source_label = QLabel("Work Order #:")
        header_layout.addWidget(self.direct_source_label, 2, 2)
        self.direct_source_combo = QComboBox()
        self.direct_source_combo.setMinimumWidth(320)
        self.direct_source_combo.currentTextChanged.connect(self.on_direct_material_request_context_changed)
        header_layout.addWidget(self.direct_source_combo, 2, 3)

        self.direct_request_note_label = QLabel("Call Notes:")
        header_layout.addWidget(self.direct_request_note_label, 3, 0)
        self.direct_eta_note_edit = QLineEdit()
        header_layout.addWidget(self.direct_eta_note_edit, 3, 1, 1, 3)

        self.direct_mode_hint_label = QLabel("")
        self.direct_mode_hint_label.setWordWrap(True)
        self.direct_mode_hint_label.setStyleSheet("color: #8a5a00;")
        header_layout.addWidget(self.direct_mode_hint_label, 4, 0, 1, 4)

        self.direct_status_label = QLabel("Material Call workspace not saved yet.")
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

        material_call_context = QFrame()
        material_call_context_layout = QVBoxLayout(material_call_context)
        material_call_context_layout.setContentsMargins(0, 0, 0, 0)
        material_call_context_layout.setSpacing(4)
        self.direct_create_material_call_button = QPushButton("Create Material Call From Rows")
        self.direct_create_material_call_button.clicked.connect(self.create_material_call_from_material_request_rows)
        material_call_context_layout.addWidget(
            self.direct_create_material_call_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )
        self.direct_material_call_label = QLabel("Current Material Call: none")
        self.direct_material_call_label.setStyleSheet("font-weight: 700; color: #1f4f99;")
        material_call_context_layout.addWidget(self.direct_material_call_label)
        self.direct_material_call_status_label = QLabel(
            "Create a Material Call from the staged material rows when you want to stage a pricing package."
        )
        self.direct_material_call_status_label.setWordWrap(True)
        self.direct_material_call_status_label.setStyleSheet("color: #6c757d;")
        material_call_context_layout.addWidget(self.direct_material_call_status_label)
        layout.addWidget(material_call_context)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.direct_total_label = QLabel("MATERIAL CALL TOTAL: $0.00")
        self.direct_total_label.setStyleSheet("font-size: 12px; font-weight: 700; color: blue;")
        footer_layout.addWidget(self.direct_total_label)
        footer_layout.addStretch(1)
        self.direct_save_material_call_button = QPushButton("Save Draft MC")
        self.direct_save_material_call_button.clicked.connect(self.save_direct_material_call_draft)
        footer_layout.addWidget(self.direct_save_material_call_button)
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

    def build_package_ui(self) -> None:
        layout = QVBoxLayout(self.tab_build_rfq)
        logistics = QGroupBox("1. Material Call Source")
        logistics_layout = QGridLayout(logistics)
        layout.addWidget(logistics)

        logistics_layout.addWidget(QLabel("Source Type:"), 0, 0)
        self.build_mc_source_type_combo = QComboBox()
        self.build_mc_source_type_combo.addItem("Estimate", "Estimate")
        self.build_mc_source_type_combo.addItem("Work Order", "WorkOrder")
        self.build_mc_source_type_combo.currentIndexChanged.connect(self.on_build_mc_source_type_changed)
        logistics_layout.addWidget(self.build_mc_source_type_combo, 0, 1)

        self.build_mc_source_label = QLabel("Estimate #:")
        logistics_layout.addWidget(self.build_mc_source_label, 0, 2)
        self.est_combo = QComboBox()
        self.est_combo.setMinimumWidth(260)
        self.est_combo.currentIndexChanged.connect(self.on_build_mc_source_changed)
        logistics_layout.addWidget(self.est_combo, 0, 3)

        logistics_layout.addWidget(QLabel("Date Prepared:"), 1, 0)
        self.date_sent_edit = QLineEdit(datetime.date.today().strftime("%Y-%m-%d"))
        self.date_sent_edit.setReadOnly(True)
        logistics_layout.addWidget(self.date_sent_edit, 1, 1)

        self.build_mc_new_button = QPushButton("New Material Call")
        self.build_mc_new_button.clicked.connect(self.start_new_build_material_call)
        logistics_layout.addWidget(self.build_mc_new_button, 1, 2)

        self.build_mc_save_button = QPushButton("Save Material Call")
        self.build_mc_save_button.clicked.connect(self.save_build_material_call)
        logistics_layout.addWidget(self.build_mc_save_button, 1, 3)

        basket_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(basket_splitter, 1)
        left = QGroupBox("Available Source Materials")
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

        right = QGroupBox("Current Material Call Package")
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
        package_footer = QFrame()
        package_footer_layout = QVBoxLayout(package_footer)
        package_footer_layout.setContentsMargins(0, 0, 0, 0)
        package_footer_layout.setSpacing(4)
        self.btn_create_material_call = QPushButton("Create Material Call From Selected")
        self.btn_create_material_call.clicked.connect(self.save_build_material_call)
        package_footer_layout.addWidget(self.btn_create_material_call, 0, Qt.AlignmentFlag.AlignLeft)
        self.build_rfq_material_call_label = QLabel("Current Material Call: none")
        self.build_rfq_material_call_label.setStyleSheet("font-weight: 700; color: #1f4f99;")
        package_footer_layout.addWidget(self.build_rfq_material_call_label)
        self.build_rfq_material_call_status_label = QLabel(
            "Click New Material Call to start a vendor-neutral package, then Save Material Call when the source and rows are ready."
        )
        self.build_rfq_material_call_status_label.setWordWrap(True)
        self.build_rfq_material_call_status_label.setStyleSheet("color: #6c757d;")
        package_footer_layout.addWidget(self.build_rfq_material_call_status_label)
        right_layout.addWidget(package_footer)
        basket_splitter.addWidget(right)

    def _initialize_build_mc_shell(self) -> None:
        if hasattr(self, "build_mc_source_type_combo"):
            self.build_mc_source_type_combo.blockSignals(True)
            self.build_mc_source_type_combo.setCurrentText("Estimate")
            self.build_mc_source_type_combo.blockSignals(False)
        if hasattr(self, "est_combo"):
            self.est_combo.blockSignals(True)
            self.est_combo.clear()
            self.est_combo.addItem("Open Build MC to load source choices…", None)
            self.est_combo.setCurrentIndex(0)
            self.est_combo.blockSignals(False)
        self.build_mc_source_lookup = {}
        self.build_mc_estimate_lookup = {}
        self.build_mc_workorder_lookup = {}
        self.master_table.setRowCount(0)
        self.package_table.setRowCount(0)
        self.current_estimate_id = None
        self._clear_build_rfq_material_call_context()
        self.build_rfq_material_call_unsaved = False

    def build_create_rfq_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_create_rfq)

        metadata_group = QGroupBox("Current Material Call / RFQ Context")
        metadata_layout = QGridLayout(metadata_group)
        layout.addWidget(metadata_group)

        metadata_layout.addWidget(QLabel("Material Call:"), 0, 0)
        self.create_rfq_material_call_value = QLabel("none")
        self.create_rfq_material_call_value.setStyleSheet("font-weight: 700; color: #1f4f99;")
        metadata_layout.addWidget(self.create_rfq_material_call_value, 0, 1)

        metadata_layout.addWidget(QLabel("Source Estimate:"), 0, 2)
        self.create_rfq_estimate_value = QLabel("—")
        metadata_layout.addWidget(self.create_rfq_estimate_value, 0, 3)

        metadata_layout.addWidget(QLabel("Customer:"), 1, 0)
        self.create_rfq_customer_value = QLabel("—")
        metadata_layout.addWidget(self.create_rfq_customer_value, 1, 1)

        metadata_layout.addWidget(QLabel("Site:"), 1, 2)
        self.create_rfq_site_value = QLabel("—")
        metadata_layout.addWidget(self.create_rfq_site_value, 1, 3)

        metadata_layout.addWidget(QLabel("Vendor:"), 2, 0)
        self.create_rfq_vendor_combo = QComboBox()
        self.create_rfq_vendor_combo.setMinimumWidth(240)
        self.create_rfq_vendor_combo.currentTextChanged.connect(self._on_create_rfq_vendor_changed)
        metadata_layout.addWidget(self.create_rfq_vendor_combo, 2, 1)

        metadata_layout.addWidget(QLabel("Due Date:"), 2, 2)
        self.create_rfq_due_date_edit = QLineEdit("")
        self.due_date_edit = self.create_rfq_due_date_edit
        self.due_date_edit.textChanged.connect(
            lambda _text: self.request_rfq_preview_refresh("Due date changed.")
        )
        metadata_layout.addWidget(self.due_date_edit, 2, 3)

        metadata_layout.addWidget(QLabel("Date Sent:"), 3, 0)
        self.create_rfq_date_sent_value = QLabel("—")
        metadata_layout.addWidget(self.create_rfq_date_sent_value, 3, 1)

        metadata_layout.addWidget(QLabel("Status:"), 3, 2)
        self.create_rfq_status_value = QLabel("Draft")
        metadata_layout.addWidget(self.create_rfq_status_value, 3, 3)

        metadata_layout.addWidget(QLabel("Created From:"), 4, 0)
        self.create_rfq_source_value = QLabel("—")
        self.create_rfq_source_value.setWordWrap(True)
        metadata_layout.addWidget(self.create_rfq_source_value, 4, 1, 1, 3)

        metadata_layout.addWidget(QLabel("Material Lines:"), 5, 0)
        self.create_rfq_line_count_value = QLabel("0")
        metadata_layout.addWidget(self.create_rfq_line_count_value, 5, 1)

        self.create_rfq_context_status_label = QLabel(
            "Select a Material Call in the RFQ Packages pipeline or create one from Build MC from Estimate."
        )
        self.create_rfq_context_status_label.setWordWrap(True)
        self.create_rfq_context_status_label.setStyleSheet("color: #6c757d;")
        metadata_layout.addWidget(self.create_rfq_context_status_label, 6, 0, 1, 4)

        template_row = QFrame()
        template_layout = QHBoxLayout(template_row)
        template_layout.setContentsMargins(0, 0, 0, 0)
        template_layout.setSpacing(8)
        template_layout.addWidget(QLabel("Header:"))
        self.rfq_header_template_combo = QComboBox()
        self.rfq_header_template_combo.setMinimumWidth(170)
        self.rfq_header_template_combo.currentIndexChanged.connect(
            lambda _index: self._on_rfq_template_selection_changed("Header template changed.")
        )
        template_layout.addWidget(self.rfq_header_template_combo)
        template_layout.addWidget(QLabel("Body:"))
        self.rfq_body_template_combo = QComboBox()
        self.rfq_body_template_combo.setMinimumWidth(190)
        self.rfq_body_template_combo.currentIndexChanged.connect(
            lambda _index: self._on_rfq_template_selection_changed("Body template changed.")
        )
        template_layout.addWidget(self.rfq_body_template_combo)
        template_layout.addWidget(QLabel("Footer:"))
        self.rfq_footer_template_combo = QComboBox()
        self.rfq_footer_template_combo.setMinimumWidth(170)
        self.rfq_footer_template_combo.currentIndexChanged.connect(
            lambda _index: self._on_rfq_template_selection_changed("Footer template changed.")
        )
        template_layout.addWidget(self.rfq_footer_template_combo)
        template_layout.addStretch(1)
        layout.addWidget(template_row)

        preview_group = QGroupBox("RFQ Email Body Draft")
        preview_layout = QVBoxLayout(preview_group)
        self.rfq_preview_note_label = QLabel(
            "This editable body will be used for Preview RFQ Send and Send RFQ."
        )
        self.rfq_preview_note_label.setWordWrap(True)
        self.rfq_preview_note_label.setStyleSheet("color: #6c757d;")
        preview_layout.addWidget(self.rfq_preview_note_label)
        self.rfq_preview_status_label = QLabel(
            "Select a Material Call package or RFQ to render the email body draft."
        )
        self.rfq_preview_status_label.setWordWrap(True)
        self.rfq_preview_status_label.setStyleSheet("color: #1f4f99; font-weight: 600;")
        preview_layout.addWidget(self.rfq_preview_status_label)
        self.rfq_preview = QTextEdit()
        self.rfq_preview.setAcceptRichText(True)
        self.rfq_preview.textChanged.connect(self._on_rfq_preview_text_changed)
        preview_layout.addWidget(self.rfq_preview)
        layout.addWidget(preview_group)

        action_bar = QFrame()
        action_bar_layout = QHBoxLayout(action_bar)
        action_bar_layout.setContentsMargins(0, 0, 0, 0)
        action_bar_layout.setSpacing(12)

        draft_actions = QGroupBox("Draft Actions")
        draft_actions_layout = QHBoxLayout(draft_actions)
        draft_actions_layout.setContentsMargins(10, 8, 10, 8)
        draft_actions_layout.setSpacing(8)
        self.rfq_rerender_preview_button = QPushButton("Re-render From Templates")
        self.rfq_rerender_preview_button.clicked.connect(self.rerender_rfq_preview_from_templates)
        draft_actions_layout.addWidget(self.rfq_rerender_preview_button)
        self.btn_generate_rfq = QPushButton("Save RFQ Draft")
        self.btn_generate_rfq.clicked.connect(self.generate_and_save_rfq)
        draft_actions_layout.addWidget(self.btn_generate_rfq)
        self.btn_lock_rfq = QPushButton("Lock RFQ")
        self.btn_lock_rfq.clicked.connect(self.lock_current_rfq)
        draft_actions_layout.addWidget(self.btn_lock_rfq)

        preview_send_actions = QGroupBox("Preview / Send")
        preview_send_actions_layout = QHBoxLayout(preview_send_actions)
        preview_send_actions_layout.setContentsMargins(10, 8, 10, 8)
        preview_send_actions_layout.setSpacing(8)
        self.create_rfq_preview_button = QPushButton("Preview RFQ")
        self.create_rfq_preview_button.clicked.connect(self.preview_current_rfq)
        preview_send_actions_layout.addWidget(self.create_rfq_preview_button)
        self.create_rfq_preview_send_button = QPushButton("Preview RFQ Send")
        self.create_rfq_preview_send_button.clicked.connect(self.preview_rfq_send)
        preview_send_actions_layout.addWidget(self.create_rfq_preview_send_button)
        self.btn_send_rfq = QPushButton("Send RFQ")
        self.btn_send_rfq.clicked.connect(self.send_current_rfq)
        preview_send_actions_layout.addWidget(self.btn_send_rfq)

        action_bar_layout.addWidget(draft_actions, 1)
        action_bar_layout.addWidget(preview_send_actions, 1)
        layout.addWidget(action_bar)

    def build_matrix_ui(self) -> None:
        layout = QVBoxLayout(self.tab_matrix)
        title = QLabel("Reviewing Responses")
        title.setStyleSheet("font-size: 12px; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)
        metadata_group = QGroupBox("Current RFQ")
        metadata_layout = QGridLayout(metadata_group)
        metadata_layout.addWidget(QLabel("Material Call:"), 0, 0)
        self.receive_quotes_material_call_value = QLabel("—")
        self.receive_quotes_material_call_value.setStyleSheet("font-weight: 700; color: #1f4f99;")
        metadata_layout.addWidget(self.receive_quotes_material_call_value, 0, 1)
        metadata_layout.addWidget(QLabel("RFQ #:"), 0, 2)
        self.receive_quotes_rfq_value = QLabel("—")
        self.receive_quotes_rfq_value.setStyleSheet("font-weight: 700; color: #1f4f99;")
        metadata_layout.addWidget(self.receive_quotes_rfq_value, 0, 3)
        metadata_layout.addWidget(QLabel("Estimate:"), 1, 0)
        self.receive_quotes_estimate_value = QLabel("—")
        metadata_layout.addWidget(self.receive_quotes_estimate_value, 1, 1)
        metadata_layout.addWidget(QLabel("Work Order:"), 1, 2)
        self.receive_quotes_work_order_value = QLabel("—")
        metadata_layout.addWidget(self.receive_quotes_work_order_value, 1, 3)
        metadata_layout.addWidget(QLabel("Vendor:"), 2, 0)
        self.receive_quotes_vendor_value = QLabel("—")
        metadata_layout.addWidget(self.receive_quotes_vendor_value, 2, 1)
        metadata_layout.addWidget(QLabel("Customer / Prepared For:"), 2, 2)
        self.receive_quotes_customer_value = QLabel("—")
        self.receive_quotes_customer_value.setWordWrap(True)
        metadata_layout.addWidget(self.receive_quotes_customer_value, 2, 3)
        metadata_layout.addWidget(QLabel("Project / Site:"), 3, 0)
        self.receive_quotes_site_value = QLabel("—")
        self.receive_quotes_site_value.setWordWrap(True)
        metadata_layout.addWidget(self.receive_quotes_site_value, 3, 1)
        metadata_layout.addWidget(QLabel("Due Date:"), 3, 2)
        self.receive_quotes_due_date_value = QLabel("—")
        metadata_layout.addWidget(self.receive_quotes_due_date_value, 3, 3)
        metadata_layout.addWidget(QLabel("RFQ Status:"), 4, 0)
        self.receive_quotes_status_value = QLabel("—")
        metadata_layout.addWidget(self.receive_quotes_status_value, 4, 1)
        metadata_layout.addWidget(QLabel("RFQ Date:"), 4, 2)
        self.receive_quotes_rfq_date_value = QLabel("—")
        metadata_layout.addWidget(self.receive_quotes_rfq_date_value, 4, 3)
        self.receive_quotes_context_label = QLabel(
            "Receive Quotes records vendor quote data only. Use Bid Compare to carry pricing."
        )
        self.receive_quotes_context_label.setWordWrap(True)
        self.receive_quotes_context_label.setStyleSheet("color: #1f4f99; font-weight: 600;")
        metadata_layout.addWidget(self.receive_quotes_context_label, 5, 0, 1, 4)
        layout.addWidget(metadata_group)

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
        self.quote_attach_button = QPushButton("📎 Attach Quote")
        self.quote_attach_button.clicked.connect(self.on_attach_file)
        lot_layout.addWidget(self.quote_attach_button, 1, 3)
        layout.addWidget(lot_group)

        matrix_group = QGroupBox("Vendor Pricing Matrix")
        matrix_layout = QVBoxLayout(matrix_group)
        self.matrix_table = QTableWidget(0, 7)
        self.matrix_table.setHorizontalHeaderLabels(
            ["ItemID", "MaterialID", "Qty", "Description", "DB Price", "Quote Price", "Quote Ext"]
        )
        self.matrix_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.matrix_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix_table.verticalHeader().setVisible(False)
        self.matrix_table.setColumnHidden(0, True)
        self.matrix_table.setColumnHidden(1, True)
        self.matrix_table.setColumnWidth(2, 50)
        self.matrix_table.setColumnWidth(3, 300)
        self.matrix_table.setColumnWidth(4, 80)
        self.matrix_table.setColumnWidth(5, 80)
        self.matrix_table.setColumnWidth(6, 80)
        self.matrix_table.cellDoubleClicked.connect(self.on_matrix_double_click)
        matrix_layout.addWidget(self.matrix_table)
        layout.addWidget(matrix_group, 1)

        action_row = QFrame()
        action_layout = QHBoxLayout(action_row)
        action_layout.addStretch(1)
        self.carry_btn = QPushButton("➡️ Carry Selected Items to Estimate")
        self.carry_btn.clicked.connect(self.on_carry_click)
        self.carry_btn.setText("Save Quote Response")
        self.carry_btn.setToolTip("Save vendor quote data only. Use Bid Compare to carry pricing.")
        action_layout.addWidget(self.carry_btn)
        self.lock_quote_btn = QPushButton("Lock Quote")
        self.lock_quote_btn.clicked.connect(self.on_lock_quote_click)
        action_layout.addWidget(self.lock_quote_btn)
        layout.addWidget(action_row)
        self._refresh_receive_quotes_edit_state()

    def _clear_receive_quotes_material_call_context(self) -> None:
        self.receive_quotes_material_call_id = None
        self.receive_quotes_material_call_number = ""
        self.receive_quotes_carry_warning_shown = False
        self.receive_quotes_current_status = ""
        self.receive_quotes_edit_locked = False
        self._set_receive_quotes_metadata_summary()
        if hasattr(self, "receive_quotes_context_label"):
            self.receive_quotes_context_label.setText(
                "Receive Quotes records vendor quote data only. Use Bid Compare to carry pricing."
            )
        if hasattr(self, "carry_btn"):
            self.carry_btn.setText("Save Quote Response")
            self.carry_btn.setToolTip("Save vendor quote data only. Use Bid Compare to carry pricing.")
        self._refresh_receive_quotes_edit_state()

    def _set_receive_quotes_metadata_summary(
        self,
        *,
        material_call_number: str = "—",
        rfq_number: str = "—",
        estimate_number: str = "—",
        work_order_number: str = "—",
        vendor_name: str = "—",
        customer_name: str = "—",
        site_name: str = "—",
        due_date_text: str = "—",
        status_text: str = "—",
        rfq_date_text: str = "—",
    ) -> None:
        if hasattr(self, "receive_quotes_material_call_value"):
            self.receive_quotes_material_call_value.setText(str(material_call_number or "—"))
        if hasattr(self, "receive_quotes_rfq_value"):
            self.receive_quotes_rfq_value.setText(str(rfq_number or "—"))
        if hasattr(self, "receive_quotes_estimate_value"):
            self.receive_quotes_estimate_value.setText(str(estimate_number or "—"))
        if hasattr(self, "receive_quotes_work_order_value"):
            self.receive_quotes_work_order_value.setText(str(work_order_number or "—"))
        if hasattr(self, "receive_quotes_vendor_value"):
            self.receive_quotes_vendor_value.setText(str(vendor_name or "—"))
        if hasattr(self, "receive_quotes_customer_value"):
            self.receive_quotes_customer_value.setText(str(customer_name or "—"))
        if hasattr(self, "receive_quotes_site_value"):
            self.receive_quotes_site_value.setText(str(site_name or "—"))
        if hasattr(self, "receive_quotes_due_date_value"):
            self.receive_quotes_due_date_value.setText(str(due_date_text or "—"))
        if hasattr(self, "receive_quotes_status_value"):
            self.receive_quotes_status_value.setText(str(status_text or "—"))
        if hasattr(self, "receive_quotes_rfq_date_value"):
            self.receive_quotes_rfq_date_value.setText(str(rfq_date_text or "—"))

    def _populate_receive_quotes_metadata_summary(
        self,
        *,
        header: dict | None = None,
        rfq_id: int | None = None,
        material_call_row: dict[str, Any] | None = None,
    ) -> None:
        header = dict(header or {})
        material_call_row = dict(material_call_row or self.active_material_call_row or {})
        work_order_id = None
        if isinstance(self.wo_resolution, dict):
            work_order_id = self.wo_resolution.get("open_work_order_id") or self.wo_resolution.get("latest_work_order_id")
        material_call_number = str(
            header.get("MaterialCallNumber")
            or material_call_row.get("MaterialCallNumber")
            or self.receive_quotes_material_call_number
            or "—"
        )
        rfq_display_id = rfq_id or header.get("PriceRequestID")
        estimate_number = (
            f"Estimate #{header.get('EstimateID')}"
            if header.get("EstimateID")
            else (
                f"Estimate #{material_call_row.get('EstimateID')}"
                if material_call_row.get("EstimateID")
                else "—"
            )
        )
        header_work_order_id = header.get("MaterialRequestWorkOrderID")
        work_order_number = (
            f"WO #{header_work_order_id}"
            if header_work_order_id
            else (f"WO #{work_order_id}" if work_order_id else "—")
        )
        status_text = str(header.get("Status") or "—")
        date_sent = str(header.get("DateSent") or "").strip()
        if date_sent:
            rfq_date_text = date_sent
        else:
            normalized_status = status_text.strip().lower()
            rfq_date_text = "Draft / Not Sent" if normalized_status in {"", "draft"} else "Not Sent"
        self._set_receive_quotes_metadata_summary(
            material_call_number=material_call_number,
            rfq_number=f"RFQ #{rfq_display_id}" if rfq_display_id else "—",
            estimate_number=estimate_number,
            work_order_number=work_order_number,
            vendor_name=str(header.get("VendorName") or "—"),
            customer_name=str(header.get("CustomerName") or material_call_row.get("CustomerName") or "—"),
            site_name=str(header.get("SiteName") or material_call_row.get("SiteName") or "—"),
            due_date_text=str(header.get("DueDate") or "—"),
            status_text=status_text,
            rfq_date_text=rfq_date_text,
        )

    def _apply_receive_quotes_header_context(self, header: dict | None) -> None:
        self._clear_receive_quotes_material_call_context()
        if not header:
            return
        self.receive_quotes_current_status = str(header.get("Status") or "").strip()
        self.receive_quotes_edit_locked = self._receive_quotes_status_is_edit_locked(self.receive_quotes_current_status)
        self._populate_receive_quotes_metadata_summary(
            header=header,
            rfq_id=int(self.active_rfq_id or self._receive_quotes_loaded_rfq_id or 0) or None,
        )
        material_call_id = header.get("MaterialCallID")
        if material_call_id in (None, "", 0, "0"):
            return
        self.receive_quotes_material_call_id = int(material_call_id)
        self.receive_quotes_material_call_number = str(
            header.get("MaterialCallNumber") or f"MC#{self.receive_quotes_material_call_id}"
        ).strip()
        if hasattr(self, "receive_quotes_context_label"):
            self.receive_quotes_context_label.setText(
                f"Material Call: {self.receive_quotes_material_call_number}. "
                "Receive Quotes saves vendor pricing for this RFQ only. "
                "Use Bid Compare to choose carried pricing and update estimate pricing."
            )
        if hasattr(self, "carry_btn"):
            self.carry_btn.setText("Save Quote Response")
            self.carry_btn.setToolTip(
                "Material Call RFQs save vendor pricing here. Carried selection and estimate price updates are managed in Bid Compare."
            )
        self._refresh_receive_quotes_edit_state()

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

        workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        workspace_splitter.setChildrenCollapsible(False)
        layout.addWidget(workspace_splitter, 1)

        source_group = QGroupBox("Basket: Carried Items Available")
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

        self.po_carried_status_label = QLabel("Select a Work Order from the PO pipeline first.")
        self.po_carried_status_label.setWordWrap(True)
        self.po_carried_status_label.setStyleSheet("color: #6c757d;")
        source_layout.addWidget(self.po_carried_status_label)

        self.po_carried_source_table = QTableWidget(0, 19)
        self.po_carried_source_table.setHorizontalHeaderLabels(
            [
                "CarryID",
                "PRItemID",
                "VendorID",
                "EstimateID",
                "MaterialID",
                "MaterialCallID",
                "MaterialCallItemID",
                "RFQ",
                "Estimate",
                "Work Order",
                "Material Call",
                "Vendor",
                "Part #",
                "Description",
                "Qty",
                "Unit",
                "Carried Unit Price",
                "Carried Ext Price",
                "Source",
            ]
        )
        self.po_carried_source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.po_carried_source_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.po_carried_source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.po_carried_source_table.verticalHeader().setVisible(False)
        for hidden_col in (0, 1, 2, 3, 4, 5, 6, 8, 9, 18):
            self.po_carried_source_table.setColumnHidden(hidden_col, True)
        self.po_carried_source_table.setColumnWidth(7, 70)
        self.po_carried_source_table.setColumnWidth(10, 110)
        self.po_carried_source_table.setColumnWidth(11, 150)
        self.po_carried_source_table.setColumnWidth(12, 130)
        self.po_carried_source_table.setColumnWidth(13, 280)
        self.po_carried_source_table.setColumnWidth(14, 75)
        self.po_carried_source_table.setColumnWidth(15, 65)
        self.po_carried_source_table.setColumnWidth(16, 110)
        self.po_carried_source_table.setColumnWidth(17, 120)
        source_layout.addWidget(self.po_carried_source_table, 1)

        source_action_row = QFrame()
        source_action_layout = QHBoxLayout(source_action_row)
        source_action_layout.setContentsMargins(0, 0, 0, 0)
        self.po_add_vendor_package_button = QPushButton("Add Filtered Vendor Package")
        self.po_add_vendor_package_button.clicked.connect(self.add_filtered_vendor_package_to_po_draft)
        source_action_layout.addWidget(self.po_add_vendor_package_button)
        self.po_add_selected_button = QPushButton("Add Selected to PO Draft")
        self.po_add_selected_button.clicked.connect(self.add_selected_carried_items_to_po_draft)
        source_action_layout.addWidget(self.po_add_selected_button)
        source_action_layout.addStretch(1)
        source_layout.addWidget(source_action_row)
        workspace_splitter.addWidget(source_group)

        draft_group = QGroupBox("Checkout: Current PO Draft")
        draft_layout = QVBoxLayout(draft_group)
        self.po_checkout_status_label = QLabel(
            "Add carried rows from the basket to assemble the current draft. Save PO Draft persists draft data only."
        )
        self.po_checkout_status_label.setWordWrap(True)
        self.po_checkout_status_label.setStyleSheet("color: #6c757d;")
        draft_layout.addWidget(self.po_checkout_status_label)
        self.po_item_table = QTableWidget(0, 14)
        self.po_item_table.setHorizontalHeaderLabels(
            [
                "MaterialID",
                "Description",
                "Available Qty",
                "Order Qty",
                "Carried Unit Price",
                "CatalogItemID",
                "RFQCarryID",
                "RFQID",
                "PRItemID",
                "VendorID",
                "EstimateID",
                "Part #",
                "MaterialCallID",
                "MaterialCallItemID",
            ]
        )
        self.po_item_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.po_item_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.po_item_table.verticalHeader().setVisible(False)
        for hidden_col in (0, 5, 6, 7, 8, 9, 10, 12, 13):
            self.po_item_table.setColumnHidden(hidden_col, True)
        self.po_item_table.setColumnWidth(11, 130)
        self.po_item_table.setColumnWidth(1, 320)
        self.po_item_table.setColumnWidth(2, 90)
        self.po_item_table.setColumnWidth(3, 90)
        self.po_item_table.setColumnWidth(4, 110)
        self.po_item_table.cellDoubleClicked.connect(self.on_po_item_double_click)
        draft_layout.addWidget(self.po_item_table, 1)
        workspace_splitter.addWidget(draft_group)
        workspace_splitter.setStretchFactor(0, 1)
        workspace_splitter.setStretchFactor(1, 1)
        workspace_splitter.setSizes([620, 560])

        button_row = QFrame()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        self.po_remove_selected_button = QPushButton("Remove Selected from PO Draft")
        self.po_remove_selected_button.clicked.connect(self.remove_selected_from_po_draft)
        button_layout.addWidget(self.po_remove_selected_button)
        button_layout.addStretch(1)
        self.btn_save_po_draft = QPushButton("Save PO Draft")
        self.btn_save_po_draft.clicked.connect(self.save_build_po_draft)
        self.btn_save_po_draft.setEnabled(False)
        button_layout.addWidget(self.btn_save_po_draft)
        self.btn_create_po = QPushButton("Create & Lock PO")
        self.btn_create_po.clicked.connect(self.on_create_po)
        self.btn_create_po.setEnabled(False)
        self.btn_create_po.setVisible(False)
        self.btn_view_po = QPushButton("Preview PO")
        self.btn_view_po.clicked.connect(self.on_view_po)
        self.btn_view_po.setEnabled(False)
        self.btn_view_po.setVisible(False)
        self.btn_send_po = QPushButton("Send PO to Vendor")
        self.btn_send_po.clicked.connect(self.on_send_po)
        self.btn_send_po.setEnabled(False)
        self.btn_send_po.setVisible(False)
        self.btn_export_po = QPushButton("Generate DOCX to Folder")
        self.btn_export_po.clicked.connect(self.on_export_to_folder)
        self.btn_export_po.setEnabled(False)
        self.btn_export_po.setVisible(False)
        layout.addWidget(button_row)

    def build_create_po_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_create_po)

        metadata_group = QGroupBox("Selected Purchase Order Context")
        metadata_layout = QGridLayout(metadata_group)
        layout.addWidget(metadata_group)

        metadata_layout.addWidget(QLabel("Work Order #:"), 0, 0)
        self.create_po_work_order_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_work_order_value, 0, 1)

        metadata_layout.addWidget(QLabel("PO #:"), 0, 2)
        self.create_po_number_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_number_value, 0, 3)

        metadata_layout.addWidget(QLabel("Vendor:"), 1, 0)
        self.create_po_vendor_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_vendor_value, 1, 1)

        metadata_layout.addWidget(QLabel("PO Status:"), 1, 2)
        self.create_po_status_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_status_value, 1, 3)

        metadata_layout.addWidget(QLabel("Date Created:"), 2, 0)
        self.create_po_date_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_date_value, 2, 1)

        metadata_layout.addWidget(QLabel("Expected Arrival:"), 2, 2)
        self.create_po_eta_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_eta_value, 2, 3)

        metadata_layout.addWidget(QLabel("PurchaseOrderID:"), 3, 0)
        self.create_po_id_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_id_value, 3, 1)

        metadata_layout.addWidget(QLabel("Total Amount:"), 3, 2)
        self.create_po_total_value = QLabel("—")
        metadata_layout.addWidget(self.create_po_total_value, 3, 3)

        self.create_po_context_status_label = QLabel(
            "Select or save a Purchase Order before creating the PO document."
        )
        self.create_po_context_status_label.setWordWrap(True)
        self.create_po_context_status_label.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(self.create_po_context_status_label)

        template_group = QGroupBox("Document Template Controls")
        template_layout = QGridLayout(template_group)
        layout.addWidget(template_group)

        template_layout.addWidget(QLabel("Header Template:"), 0, 0)
        self.create_po_header_template_combo = QComboBox()
        self.create_po_header_template_combo.addItem("Select a Purchase Order first", None)
        self.create_po_header_template_combo.setEnabled(False)
        self.create_po_header_template_combo.currentIndexChanged.connect(self._on_create_po_template_selection_changed)
        template_layout.addWidget(self.create_po_header_template_combo, 0, 1)

        template_layout.addWidget(QLabel("Body Template:"), 0, 2)
        self.create_po_body_template_combo = QComboBox()
        self.create_po_body_template_combo.addItem("Select a Purchase Order first", None)
        self.create_po_body_template_combo.setEnabled(False)
        self.create_po_body_template_combo.currentIndexChanged.connect(self._on_create_po_template_selection_changed)
        template_layout.addWidget(self.create_po_body_template_combo, 0, 3)

        template_layout.addWidget(QLabel("Footer Template:"), 1, 0)
        self.create_po_footer_template_combo = QComboBox()
        self.create_po_footer_template_combo.addItem("Select a Purchase Order first", None)
        self.create_po_footer_template_combo.setEnabled(False)
        self.create_po_footer_template_combo.currentIndexChanged.connect(self._on_create_po_template_selection_changed)
        template_layout.addWidget(self.create_po_footer_template_combo, 1, 1)

        self.create_po_template_note = QLabel(
            "Select a Purchase Order child to load purchase-order templates from Document Control."
        )
        self.create_po_template_note.setWordWrap(True)
        self.create_po_template_note.setStyleSheet("color: #6c757d;")
        template_layout.addWidget(self.create_po_template_note, 1, 2, 1, 2)

        body_group = QGroupBox("PO Document Draft")
        body_layout = QVBoxLayout(body_group)
        layout.addWidget(body_group, 1)

        self.create_po_body_editor = QTextEdit()
        self.create_po_body_editor.setPlaceholderText(
            "Select a Purchase Order, choose templates, and click Re-render From Templates.\n\nManual edits in this workspace are tracked and Preview PO will use the current edited body."
        )
        self.create_po_body_editor.setEnabled(False)
        self.create_po_body_editor.textChanged.connect(self._on_create_po_body_text_changed)
        body_layout.addWidget(self.create_po_body_editor)

        action_row = QFrame()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self.create_po_rerender_button = QPushButton("Re-render From Templates")
        self.create_po_rerender_button.setEnabled(False)
        self.create_po_rerender_button.clicked.connect(self.on_create_po_rerender_from_templates)
        action_layout.addWidget(self.create_po_rerender_button)
        self.create_po_save_draft_button = QPushButton("Save PO Draft")
        self.create_po_save_draft_button.setEnabled(False)
        self.create_po_save_draft_button.setToolTip("Slice 5+ will decide whether this tab owns draft persistence.")
        action_layout.addWidget(self.create_po_save_draft_button)
        self.create_po_lock_button = QPushButton("Lock PO")
        self.create_po_lock_button.setEnabled(False)
        self.create_po_lock_button.setToolTip("Slice 6 will move lock behavior into Create Purchase Order.")
        self.create_po_lock_button.clicked.connect(self.on_create_po_lock)
        action_layout.addWidget(self.create_po_lock_button)
        self.create_po_preview_button = QPushButton("Preview PO")
        self.create_po_preview_button.setEnabled(False)
        self.create_po_preview_button.clicked.connect(self.on_preview_create_purchase_order)
        action_layout.addWidget(self.create_po_preview_button)
        self.create_po_save_locked_button = QPushButton("Save Locked PO to Company Folder")
        self.create_po_save_locked_button.setEnabled(False)
        self.create_po_save_locked_button.setToolTip("Slice 6 will move legal-document archive handling here.")
        self.create_po_save_locked_button.clicked.connect(self.on_create_po_save_locked_to_company_folder)
        action_layout.addWidget(self.create_po_save_locked_button)
        self.create_po_send_button = QPushButton("Send PO to Vendor")
        self.create_po_send_button.setEnabled(False)
        self.create_po_send_button.setToolTip("Send uses the existing guarded purchase-order delivery path.")
        self.create_po_send_button.clicked.connect(self.on_create_po_send)
        action_layout.addWidget(self.create_po_send_button)
        layout.addWidget(action_row)

        self.refresh_create_purchase_order_shell()

    def refresh_create_purchase_order_shell(self) -> None:
        work_order_text = self.active_work_order_number or (f"WO #{self.active_work_order_id}" if self.active_work_order_id else "—")
        self.create_po_work_order_value.setText(work_order_text or "—")

        po_row = dict(self.active_purchase_order_row or {})
        if not po_row and self.active_purchase_order_id:
            try:
                po_row = dict(get_po_export_data(int(self.active_purchase_order_id)) or {})
            except Exception:
                po_row = {}

        po_id = int(po_row.get("PurchaseOrderID") or self.active_purchase_order_id or 0) or None
        vendor_name = str(po_row.get("VendorName") or "—")
        status_text = str(po_row.get("Status") or "—")
        date_created = str(po_row.get("Date") or "—")
        eta_value = po_row.get("ExpectedArrivalDate")
        eta_text = eta_value.isoformat() if hasattr(eta_value, "isoformat") else str(eta_value or "—")
        total_raw = po_row.get("PurchaseOrderTotal")
        total_text = f"${float(total_raw or 0):,.2f}" if total_raw not in (None, "") else "—"

        self.create_po_number_value.setText(f"PO #{po_id}" if po_id else "—")
        self.create_po_vendor_value.setText(vendor_name or "—")
        self.create_po_status_value.setText(status_text or "—")
        self.create_po_date_value.setText(date_created or "—")
        self.create_po_eta_value.setText(eta_text or "—")
        self.create_po_id_value.setText(str(po_id) if po_id else "—")
        self.create_po_total_value.setText(total_text)

        has_selected_po = po_id is not None
        is_read_only_po = self._is_create_po_read_only_status(status_text)
        self._set_create_po_editor_interaction_state(
            has_selected_po=has_selected_po,
            read_only=is_read_only_po,
        )
        if has_selected_po:
            if self.procurement_center == "PO" and self._is_create_po_tab_active():
                self.load_create_po_template_options(force_reload=False)
            selected_body = str(self.create_po_body_editor.toPlainText() or "").strip()
            po_selection_changed = self._create_po_loaded_po_id not in (None, po_id)
            if po_selection_changed:
                had_existing_body = bool(selected_body)
                had_manual_draft = self.create_po_document_dirty and had_existing_body
                self._set_create_po_body_text("")
                self._create_po_loaded_po_id = po_id
                self.create_po_document_stale = True
                self.create_po_document_stale_reason = f"Selected PO changed to #{po_id}."

                auto_loaded = False
                selected_template_ids = self.get_selected_create_po_template_ids()
                has_template_selection = any(value is not None for value in selected_template_ids.values())
                if has_template_selection:
                    try:
                        render_result = self.render_create_po_document_from_selected_templates()
                    except Exception:
                        render_result = {"success": False}
                    if render_result.get("success"):
                        self._set_create_po_body_text(str(render_result.get("rendered_content") or ""))
                        auto_loaded = True

                if auto_loaded:
                    if had_manual_draft and is_read_only_po:
                        self.create_po_context_status_label.setText(
                            f"Selected PO changed to #{po_id}. The previous manual draft was cleared to avoid cross-PO carryover, and the new PO was loaded in read-only preview mode because status is {status_text or 'read-only'}."
                        )
                    elif had_manual_draft:
                        self.create_po_context_status_label.setText(
                            f"Selected PO changed to #{po_id}. The previous manual draft was cleared to avoid cross-PO carryover, and the new PO was loaded into the editable draft workspace."
                        )
                    elif is_read_only_po:
                        self.create_po_context_status_label.setText(
                            f"PO #{po_id} loaded in read-only preview mode because status is {status_text or 'read-only'}."
                        )
                    else:
                        self.create_po_context_status_label.setText(
                            f"PO #{po_id} loaded into the editable draft workspace for review and preview."
                        )
                elif had_manual_draft:
                    self.create_po_context_status_label.setText(
                        f"Selected PO changed to #{po_id}. The previous manual draft was cleared to avoid cross-PO carryover. Click Re-render From Templates to load the new PO."
                    )
                else:
                    self.create_po_context_status_label.setText(
                        f"PO #{po_id} selected. Click Re-render From Templates to load the current PO draft."
                    )
            elif is_read_only_po:
                self.create_po_context_status_label.setText(
                    f"PO #{po_id} is {status_text or 'read-only'}. Editing is disabled; preview remains available and archive/send actions follow the current guarded workflow."
                )
            elif self.create_po_document_dirty:
                self.create_po_context_status_label.setText(
                    "Manual edits detected. Preview PO will use the current edited body."
                )
            elif self.create_po_document_stale:
                stale_reason = str(self.create_po_document_stale_reason or "PO document content is stale.")
                self.create_po_context_status_label.setText(
                    f"{stale_reason} Click Re-render From Templates to refresh the editable document body."
                )
            else:
                self.create_po_context_status_label.setText(
                    f"PO #{po_id} ready for template rerender and preview."
                )
        else:
            self.create_po_context_status_label.setText(
                "Select or save a Purchase Order before creating the PO document."
            )
            self._set_create_po_body_text("")
            self._create_po_loaded_po_id = None
            self.reset_create_po_template_options("Select a Purchase Order first")
        self._update_create_po_action_states()

    def _is_create_po_read_only_status(self, status_value: Any) -> bool:
        normalized = str(status_value or "").strip().lower()
        if not normalized:
            return False
        if normalized in {"locked", "sent"}:
            return True
        return "locked" in normalized

    def _set_create_po_editor_interaction_state(self, *, has_selected_po: bool, read_only: bool) -> None:
        self.create_po_body_editor.setEnabled(has_selected_po)
        self.create_po_body_editor.setReadOnly((not has_selected_po) or read_only)

    def _is_create_po_draft_status(self, status_value: Any) -> bool:
        normalized = str(status_value or "").strip().lower()
        return normalized == "draft"

    def _can_lock_create_po(self) -> bool:
        if self.active_purchase_order_id is None:
            return False
        if not self._is_create_po_draft_status(self.create_po_status_value.text()):
            return False
        return bool(str(self.create_po_body_editor.toPlainText() or "").strip())

    def _can_archive_create_po(self) -> bool:
        if self.active_purchase_order_id is None:
            return False
        status_text = str(self.create_po_status_value.text() or "").strip().lower()
        return status_text in {"locked", "sent"}

    def _build_current_po_lock_items(self, po_id: int) -> list[dict[str, Any]]:
        edit_rows = list(get_purchase_order_edit_items(int(po_id)) or [])
        source_links = {
            int(row["POItemID"]): row
            for row in get_purchase_order_source_links(int(po_id))
            if row.get("POItemID") is not None
        }
        items: list[dict[str, Any]] = []
        for row in edit_rows:
            po_item_id = int(row.get("POItemID") or 0)
            source_link = dict(source_links.get(po_item_id) or {})
            items.append(
                {
                    "mat_id": row.get("MaterialID"),
                    "desc": str(row.get("Description") or ""),
                    "qty": float(row.get("QuantityOrdered") or 0),
                    "price": float(row.get("UnitPriceAtOrder") or 0),
                    "catalog_item_id": row.get("CatalogItemID") or "",
                    "rfq_carried_selection_id": source_link.get("RFQCarriedSelectionID") or "",
                    "source_price_request_id": source_link.get("PriceRequestID") or "",
                    "source_pr_item_id": source_link.get("PRItemID") or "",
                    "source_vendor_id": source_link.get("VendorID") or "",
                    "source_estimate_id": source_link.get("EstimateID") or "",
                    "part_number": str(row.get("PartNumber") or ""),
                    "source_material_call_id": source_link.get("MaterialCallID") or "",
                    "source_material_call_item_id": source_link.get("MaterialCallItemID") or "",
                }
            )
        return items

    def _format_receiving_meta_value(self, value: Any, default: str = "—") -> str:
        if value in (None, ""):
            return default
        if hasattr(value, "isoformat"):
            try:
                return str(value.isoformat())
            except Exception:
                return str(value)
        return str(value)

    def refresh_receiving_metadata_panel(self) -> None:
        po_id = int(self.active_purchase_order_id or 0) or None
        work_order_text = self.active_work_order_number or (f"WO #{self.active_work_order_id}" if self.active_work_order_id else "—")
        self.receiving_work_order_value.setText(work_order_text or "—")

        if not po_id:
            self.receiving_po_value.setText("—")
            self.receiving_vendor_value.setText("—")
            self.receiving_status_value.setText("—")
            self.receiving_ordered_date_value.setText("—")
            self.receiving_eta_value.setText("—")
            self.receiving_last_received_value.setText("—")
            self.receiving_last_packing_slip_value.setText("—")
            self.receiving_total_lines_value.setText("—")
            self.receiving_summary_value.setText("—")
            self.receiving_context_status_label.setText(
                "Select a Purchase Order before receiving goods."
            )
            self.receiving_table.setRowCount(0)
            self.reset_receiving_session()
            self._receiving_loaded_po_id = None
            return

        try:
            metadata = dict(get_purchase_order_receiving_metadata(po_id) or {})
        except Exception:
            metadata = {}

        if self._receiving_loaded_po_id != po_id:
            self.reset_receiving_session()
            try:
                receiving_rows = list(get_po_items_with_receiving(po_id) or [])
            except Exception:
                receiving_rows = []
            self._populate_receiving_table_rows(receiving_rows)
            self._receiving_loaded_po_id = po_id

        status_text = self._format_receiving_meta_value(metadata.get("Status"))
        ordered_date = self._format_receiving_meta_value(metadata.get("Date"))
        eta_text = self._format_receiving_meta_value(metadata.get("ExpectedArrivalDate"))
        last_received = self._format_receiving_meta_value(metadata.get("LastReceivedDate"), default="Not received yet")
        last_packing_slip = self._format_receiving_meta_value(metadata.get("LastPackingSlip"), default="None recorded")
        total_lines = int(metadata.get("TotalOrderedLines") or 0)
        total_received_qty = float(metadata.get("TotalReceivedQty") or 0)
        total_outstanding_qty = float(metadata.get("TotalOutstandingQty") or 0)

        self.receiving_po_value.setText(f"PO #{po_id}")
        self.receiving_vendor_value.setText(self._format_receiving_meta_value(metadata.get("VendorName")))
        self.receiving_status_value.setText(status_text)
        self.receiving_ordered_date_value.setText(ordered_date)
        self.receiving_eta_value.setText(eta_text)
        self.receiving_last_received_value.setText(last_received)
        self.receiving_last_packing_slip_value.setText(last_packing_slip)
        self.receiving_total_lines_value.setText(str(total_lines))
        self.receiving_summary_value.setText(
            f"{total_received_qty:,.2f} received / {total_outstanding_qty:,.2f} outstanding"
        )
        self.receiving_context_status_label.setText(
            f"Receiving against PO #{po_id}. Enter a packing slip and start a receiving session to capture arriving quantities."
        )

    def _populate_receiving_table_rows(self, rows: list[dict[str, Any]]) -> None:
        self.receiving_table.setRowCount(0)
        for row_data in rows:
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

    def _resolve_create_po_document_type_code(
        self,
        catalog_service: DocumentCatalogService,
        *,
        force_reload: bool = False,
    ) -> str | None:
        if self._create_po_template_document_type_code is not None and not force_reload:
            return self._create_po_template_document_type_code

        candidate_codes: set[str] = set()
        try:
            for summary in catalog_service.list_templates():
                code = str(getattr(summary, "document_type_code", "") or "").strip()
                if code:
                    candidate_codes.add(code)
        except Exception:
            pass

        try:
            for doc_type in catalog_service.list_document_types():
                code = str(getattr(doc_type, "document_type_code", "") or "").strip()
                if code:
                    candidate_codes.add(code)
        except Exception:
            pass

        def _score(code: str) -> tuple[int, int, str]:
            normalized = str(code or "").strip()
            upper_code = normalized.upper()
            if upper_code in {candidate.upper() for candidate in self.PO_DOCUMENT_TYPE_CODE_CANDIDATES}:
                return (0, len(normalized), upper_code)
            if "PURCHASE" in upper_code and "ORDER" in upper_code:
                return (1, len(normalized), upper_code)
            if upper_code.startswith("PO") or "_PO_" in upper_code or upper_code.endswith("_PO"):
                return (2, len(normalized), upper_code)
            return (99, len(normalized), upper_code)

        ranked_codes = [code for code in candidate_codes if _score(code)[0] < 99]
        ranked_codes.sort(key=_score)
        self._create_po_template_document_type_code = ranked_codes[0] if ranked_codes else None
        return self._create_po_template_document_type_code

    def reset_create_po_template_options(self, empty_label: str) -> None:
        for combo in (
            self.create_po_header_template_combo,
            self.create_po_body_template_combo,
            self.create_po_footer_template_combo,
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(empty_label, None)
            combo.setEnabled(False)
            combo.blockSignals(False)

        self._create_po_template_choices_by_kind = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        self._create_po_template_defaults_by_kind = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }
        self._create_po_template_cache_loaded = False
        self._create_po_template_document_type_code = None
        self._update_create_po_action_states()

    def load_create_po_template_options(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        has_selected_po = self.active_purchase_order_id is not None
        if not has_selected_po:
            self.reset_create_po_template_options("Select a Purchase Order first")
            self.create_po_template_note.setText(
                "Select a Purchase Order child to load purchase-order templates from Document Control."
            )
            _perf_log(
                "ui",
                "create_po.template_dropdown_reload",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                skipped=True,
                reason="no_active_purchase_order",
            )
            return

        cache_hit = self._create_po_template_cache_loaded and not force_reload
        if not cache_hit:
            service_load_started_at = time.perf_counter()
            service_resolve_started_at = time.perf_counter()
            catalog_service = None
            template_document_type_code = None
            try:
                catalog_service = self._get_document_catalog_service()
                _perf_log(
                    "ui",
                    "create_po.template_service_resolve",
                    service_resolve_started_at,
                    center=self.procurement_center,
                    active_tab=self._active_procurement_tab_name(),
                    cache_hit=cache_hit,
                    skipped=False,
                )
                template_document_type_code = self._resolve_create_po_document_type_code(
                    catalog_service,
                    force_reload=force_reload,
                )
                template_list_started_at = time.perf_counter()
                summaries = (
                    catalog_service.list_current_templates(document_type_code=template_document_type_code)
                    if template_document_type_code
                    else []
                )
                _perf_log(
                    "ui",
                    "create_po.template_list_query",
                    template_list_started_at,
                    center=self.procurement_center,
                    active_tab=self._active_procurement_tab_name(),
                    row_count=len(summaries),
                    cache_hit=cache_hit,
                    skipped=False,
                    reason=(template_document_type_code or "no_po_document_type"),
                )
            except Exception:
                summaries = []
                catalog_service = None
                template_document_type_code = None
                _perf_log(
                    "ui",
                    "create_po.template_service_resolve",
                    service_resolve_started_at,
                    center=self.procurement_center,
                    active_tab=self._active_procurement_tab_name(),
                    cache_hit=cache_hit,
                    skipped=False,
                    reason="service_load_failed",
                )

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

            if catalog_service is not None and template_document_type_code:
                defaults_started_at = time.perf_counter()
                try:
                    default_mappings = catalog_service.list_template_defaults(
                        document_type_code=template_document_type_code,
                        usage_context=self.PO_DRAFT_USAGE_CONTEXT,
                    )
                    _perf_log(
                        "ui",
                        "create_po.template_defaults_query",
                        defaults_started_at,
                        center=self.procurement_center,
                        active_tab=self._active_procurement_tab_name(),
                        row_count=len(default_mappings),
                        cache_hit=cache_hit,
                        skipped=False,
                    )
                except Exception:
                    default_mappings = []
                    _perf_log(
                        "ui",
                        "create_po.template_defaults_query",
                        defaults_started_at,
                        center=self.procurement_center,
                        active_tab=self._active_procurement_tab_name(),
                        row_count=0,
                        cache_hit=cache_hit,
                        skipped=False,
                        reason="defaults_load_failed",
                    )
                for default_mapping in default_mappings:
                    try:
                        kind_value = (
                            default_mapping.template_kind.value
                            if hasattr(default_mapping.template_kind, "value")
                            else str(default_mapping.template_kind or "").strip().lower()
                        )
                    except Exception:
                        kind_value = str(getattr(default_mapping, "template_kind", "") or "").strip().lower()
                    if kind_value in defaults_by_kind and defaults_by_kind[kind_value] is None:
                        try:
                            defaults_by_kind[kind_value] = int(default_mapping.template_id)
                        except Exception:
                            defaults_by_kind[kind_value] = None

            _perf_log(
                "ui",
                "create_po.template_service_load",
                service_load_started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                row_count=len(summaries),
                cache_hit=cache_hit,
                skipped=False,
                reason=(template_document_type_code or "no_po_document_type"),
            )

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
                        0
                        if int(getattr(summary, "template_id", 0) or 0)
                        == (defaults_by_kind.get(kind_value) or -1)
                        else 1,
                        str(getattr(summary, "template_name", "") or "").lower(),
                        int(getattr(summary, "template_id", 0) or 0),
                    )
                )

            self._create_po_template_choices_by_kind = choices_by_kind
            self._create_po_template_defaults_by_kind = defaults_by_kind
            self._create_po_template_cache_loaded = True
        else:
            _perf_log(
                "ui",
                "create_po.template_service_load",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                cache_hit=cache_hit,
                skipped=True,
                reason="cached_template_choices",
            )

        populate_started_at = time.perf_counter()
        self.populate_create_po_template_combo(
            self.create_po_header_template_combo,
            DocumentTemplateKind.HEADER,
            "No active PO header templates",
        )
        self.populate_create_po_template_combo(
            self.create_po_body_template_combo,
            DocumentTemplateKind.BODY,
            "No active PO body templates",
        )
        self.populate_create_po_template_combo(
            self.create_po_footer_template_combo,
            DocumentTemplateKind.FOOTER,
            "No active PO footer templates",
        )
        _perf_log(
            "ui",
            "create_po.template_dropdown_populate",
            populate_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            cache_hit=cache_hit,
            skipped=False,
        )

        row_count = sum(
            len(self._create_po_template_choices_by_kind.get(kind) or [])
            for kind in self._create_po_template_choices_by_kind
        )
        document_type_code = self._create_po_template_document_type_code
        if row_count:
            self.create_po_template_note.setText(
                f"Purchase-order templates loaded from Document Control ({document_type_code}). Re-render uses the selected header/body/footer templates."
            )
        elif document_type_code:
            self.create_po_template_note.setText(
                f"No active purchase-order templates were found for document type {document_type_code}. Create Purchase Order stays preview-only until templates are added."
            )
        else:
            self.create_po_template_note.setText(
                "No purchase-order document type was found in Document Control. Create Purchase Order stays preview-only until PO templates are added."
            )
        self._update_create_po_action_states()
        _perf_log(
            "ui",
            "create_po.template_dropdown_reload",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=row_count,
            cache_hit=cache_hit,
            skipped=False,
            reason=(document_type_code or "no_po_document_type"),
        )

    def populate_create_po_template_combo(
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

        choices = list(self._create_po_template_choices_by_kind.get(kind_value) or [])
        if not choices:
            combo.addItem(empty_label, None)
            combo.setEnabled(False)
            combo.blockSignals(False)
            return

        combo.setEnabled(True)
        for summary in choices:
            combo.addItem(
                str(getattr(summary, "template_name", "") or f"Template {summary.template_id}"),
                int(summary.template_id),
            )

        preferred_id = previous_template_id
        if preferred_id is None:
            preferred_id = self._create_po_template_defaults_by_kind.get(kind_value)
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

    def get_selected_create_po_template_ids(self) -> dict[str, int | None]:
        return {
            "header_template_id": (
                int(self.create_po_header_template_combo.currentData())
                if self.create_po_header_template_combo.currentData() not in (None, "")
                else None
            ),
            "body_template_id": (
                int(self.create_po_body_template_combo.currentData())
                if self.create_po_body_template_combo.currentData() not in (None, "")
                else None
            ),
            "footer_template_id": (
                int(self.create_po_footer_template_combo.currentData())
                if self.create_po_footer_template_combo.currentData() not in (None, "")
                else None
            ),
        }

    def _on_create_po_template_selection_changed(self) -> None:
        if not self.active_purchase_order_id:
            return
        self.create_po_document_stale = True
        self.create_po_document_stale_reason = "Template selection changed."
        if self.create_po_document_dirty:
            self.create_po_context_status_label.setText(
                "Template selection changed. Manual edits were preserved; Re-render From Templates will overwrite the current edited body."
            )
        else:
            self.create_po_context_status_label.setText(
                "PO template selection changed. Click Re-render From Templates to refresh the editable document body."
            )
        self._update_create_po_action_states()

    def _on_create_po_body_text_changed(self) -> None:
        if self._suspend_create_po_dirty_tracking:
            return
        if not self.active_purchase_order_id:
            return
        if self.create_po_body_editor.isReadOnly():
            return
        self.create_po_document_dirty = True
        self.create_po_document_stale = False
        self.create_po_document_stale_reason = ""
        self.create_po_document_is_html = self._rfq_body_looks_like_html(
            self.create_po_body_editor.toPlainText()
        )
        self.create_po_context_status_label.setText(
            "Manual edits detected. Preview PO will use the current edited body."
        )
        self._update_create_po_action_states()

    def _set_create_po_body_text(self, body_text: str) -> None:
        self._suspend_create_po_dirty_tracking = True
        try:
            self.create_po_body_editor.setPlainText(str(body_text or ""))
        finally:
            self._suspend_create_po_dirty_tracking = False
        self.create_po_document_dirty = False
        self.create_po_document_is_html = self._rfq_body_looks_like_html(str(body_text or ""))
        self.create_po_document_stale = False
        self.create_po_document_stale_reason = ""
        self._update_create_po_action_states()

    def _update_create_po_action_states(self) -> None:
        has_selected_po = self.active_purchase_order_id is not None
        is_read_only_po = self._is_create_po_read_only_status(self.create_po_status_value.text())
        selected_template_ids = self.get_selected_create_po_template_ids()
        has_template_selection = any(value is not None for value in selected_template_ids.values())
        has_body = bool(str(self.create_po_body_editor.toPlainText() or "").strip())
        can_rerender = has_selected_po and has_template_selection and not is_read_only_po
        self.create_po_rerender_button.setEnabled(can_rerender)
        self.create_po_preview_button.setEnabled(has_selected_po and has_body)
        self.create_po_save_draft_button.setEnabled(False)
        self.create_po_lock_button.setEnabled(self._can_lock_create_po())
        self.create_po_save_locked_button.setEnabled(self._can_archive_create_po())
        can_send = False
        if self._can_archive_create_po():
            try:
                can_send, _, _ = can_send_purchase_order(int(self.active_purchase_order_id or 0))
            except Exception:
                can_send = False
        self.create_po_send_button.setEnabled(can_send)
        if self._can_archive_create_po():
            self.create_po_save_locked_button.setToolTip(
                "Archive the official locked purchase-order document to the company/project folder using the current PO export generator."
            )
            if can_send:
                self.create_po_send_button.setToolTip(
                    "Send stays on the existing guarded PO delivery path and may archive the official document if needed."
                )
            else:
                self.create_po_send_button.setToolTip(
                    "Send is still guarded by the existing PO delivery prerequisites."
                )
        else:
            self.create_po_save_locked_button.setToolTip(
                "Save Locked PO to Company Folder becomes available after the selected PO is locked."
            )
            self.create_po_send_button.setToolTip(
                "Send PO to Vendor stays guarded and will enable only after the selected PO is locked and send prerequisites are satisfied."
            )
        self._set_create_po_editor_interaction_state(
            has_selected_po=has_selected_po,
            read_only=is_read_only_po,
        )

    def _format_create_po_currency(self, value: Any) -> str:
        try:
            return f"${float(value or 0):,.2f}"
        except Exception:
            return "$0.00"

    def _build_create_po_line_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in items:
            qty = float(item.get("QuantityOrdered") or 0)
            price = float(item.get("UnitPriceAtOrder") or 0)
            line_total = float(item.get("LineTotal") or (qty * price) or 0)
            rows.append(
                {
                    "LineType": "Material",
                    "Description": str(item.get("Description") or ""),
                    "PartNumber": str(item.get("PartNumber") or ""),
                    "MaterialDescription": str(item.get("Description") or ""),
                    "MaterialQuantity": qty,
                    "MaterialUnitCost": price,
                    "MaterialLineTotal": line_total,
                    "SellTotal": round(line_total, 2),
                    "SellTotalFormatted": self._format_create_po_currency(line_total),
                }
            )
        return rows

    def _rows_to_create_po_text(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        lines: list[str] = []
        for row in rows:
            description = str(row.get("Description") or row.get("MaterialDescription") or "").strip()
            qty = float(row.get("MaterialQuantity") or 0)
            price = float(row.get("MaterialUnitCost") or 0)
            total = float(row.get("MaterialLineTotal") or 0)
            lines.append(
                f"- {description} | Qty {qty:.2f} | Unit {self._format_create_po_currency(price)} | Ext {self._format_create_po_currency(total)}"
            )
        return "\n".join(lines)

    def _rows_to_create_po_html(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "<p>No line items.</p>"
        parts = ["<table>", "<thead><tr><th>Qty</th><th>Description</th><th>Unit Price</th><th>Ext Price</th></tr></thead>", "<tbody>"]
        for row in rows:
            description = escape(str(row.get("Description") or row.get("MaterialDescription") or "").strip())
            qty = float(row.get("MaterialQuantity") or 0)
            price = float(row.get("MaterialUnitCost") or 0)
            total = float(row.get("MaterialLineTotal") or 0)
            parts.append(
                "<tr>"
                f"<td>{qty:.2f}</td>"
                f"<td>{description}</td>"
                f"<td>{escape(self._format_create_po_currency(price))}</td>"
                f"<td>{escape(self._format_create_po_currency(total))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
        return "".join(parts)

    def _build_create_po_document_context(self, po_id: int) -> dict[str, Any]:
        return dict(get_purchase_order_document_tokens(po_id))

    def render_create_po_document_from_selected_templates(self) -> dict[str, Any]:
        po_id = int(self.active_purchase_order_id or 0)
        if po_id <= 0:
            return {"success": False, "error": "Select a Purchase Order first."}

        selected_template_ids = self.get_selected_create_po_template_ids()
        selected_templates: list[tuple[DocumentTemplateKind, int]] = []
        if selected_template_ids["header_template_id"] is not None:
            selected_templates.append((DocumentTemplateKind.HEADER, int(selected_template_ids["header_template_id"])))
        if selected_template_ids["body_template_id"] is not None:
            selected_templates.append((DocumentTemplateKind.BODY, int(selected_template_ids["body_template_id"])))
        if selected_template_ids["footer_template_id"] is not None:
            selected_templates.append((DocumentTemplateKind.FOOTER, int(selected_template_ids["footer_template_id"])))
        if not selected_templates:
            return {"success": False, "error": "No purchase-order templates are selected."}

        repository = DocumentControlRepository()
        render_service = DocumentRenderService(repository)
        resolved_versions = []
        rendered_templates = []
        resolved_document_type_code = self._create_po_template_document_type_code

        for expected_kind, template_id in selected_templates:
            version = repository.get_template_version(template_id=template_id)
            if version is None:
                return {"success": False, "error": f"Template #{template_id} could not be loaded."}
            if version.kind != expected_kind:
                return {
                    "success": False,
                    "error": f"Template #{template_id} is {version.kind.value}, not {expected_kind.value}.",
                }
            if resolved_document_type_code and str(version.document_type_code or "").strip() != str(resolved_document_type_code).strip():
                return {
                    "success": False,
                    "error": (
                        f"Template #{template_id} belongs to {version.document_type_code}, "
                        f"not {resolved_document_type_code}."
                    ),
                }
            if not resolved_document_type_code:
                resolved_document_type_code = str(version.document_type_code or "").strip() or None
            resolved_versions.append(version)
            rendered_templates.append(
                {
                    "template_id": int(version.template_id) if version.template_id is not None else None,
                    "template_version_id": (
                        int(version.template_version_id) if version.template_version_id is not None else None
                    ),
                    "template_name": str(version.template_name or ""),
                    "template_kind": version.kind.value,
                    "version_number": int(version.version_number or 0),
                }
            )

        if not resolved_document_type_code:
            return {
                "success": False,
                "error": "Purchase-order document type could not be resolved from the selected templates.",
            }

        tokens = self._build_create_po_document_context(po_id)
        rendered_text = render_service.render_text(
            document_type_code=resolved_document_type_code,
            context=tokens,
            templates=resolved_versions,
        )
        return {
            "success": True,
            "purchase_order_id": po_id,
            "document_type_code": resolved_document_type_code,
            "tokens": tokens,
            "rendered_content": rendered_text,
            "rendered_templates": rendered_templates,
            "template_ids": selected_template_ids,
            "Body": rendered_text,
            "BodyIsHtml": self._rfq_body_looks_like_html(rendered_text),
        }

    def on_create_po_rerender_from_templates(self) -> None:
        if not self.active_purchase_order_id:
            QMessageBox.information(
                self,
                "Create Purchase Order",
                "Select a Purchase Order first.",
            )
            return

        selected_template_ids = self.get_selected_create_po_template_ids()
        if not any(value is not None for value in selected_template_ids.values()):
            QMessageBox.information(
                self,
                "Re-render From Templates",
                "No active purchase-order templates are selected yet.",
            )
            return

        if self.create_po_document_dirty and self.create_po_body_editor.toPlainText().strip():
            confirm = QMessageBox.question(
                self,
                "Overwrite Manual Edits?",
                "Manual edits were detected in the Purchase Order draft body.\n\nRe-rendering from templates will overwrite the current edited body. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        started_at = time.perf_counter()
        try:
            render_result = self.render_create_po_document_from_selected_templates()
            if not render_result.get("success"):
                QMessageBox.warning(
                    self,
                    "Re-render From Templates",
                    str(render_result.get("error") or "Purchase-order rerender failed."),
                )
                return

            rendered_text = str(render_result.get("rendered_content") or "")
            self._set_create_po_body_text(rendered_text)
            self._create_po_loaded_po_id = int(self.active_purchase_order_id or 0) or None
            rendered_templates = list(render_result.get("rendered_templates") or [])
            template_summary = ", ".join(
                str(template.get("template_name") or f"Template {template.get('template_id')}")
                for template in rendered_templates
            ) or "selected templates"
            status_text = str(self.create_po_status_value.text() or "").strip()
            if self._is_create_po_read_only_status(status_text):
                self.create_po_context_status_label.setText(
                    f"PO #{self._create_po_loaded_po_id} preview body re-rendered from {template_summary}. Editing remains disabled because status is {status_text or 'read-only'}."
                )
            else:
                self.create_po_context_status_label.setText(
                    f"PO #{self._create_po_loaded_po_id} document body re-rendered from {template_summary}."
                )
        except Exception as exc:
            QMessageBox.critical(self, "Re-render From Templates", str(exc))
        finally:
            _perf_log(
                "ui",
                "create_po.rerender",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                purchase_order_id=self.active_purchase_order_id,
            )

    def on_create_po_lock(self) -> None:
        po_id = int(self.active_purchase_order_id or 0)
        if po_id <= 0:
            QMessageBox.information(self, "Lock PO", "Select a Purchase Order first.")
            return
        if not self._is_create_po_draft_status(self.create_po_status_value.text()):
            QMessageBox.information(
                self,
                "Lock PO",
                "Only Draft purchase orders can be locked from Create Purchase Order.",
            )
            return
        if not str(self.create_po_body_editor.toPlainText() or "").strip():
            QMessageBox.information(
                self,
                "Lock PO",
                "Re-render from templates or type a PO body before locking the purchase order.",
            )
            return

        try:
            header = dict(get_po_export_data(po_id) or {})
            if not header:
                raise ValueError(f"Purchase Order #{po_id} was not found.")
            work_order_id = int(header.get("WorkOrderID") or 0)
            vendor_id = int(header.get("VendorID") or 0)
            if work_order_id <= 0 or vendor_id <= 0:
                raise ValueError("Selected purchase order is missing work-order or vendor context.")
            items = self._build_current_po_lock_items(po_id)
            if not items:
                raise ValueError("Selected purchase order does not have any saved line items to lock.")

            locked_po_id = int(
                lock_purchase_order_record(
                    work_order_id=work_order_id,
                    items=items,
                    vendor_id=vendor_id,
                    po_id=po_id,
                    expected_date=header.get("ExpectedArrivalDate"),
                    expected_note=header.get("ExpectedArrivalNote"),
                )
            )
            self.active_po_id = locked_po_id
            self.active_purchase_order_id = locked_po_id
            self.refresh_po_pipeline()
            self.load_existing_purchase_order(locked_po_id, select_tab=False)
            self._create_po_loaded_po_id = locked_po_id
            self.create_po_context_status_label.setText(
                f"PO #{locked_po_id} was locked. The Create Purchase Order editor is now read-only. Save Locked PO to Company Folder archives the official legal document using the current PO export generator."
            )
            QMessageBox.information(
                self,
                "PO Locked",
                f"Purchase Order #{locked_po_id} was locked.\n\nThe current Create Purchase Order body remains visible for preview in this session, while the official archived file still uses the existing PO export generator.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Lock PO", str(exc))

    def on_create_po_save_locked_to_company_folder(self) -> None:
        po_id = int(self.active_purchase_order_id or 0)
        if po_id <= 0:
            QMessageBox.information(self, "Save Locked PO", "Select a Purchase Order first.")
            return
        if not self._can_archive_create_po():
            QMessageBox.information(
                self,
                "Save Locked PO",
                "Lock the selected purchase order before saving the official document to the company folder.",
            )
            return
        try:
            path = archive_purchase_order_docx(po_id)
            self.active_po_id = po_id
            self.refresh_po_pipeline()
            self.load_existing_purchase_order(po_id, select_tab=False)
            self._create_po_loaded_po_id = po_id
            self.create_po_context_status_label.setText(
                f"Locked PO #{po_id} was saved to the company folder at {path}. The archived legal document currently uses the existing PO export generator rather than the in-editor Create Purchase Order body."
            )
            QMessageBox.information(
                self,
                "PO Archived",
                f"Purchase Order #{po_id} was saved to:\n{path}\n\nNote: the official archived document currently uses the existing PO export data/generator path, not the in-editor Create Purchase Order body.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save Locked PO", str(exc))

    def on_create_po_send(self) -> None:
        po_id = int(self.active_purchase_order_id or 0)
        if po_id <= 0:
            QMessageBox.information(self, "Send PO", "Select a Purchase Order first.")
            return
        self.active_po_id = po_id
        self.on_send_po()
        try:
            self.refresh_po_pipeline()
            self.load_existing_purchase_order(po_id, select_tab=False)
            status_text = str(self.create_po_status_value.text() or "").strip()
            if status_text == "Sent":
                self.create_po_context_status_label.setText(
                    f"PO #{po_id} was sent through the existing guarded vendor send flow."
                )
        except Exception:
            pass

    def on_preview_create_purchase_order(self) -> None:
        if not self.active_purchase_order_id:
            QMessageBox.information(
                self,
                "Preview PO",
                "Select a Purchase Order first.",
            )
            return

        body_text = str(self.create_po_body_editor.toPlainText() or "")
        if not body_text.strip():
            QMessageBox.information(
                self,
                "Preview PO",
                "Re-render from templates or type a PO draft body first.",
            )
            return

        started_at = time.perf_counter()
        preview = {
            "PurchaseOrderID": self.active_purchase_order_id,
            "WorkOrderID": self.active_work_order_id,
            "VendorName": self.create_po_vendor_value.text(),
            "Body": body_text,
            "BodyIsHtml": self.create_po_document_is_html,
            "TemplateUsed": ", ".join(
                value
                for value in (
                    self.create_po_header_template_combo.currentText() if self.create_po_header_template_combo.isEnabled() else "",
                    self.create_po_body_template_combo.currentText() if self.create_po_body_template_combo.isEnabled() else "",
                    self.create_po_footer_template_combo.currentText() if self.create_po_footer_template_combo.isEnabled() else "",
                )
                if value and "Select a Purchase Order first" not in value and "No active PO" not in value
            ) or "Manual draft / no PO templates",
        }

        dialog = QDialog(self)
        dialog.setWindowTitle("Preview PO")
        dialog.resize(760, 620)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        warning = QLabel(
            "Preview only. Purchase order is not locked, archived, or sent in this workflow slice."
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

        self._add_rfq_body_preview_section(
            layout,
            preview=preview,
            source_title="PO Preview HTML Source",
        )

        template_label = QLabel(f"Templates used: {preview.get('TemplateUsed')}")
        template_label.setWordWrap(True)
        layout.addWidget(template_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()
        _perf_log(
            "ui",
            "create_po.preview",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            purchase_order_id=self.active_purchase_order_id,
        )

    def build_receiving_tab_ui(self) -> None:
        layout = QVBoxLayout(self.tab_receiving)
        title = QLabel("Receiving & Inventory Tracking")
        title.setStyleSheet("font-size: 12px; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)

        metadata_group = QGroupBox("Selected Purchase Order Context")
        metadata_layout = QGridLayout(metadata_group)
        metadata_layout.addWidget(QLabel("Work Order #:"), 0, 0)
        self.receiving_work_order_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_work_order_value, 0, 1)
        metadata_layout.addWidget(QLabel("PO #:"), 0, 2)
        self.receiving_po_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_po_value, 0, 3)
        metadata_layout.addWidget(QLabel("Vendor:"), 1, 0)
        self.receiving_vendor_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_vendor_value, 1, 1)
        metadata_layout.addWidget(QLabel("PO Status:"), 1, 2)
        self.receiving_status_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_status_value, 1, 3)
        metadata_layout.addWidget(QLabel("Ordered Date:"), 2, 0)
        self.receiving_ordered_date_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_ordered_date_value, 2, 1)
        metadata_layout.addWidget(QLabel("Expected Arrival:"), 2, 2)
        self.receiving_eta_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_eta_value, 2, 3)
        metadata_layout.addWidget(QLabel("Last Received:"), 3, 0)
        self.receiving_last_received_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_last_received_value, 3, 1)
        metadata_layout.addWidget(QLabel("Last Packing Slip:"), 3, 2)
        self.receiving_last_packing_slip_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_last_packing_slip_value, 3, 3)
        metadata_layout.addWidget(QLabel("Ordered Lines:"), 4, 0)
        self.receiving_total_lines_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_total_lines_value, 4, 1)
        metadata_layout.addWidget(QLabel("Received / Outstanding:"), 4, 2)
        self.receiving_summary_value = QLabel("—")
        metadata_layout.addWidget(self.receiving_summary_value, 4, 3)
        layout.addWidget(metadata_group)

        self.receiving_context_status_label = QLabel(
            "Select a Purchase Order before receiving goods."
        )
        self.receiving_context_status_label.setWordWrap(True)
        self.receiving_context_status_label.setStyleSheet("color: #8a5a00; font-weight: 600;")
        layout.addWidget(self.receiving_context_status_label)

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
        self.refresh_receiving_metadata_panel()

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
        summary_layout.addWidget(QLabel("Material Call:"), 1, 0)
        self.bid_compare_material_call_label = QLabel("—")
        summary_layout.addWidget(self.bid_compare_material_call_label, 1, 1)
        summary_layout.addWidget(QLabel("Status:"), 1, 2)
        self.bid_compare_status_label = QLabel("—")
        summary_layout.addWidget(self.bid_compare_status_label, 1, 3)
        summary_layout.addWidget(QLabel("Compared Vendors:"), 2, 0)
        self.bid_compare_vendor_summary_label = QLabel("Select an RFQ to compare vendor pricing.")
        self.bid_compare_vendor_summary_label.setWordWrap(True)
        summary_layout.addWidget(self.bid_compare_vendor_summary_label, 2, 1, 1, 5)
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
        self.bid_compare_save_supported = True
        self.bid_compare_save_message = ""
        self.bid_compare_rfq_label.setText("No RFQ selected")
        self.bid_compare_estimate_label.setText("—")
        self.bid_compare_work_order_label.setText("—")
        self.bid_compare_material_call_label.setText("—")
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

    def load_po_work_order_choices(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        current_value = self.po_work_order_combo.currentData() if hasattr(self, "po_work_order_combo") else None
        choices = []
        cache_hit = False
        try:
            choices, cache_hit = self._get_cached_po_work_orders(force_reload=force_reload)
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
        _perf_log(
            "ui",
            "po.work_order_dropdown_reload",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=len(choices),
            cache_hit=cache_hit,
            skipped=False,
        )

    def on_po_work_order_selected(self) -> None:
        if self.build_po_readonly:
            return
        work_order_id = self.po_work_order_combo.currentData()
        if not work_order_id:
            self.active_work_order_id = None
            self.active_work_order_number = ""
            self.active_purchase_order_id = None
            self.active_purchase_order_row = None
            self.active_vendor_id = None
            self.active_po_id = None
            self.wo_edit.setText("")
            self.wo_resolution = None
            self.current_vendor_id = None if self.po_item_table.rowCount() == 0 else self.current_vendor_id
            self.po_vendor_label.setText("")
            self.po_carried_source_rows = []
            self._populate_po_carried_source_filters([])
            self._populate_po_carried_source_table([])
            self.po_carried_status_label.setText("Select a work order to load carried material decisions.")
            self.refresh_po_state_for_current_selection()
            self.refresh_create_purchase_order_shell()
            self.refresh_receiving_metadata_panel()
            return
        self.active_work_order_id = int(work_order_id)
        self.active_work_order_number = f"WO #{int(work_order_id)}"
        self.active_purchase_order_id = None
        self.active_purchase_order_row = None
        self.active_vendor_id = None
        self.active_po_id = None
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
        self.refresh_create_purchase_order_shell()
        self.refresh_receiving_metadata_panel()

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
        work_order_id = self.active_work_order_id or self.po_work_order_combo.currentData()
        if not work_order_id:
            self.po_carried_source_rows = []
            self._populate_po_carried_source_filters([])
            self._populate_po_carried_source_table([])
            self.po_carried_status_label.setText("Select a Work Order from the PO pipeline first.")
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
                str(row_data.get("MaterialCallID") or ""),
                str(row_data.get("MaterialCallItemID") or ""),
                str(row_data.get("RFQID") or row_data.get("PriceRequestID") or ""),
                str(row_data.get("EstimateID") or ""),
                str(row_data.get("WorkOrderID") or ""),
                str(row_data.get("MaterialCallNumber") or "—"),
                str(row_data.get("VendorName") or ""),
                str(row_data.get("PartNumber") or ""),
                str(row_data.get("Description") or ""),
                f"{float(row_data.get('CarriedQuantity') or 0):.2f}",
                str(row_data.get("Unit") or "—"),
                f"${float(row_data.get('CarriedUnitPrice') or 0):.2f}",
                f"${float(row_data.get('CarriedExtendedPrice') or 0):.2f}",
                str(row_data.get("SourceLabel") or row_data.get("SourceType") or row_data.get("SelectionType") or ""),
            ]
            for col, value in enumerate(values):
                self._set_item(
                    self.po_carried_source_table,
                    row,
                    col,
                    value,
                    center=col in (7, 8, 9, 14, 15, 18),
                    align_right=col in (16, 17),
                )

    def remove_selected_from_po_draft(self) -> None:
        if self.build_po_readonly:
            return
        rows = sorted({item.row() for item in self.po_item_table.selectedItems()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Remove Rows", "Select one or more draft rows first.")
            return
        for row in rows:
            self.po_item_table.removeRow(row)
        if self.po_item_table.rowCount() == 0:
            self.current_vendor_id = None
            self.po_vendor_label.setText("")
        self.lbl_po_status.setText("Current PO draft changed. Click Save PO Draft to persist checkout changes.")
        self.lbl_po_status.setStyleSheet("font-weight: 700; color: #8a5a00;")
        self.po_checkout_status_label.setText(
            "Checkout changed. Save PO Draft persists the current draft data only."
        )
        self.refresh_po_state_for_current_selection()

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
                    "source_material_call_id": self.po_item_table.item(row, 12).text() if self.po_item_table.item(row, 12) else "",
                    "source_material_call_item_id": self.po_item_table.item(row, 13).text() if self.po_item_table.item(row, 13) else "",
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
        self.active_purchase_order_id = int(po_id)
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
            self.active_purchase_order_id = po_id
            self.po_checkout_status_label.setText(
                f"Draft PO #{po_id} saved. This workspace only saves checkout data; document lock/send remain in later workflow steps."
            )
            QMessageBox.information(self, "PO Draft Saved", f"Purchase Order #{po_id} draft saved from carried items.")
        except Exception as exc:
            QMessageBox.critical(self, "Save PO Draft Error", str(exc))

    def _build_po_source_key(
        self,
        *,
        carry_id: str = "",
        material_call_id: str = "",
        material_call_item_id: str = "",
        vendor_id: str = "",
        pr_item_id: str = "",
        material_id: str = "",
    ) -> tuple[str, str] | None:
        carry_id = str(carry_id or "").strip()
        material_call_id = str(material_call_id or "").strip()
        material_call_item_id = str(material_call_item_id or "").strip()
        vendor_id = str(vendor_id or "").strip()
        pr_item_id = str(pr_item_id or "").strip()
        material_id = str(material_id or "").strip()
        if carry_id:
            return ("carry", carry_id)
        if material_call_id and material_call_item_id and vendor_id:
            return ("mcitem", f"{material_call_id}:{material_call_item_id}:{vendor_id}")
        if pr_item_id:
            return ("pritem", pr_item_id)
        if material_id:
            return ("material", material_id)
        return None

    def _current_po_source_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for row in range(self.po_item_table.rowCount()):
            carry_id = str(self.po_item_table.item(row, 6).text() if self.po_item_table.item(row, 6) else "").strip()
            pr_item_id = str(self.po_item_table.item(row, 8).text() if self.po_item_table.item(row, 8) else "").strip()
            vendor_id = str(self.po_item_table.item(row, 9).text() if self.po_item_table.item(row, 9) else "").strip()
            material_id = str(self.po_item_table.item(row, 0).text() if self.po_item_table.item(row, 0) else "").strip()
            material_call_id = str(self.po_item_table.item(row, 12).text() if self.po_item_table.item(row, 12) else "").strip()
            material_call_item_id = str(self.po_item_table.item(row, 13).text() if self.po_item_table.item(row, 13) else "").strip()
            key = self._build_po_source_key(
                carry_id=carry_id,
                material_call_id=material_call_id,
                material_call_item_id=material_call_item_id,
                vendor_id=vendor_id,
                pr_item_id=pr_item_id,
                material_id=material_id,
            )
            if key:
                keys.add(key)
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
            str(source_row.get("MaterialCallID") or ""),
            str(source_row.get("MaterialCallItemID") or ""),
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
        work_order_id = self.active_work_order_id or self.po_work_order_combo.currentData()
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
                "Use New Material Call or finish this draft before adding another vendor package."
            )

        existing_keys = self._current_po_source_keys()
        added = 0
        skipped = 0
        for row_data in rows:
            carry_id = str(row_data.get("RFQCarriedSelectionID") or "").strip()
            pr_item_id = str(row_data.get("PRItemID") or "").strip()
            vendor_id = str(row_data.get("VendorID") or "").strip()
            material_id = str(row_data.get("MaterialID") or "").strip()
            material_call_id = str(row_data.get("MaterialCallID") or "").strip()
            material_call_item_id = str(row_data.get("MaterialCallItemID") or "").strip()
            key = self._build_po_source_key(
                carry_id=carry_id,
                material_call_id=material_call_id,
                material_call_item_id=material_call_item_id,
                vendor_id=vendor_id,
                pr_item_id=pr_item_id,
                material_id=material_id,
            )
            if not key:
                skipped += 1
                continue
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
        self.active_vendor_id = selected_vendor_id
        self.po_vendor_label.setText(selected_vendor_name)
        self.wo_edit.setText(str(work_order_id))
        self.wo_resolution = {
            "can_create_po": True,
            "open_work_order_id": int(work_order_id),
            "latest_work_order_id": int(work_order_id),
            "latest_status": "Open",
            "message": f"Work Order #{work_order_id} is open and ready for PO drafting.",
        }
        self.lbl_po_status.setText(
            f"Checkout updated from carried selections ({added} added, {skipped} skipped). Click Save PO Draft to persist."
        )
        self.lbl_po_status.setStyleSheet("font-weight: 700; color: #8a5a00;")
        self.po_checkout_status_label.setText(
            f"Vendor: {selected_vendor_name}. {added} row(s) added to checkout and {skipped} skipped as duplicates."
        )
        self.refresh_po_state_for_current_selection()

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

    def load_receive_quotes_for_active_selection(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        if not self._is_receive_quotes_tab_active():
            _perf_log(
                "ui",
                "receive_quotes.tab_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="inactive_tab",
            )
            return
        rfq_id = int(self.active_rfq_id or 0) or None
        if not rfq_id:
            self._receive_quotes_loaded_rfq_id = None
            self._load_receive_quotes_material_call_summary(self.active_material_call_row)
            self._refresh_receive_quotes_edit_state()
            _perf_log(
                "ui",
                "receive_quotes.tab_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                row_count=0,
                material_call_id=self.active_material_call_id,
                skipped=False,
            )
            return
        if not force_reload and self._receive_quotes_loaded_rfq_id == rfq_id:
            _perf_log(
                "ui",
                "receive_quotes.tab_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                row_count=self.matrix_table.rowCount(),
                rfq_id=rfq_id,
                skipped=True,
                reason="already_loaded",
            )
            return

        first_load_started_at = time.perf_counter()
        self.selected_quote_path = None
        self.quote_no_edit.setText("")
        self.quote_date_edit.setText("")
        self.attach_label.setText("No file attached")
        self.attach_label.setStyleSheet("color: gray;")
        self._clear_receive_quotes_material_call_context()
        self.matrix_table.setRowCount(0)
        row_count = 0
        try:
            header = get_rfq_header_data(rfq_id)
            if header:
                self._apply_receive_quotes_header_context(header)
                self.quote_no_edit.setText(str(header.get("VendorQuoteNumber") or ""))
                self.quote_date_edit.setText(str(header.get("VendorQuoteDate") or ""))
                self.due_date_edit.setText(str(header.get("DueDate") or ""))
                path = header.get("QuoteFilePath")
                if path and os.path.exists(path):
                    self.selected_quote_path = path
                    self.attach_label.setText(f"Attached {os.path.basename(path)}")
                    self.attach_label.setStyleSheet("color: green;")
            for rfq_item in get_rfq_items_for_matrix(rfq_id):
                db_price_value = rfq_item.get("DatabaseUnitPrice")
                db_price = float(db_price_value) if db_price_value not in (None, "") else None
                quote_unit = float(rfq_item.get("QuotedUnitPrice") or 0.0)
                carry_box = "[Ã¢Å“â€œ]" if rfq_item.get("IsCarried") else "[ ]"
                row_index = self.matrix_table.rowCount()
                self.matrix_table.insertRow(row_index)
                row_count += 1
                db_price_display = f"${db_price:.2f}" if db_price is not None else "—"
                values = [
                    str(rfq_item["PRItemID"]),
                    str(rfq_item["MaterialID"]),
                    str(rfq_item["Quantity"]),
                    str(rfq_item["Description"]),
                    db_price_display,
                    f"${quote_unit:.2f}",
                    f"${quote_unit * float(rfq_item['Quantity'] or 0.0):.2f}",
                ]
                for column, value in enumerate(values):
                    self._set_item(
                        self.matrix_table,
                        row_index,
                        column,
                        value,
                        center=column == 2,
                        align_right=column in (4, 5, 6),
                    )
                db_price_item = self.matrix_table.item(row_index, 4)
                if db_price_item is not None:
                    db_price_source = str(rfq_item.get("DatabasePriceSource") or "").strip()
                    catalog_item_id = rfq_item.get("CatalogItemID")
                    if db_price is None:
                        if catalog_item_id in (None, "", 0, "0"):
                            db_price_item.setToolTip(
                                "No catalogue price: this row is not linked to a Materials Catalogue item, so DB Price is blank."
                            )
                        else:
                            db_price_item.setToolTip(
                                "No catalogue price: the linked Materials Catalogue item has no InternalPrice, and no stored estimate fallback price is available."
                            )
                    else:
                        db_price_item.setToolTip(f"DB Price source: {db_price_source}.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load RFQ items: {exc}")
        self._refresh_receive_quotes_edit_state()
        _perf_log(
            "ui",
            "receive_quotes.first_load",
            first_load_started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            rfq_id=rfq_id,
            material_call_id=self.active_material_call_id,
            force_reload=force_reload,
            skipped=False,
        )
        self._receive_quotes_loaded_rfq_id = rfq_id
        _perf_log(
            "ui",
            "receive_quotes.tab_load",
            started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            rfq_id=rfq_id,
            material_call_id=self.active_material_call_id,
            row_count=row_count,
            skipped=False,
        )

    def load_bid_compare_for_active_selection(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        if not self._is_bid_compare_tab_active():
            _perf_log(
                "ui",
                "bid_compare.tab_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="inactive_tab",
            )
            return
        rfq_id = int(self.active_rfq_id or 0) or None
        if not rfq_id and self.active_material_call_rfq_rows:
            rfq_id = int(
                self.active_material_call_rfq_rows[0].get("PriceRequestID")
                or self.active_material_call_rfq_rows[0].get("RFQID")
                or 0
            ) or None
        if not rfq_id:
            self._bid_compare_loaded_rfq_id = None
            self.clear_bid_compare_workspace("No vendor RFQs exist under this Material Call yet.")
            _perf_log(
                "ui",
                "bid_compare.tab_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                row_count=0,
                material_call_id=self.active_material_call_id,
                skipped=False,
            )
            return
        if not force_reload and self._bid_compare_loaded_rfq_id == rfq_id:
            _perf_log(
                "ui",
                "bid_compare.tab_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                rfq_id=rfq_id,
                row_count=self.bid_compare_table.rowCount(),
                skipped=True,
                reason="already_loaded",
            )
            return
        first_load_started_at = time.perf_counter()
        self.load_bid_compare_for_rfq(rfq_id)
        self._bid_compare_loaded_rfq_id = rfq_id
        _perf_log(
            "ui",
            "bid_compare.first_load",
            first_load_started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            rfq_id=rfq_id,
            material_call_id=self.active_material_call_id,
            force_reload=force_reload,
            skipped=False,
        )
        _perf_log(
            "ui",
            "bid_compare.tab_load",
            started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            rfq_id=rfq_id,
            material_call_id=self.active_material_call_id,
            row_count=self.bid_compare_table.rowCount(),
            skipped=False,
        )

    def load_bid_compare_for_rfq(self, rfq_id: int) -> None:
        started_at = time.perf_counter()
        data_load_started_at = time.perf_counter()
        try:
            compare_data = get_bid_compare_data(rfq_id)
        except Exception as exc:
            self.clear_bid_compare_workspace(f"Bid Compare load failed: {exc}")
            _perf_log(
                "ui",
                "bid_compare.workspace_load",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                rfq_id=rfq_id,
                row_count=0,
                skipped=False,
            )
            return
        _perf_log(
            "ui",
            "bid_compare.data_load",
            data_load_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            rfq_id=rfq_id,
            row_count=len(compare_data.get("lines") or []),
            vendor_count=len(compare_data.get("vendors") or []),
            skipped=False,
        )

        ui_populate_started_at = time.perf_counter()
        header = compare_data.get("header") or {}
        vendor_rows = list(compare_data.get("vendors") or [])
        self.bid_compare_current_rfq_id = int(header.get("rfq_id") or rfq_id)
        self.bid_compare_vendor_rows = vendor_rows
        self.bid_compare_save_supported = bool(header.get("carried_save_supported", True))
        self.bid_compare_save_message = str(header.get("carried_save_message") or "")
        self.bid_compare_rfq_label.setText(str(header.get("rfq_id") or rfq_id))
        self.bid_compare_estimate_label.setText(str(header.get("estimate_id") or "—"))
        self.bid_compare_work_order_label.setText(str(header.get("work_order_id") or "—"))
        material_call_text = str(header.get("material_call_number") or "").strip()
        if not material_call_text and header.get("material_call_id") is not None:
            material_call_text = f"MC#{header.get('material_call_id')}"
        self.bid_compare_material_call_label.setText(material_call_text or "—")
        self.bid_compare_status_label.setText(str(header.get("status") or "—"))

        vendor_summary = []
        for vendor_row in vendor_rows:
            vendor_name = str(vendor_row.get("VendorName") or f"RFQ #{vendor_row.get('PriceRequestID')}")
            rfq_label = f"RFQ #{vendor_row.get('PriceRequestID')}"
            quote_label = str(vendor_row.get("VendorQuoteNumber") or "").strip() or "No quote #"
            vendor_summary.append(f"{vendor_name} ({rfq_label}, {quote_label})")
        summary_text = ", ".join(vendor_summary) if vendor_summary else "No related vendor RFQs found."
        if not self.bid_compare_save_supported and self.bid_compare_save_message:
            summary_text = f"{summary_text}\n\nSave disabled: {self.bid_compare_save_message}"
        self.bid_compare_vendor_summary_label.setText(summary_text)

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
        self.bid_compare_save_btn.setEnabled(has_lines and self.bid_compare_save_supported)
        self.bid_compare_refresh_btn.setEnabled(True)
        _perf_log(
            "ui",
            "bid_compare.ui_populate",
            ui_populate_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            rfq_id=rfq_id,
            row_count=len(compare_data.get("lines") or []),
            vendor_count=len(vendor_rows),
            skipped=False,
        )
        _perf_log(
            "ui",
            "bid_compare.workspace_load",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            rfq_id=rfq_id,
            row_count=len(compare_data.get("lines") or []),
            vendor_count=len(vendor_rows),
            skipped=False,
        )

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
                        "estimate_material_id": int(line["estimate_material_id"]) if line.get("estimate_material_id") is not None else None,
                        "material_call_item_id": int(line["material_call_item_id"]) if line.get("material_call_item_id") is not None else None,
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
        if not self.bid_compare_save_supported:
            QMessageBox.warning(
                self,
                "Bid Compare Save Unavailable",
                self.bid_compare_save_message
                or "Carried-selection save is not enabled for this Bid Compare context yet.",
            )
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
            if result.get("estimate_pricing_updated"):
                message = (
                    f"Saved {result.get('selection_count', 0)} carried selection(s) "
                    f"using {result.get('selection_type', 'LineItem')} mode. "
                    "Unlocked estimate pricing was updated from the carried selections."
                )
            else:
                message = (
                    f"Saved {result.get('selection_count', 0)} carried selection(s) "
                    f"using {result.get('selection_type', 'LineItem')} mode. "
                    f"Estimate pricing was not updated because the estimate status is {result.get('estimate_status', 'Unknown')}."
                )
            QMessageBox.information(self, "Carried Selections Saved", message)
        except Exception as exc:
            QMessageBox.critical(self, "Bid Compare Save Error", str(exc))

    def _set_procurement_tab(self, widget: QWidget, *, center: str | None = None) -> None:
        if center:
            self.set_procurement_center(center)
        self.notebook.setCurrentWidget(widget)

    def _on_procurement_tab_changed(self, _index: int) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        try:
            self._hydrate_active_rfq_tree_selection_context(force_reload=False)
            if self.procurement_center == "PO" and self._is_create_po_tab_active():
                self.refresh_create_purchase_order_shell()
        finally:
            _perf_log(
                "ui",
                "rfq_viewer.tab_switch",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
            )

    def set_procurement_center(self, center: str, *, hydrate_choices: bool = True) -> None:
        started_at = time.perf_counter()
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
            if hydrate_choices:
                self.load_po_work_order_choices()
                self._po_build_tab_loaded = True
        self.direct_action_type_combo.setEnabled(False)
        self.direct_action_type_combo.blockSignals(False)
        self.direct_source_type_combo.blockSignals(False)

        tab_visibility = {
            self.tab_direct_po: True,
            self.tab_build_rfq: is_rfq,
            self.tab_create_rfq: is_rfq,
            self.tab_matrix: is_rfq,
            self.tab_bid_compare: is_rfq,
            self.tab_build_po: not is_rfq,
            self.tab_create_po: not is_rfq,
            self.tab_receiving: not is_rfq,
            self.tab_document: (not is_rfq) and self._legacy_po_document_tab_visible_in_normal_runtime(),
        }
        tab_titles = {
            self.tab_direct_po: "1. Material Call" if is_rfq else "1. Build PO",
            self.tab_build_rfq: "2. Build MC from Estimate",
            self.tab_create_rfq: "3. Create RFQ",
            self.tab_matrix: "4. Receive Quotes",
            self.tab_bid_compare: "5. Bid Compare",
            self.tab_build_po: "2. Build PO from Carried Items",
            self.tab_create_po: "3. Create Purchase Order",
            self.tab_receiving: "4. Receive Goods",
            self.tab_document: "5. PO Document",
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

        if hydrate_choices:
            self.refresh_direct_po_choices()
            self._material_call_tab_loaded = True
        self.on_material_request_mode_changed()
        main_window = self.window()
        if main_window is not None and hasattr(main_window, "refresh_materials_subnav_selection"):
            main_window.refresh_materials_subnav_selection(center)
        _perf_log(
            "ui",
            "rfq_viewer.set_procurement_center",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
        )

    def ensure_material_call_tab_loaded(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        if not self._is_material_call_tab_active():
            _perf_log(
                "ui",
                "material_call.first_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="inactive_tab",
            )
            return
        if self._material_call_tab_loaded and not force_reload:
            _perf_log(
                "ui",
                "material_call.first_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="already_loaded",
            )
            return
        self.refresh_direct_po_choices(force_reload=force_reload)
        self.on_material_request_mode_changed()
        self._material_call_tab_loaded = True
        _perf_log(
            "ui",
            "material_call.first_load",
            started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            force_reload=force_reload,
            skipped=False,
        )

    def ensure_build_mc_tab_loaded(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        if not self._is_build_mc_tab_active():
            _perf_log(
                "ui",
                "build_mc.first_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="inactive_tab",
            )
            return
        if self._build_mc_tab_loaded and not force_reload:
            _perf_log(
                "ui",
                "build_mc.first_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="already_loaded",
            )
            return
        if self.active_material_call_row and self.active_material_call_id:
            self._load_build_rfq_from_material_call(self.active_material_call_row)
        else:
            self.refresh_build_mc_source_choices(force_reload=force_reload)
            if self.est_combo.currentData():
                self._load_build_mc_source_materials()
        self._build_mc_tab_loaded = True
        _perf_log(
            "ui",
            "build_mc.first_load",
            started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            force_reload=force_reload,
            skipped=False,
        )

    def ensure_po_build_tab_loaded(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        if active_tab != "build_po":
            _perf_log(
                "ui",
                "po.first_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="inactive_tab",
            )
            return
        if self._po_build_tab_loaded and not force_reload:
            _perf_log(
                "ui",
                "po.first_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="already_loaded",
            )
            return
        self.load_po_work_order_choices(force_reload=force_reload)
        self._po_build_tab_loaded = True
        _perf_log(
            "ui",
            "po.first_load",
            started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            force_reload=force_reload,
            skipped=False,
        )

    def ensure_create_rfq_tab_loaded(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        is_active = self._is_create_rfq_tab_active()
        if not is_active:
            _perf_log(
                "ui",
                "create_rfq.tab_refresh",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="inactive_tab",
            )
            return
        first_load_started_at = time.perf_counter()
        self._load_create_rfq_vendor_choices(force_reload=force_reload)
        self.load_rfq_template_options(force_reload=force_reload)
        self._refresh_create_rfq_context_panel()
        first_load = not self._create_rfq_tab_loaded
        self._create_rfq_tab_loaded = True
        _perf_log(
            "ui",
            "create_rfq.first_load",
            first_load_started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            force_reload=force_reload,
            skipped=False,
            first_load=first_load,
        )
        _perf_log(
            "ui",
            "create_rfq.tab_refresh",
            started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            skipped=False,
        )

    def _hydrate_active_rfq_tree_selection_context(self, *, force_reload: bool) -> None:
        started_at = time.perf_counter()
        active_tab = self._active_procurement_tab_name()
        if self.procurement_center != "RFQ":
            _perf_log(
                "ui",
                "rfq_viewer.context_load_skipped",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                skipped=True,
                reason="inactive_center",
            )
            return

        if self._is_create_rfq_tab_active():
            context_started_at = time.perf_counter()
            self.ensure_create_rfq_tab_loaded(force_reload=force_reload)
            _perf_log(
                "ui",
                "create_rfq.context_load",
                context_started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                material_call_id=self.active_material_call_id,
                rfq_id=self.active_rfq_id,
                force_reload=force_reload,
            )
            return

        if self._is_receive_quotes_tab_active():
            context_started_at = time.perf_counter()
            self.load_receive_quotes_for_active_selection(force_reload=force_reload)
            _perf_log(
                "ui",
                "receive_quotes.context_load",
                context_started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                material_call_id=self.active_material_call_id,
                rfq_id=self.active_rfq_id,
                force_reload=force_reload,
            )
            return

        if self._is_bid_compare_tab_active():
            context_started_at = time.perf_counter()
            self.load_bid_compare_for_active_selection(force_reload=force_reload)
            _perf_log(
                "ui",
                "bid_compare.context_load",
                context_started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                material_call_id=self.active_material_call_id,
                rfq_id=self.active_rfq_id,
                force_reload=force_reload,
            )
            return

        if self._is_material_call_tab_active():
            self.ensure_material_call_tab_loaded(force_reload=force_reload)
            if not self.active_material_call_id or not self.active_material_call_row:
                _perf_log(
                    "ui",
                    "rfq_viewer.context_load_skipped",
                    started_at,
                    center=self.procurement_center,
                    active_tab=active_tab,
                    skipped=True,
                    reason="no_material_call",
                )
                return
            if (
                not force_reload
                and int(self.direct_material_call_id or 0) == int(self.active_material_call_id or 0)
                and not self.direct_material_call_is_stale
            ):
                _perf_log(
                    "ui",
                    "rfq_viewer.context_load_skipped",
                    started_at,
                    center=self.procurement_center,
                    active_tab=active_tab,
                    material_call_id=self.active_material_call_id,
                    rfq_id=self.active_rfq_id,
                    skipped=True,
                    reason="material_call_workspace_already_loaded",
                )
                return
            self.load_material_call_workspace(
                int(self.active_material_call_id),
                select_tab=False,
                material_call_row=self.active_material_call_row,
            )
            _perf_log(
                "ui",
                "rfq_viewer.context_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                material_call_id=self.active_material_call_id,
                rfq_id=self.active_rfq_id,
                context="material_call",
                force_reload=force_reload,
                skipped=False,
            )
            return

        if self._is_build_mc_tab_active():
            self.ensure_build_mc_tab_loaded(force_reload=force_reload)
            if not self.active_material_call_id or not self.active_material_call_row:
                _perf_log(
                    "ui",
                    "rfq_viewer.context_load_skipped",
                    started_at,
                    center=self.procurement_center,
                    active_tab=active_tab,
                    skipped=True,
                    reason="no_material_call",
                )
                return
            if (
                not force_reload
                and int(self.build_rfq_material_call_id or 0) == int(self.active_material_call_id or 0)
                and not self.build_rfq_material_call_is_stale
            ):
                _perf_log(
                    "ui",
                    "rfq_viewer.context_load_skipped",
                    started_at,
                    center=self.procurement_center,
                    active_tab=active_tab,
                    material_call_id=self.active_material_call_id,
                    rfq_id=self.active_rfq_id,
                    skipped=True,
                    reason="build_mc_context_already_loaded",
                )
                return
            self._load_build_rfq_from_material_call(self.active_material_call_row)
            _perf_log(
                "ui",
                "rfq_viewer.context_load",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                material_call_id=self.active_material_call_id,
                rfq_id=self.active_rfq_id,
                context="build_mc",
                force_reload=force_reload,
                skipped=False,
            )
            return

        if active_tab == "build_po":
            self.ensure_po_build_tab_loaded(force_reload=force_reload)
            _perf_log(
                "ui",
                "rfq_viewer.context_load_skipped",
                started_at,
                center=self.procurement_center,
                active_tab=active_tab,
                material_call_id=self.active_material_call_id,
                rfq_id=self.active_rfq_id,
                skipped=True,
                reason="build_po_tab_owns_its_own_load",
            )
            return

        _perf_log(
            "ui",
            "rfq_viewer.context_load_skipped",
            started_at,
            center=self.procurement_center,
            active_tab=active_tab,
            material_call_id=self.active_material_call_id,
            rfq_id=self.active_rfq_id,
            skipped=True,
            reason="inactive_tab_data_deferred",
        )

    # Legacy flat-pipeline handlers are kept only as a compatibility reference
    # while the active RFQ Center runtime uses the MaterialCall-first tree
    # pipeline methods defined later in this class.
    def _legacy_flat_on_rfq_select(self) -> None:
        if self.po_tree.selectedItems():
            self.po_tree.clearSelection()
        row = self._selected_row(self.rfq_table)
        if row is None:
            return

        rfq_id = int(self.rfq_table.item(row, 0).text())
        self.active_rfq_id = rfq_id
        self.current_estimate_id = int(self.rfq_table.item(row, 1).text())
        self._clear_build_rfq_material_call_context()
        self._set_vendor_selection(self.rfq_table.item(row, 4).text())
        self.due_date_edit.setText(self.rfq_table.item(row, 6).text())

        self.selected_quote_path = None
        self.quote_no_edit.setText("")
        self.quote_date_edit.setText("")
        self.attach_label.setText("No file attached")
        self.attach_label.setStyleSheet("color: gray;")
        self._clear_receive_quotes_material_call_context()
        self.matrix_table.setRowCount(0)
        self.package_table.setRowCount(0)
        self.master_table.setRowCount(0)

        try:
            header = get_rfq_header_data(rfq_id)
            if header:
                self._apply_receive_quotes_header_context(header)
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
                        center=column == 2,
                        align_right=column in (4, 5, 6),
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
            QMessageBox.critical(self, "Material Call Load Error", str(exc))
        self.load_bid_compare_for_rfq(rfq_id)

    def _legacy_flat_on_po_select(self) -> None:
        if self.rfq_table.selectedItems():
            self.rfq_table.clearSelection()
        items = self.po_tree.selectedItems()
        if not items:
            return
        payload = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        row_data = dict(payload.get("row") or {})
        po_id = int(row_data.get("PurchaseOrderID") or 0)
        status_text = str(row_data.get("Status") or "").strip()
        try:
            self.load_material_request_purchase_order(po_id, select_tab=False)
        except Exception as exc:
            QMessageBox.critical(self, "Material Call Load Error", str(exc))
            return
        if status_text != "Draft":
            self.load_existing_purchase_order(po_id, select_tab=True)

    def refresh_data(self, *, clear_caches: bool = False) -> None:
        started_at = time.perf_counter()
        if clear_caches:
            self._clear_page_session_caches()
        self.load_pipeline()
        if self._is_material_call_tab_active() or self._material_call_tab_loaded:
            self.ensure_material_call_tab_loaded(force_reload=clear_caches)
        else:
            self.on_material_request_mode_changed()
        if self._is_build_mc_tab_active() or self._build_mc_tab_loaded:
            self.ensure_build_mc_tab_loaded(force_reload=clear_caches)
        if (self._active_procurement_tab_name() == "build_po") or self._po_build_tab_loaded:
            self.ensure_po_build_tab_loaded(force_reload=clear_caches)
        if self._is_create_rfq_tab_active() or self._create_rfq_tab_loaded:
            self.ensure_create_rfq_tab_loaded(force_reload=clear_caches)
        if self.current_estimate_id or self.active_rfq_id or self.active_material_call_id:
            self.request_rfq_preview_refresh("Template list refreshed.")
        _perf_log(
            "ui",
            "rfq_viewer.refresh_data",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            clear_caches=clear_caches,
        )

    def _get_document_catalog_service(self) -> DocumentCatalogService:
        if self._document_catalog_service is not None:
            return self._document_catalog_service
        catalog_service = getattr(getattr(self.parent(), "container", None), "document_catalog_service", None)
        if catalog_service is None and hasattr(self.parent(), "main_window"):
            catalog_service = getattr(getattr(self.parent().main_window, "container", None), "document_catalog_service", None)
        if catalog_service is None:
            main_window = self.window()
            catalog_service = getattr(getattr(main_window, "container", None), "document_catalog_service", None)
        if catalog_service is not None:
            self._document_catalog_service = catalog_service
            return catalog_service
        self._document_catalog_service = DocumentCatalogService(DocumentControlRepository())
        return self._document_catalog_service

    def load_rfq_template_options(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        cache_hit = self._rfq_template_cache_loaded and not force_reload
        if not cache_hit:
            service_load_started_at = time.perf_counter()
            service_resolve_started_at = time.perf_counter()
            try:
                catalog_service = self._get_document_catalog_service()
                _perf_log(
                    "ui",
                    "create_rfq.template_service_resolve",
                    service_resolve_started_at,
                    center=self.procurement_center,
                    active_tab=self._active_procurement_tab_name(),
                    cache_hit=cache_hit,
                    skipped=False,
                )
                template_list_started_at = time.perf_counter()
                summaries = catalog_service.list_current_templates(
                    document_type_code=self.RFQ_DELIVERY_DOCUMENT_TYPE_CODE
                )
                _perf_log(
                    "ui",
                    "create_rfq.template_list_query",
                    template_list_started_at,
                    center=self.procurement_center,
                    active_tab=self._active_procurement_tab_name(),
                    row_count=len(summaries),
                    cache_hit=cache_hit,
                    skipped=False,
                )
            except Exception:
                summaries = []
                catalog_service = None
                _perf_log(
                    "ui",
                    "create_rfq.template_service_resolve",
                    service_resolve_started_at,
                    center=self.procurement_center,
                    active_tab=self._active_procurement_tab_name(),
                    cache_hit=cache_hit,
                    skipped=False,
                    reason="service_load_failed",
                )

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
                defaults_started_at = time.perf_counter()
                try:
                    default_mappings = catalog_service.list_template_defaults(
                        document_type_code=self.RFQ_DELIVERY_DOCUMENT_TYPE_CODE,
                        usage_context=self.RFQ_SEND_USAGE_CONTEXT,
                    )
                    _perf_log(
                        "ui",
                        "create_rfq.template_defaults_query",
                        defaults_started_at,
                        center=self.procurement_center,
                        active_tab=self._active_procurement_tab_name(),
                        row_count=len(default_mappings),
                        cache_hit=cache_hit,
                        skipped=False,
                    )
                except Exception:
                    default_mappings = []
                    _perf_log(
                        "ui",
                        "create_rfq.template_defaults_query",
                        defaults_started_at,
                        center=self.procurement_center,
                        active_tab=self._active_procurement_tab_name(),
                        row_count=0,
                        cache_hit=cache_hit,
                        skipped=False,
                        reason="defaults_load_failed",
                    )
                for default_mapping in default_mappings:
                    try:
                        kind_value = (
                            default_mapping.template_kind.value
                            if hasattr(default_mapping.template_kind, "value")
                            else str(default_mapping.template_kind or "").strip().lower()
                        )
                    except Exception:
                        kind_value = str(getattr(default_mapping, "template_kind", "") or "").strip().lower()
                    if kind_value in defaults_by_kind:
                        try:
                            defaults_by_kind[kind_value] = int(default_mapping.template_id)
                        except Exception:
                            defaults_by_kind[kind_value] = None
            _perf_log(
                "ui",
                "create_rfq.template_service_load",
                service_load_started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                row_count=len(summaries),
                cache_hit=cache_hit,
                skipped=False,
            )

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
            self._rfq_template_cache_loaded = True
        else:
            _perf_log(
                "ui",
                "create_rfq.template_service_load",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                cache_hit=cache_hit,
                skipped=True,
                reason="cached_template_choices",
            )

        populate_started_at = time.perf_counter()
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
        _perf_log(
            "ui",
            "create_rfq.template_dropdown_populate",
            populate_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            cache_hit=cache_hit,
            skipped=False,
        )
        row_count = sum(len(self._rfq_template_choices_by_kind.get(kind) or []) for kind in self._rfq_template_choices_by_kind)
        _perf_log(
            "ui",
            "create_rfq.template_dropdown_reload",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=row_count,
            cache_hit=cache_hit,
            skipped=False,
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
        self._clear_direct_material_call_context()
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
        is_rfq_center = self.procurement_center == "RFQ"

        self.direct_date_label.setText("Due Date:" if is_rfq_center else "Expected Arrival:")
        self.direct_source_label.setText(self._material_request_source_selector_label(source_type))
        self.direct_request_note_label.setText("Call Notes:" if is_rfq_center else "ETA Note:")

        self.direct_action_type_label.setVisible(not is_rfq_center)
        self.direct_action_type_combo.setVisible(not is_rfq_center)
        self.direct_vendor_label.setVisible(not is_rfq_center)
        self.direct_vendor_combo.setVisible(not is_rfq_center)
        self.direct_add_vendor_rfq_button.setVisible(False)
        self.direct_save_material_call_button.setVisible(is_rfq_center)
        self.direct_create_material_call_button.setVisible(is_rfq_center)
        self.direct_material_call_label.setVisible(is_rfq_center)
        self.direct_material_call_status_label.setVisible(is_rfq_center)
        self.direct_clear_button.setVisible(not is_rfq_center)

        self.direct_new_request_button.setText("New Material Call" if is_rfq_center else "New RFQ/PO")
        self.direct_create_material_call_button.setText("Create MC" if is_rfq_center else "Create Material Call From Rows")
        self.refresh_direct_po_total()

        legacy_debug_enabled = self._legacy_loose_rfq_debug_path_enabled()
        for button in (
            self.direct_save_rfq_button,
            self.direct_preview_rfq_button,
            self.direct_preview_rfq_send_button,
            self.direct_send_rfq_button,
        ):
            button.setVisible(legacy_debug_enabled and is_rfq and not is_rfq_center)
            button.setEnabled(legacy_debug_enabled and is_rfq and not is_rfq_center and not self.direct_workspace_readonly)
        self.direct_save_material_call_button.setEnabled(is_rfq_center and not self.direct_workspace_readonly)
        self.direct_create_material_call_button.setEnabled(is_rfq_center and not self.direct_workspace_readonly)

        for button in (
            self.direct_save_po_button,
            self.direct_lock_po_button,
            self.direct_preview_po_button,
            self.direct_export_po_button,
            self.direct_send_po_button,
        ):
            button.setVisible(not is_rfq_center and not is_rfq)
            button.setEnabled(po_ready and not self.direct_workspace_readonly)

        self.direct_eta_edit.setEnabled(not self.direct_workspace_readonly)
        self.direct_eta_note_edit.setEnabled(not self.direct_workspace_readonly)

        notes = []
        if is_rfq_center:
            notes.append(
                "Material Call is the vendor-neutral package stage before vendor-specific RFQs. Create RFQ owns vendor selection, RFQ preview, draft save, and send."
            )
        elif source_type != "WorkOrder":
            notes.append(
                "Purchase Order compatibility actions currently require Work Order as the source in this workspace."
            )
        if self.direct_workspace_readonly:
            notes.append("This workspace is locked or no longer editable.")
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
        self.direct_save_material_call_button.setEnabled(editable and self.procurement_center == "RFQ")
        self.direct_create_material_call_button.setEnabled(editable and self.procurement_center == "RFQ")
        self.direct_add_vendor_rfq_button.setEnabled(False)
        self.direct_save_rfq_button.setEnabled(editable)
        self.direct_save_po_button.setEnabled(editable)
        self.direct_lock_po_button.setEnabled(editable and self._current_material_request_source_type() == "WorkOrder")
        self.on_material_request_mode_changed()
        self._refresh_direct_material_call_context_status()

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

    def start_new_material_call(self) -> None:
        self.direct_active_rfq_id = None
        self.direct_active_rfq_source_type = None
        self.direct_active_rfq_source_id = None
        self.direct_active_rfq_vendor_id = None
        self.direct_active_rfq_material_call_id = None
        self.direct_active_rfq_material_call_number = ""
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
        self.rfq_tree.clearSelection()
        self.po_tree.clearSelection()
        self._clear_direct_material_call_context()
        self._set_material_request_readonly(False)
        self.direct_status_label.setText("New Material Call workspace.")
        self._set_procurement_tab(self.tab_direct_po, center=self.procurement_center)

    def start_new_material_request(self) -> None:
        # LEGACY_COMPAT_ONLY:
        # Legacy compatibility alias retained for old Material Request wiring.
        # Do not call from the new Material Call / Create RFQ workflow.
        if not self._legacy_loose_rfq_debug_path_enabled():
            print(
                "[legacy-ui-fence] Legacy loose-RFQ compatibility is disabled in normal runtime. "
                "start_new_material_request now falls through to the MaterialCall workspace reset only."
            )
        self.start_new_material_call()

    def _legacy_loose_rfq_debug_path_enabled(self) -> bool:
        return _legacy_loose_rfq_debug_path_enabled()

    def _require_legacy_loose_rfq_debug_path(self, action_label: str) -> None:
        if self._legacy_loose_rfq_debug_path_enabled():
            return
        print(
            "[legacy-ui-fence] Legacy loose-RFQ compatibility is disabled in normal runtime. "
            f"Blocked attempt to {action_label}."
        )
        raise ValueError(
            f"Legacy loose-RFQ compatibility is disabled in normal runtime. "
            f"RFQ Center is MaterialCall-only. Cannot {action_label}."
        )

    def load_material_call_workspace(
        self,
        material_call_id: int,
        *,
        select_tab: bool = False,
        material_call_row: dict[str, Any] | None = None,
    ) -> None:
        material_call_id = int(material_call_id)
        material_call = dict(material_call_row or get_material_call(material_call_id) or {})
        if not material_call:
            raise ValueError(f"Material Call #{material_call_id} was not found.")

        source_type = str(material_call.get("SourceType") or "Estimate")
        source_id = int(material_call.get("SourceID") or 0) or None
        status_text = str(material_call.get("Status") or "Draft")
        due_date_text, note_text = self._parse_material_call_notes_payload(material_call.get("Notes"))
        rfq_count = int(material_call.get("RFQCount") or len(list(material_call.get("rfqs") or [])) or 0)
        editable = rfq_count <= 0 and str(status_text or "").strip().lower() in {"draft", "open"}

        self.direct_action_type_combo.setCurrentText("RFQ")
        self.direct_source_type_combo.setCurrentText("Work Order" if source_type == "WorkOrder" else "Estimate")
        self.refresh_direct_po_choices()
        self._set_direct_source_selection(
            source_id,
            fallback_label=str(material_call.get("SourceDocumentLabel") or "").strip() or None,
        )
        self.direct_eta_edit.setText(due_date_text)
        self.direct_eta_note_edit.setText(note_text)
        self.direct_po_table.setRowCount(0)
        for row_data in get_material_call_items(material_call_id):
            self._append_material_request_row(
                qty=float(row_data.get("Quantity") or 0),
                part_number=str(row_data.get("PartNumber") or ""),
                description=str(row_data.get("Description") or ""),
                unit_cost=0.0,
                notes=str(row_data.get("Notes") or ""),
                catalog_item_id=row_data.get("MaterialID"),
            )
        self.refresh_direct_po_total()

        self.direct_material_call_id = material_call_id
        self.direct_material_call_number = str(
            material_call.get("MaterialCallNumber") or f"MC#{material_call_id}"
        ).strip()
        self.direct_material_call_source_type = source_type
        self.direct_material_call_source_id = int(source_id or 0) or None
        self.direct_material_call_signature = self._current_material_request_material_call_signature()
        self.direct_material_call_is_stale = False
        self.direct_current_status = status_text or "Draft"
        self._clear_material_request_entry_fields()
        self._set_material_request_readonly(not editable)
        if rfq_count > 0:
            self.direct_status_label.setText(
                f"Material Call {self.direct_material_call_number} loaded read-only. Create RFQ owns vendor-specific child RFQs."
            )
        else:
            self.direct_status_label.setText(
                f"Material Call {self.direct_material_call_number} loaded."
            )
        self._refresh_direct_material_call_context_status()
        if select_tab:
            self._set_procurement_tab(self.tab_direct_po, center="RFQ")

    def load_material_request_rfq(self, rfq_id: int, select_tab: bool = False) -> None:
        # LEGACY_COMPAT_ONLY:
        # Loads old loose RFQ / PriceRequest compatibility records into the shared workspace.
        # Do not call from the new Material Call / Create RFQ workflow.
        self._require_legacy_loose_rfq_debug_path(f"load RFQ #{int(rfq_id)} through the legacy loose-RFQ workspace")
        rfq_id = int(rfq_id)
        header = get_rfq_for_material_request(rfq_id)
        if not header:
            raise ValueError(f"RFQ #{rfq_id} was not found.")
        material_call_id = int(header.get("MaterialCallID") or 0) or None
        if not material_call_id:
            self.start_new_material_call()
            self.direct_status_label.setText(f"RFQ #{rfq_id} is a legacy loose RFQ with no Material Call package.")
            self._set_material_request_readonly(True)
            return
        self.load_material_call_workspace(material_call_id, select_tab=select_tab, material_call_row=self.active_material_call_row)

    def load_material_request_purchase_order(self, po_id: int, select_tab: bool = False) -> None:
        # LEGACY_COMPAT_ONLY:
        # Loads old shared Material Request / Purchase Order compatibility records.
        # Do not call from the new Material Call / Create RFQ workflow.
        # Intentionally left unfenced in this slice pending separate PO/receiving review.
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
        self.active_work_order_id = work_order_id or None
        self.active_work_order_number = f"WO #{work_order_id}" if work_order_id else ""
        self.active_purchase_order_id = po_id
        self.active_purchase_order_row = {**dict(self.active_purchase_order_row or {}), **dict(header)}
        self.active_vendor_id = vendor_id or None
        self.active_po_id = po_id
        self.current_vendor_id = vendor_id or None
        self.direct_active_rfq_id = None
        self.direct_active_rfq_source_type = None
        self.direct_active_rfq_source_id = None
        self.direct_active_rfq_vendor_id = None
        self.direct_active_rfq_material_call_id = None
        self.direct_active_rfq_material_call_number = ""
        self.direct_current_status = status_text or "Draft"
        self._clear_material_request_entry_fields()
        self._clear_direct_material_call_context()
        self._set_material_request_readonly(not editable)
        self.direct_status_label.setText(
            f"PO #{po_id} loaded." if editable else f"PO #{po_id} loaded read-only."
        )
        if select_tab:
            self._set_procurement_tab(self.tab_direct_po, center="PO")
        self.refresh_create_purchase_order_shell()
        self.refresh_receiving_metadata_panel()

    def refresh_direct_po_choices(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        try:
            selected_source_id = self.direct_source_combo.currentData() if hasattr(self, "direct_source_combo") else None
            selected_vendor = self.direct_vendor_combo.currentText().strip()
            source_type = self._current_material_request_source_type()
            source_cache_hit = False
            if source_type == "Estimate":
                estimate_rows, source_cache_hit = self._get_cached_draft_estimates(force_reload=force_reload)
                self.direct_estimate_lookup = {
                    str(row.get("Label") or f"Estimate #{row['EstimateID']}"): int(row["EstimateID"])
                    for row in estimate_rows
                }
                self.direct_source_lookup = dict(self.direct_estimate_lookup)
            else:
                work_order_rows, source_cache_hit = self._get_cached_material_request_work_orders(force_reload=force_reload)
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
            _perf_log(
                "ui",
                "material_request.estimate_dropdown_reload" if source_type == "Estimate" else "material_request.work_order_dropdown_reload",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                row_count=len(self.direct_source_lookup),
                cache_hit=source_cache_hit,
                source_type=source_type,
                skipped=False,
            )

            vendor_started_at = time.perf_counter()
            vendor_rows, vendor_cache_hit = self._get_cached_vendors(force_reload=force_reload)
            self.direct_vendor_lookup = {
                str(row.get("VendorName") or "").strip(): int(row["VendorID"])
                for row in vendor_rows
                if str(row.get("VendorName") or "").strip()
            }
            self.direct_vendor_combo.blockSignals(True)
            self.direct_vendor_combo.clear()
            self.direct_vendor_combo.addItems(self.direct_vendor_lookup.keys())
            self.direct_vendor_combo.setCurrentText(selected_vendor if selected_vendor in self.direct_vendor_lookup else "")
            self.direct_vendor_combo.blockSignals(False)
            _perf_log(
                "ui",
                "material_request.vendor_dropdown_reload",
                vendor_started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                row_count=len(self.direct_vendor_lookup),
                cache_hit=vendor_cache_hit,
                skipped=False,
            )
        except Exception as exc:
            print(f"Material request choices error: {exc}")

    def on_direct_vendor_select(self, _value: str | None = None) -> None:
        self.on_direct_material_request_context_changed()
        current_material = getattr(self.direct_part_number_edit, "current_material_row", None)
        if current_material:
            self._apply_direct_material_selection(current_material)

    def on_direct_material_request_context_changed(self, _value: str | None = None) -> None:
        current_source_type = self._current_material_request_source_type()
        current_source_id = self.direct_source_combo.currentData()
        if (
            self.direct_material_call_id
            and (
                str(self.direct_material_call_source_type or "") != str(current_source_type)
                or int(self.direct_material_call_source_id or 0) != int(current_source_id or 0)
            )
        ):
            self._clear_direct_material_call_context()
        if self._current_material_request_action_type() != "RFQ":
            return
        if not self.direct_active_rfq_id:
            return
        current_vendor_label = self.direct_vendor_combo.currentText().strip()
        current_vendor_id = self.direct_vendor_lookup.get(current_vendor_label)
        context_changed = (
            str(self.direct_active_rfq_source_type or "") != current_source_type
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
            QMessageBox.information(self, "Material Workspace", "This workspace is locked or no longer editable.")
            return
        try:
            part_no = self.direct_part_number_edit.currentText().strip()
            desc = self.direct_desc.text().strip()
            qty = float(self.direct_qty.text().strip() or 0)
            price = float(self.direct_price.text().strip() or 0)
            if not part_no or qty <= 0:
                QMessageBox.warning(self, "Material Call", "Qty and Part Number are required.")
                return
            item_id = getattr(self.direct_part_number_edit, "current_item_id", None)
            if not item_id and not desc:
                QMessageBox.warning(self, "Material Call", "Description is required for unknown part numbers.")
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
            self._refresh_direct_material_call_context_status()
        except ValueError:
            QMessageBox.critical(self, "Error", "Quantity and price must be valid numbers.")

    def remove_direct_po_line(self, _event=None) -> None:
        if self.direct_workspace_readonly:
            QMessageBox.information(self, "Material Workspace", "This workspace is locked or no longer editable.")
            return
        rows = sorted({item.row() for item in self.direct_po_table.selectedItems()}, reverse=True)
        for row in rows:
            self.direct_po_table.removeRow(row)
        self.refresh_direct_po_total()
        self._refresh_direct_material_call_context_status()

    def clear_direct_po_draft(self) -> None:
        self.start_new_material_request()

    def refresh_direct_po_total(self) -> None:
        total = 0.0
        for row in range(self.direct_po_table.rowCount()):
            total += float(str(self.direct_po_table.item(row, 4).text()).replace("$", "").replace(",", "").strip() or 0)
        label_prefix = "MATERIAL CALL TOTAL" if self.procurement_center == "RFQ" else "MATERIAL REQUEST TOTAL"
        self.direct_total_label.setText(f"{label_prefix}: ${total:,.2f}")

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
                self._refresh_direct_material_call_context_status()
            except ValueError:
                return

        self._show_inline_editor(self.direct_po_table, row, column, current_text, commit)

    def _current_material_request_material_call_signature(self) -> tuple[tuple[str, ...], ...]:
        signature: list[tuple[str, ...]] = []
        for row in range(self.direct_po_table.rowCount()):
            signature.append(
                (
                    str(self.direct_po_table.item(row, 0).text() or "").strip(),
                    str(self.direct_po_table.item(row, 1).text() or "").strip(),
                    str(self.direct_po_table.item(row, 2).text() or "").strip(),
                    str(self.direct_po_table.item(row, 5).text() or "").strip(),
                    str(self.direct_po_table.item(row, 6).text() or "").strip(),
                )
            )
        return tuple(signature)

    def _compose_material_call_notes_payload(self) -> str | None:
        due_date_text = str(self.direct_eta_edit.text() or "").strip()
        note_text = str(self.direct_eta_note_edit.text() or "").strip()
        lines: list[str] = []
        if due_date_text:
            lines.append(f"Due Date: {due_date_text}")
        if note_text:
            if lines:
                lines.append("")
            lines.append(note_text)
        payload = "\n".join(lines).strip()
        return payload or None

    def _parse_material_call_notes_payload(self, notes: str | None) -> tuple[str, str]:
        note_text = str(notes or "").strip()
        if not note_text:
            return "", ""
        lines = note_text.splitlines()
        if lines and lines[0].startswith("Due Date: "):
            due_date_text = lines[0].split("Due Date: ", 1)[1].strip()
            remaining = "\n".join(lines[1:]).strip()
            return due_date_text, remaining
        return "", note_text

    def _current_material_call_context_for_vendor_rfq(self) -> tuple[int | None, str]:
        if self.direct_active_rfq_material_call_id:
            material_call_number = str(
                self.direct_active_rfq_material_call_number
                or (f"MC#{self.direct_active_rfq_material_call_id}")
            ).strip()
            return int(self.direct_active_rfq_material_call_id), material_call_number
        if self.direct_material_call_id and not self.direct_material_call_is_stale:
            material_call_number = str(
                self.direct_material_call_number or f"MC#{self.direct_material_call_id}"
            ).strip()
            return int(self.direct_material_call_id), material_call_number
        return None, ""

    def _clear_direct_material_call_context(self) -> None:
        self.direct_material_call_id = None
        self.direct_material_call_number = ""
        self.direct_material_call_source_type = None
        self.direct_material_call_source_id = None
        self.direct_material_call_signature = ()
        self.direct_material_call_is_stale = False
        if hasattr(self, "direct_material_call_label"):
            self.direct_material_call_label.setText("Current Material Call: none")
        if hasattr(self, "direct_material_call_status_label"):
            self.direct_material_call_status_label.setText(
                "Create a Material Call from the staged material rows when you want to stage a pricing package."
            )
            self.direct_material_call_status_label.setStyleSheet("color: #6c757d;")
        if hasattr(self, "direct_add_vendor_rfq_button"):
            self.direct_add_vendor_rfq_button.setEnabled(False)

    def _refresh_direct_material_call_context_status(self) -> None:
        if not getattr(self, "direct_material_call_label", None):
            return
        if not self.direct_material_call_id or not self.direct_material_call_number:
            self.direct_material_call_label.setText("Current Material Call: none")
            self.direct_material_call_status_label.setText(
                "Create a Material Call from the staged material rows when you want to stage a pricing package."
            )
            self.direct_material_call_status_label.setStyleSheet("color: #6c757d;")
        else:
            self.direct_material_call_label.setText(
                f"Current Material Call: {self.direct_material_call_number}"
            )
            current_signature = self._current_material_request_material_call_signature()
            signature_matches = (
                bool(current_signature)
                and current_signature == self.direct_material_call_signature
                and str(self._current_material_request_source_type()) == str(self.direct_material_call_source_type or "")
                and int(self.direct_source_combo.currentData() or 0) == int(self.direct_material_call_source_id or 0)
            )
            self.direct_material_call_is_stale = not signature_matches
            if self.direct_material_call_is_stale:
                self.direct_material_call_status_label.setText(
                    f"Material rows changed after Material Call {self.direct_material_call_number} was created. "
                    "Create a new Material Call if needed."
                )
                self.direct_material_call_status_label.setStyleSheet("color: #8a5a00; font-weight: 600;")
            else:
                self.direct_material_call_status_label.setText(
                    f"Material Call {self.direct_material_call_number} matches the currently staged material rows."
                )
                self.direct_material_call_status_label.setStyleSheet("color: #1f4f99; font-weight: 600;")

        material_call_id, _material_call_number = self._current_material_call_context_for_vendor_rfq()
        if hasattr(self, "direct_add_vendor_rfq_button"):
            self.direct_add_vendor_rfq_button.setEnabled(
                self._current_material_request_action_type() == "RFQ"
                and not self.direct_workspace_readonly
                and bool(material_call_id)
            )

    def _harvest_material_request_material_call_payload(self) -> dict[str, Any]:
        if self.direct_workspace_readonly:
            raise ValueError("This Material Call is locked or no longer editable.")

        source_type = self._current_material_request_source_type()
        if source_type not in {"Estimate", "WorkOrder"}:
            raise ValueError("Select Source Type as Estimate or Work Order first.")

        source_id = self.direct_source_combo.currentData()
        if not source_id:
            raise ValueError(
                f"Select a valid {self._material_request_source_selector_label(source_type).rstrip(':').lower()} first."
            )
        if self.direct_po_table.rowCount() == 0:
            raise ValueError("Add at least one material row before creating a Material Call.")

        material_rows: list[dict[str, Any]] = []
        for row in range(self.direct_po_table.rowCount()):
            qty = float(str(self.direct_po_table.item(row, 0).text() or "0").strip())
            part_number = str(self.direct_po_table.item(row, 1).text() or "").strip() or None
            description = str(self.direct_po_table.item(row, 2).text() or "").strip()
            notes = str(self.direct_po_table.item(row, 5).text() or "").strip() or None
            material_id = str(self.direct_po_table.item(row, 6).text() or "").strip() or None
            if qty <= 0:
                raise ValueError(f"Material row #{row + 1} quantity must be greater than zero.")
            if not description and material_id in (None, ""):
                raise ValueError(
                    f"Material row #{row + 1} is missing a description. Add a description or choose a known material."
                )
            material_rows.append(
                {
                    "qty": qty,
                    "part_number": part_number,
                    "description": description,
                    "unit": None,
                    "notes": notes,
                    "material_id": int(material_id) if material_id not in (None, "") else None,
                }
            )

        return {
            "source_type": source_type,
            "source_id": int(source_id),
            "rows": material_rows,
        }

    def _persist_direct_material_call(self, *, status: str, action_label: str) -> tuple[int, str, int, bool]:
        payload = self._harvest_material_request_material_call_payload()
        notes_payload = self._compose_material_call_notes_payload()
        current_material_call_id = int(self.direct_material_call_id or 0) or None
        reuse_existing = (
            current_material_call_id is not None
            and not self.direct_material_call_is_stale
            and str(self.direct_material_call_source_type or "") == str(payload["source_type"])
            and int(self.direct_material_call_source_id or 0) == int(payload["source_id"])
        )
        if reuse_existing:
            update_material_call_status_and_notes(
                int(current_material_call_id),
                status=status,
                notes=notes_payload,
            )
            material_call_id = int(current_material_call_id)
            reused_existing = True
        else:
            material_call_id = create_material_call_from_manual_rows(
                payload["source_type"],
                int(payload["source_id"]),
                payload["rows"],
                notes=notes_payload,
                status=status,
            )
            reused_existing = False
        material_call = get_material_call(int(material_call_id)) or {}
        material_call_number = str(material_call.get("MaterialCallNumber") or f"MC#{material_call_id}")
        item_count = int(material_call.get("ItemCount") or len(payload["rows"]))
        self.direct_material_call_id = int(material_call_id)
        self.direct_material_call_number = material_call_number
        self.direct_material_call_source_type = str(payload["source_type"])
        self.direct_material_call_source_id = int(payload["source_id"])
        self.direct_material_call_signature = self._current_material_request_material_call_signature()
        self.direct_material_call_is_stale = False
        self.direct_current_status = str(status or "Draft")
        self._refresh_direct_material_call_context_status()
        self.load_pipeline()
        self._select_material_call_in_pipeline(int(material_call_id))
        self.direct_status_label.setText(
            f"Material Call {material_call_number} {action_label.lower()} with {item_count} item(s)."
        )
        return int(material_call_id), material_call_number, item_count, reused_existing

    def save_direct_material_call_draft(self) -> None:
        try:
            material_call_id, material_call_number, item_count, reused_existing = self._persist_direct_material_call(
                status="Draft",
                action_label="saved as Draft",
            )
            QMessageBox.information(
                self,
                "Material Call Draft Saved",
                (
                    f"Material Call {material_call_number} draft updated with {item_count} item(s)."
                    if reused_existing
                    else f"Material Call {material_call_number} draft saved with {item_count} item(s)."
                ),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save Draft MC Failed", str(exc))

    def create_material_call_from_material_request_rows(self) -> None:
        try:
            material_call_id, material_call_number, item_count, reused_existing = self._persist_direct_material_call(
                status="Open",
                action_label="created",
            )
            self._set_procurement_tab(self.tab_create_rfq, center="RFQ")
            QMessageBox.information(
                self,
                "Material Call Created",
                (
                    f"Material Call {material_call_number} finalized and updated with {item_count} item(s)."
                    if reused_existing
                    else f"Material Call {material_call_number} created with {item_count} item(s)."
                ),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Create MC Failed", str(exc))

    def create_vendor_rfq_from_current_material_call(self) -> None:
        if self._current_material_request_action_type() != "RFQ":
            QMessageBox.warning(self, "Create Vendor RFQ", "Switch Action Type to RFQ first.")
            return
        if self.direct_workspace_readonly:
            QMessageBox.information(self, "Create Vendor RFQ", "This RFQ/PO is locked or no longer editable.")
            return
        material_call_id, material_call_number = self._current_material_call_context_for_vendor_rfq()
        if not material_call_id:
            QMessageBox.warning(
                self,
                "Create Vendor RFQ",
                "Create or select a Material Call before adding another vendor RFQ.",
            )
            return

        vendor_label = self.direct_vendor_combo.currentText().strip()
        vendor_id = self.direct_vendor_lookup.get(vendor_label)
        if not vendor_id:
            QMessageBox.warning(self, "Create Vendor RFQ", "Select a Vendor before creating the RFQ.")
            return

        try:
            result = create_vendor_rfq_for_material_call(
                int(material_call_id),
                int(vendor_id),
                due_date=self.direct_eta_edit.text().strip() or None,
                notes=self.direct_eta_note_edit.text().strip() or None,
            )
            rfq_id = int(result["rfq_id"])
            material_call_number = str(result.get("material_call_number") or material_call_number or "")
            self.direct_active_rfq_id = rfq_id
            self.direct_active_rfq_source_type = str(result.get("source_type") or self._current_material_request_source_type())
            self.direct_active_rfq_source_id = int(result.get("source_id") or self.direct_source_combo.currentData() or 0) or None
            self.direct_active_rfq_vendor_id = int(result.get("vendor_id") or vendor_id)
            self.direct_active_rfq_material_call_id = int(result.get("material_call_id") or material_call_id)
            self.direct_active_rfq_material_call_number = material_call_number
            self.active_rfq_id = rfq_id
            self.current_estimate_id = int(result.get("estimate_id") or 0) or self.current_estimate_id
            self.current_vendor_id = int(result.get("vendor_id") or vendor_id)
            self.selected_vendor_name = str(result.get("vendor_name") or vendor_label or "")
            self.direct_current_status = str(result.get("status") or "Draft")
            self.direct_status_label.setText(
                f"RFQ #{rfq_id} created under Material Call {material_call_number} for {self.selected_vendor_name or vendor_label}."
            )
            self.load_pipeline()
            self._select_rfq_in_pipeline(rfq_id)
            self.on_material_request_mode_changed()
            QMessageBox.information(
                self,
                "Vendor RFQ Created",
                f"RFQ #{rfq_id} created under Material Call {material_call_number} for {self.selected_vendor_name or vendor_label}.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Create Vendor RFQ Failed", str(exc))

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
        # LEGACY_COMPAT_ONLY:
        # Saves old loose RFQ / PriceRequest drafts for compatibility records.
        # Do not call from the new Material Call / Create RFQ workflow.
        self._require_legacy_loose_rfq_debug_path("save a legacy loose RFQ draft")
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
                if self.direct_active_rfq_material_call_id:
                    if self.direct_material_call_is_stale:
                        raise ValueError(
                            "Material rows changed after Material Call creation. Recreate the Material Call before saving the RFQ."
                        )
                    loaded_signature = self.direct_material_call_signature
                    current_signature = self._current_material_request_material_call_signature()
                    if not loaded_signature or current_signature != loaded_signature:
                        raise ValueError(
                            "Existing MaterialCall-backed RFQs must be revised by creating/recreating the Material Call package."
                        )
                    update_material_call_backed_rfq_draft_header(
                        int(self.direct_active_rfq_id),
                        due_date=payload["due_date"],
                        rfq_notes=payload["rfq_notes"],
                    )
                    self.direct_material_call_is_stale = False
                else:
                    update_manual_rfq_draft(
                        int(self.direct_active_rfq_id),
                        payload["material_rows"],
                        due_date=payload["due_date"],
                        rfq_notes=payload["rfq_notes"],
                    )
                rfq_id = int(self.direct_active_rfq_id)
                success_message = f"RFQ #{rfq_id} was updated as a draft."
            else:
                if not self.direct_material_call_id:
                    raise ValueError("Create a Material Call from rows before saving a vendor RFQ.")
                if self.direct_material_call_is_stale:
                    raise ValueError(
                        "Material rows changed after Material Call creation. Recreate the Material Call before saving the RFQ."
                    )
                rfq_result = create_vendor_rfq_for_material_call(
                    int(self.direct_material_call_id),
                    int(payload["vendor_id"]),
                    due_date=payload["due_date"],
                    notes=payload["rfq_notes"],
                )
                rfq_id = int(rfq_result["rfq_id"])
                self.direct_active_rfq_material_call_id = int(
                    rfq_result.get("material_call_id") or self.direct_material_call_id
                )
                self.direct_active_rfq_material_call_number = str(
                    rfq_result.get("material_call_number") or self.direct_material_call_number or ""
                ).strip()
                success_message = (
                    f"RFQ #{rfq_id} was created as a draft under Material Call "
                    f"{self.direct_active_rfq_material_call_number or f'MC#{self.direct_active_rfq_material_call_id}'}."
                )

            self.direct_active_rfq_id = int(rfq_id)
            self.direct_active_rfq_source_type = str(payload["source_type"])
            self.direct_active_rfq_source_id = int(payload["source_id"])
            self.direct_active_rfq_vendor_id = int(payload["vendor_id"])
            self.active_rfq_id = int(rfq_id)
            self.current_vendor_id = int(payload["vendor_id"])
            self.selected_vendor_name = str(payload["vendor_label"] or "")
            self.direct_current_status = "Draft"
            if self.direct_active_rfq_material_call_id:
                material_call_number = str(
                    self.direct_active_rfq_material_call_number
                    or self.direct_material_call_number
                    or f"MC#{self.direct_active_rfq_material_call_id}"
                ).strip()
                self.direct_status_label.setText(
                    f"RFQ #{rfq_id} saved as Draft under Material Call {material_call_number}"
                )
            else:
                self.direct_status_label.setText(f"RFQ #{rfq_id} saved as Draft")
            self.load_pipeline()
            self._select_rfq_in_pipeline(rfq_id)
            QMessageBox.information(self, "Save RFQ Draft Succeeded", success_message)
        except Exception as exc:
            QMessageBox.critical(self, "Save RFQ Draft Failed", f"Save RFQ Draft failed: {exc}")

    def preview_material_request_rfq(self) -> None:
        # LEGACY_COMPAT_ONLY:
        # Previews old loose RFQ / PriceRequest compatibility records.
        # Do not call from the new Material Call / Create RFQ workflow.
        self._require_legacy_loose_rfq_debug_path("preview a legacy loose RFQ")
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
        # LEGACY_COMPAT_ONLY:
        # Previews legacy send output for old loose RFQ / PriceRequest compatibility records.
        # Do not call from the new Material Call / Create RFQ workflow.
        self._require_legacy_loose_rfq_debug_path("preview legacy loose RFQ send output")
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
        # LEGACY_COMPAT_ONLY:
        # Sends old loose RFQ / PriceRequest compatibility records.
        # Do not call from the new Material Call / Create RFQ workflow.
        self._require_legacy_loose_rfq_debug_path("send a legacy loose RFQ")
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

    def _legacy_flat_select_rfq_in_pipeline(self, rfq_id: int) -> None:
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
        item = self._select_purchase_order_in_pipeline_item(int(po_id))
        if item is None:
            return
        self.po_tree.setCurrentItem(item)
        payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
        row_data = dict(payload.get("row") or {})
        status_text = str(row_data.get("Status") or "").strip()
        self.load_material_request_purchase_order(po_id, select_tab=True)
        if not prefer_direct and status_text != "Draft":
            self.load_existing_purchase_order(po_id, select_tab=False)

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
            self.active_work_order_id = int(header.get("WorkOrderID") or 0) or None
            self.active_work_order_number = f"WO #{self.active_work_order_id}" if self.active_work_order_id else ""
            self.active_purchase_order_id = po_id
            self.active_purchase_order_row = {**dict(self.active_purchase_order_row or {}), **dict(header)}
            self.active_vendor_id = int(header.get("VendorID") or 0) or None
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
                    str(source_link.get("MaterialCallID") or ""),
                    str(source_link.get("MaterialCallItemID") or ""),
                ]
                for column, value in enumerate(values):
                    self._set_item(self.po_item_table, row, column, value, center=column in (2, 3), align_right=column == 4)

            self.reset_receiving_session()
            receiving_rows = list(get_po_items_with_receiving(po_id) or [])
            self._populate_receiving_table_rows(receiving_rows)
            self._receiving_loaded_po_id = po_id

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
            self.refresh_create_purchase_order_shell()
            self.refresh_receiving_metadata_panel()
        except Exception as exc:
            QMessageBox.critical(self, "PO Load Error", str(exc))

    def on_attach_file(self) -> None:
        if not self.active_rfq_id:
            QMessageBox.information(self, "Select RFQ", "Select a child RFQ before attaching a quote file.")
            return
        if self.receive_quotes_edit_locked:
            QMessageBox.information(self, "Quote Locked", "This quote is locked and its attachment cannot be changed.")
            return
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
            if hasattr(self, "est_combo"):
                self.refresh_build_mc_source_choices()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _current_build_mc_source_type(self) -> str:
        value = self.build_mc_source_type_combo.currentData() if hasattr(self, "build_mc_source_type_combo") else "Estimate"
        return str(value or "Estimate")

    def _build_mc_source_selector_label(self, source_type: str | None = None) -> str:
        normalized = self._current_build_mc_source_type() if source_type is None else str(source_type or "")
        return "Work Order #:" if normalized == "WorkOrder" else "Estimate #:"

    def _set_build_mc_source_selection(self, source_id: int | None, *, fallback_label: str | None = None) -> None:
        self.est_combo.blockSignals(True)
        try:
            if not source_id:
                if self.est_combo.count() > 0:
                    self.est_combo.setCurrentIndex(0)
                return
            target_index = self.est_combo.findData(int(source_id))
            if target_index < 0 and fallback_label:
                label = str(fallback_label).strip()
                if label:
                    self.est_combo.addItem(label, int(source_id))
                    self.build_mc_source_lookup[label] = int(source_id)
                    target_index = self.est_combo.findData(int(source_id))
            if target_index >= 0:
                self.est_combo.setCurrentIndex(target_index)
            elif self.est_combo.count() > 0:
                self.est_combo.setCurrentIndex(0)
        finally:
            self.est_combo.blockSignals(False)

    def refresh_build_mc_source_choices(self, *, force_reload: bool = False) -> None:
        started_at = time.perf_counter()
        try:
            selected_source_id = self.est_combo.currentData() if hasattr(self, "est_combo") else None
            source_type = self._current_build_mc_source_type()
            cache_hit = False
            if source_type == "Estimate":
                estimate_rows, cache_hit = self._get_cached_draft_estimates(force_reload=force_reload)
                self.build_mc_estimate_lookup = {
                    str(row.get("Label") or f"Estimate #{row['EstimateID']}"): int(row["EstimateID"])
                    for row in estimate_rows
                }
                self.build_mc_source_lookup = dict(self.build_mc_estimate_lookup)
            else:
                work_order_rows, cache_hit = self._get_cached_material_request_work_orders(force_reload=force_reload)
                self.build_mc_workorder_lookup = {
                    str(row.get("Label") or f"Work Order #{row['WorkOrderID']}"): int(row["WorkOrderID"])
                    for row in work_order_rows
                }
                self.build_mc_source_lookup = dict(self.build_mc_workorder_lookup)

            self.est_combo.blockSignals(True)
            self.est_combo.clear()
            empty_label = (
                "Select a saved draft estimate..."
                if source_type == "Estimate"
                else "Select an open work order..."
            )
            self.est_combo.addItem(empty_label, None)
            for label, source_id in self.build_mc_source_lookup.items():
                self.est_combo.addItem(label, int(source_id))
            if selected_source_id not in (None, ""):
                selected_index = self.est_combo.findData(int(selected_source_id))
                self.est_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            else:
                self.est_combo.setCurrentIndex(0)
        finally:
            self.est_combo.blockSignals(False)
        self.build_mc_source_label.setText(self._build_mc_source_selector_label(source_type))
        _perf_log(
            "ui",
            "build_mc.estimate_dropdown_reload" if source_type == "Estimate" else "build_mc.work_order_dropdown_reload",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            row_count=len(self.build_mc_source_lookup),
            cache_hit=cache_hit,
            source_type=source_type,
            skipped=False,
        )

    def _load_build_mc_source_materials(self) -> None:
        self.master_table.setRowCount(0)
        self.package_table.setRowCount(0)
        source_type = self._current_build_mc_source_type()
        source_id = self.est_combo.currentData()
        if not source_id:
            self.current_estimate_id = None
            return
        if source_type == "Estimate":
            self.current_estimate_id = int(source_id)
            try:
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
        else:
            self.current_estimate_id = None
            self.build_rfq_material_call_status_label.setText(
                "Open Work Order source selection is available here, but row staging for Work Order packages is still handled in Material Call."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #8a5a00; font-weight: 600;")

    # CLEANUP_CANDIDATE_SHADOWED:
    # This earlier definition is shadowed by a later on_build_mc_source_type_changed()
    # definition in this class.
    def on_build_mc_source_type_changed(self) -> None:
        self.current_vendor_id = None
        self._set_vendor_selection("")
        self._clear_build_rfq_material_call_context()
        self.refresh_build_mc_source_choices()
        self._load_build_mc_source_materials()
        self.render_rfq_preview_for_selection(force=True, reason="Build MC source type changed.")

    # CLEANUP_CANDIDATE_SHADOWED:
    # This earlier definition is shadowed by a later on_build_mc_source_changed()
    # definition in this class.
    def on_build_mc_source_changed(self) -> None:
        self.active_rfq_id = None
        self.current_vendor_id = None
        self._set_vendor_selection("")
        self._clear_build_rfq_material_call_context()
        self._load_build_mc_source_materials()
        self.render_rfq_preview_for_selection(force=True, reason="Build MC source changed.")

    def _set_vendor_selection(self, vendor_name: str) -> None:
        self.selected_vendor_name = vendor_name or ""
        button = self.vendor_buttons.get(self.selected_vendor_name)
        if button:
            button.setChecked(True)
        if hasattr(self, "create_rfq_vendor_combo"):
            self.create_rfq_vendor_combo.blockSignals(True)
            self.create_rfq_vendor_combo.setCurrentText(self.selected_vendor_name)
            self.create_rfq_vendor_combo.blockSignals(False)
        self._refresh_create_rfq_context_panel()

    def _rebuild_vendor_buttons(self, quoted: list[str]) -> None:
        if not hasattr(self, "vendor_inner_layout"):
            if self._is_create_rfq_tab_active() or self._create_rfq_tab_loaded:
                self._load_create_rfq_vendor_choices()
            return
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
        if self._is_create_rfq_tab_active() or self._create_rfq_tab_loaded:
            self._load_create_rfq_vendor_choices()

    def _on_vendor_button_clicked(self, vendor_name: str) -> None:
        self.selected_vendor_name = vendor_name
        if self._is_create_rfq_tab_active() or self._create_rfq_tab_loaded:
            self._load_create_rfq_vendor_choices()
        self.request_rfq_preview_refresh("Vendor selection changed.")

    def _get_vendor_id_for_build_rfq_selection(self) -> int | None:
        vendor_name = str(self.selected_vendor_name or "").strip()
        if not vendor_name:
            return None
        for row in get_vendors() or []:
            row_name = str(row.get("VendorName") or "").strip()
            if row_name == vendor_name:
                try:
                    return int(row["VendorID"])
                except (KeyError, TypeError, ValueError):
                    return None
        return None

    def on_estimate_select(self, selection: str) -> None:
        self.on_build_mc_source_changed()

    def _current_build_rfq_material_selection_ids(self) -> list[int]:
        material_ids: list[int] = []
        for row in range(self.package_table.rowCount()):
            item = self.package_table.item(row, 0)
            if item is None:
                continue
            raw_value = str(item.text() or "").strip()
            if not raw_value:
                continue
            material_ids.append(int(raw_value))
        return material_ids

    def _current_build_rfq_material_signature(self) -> tuple[int, ...]:
        return tuple(sorted(self._current_build_rfq_material_selection_ids()))

    # CLEANUP_CANDIDATE_SHADOWED:
    # This earlier definition is shadowed by a later
    # _clear_build_rfq_material_call_context() definition in this class.
    def _clear_build_rfq_material_call_context(self) -> None:
        self.build_rfq_material_call_id = None
        self.build_rfq_material_call_number = ""
        self.build_rfq_material_call_estimate_id = None
        self.build_rfq_material_call_signature = ()
        self.build_rfq_material_call_is_stale = False
        if hasattr(self, "build_rfq_material_call_label"):
            self.build_rfq_material_call_label.setText("Current Material Call: none")
        if hasattr(self, "build_rfq_material_call_status_label"):
            self.build_rfq_material_call_status_label.setText(
                "Create a Material Call from the selected estimate materials when you want to stage a pricing package."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #6c757d;")

    # CLEANUP_CANDIDATE_SHADOWED:
    # This earlier definition is shadowed by a later
    # _refresh_build_rfq_material_call_context_status() definition in this class.
    def _refresh_build_rfq_material_call_context_status(self) -> None:
        if not getattr(self, "build_rfq_material_call_label", None):
            return
        if not self.build_rfq_material_call_id or not self.build_rfq_material_call_number:
            self.build_rfq_material_call_label.setText("Current Material Call: none")
            self.build_rfq_material_call_status_label.setText(
                "Create a Material Call from the selected estimate materials when you want to stage a pricing package."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #6c757d;")
            return

        self.build_rfq_material_call_label.setText(
            f"Current Material Call: {self.build_rfq_material_call_number}"
        )
        current_signature = self._current_build_rfq_material_signature()
        signature_matches = (
            bool(current_signature)
            and current_signature == self.build_rfq_material_call_signature
            and int(self.current_estimate_id or 0) == int(self.build_rfq_material_call_estimate_id or 0)
        )
        self.build_rfq_material_call_is_stale = not signature_matches
        if self.build_rfq_material_call_is_stale:
            self.build_rfq_material_call_status_label.setText(
                f"Material Call {self.build_rfq_material_call_number} was created from an earlier package selection. "
                "If the selected rows changed, create a new Material Call if needed."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #8a5a00; font-weight: 600;")
        else:
            self.build_rfq_material_call_status_label.setText(
                f"Material Call {self.build_rfq_material_call_number} matches the currently packaged estimate rows."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #1f4f99; font-weight: 600;")

    # CLEANUP_CANDIDATE_SHADOWED:
    # This earlier definition is shadowed by a later
    # create_material_call_from_selected_estimate_materials() definition in this class.
    def create_material_call_from_selected_estimate_materials(self) -> None:
        if not self.current_estimate_id:
            QMessageBox.warning(self, "Create Material Call", "Select a draft estimate first.")
            return

        material_ids = self._current_build_rfq_material_selection_ids()
        if not material_ids:
            QMessageBox.warning(
                self,
                "Create Material Call",
                "Select or package at least one estimate material row first.",
            )
            return

        try:
            material_call_id = create_material_call_from_estimate_items(
                int(self.current_estimate_id),
                material_ids,
                notes="Created from RFQ Center Build MC from Estimate staging UI.",
            )
            material_call = get_material_call(int(material_call_id)) or {}
            material_call_number = str(material_call.get("MaterialCallNumber") or f"MC#{material_call_id}")
            item_count = int(material_call.get("ItemCount") or len(material_ids))
            self.build_rfq_material_call_id = int(material_call_id)
            self.build_rfq_material_call_number = material_call_number
            self.build_rfq_material_call_estimate_id = int(self.current_estimate_id)
            self.build_rfq_material_call_signature = self._current_build_rfq_material_signature()
            self.build_rfq_material_call_is_stale = False
            self._refresh_build_rfq_material_call_context_status()
            self.load_pipeline()
            self._select_material_call_in_pipeline(int(material_call_id))
            self._set_procurement_tab(self.tab_create_rfq, center="RFQ")
            QMessageBox.information(
                self,
                "Material Call Created",
                f"Material Call {material_call_number} created with {item_count} item(s).",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Create Material Call Failed", str(exc))

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
        self._refresh_build_rfq_material_call_context_status()

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
        self._refresh_build_rfq_material_call_context_status()

    def mock_add_all(self) -> None:
        self._move_all_rows(self.master_table, self.package_table)

    def mock_remove_all(self) -> None:
        self._move_all_rows(self.package_table, self.master_table)

    def _legacy_build_tab_generate_and_save_rfq(self) -> None:
        vendor_name = self.selected_vendor_name.strip()
        if not self.current_estimate_id or self.package_table.rowCount() == 0:
            QMessageBox.warning(self, "Save RFQ Draft", "Select an estimate and package at least one material row first.")
            return
        if not self.build_rfq_material_call_id or not self.build_rfq_material_call_number:
            QMessageBox.warning(
                self,
                "Save RFQ Draft",
                "Create a Material Call from selected materials before saving a vendor RFQ.",
            )
            return
        if self.build_rfq_material_call_is_stale:
            QMessageBox.warning(
                self,
                "Save RFQ Draft",
                "Material rows changed after Material Call creation. Recreate the Material Call before saving the RFQ.",
            )
            return
        if not vendor_name:
            QMessageBox.warning(self, "Save RFQ Draft", "Select a vendor before saving the RFQ draft.")
            return
        vendor_id = self._get_vendor_id_for_build_rfq_selection()
        if not vendor_id:
            QMessageBox.warning(self, "Save RFQ Draft", "Select a valid vendor before saving the RFQ draft.")
            return
        try:
            result = create_vendor_rfq_for_material_call(
                int(self.build_rfq_material_call_id),
                int(vendor_id),
                due_date=self.due_date_edit.text().strip() or None,
                notes=None,
            )
            rfq_id = int(result["rfq_id"])
            self.active_rfq_id = int(rfq_id)
            self.current_estimate_id = int(result.get("estimate_id") or self.current_estimate_id or 0) or self.current_estimate_id
            self.current_vendor_id = int(result.get("vendor_id") or vendor_id)
            self.selected_vendor_name = str(result.get("vendor_name") or vendor_name or "")
            self.load_pipeline()
            self._select_rfq_in_pipeline(int(rfq_id))
            self.render_rfq_preview_for_selection(force=True, reason="RFQ draft saved.")
            QMessageBox.information(
                self,
                "Success",
                f"RFQ #{rfq_id} draft saved under Material Call {self.build_rfq_material_call_number}. Review it, then send it to the vendor.",
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Save RFQ Draft", str(exc))
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
        self.rfq_preview_stale = False
        self.rfq_preview_stale_reason = ""
        self.rfq_preview_refresh_pending = False
        self.rfq_preview_refresh_reason = ""

    def _current_rfq_preview_body(self) -> str:
        return self.rfq_preview.toHtml() if self.rfq_preview_is_html else self.rfq_preview.toPlainText()

    def _build_rfq_preview_stale_message(self) -> str:
        base_message = "RFQ body needs re-render. Click Re-render From Templates."
        if self.rfq_preview_dirty:
            return f"{base_message} Manual edits are preserved."
        return base_message

    def _mark_rfq_preview_stale(self, reason: str, *, skipped_reason: str) -> None:
        started_at = time.perf_counter()
        self.rfq_preview_stale = True
        self.rfq_preview_stale_reason = str(reason or "").strip()
        self.rfq_preview_refresh_pending = True
        self.rfq_preview_refresh_reason = str(reason or "").strip()
        self._set_rfq_preview_status(self._build_rfq_preview_stale_message(), warning=True)
        _perf_log(
            "ui",
            "rfq_preview.render",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            skipped=True,
            reason=skipped_reason,
            trigger=self.rfq_preview_stale_reason or "context_changed",
        )

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

    def _legacy_build_tab_render_rfq_preview_payload(self) -> dict:
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
        started_at = time.perf_counter()
        skipped_reason = "manual_mode" if self.rfq_preview_dirty else "stale_only_mode"
        self._mark_rfq_preview_stale(reason, skipped_reason=skipped_reason)
        _perf_log(
            "ui",
            "rfq_preview.refresh_request",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            skipped=True,
            reason=skipped_reason,
            trigger=str(reason or "").strip() or "context_changed",
        )

    def rerender_rfq_preview_from_templates(self) -> None:
        started_at = time.perf_counter()
        if self.rfq_preview_dirty:
            answer = QMessageBox.question(
                self,
                "Overwrite Manual RFQ Edits?",
                "Re-rendering from templates will overwrite the current manual edits in the RFQ email body draft.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                _perf_log(
                    "ui",
                    "rfq_preview.rerender",
                    started_at,
                    center=self.procurement_center,
                    active_tab=self._active_procurement_tab_name(),
                    skipped=True,
                    reason="user_cancelled",
                )
                return
        self.render_rfq_preview_for_selection(force=True, reason="Preview re-rendered from selected templates.")
        _perf_log(
            "ui",
            "rfq_preview.rerender",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            skipped=False,
        )

    def _on_rfq_preview_text_changed(self) -> None:
        if self._suspend_rfq_preview_dirty_tracking:
            return
        self.rfq_preview_dirty = True
        if self.rfq_preview_stale:
            self._set_rfq_preview_status(self._build_rfq_preview_stale_message(), warning=True)
        else:
            self._set_rfq_preview_status(
                "Manual edits detected. This editable body will be used for Preview RFQ Send and Send RFQ."
            )

    def _on_rfq_template_selection_changed(self, reason: str) -> None:
        self.request_rfq_preview_refresh(reason)

    def render_rfq_preview_for_selection(self, *, force: bool = False, reason: str = "") -> None:
        started_at = time.perf_counter()
        if not self._is_create_rfq_tab_active():
            self.rfq_preview_refresh_pending = True
            self.rfq_preview_refresh_reason = reason or self.rfq_preview_refresh_reason
            _perf_log(
                "ui",
                "rfq_preview.render",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                skipped=True,
                force=force,
                reason="inactive_tab",
            )
            return
        if self.rfq_preview_dirty and not force:
            self._mark_rfq_preview_stale(reason or "Preview context changed.", skipped_reason="manual_mode")
            _perf_log(
                "ui",
                "rfq_preview.render",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                skipped=True,
                force=force,
                reason="dirty_preview",
            )
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
        _perf_log(
            "ui",
            "rfq_preview.render",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            skipped=False,
            force=force,
            reason=reason or "rendered",
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

    def _legacy_flat_selected_rfq_ids(self) -> list[int]:
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
        if column != 5:
            return
        if not self.active_rfq_id:
            QMessageBox.information(self, "Select RFQ", "Select a child RFQ before editing Quote Price.")
            return
        if self.receive_quotes_edit_locked:
            QMessageBox.information(self, "Quote Locked", "This quote is locked and can no longer be edited.")
            return
        current_price = float(str(self.matrix_table.item(row, 5).text()).replace("$", "").strip() or 0)
        def commit(new_text: str) -> None:
            try:
                new_price = float(new_text.strip() or 0)
                qty = float(self.matrix_table.item(row, 2).text())
                self.matrix_table.item(row, 5).setText(f"${new_price:.2f}")
                self.matrix_table.item(row, 6).setText(f"${new_price * qty:.2f}")
            except ValueError:
                return

        self._show_inline_editor(self.matrix_table, row, column, f"{current_price:.2f}", commit)

    def on_matrix_click(self, row: int, column: int) -> None:
        if column != 2:
            return
        if self.receive_quotes_material_call_id is not None:
            if not self.receive_quotes_carry_warning_shown:
                self.receive_quotes_carry_warning_shown = True
                QMessageBox.information(
                    self,
                    "Bid Compare Owns Carried Selection",
                    "For Material Call-backed RFQs, Receive Quotes saves vendor pricing only. "
                    "Use Bid Compare to choose carried pricing.",
                )
            return
        item = self.matrix_table.item(row, 2)
        item.setText("[ ]" if item.text() in {"[✓]", "[âœ“]", "[Ã¢Å“â€œ]", "[ÃƒÂ¢Ã…â€œÃ¢â‚¬Å“]"} else "[✓]")

    def _legacy_flat_on_carry_click(self) -> None:
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
                quote_unit = float(str(self.matrix_table.item(row, 5).text()).replace("$", "").strip() or 0)
                all_items.append((pr_item_id, material_id, quote_unit))
                if self.matrix_table.item(row, 2).text() == "[✓]":
                    carried.append(material_id)
            rfq_id = int(self.rfq_table.item(rfq_row, 0).text())
            result = save_quote_response(
                rfq_id,
                self.quote_no_edit.text().strip(),
                self.quote_date_edit.text().strip(),
                all_items,
                self.selected_quote_path,
            )
            if result.get("saved"):
                QMessageBox.information(
                    self,
                    "Quote Saved",
                    "Quote saved for this RFQ. "
                    "Receive Quotes records vendor quote data only. "
                    "Use Bid Compare to carry pricing and update estimate pricing.",
                )
            self.load_receive_quotes_for_active_selection(force_reload=True)
            if self._is_bid_compare_tab_active():
                self.load_bid_compare_for_active_selection(force_reload=True)
            else:
                self._bid_compare_loaded_rfq_id = None
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def on_lock_quote_click(self) -> None:
        if not self.active_rfq_id:
            QMessageBox.warning(self, "Lock Quote", "Select a child RFQ first.")
            return
        if self.receive_quotes_edit_locked:
            QMessageBox.information(self, "Lock Quote", "The selected quote is already locked.")
            return
        try:
            result = lock_quote_response(int(self.active_rfq_id))
            self.load_pipeline()
            self._select_rfq_in_pipeline(int(result["rfq_id"]))
            self.load_receive_quotes_for_active_selection(force_reload=True)
            QMessageBox.information(
                self,
                "Quote Locked",
                f"RFQ #{result['rfq_id']} is now locked for quote editing. "
                "Bid Compare can still use this vendor pricing.",
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Lock Quote", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Lock Quote Failed", str(exc))

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
            if hasattr(self, "po_remove_selected_button"):
                self.po_remove_selected_button.setEnabled(False)
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
        if hasattr(self, "po_remove_selected_button"):
            self.po_remove_selected_button.setEnabled(self.po_item_table.rowCount() > 0 and not self.build_po_readonly)

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
            self.refresh_receiving_metadata_panel()
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

    def _load_build_rfq_from_material_call(self, material_call_row: dict[str, Any] | None) -> None:
        if not material_call_row:
            return
        source_type = str(material_call_row.get("SourceType") or "Estimate")
        source_id = int(material_call_row.get("SourceID") or 0) or None
        if hasattr(self, "build_mc_source_type_combo"):
            self.build_mc_source_type_combo.blockSignals(True)
            self.build_mc_source_type_combo.setCurrentText("Work Order" if source_type == "WorkOrder" else "Estimate")
            self.build_mc_source_type_combo.blockSignals(False)
        self.refresh_build_mc_source_choices()
        self._set_build_mc_source_selection(
            source_id,
            fallback_label=str(material_call_row.get("SourceDocumentLabel") or "").strip() or None,
        )
        self.master_table.setRowCount(0)
        self.package_table.setRowCount(0)
        selected_ids: set[int] = set()
        material_call_items = list(get_material_call_items(int(material_call_row["MaterialCallID"])) or [])
        for item in material_call_items:
            if item.get("EstimateItemID") not in (None, "", 0):
                selected_ids.add(int(item["EstimateItemID"]))

        if source_type == "Estimate" and source_id:
            self.current_estimate_id = int(source_id)
            try:
                for mat in get_estimate_materials(int(source_id)):
                    material_id = (
                        mat.get("EstimateMaterialID")
                        or mat.get("estimate_material_id")
                        or mat.get("MaterialID")
                        or mat.get("materialid")
                        or 0
                    )
                    target_table = self.package_table if int(material_id or 0) in selected_ids else self.master_table
                    row = target_table.rowCount()
                    target_table.insertRow(row)
                    self._set_item(target_table, row, 0, str(material_id))
                    self._set_item(target_table, row, 1, str(mat.get("Quantity") or ""), center=True)
                    self._set_item(target_table, row, 2, str(mat.get("Description") or ""))
            except Exception as exc:
                print(f"Build MC material call hydration error: {exc}")
        else:
            self.current_estimate_id = int(material_call_row.get("EstimateID") or 0) or None
            for row_data in material_call_items:
                row = self.package_table.rowCount()
                self.package_table.insertRow(row)
                self._set_item(self.package_table, row, 0, str(row_data.get("EstimateItemID") or row_data.get("MaterialCallItemID") or ""))
                self._set_item(self.package_table, row, 1, str(row_data.get("Quantity") or ""), center=True)
                self._set_item(self.package_table, row, 2, str(row_data.get("Description") or ""))
        self.build_rfq_material_call_id = int(material_call_row["MaterialCallID"])
        self.build_rfq_material_call_number = str(material_call_row.get("MaterialCallNumber") or f"MC#{material_call_row['MaterialCallID']}")
        self.build_rfq_material_call_estimate_id = int(material_call_row.get("EstimateID") or 0) or None
        self.build_rfq_material_call_source_type = source_type
        self.build_rfq_material_call_source_id = int(source_id or 0) or None
        self.build_rfq_material_call_signature = self._current_build_rfq_material_signature()
        self.build_rfq_material_call_is_stale = False
        self.build_rfq_material_call_unsaved = False
        self._refresh_build_rfq_material_call_context_status()

    def _clear_build_rfq_material_call_context(self) -> None:
        self.build_rfq_material_call_id = None
        self.build_rfq_material_call_number = ""
        self.build_rfq_material_call_estimate_id = None
        self.build_rfq_material_call_signature = ()
        self.build_rfq_material_call_is_stale = False
        self.build_rfq_material_call_source_type = self._current_build_mc_source_type() if hasattr(self, "build_mc_source_type_combo") else "Estimate"
        source_id = self.est_combo.currentData() if hasattr(self, "est_combo") else None
        self.build_rfq_material_call_source_id = int(source_id or 0) or None
        self.build_rfq_material_call_unsaved = False
        if hasattr(self, "build_rfq_material_call_label"):
            self.build_rfq_material_call_label.setText("Current Material Call: none")
        if hasattr(self, "build_rfq_material_call_status_label"):
            self.build_rfq_material_call_status_label.setText(
                "Click New Material Call to start a vendor-neutral package, then Save Material Call when the source and rows are ready."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #6c757d;")

    def _refresh_build_rfq_material_call_context_status(self) -> None:
        if not getattr(self, "build_rfq_material_call_label", None):
            return
        if self.build_rfq_material_call_unsaved and not self.build_rfq_material_call_id:
            label = self.build_rfq_material_call_number or "New / Unsaved"
            self.build_rfq_material_call_label.setText(f"Current Material Call: {label}")
            self.build_rfq_material_call_status_label.setText(
                "This is a staged vendor-neutral Material Call workspace. Save Material Call to write the parent package."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #1f4f99; font-weight: 600;")
            return
        if not self.build_rfq_material_call_id or not self.build_rfq_material_call_number:
            self.build_rfq_material_call_label.setText("Current Material Call: none")
            self.build_rfq_material_call_status_label.setText(
                "Click New Material Call to start a vendor-neutral package, then Save Material Call when the source and rows are ready."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #6c757d;")
            return
        self.build_rfq_material_call_label.setText(f"Current Material Call: {self.build_rfq_material_call_number}")
        current_signature = self._current_build_rfq_material_signature()
        signature_matches = (
            bool(current_signature)
            and current_signature == self.build_rfq_material_call_signature
            and str(self._current_build_mc_source_type()) == str(self.build_rfq_material_call_source_type or "")
            and int(self.est_combo.currentData() or 0) == int(self.build_rfq_material_call_source_id or 0)
        )
        self.build_rfq_material_call_is_stale = not signature_matches
        if self.build_rfq_material_call_is_stale:
            self.build_rfq_material_call_status_label.setText(
                "Material rows changed after save. Create/save a new Material Call or use a revision workflow."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #8a5a00; font-weight: 600;")
        else:
            self.build_rfq_material_call_status_label.setText(
                f"Material Call {self.build_rfq_material_call_number} matches the currently staged package."
            )
            self.build_rfq_material_call_status_label.setStyleSheet("color: #1f4f99; font-weight: 600;")

    def start_new_build_material_call(self, *, render_preview: bool = True) -> None:
        has_rows = self.master_table.rowCount() > 0 or self.package_table.rowCount() > 0
        has_unsaved = self.build_rfq_material_call_unsaved and not self.build_rfq_material_call_id
        if has_rows and has_unsaved:
            answer = QMessageBox.question(
                self,
                "New Material Call",
                "Clear the current unsaved Material Call workspace and start a new one?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.active_rfq_id = None
        self.current_vendor_id = None
        self._set_vendor_selection("")
        self.rfq_tree.clearSelection()
        if hasattr(self, "build_mc_source_type_combo"):
            self.build_mc_source_type_combo.blockSignals(True)
            self.build_mc_source_type_combo.setCurrentText("Estimate")
            self.build_mc_source_type_combo.blockSignals(False)
        self.refresh_build_mc_source_choices()
        self._set_build_mc_source_selection(None)
        self.master_table.setRowCount(0)
        self.package_table.setRowCount(0)
        self.current_estimate_id = None
        self._clear_build_rfq_material_call_context()
        self.build_rfq_material_call_unsaved = True
        number_preview_started_at = time.perf_counter()
        self.build_rfq_material_call_number = get_next_material_call_number_preview()
        _perf_log(
            "ui",
            "material_call.number_preview",
            number_preview_started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
        )
        self._refresh_build_rfq_material_call_context_status()
        if render_preview:
            self.render_rfq_preview_for_selection(force=True, reason="New Material Call started.")

    def on_build_mc_source_type_changed(self) -> None:
        self.active_rfq_id = None
        self.current_vendor_id = None
        self._set_vendor_selection("")
        self.refresh_build_mc_source_choices()
        self._load_build_mc_source_materials()
        if self.build_rfq_material_call_id:
            self._refresh_build_rfq_material_call_context_status()
        elif self.build_rfq_material_call_unsaved:
            self.build_rfq_material_call_source_type = self._current_build_mc_source_type()
            self.build_rfq_material_call_source_id = int(self.est_combo.currentData() or 0) or None
            self._refresh_build_rfq_material_call_context_status()
        else:
            self._clear_build_rfq_material_call_context()
        self.render_rfq_preview_for_selection(force=True, reason="Build MC source type changed.")

    def on_build_mc_source_changed(self) -> None:
        self.active_rfq_id = None
        self.current_vendor_id = None
        self._set_vendor_selection("")
        self._load_build_mc_source_materials()
        if self.build_rfq_material_call_id:
            self._refresh_build_rfq_material_call_context_status()
        elif self.build_rfq_material_call_unsaved:
            self.build_rfq_material_call_source_type = self._current_build_mc_source_type()
            self.build_rfq_material_call_source_id = int(self.est_combo.currentData() or 0) or None
            self._refresh_build_rfq_material_call_context_status()
        else:
            self._clear_build_rfq_material_call_context()
        self.render_rfq_preview_for_selection(force=True, reason="Build MC source changed.")

    def save_build_material_call(self) -> None:
        source_type = self._current_build_mc_source_type()
        source_id = self.est_combo.currentData()
        material_ids = self._current_build_rfq_material_selection_ids()
        if not source_id:
            QMessageBox.warning(
                self,
                "Save Material Call",
                f"Select a valid {self._build_mc_source_selector_label(source_type).rstrip(':').lower()} first.",
            )
            return
        if not material_ids:
            QMessageBox.warning(self, "Save Material Call", "Add at least one material row before saving the Material Call.")
            return
        if self.build_rfq_material_call_id and not self.build_rfq_material_call_is_stale:
            QMessageBox.information(
                self,
                "Save Material Call",
                f"Material Call {self.build_rfq_material_call_number} is already saved and matches the current package.",
            )
            return
        if source_type == "WorkOrder":
            QMessageBox.warning(
                self,
                "Save Material Call",
                "Work Order source selection is available here, but row staging for Work Order packages is still handled in Material Call.",
            )
            return
        try:
            material_call_id = create_material_call_from_estimate_items(
                int(source_id),
                material_ids,
                notes="Saved from RFQ Center Build MC from Estimate workspace.",
            )
            material_call = get_material_call(int(material_call_id)) or {}
            material_call_number = str(material_call.get("MaterialCallNumber") or f"MC#{material_call_id}")
            item_count = int(material_call.get("ItemCount") or len(material_ids))
            self.build_rfq_material_call_id = int(material_call_id)
            self.build_rfq_material_call_number = material_call_number
            self.build_rfq_material_call_estimate_id = int(material_call.get("EstimateID") or source_id)
            self.build_rfq_material_call_source_type = str(material_call.get("SourceType") or source_type)
            self.build_rfq_material_call_source_id = int(material_call.get("SourceID") or source_id)
            self.build_rfq_material_call_signature = self._current_build_rfq_material_signature()
            self.build_rfq_material_call_is_stale = False
            self.build_rfq_material_call_unsaved = False
            self.active_material_call_id = int(material_call_id)
            self.active_material_call_number = material_call_number
            self.active_rfq_id = None
            self._refresh_build_rfq_material_call_context_status()
            self.load_pipeline()
            self._select_material_call_in_pipeline(int(material_call_id))
            QMessageBox.information(
                self,
                "Material Call Saved",
                f"Material Call {material_call_number} saved with {item_count} item(s).",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save Material Call Failed", str(exc))

    def create_material_call_from_selected_estimate_materials(self) -> None:
        self.save_build_material_call()

    def _current_material_call_context_for_create_rfq(self) -> tuple[int | None, str, dict[str, Any] | None]:
        if self.active_material_call_id and self.active_material_call_row:
            return int(self.active_material_call_id), str(self.active_material_call_number or ""), dict(self.active_material_call_row)
        return None, "", None

    def _active_material_call_is_stale(self) -> bool:
        if self.active_material_call_id and int(self.active_material_call_id) == int(self.build_rfq_material_call_id or 0):
            return bool(self.build_rfq_material_call_is_stale)
        if self.active_material_call_id and int(self.active_material_call_id) == int(self.direct_material_call_id or 0):
            return bool(self.direct_material_call_is_stale)
        return False

    def generate_and_save_rfq(self) -> None:
        material_call_id, material_call_number, material_call_row = self._current_material_call_context_for_create_rfq()
        if not material_call_id:
            QMessageBox.warning(
                self,
                "Save RFQ Draft",
                "Select or create a Material Call first.",
            )
            return
        if self._active_material_call_is_stale():
            QMessageBox.warning(
                self,
                "Save RFQ Draft",
                "Material rows changed after Material Call creation. Recreate the Material Call before saving the RFQ.",
            )
            return

        vendor_name = str(self.create_rfq_vendor_combo.currentText() or self.selected_vendor_name or "").strip()
        if not vendor_name:
            QMessageBox.warning(self, "Save RFQ Draft", "Select a vendor before saving the RFQ draft.")
            return
        vendor_id = self.create_rfq_vendor_combo.currentData()
        if vendor_id in (None, "", 0):
            QMessageBox.warning(self, "Save RFQ Draft", "Select a valid vendor before saving the RFQ draft.")
            return
        if self._current_create_rfq_is_edit_locked():
            QMessageBox.warning(
                self,
                "Save RFQ Draft",
                "Locked or sent RFQs cannot be edited through Create RFQ.",
            )
            return

        try:
            rfq_id: int
            if self.active_rfq_id:
                if not self.current_vendor_id:
                    raise ValueError("Reload the selected RFQ before saving changes.")
                if int(self.current_vendor_id) != int(vendor_id):
                    raise ValueError(
                        "Vendor is fixed for an existing child RFQ. Select the Material Call parent to create a new vendor RFQ for a different vendor."
                    )
                update_material_call_backed_rfq_draft_header(
                    int(self.active_rfq_id),
                    due_date=self.due_date_edit.text().strip() or None,
                    rfq_notes=None,
                )
                rfq_id = int(self.active_rfq_id)
                success_message = f"RFQ #{rfq_id} draft updated under Material Call {material_call_number or material_call_id}."
            else:
                result = create_vendor_rfq_for_material_call(
                    int(material_call_id),
                    int(vendor_id),
                    due_date=self.due_date_edit.text().strip() or None,
                    notes=None,
                )
                rfq_id = int(result["rfq_id"])
                self.current_estimate_id = int(result.get("estimate_id") or self.current_estimate_id or 0) or self.current_estimate_id
                self.current_vendor_id = int(result.get("vendor_id") or vendor_id)
                self.selected_vendor_name = str(result.get("vendor_name") or vendor_name or "")
                success_message = (
                    f"RFQ #{rfq_id} draft saved under Material Call "
                    f"{result.get('material_call_number') or material_call_number or material_call_id}."
                )
            self.active_rfq_id = int(rfq_id)
            self.load_pipeline()
            self._select_rfq_in_pipeline(int(rfq_id))
            self.request_rfq_preview_refresh("RFQ draft saved.")
            QMessageBox.information(self, "Success", success_message)
        except ValueError as exc:
            QMessageBox.warning(self, "Save RFQ Draft", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def lock_current_rfq(self) -> None:
        material_call_id, material_call_number, _material_call_row = self._current_material_call_context_for_create_rfq()
        if not material_call_id:
            QMessageBox.warning(self, "Lock RFQ", "Select or create a Material Call first.")
            return
        if not self.active_rfq_id:
            QMessageBox.warning(self, "Lock RFQ", "Save the RFQ draft first.")
            return
        if not self.current_vendor_id:
            QMessageBox.warning(self, "Lock RFQ", "Select a saved vendor RFQ before locking it.")
            return
        if self._current_create_rfq_is_edit_locked():
            QMessageBox.information(self, "Lock RFQ", "The selected RFQ is already locked or sent.")
            return

        try:
            result = lock_rfq_draft(int(self.active_rfq_id))
            rfq_id = int(result["rfq_id"])
            self.active_rfq_id = rfq_id
            self.load_pipeline()
            self._select_rfq_in_pipeline(rfq_id)
            self._refresh_create_rfq_context_panel()
            self.request_rfq_preview_refresh(f"RFQ #{rfq_id} locked.")
            QMessageBox.information(
                self,
                "RFQ Locked",
                f"RFQ #{rfq_id} under Material Call {material_call_number or material_call_id} is now locked.",
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Lock RFQ", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Lock RFQ Failed", str(exc))

    def _render_rfq_preview_payload(self) -> dict:
        try:
            selected_template_ids = self.get_selected_rfq_template_ids()
            if self.active_rfq_id:
                preview = prepare_rfq_delivery_message(
                    int(self.active_rfq_id),
                    header_template_id=selected_template_ids["header_template_id"],
                    body_template_id=selected_template_ids["body_template_id"],
                    footer_template_id=selected_template_ids["footer_template_id"],
                )
                if preview.get("success"):
                    return preview
                return {
                    "success": False,
                    "reason": str(preview.get("reason") or "Preview unavailable."),
                    "Body": str(preview.get("reason") or "Preview unavailable."),
                    "TemplateUsed": preview.get("TemplateUsed") or "fallback",
                    "TemplateRenderWarning": preview.get("TemplateRenderWarning"),
                }

            material_call_id, _material_call_number, material_call_row = self._current_material_call_context_for_create_rfq()
            vendor_name = str(self.create_rfq_vendor_combo.currentText() or self.selected_vendor_name or "").strip()
            estimate_id = int((material_call_row or {}).get("EstimateID") or 0) or None
            material_rows = list(get_material_call_items(int(material_call_id)) or []) if material_call_id else []
            estimate_item_ids = [
                int(row["EstimateItemID"])
                for row in material_rows
                if row.get("EstimateItemID") not in (None, "", 0)
            ]
            if not estimate_id or not vendor_name or not estimate_item_ids:
                return {
                    "success": False,
                    "reason": "Select a Material Call and vendor, then save the RFQ draft if this package came from manual Material Call rows.",
                    "Body": "Select a Material Call and vendor, then save the RFQ draft if this package came from manual Material Call rows.",
                    "TemplateUsed": "fallback",
                    "TemplateRenderWarning": None,
                }
            preview = render_rfq_delivery_preview_context(
                estimate_id=int(estimate_id),
                vendor_name=vendor_name,
                due_date=self.due_date_edit.text().strip() or None,
                material_ids=estimate_item_ids,
                rfq_id=self.active_rfq_id,
                header_template_id=selected_template_ids["header_template_id"],
                body_template_id=selected_template_ids["body_template_id"],
                footer_template_id=selected_template_ids["footer_template_id"],
            )
            if not preview.get("success"):
                preview_text = build_rfq_preview_text(
                    int(estimate_id),
                    vendor_name,
                    due_date=self.due_date_edit.text().strip() or None,
                    material_ids=estimate_item_ids,
                    rfq_id=self.active_rfq_id,
                )
                preview["Body"] = preview_text
                preview["TemplateUsed"] = preview.get("TemplateUsed") or "fallback"
            return preview
        except Exception as exc:
            return {
                "success": False,
                "reason": f"Preview unavailable: {exc}",
                "Body": f"Preview unavailable:\n{exc}",
                "TemplateUsed": "fallback",
                "TemplateRenderWarning": str(exc),
            }

    def _iter_rfq_tree_items(self):
        for top_index in range(self.rfq_tree.topLevelItemCount()):
            parent = self.rfq_tree.topLevelItem(top_index)
            if parent is None:
                continue
            yield parent
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if child is not None:
                    yield child

    def _select_material_call_in_pipeline(self, material_call_id: int) -> QTreeWidgetItem | None:
        target = int(material_call_id)
        for item in self._iter_rfq_tree_items():
            payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if str(payload.get("kind") or "") != "material_call":
                continue
            row = payload.get("row") or {}
            if int(row.get("MaterialCallID") or 0) == target:
                if self.rfq_tree.currentItem() is not item:
                    self.rfq_tree.setCurrentItem(item)
                item.setExpanded(True)
                return item
        return None

    def _select_rfq_in_pipeline(self, rfq_id: int) -> QTreeWidgetItem | None:
        target = int(rfq_id)
        for item in self._iter_rfq_tree_items():
            payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if str(payload.get("kind") or "") not in {"rfq", "legacy_rfq"}:
                continue
            row = payload.get("row") or {}
            if int(row.get("PriceRequestID") or row.get("RFQID") or 0) == target:
                parent = item.parent()
                if parent is not None:
                    parent.setExpanded(True)
                if self.rfq_tree.currentItem() is not item:
                    self.rfq_tree.setCurrentItem(item)
                return item
        return None

    def _selected_rfq_ids(self) -> list[int]:
        rfq_ids: list[int] = []
        seen: set[int] = set()
        for item in self.rfq_tree.selectedItems():
            payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if str(payload.get("kind") or "") not in {"rfq", "legacy_rfq"}:
                continue
            row = payload.get("row") or {}
            rfq_id = int(row.get("PriceRequestID") or row.get("RFQID") or 0)
            if rfq_id and rfq_id not in seen:
                seen.add(rfq_id)
                rfq_ids.append(rfq_id)
        return rfq_ids

    def on_po_select(self) -> None:
        if self.rfq_tree.selectedItems():
            self.rfq_tree.clearSelection()
        if self._po_tree_selection_suppressed:
            return
        items = self.po_tree.selectedItems()
        if not items:
            return
        item = items[0]
        payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = str(payload.get("kind") or "")
        row_data = dict(payload.get("row") or {})
        if kind == "work_order":
            self._apply_active_work_order_context(row_data)
            self.build_po_readonly = False
            work_order_id = int(row_data.get("WorkOrderID") or 0) or None
            if hasattr(self, "po_work_order_combo"):
                combo_index = self.po_work_order_combo.findData(work_order_id) if work_order_id else -1
                self.po_work_order_combo.blockSignals(True)
                if combo_index >= 0:
                    self.po_work_order_combo.setCurrentIndex(combo_index)
                elif self.po_work_order_combo.count() > 0:
                    self.po_work_order_combo.setCurrentIndex(0)
                self.po_work_order_combo.blockSignals(False)
            self.on_po_work_order_selected()
            self.refresh_create_purchase_order_shell()
            self.refresh_receiving_metadata_panel()
            return
        po_id = int(row_data.get("PurchaseOrderID") or 0)
        status_text = str(row_data.get("Status") or "").strip()
        self._apply_active_purchase_order_context(row_data)
        try:
            self.load_material_request_purchase_order(po_id, select_tab=False)
        except Exception as exc:
            QMessageBox.critical(self, "Material Call Load Error", str(exc))
            return
        if status_text != "Draft":
            self.load_existing_purchase_order(po_id, select_tab=True)
        self.refresh_create_purchase_order_shell()
        self.refresh_receiving_metadata_panel()

    def _apply_rfq_tree_selection_light_context(
        self,
        item: QTreeWidgetItem | None,
        *,
        preview_reason: str = "",
    ) -> dict[str, Any]:
        if item is None:
            return {
                "kind": "none",
                "material_call_id": self.active_material_call_id,
                "rfq_id": self.active_rfq_id,
                "skipped": True,
                "reason": "no_item",
            }
        payload = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = str(payload.get("kind") or "")
        row_data = dict(payload.get("row") or {})

        if kind == "legacy_section":
            return {
                "kind": kind,
                "material_call_id": self.active_material_call_id,
                "rfq_id": self.active_rfq_id,
                "skipped": True,
                "reason": "legacy_section",
            }

        if kind == "material_call":
            self.active_rfq_id = None
            self.current_vendor_id = None
            self._apply_active_material_call_context(row_data)
            self._set_vendor_selection("")
            self._receive_quotes_loaded_rfq_id = None
            self._bid_compare_loaded_rfq_id = None
            self.request_rfq_preview_refresh(
                preview_reason or f"Material Call {self.active_material_call_number or row_data.get('MaterialCallID')} loaded."
            )
            return {
                "kind": kind,
                "material_call_id": self.active_material_call_id,
                "rfq_id": self.active_rfq_id,
                "skipped": False,
                "reason": "",
            }

        parent = item.parent()
        parent_payload = parent.data(0, Qt.ItemDataRole.UserRole) if parent is not None else {}
        parent_row = {}
        if str((parent_payload or {}).get("kind") or "") == "material_call":
            parent_row = dict((parent_payload or {}).get("row") or {})
            self._apply_active_material_call_context(parent_row)
        else:
            self._clear_active_material_call_context()
            self._refresh_create_rfq_context_panel()

        self._load_selected_rfq_context_from_pipeline_row(row_data, preview_reason=preview_reason)
        return {
            "kind": kind,
            "material_call_id": self.active_material_call_id,
            "rfq_id": self.active_rfq_id,
            "skipped": False,
            "reason": "",
        }

    def _apply_rfq_tree_selection_context(
        self,
        item: QTreeWidgetItem | None,
        *,
        preview_reason: str = "",
    ) -> None:
        started_at = time.perf_counter()
        light_context = self._apply_rfq_tree_selection_light_context(item, preview_reason=preview_reason)
        if light_context.get("skipped"):
            _perf_log(
                "ui",
                "rfq_viewer.context_load_skipped",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                kind=str(light_context.get("kind") or ""),
                material_call_id=light_context.get("material_call_id"),
                rfq_id=light_context.get("rfq_id"),
                skipped=True,
                reason=str(light_context.get("reason") or "") or None,
            )
            return

        self._hydrate_active_rfq_tree_selection_context(force_reload=False)
        _perf_log(
            "ui",
            "rfq_viewer.context_load",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            kind=str(light_context.get("kind") or ""),
            material_call_id=light_context.get("material_call_id"),
            rfq_id=light_context.get("rfq_id"),
            preview_mode="stale_only",
            skipped=False,
        )

    def on_rfq_select(self) -> None:
        started_at = time.perf_counter()
        if self._rfq_tree_selection_suppressed:
            _perf_log(
                "ui",
                "rfq_viewer.on_rfq_select",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                skipped=True,
                reason="selection_suppressed",
            )
            return
        if self.po_tree.selectedItems():
            self.po_tree.clearSelection()
        items = self.rfq_tree.selectedItems()
        if not items:
            _perf_log(
                "ui",
                "rfq_viewer.on_rfq_select",
                started_at,
                center=self.procurement_center,
                active_tab=self._active_procurement_tab_name(),
                skipped=True,
                reason="no_selection",
            )
            return

        self._apply_rfq_tree_selection_context(items[0])
        _perf_log(
            "ui",
            "rfq_viewer.on_rfq_select",
            started_at,
            center=self.procurement_center,
            active_tab=self._active_procurement_tab_name(),
            skipped=False,
        )

    def _load_selected_rfq_context_from_pipeline_row(
        self,
        row_data: dict[str, Any],
        *,
        preview_reason: str = "",
    ) -> None:
        rfq_id = int(row_data.get("PriceRequestID") or row_data.get("RFQID") or 0)
        if not rfq_id:
            return

        self.active_rfq_id = rfq_id
        parent_estimate_id = int((self.active_material_call_row or {}).get("EstimateID") or 0) or None
        self.current_estimate_id = int(row_data.get("EstimateID") or parent_estimate_id or 0) or None
        self.current_vendor_id = int(row_data.get("VendorID") or 0) or None
        self._set_vendor_selection(str(row_data.get("VendorName") or ""))
        self.build_po_readonly = False
        self.active_po_id = None
        self.request_rfq_preview_refresh(preview_reason or f"RFQ #{rfq_id} loaded.")

    def on_carry_click(self) -> None:
        try:
            if not self.active_rfq_id:
                QMessageBox.warning(self, "Select RFQ", "Select a child RFQ before saving a quote response.")
                return
            if self.receive_quotes_edit_locked:
                QMessageBox.information(self, "Quote Locked", "This quote is locked and can no longer be edited.")
                return
            all_items = []
            carried = []
            for row in range(self.matrix_table.rowCount()):
                pr_item_id = int(self.matrix_table.item(row, 0).text())
                material_id = int(self.matrix_table.item(row, 1).text())
                quote_unit = float(str(self.matrix_table.item(row, 5).text()).replace("$", "").strip() or 0)
                all_items.append((pr_item_id, material_id, quote_unit))
                if self.matrix_table.item(row, 2).text() == "[âœ“]":
                    carried.append(material_id)
            rfq_id = int(self.active_rfq_id)
            result = save_quote_response(
                rfq_id,
                self.quote_no_edit.text().strip(),
                self.quote_date_edit.text().strip(),
                all_items,
                self.selected_quote_path,
            )
            if result.get("saved"):
                QMessageBox.information(
                    self,
                    "Quote Saved",
                    "Quote saved for this RFQ. "
                    "Receive Quotes records vendor quote data only. "
                    "Use Bid Compare to carry pricing and update estimate pricing.",
                )
            self.load_receive_quotes_for_active_selection(force_reload=True)
            if self._is_bid_compare_tab_active():
                self.load_bid_compare_for_active_selection(force_reload=True)
            else:
                self._bid_compare_loaded_rfq_id = None
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
