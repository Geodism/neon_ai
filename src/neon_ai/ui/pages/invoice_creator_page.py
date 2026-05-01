from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
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

from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.models import DocumentTemplateKind
from neon_ai.document_control.repository import DocumentControlRepository
from neon_ai.database.invoices import (
    get_invoice_detail,
    get_invoice_header_data,
    get_latest_invoice_for_workorder,
    get_unbilled_labor,
    get_unbilled_materials,
    get_unbilled_workorders,
    lock_and_export_invoice,
    mark_invoice_sent,
    save_invoice_draft,
)
from neon_ai.invoice_generator import generate_invoice_docx
from neon_ai.services.document_generation_service import (
    export_customer_invoice_document_draft,
    render_customer_invoice_document_from_template_selection,
)
from neon_ai.services.customer_invoice_document_draft_service import (
    get_invoice_draft_for_display,
    get_or_create_active_invoice_draft,
    lock_invoice_draft,
    record_invoice_draft_exported_file,
    save_invoice_draft as save_invoice_document_draft,
)
from neon_ai.services.customer_invoice_document_send_service import can_send_customer_invoice_document_draft
from neon_ai.ui.widgets.rich_text_toolbar import RichTextToolbar

CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE = "CUSTOMER_INVOICE"
CUSTOMER_INVOICE_DRAFT_WORKSPACE_CONTEXT = "CUSTOMER_INVOICE_DRAFT_WORKSPACE"


def normalize_billing_mode(raw_value):
    text = str(raw_value or "").strip().lower()
    if text in {"t&m", "time and materials", "time & materials"}:
        return "T&M"
    if text in {"stipulated", "stipulated price", "fixed price"}:
        return "Stipulated"
    if "time" in text and "material" in text:
        return "T&M"
    if text:
        return "Stipulated"
    return raw_value


class InvoiceCreatorPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_wo_id: int | None = None
        self.current_invoice_id: int | None = None
        self.current_invoice_header: dict | None = None
        self.current_doc_path: str | None = None
        self.current_invoice_document_draft: dict | None = None
        self._invoice_template_choices_by_kind: dict[str, list[object]] = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        self._invoice_template_defaults_by_kind: dict[str, int | None] = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }
        self._suspend_invoice_template_selection_tracking = False
        self._pending_invoice_template_selection_change = False
        self._last_rendered_invoice_template_ids: dict[str, int | None] = {
            "header_template_id": None,
            "body_template_id": None,
            "footer_template_id": None,
        }
        self.tm_summary_label: QLabel | None = None
        self.labor_table: QTableWidget | None = None
        self.mat_table: QTableWidget | None = None
        self.stip_summary_label: QLabel | None = None
        self.l_perc_field: QLineEdit | None = None
        self.m_perc_field: QLineEdit | None = None
        self.l_note_text: QPlainTextEdit | None = None
        self.m_note_text: QPlainTextEdit | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_pipeline_panel())
        splitter.addWidget(self._build_crafting_workspace())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

        self._reset_invoice_template_dropdowns()
        self._clear_document_draft_workspace()
        self._refresh_invoice_document_metadata()
        self._refresh_workflow_status_label()

    def _build_pipeline_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("Invoice Pipeline")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        helper = QLabel("Select a work order or invoice candidate to keep billing data and customer-facing document work side by side.")
        helper.setWordWrap(True)
        helper.setStyleSheet("color: #555;")
        layout.addWidget(helper)

        self.pipeline_table = QTableWidget(0, 3)
        self.pipeline_table.setHorizontalHeaderLabels(["WO #", "Customer", "Site"])
        self.pipeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pipeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pipeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pipeline_table.verticalHeader().setVisible(False)
        self.pipeline_table.setColumnWidth(0, 60)
        self.pipeline_table.horizontalHeader().setStretchLastSection(True)
        self.pipeline_table.itemSelectionChanged.connect(self.on_wo_select)
        layout.addWidget(self.pipeline_table, 1)
        return panel

    def _build_crafting_workspace(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("Crafting Workspace")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        helper = QLabel(
            "Use Invoice Data for the structured billing record and Invoice Document for the customer-facing draft artifact."
        )
        helper.setWordWrap(True)
        helper.setStyleSheet("color: #555;")
        layout.addWidget(helper)

        self.workflow_status_label = QLabel("Select a work order to begin.")
        self.workflow_status_label.setWordWrap(True)
        self.workflow_status_label.setStyleSheet("color: #5f6368;")
        layout.addWidget(self.workflow_status_label)

        self.invoice_workspace_tabs = QTabWidget()
        self.invoice_workspace_tabs.addTab(self._build_invoice_data_tab(), "Invoice Data")
        self.invoice_workspace_tabs.addTab(self._build_invoice_document_tab(), "Invoice Document")
        layout.addWidget(self.invoice_workspace_tabs, 1)
        return panel

    def _build_invoice_data_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        gate_group = QGroupBox("Billing Gatekeeper")
        gate_layout = QVBoxLayout(gate_group)
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(QLabel("Select Billing Mode:"))
        self.rb_tm = QRadioButton("Time & Materials")
        self.rb_stip = QRadioButton("Stipulated Price")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_tm)
        self.mode_group.addButton(self.rb_stip)
        self.rb_tm.toggled.connect(self.on_mode_change)
        self.rb_stip.toggled.connect(self.on_mode_change)
        mode_layout.addWidget(self.rb_tm)
        mode_layout.addWidget(self.rb_stip)
        mode_layout.addStretch(1)
        gate_layout.addWidget(mode_row)
        self.progress_label = QLabel("Select a work order to begin.")
        gate_layout.addWidget(self.progress_label)
        layout.addWidget(gate_group)

        scope_group = QGroupBox("Living Scope of Work")
        scope_layout = QVBoxLayout(scope_group)
        self.scope_text = QPlainTextEdit()
        self.scope_text.setFixedHeight(120)
        scope_layout.addWidget(self.scope_text)
        layout.addWidget(scope_group)

        self.workspace_container = QWidget()
        self.workspace_layout = QVBoxLayout(self.workspace_container)
        self.workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.setSpacing(0)
        layout.addWidget(self.workspace_container, 1)

        data_actions = QFrame()
        data_actions_layout = QHBoxLayout(data_actions)
        data_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.invoice_record_action_label = QLabel(
            "Step 1: create or save the structured invoice record here. Step 2: switch to Invoice Document to load or create the customer-facing draft."
        )
        self.invoice_record_action_label.setWordWrap(True)
        self.invoice_record_action_label.setStyleSheet("color: #555;")
        data_actions_layout.addWidget(self.invoice_record_action_label, 1)
        data_actions_layout.addStretch(1)
        self.btn_save_draft = QPushButton("Create / Save Invoice Record")
        self.btn_save_draft.setStyleSheet("font-weight: 700; padding: 6px 14px;")
        self.btn_save_draft.clicked.connect(self.on_save_draft)
        data_actions_layout.addWidget(self.btn_save_draft)
        layout.addWidget(data_actions)
        return panel

    def _build_invoice_document_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top_group = QGroupBox("Invoice Metadata & Template Selection")
        top_layout = QGridLayout(top_group)
        top_layout.setHorizontalSpacing(12)
        top_layout.setVerticalSpacing(8)

        self.invoice_customer_value = self._make_meta_value_label()
        self.invoice_site_address_value = self._make_meta_value_label()
        self.invoice_value_value = self._make_meta_value_label()
        self.invoice_draft_status_value = self._make_meta_value_label("No draft loaded.")
        self.invoice_site_work_order_value = self._make_meta_value_label()
        self.invoice_contact_value = self._make_meta_value_label()
        self.invoice_created_value = self._make_meta_value_label()
        self.invoice_status_value = self._make_meta_value_label()

        self.invoice_header_template_combo = QComboBox()
        self.invoice_body_template_combo = QComboBox()
        self.invoice_footer_template_combo = QComboBox()
        self.invoice_header_template_combo.currentIndexChanged.connect(self._on_invoice_template_selection_changed)
        self.invoice_body_template_combo.currentIndexChanged.connect(self._on_invoice_template_selection_changed)
        self.invoice_footer_template_combo.currentIndexChanged.connect(self._on_invoice_template_selection_changed)

        top_layout.addWidget(self._meta_label("Customer"), 0, 0)
        top_layout.addWidget(self.invoice_customer_value, 0, 1)
        top_layout.addWidget(self._meta_label("Site / Work Order"), 0, 2)
        top_layout.addWidget(self.invoice_site_work_order_value, 0, 3)

        top_layout.addWidget(self._meta_label("Site Address"), 1, 0)
        top_layout.addWidget(self.invoice_site_address_value, 1, 1)
        top_layout.addWidget(self._meta_label("Contact"), 1, 2)
        top_layout.addWidget(self.invoice_contact_value, 1, 3)

        top_layout.addWidget(self._meta_label("Invoice Value"), 2, 0)
        top_layout.addWidget(self.invoice_value_value, 2, 1)
        top_layout.addWidget(self._meta_label("Date Created"), 2, 2)
        top_layout.addWidget(self.invoice_created_value, 2, 3)

        top_layout.addWidget(self._meta_label("Draft Status"), 3, 0)
        top_layout.addWidget(self.invoice_draft_status_value, 3, 1)
        top_layout.addWidget(self._meta_label("Invoice Status"), 3, 2)
        top_layout.addWidget(self.invoice_status_value, 3, 3)

        top_layout.addWidget(self._meta_label("Header Template"), 4, 0)
        top_layout.addWidget(self.invoice_header_template_combo, 4, 1)
        top_layout.addWidget(self._meta_label("Invoice Template"), 4, 2)
        top_layout.addWidget(self.invoice_body_template_combo, 4, 3)

        top_layout.addWidget(self._meta_label("Footer Template"), 5, 0)
        top_layout.addWidget(self.invoice_footer_template_combo, 5, 1)
        top_layout.addWidget(self._meta_label("Draft Action"), 5, 2)
        self.btn_load_document_draft = QPushButton("Load/Create Draft")
        self.btn_load_document_draft.clicked.connect(self.on_load_or_create_document_draft)
        top_layout.addWidget(self.btn_load_document_draft, 5, 3)

        self.document_draft_template_status_label = self._make_meta_value_label("")
        self.document_draft_template_status_label.setStyleSheet("color: #8a5a00; padding: 2px 0;")
        top_layout.addWidget(self.document_draft_template_status_label, 6, 0, 1, 4)
        layout.addWidget(top_group)

        draft_group = QGroupBox("Invoice Document Draft")
        draft_layout = QVBoxLayout(draft_group)
        self.document_draft_status_label = QLabel("No invoice document draft loaded.")
        self.document_draft_status_label.setWordWrap(True)
        draft_layout.addWidget(self.document_draft_status_label)

        self.document_draft_editor = QTextEdit()
        self.document_draft_editor.setAcceptRichText(True)
        self.document_draft_editor.setPlaceholderText(
            "Load or create an invoice document draft to edit customer-facing invoice content here."
        )
        self.document_draft_editor.setReadOnly(True)
        self.document_draft_toolbar = RichTextToolbar(self.document_draft_editor, parent=self)
        draft_layout.addWidget(self.document_draft_toolbar)
        draft_layout.addWidget(self.document_draft_editor, 1)
        layout.addWidget(draft_group, 1)

        action_frame = QFrame()
        action_layout = QVBoxLayout(action_frame)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)

        primary_row = QHBoxLayout()
        primary_row.setContentsMargins(0, 0, 0, 0)
        self.btn_save_document_draft = QPushButton("Save Draft")
        self.btn_save_document_draft.clicked.connect(self.on_save_document_draft)
        primary_row.addWidget(self.btn_save_document_draft)
        self.btn_lock_document_draft = QPushButton("Lock Draft")
        self.btn_lock_document_draft.clicked.connect(self.on_lock_document_draft)
        primary_row.addWidget(self.btn_lock_document_draft)
        self.btn_render_document_draft_templates = QPushButton("Render From Selected Templates")
        self.btn_render_document_draft_templates.clicked.connect(self.on_render_document_draft_templates)
        primary_row.addWidget(self.btn_render_document_draft_templates)
        self.btn_preview_document_draft = QPushButton("Preview")
        self.btn_preview_document_draft.clicked.connect(self.on_preview_document_draft)
        primary_row.addWidget(self.btn_preview_document_draft)
        primary_row.addStretch(1)
        action_layout.addLayout(primary_row)

        secondary_row = QHBoxLayout()
        secondary_row.setContentsMargins(0, 0, 0, 0)
        self.btn_print_document_draft = QPushButton("Print")
        self.btn_print_document_draft.clicked.connect(self.on_print_document_draft)
        secondary_row.addWidget(self.btn_print_document_draft)
        self.btn_export_document_draft = QPushButton("Export")
        self.btn_export_document_draft.clicked.connect(self.on_export_document_draft)
        secondary_row.addWidget(self.btn_export_document_draft)
        self.btn_send_document_draft = QPushButton("Send to Customer")
        self.btn_send_document_draft.clicked.connect(self.on_validate_send_document_draft)
        self.btn_send_document_draft.setEnabled(False)
        secondary_row.addWidget(self.btn_send_document_draft)
        secondary_row.addStretch(1)
        action_layout.addLayout(secondary_row)

        layout.addWidget(action_frame)

        self.document_draft_send_status_label = QLabel("No invoice document draft loaded.")
        self.document_draft_send_status_label.setWordWrap(True)
        self.document_draft_send_status_label.setStyleSheet("color: #5f6368;")
        layout.addWidget(self.document_draft_send_status_label)

        legacy_group = QGroupBox("Legacy / Staged Actions")
        legacy_layout = QVBoxLayout(legacy_group)
        self.document_draft_warning_label = QLabel(
            "Legacy invoice export/send/review actions remain available during transition. The new invoice document draft workflow does not replace them yet."
        )
        self.document_draft_warning_label.setWordWrap(True)
        self.document_draft_warning_label.setStyleSheet("color: #7a5c00;")
        legacy_layout.addWidget(self.document_draft_warning_label)

        legacy_button_row = QHBoxLayout()
        legacy_button_row.setContentsMargins(0, 0, 0, 0)
        self.btn_export = QPushButton("Export & Lock")
        self.btn_export.clicked.connect(self.on_export_final)
        legacy_button_row.addWidget(self.btn_export)
        self.btn_send = QPushButton("Send Invoice")
        self.btn_send.clicked.connect(self.on_send_invoice)
        legacy_button_row.addWidget(self.btn_send)
        self.btn_review = QPushButton("Review Invoice")
        self.btn_review.clicked.connect(self.on_review_invoice)
        legacy_button_row.addWidget(self.btn_review)
        legacy_button_row.addStretch(1)
        legacy_layout.addLayout(legacy_button_row)
        layout.addWidget(legacy_group)

        return panel

    def _clear_workspace(self) -> None:
        while self.workspace_layout.count():
            item = self.workspace_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.tm_summary_label = None
        self.labor_table = None
        self.mat_table = None
        self.stip_summary_label = None
        self.l_perc_field = None
        self.m_perc_field = None
        self.l_note_text = None
        self.m_note_text = None

    def _refresh_invoice_document_metadata(self, header: dict | None = None) -> None:
        current_header = header if header is not None else (self.current_invoice_header or {})
        invoice_value = current_header.get("CustomerInvoiceAmount")
        invoice_created = current_header.get("CustomerInvoiceDate")
        customer_name = str(current_header.get("CustomerName") or "").strip()
        site_name = str(current_header.get("SiteName") or "").strip()
        work_order_id = current_header.get("WorkOrderID") or self.current_wo_id
        site_work_order_parts = []
        if site_name:
            site_work_order_parts.append(site_name)
        if work_order_id:
            site_work_order_parts.append(f"WO #{work_order_id}")

        draft_status_text = self._draft_status_summary_text()
        if self.current_invoice_document_draft:
            draft_id = self.current_invoice_document_draft.get("CustomerInvoiceDocumentDraftID")
            status = str(self.current_invoice_document_draft.get("DraftStatus") or "Draft")
            draft_status_text = f"{status} (Draft #{draft_id})" if draft_id else status

        self.invoice_customer_value.setText(customer_name or "—")
        self.invoice_site_address_value.setText(str(current_header.get("SiteAddress") or "—"))
        self.invoice_value_value.setText(
            f"${float(invoice_value or 0):,.2f}" if invoice_value not in (None, "") else "—"
        )
        self.invoice_draft_status_value.setText(draft_status_text or "—")
        self.invoice_site_work_order_value.setText(" | ".join(site_work_order_parts) or "—")
        self.invoice_contact_value.setText(str(current_header.get("CustomerEmail") or "—"))
        self.invoice_created_value.setText(str(invoice_created or "—"))
        self.invoice_status_value.setText(str(current_header.get("InvoiceStatus") or "—"))
        self._refresh_workflow_status_label()

    def _make_meta_value_label(self, text: str = "—") -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet("padding: 2px 0;")
        return label

    def _meta_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600;")
        return label

    def _draft_status_summary_text(self) -> str:
        if self.current_invoice_document_draft:
            draft_id = self.current_invoice_document_draft.get("CustomerInvoiceDocumentDraftID")
            status = str(self.current_invoice_document_draft.get("DraftStatus") or "Draft")
            return f"{status} (Draft #{draft_id})" if draft_id else status
        if self.current_invoice_id:
            return "No document draft loaded."
        if self.current_wo_id:
            return "No invoice record."
        return "No draft loaded."

    def _empty_document_draft_status_message(self) -> str:
        if self.current_invoice_document_draft:
            return "Invoice document draft loaded."
        if self.current_invoice_id:
            return "Invoice selected. No document draft loaded."
        if self.current_wo_id:
            return "No invoice record exists. Create/save invoice data first."
        return "No invoice document draft loaded."

    def _empty_document_draft_send_message(self) -> str:
        if self.current_invoice_document_draft:
            return ""
        if self.current_invoice_id:
            return "Load or create an invoice document draft first."
        if self.current_wo_id:
            return "Create/save invoice data first."
        return "No invoice document draft loaded."

    def _template_combo_placeholder(self) -> str:
        if self.current_invoice_id:
            return "No active invoice templates"
        if self.current_wo_id:
            return "Create/save invoice data first"
        return "Select an invoice first"

    def _refresh_workflow_status_label(self) -> None:
        if not self.current_wo_id:
            self.workflow_status_label.setText("Select a work order to begin.")
            self.btn_save_draft.setEnabled(False)
            self.btn_save_draft.setToolTip("Select a work order first.")
            return

        self.btn_save_draft.setEnabled(True)
        invoice_status = str((self.current_invoice_header or {}).get("InvoiceStatus") or "").strip()
        if self.current_invoice_document_draft:
            self.btn_save_draft.setToolTip("Update the structured invoice record for this work order.")
            draft_status = str(self.current_invoice_document_draft.get("DraftStatus") or "Draft").strip()
            if invoice_status in {"Sent", "Paid"}:
                self.workflow_status_label.setText(
                    f"Invoice document draft loaded. Invoice is {invoice_status.lower()}, so some editing may be restricted."
                )
            else:
                self.workflow_status_label.setText("Invoice document draft loaded.")
            return

        if self.current_invoice_id:
            self.btn_save_draft.setToolTip("Update the structured invoice record for this work order.")
            if invoice_status in {"Sent", "Paid"}:
                self.workflow_status_label.setText(
                    f"Invoice data saved. Invoice is {invoice_status.lower()}, so document actions may be limited."
                )
            else:
                self.workflow_status_label.setText("Invoice data saved. Document draft can now be created.")
            return

        self.btn_save_draft.setToolTip(
            "Create the structured invoice record for this work order so the Invoice Document tab can be used."
        )
        self.workflow_status_label.setText("Work order selected. Create/save invoice data first.")

    def _clear_mode_selection(self) -> None:
        self.mode_group.setExclusive(False)
        self.rb_tm.setChecked(False)
        self.rb_stip.setChecked(False)
        self.mode_group.setExclusive(True)

    def _current_mode(self) -> str:
        if self.rb_tm.isChecked():
            return "T&M"
        if self.rb_stip.isChecked():
            return "Stipulated"
        return ""

    def _set_mode(self, mode: str) -> None:
        if mode == "T&M":
            self.rb_tm.setChecked(True)
        elif mode == "Stipulated":
            self.rb_stip.setChecked(True)
        else:
            self._clear_mode_selection()

    def on_mode_change(self) -> None:
        mode = self._current_mode()
        self._clear_workspace()
        if mode == "T&M":
            self.build_tm_ui()
        elif mode == "Stipulated":
            self.build_stipulated_ui()

    def build_tm_ui(self) -> None:
        container = QGroupBox("Labor & Material Crafting Table")
        layout = QVBoxLayout(container)
        self.tm_summary_label = QLabel("Unbilled labor and received materials will appear below.")
        self.tm_summary_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.tm_summary_label)

        layout.addWidget(QLabel("Labor Logs"))
        self.labor_table = QTableWidget(0, 4)
        self.labor_table.setHorizontalHeaderLabels(["Desc", "Hours", "Rate", "Total"])
        self.labor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.labor_table.verticalHeader().setVisible(False)
        self.labor_table.setColumnWidth(0, 340)
        self.labor_table.setColumnWidth(1, 80)
        self.labor_table.setColumnWidth(2, 90)
        self.labor_table.setColumnWidth(3, 100)
        layout.addWidget(self.labor_table)

        layout.addWidget(QLabel("Received Materials"))
        self.mat_table = QTableWidget(0, 5)
        self.mat_table.setHorizontalHeaderLabels(["Desc", "Qty", "Cost", "Source", "Total"])
        self.mat_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mat_table.verticalHeader().setVisible(False)
        self.mat_table.setColumnWidth(0, 340)
        self.mat_table.setColumnWidth(1, 80)
        self.mat_table.setColumnWidth(2, 90)
        self.mat_table.setColumnWidth(3, 90)
        self.mat_table.setColumnWidth(4, 100)
        layout.addWidget(self.mat_table)

        self.workspace_layout.addWidget(container)

    def build_stipulated_ui(self) -> None:
        container = QGroupBox("Stipulated Progress Claim")
        layout = QVBoxLayout(container)

        self.stip_summary_label = QLabel("Previously invoiced on this contract: $0.00 (0.0%)")
        self.stip_summary_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.stip_summary_label)

        labor_block = QWidget()
        labor_layout = QVBoxLayout(labor_block)
        labor_row = QWidget()
        labor_row_layout = QHBoxLayout(labor_row)
        labor_row_layout.setContentsMargins(0, 0, 0, 0)
        labor_row_layout.addWidget(QLabel("Labor %:"))
        self.l_perc_field = QLineEdit("0.0")
        self.l_perc_field.setMaximumWidth(80)
        labor_row_layout.addWidget(self.l_perc_field)
        labor_row_layout.addStretch(1)
        labor_layout.addWidget(labor_row)
        labor_layout.addWidget(QLabel("Labor Milestone Notes:"))
        self.l_note_text = QPlainTextEdit()
        self.l_note_text.setFixedHeight(70)
        labor_layout.addWidget(self.l_note_text)
        layout.addWidget(labor_block)

        mat_block = QWidget()
        mat_layout = QVBoxLayout(mat_block)
        mat_row = QWidget()
        mat_row_layout = QHBoxLayout(mat_row)
        mat_row_layout.setContentsMargins(0, 0, 0, 0)
        mat_row_layout.addWidget(QLabel("Material %:"))
        self.m_perc_field = QLineEdit("0.0")
        self.m_perc_field.setMaximumWidth(80)
        mat_row_layout.addWidget(self.m_perc_field)
        mat_row_layout.addStretch(1)
        mat_layout.addWidget(mat_row)
        mat_layout.addWidget(QLabel("Material Milestone Notes:"))
        self.m_note_text = QPlainTextEdit()
        self.m_note_text.setFixedHeight(70)
        mat_layout.addWidget(self.m_note_text)
        layout.addWidget(mat_block)

        self.workspace_layout.addWidget(container)

    def on_wo_select(self) -> None:
        selection = self.pipeline_table.selectedItems()
        if not selection:
            return

        row = self.pipeline_table.row(selection[0])
        self.current_wo_id = int(self.pipeline_table.item(row, 0).text())
        self.current_invoice_id = None
        self.current_invoice_header = None
        self.current_doc_path = None
        self.current_invoice_document_draft = None
        self.scope_text.setPlainText("")
        self._clear_workspace()
        self._clear_document_draft_workspace()
        self._reset_invoice_template_dropdowns()

        try:
            header = get_invoice_header_data(self.current_wo_id)
            if not header:
                self._refresh_invoice_document_metadata()
                return

            latest_invoice = get_latest_invoice_for_workorder(self.current_wo_id)
            if latest_invoice and not header.get("CustomerInvoiceId"):
                header["CustomerInvoiceId"] = latest_invoice.get("CustomerInvoiceId")
                header["InvoiceStatus"] = latest_invoice.get("InvoiceStatus") or header.get("InvoiceStatus")
                header["CustomerInvoiceDocPath"] = (
                    latest_invoice.get("CustomerInvoiceDocPath") or header.get("CustomerInvoiceDocPath")
                )
                header["CustomerInvoiceAmount"] = (
                    latest_invoice.get("CustomerInvoiceAmount") or header.get("CustomerInvoiceAmount")
                )
                header["CustomerInvoiceDate"] = (
                    latest_invoice.get("CustomerInvoiceDate") or header.get("CustomerInvoiceDate")
                )

            self.current_invoice_header = header
            self.scope_text.setPlainText(str(header.get("Scope") or ""))
            billing_mode = normalize_billing_mode(header.get("BillingType"))
            self.current_invoice_id = header.get("CustomerInvoiceId")
            self.current_doc_path = header.get("CustomerInvoiceDocPath")
            self.progress_label.setText(
                "Previously invoiced: "
                f"${float(header.get('PreviouslyInvoicedAmount') or 0):,.2f} | "
                f"Stipulated billed to date: {float(header.get('PreviouslyInvoicedStipulatedPercent') or 0):.1f}%"
            )

            if billing_mode in ("T&M", "Stipulated"):
                self.rb_tm.setEnabled(False)
                self.rb_stip.setEnabled(False)
                self._set_mode(billing_mode)
                if billing_mode == "T&M":
                    self.load_tm_workspace()
                else:
                    self.load_stipulated_workspace(header)
            else:
                self.rb_tm.setEnabled(True)
                self.rb_stip.setEnabled(True)
                self._clear_mode_selection()
            if self.current_invoice_id:
                self._populate_invoice_template_dropdowns()
            else:
                self._reset_invoice_template_dropdowns()
            self._refresh_invoice_document_metadata(header)
            self._load_existing_document_draft_if_any()
        except Exception as exc:
            QMessageBox.critical(self, "Data Error", str(exc))

    def load_tm_workspace(self) -> None:
        if self.labor_table is None or self.mat_table is None or self.tm_summary_label is None:
            self.build_tm_ui()

        labor_rows = get_unbilled_labor(self.current_wo_id)
        material_rows = get_unbilled_materials(self.current_wo_id)
        self.labor_table.setRowCount(0)
        self.mat_table.setRowCount(0)

        labor_total = 0.0
        for row in labor_rows:
            total = float(row.get("LineTotal") or 0)
            labor_total += total
            desc = f"{row.get('DateWorked')} | {row.get('EmployeeName')} | {row.get('TaskName') or 'Task'}"
            table_row = self.labor_table.rowCount()
            self.labor_table.insertRow(table_row)
            values = [
                desc,
                f"{float(row.get('HoursWorked') or 0):.2f}",
                f"${float(row.get('HourlyRate') or 0):,.2f}",
                f"${total:,.2f}",
            ]
            for column, value in enumerate(values):
                self.labor_table.setItem(table_row, column, QTableWidgetItem(value))

        material_total = 0.0
        for row in material_rows:
            total = float(row.get("LineTotal") or 0)
            material_total += total
            desc = f"PO #{row.get('PurchaseOrderID')} | {row.get('Description') or ''}"
            table_row = self.mat_table.rowCount()
            self.mat_table.insertRow(table_row)
            values = [
                desc,
                f"{float(row.get('Qty') or 0):.2f}",
                f"${float(row.get('Cost') or 0):,.2f}",
                "Received",
                f"${total:,.2f}",
            ]
            for column, value in enumerate(values):
                self.mat_table.setItem(table_row, column, QTableWidgetItem(value))

        self.tm_summary_label.setText(
            "Labor ready: "
            f"${labor_total:,.2f} | Materials ready: ${material_total:,.2f} | "
            f"Draft total: ${(labor_total + material_total):,.2f}"
        )

    def load_stipulated_workspace(self, header) -> None:
        if self.l_perc_field is None or self.m_perc_field is None or self.stip_summary_label is None:
            self.build_stipulated_ui()

        self.l_perc_field.setText(str(float(header.get("LaborPercent") or 0)))
        self.m_perc_field.setText(str(float(header.get("MaterialPercent") or 0)))
        self.l_note_text.setPlainText(str(header.get("LaborMilestoneNote") or ""))
        self.m_note_text.setPlainText(str(header.get("MaterialMilestoneNote") or ""))
        self.stip_summary_label.setText(
            "Previously invoiced on this contract: "
            f"${float(header.get('PreviouslyInvoicedAmount') or 0):,.2f} "
            f"({float(header.get('PreviouslyInvoicedStipulatedPercent') or 0):.1f}%)"
        )

    def build_current_invoice_payload(self):
        mode = self._current_mode()
        data = {
            "mode": mode,
            "scope": self.scope_text.toPlainText().strip(),
            "total": 0.0,
            "l_perc": 0.0,
            "m_perc": 0.0,
            "l_note": "",
            "m_note": "",
            "labor_lines": [],
            "material_lines": [],
            "review_summary": "",
        }

        if mode == "Stipulated":
            header = get_invoice_header_data(self.current_wo_id)
            data["l_perc"] = float((self.l_perc_field.text() if self.l_perc_field else "0") or 0)
            data["m_perc"] = float((self.m_perc_field.text() if self.m_perc_field else "0") or 0)
            data["l_note"] = self.l_note_text.toPlainText().strip() if self.l_note_text else ""
            data["m_note"] = self.m_note_text.toPlainText().strip() if self.m_note_text else ""
            est_total = float(header.get("EstimateTotal") or 0)
            data["total"] = (est_total * data["l_perc"] / 100.0) + (est_total * data["m_perc"] / 100.0)
        elif mode == "T&M":
            data["labor_lines"] = get_unbilled_labor(self.current_wo_id)
            data["material_lines"] = get_unbilled_materials(self.current_wo_id)
            data["total"] = sum(float(row.get("LineTotal") or 0) for row in data["labor_lines"]) + sum(
                float(row.get("LineTotal") or 0) for row in data["material_lines"]
            )
            data["review_summary"] = (
                f"{len(data['labor_lines'])} labor rows and {len(data['material_lines'])} material rows prepared for billing."
            )
        else:
            raise ValueError("Select a billing mode first.")
        return data

    def on_save_draft(self) -> None:
        if not self.current_wo_id:
            QMessageBox.warning(self, "Missing Work Order", "Select a work order first.")
            return
        try:
            preserved_work_order_id = int(self.current_wo_id)
            preserved_tab_index = self.invoice_workspace_tabs.currentIndex()
            data = self.build_current_invoice_payload()
            invoice = save_invoice_draft(self.current_wo_id, data)
            saved_invoice_id = invoice.get("CustomerInvoiceId")
            if not saved_invoice_id:
                raise RuntimeError("Invoice save did not return a CustomerInvoiceId.")
            self.current_invoice_id = int(saved_invoice_id)
            header = get_invoice_header_data(preserved_work_order_id) or {}
            if not header.get("CustomerInvoiceId"):
                header["CustomerInvoiceId"] = self.current_invoice_id
            if not header.get("InvoiceStatus"):
                header["InvoiceStatus"] = invoice.get("InvoiceStatus")
            self.current_invoice_header = header
            if header:
                self.current_doc_path = header.get("CustomerInvoiceDocPath")
                self._populate_invoice_template_dropdowns()
                self._refresh_invoice_document_metadata(header)
                self._load_existing_document_draft_if_any()
            self.refresh_data(
                preserve_work_order_id=preserved_work_order_id,
                preserve_tab_index=preserved_tab_index,
                show_reselect_warning=True,
            )
            print(
                "[InvoiceCreatorPage] Structured invoice saved | "
                f"WorkOrderID={preserved_work_order_id} | "
                f"CustomerInvoiceID={self.current_invoice_id} | "
                f"InvoiceStatus={(self.current_invoice_header or {}).get('InvoiceStatus') or invoice.get('InvoiceStatus') or 'Draft'} | "
                f"DocumentDraftEnabled={'yes' if bool(self.current_invoice_id) else 'no'}"
            )
            message = (
                "Invoice data saved. Document draft can now be created."
                f"\n\nCustomerInvoiceID: {self.current_invoice_id}"
            )
            QMessageBox.information(self, "Success", message)
        except ValueError as exc:
            QMessageBox.warning(self, "Invoice Data Incomplete", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))

    def on_export_final(self) -> None:
        if not self.current_wo_id:
            return
        confirmed = QMessageBox.question(self, "Finalize", "Lock the invoice and generate the document?")
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            data = self.build_current_invoice_payload()
            invoice = save_invoice_draft(self.current_wo_id, data)
            self.current_invoice_id = invoice["CustomerInvoiceId"]
            header = get_invoice_header_data(self.current_wo_id)
            self.current_doc_path = generate_invoice_docx(header, self._current_mode())
            self.current_invoice_id = lock_and_export_invoice(self.current_wo_id, doc_path=self.current_doc_path)
            detail = get_invoice_detail(self.current_invoice_id) or {}
            invoice_row = detail.get("invoice") or {}
            self.current_doc_path = invoice_row.get("CustomerInvoiceDocPath") or self.current_doc_path
            QMessageBox.information(self, "Export Success", f"Invoice saved to:\n{self.current_doc_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def on_review_invoice(self) -> None:
        if not self.current_wo_id:
            return
        try:
            header = get_invoice_header_data(self.current_wo_id)
            lines = [
                f"WO #{self.current_wo_id}",
                f"Customer: {header.get('CustomerName') or 'N/A'}",
                f"Billing mode: {self._current_mode()}",
                f"Previously invoiced: ${float(header.get('PreviouslyInvoicedAmount') or 0):,.2f}",
            ]
            if self._current_mode() == "T&M":
                lines.append(f"Labor rows ready: {len(get_unbilled_labor(self.current_wo_id))}")
                lines.append(f"Material rows ready: {len(get_unbilled_materials(self.current_wo_id))}")
            else:
                lines.append(
                    "Stipulated percent billed to date: "
                    f"{float(header.get('PreviouslyInvoicedStipulatedPercent') or 0):.1f}%"
                )
            QMessageBox.information(self, "Invoice Review", "\n".join(lines))
        except Exception as exc:
            QMessageBox.critical(self, "Review Error", str(exc))

    def on_send_invoice(self) -> None:
        if not self.current_invoice_id and self.current_wo_id:
            latest = get_latest_invoice_for_workorder(self.current_wo_id)
            if latest:
                self.current_invoice_id = latest.get("CustomerInvoiceId")
                self.current_doc_path = latest.get("CustomerInvoiceDocPath") or self.current_doc_path
        if not self.current_invoice_id:
            QMessageBox.warning(self, "Missing Invoice", "Export and lock the invoice before sending it.")
            return
        try:
            from neon_ai.gateway import send_to_user

            detail = get_invoice_detail(self.current_invoice_id)
            if not detail:
                raise ValueError("This invoice could not be reloaded after export.")
            header = detail["header"]
            invoice_row = detail["invoice"] or {}
            doc_path = self.current_doc_path or invoice_row.get("CustomerInvoiceDocPath") or header.get("CustomerInvoiceDocPath")
            recipient = header.get("CustomerEmail")
            if not recipient:
                raise ValueError(
                    "This customer does not have an email on file yet. Add the customer email, then send the invoice."
                )
            if not doc_path:
                raise ValueError("Export the invoice document before sending it.")
            if not os.path.exists(doc_path):
                raise ValueError(f"The exported invoice document could not be found:\n{doc_path}")
            sent = send_to_user(
                subject=f"Invoice #{self.current_invoice_id} - Work Order #{self.current_wo_id}",
                content=(
                    f"Hello {header.get('CustomerName')},\n\n"
                    f"Please find attached invoice #{self.current_invoice_id} for work order #{self.current_wo_id}.\n\n"
                    "Thank you,\nArgon Electrical"
                ),
                recipient=recipient,
                attachment_path=doc_path,
            )
            if not sent:
                raise RuntimeError("The email gateway did not confirm the send.")
            mark_invoice_sent(self.current_invoice_id, doc_path=doc_path)
            self.current_doc_path = doc_path
            QMessageBox.information(self, "Invoice Sent", f"Invoice #{self.current_invoice_id} sent to {recipient}.")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Send Error", str(exc))

    def refresh_data(
        self,
        preserve_work_order_id: int | None = None,
        preserve_tab_index: int | None = None,
        *,
        show_reselect_warning: bool = False,
    ) -> None:
        self.current_wo_id = None
        self.current_invoice_id = None
        self.current_invoice_header = None
        self.current_doc_path = None
        self.current_invoice_document_draft = None
        self.progress_label.setText("Select a work order to begin.")
        self.scope_text.setPlainText("")
        self._clear_workspace()
        self._reset_invoice_template_dropdowns()
        self._clear_document_draft_workspace()
        self._refresh_invoice_document_metadata()
        self.rb_tm.setEnabled(True)
        self.rb_stip.setEnabled(True)
        self._clear_mode_selection()
        self.pipeline_table.setRowCount(0)
        try:
            for row in get_unbilled_workorders():
                table_row = self.pipeline_table.rowCount()
                self.pipeline_table.insertRow(table_row)
                values = [
                    str(row["WorkOrderID"]),
                    str(row.get("CustomerName") or ""),
                    str(row.get("SiteName") or ""),
                ]
                for column, value in enumerate(values):
                    self.pipeline_table.setItem(table_row, column, QTableWidgetItem(value))
        except Exception as exc:
            print(f"Refresh Error: {exc}")
            return

        if preserve_work_order_id is not None:
            reselected = self._reselect_pipeline_work_order(int(preserve_work_order_id))
            if not reselected and show_reselect_warning:
                QMessageBox.information(
                    self,
                    "Selection Not Restored",
                    "The invoice data was saved, but the previous work order could not be reselected in the pipeline."
                    "\n\nYour save was preserved.",
                )
        if preserve_tab_index is not None and 0 <= preserve_tab_index < self.invoice_workspace_tabs.count():
            self.invoice_workspace_tabs.setCurrentIndex(preserve_tab_index)

    def _reselect_pipeline_work_order(self, work_order_id: int) -> bool:
        self.pipeline_table.blockSignals(True)
        try:
            for row in range(self.pipeline_table.rowCount()):
                item = self.pipeline_table.item(row, 0)
                if not item:
                    continue
                try:
                    row_work_order_id = int(item.text())
                except (TypeError, ValueError):
                    continue
                if row_work_order_id == work_order_id:
                    self.pipeline_table.clearSelection()
                    self.pipeline_table.selectRow(row)
                    self.pipeline_table.setCurrentCell(row, 0)
                    break
            else:
                return False
        finally:
            self.pipeline_table.blockSignals(False)

        self.on_wo_select()
        return True

    def on_load_or_create_document_draft(self) -> None:
        if not self.current_invoice_id:
            QMessageBox.warning(self, "Missing Invoice", "Create/save invoice data first.")
            return
        self._populate_invoice_template_dropdowns()
        result = get_or_create_active_invoice_draft(
            int(self.current_invoice_id),
            **self._current_invoice_template_selection_ids(),
        )
        if not result.get("success"):
            QMessageBox.critical(
                self,
                "Document Draft Error",
                str(result.get("error") or "Invoice document draft could not be loaded or created."),
            )
            return
        self.current_invoice_document_draft = result.get("draft")
        self._seed_invoice_draft_template_ids_from_dropdowns_if_needed()
        rendered_from_templates = False
        if result.get("created"):
            rendered_from_templates = self._render_selected_templates_into_document_draft(
                show_success=False,
                preserve_existing_on_failure=True,
            )
        self._apply_document_draft()
        if (
            str(self.current_invoice_document_draft.get("DraftStatus") or "") == "Draft"
            and self._invoice_template_selection_available()
            and (
                (result.get("created") and not rendered_from_templates)
                or (
                    result.get("hydrated")
                    and str(result.get("content_source") or "").strip() == "plain_text_fallback"
                )
            )
        ):
            self._set_invoice_template_selection_pending(True)
            self._update_document_draft_controls()
        if result.get("created"):
            content_source = "selected templates" if rendered_from_templates else str(
                result.get("content_source") or "fallback content"
            )
            QMessageBox.information(
                self,
                "Document Draft Created",
                "A new customer invoice document draft was created for this invoice.\n\n"
                f"Initial content source: {content_source}.",
            )
        else:
            hydrated_note = ""
            if result.get("hydrated"):
                hydrated_note = (
                    f"\n\nBlank draft content was initialized from {result.get('content_source') or 'fallback content'}."
                )
            QMessageBox.information(
                self,
                "Document Draft Loaded",
                f"Loaded invoice document draft #{self.current_invoice_document_draft.get('CustomerInvoiceDocumentDraftID')}."
                f"{hydrated_note}",
            )

    def on_save_document_draft(self) -> None:
        self._save_document_draft_internal(show_success=True)

    def _save_document_draft_internal(self, *, show_success: bool) -> bool:
        if not self.current_invoice_document_draft:
            QMessageBox.warning(self, "No Document Draft", "Load or create an invoice document draft first.")
            return False
        pending_selection_before_save = self._pending_invoice_template_selection_change
        rendered_template_ids_before_save = dict(self._last_rendered_invoice_template_ids)
        result = save_invoice_document_draft(
            draft_id=int(self.current_invoice_document_draft["CustomerInvoiceDocumentDraftID"]),
            editable_content=self.document_draft_editor.toHtml(),
            header_template_id=self._current_selected_template_id(self.invoice_header_template_combo),
            body_template_id=self._current_selected_template_id(self.invoice_body_template_combo),
            footer_template_id=self._current_selected_template_id(self.invoice_footer_template_combo),
            rendered_preview_html=self.document_draft_editor.toHtml(),
        )
        if not result.get("success"):
            QMessageBox.warning(
                self,
                "Save Refused",
                str(result.get("error") or "Invoice document draft could not be saved."),
            )
            return False
        self.current_invoice_document_draft = result.get("draft")
        self._apply_document_draft()
        self._apply_invoice_template_selection_from_draft()
        if pending_selection_before_save:
            self._last_rendered_invoice_template_ids = rendered_template_ids_before_save
            self._set_invoice_template_selection_pending(True)
            self._update_document_draft_controls()
        if show_success:
            pending_note = ""
            if pending_selection_before_save:
                pending_note = (
                    "\n\nTemplate selections were saved, but the draft still needs Render From Selected Templates "
                    "to update the editor content."
                )
            QMessageBox.information(
                self,
                "Document Draft Saved",
                "Invoice document draft saved successfully." + pending_note,
            )
        return True

    def on_lock_document_draft(self) -> None:
        if not self.current_invoice_document_draft:
            QMessageBox.warning(self, "No Document Draft", "Load or create an invoice document draft first.")
            return
        confirm = QMessageBox.question(
            self,
            "Lock Document Draft",
            "This will freeze the invoice document draft and make it read-only.\n\nProceed?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        result = lock_invoice_draft(
            draft_id=int(self.current_invoice_document_draft["CustomerInvoiceDocumentDraftID"]),
            locked_by="UI",
        )
        if not result.get("success"):
            QMessageBox.warning(
                self,
                "Lock Refused",
                str(result.get("error") or "Invoice document draft could not be locked."),
            )
            return
        self.current_invoice_document_draft = result.get("draft")
        self._apply_document_draft()
        QMessageBox.information(self, "Document Draft Locked", "Invoice document draft is now locked and read-only.")

    def on_render_document_draft_templates(self) -> None:
        if not self.current_invoice_document_draft:
            QMessageBox.warning(self, "No Document Draft", "Load or create an invoice document draft first.")
            return
        if str(self.current_invoice_document_draft.get("DraftStatus") or "") != "Draft":
            QMessageBox.information(
                self,
                "Read-Only Draft",
                "Only drafts in Draft status can be re-rendered from the selected templates.",
            )
            return
        if not self._confirm_render_overwrite_if_needed():
            return
        self._render_selected_templates_into_document_draft(
            show_success=True,
            preserve_existing_on_failure=True,
        )

    def on_preview_document_draft(self) -> None:
        if not self.current_invoice_id:
            QMessageBox.warning(self, "No Invoice Selected", "Please select or save an invoice first.")
            return
        if not self.current_invoice_document_draft:
            QMessageBox.warning(self, "No Document Draft", "Load or create an invoice document draft first.")
            return

        preview_html = self.document_draft_editor.toHtml().strip()
        preview_text = self.document_draft_editor.toPlainText().strip()
        if not preview_html and not preview_text:
            QMessageBox.information(
                self,
                "No Document Draft Content",
                "There is no invoice document draft content to preview yet.",
            )
            return

        self._show_document_draft_preview_dialog(
            title=f"Invoice Draft Preview #{self.current_invoice_id}",
            preview_html=preview_html or preview_text,
        )

    def on_export_document_draft(self) -> None:
        if not self.current_invoice_document_draft:
            QMessageBox.warning(self, "No Document Draft", "Load or create an invoice document draft first.")
            return

        draft_status = str(self.current_invoice_document_draft.get("DraftStatus") or "").strip()
        use_unsaved_editor_content = False
        if draft_status == "Draft" and self._document_draft_has_unsaved_editor_changes():
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Question)
            dialog.setWindowTitle("Unsaved Document Draft Changes")
            dialog.setText("This invoice document draft has unsaved changes.")
            dialog.setInformativeText("Choose whether to save before exporting or export the current unsaved view.")
            save_button = dialog.addButton("Save Before Exporting", QMessageBox.ButtonRole.AcceptRole)
            unsaved_button = dialog.addButton("Export Current Unsaved View", QMessageBox.ButtonRole.ActionRole)
            cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.setDefaultButton(save_button)
            dialog.exec()
            clicked_button = dialog.clickedButton()
            if clicked_button == cancel_button:
                QMessageBox.information(self, "Export Cancelled", "Invoice document draft export was cancelled.")
                return
            if clicked_button == save_button:
                if not self._save_document_draft_internal(show_success=False):
                    return
            elif clicked_button == unsaved_button:
                use_unsaved_editor_content = True
            else:
                return

        if not self._document_draft_has_any_content():
            QMessageBox.warning(
                self,
                "Empty Document Draft",
                "There is no invoice document draft content to export yet.",
            )
            return

        html_content, plain_text_content = self._current_document_draft_export_source(
            use_unsaved_editor_content=use_unsaved_editor_content
        )
        if not html_content and not plain_text_content:
            QMessageBox.warning(
                self,
                "Empty Document Draft",
                "There is no invoice document draft content to export yet.",
            )
            return

        result = export_customer_invoice_document_draft(
            self.current_invoice_document_draft,
            html_content=html_content,
            plain_text_content=plain_text_content,
            created_by="UI",
            create_folders=True,
        )
        if not result.get("success"):
            QMessageBox.warning(
                self,
                "Export Failed",
                str(result.get("error") or "Invoice document draft export failed."),
            )
            return

        exported_path = str(result.get("absolute_path") or result.get("relative_path") or "").strip()
        generated_document_id = result.get("generated_document_id")
        export_note = str(result.get("export_note") or "").strip()
        draft_id = self.current_invoice_document_draft.get("CustomerInvoiceDocumentDraftID")
        record_result = (
            record_invoice_draft_exported_file(int(draft_id), exported_path)
            if draft_id is not None and exported_path
            else {"success": False, "error": "Draft export path could not be recorded."}
        )

        final_path_note = ""
        if record_result.get("success"):
            self.current_invoice_document_draft = record_result.get("draft")
            if use_unsaved_editor_content and draft_status == "Draft":
                self._refresh_document_draft_status_label()
                self._update_document_draft_controls()
            else:
                self._apply_document_draft()
        else:
            final_path_note = (
                "\n\nThe file exported successfully, but the draft FinalFilePath could not be updated:\n"
                f"{record_result.get('error') or 'Unknown error.'}"
            )

        history_note = ""
        if generated_document_id:
            history_note = f"\nGenerated document history ID: {generated_document_id}"
        format_note = ""
        if export_note:
            format_note = f"\n{export_note}"

        QMessageBox.information(
            self,
            "Document Draft Exported",
            f"Invoice document draft exported to:\n{exported_path}{format_note}{history_note}{final_path_note}",
        )

    def on_print_document_draft(self) -> None:
        if not self.current_invoice_id:
            QMessageBox.warning(self, "No Invoice Selected", "Please select or save an invoice first.")
            return
        if not self.current_invoice_document_draft:
            QMessageBox.warning(self, "No Document Draft", "Load or create an invoice document draft first.")
            return

        draft_status = str(self.current_invoice_document_draft.get("DraftStatus") or "")
        use_unsaved_editor_content = False
        if draft_status == "Draft" and self._document_draft_has_unsaved_editor_changes():
            choice_box = QMessageBox(self)
            choice_box.setIcon(QMessageBox.Icon.Question)
            choice_box.setWindowTitle("Unsaved Changes")
            choice_box.setText("This invoice document draft has unsaved changes.")
            choice_box.setInformativeText("Choose whether to save before printing or print the current unsaved view.")
            save_button = choice_box.addButton("Save Before Printing", QMessageBox.ButtonRole.AcceptRole)
            print_unsaved_button = choice_box.addButton(
                "Print Current Unsaved View",
                QMessageBox.ButtonRole.DestructiveRole,
            )
            cancel_button = choice_box.addButton(QMessageBox.StandardButton.Cancel)
            choice_box.setDefaultButton(save_button)
            choice_box.exec()

            clicked = choice_box.clickedButton()
            if clicked == cancel_button:
                QMessageBox.information(
                    self,
                    "Print Cancelled",
                    "Printing was cancelled. The invoice document draft was not changed.",
                )
                return
            if clicked == save_button:
                if not self._save_document_draft_internal(show_success=False):
                    return
            elif clicked == print_unsaved_button:
                use_unsaved_editor_content = True
            else:
                return

        html_content, plain_text_content = self._current_document_draft_print_source(
            use_unsaved_editor_content=use_unsaved_editor_content
        )
        if not html_content.strip() and not plain_text_content.strip():
            QMessageBox.warning(
                self,
                "No Document Draft Content",
                "There is no invoice document draft content to print yet.",
            )
            return

        try:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Print Unavailable",
                f"The print subsystem is unavailable.\n\n{exc}",
            )
            return

        try:
            dialog = QPrintDialog(printer, self)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Print Unavailable",
                f"The print dialog could not be opened.\n\n{exc}",
            )
            return

        if dialog.exec() != QDialog.DialogCode.Accepted:
            QMessageBox.information(self, "Print Cancelled", "Printing was cancelled.")
            return

        try:
            document = QTextDocument(self)
            if self._content_looks_like_html(html_content):
                document.setHtml(html_content)
            else:
                document.setPlainText(plain_text_content or html_content)
            document.print_(printer)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Print Failed",
                f"The invoice document draft could not be printed.\n\n{exc}",
            )
            return

        if use_unsaved_editor_content:
            QMessageBox.information(
                self,
                "Printed Unsaved View",
                "The current unsaved invoice document draft view was sent to the printer. The draft content was not saved.",
            )
        else:
            QMessageBox.information(
                self,
                "Print Started",
                "The current invoice document draft was sent to the printer.",
            )

    def _show_document_draft_preview_dialog(self, title: str, preview_html: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 700)

        layout = QVBoxLayout(dialog)
        viewer = QTextEdit(dialog)
        viewer.setReadOnly(True)
        if self._content_looks_like_html(preview_html):
            viewer.setHtml(preview_html)
        else:
            viewer.setPlainText(preview_html)
        layout.addWidget(viewer)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        dialog.exec()

    def _current_document_draft_send_validation(self) -> tuple[bool, str, dict]:
        if not self.current_invoice_document_draft:
            if self.current_wo_id and not self.current_invoice_id:
                return False, "No invoice record exists. Create/save invoice data first.", {}
            return False, "No invoice document draft loaded.", {}

        draft_status = str(self.current_invoice_document_draft.get("DraftStatus") or "").strip()
        if draft_status == "Draft" and self._pending_invoice_template_selection_change:
            return (
                False,
                "Render and save current template selections before sending.",
                self._send_context_from_current_document_draft(),
            )

        draft_id = self.current_invoice_document_draft.get("CustomerInvoiceDocumentDraftID")
        try:
            normalized_draft_id = int(draft_id) if draft_id is not None else None
        except (TypeError, ValueError):
            normalized_draft_id = None
        if normalized_draft_id is None:
            return False, "Invoice document draft was not found.", self._send_context_from_current_document_draft()

        return can_send_customer_invoice_document_draft(normalized_draft_id)

    def _send_context_from_current_document_draft(self) -> dict:
        draft = self.current_invoice_document_draft or {}
        header = get_invoice_header_data(self.current_wo_id) if self.current_wo_id else {}
        return {
            "CustomerInvoiceDocumentDraftID": draft.get("CustomerInvoiceDocumentDraftID"),
            "CustomerInvoiceId": draft.get("CustomerInvoiceId") or self.current_invoice_id,
            "InvoiceID": draft.get("CustomerInvoiceId") or self.current_invoice_id,
            "WorkOrderID": draft.get("WorkOrderID") or header.get("WorkOrderID"),
            "CustomerName": header.get("CustomerName"),
            "CustomerEmail": header.get("CustomerEmail"),
            "FinalFilePath": draft.get("FinalFilePath"),
            "DraftStatus": draft.get("DraftStatus"),
            "InvoiceStatus": header.get("InvoiceStatus"),
        }

    def _update_document_draft_send_button_state(self) -> None:
        can_send, reason, _context = self._current_document_draft_send_validation()
        self.btn_send_document_draft.setEnabled(can_send)
        self.btn_send_document_draft.setToolTip(reason or "Invoice draft send validation is unavailable.")
        self.document_draft_send_status_label.setText(reason or "")

    def on_validate_send_document_draft(self) -> None:
        can_send, reason, context = self._current_document_draft_send_validation()
        if not can_send:
            QMessageBox.information(
                self,
                "Invoice Draft Send Unavailable",
                reason or "This invoice document draft is not ready to send yet.",
            )
            return

        customer_name = str(context.get("CustomerName") or "Unknown Customer")
        customer_email = str(context.get("CustomerEmail") or "Not available")
        invoice_id = context.get("CustomerInvoiceId") or context.get("InvoiceID") or "Unknown"
        draft_id = context.get("CustomerInvoiceDocumentDraftID") or "Unknown"
        attachment_path = str(context.get("FinalFilePath") or "Not available")

        QMessageBox.information(
            self,
            "Invoice Draft Send Validation",
            "This invoice document draft passed send validation.\n\n"
            f"Customer: {customer_name}\n"
            f"Customer Email: {customer_email}\n"
            f"Invoice ID: {invoice_id}\n"
            f"Draft ID: {draft_id}\n"
            f"Attachment Path: {attachment_path}\n\n"
            "Sending is not wired in this validation slice.",
        )

    def _load_existing_document_draft_if_any(self) -> None:
        if not self.current_invoice_id:
            self.current_invoice_document_draft = None
            self._clear_document_draft_workspace()
            self._apply_invoice_template_selection_from_draft()
            return
        result = get_invoice_draft_for_display(int(self.current_invoice_id))
        if not result.get("success"):
            self.current_invoice_document_draft = None
            self._clear_document_draft_workspace()
            self._apply_invoice_template_selection_from_draft()
            return
        self.current_invoice_document_draft = result.get("draft")
        self._apply_document_draft()

    def _apply_document_draft(self) -> None:
        draft = self.current_invoice_document_draft
        if not draft:
            self._clear_document_draft_workspace()
            return
        self._refresh_document_draft_status_label()
        content = str(draft.get("EditableContent") or draft.get("RenderedPreviewHtml") or "")
        self._set_document_draft_editor_content(content)
        draft_is_editable = str(draft.get("DraftStatus") or "Draft") == "Draft"
        self.document_draft_editor.setReadOnly(not draft_is_editable)
        self.document_draft_toolbar.setEnabled(draft_is_editable)
        self._apply_invoice_template_selection_from_draft()
        self._sync_invoice_template_tracking_from_draft()
        self._refresh_invoice_document_metadata()
        self._update_document_draft_controls()

    def _refresh_document_draft_status_label(self) -> None:
        draft = self.current_invoice_document_draft
        if not draft:
            self.document_draft_status_label.setText("No invoice document draft loaded.")
            return
        draft_id = draft.get("CustomerInvoiceDocumentDraftID")
        status = str(draft.get("DraftStatus") or "Draft")
        updated_at = draft.get("UpdatedAt") or draft.get("CreatedAt") or "n/a"
        self.document_draft_status_label.setText(
            f"Document Draft #{draft_id} | Status: {status} | Last Updated: {updated_at}"
        )
        self.invoice_draft_status_value.setText(f"{status} (Draft #{draft_id})" if draft_id else status)

    def _clear_document_draft_workspace(self) -> None:
        self.document_draft_status_label.setText(self._empty_document_draft_status_message())
        self.document_draft_template_status_label.setText("")
        self.document_draft_send_status_label.setText(self._empty_document_draft_send_message())
        self.document_draft_editor.clear()
        self.document_draft_editor.setReadOnly(True)
        self.document_draft_toolbar.setEnabled(False)
        self._set_invoice_template_selection_pending(False)
        self._last_rendered_invoice_template_ids = {
            "header_template_id": None,
            "body_template_id": None,
            "footer_template_id": None,
        }
        self._refresh_invoice_document_metadata()
        self._update_document_draft_controls()

    def _update_document_draft_controls(self) -> None:
        has_invoice = self.current_invoice_id is not None
        draft_loaded = self.current_invoice_document_draft is not None
        draft_status = str(self.current_invoice_document_draft.get("DraftStatus") or "") if draft_loaded else ""
        draft_is_editable = draft_status == "Draft"
        self.btn_load_document_draft.setEnabled(bool(has_invoice))
        self.btn_save_document_draft.setEnabled(bool(draft_loaded and draft_is_editable))
        self.btn_lock_document_draft.setEnabled(bool(draft_loaded and draft_is_editable))
        self.btn_render_document_draft_templates.setEnabled(
            bool(draft_loaded and draft_is_editable and self._invoice_template_selection_available())
        )
        self.btn_preview_document_draft.setEnabled(bool(draft_loaded and self._document_draft_has_any_content()))
        self.btn_export_document_draft.setEnabled(
            bool(
                draft_loaded
                and draft_status in {"Draft", "Locked", "Sent", "Retired"}
                and self._document_draft_has_any_content()
            )
        )
        self.btn_print_document_draft.setEnabled(bool(draft_loaded and self._document_draft_has_any_content()))
        dropdowns_enabled = bool(
            has_invoice
            and self._invoice_template_selection_available()
            and (not draft_loaded or draft_is_editable)
        )
        self.invoice_header_template_combo.setEnabled(dropdowns_enabled)
        self.invoice_body_template_combo.setEnabled(dropdowns_enabled)
        self.invoice_footer_template_combo.setEnabled(dropdowns_enabled)
        self.document_draft_editor.setReadOnly(not draft_is_editable)
        self.document_draft_toolbar.setEnabled(draft_is_editable)
        if not draft_loaded:
            self.document_draft_status_label.setText(self._empty_document_draft_status_message())
        self._update_document_draft_send_button_state()

    def _set_document_draft_editor_content(self, content: str) -> None:
        text = str(content or "")
        if self._content_looks_like_html(text):
            self.document_draft_editor.setHtml(text)
        else:
            self.document_draft_editor.setPlainText(text)

    def _content_looks_like_html(self, content: str) -> bool:
        lowered = str(content or "").strip().lower()
        return bool(
            lowered.startswith("<!doctype html")
            or lowered.startswith("<html")
            or "<p" in lowered
            or "<div" in lowered
            or "<section" in lowered
            or "<ul" in lowered
            or "<ol" in lowered
            or "<table" in lowered
            or "<h1" in lowered
            or "<h2" in lowered
            or "<body" in lowered
        )

    def _document_draft_has_unsaved_editor_changes(self) -> bool:
        if not self.current_invoice_document_draft:
            return False
        stored_content = str(
            self.current_invoice_document_draft.get("EditableContent")
            or self.current_invoice_document_draft.get("RenderedPreviewHtml")
            or ""
        ).strip()
        current_html = self.document_draft_editor.toHtml().strip()
        current_plain = self.document_draft_editor.toPlainText().strip()
        return bool(stored_content != current_html and stored_content != current_plain)

    def _document_draft_has_any_content(self) -> bool:
        if not self.current_invoice_document_draft:
            return False
        stored_content = str(
            self.current_invoice_document_draft.get("EditableContent")
            or self.current_invoice_document_draft.get("RenderedPreviewHtml")
            or ""
        ).strip()
        current_html = self.document_draft_editor.toHtml().strip()
        current_plain = self.document_draft_editor.toPlainText().strip()
        return bool(stored_content or current_html or current_plain)

    def _current_document_draft_export_source(self, *, use_unsaved_editor_content: bool) -> tuple[str, str]:
        draft_status = str(self.current_invoice_document_draft.get("DraftStatus") or "") if self.current_invoice_document_draft else ""
        if use_unsaved_editor_content or draft_status == "Draft":
            html_content = self.document_draft_editor.toHtml().strip()
            plain_text_content = self.document_draft_editor.toPlainText().strip()
            if html_content or plain_text_content:
                return html_content, plain_text_content

        saved_html = (
            str(
                self.current_invoice_document_draft.get("EditableContent")
                or self.current_invoice_document_draft.get("RenderedPreviewHtml")
                or ""
            ).strip()
            if self.current_invoice_document_draft
            else ""
        )
        return saved_html, self.document_draft_editor.toPlainText().strip()

    def _current_document_draft_print_source(self, *, use_unsaved_editor_content: bool) -> tuple[str, str]:
        draft_status = (
            str(self.current_invoice_document_draft.get("DraftStatus") or "")
            if self.current_invoice_document_draft
            else ""
        )
        if use_unsaved_editor_content or draft_status == "Draft":
            html_content = self.document_draft_editor.toHtml().strip()
            plain_text_content = self.document_draft_editor.toPlainText().strip()
            if html_content or plain_text_content:
                return html_content, plain_text_content

        saved_html = (
            str(
                self.current_invoice_document_draft.get("EditableContent")
                or self.current_invoice_document_draft.get("RenderedPreviewHtml")
                or ""
            ).strip()
            if self.current_invoice_document_draft
            else ""
        )
        return saved_html, self.document_draft_editor.toPlainText().strip()

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

    def _populate_invoice_template_dropdowns(self) -> None:
        if not self.current_invoice_id:
            self._reset_invoice_template_dropdowns()
            return
        try:
            catalog_service = self._get_document_catalog_service()
            template_summaries = catalog_service.list_templates(
                document_type_code=CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE
            )
            defaults = catalog_service.list_template_defaults(
                document_type_code=CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
                usage_context=CUSTOMER_INVOICE_DRAFT_WORKSPACE_CONTEXT,
            )
        except Exception as exc:
            self._reset_invoice_template_dropdowns()
            self.document_draft_template_status_label.setText(
                f"Invoice templates could not be loaded right now. Details: {exc}"
            )
            return

        defaults_by_kind: dict[str, int | None] = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }
        for default_mapping in defaults:
            defaults_by_kind[default_mapping.kind.value] = int(default_mapping.template_id)
        self._invoice_template_defaults_by_kind = defaults_by_kind

        by_kind: dict[str, list[object]] = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        seen_template_ids: dict[str, set[int]] = {
            DocumentTemplateKind.HEADER.value: set(),
            DocumentTemplateKind.BODY.value: set(),
            DocumentTemplateKind.FOOTER.value: set(),
        }
        for summary in template_summaries:
            if str(summary.document_type_code or "") != CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE:
                continue
            if not bool(summary.is_active):
                continue
            kind_key = summary.kind.value
            if kind_key not in by_kind:
                continue
            template_id = int(summary.template_id)
            if template_id in seen_template_ids[kind_key]:
                continue
            seen_template_ids[kind_key].add(template_id)
            by_kind[kind_key].append(summary)
        self._invoice_template_choices_by_kind = by_kind

        self._populate_invoice_template_combo(
            self.invoice_header_template_combo,
            DocumentTemplateKind.HEADER.value,
            "No active invoice header templates",
        )
        self._populate_invoice_template_combo(
            self.invoice_body_template_combo,
            DocumentTemplateKind.BODY.value,
            "No active invoice body templates",
        )
        self._populate_invoice_template_combo(
            self.invoice_footer_template_combo,
            DocumentTemplateKind.FOOTER.value,
            "No active invoice footer templates",
        )
        self._apply_invoice_template_selection_from_draft()
        self._update_document_draft_controls()

    def _populate_invoice_template_combo(self, combo: QComboBox, kind_key: str, empty_text: str) -> None:
        summaries = self._invoice_template_choices_by_kind.get(kind_key, [])
        combo.blockSignals(True)
        combo.clear()
        if not summaries:
            combo.addItem(empty_text, None)
            combo.setEnabled(False)
            combo.blockSignals(False)
            return

        label_counts: dict[str, int] = {}
        for summary in summaries:
            base_label = self._template_base_label(summary)
            label_counts[base_label] = label_counts.get(base_label, 0) + 1

        for summary in summaries:
            label = self._template_option_label(summary, label_counts)
            combo.addItem(label, int(summary.template_id))
        combo.setCurrentIndex(0)
        combo.setEnabled(True)
        combo.blockSignals(False)

    def _reset_invoice_template_dropdowns(self) -> None:
        self._invoice_template_defaults_by_kind = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }
        placeholder = self._template_combo_placeholder()
        for combo, text in (
            (self.invoice_header_template_combo, placeholder),
            (self.invoice_body_template_combo, placeholder),
            (self.invoice_footer_template_combo, placeholder),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(text, None)
            combo.setEnabled(False)
            combo.blockSignals(False)
        self._set_invoice_template_selection_pending(False)

    def _apply_invoice_template_selection_from_draft(self) -> None:
        draft = self.current_invoice_document_draft or {}
        self._suspend_invoice_template_selection_tracking = True
        try:
            self._select_invoice_template_combo_value(
                self.invoice_header_template_combo,
                draft.get("HeaderTemplateID"),
                self._invoice_template_defaults_by_kind.get(DocumentTemplateKind.HEADER.value),
            )
            self._select_invoice_template_combo_value(
                self.invoice_body_template_combo,
                draft.get("BodyTemplateID"),
                self._invoice_template_defaults_by_kind.get(DocumentTemplateKind.BODY.value),
            )
            self._select_invoice_template_combo_value(
                self.invoice_footer_template_combo,
                draft.get("FooterTemplateID"),
                self._invoice_template_defaults_by_kind.get(DocumentTemplateKind.FOOTER.value),
            )
        finally:
            self._suspend_invoice_template_selection_tracking = False
        self._refresh_invoice_template_selection_pending_state()

    def _select_invoice_template_combo_value(
        self,
        combo: QComboBox,
        template_id,
        default_template_id: int | None = None,
    ) -> None:
        preferred_ids: list[int] = []
        for value in (template_id, default_template_id):
            try:
                normalized = int(value) if value is not None else None
            except (TypeError, ValueError):
                normalized = None
            if normalized is not None and normalized not in preferred_ids:
                preferred_ids.append(normalized)
        for preferred_id in preferred_ids:
            for index in range(combo.count()):
                if combo.itemData(index) == preferred_id:
                    combo.setCurrentIndex(index)
                    return
        if combo.count() > 0:
            combo.setCurrentIndex(0)

    def _current_selected_template_id(self, combo: QComboBox) -> int | None:
        value = combo.currentData()
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _current_invoice_template_selection_ids(self) -> dict[str, int | None]:
        return {
            "header_template_id": self._current_selected_template_id(self.invoice_header_template_combo),
            "body_template_id": self._current_selected_template_id(self.invoice_body_template_combo),
            "footer_template_id": self._current_selected_template_id(self.invoice_footer_template_combo),
        }

    def _invoice_draft_template_ids(self, draft: dict | None = None) -> dict[str, int | None]:
        source = draft or self.current_invoice_document_draft or {}
        resolved: dict[str, int | None] = {}
        for draft_key, target_key in (
            ("HeaderTemplateID", "header_template_id"),
            ("BodyTemplateID", "body_template_id"),
            ("FooterTemplateID", "footer_template_id"),
        ):
            value = source.get(draft_key)
            try:
                resolved[target_key] = int(value) if value is not None else None
            except (TypeError, ValueError):
                resolved[target_key] = None
        return resolved

    def _sync_invoice_template_tracking_from_draft(self) -> None:
        self._last_rendered_invoice_template_ids = self._invoice_draft_template_ids(self.current_invoice_document_draft)
        self._set_invoice_template_selection_pending(False)

    def _current_invoice_template_selection_differs_from_rendered(self) -> bool:
        current_ids = self._current_invoice_template_selection_ids()
        return any(
            current_ids.get(key) != self._last_rendered_invoice_template_ids.get(key)
            for key in ("header_template_id", "body_template_id", "footer_template_id")
        )

    def _set_invoice_template_selection_pending(self, pending: bool) -> None:
        self._pending_invoice_template_selection_change = pending
        if pending:
            self.document_draft_template_status_label.setText(
                "Template selection changed. Click Render From Selected Templates to update the invoice draft."
            )
            self.btn_render_document_draft_templates.setStyleSheet("font-weight: 700; border: 2px solid #c98a00;")
        else:
            self.document_draft_template_status_label.setText("")
            self.btn_render_document_draft_templates.setStyleSheet("")

    def _refresh_invoice_template_selection_pending_state(self) -> None:
        draft = self.current_invoice_document_draft or {}
        if self._suspend_invoice_template_selection_tracking:
            return
        if str(draft.get("DraftStatus") or "") != "Draft":
            self._set_invoice_template_selection_pending(False)
            return
        self._set_invoice_template_selection_pending(self._current_invoice_template_selection_differs_from_rendered())

    def _on_invoice_template_selection_changed(self, index: int) -> None:
        del index
        self._refresh_invoice_template_selection_pending_state()
        self._update_document_draft_controls()

    def _seed_invoice_draft_template_ids_from_dropdowns_if_needed(self) -> None:
        if not self.current_invoice_document_draft:
            return
        if str(self.current_invoice_document_draft.get("DraftStatus") or "") != "Draft":
            return
        if any(
            self.current_invoice_document_draft.get(field)
            for field in ("HeaderTemplateID", "BodyTemplateID", "FooterTemplateID")
        ):
            return

        header_template_id = self._current_selected_template_id(self.invoice_header_template_combo)
        body_template_id = self._current_selected_template_id(self.invoice_body_template_combo)
        footer_template_id = self._current_selected_template_id(self.invoice_footer_template_combo)
        if header_template_id is None and body_template_id is None and footer_template_id is None:
            return

        result = save_invoice_document_draft(
            draft_id=int(self.current_invoice_document_draft["CustomerInvoiceDocumentDraftID"]),
            editable_content=str(self.current_invoice_document_draft.get("EditableContent") or ""),
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
            rendered_preview_html=self.current_invoice_document_draft.get("RenderedPreviewHtml"),
        )
        if result.get("success"):
            self.current_invoice_document_draft = result.get("draft")

    def _invoice_template_selection_available(self) -> bool:
        return any(
            self._current_selected_template_id(combo) is not None
            for combo in (
                self.invoice_header_template_combo,
                self.invoice_body_template_combo,
                self.invoice_footer_template_combo,
            )
        )

    def _template_base_label(self, summary: object) -> str:
        template_name = str(getattr(summary, "template_name", "") or "").strip()
        if template_name:
            return template_name
        display_name = str(getattr(summary, "display_name", "") or "").strip()
        if display_name:
            return display_name
        template_code = str(getattr(summary, "template_code", "") or "").strip()
        if template_code:
            return template_code
        template_id = getattr(summary, "template_id", None)
        return f"Template #{template_id}" if template_id is not None else "Unnamed Template"

    def _template_option_label(self, summary: object, label_counts: dict[str, int]) -> str:
        base_label = self._template_base_label(summary)
        if label_counts.get(base_label, 0) <= 1:
            return base_label
        template_id = getattr(summary, "template_id", None)
        return f"{base_label} (Template #{template_id})" if template_id is not None else base_label

    def _render_selected_templates_into_document_draft(
        self,
        *,
        show_success: bool,
        preserve_existing_on_failure: bool,
    ) -> bool:
        if not self.current_invoice_id or not self.current_invoice_document_draft:
            return False

        header_template_id = self._current_selected_template_id(self.invoice_header_template_combo)
        body_template_id = self._current_selected_template_id(self.invoice_body_template_combo)
        footer_template_id = self._current_selected_template_id(self.invoice_footer_template_combo)
        result = render_customer_invoice_document_from_template_selection(
            int(self.current_invoice_id),
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
        )
        if not result.get("success"):
            error_message = str(result.get("error") or "Selected invoice templates could not be rendered.")
            missing_tokens = result.get("missing_tokens") or []
            if missing_tokens:
                error_message += "\n\nMissing tokens:\n" + "\n".join(f"- {token}" for token in missing_tokens)
            print(
                f"[InvoiceCreatorPage] Template render failed for CustomerInvoiceId={self.current_invoice_id}: {error_message}"
            )
            if preserve_existing_on_failure:
                QMessageBox.warning(self, "Render Failed", error_message)
            return False

        preview_html = str(result.get("preview_html") or "")
        rendered_content = str(result.get("rendered_content") or preview_html or "")
        if not rendered_content.strip():
            QMessageBox.warning(
                self,
                "Render Failed",
                "Selected templates rendered no usable content. Existing invoice draft content was preserved.",
            )
            return False

        self._set_document_draft_editor_content(rendered_content)
        save_result = save_invoice_document_draft(
            draft_id=int(self.current_invoice_document_draft["CustomerInvoiceDocumentDraftID"]),
            editable_content=self.document_draft_editor.toHtml(),
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
            rendered_preview_html=preview_html or self.document_draft_editor.toHtml(),
        )
        if not save_result.get("success"):
            QMessageBox.warning(
                self,
                "Draft Save Failed",
                str(save_result.get("error") or "Rendered invoice draft content could not be saved."),
            )
            return False

        self.current_invoice_document_draft = save_result.get("draft")
        self._apply_document_draft()
        rendered_templates = result.get("rendered_templates") or []
        if rendered_templates:
            for template_info in rendered_templates:
                print(
                    "[InvoiceCreatorPage] Rendered "
                    f"{template_info.get('template_kind')} template: "
                    f"{template_info.get('template_name')} #{template_info.get('template_id')}, "
                    f"version #{template_info.get('template_version_id')}"
                )
        if show_success:
            render_details = ""
            if rendered_templates:
                detail_lines = [
                    f"- {str(item.get('template_kind') or '').capitalize()}: "
                    f"{item.get('template_name')} #{item.get('template_id')} "
                    f"(version #{item.get('template_version_id')})"
                    for item in rendered_templates
                ]
                render_details = "\n\nRendered from real Document Studio templates:\n" + "\n".join(detail_lines)
            QMessageBox.information(
                self,
                "Document Draft Rendered",
                "The selected invoice header, body, and footer templates were rendered into the invoice draft editor."
                + render_details,
            )
        return True

    def _confirm_render_overwrite_if_needed(self) -> bool:
        current_text = self.document_draft_editor.toPlainText().strip()
        if not current_text:
            return True
        choice = QMessageBox.question(
            self,
            "Overwrite Draft Content?",
            "Rendering from the selected templates may overwrite manual edits in this invoice draft.\n\nProceed?",
        )
        return choice == QMessageBox.StandardButton.Yes
