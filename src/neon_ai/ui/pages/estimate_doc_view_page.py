from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from neon_ai.document_control.models import DocumentTemplateKind
from neon_ai.document_control.seed import seed_document_control_defaults
from neon_ai.database.automation import build_client_estimate_doc, get_client_estimate_doc_path
from neon_ai.database.estimates import (
    get_dashboard_estimates,
    get_detailed_estimate_data,
    recalculate_estimate_totals,
    update_estimate_status,
)
from neon_ai.database.rfq import create_and_send_rfq_batch, get_estimate_rfq_status, get_vendor_choices
from neon_ai.services.estimate_document_draft_service import (
    get_draft_for_display,
    get_or_create_active_draft,
    lock_draft,
    save_draft,
)
from neon_ai.services.document_generation_service import (
    generate_estimate_document_record,
    preview_estimate_document,
)
from neon_ai.ui.widgets.rich_text_toolbar import RichTextToolbar

RFQ_EMAIL_MEMORY_PATH = Path(__file__).resolve().parents[4] / "resources" / "rfq_vendor_email_memory.json"
ESTIMATE_DOCUMENT_TYPE_CODE = "ESTIMATE_DOCUMENT"
ESTIMATE_DRAFT_WORKSPACE_CONTEXT = "ESTIMATE_DRAFT_WORKSPACE"


