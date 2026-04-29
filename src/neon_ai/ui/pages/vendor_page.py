from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from neon_ai.database.vendors import (
    get_vendor_by_id,
    get_vendor_contacts,
    get_vendor_overview,
    get_vendor_pipeline,
    save_vendor,
)


class VendorPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_vendor_id: int | None = None
        self.vendor_lookup: dict[str, int] = {}
        self.contacts: list[dict[str, str]] = []
        self.contact_edit_index: int | None = None
        self._suspend_pipeline_events = False
        self._loading_vendor = False
        self._ignore_next_pipeline_event = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Vendor Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_data)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_pipeline_panel())
        splitter.addWidget(self._build_workspace_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

    def _build_pipeline_panel(self) -> QGroupBox:
        group = QGroupBox("Vendors Pipeline")
        layout = QVBoxLayout(group)

        self.pipeline_table = QTableWidget(0, 6)
        self.pipeline_table.setHorizontalHeaderLabels(["VendorID", "Vendor", "City", "Phone", "POs", "RFQs"])
        self.pipeline_table.setColumnHidden(0, True)
        self.pipeline_table.setSortingEnabled(True)
        self.pipeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pipeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pipeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pipeline_table.verticalHeader().setVisible(False)
        header = self.pipeline_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.pipeline_table.setColumnWidth(2, 110)
        self.pipeline_table.setColumnWidth(3, 120)
        self.pipeline_table.setColumnWidth(4, 45)
        self.pipeline_table.setColumnWidth(5, 45)
        self.pipeline_table.itemSelectionChanged.connect(self._on_vendor_select)
        layout.addWidget(self.pipeline_table)
        return group

    def _build_workspace_panel(self) -> QGroupBox:
        group = QGroupBox("Vendor Workspace")
        layout = QVBoxLayout(group)

        self.notebook = QTabWidget()
        layout.addWidget(self.notebook)

        self.editor_tab = QWidget()
        self.overview_tab = QWidget()
        self.notebook.addTab(self.editor_tab, "Create / Edit Vendor")
        self.notebook.addTab(self.overview_tab, "Vendor Activity")

        self._build_editor_tab()
        self._build_overview_tab()
        return group

    def _build_editor_tab(self) -> None:
        layout = QVBoxLayout(self.editor_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(QLabel("Load Existing Vendor"))
        self.vendor_combo = QComboBox()
        self.vendor_combo.currentTextChanged.connect(self._on_vendor_combo_selected)
        self.vendor_combo.setMinimumWidth(340)
        header_layout.addWidget(self.vendor_combo)
        new_vendor_button = QPushButton("New Vendor")
        new_vendor_button.clicked.connect(self._prepare_new_vendor)
        header_layout.addWidget(new_vendor_button)
        header_layout.addStretch(1)
        layout.addWidget(header)

        form = QFrame()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)

        self.vendor_fields: dict[str, QLineEdit] = {}
        field_names = [
            "vendor_name",
            "account_number",
            "street_address",
            "city",
            "billing_address",
            "billing_city",
            "main_phone",
        ]
        field_labels = [
            "Vendor Name *",
            "Account Number",
            "Street Address",
            "City",
            "Billing (AR/AP) Address",
            "Billing City",
            "Main Phone",
        ]
        for index in range(0, len(field_names), 2):
            row_frame = QFrame()
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)

            label = QLabel(field_labels[index])
            label.setMinimumWidth(140)
            row_layout.addWidget(label)
            field = QLineEdit()
            self.vendor_fields[field_names[index]] = field
            row_layout.addWidget(field, 1)

            if index + 1 < len(field_names):
                label = QLabel(field_labels[index + 1])
                label.setMinimumWidth(140)
                row_layout.addWidget(label)
                field = QLineEdit()
                self.vendor_fields[field_names[index + 1]] = field
                row_layout.addWidget(field, 1)

            form_layout.addWidget(row_frame)

        layout.addWidget(form)

        contacts_group = QGroupBox("Vendor Contacts")
        contacts_layout = QVBoxLayout(contacts_group)

        contact_entry = QFrame()
        contact_entry_layout = QHBoxLayout(contact_entry)
        contact_entry_layout.setContentsMargins(0, 0, 0, 0)
        contact_entry_layout.setSpacing(8)

        contact_entry_layout.addWidget(QLabel("Name"))
        self.contact_name_field = QLineEdit()
        self.contact_name_field.setMaximumWidth(150)
        contact_entry_layout.addWidget(self.contact_name_field)
        contact_entry_layout.addWidget(QLabel("Phone"))
        self.contact_phone_field = QLineEdit()
        self.contact_phone_field.setMaximumWidth(120)
        contact_entry_layout.addWidget(self.contact_phone_field)
        contact_entry_layout.addWidget(QLabel("Mobile"))
        self.contact_mobile_field = QLineEdit()
        self.contact_mobile_field.setMaximumWidth(120)
        contact_entry_layout.addWidget(self.contact_mobile_field)
        contact_entry_layout.addWidget(QLabel("Email"))
        self.contact_email_field = QLineEdit()
        self.contact_email_field.setMaximumWidth(210)
        contact_entry_layout.addWidget(self.contact_email_field, 1)
        save_contact_button = QPushButton("Add / Update Contact")
        save_contact_button.clicked.connect(self._save_contact_row)
        contact_entry_layout.addWidget(save_contact_button)
        clear_contact_button = QPushButton("Clear Contact")
        clear_contact_button.clicked.connect(self._clear_contact_inputs)
        contact_entry_layout.addWidget(clear_contact_button)
        contacts_layout.addWidget(contact_entry)

        self.contacts_table = QTableWidget(0, 4)
        self.contacts_table.setHorizontalHeaderLabels(["Name", "Phone", "Mobile", "Email"])
        self.contacts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.contacts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.contacts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.contacts_table.verticalHeader().setVisible(False)
        contacts_header = self.contacts_table.horizontalHeader()
        contacts_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.contacts_table.setColumnWidth(0, 150)
        self.contacts_table.setColumnWidth(1, 120)
        self.contacts_table.setColumnWidth(2, 120)
        self.contacts_table.itemSelectionChanged.connect(self._on_contact_select)
        contacts_layout.addWidget(self.contacts_table)

        remove_contact_button = QPushButton("Remove Selected Contact")
        remove_contact_button.clicked.connect(self._remove_contact_row)
        contacts_layout.addWidget(remove_contact_button)
        layout.addWidget(contacts_group, 1)

        save_bar = QFrame()
        save_layout = QHBoxLayout(save_bar)
        save_layout.setContentsMargins(0, 0, 0, 0)
        self.mode_label = QLabel("Mode: New vendor")
        self.mode_label.setStyleSheet("font-weight: 700;")
        save_layout.addWidget(self.mode_label)
        save_layout.addStretch(1)
        save_button = QPushButton("Save Vendor")
        save_button.clicked.connect(self._save_vendor_record)
        save_layout.addWidget(save_button)
        layout.addWidget(save_bar)

    def _build_overview_tab(self) -> None:
        layout = QVBoxLayout(self.overview_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        summary = QFrame()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_vendor_header = QLabel("Select a vendor from the pipeline.")
        self.lbl_vendor_header.setStyleSheet("font-size: 12px; font-weight: 700;")
        summary_layout.addWidget(self.lbl_vendor_header, 1)
        layout.addWidget(summary)

        counts = QFrame()
        counts_layout = QHBoxLayout(counts)
        counts_layout.setContentsMargins(0, 0, 0, 0)
        counts_layout.setSpacing(20)
        self.lbl_summary_rfqs = QLabel("RFQs: 0")
        self.lbl_summary_pos = QLabel("POs: 0")
        self.lbl_summary_invoices = QLabel("Invoices: 0")
        for widget in (self.lbl_summary_rfqs, self.lbl_summary_pos, self.lbl_summary_invoices):
            counts_layout.addWidget(widget)
        counts_layout.addStretch(1)
        layout.addWidget(counts)

        self.rfq_table = self._build_overview_table(["RFQ #", "Estimate #", "Site", "Due", "Status"])
        self.rfq_table.setColumnWidth(0, 70)
        self.rfq_table.setColumnWidth(1, 80)
        self.rfq_table.setColumnWidth(2, 220)
        self.rfq_table.setColumnWidth(3, 90)
        self.rfq_table.setColumnWidth(4, 120)
        layout.addWidget(self._wrap_group("RFQs", self.rfq_table))

        self.po_table = self._build_overview_table(["PO #", "Date", "Site", "Status", "Total"])
        self.po_table.setColumnWidth(0, 70)
        self.po_table.setColumnWidth(1, 90)
        self.po_table.setColumnWidth(2, 220)
        self.po_table.setColumnWidth(3, 120)
        self.po_table.setColumnWidth(4, 100)
        layout.addWidget(self._wrap_group("Purchase Orders", self.po_table))

        self.invoice_table = self._build_overview_table(["Invoice #", "Date", "Status", "Amount"])
        self.invoice_table.setColumnWidth(0, 100)
        self.invoice_table.setColumnWidth(1, 100)
        self.invoice_table.setColumnWidth(2, 120)
        self.invoice_table.setColumnWidth(3, 110)
        layout.addWidget(self._wrap_group("Vendor Invoices", self.invoice_table), 1)

    def _build_overview_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        return table

    def _wrap_group(self, title: str, child: QWidget) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(child)
        return group

    def refresh_data(self) -> None:
        previous_vendor_id = self.active_vendor_id
        self.vendor_lookup = {}
        combo_values: list[str] = []
        selected_label = ""

        self.pipeline_table.setSortingEnabled(False)
        self.pipeline_table.setRowCount(0)

        try:
            rows = get_vendor_pipeline()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to load vendors:\n{exc}")
            return

        for row in rows:
            label = f"{row['VendorName']} (ID {row['VendorID']})"
            self.vendor_lookup[label] = row["VendorID"]
            combo_values.append(label)

            table_row = self.pipeline_table.rowCount()
            self.pipeline_table.insertRow(table_row)
            values = (
                (str(row["VendorID"]), Qt.AlignmentFlag.AlignCenter),
                (row.get("VendorName") or "", Qt.AlignmentFlag.AlignLeft),
                (row.get("VendorCity") or "", Qt.AlignmentFlag.AlignLeft),
                (row.get("MainPhone") or "", Qt.AlignmentFlag.AlignLeft),
                (str(row.get("POCount") or 0), Qt.AlignmentFlag.AlignCenter),
                (str(row.get("RFQCount") or 0), Qt.AlignmentFlag.AlignCenter),
            )
            for col_index, (text, alignment) in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(alignment)
                self.pipeline_table.setItem(table_row, col_index, item)

            if previous_vendor_id and int(row["VendorID"]) == int(previous_vendor_id):
                selected_label = label

        self.pipeline_table.setSortingEnabled(True)

        self.vendor_combo.blockSignals(True)
        self.vendor_combo.clear()
        self.vendor_combo.addItems(combo_values)
        if selected_label:
            self.vendor_combo.setCurrentText(selected_label)
        self.vendor_combo.blockSignals(False)

        if selected_label:
            self._select_vendor(self.vendor_lookup[selected_label], switch_tab="editor")
        elif not rows:
            self._prepare_new_vendor()
            self._clear_overview()
        else:
            self.vendor_combo.setCurrentIndex(-1)
            self.active_vendor_id = None
            self._clear_overview()

    def _prepare_new_vendor(self) -> None:
        self.active_vendor_id = None
        self.vendor_combo.setCurrentIndex(-1)
        for field in self.vendor_fields.values():
            field.setText("")
        self.contacts = []
        self._render_contacts()
        self._clear_contact_inputs()
        self.mode_label.setText("Mode: New vendor")
        self.notebook.setCurrentWidget(self.editor_tab)

    def _select_vendor(self, vendor_id: int, switch_tab: str | None = None) -> None:
        if not vendor_id or self._loading_vendor:
            return

        self._loading_vendor = True
        vendor_id = int(vendor_id)
        self.active_vendor_id = vendor_id
        try:
            vendor = get_vendor_by_id(vendor_id)
            contacts = get_vendor_contacts(vendor_id)
            overview = get_vendor_overview(vendor_id)
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to load vendor details:\n{exc}")
            return
        finally:
            self._loading_vendor = False

        if not vendor:
            return

        self.vendor_fields["vendor_name"].setText(vendor.get("VendorName") or "")
        self.vendor_fields["account_number"].setText("" if vendor.get("AccountNumber") is None else str(vendor.get("AccountNumber")))
        self.vendor_fields["street_address"].setText(vendor.get("VendorAddressNumber") or "")
        self.vendor_fields["city"].setText(vendor.get("VendorCity") or "")
        self.vendor_fields["billing_address"].setText(vendor.get("BillingAddress") or "")
        self.vendor_fields["billing_city"].setText(vendor.get("BillingCity") or "")
        self.vendor_fields["main_phone"].setText(vendor.get("VendorMainPhone") or vendor.get("VendorContactNumber") or "")

        self.contacts = [
            {
                "name": row.get("ContactName") or "",
                "phone": row.get("Phone") or "",
                "mobile": row.get("MobilePhone") or "",
                "email": row.get("Email") or "",
            }
            for row in contacts
        ]
        self._render_contacts()
        self._clear_contact_inputs()
        self.mode_label.setText(f"Mode: Editing vendor #{vendor_id}")

        combo_label = next((label for label, vid in self.vendor_lookup.items() if int(vid) == vendor_id), "")
        if combo_label:
            self.vendor_combo.blockSignals(True)
            self.vendor_combo.setCurrentText(combo_label)
            self.vendor_combo.blockSignals(False)

        self._load_overview(vendor, overview)
        if switch_tab == "overview":
            self.notebook.setCurrentWidget(self.overview_tab)
        elif switch_tab == "editor":
            self.notebook.setCurrentWidget(self.editor_tab)

    def _load_overview(self, vendor: dict, overview: dict) -> None:
        self.lbl_vendor_header.setText(
            f"{vendor.get('VendorName') or 'Vendor'}  |  Main Phone: "
            f"{vendor.get('VendorMainPhone') or vendor.get('VendorContactNumber') or 'Not on file'}"
        )

        for table in (self.rfq_table, self.po_table, self.invoice_table):
            table.setRowCount(0)

        for row in overview.get("rfqs", []):
            self._append_row(
                self.rfq_table,
                [
                    str(row.get("PriceRequestID") or ""),
                    str(row.get("EstimateID") or ""),
                    str(row.get("SiteName") or ""),
                    str(row.get("DueDate") or ""),
                    str(row.get("Status") or ""),
                ],
                {0: Qt.AlignmentFlag.AlignCenter, 1: Qt.AlignmentFlag.AlignCenter, 3: Qt.AlignmentFlag.AlignCenter, 4: Qt.AlignmentFlag.AlignCenter},
            )

        for row in overview.get("purchase_orders", []):
            self._append_row(
                self.po_table,
                [
                    str(row.get("PurchaseOrderID") or ""),
                    str(row.get("Date") or ""),
                    str(row.get("SiteName") or ""),
                    str(row.get("Status") or ""),
                    f"${float(row.get('PurchaseOrderTotal') or 0):,.2f}",
                ],
                {0: Qt.AlignmentFlag.AlignCenter, 1: Qt.AlignmentFlag.AlignCenter, 3: Qt.AlignmentFlag.AlignCenter, 4: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter},
            )

        for row in overview.get("invoices", []):
            self._append_row(
                self.invoice_table,
                [
                    str(row.get("VendorInvoiceNumber") or row.get("VendorInvoiceID") or ""),
                    str(row.get("VendorInvoiceDate") or ""),
                    str(row.get("VendorInvoiceStatus") or ""),
                    f"${float(row.get('VendorInvoiceAmount') or 0):,.2f}",
                ],
                {0: Qt.AlignmentFlag.AlignCenter, 1: Qt.AlignmentFlag.AlignCenter, 2: Qt.AlignmentFlag.AlignCenter, 3: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter},
            )

        self.lbl_summary_rfqs.setText(f"RFQs: {len(overview.get('rfqs', []))}")
        self.lbl_summary_pos.setText(f"POs: {len(overview.get('purchase_orders', []))}")
        self.lbl_summary_invoices.setText(f"Invoices: {len(overview.get('invoices', []))}")

    def _clear_overview(self) -> None:
        self.lbl_vendor_header.setText("Select a vendor from the pipeline.")
        self.lbl_summary_rfqs.setText("RFQs: 0")
        self.lbl_summary_pos.setText("POs: 0")
        self.lbl_summary_invoices.setText("Invoices: 0")
        for table in (self.rfq_table, self.po_table, self.invoice_table):
            table.setRowCount(0)

    def _append_row(self, table: QTableWidget, values: list[str], alignments: dict[int, Qt.AlignmentFlag] | None = None) -> None:
        alignments = alignments or {}
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(alignments.get(column, Qt.AlignmentFlag.AlignLeft))
            table.setItem(row, column, item)

    def _on_vendor_select(self) -> None:
        if self._ignore_next_pipeline_event:
            self._ignore_next_pipeline_event = False
            return
        if self._suspend_pipeline_events or self._loading_vendor:
            return
        selection = self.pipeline_table.selectedItems()
        if not selection:
            return
        vendor_id = self.pipeline_table.item(self.pipeline_table.row(selection[0]), 0).text()
        if vendor_id:
            self._select_vendor(int(vendor_id), switch_tab="overview")

    def _on_vendor_combo_selected(self, label: str) -> None:
        if not label:
            return
        vendor_id = self.vendor_lookup.get(label)
        if vendor_id:
            self._select_vendor(vendor_id, switch_tab="editor")

    def _save_contact_row(self) -> None:
        name = self.contact_name_field.text().strip()
        phone = self.contact_phone_field.text().strip()
        mobile = self.contact_mobile_field.text().strip()
        email = self.contact_email_field.text().strip()
        if not any((name, phone, mobile, email)):
            QMessageBox.critical(self, "Validation Error", "Enter at least one contact field before adding the row.")
            return

        payload = {"name": name, "phone": phone, "mobile": mobile, "email": email}
        if self.contact_edit_index is None:
            self.contacts.append(payload)
        else:
            self.contacts[self.contact_edit_index] = payload

        self._render_contacts()
        self._clear_contact_inputs()

    def _render_contacts(self) -> None:
        self.contacts_table.setRowCount(len(self.contacts))
        for index, contact in enumerate(self.contacts):
            self.contacts_table.setItem(index, 0, QTableWidgetItem(contact["name"]))
            self.contacts_table.setItem(index, 1, QTableWidgetItem(contact["phone"]))
            self.contacts_table.setItem(index, 2, QTableWidgetItem(contact["mobile"]))
            self.contacts_table.setItem(index, 3, QTableWidgetItem(contact["email"]))

    def _on_contact_select(self) -> None:
        selection = self.contacts_table.selectedItems()
        if not selection:
            return
        row = self.contacts_table.row(selection[0])
        contact = self.contacts[row]
        self.contact_edit_index = row
        self.contact_name_field.setText(contact.get("name") or "")
        self.contact_phone_field.setText(contact.get("phone") or "")
        self.contact_mobile_field.setText(contact.get("mobile") or "")
        self.contact_email_field.setText(contact.get("email") or "")

    def _clear_contact_inputs(self) -> None:
        self.contact_edit_index = None
        self.contact_name_field.setText("")
        self.contact_phone_field.setText("")
        self.contact_mobile_field.setText("")
        self.contact_email_field.setText("")
        self.contacts_table.clearSelection()

    def _remove_contact_row(self) -> None:
        selection = self.contacts_table.selectedItems()
        if not selection:
            QMessageBox.information(self, "Contacts", "Select a contact row to remove.")
            return
        row = self.contacts_table.row(selection[0])
        del self.contacts[row]
        self._render_contacts()
        self._clear_contact_inputs()

    def _save_vendor_record(self) -> None:
        vendor_name = self.vendor_fields["vendor_name"].text().strip()
        if not vendor_name:
            QMessageBox.critical(self, "Validation Error", "Vendor Name is required.")
            return

        payload = {
            "vendor_name": vendor_name,
            "account_number": self.vendor_fields["account_number"].text().strip(),
            "street_address": self.vendor_fields["street_address"].text().strip(),
            "city": self.vendor_fields["city"].text().strip(),
            "billing_address": self.vendor_fields["billing_address"].text().strip(),
            "billing_city": self.vendor_fields["billing_city"].text().strip(),
            "main_phone": self.vendor_fields["main_phone"].text().strip(),
        }

        try:
            saved_vendor_id = save_vendor(self.active_vendor_id, payload, self.contacts)
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to save vendor:\n{exc}")
            return

        action = "updated" if self.active_vendor_id else "saved"
        self.active_vendor_id = saved_vendor_id
        self.refresh_data()
        self._select_vendor(saved_vendor_id, switch_tab="editor")
        QMessageBox.information(self, "Success", f"Vendor '{vendor_name}' {action} successfully.")