def load_rfq_email_memory() -> dict:
    if not RFQ_EMAIL_MEMORY_PATH.exists():
        return {}
    try:
        data = json.loads(RFQ_EMAIL_MEMORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_rfq_email_memory(memory: dict) -> None:
    try:
        RFQ_EMAIL_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        RFQ_EMAIL_MEMORY_PATH.write_text(json.dumps(memory, indent=2), encoding="utf-8")
    except Exception:
        pass


class CreateRFQDialog(QDialog):
    def __init__(self, parent, vendors: list[dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create RFQ")
        self.setModal(True)
        self.result: dict | None = None
        self.vendors = vendors or []
        self.vendor_widgets: dict[int, tuple[QCheckBox, QLineEdit, str]] = {}
        self.email_memory = load_rfq_email_memory()

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Select wholesalers and enter the recipient email for each RFQ.")
        )

        due_row = QFrame()
        due_layout = QHBoxLayout(due_row)
        due_layout.setContentsMargins(0, 0, 0, 0)
        due_layout.addWidget(QLabel("Due Date:"))
        self.due_date_field = QLineEdit((datetime.date.today() + datetime.timedelta(days=7)).isoformat())
        self.due_date_field.setMaximumWidth(120)
        due_layout.addWidget(self.due_date_field)
        due_layout.addStretch(1)
        layout.addWidget(due_row)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        for vendor in self.vendors:
            row = QFrame()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            enabled = QCheckBox(vendor["VendorName"])
            row_layout.addWidget(enabled)

            contact_bits = []
            if vendor.get("VendorContactName"):
                contact_bits.append(vendor["VendorContactName"])
            if vendor.get("VendorContactNumber"):
                contact_bits.append(vendor["VendorContactNumber"])
            if contact_bits:
                row_layout.addWidget(QLabel(f"({', '.join(contact_bits)})"))

            remembered_email = self.email_memory.get(str(vendor["VendorID"])) or self.email_memory.get(vendor["VendorName"], "")
            email_field = QLineEdit(remembered_email)
            row_layout.addWidget(email_field, 1)
            body_layout.addWidget(row)
            self.vendor_widgets[vendor["VendorID"]] = (enabled, email_field, vendor["VendorName"])
        layout.addWidget(body)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_button)
        submit_button = QPushButton("Create & Send RFQs")
        submit_button.clicked.connect(self.on_submit)
        footer_layout.addWidget(submit_button)
        layout.addWidget(footer)

    def on_submit(self) -> None:
        selected: list[dict] = []
        for vendor_id, (enabled, email_field, vendor_name) in self.vendor_widgets.items():
            if enabled.isChecked():
                email_value = email_field.text().strip()
                if not email_value:
                    QMessageBox.warning(self, "Missing Email", f"Please enter a recipient email for {vendor_name}.")
                    return
                selected.append(
                    {
                        "vendor_id": vendor_id,
                        "vendor_name": vendor_name,
                        "recipient_email": email_value,
                    }
                )

        if not selected:
            QMessageBox.warning(self, "No Vendors Selected", "Select at least one wholesaler to create RFQs.")
            return

        self.result = {
            "due_date": self.due_date_field.text().strip(),
            "vendors": selected,
        }
        updated_memory = dict(self.email_memory)
        for vendor in selected:
            updated_memory[str(vendor["vendor_id"])] = vendor["recipient_email"]
            updated_memory[vendor["vendor_name"]] = vendor["recipient_email"]
        save_rfq_email_memory(updated_memory)
        self.accept()


class EstimateDocViewPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_loaded_id: int | None = None
        self.current_data: dict | None = None
        self.current_customer_copy_path: str | None = None
        self.current_estimate_draft: dict | None = None
        self._template_choices_by_kind: dict[str, list[object]] = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        self._template_defaults_by_kind: dict[str, int | None] = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_doc_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

    def _build_list_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        heading = QLabel("Estimate Pipeline")
        heading.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(heading)
        subtitle = QLabel("Select an estimate to load the customer-facing crafting workspace.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        self.tree = QTableWidget(0, 6)
        self.tree.setHorizontalHeaderLabels(["ID", "Customer", "Site", "Status", "Value", "Created"])
        self.tree.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tree.verticalHeader().setVisible(False)
        self.tree.setColumnWidth(0, 80)
        self.tree.setColumnWidth(1, 180)
        self.tree.setColumnWidth(2, 100)
        self.tree.setColumnWidth(3, 90)
        self.tree.setColumnWidth(4, 110)
        self.tree.setColumnWidth(5, 100)
        self.tree.itemSelectionChanged.connect(self.on_select)
        layout.addWidget(self.tree)
        return panel

    def _build_doc_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        title = QLabel("Crafting Workspace")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        subtitle = QLabel(
            "Craft the customer-facing estimate document here. Estimate math and pricing stay in New Estimate."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        top_group = QGroupBox("Estimate Metadata & Template Selection")
        top_layout = QGridLayout(top_group)
        top_layout.setHorizontalSpacing(12)
        top_layout.setVerticalSpacing(8)

        self.customer_value = self._make_meta_value_label()
        self.site_value = self._make_meta_value_label()
        self.site_address_value = self._make_meta_value_label()
        self.contact_value = self._make_meta_value_label()
        self.estimate_value_label = self._make_meta_value_label()
        self.created_value = self._make_meta_value_label()
        self.draft_status_label = self._make_meta_value_label("No draft loaded.")

        self.header_template_combo = QComboBox()
        self.body_template_combo = QComboBox()
        self.footer_template_combo = QComboBox()

        top_layout.addWidget(self._meta_label("Customer"), 0, 0)
        top_layout.addWidget(self.customer_value, 0, 1)
        top_layout.addWidget(self._meta_label("Site"), 0, 2)
        top_layout.addWidget(self.site_value, 0, 3)

        top_layout.addWidget(self._meta_label("Site Address"), 1, 0)
        top_layout.addWidget(self.site_address_value, 1, 1)
        top_layout.addWidget(self._meta_label("Contact"), 1, 2)
        top_layout.addWidget(self.contact_value, 1, 3)

        top_layout.addWidget(self._meta_label("Estimate Value"), 2, 0)
        top_layout.addWidget(self.estimate_value_label, 2, 1)
        top_layout.addWidget(self._meta_label("Date Created"), 2, 2)
        top_layout.addWidget(self.created_value, 2, 3)

        top_layout.addWidget(self._meta_label("Draft Status"), 3, 0)
        top_layout.addWidget(self.draft_status_label, 3, 1, 1, 3)

        top_layout.addWidget(self._meta_label("Header Template"), 4, 0)
        top_layout.addWidget(self.header_template_combo, 4, 1)
        top_layout.addWidget(self._meta_label("Estimate Template"), 4, 2)
        top_layout.addWidget(self.body_template_combo, 4, 3)

        top_layout.addWidget(self._meta_label("Footer Template"), 5, 0)
        top_layout.addWidget(self.footer_template_combo, 5, 1)

        self.btn_load_draft = QPushButton("Load/Create Draft")
        self.btn_load_draft.clicked.connect(self.load_or_create_draft)
        self.btn_load_draft.setEnabled(False)
        top_layout.addWidget(self.btn_load_draft, 5, 3)

        layout.addWidget(top_group)

        draft_group = QGroupBox("Estimate Document Draft")
        draft_layout = QVBoxLayout(draft_group)
        self.draft_editor = QTextEdit()
        self.draft_editor.setAcceptRichText(True)
        self.draft_editor.setPlaceholderText(
            "Load or create an estimate document draft, then enter customer-facing document content here."
        )
        self.draft_editor.setReadOnly(True)
        self.draft_toolbar = RichTextToolbar(self.draft_editor, parent=self)
        draft_layout.addWidget(self.draft_toolbar)
        draft_layout.addWidget(self.draft_editor, 1)
        layout.addWidget(draft_group, 1)

        primary_button_row = QFrame()
        primary_button_layout = QHBoxLayout(primary_button_row)
        primary_button_layout.setContentsMargins(0, 0, 0, 0)
        primary_button_layout.setSpacing(8)
        self.btn_save_draft = QPushButton("Save Draft")
        self.btn_save_draft.clicked.connect(self.save_current_draft)
        self.btn_save_draft.setEnabled(False)
        primary_button_layout.addWidget(self.btn_save_draft)

        self.btn_lock_draft = QPushButton("Lock Draft")
        self.btn_lock_draft.clicked.connect(self.lock_current_draft)
        self.btn_lock_draft.setEnabled(False)
        primary_button_layout.addWidget(self.btn_lock_draft)

        self.btn_workspace_preview = QPushButton("Preview")
        self.btn_workspace_preview.clicked.connect(self.preview_template_document)
        self.btn_workspace_preview.setEnabled(False)
        primary_button_layout.addWidget(self.btn_workspace_preview)

        self.btn_print = QPushButton("Print")
        self.btn_print.setEnabled(False)
        self.btn_print.setToolTip("Print is staged and not wired yet.")
        primary_button_layout.addWidget(self.btn_print)

        self.btn_export = QPushButton("Export")
        self.btn_export.setEnabled(False)
        self.btn_export.setToolTip("Export is staged and not wired yet.")
        primary_button_layout.addWidget(self.btn_export)

        self.btn_send_customer = QPushButton("Send to Customer")
        self.btn_send_customer.setEnabled(False)
        self.btn_send_customer.setToolTip("Send to Customer is not wired yet in this workspace slice.")
        primary_button_layout.addWidget(self.btn_send_customer)
        primary_button_layout.addStretch(1)
        layout.addWidget(primary_button_row)

        legacy_group = QGroupBox("Legacy / Staged Actions")
        legacy_layout = QGridLayout(legacy_group)
        legacy_layout.setHorizontalSpacing(8)
        legacy_layout.setVerticalSpacing(8)

        self.btn_lock = QPushButton("Lock For Submission")
        self.btn_lock.clicked.connect(self.lock_for_submission)
        self.btn_lock.setEnabled(False)
        legacy_layout.addWidget(self.btn_lock, 0, 0)

        self.btn_create_copy = QPushButton("Create Customer Copy")
        self.btn_create_copy.clicked.connect(self.create_customer_copy)
        self.btn_create_copy.setEnabled(False)
        legacy_layout.addWidget(self.btn_create_copy, 0, 1)

        self.btn_view_copy = QPushButton("View Customer Copy")
        self.btn_view_copy.clicked.connect(self.view_customer_copy)
        self.btn_view_copy.setEnabled(False)
        legacy_layout.addWidget(self.btn_view_copy, 0, 2)

        self.btn_create_rfq = QPushButton("Create RFQ")
        self.btn_create_rfq.clicked.connect(self.create_rfq)
        self.btn_create_rfq.setEnabled(False)
        legacy_layout.addWidget(self.btn_create_rfq, 0, 3)

        self.btn_preview_template = QPushButton("Preview Template Document")
        self.btn_preview_template.clicked.connect(self.preview_template_document)
        self.btn_preview_template.setEnabled(False)
        legacy_layout.addWidget(self.btn_preview_template, 1, 0, 1, 2)

        self.btn_generate_template = QPushButton("Generate Template Document Record")
        self.btn_generate_template.clicked.connect(self.generate_template_document_record)
        self.btn_generate_template.setEnabled(False)
        legacy_layout.addWidget(self.btn_generate_template, 1, 2, 1, 2)
        layout.addWidget(legacy_group)
        return panel

    def refresh_data(self) -> None:
        self.tree.setRowCount(0)
        for est in get_dashboard_estimates():
            status = str(est.get("Status", "Draft"))
            row = self.tree.rowCount()
            self.tree.insertRow(row)
            values = [
                str(est["EstimateID"]),
                str(est.get("CustomerName") or ""),
                str(est.get("SiteName") or ""),
                status,
                self._format_money(est.get("TotalValue")),
                str(est.get("CreatedDate") or "N/A"),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if status.lower() in {"locked", "sent"}:
                    item.setForeground(Qt.GlobalColor.red)
                self.tree.setItem(row, column, item)

        self.current_loaded_id = None
        self.current_data = None
        self.current_customer_copy_path = None
        self.current_estimate_draft = None
        self._clear_metadata()
        self._reset_template_dropdowns()
        self._clear_draft_display()
        self._update_button_states()

    def on_select(self) -> None:
        selection = self.tree.selectedItems()
        if not selection:
            return
        selected_row = self.tree.row(selection[0])
        self.current_loaded_id = int(self.tree.item(selected_row, 0).text())
        self.current_data = get_detailed_estimate_data(self.current_loaded_id)
        if not self.current_data or not self.current_data.get("parent"):
            self.current_customer_copy_path = None
            self.current_estimate_draft = None
            self._clear_metadata()
            self._clear_draft_display()
            self._update_button_states()
            QMessageBox.critical(
                self,
                "Estimate Load Failed",
                f"Estimate #{self.current_loaded_id} could not be loaded into Final Doc View.",
            )
            print(
                f"[EstimateDocViewPage] Failed to load estimate metadata for EstimateID={self.current_loaded_id}"
            )
            return
        self.current_customer_copy_path = self._get_customer_copy_path()
        self._apply_metadata(self.current_data)
        self._populate_template_dropdowns()
        self._load_or_create_current_draft(
            auto_create=True,
            show_success=False,
            show_not_found=False,
        )
        self._update_button_states()

    def lock_for_submission(self) -> None:
        if not self.current_loaded_id or not self.current_data:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return

        current_status = str(self.current_data["parent"].get("Status") or "").strip().lower()
        if current_status in {"locked", "sent"}:
            QMessageBox.information(self, "Already Locked", "This estimate is already locked for submission.")
            return

        rfq_status = get_estimate_rfq_status(self.current_loaded_id)
        if rfq_status["has_materials"] and not rfq_status["has_rfq"]:
            proceed = QMessageBox.question(
                self,
                "No RFQ Created",
                "No material RFQ has been created for this estimate yet.\n\n"
                "Are you sure you want to lock and submit this pricing?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return
        elif rfq_status["has_materials"] and rfq_status["has_rfq"] and not rfq_status["has_returned_quotes"]:
            proceed = QMessageBox.question(
                self,
                "Quotes Not Returned",
                "RFQs have been created for this estimate, but no vendor quotes have been logged yet.\n\n"
                "Are you sure you want to lock and submit this pricing?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        confirm = QMessageBox.question(
            self,
            "Lock For Submission",
            "This will lock the estimate for AI submission to the customer. Proceed?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        estimate_id = self.current_loaded_id
        if update_estimate_status(estimate_id, "Locked"):
            recalculate_estimate_totals(estimate_id)
            self.refresh_data()
            for row in range(self.tree.rowCount()):
                if int(self.tree.item(row, 0).text()) == estimate_id:
                    self.tree.selectRow(row)
                    self.on_select()
                    break
            QMessageBox.information(self, "Locked", "Estimate locked for submission.")
        else:
            QMessageBox.critical(self, "Error", "Failed to lock estimate for submission.")

    def create_customer_copy(self) -> None:
        if not self.current_data or not self.current_loaded_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return
        try:
            self.current_customer_copy_path = build_client_estimate_doc(self.current_loaded_id)
            self._update_button_states()
            QMessageBox.information(self, "Saved", f"Customer copy saved to:\n{self.current_customer_copy_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create customer copy:\n{exc}")

    def view_customer_copy(self) -> None:
        if not self.current_data or not self.current_loaded_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return
        if not self.current_customer_copy_path or not os.path.exists(self.current_customer_copy_path):
            self.current_customer_copy_path = self._get_customer_copy_path()
        if not self.current_customer_copy_path or not os.path.exists(self.current_customer_copy_path):
            QMessageBox.warning(
                self,
                "No Customer Copy",
                "No saved customer copy was found yet. Please create it first.",
            )
            self._update_button_states()
            return
        try:
            os.startfile(self.current_customer_copy_path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Error", f"Could not open the customer copy:\n{exc}")

    def create_rfq(self) -> None:
        if not self.current_loaded_id or not self.current_data:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return

        vendors = get_vendor_choices()
        if not vendors:
            QMessageBox.warning(self, "No Vendors", "No wholesalers are set up yet in the vendor list.")
            return

        dialog = CreateRFQDialog(self, vendors)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result:
            return

        try:
            results = create_and_send_rfq_batch(
                self.current_loaded_id,
                dialog.result["vendors"],
                dialog.result["due_date"] or None,
            )
        except Exception as exc:
            QMessageBox.critical(self, "RFQ Error", f"Failed to create/send RFQ package:\n{exc}")
            return

        sent_count = sum(1 for row in results if row["sent"])
        total_count = len(results)
        QMessageBox.information(
            self,
            "RFQ Complete",
            f"Created {total_count} RFQ(s).\nSent successfully: {sent_count}.",
        )

    def _get_customer_copy_path(self) -> str | None:
        if not self.current_loaded_id:
            return None
        try:
            return get_client_estimate_doc_path(self.current_loaded_id)
        except Exception:
            return None

    def preview_template_document(self) -> None:
        if not self.current_loaded_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return

        result = preview_estimate_document(self.current_loaded_id)
        if not result.get("success"):
            missing_tokens = result.get("missing_tokens") or []
            if missing_tokens:
                QMessageBox.warning(
                    self,
                    "Missing Tokens",
                    "Estimate template preview could not be generated because these required tokens are missing:\n"
                    + "\n".join(f"- {token}" for token in missing_tokens),
                )
                return
            QMessageBox.warning(
                self,
                "Preview Unavailable",
                str(result.get("error") or "Estimate template preview is not available yet."),
            )
            return

        self._show_template_preview_dialog(
            title=f"Estimate Template Preview #{self.current_loaded_id}",
            preview_html=str(result.get("preview_html") or ""),
        )

    def generate_template_document_record(self) -> None:
        if not self.current_loaded_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return

        result = generate_estimate_document_record(self.current_loaded_id, generated_by="UI")
        if not result.get("success"):
            missing_tokens = result.get("missing_tokens") or []
            if missing_tokens:
                QMessageBox.warning(
                    self,
                    "Missing Tokens",
                    "Estimate template document record could not be created because these required tokens are missing:\n"
                    + "\n".join(f"- {token}" for token in missing_tokens),
                )
                return
            QMessageBox.warning(
                self,
                "Generation Failed",
                str(result.get("error") or "Estimate template document record could not be created."),
            )
            return

        QMessageBox.information(
            self,
            "Template Document Recorded",
            "Template-generated estimate document history was recorded successfully.\n\n"
            f"GeneratedDocumentID: {result.get('generated_document_id')}\n"
            f"Path: {result.get('absolute_path')}",
        )

    def load_or_create_draft(self) -> None:
        if not self.current_loaded_id:
            QMessageBox.warning(self, "Warning", "Please select an estimate first.")
            return
        self._load_or_create_current_draft(
            auto_create=True,
            show_success=True,
            show_not_found=True,
        )

    def save_current_draft(self) -> None:
        if not self.current_estimate_draft:
            QMessageBox.warning(self, "No Draft", "Load or create a draft first.")
            return

        draft_id = int(self.current_estimate_draft["EstimateDocumentDraftID"])
        result = save_draft(
            draft_id=draft_id,
            editable_content=self.draft_editor.toHtml(),
            header_template_id=self._current_selected_template_id(self.header_template_combo),
            body_template_id=self._current_selected_template_id(self.body_template_combo),
            footer_template_id=self._current_selected_template_id(self.footer_template_combo),
            rendered_preview_html=self.draft_editor.toHtml(),
        )
        if not result.get("success"):
            QMessageBox.warning(
                self,
                "Save Refused",
                str(result.get("error") or "Estimate document draft could not be saved."),
            )
            return

        self.current_estimate_draft = result.get("draft")
        self._apply_current_draft()
        self._apply_template_selection_from_draft()
        QMessageBox.information(
            self,
            "Draft Saved",
            f"Estimate document draft #{draft_id} was saved successfully.",
        )

    def lock_current_draft(self) -> None:
        if not self.current_estimate_draft:
            QMessageBox.warning(self, "No Draft", "Load or create a draft first.")
            return

        draft_id = int(self.current_estimate_draft["EstimateDocumentDraftID"])
        confirm = QMessageBox.question(
            self,
            "Lock Draft",
            "This will freeze the current estimate document draft and make it read-only.\n\nProceed?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        result = lock_draft(draft_id=draft_id, locked_by="UI")
        if not result.get("success"):
            QMessageBox.warning(
                self,
                "Lock Refused",
                str(result.get("error") or "Estimate document draft could not be locked."),
            )
            return

        self.current_estimate_draft = result.get("draft")
        self._apply_current_draft()
        self._apply_template_selection_from_draft()
        QMessageBox.information(
            self,
            "Draft Locked",
            f"Estimate document draft #{draft_id} is now locked and read-only.",
        )

    def _show_template_preview_dialog(self, title: str, preview_html: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 700)

        layout = QVBoxLayout(dialog)
        viewer = QTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setHtml(preview_html)
        layout.addWidget(viewer)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        dialog.exec()

    def _clear_draft_display(self) -> None:
        self.draft_status_label.setText("No draft loaded.")
        self.draft_editor.clear()
        self.draft_editor.setReadOnly(True)
        self._set_toolbar_enabled(False)

    def _apply_current_draft(self) -> None:
        draft = self.current_estimate_draft
        if not draft:
            self._clear_draft_display()
            self._update_button_states()
            return

        draft_id = draft.get("EstimateDocumentDraftID")
        status = str(draft.get("DraftStatus") or "Draft")
        updated_at = draft.get("UpdatedAt") or draft.get("CreatedAt") or "n/a"
        self.draft_status_label.setText(
            f"Draft #{draft_id} | Status: {status} | Last Updated: {updated_at}"
        )
        draft_content = str(draft.get("EditableContent") or draft.get("RenderedPreviewHtml") or "")
        self._set_draft_editor_content(draft_content)
        self.draft_editor.setReadOnly(status != "Draft")
        self._set_toolbar_enabled(status == "Draft")
        self._update_button_states()

    def _update_button_states(self) -> None:
        has_selection = self.current_data is not None
        status_text = str(self.current_data["parent"].get("Status") or "") if has_selection else ""
        is_locked = status_text.strip().lower() in {"locked", "sent", "accepted"}
        has_copy = bool(self.current_customer_copy_path and os.path.exists(self.current_customer_copy_path))
        draft_loaded = self.current_estimate_draft is not None
        draft_status = str(self.current_estimate_draft.get("DraftStatus") or "") if draft_loaded else ""
        draft_is_editable = draft_status == "Draft"

        self.btn_lock.setEnabled(bool(has_selection and not is_locked))
        self.btn_create_copy.setEnabled(bool(has_selection))
        self.btn_view_copy.setEnabled(has_copy)
        self.btn_create_rfq.setEnabled(bool(has_selection and not is_locked))
        self.btn_preview_template.setEnabled(bool(has_selection))
        self.btn_generate_template.setEnabled(bool(has_selection))
        self.btn_load_draft.setEnabled(bool(has_selection))
        self.btn_save_draft.setEnabled(bool(draft_loaded and draft_is_editable))
        self.btn_lock_draft.setEnabled(bool(draft_loaded and draft_is_editable))
        self.btn_workspace_preview.setEnabled(bool(has_selection))
        self.draft_editor.setReadOnly(not draft_is_editable)
        self._set_toolbar_enabled(draft_is_editable)
        template_selection_enabled = bool(has_selection and (not draft_loaded or draft_is_editable))
        self.header_template_combo.setEnabled(
            bool(template_selection_enabled and self._current_selected_template_id(self.header_template_combo) is not None)
        )
        self.body_template_combo.setEnabled(
            bool(template_selection_enabled and self._current_selected_template_id(self.body_template_combo) is not None)
        )
        self.footer_template_combo.setEnabled(
            bool(template_selection_enabled and self._current_selected_template_id(self.footer_template_combo) is not None)
        )

    def _clear_metadata(self) -> None:
        for label in (
            self.customer_value,
            self.site_value,
            self.site_address_value,
            self.contact_value,
            self.estimate_value_label,
            self.created_value,
        ):
            label.setText("—")

    def _apply_metadata(self, data: dict | None) -> None:
        if not data or not data.get("parent"):
            self._clear_metadata()
            return

        parent = data["parent"]
        self.customer_value.setText(str(parent.get("CustomerName") or "—"))
        self.site_value.setText(str(parent.get("SiteName") or "—"))
        self.site_address_value.setText(self._build_site_address(parent))
        self.contact_value.setText(self._build_contact_info(parent))
        self.estimate_value_label.setText(self._estimate_total_for_display(data))
        self.created_value.setText(str(parent.get("CreatedDate") or "—"))

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

    def _build_site_address(self, parent: dict) -> str:
        line = " ".join(
            part
            for part in (
                str(parent.get("SiteNum") or "").strip(),
                str(parent.get("SiteStreet") or "").strip(),
            )
            if part
        ).strip()
        city = str(parent.get("SiteCity") or "").strip()
        if line and city:
            return f"{line}, {city}"
        return line or city or "—"

    def _build_contact_info(self, parent: dict) -> str:
        bits = []
        phone = str(parent.get("Phone") or "").strip()
        email = str(parent.get("Email") or "").strip()
        if phone:
            bits.append(phone)
        if email:
            bits.append(email)
        return " | ".join(bits) if bits else "—"

    def _estimate_total_for_display(self, data: dict) -> str:
        parent = data.get("parent") or {}
        try:
            total = float(parent.get("TotalAmount") or 0.0)
            if total <= 0:
                labor_markup = float(parent.get("LaborMarkUp") or 20.0) / 100.0
                material_markup = float(parent.get("MaterialMarkUp") or 20.0) / 100.0
                labor_total = sum(float(line.get("Hours") or 0.0) * float(line.get("Rate") or 0.0) for line in data.get("labor") or [])
                material_total = sum(
                    float(line.get("Quantity") or 0.0) * float(line.get("UnitCost") or 0.0)
                    for line in data.get("materials") or []
                )
                total = (labor_total * (1 + labor_markup)) + (material_total * (1 + material_markup))
            return self._format_money(total)
        except Exception:
            return "—"

    def _format_money(self, value) -> str:
        try:
            return f"${float(value or 0.0):,.2f}"
        except Exception:
            return "—"

    def _populate_template_dropdowns(self) -> None:
        self._template_choices_by_kind = {
            DocumentTemplateKind.HEADER.value: [],
            DocumentTemplateKind.BODY.value: [],
            DocumentTemplateKind.FOOTER.value: [],
        }
        self._template_defaults_by_kind = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }
        catalog_service = self._get_document_catalog_service()

        try:
            seed_document_control_defaults()
        except Exception:
            pass

        summaries = []
        if catalog_service is not None:
            try:
                summaries = catalog_service.list_templates(document_type_code=ESTIMATE_DOCUMENT_TYPE_CODE)
            except Exception:
                summaries = []
            try:
                default_rows = catalog_service.list_template_defaults(
                    document_type_code=ESTIMATE_DOCUMENT_TYPE_CODE,
                    usage_context=ESTIMATE_DRAFT_WORKSPACE_CONTEXT,
                )
            except Exception:
                default_rows = []
            for default_row in default_rows:
                kind_value = getattr(default_row.kind, "value", str(default_row.kind)).lower()
                if kind_value in self._template_defaults_by_kind:
                    self._template_defaults_by_kind[kind_value] = int(default_row.template_id)

        for summary in summaries:
            kind_value = getattr(summary.kind, "value", str(summary.kind)).lower()
            if kind_value in self._template_choices_by_kind and getattr(summary, "is_active", False):
                self._template_choices_by_kind[kind_value].append(summary)

        self._populate_template_combo(
            self.header_template_combo,
            self._template_choices_by_kind[DocumentTemplateKind.HEADER.value],
            "No active header templates",
        )
        self._populate_template_combo(
            self.body_template_combo,
            self._template_choices_by_kind[DocumentTemplateKind.BODY.value],
            "No active estimate templates",
        )
        self._populate_template_combo(
            self.footer_template_combo,
            self._template_choices_by_kind[DocumentTemplateKind.FOOTER.value],
            "No active footer templates",
        )
        self._apply_template_selection_from_draft()

    def _populate_template_combo(self, combo: QComboBox, summaries: list[object], empty_label: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        if not summaries:
            combo.addItem(empty_label, None)
            combo.setEnabled(False)
            combo.blockSignals(False)
            return

        for summary in summaries:
            label = str(summary.template_name)
            if getattr(summary, "is_active", False):
                label = f"{label} [ACTIVE]"
            combo.addItem(label, int(summary.template_id))
        combo.setCurrentIndex(0)
        combo.setEnabled(True)
        combo.blockSignals(False)

    def _apply_template_selection_from_draft(self) -> None:
        draft = self.current_estimate_draft or {}
        self._select_template_combo_value(
            self.header_template_combo,
            draft.get("HeaderTemplateID"),
            self._template_defaults_by_kind.get(DocumentTemplateKind.HEADER.value),
        )
        self._select_template_combo_value(
            self.body_template_combo,
            draft.get("BodyTemplateID"),
            self._template_defaults_by_kind.get(DocumentTemplateKind.BODY.value),
        )
        self._select_template_combo_value(
            self.footer_template_combo,
            draft.get("FooterTemplateID"),
            self._template_defaults_by_kind.get(DocumentTemplateKind.FOOTER.value),
        )

    def _select_template_combo_value(self, combo: QComboBox, template_id, default_template_id: int | None = None) -> None:
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

    def _get_document_catalog_service(self):
        catalog_service = getattr(getattr(self.parent(), "container", None), "document_catalog_service", None)
        if catalog_service is None and hasattr(self.parent(), "main_window"):
            catalog_service = getattr(getattr(self.parent().main_window, "container", None), "document_catalog_service", None)
        if catalog_service is None:
            main_window = self.window()
            catalog_service = getattr(getattr(main_window, "container", None), "document_catalog_service", None)
        return catalog_service

    def _current_default_template_ids(self) -> dict[str, int | None]:
        return {
            "header_template_id": self._current_selected_template_id(self.header_template_combo),
            "body_template_id": self._current_selected_template_id(self.body_template_combo),
            "footer_template_id": self._current_selected_template_id(self.footer_template_combo),
        }

    def _seed_draft_template_ids_from_dropdowns_if_needed(self) -> None:
        if not self.current_estimate_draft:
            return
        if str(self.current_estimate_draft.get("DraftStatus") or "") != "Draft":
            return
        if any(
            self.current_estimate_draft.get(field)
            for field in ("HeaderTemplateID", "BodyTemplateID", "FooterTemplateID")
        ):
            return

        header_template_id = self._current_selected_template_id(self.header_template_combo)
        body_template_id = self._current_selected_template_id(self.body_template_combo)
        footer_template_id = self._current_selected_template_id(self.footer_template_combo)
        if header_template_id is None and body_template_id is None and footer_template_id is None:
            return

        result = save_draft(
            draft_id=int(self.current_estimate_draft["EstimateDocumentDraftID"]),
            editable_content=str(self.current_estimate_draft.get("EditableContent") or ""),
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
            rendered_preview_html=self.current_estimate_draft.get("RenderedPreviewHtml"),
        )
        if result.get("success"):
            self.current_estimate_draft = result.get("draft")

    def _reset_template_dropdowns(self) -> None:
        self._template_defaults_by_kind = {
            DocumentTemplateKind.HEADER.value: None,
            DocumentTemplateKind.BODY.value: None,
            DocumentTemplateKind.FOOTER.value: None,
        }
        for combo, text in (
            (self.header_template_combo, "Select an estimate first"),
            (self.body_template_combo, "Select an estimate first"),
            (self.footer_template_combo, "Select an estimate first"),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(text, None)
            combo.setEnabled(False)
            combo.blockSignals(False)

    def _load_existing_draft_if_any(self) -> None:
        self.current_estimate_draft = None
        if not self.current_loaded_id:
            self._clear_draft_display()
            return

        result = get_draft_for_display(self.current_loaded_id)
        if result.get("success"):
            self.current_estimate_draft = result.get("draft")
            self._apply_current_draft()
            self._apply_template_selection_from_draft()
        else:
            self._clear_draft_display()
            self._apply_template_selection_from_draft()

    def _load_or_create_current_draft(
        self,
        *,
        auto_create: bool,
        show_success: bool,
        show_not_found: bool,
    ) -> bool:
        if not self.current_loaded_id:
            self.current_estimate_draft = None
            self._clear_draft_display()
            return False

        estimate_id = int(self.current_loaded_id)
        existing = get_draft_for_display(estimate_id)
        result = existing
        if not existing.get("success") and auto_create:
            result = get_or_create_active_draft(
                estimate_id,
                **self._current_default_template_ids(),
            )

        if not result.get("success"):
            self.current_estimate_draft = None
            self._clear_draft_display()
            self._apply_template_selection_from_draft()
            message = str(
                result.get("error")
                or f"Estimate document draft could not be loaded or created for estimate #{estimate_id}."
            )
            print(
                f"[EstimateDocViewPage] Draft load/create failed for EstimateID={estimate_id}: {message}"
            )
            if show_not_found or auto_create:
                QMessageBox.critical(
                    self,
                    "Draft Unavailable",
                    f"Estimate #{estimate_id} draft could not be loaded or created.\n\n{message}",
                )
            self._update_button_states()
            return False

        self.current_estimate_draft = result.get("draft")
        self._seed_draft_template_ids_from_dropdowns_if_needed()
        self._apply_current_draft()
        self._apply_template_selection_from_draft()

        if show_success:
            if result.get("created"):
                content_source = str(result.get("content_source") or "fallback content")
                QMessageBox.information(
                    self,
                    "Draft Created",
                    "A new EstimateDocumentDraft was created for this estimate.\n\n"
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
                    "Draft Loaded",
                    f"Loaded EstimateDocumentDraft #{self.current_estimate_draft.get('EstimateDocumentDraftID')}."
                    f"{hydrated_note}",
                )

        return True

    def _set_draft_editor_content(self, content: str) -> None:
        text = str(content or "")
        lowered = text.strip().lower()
        if (
            lowered.startswith("<!doctype html")
            or lowered.startswith("<html")
            or "<p" in lowered
            or "<div" in lowered
            or "<section" in lowered
            or "<ul" in lowered
            or "<ol" in lowered
        ):
            self.draft_editor.setHtml(text)
        else:
            self.draft_editor.setPlainText(text)

    def _set_toolbar_enabled(self, enabled: bool) -> None:
        if hasattr(self, "draft_toolbar") and self.draft_toolbar is not None:
            self.draft_toolbar.setEnabled(enabled)
