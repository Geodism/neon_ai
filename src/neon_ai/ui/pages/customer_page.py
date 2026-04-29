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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

from neon_ai.database.automation import send_customer_address_verification_request
from neon_ai.database.customers import (
    get_customer_by_id,
    get_customer_contacts,
    get_customer_pipeline,
    get_customer_portfolio,
    save_customer,
)


class CustomerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_customer_id: int | None = None
        self.customer_lookup: dict[str, int] = {}
        self.contacts: list[dict[str, str]] = []
        self.contact_edit_index: int | None = None
        self._suspend_pipeline_events = False
        self._loading_customer = False
        self._ignore_next_pipeline_event = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Customer Command")
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
        group = QGroupBox("Customers Pipeline")
        layout = QVBoxLayout(group)

        self.pipeline_table = QTableWidget(0, 6)
        self.pipeline_table.setHorizontalHeaderLabels(["CustomerID", "Customer", "City", "Phone", "Sites", "WOs"])
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
        self.pipeline_table.itemSelectionChanged.connect(self._on_customer_select)
        layout.addWidget(self.pipeline_table)

        return group

    def _build_workspace_panel(self) -> QGroupBox:
        group = QGroupBox("Customer Workspace")
        layout = QVBoxLayout(group)

        self.notebook = QTabWidget()
        layout.addWidget(self.notebook)

        self.editor_tab = QWidget()
        self.portfolio_tab = QWidget()
        self.notebook.addTab(self.editor_tab, "Create / Edit Customer")
        self.notebook.addTab(self.portfolio_tab, "Customer Portfolio")

        self._build_editor_tab()
        self._build_portfolio_tab()
        return group

    def _build_editor_tab(self) -> None:
        layout = QVBoxLayout(self.editor_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(QLabel("Load Existing Customer"))
        self.customer_combo = QComboBox()
        self.customer_combo.currentTextChanged.connect(self._on_customer_combo_selected)
        self.customer_combo.setMinimumWidth(340)
        header_layout.addWidget(self.customer_combo)
        new_customer_button = QPushButton("New Customer")
        new_customer_button.clicked.connect(self._prepare_new_customer)
        header_layout.addWidget(new_customer_button)
        header_layout.addStretch(1)
        layout.addWidget(header)

        form = QFrame()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)

        self.customer_fields: dict[str, QLineEdit] = {}
        field_names = [
            "customer_name",
            "address",
            "street_name",
            "city_name",
            "postal_code",
            "email",
            "phone",
        ]
        field_labels = [
            "Customer Name *",
            "Address Number",
            "Street Name",
            "City Name",
            "Postal Code",
            "Main Email",
            "Main Phone",
        ]
        for index in range(0, len(field_names), 2):
            row_frame = QFrame()
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)

            label = QLabel(field_labels[index])
            label.setMinimumWidth(120)
            row_layout.addWidget(label)
            field = QLineEdit()
            self.customer_fields[field_names[index]] = field
            row_layout.addWidget(field, 1)

            if index + 1 < len(field_names):
                label = QLabel(field_labels[index + 1])
                label.setMinimumWidth(120)
                row_layout.addWidget(label)
                field = QLineEdit()
                self.customer_fields[field_names[index + 1]] = field
                row_layout.addWidget(field, 1)

            form_layout.addWidget(row_frame)

        layout.addWidget(form)

        contacts_group = QGroupBox("Customer Contacts")
        contacts_layout = QVBoxLayout(contacts_group)

        contact_entry = QFrame()
        contact_entry_layout = QHBoxLayout(contact_entry)
        contact_entry_layout.setContentsMargins(0, 0, 0, 0)
        contact_entry_layout.setSpacing(8)

        contact_entry_layout.addWidget(QLabel("Name"))
        self.contact_name_field = QLineEdit()
        self.contact_name_field.setMaximumWidth(180)
        contact_entry_layout.addWidget(self.contact_name_field)
        contact_entry_layout.addWidget(QLabel("Phone"))
        self.contact_phone_field = QLineEdit()
        self.contact_phone_field.setMaximumWidth(130)
        contact_entry_layout.addWidget(self.contact_phone_field)
        contact_entry_layout.addWidget(QLabel("Email"))
        self.contact_email_field = QLineEdit()
        self.contact_email_field.setMaximumWidth(220)
        contact_entry_layout.addWidget(self.contact_email_field, 1)
        save_contact_button = QPushButton("Add / Update Contact")
        save_contact_button.clicked.connect(self._save_contact_row)
        contact_entry_layout.addWidget(save_contact_button)
        clear_contact_button = QPushButton("Clear Contact")
        clear_contact_button.clicked.connect(self._clear_contact_inputs)
        contact_entry_layout.addWidget(clear_contact_button)
        contacts_layout.addWidget(contact_entry)

        self.contacts_table = QTableWidget(0, 3)
        self.contacts_table.setHorizontalHeaderLabels(["Name", "Phone", "Email"])
        self.contacts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.contacts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.contacts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.contacts_table.verticalHeader().setVisible(False)
        contacts_header = self.contacts_table.horizontalHeader()
        contacts_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.contacts_table.setColumnWidth(0, 180)
        self.contacts_table.setColumnWidth(1, 130)
        self.contacts_table.itemSelectionChanged.connect(self._on_contact_select)
        contacts_layout.addWidget(self.contacts_table)
        remove_contact_button = QPushButton("Remove Selected Contact")
        remove_contact_button.clicked.connect(self._remove_contact_row)
        contacts_layout.addWidget(remove_contact_button)

        layout.addWidget(contacts_group, 1)

        save_bar = QFrame()
        save_layout = QHBoxLayout(save_bar)
        save_layout.setContentsMargins(0, 0, 0, 0)
        self.mode_label = QLabel("Mode: New customer")
        self.mode_label.setStyleSheet("font-weight: 700;")
        save_layout.addWidget(self.mode_label)
        save_layout.addStretch(1)
        address_button = QPushButton("Update Address")
        address_button.clicked.connect(self._send_address_update_request)
        save_layout.addWidget(address_button)
        save_button = QPushButton("Save Customer")
        save_button.clicked.connect(self._save_customer_record)
        save_layout.addWidget(save_button)
        layout.addWidget(save_bar)

    def _build_portfolio_tab(self) -> None:
        layout = QVBoxLayout(self.portfolio_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        summary = QFrame()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(20)
        self.lbl_portfolio_customer = QLabel("Select a customer from the pipeline.")
        self.lbl_portfolio_customer.setStyleSheet("font-size: 12px; font-weight: 700;")
        summary_layout.addWidget(self.lbl_portfolio_customer, 1)
        layout.addWidget(summary)

        counts = QFrame()
        counts_layout = QHBoxLayout(counts)
        counts_layout.setContentsMargins(0, 0, 0, 0)
        counts_layout.setSpacing(20)
        self.lbl_summary_sites = QLabel("Sites: 0")
        self.lbl_summary_estimates = QLabel("Estimates: 0")
        self.lbl_summary_workorders = QLabel("Work Orders: 0")
        self.lbl_summary_owing = QLabel("Total Owing: $0.00")
        self.lbl_summary_owing.setStyleSheet("font-weight: 700;")
        for widget in (
            self.lbl_summary_sites,
            self.lbl_summary_estimates,
            self.lbl_summary_workorders,
            self.lbl_summary_owing,
        ):
            counts_layout.addWidget(widget)
        counts_layout.addStretch(1)
        layout.addWidget(counts)

        self.sites_table = self._build_portfolio_table(["Site", "Address"])
        self.sites_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.sites_table.setColumnWidth(0, 220)
        layout.addWidget(self._wrap_group("Sites", self.sites_table))

        self.estimates_table = self._build_portfolio_table(["Estimate #", "Created", "Site", "Status", "Value"])
        self.estimates_table.setColumnWidth(0, 80)
        self.estimates_table.setColumnWidth(1, 90)
        self.estimates_table.setColumnWidth(2, 220)
        self.estimates_table.setColumnWidth(3, 120)
        self.estimates_table.setColumnWidth(4, 100)
        layout.addWidget(self._wrap_group("Estimates", self.estimates_table))

        self.workorders_table = self._build_portfolio_table(["WO #", "Site", "Status", "WO Value", "Owing"])
        self.workorders_table.setColumnWidth(0, 70)
        self.workorders_table.setColumnWidth(1, 220)
        self.workorders_table.setColumnWidth(2, 120)
        self.workorders_table.setColumnWidth(3, 110)
        self.workorders_table.setColumnWidth(4, 110)
        layout.addWidget(self._wrap_group("Work Orders", self.workorders_table), 1)

    def _build_portfolio_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        return table

    def _wrap_group(self, title: str, child: QWidget) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(child)
        return group

    def refresh_data(self) -> None:
        previous_customer_id = self.active_customer_id
        self.customer_lookup = {}
        combo_values: list[str] = []
        selected_label = ""

        self.pipeline_table.setSortingEnabled(False)
        self.pipeline_table.setRowCount(0)

        try:
            rows = get_customer_pipeline()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to load customers:\n{exc}")
            return

        for row in rows:
            label = f"{row['CustomerName']} (ID {row['CustomerID']})"
            self.customer_lookup[label] = row["CustomerID"]
            combo_values.append(label)

            table_row = self.pipeline_table.rowCount()
            self.pipeline_table.insertRow(table_row)
            values = (
                (str(row["CustomerID"]), Qt.AlignmentFlag.AlignCenter),
                (row.get("CustomerName") or "", Qt.AlignmentFlag.AlignLeft),
                (row.get("CityName") or "", Qt.AlignmentFlag.AlignLeft),
                (row.get("Phone") or "", Qt.AlignmentFlag.AlignLeft),
                (str(row.get("SiteCount") or 0), Qt.AlignmentFlag.AlignCenter),
                (str(row.get("WorkOrderCount") or 0), Qt.AlignmentFlag.AlignCenter),
            )
            for col_index, (text, alignment) in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(alignment)
                self.pipeline_table.setItem(table_row, col_index, item)

            if previous_customer_id and int(row["CustomerID"]) == int(previous_customer_id):
                selected_label = label

        self.pipeline_table.setSortingEnabled(True)

        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        self.customer_combo.addItems(combo_values)
        if selected_label:
            self.customer_combo.setCurrentText(selected_label)
        self.customer_combo.blockSignals(False)

        if selected_label:
            self._select_customer(self.customer_lookup[selected_label], switch_tab="editor")
        elif not rows:
            self._prepare_new_customer()
            self._clear_portfolio()
        else:
            self.customer_combo.setCurrentIndex(-1)
            self.active_customer_id = None
            self._clear_portfolio()

    def _prepare_new_customer(self) -> None:
        self.active_customer_id = None
        self.customer_combo.setCurrentIndex(-1)
        for field in self.customer_fields.values():
            field.setText("")
        self.contacts = []
        self._render_contacts()
        self._clear_contact_inputs()
        self.mode_label.setText("Mode: New customer")
        self.notebook.setCurrentWidget(self.editor_tab)

    def _select_customer(self, customer_id: int, switch_tab: str | None = None) -> None:
        if not customer_id or self._loading_customer:
            return

        customer_id = int(customer_id)
        self.active_customer_id = customer_id
        self._loading_customer = True

        try:
            customer = get_customer_by_id(customer_id)
            contacts = get_customer_contacts(customer_id)
            portfolio = get_customer_portfolio(customer_id)
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to load customer details:\n{exc}")
            return
        finally:
            self._loading_customer = False

        if not customer:
            return

        self.customer_fields["customer_name"].setText(customer.get("CustomerName") or "")
        self.customer_fields["address"].setText(customer.get("Address") or "")
        self.customer_fields["street_name"].setText(customer.get("StreetName") or "")
        self.customer_fields["city_name"].setText(customer.get("CityName") or "")
        self.customer_fields["postal_code"].setText(customer.get("PostalCode") or "")
        self.customer_fields["email"].setText(customer.get("Email") or "")
        self.customer_fields["phone"].setText(customer.get("Phone") or "")

        self.contacts = [
            {
                "name": contact.get("ContactName") or "",
                "phone": contact.get("Phone") or "",
                "email": contact.get("Email") or "",
            }
            for contact in contacts
        ]
        self._render_contacts()
        self._clear_contact_inputs()
        self.mode_label.setText(f"Mode: Editing customer #{customer_id}")

        combo_label = next((label for label, cid in self.customer_lookup.items() if int(cid) == customer_id), "")
        if combo_label:
            self.customer_combo.blockSignals(True)
            self.customer_combo.setCurrentText(combo_label)
            self.customer_combo.blockSignals(False)

        self._load_portfolio(customer, portfolio)
        if switch_tab == "portfolio":
            self.notebook.setCurrentWidget(self.portfolio_tab)
        elif switch_tab == "editor":
            self.notebook.setCurrentWidget(self.editor_tab)

    def _load_portfolio(self, customer: dict, portfolio: dict) -> None:
        self.lbl_portfolio_customer.setText(
            f"{customer.get('CustomerName') or 'Customer'}  |  "
            f"{customer.get('Email') or 'No email'}  |  "
            f"{customer.get('Phone') or 'No phone'}"
        )

        for table in (self.sites_table, self.estimates_table, self.workorders_table):
            table.setRowCount(0)

        total_owing = 0.0

        for site in portfolio.get("sites", []):
            address = " ".join(
                part
                for part in [
                    site.get("StreetNumber") or "",
                    site.get("StreetName") or "",
                    site.get("City") or "",
                ]
                if part
            )
            self._append_row(self.sites_table, [site.get("SiteName") or "", address])

        for estimate in portfolio.get("estimates", []):
            self._append_row(
                self.estimates_table,
                [
                    str(estimate.get("EstimateID") or ""),
                    str(estimate.get("CreatedDate") or ""),
                    str(estimate.get("SiteName") or ""),
                    str(estimate.get("Status") or ""),
                    f"${float(estimate.get('TotalAmount') or 0):,.2f}",
                ],
                alignments={
                    0: Qt.AlignmentFlag.AlignCenter,
                    4: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                },
            )

        for work_order in portfolio.get("work_orders", []):
            owing = float(work_order.get("Owing") or 0)
            total_owing += owing
            self._append_row(
                self.workorders_table,
                [
                    str(work_order.get("WorkOrderID") or ""),
                    str(work_order.get("SiteName") or ""),
                    str(work_order.get("WOStatus") or ""),
                    f"${float(work_order.get('WOValue') or 0):,.2f}",
                    f"${owing:,.2f}",
                ],
                alignments={
                    0: Qt.AlignmentFlag.AlignCenter,
                    3: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    4: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                },
            )

        self.lbl_summary_sites.setText(f"Sites: {len(portfolio.get('sites', []))}")
        self.lbl_summary_estimates.setText(f"Estimates: {len(portfolio.get('estimates', []))}")
        self.lbl_summary_workorders.setText(f"Work Orders: {len(portfolio.get('work_orders', []))}")
        self.lbl_summary_owing.setText(f"Total Owing: ${total_owing:,.2f}")

    def _clear_portfolio(self) -> None:
        self.lbl_portfolio_customer.setText("Select a customer from the pipeline.")
        self.lbl_summary_sites.setText("Sites: 0")
        self.lbl_summary_estimates.setText("Estimates: 0")
        self.lbl_summary_workorders.setText("Work Orders: 0")
        self.lbl_summary_owing.setText("Total Owing: $0.00")
        for table in (self.sites_table, self.estimates_table, self.workorders_table):
            table.setRowCount(0)

    def _append_row(
        self,
        table: QTableWidget,
        values: list[str],
        alignments: dict[int, Qt.AlignmentFlag] | None = None,
    ) -> None:
        alignments = alignments or {}
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(alignments.get(column, Qt.AlignmentFlag.AlignLeft))
            table.setItem(row, column, item)

    def _on_customer_select(self) -> None:
        if self._ignore_next_pipeline_event:
            self._ignore_next_pipeline_event = False
            return
        if self._suspend_pipeline_events or self._loading_customer:
            return
        selection = self.pipeline_table.selectedItems()
        if not selection:
            return
        customer_id = self.pipeline_table.item(self.pipeline_table.row(selection[0]), 0).text()
        if customer_id:
            self._select_customer(int(customer_id), switch_tab="portfolio")

    def _on_customer_combo_selected(self, label: str) -> None:
        if not label:
            return
        customer_id = self.customer_lookup.get(label)
        if customer_id:
            self._select_customer(customer_id, switch_tab="editor")

    def _save_contact_row(self) -> None:
        name = self.contact_name_field.text().strip()
        phone = self.contact_phone_field.text().strip()
        email = self.contact_email_field.text().strip()
        if not any((name, phone, email)):
            QMessageBox.critical(self, "Validation Error", "Enter at least one contact field before adding the row.")
            return

        payload = {"name": name, "phone": phone, "email": email}
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
            self.contacts_table.setItem(index, 2, QTableWidgetItem(contact["email"]))

    def _on_contact_select(self) -> None:
        selection = self.contacts_table.selectedItems()
        if not selection:
            return
        row = self.contacts_table.row(selection[0])
        contact = self.contacts[row]
        self.contact_edit_index = row
        self.contact_name_field.setText(contact.get("name") or "")
        self.contact_phone_field.setText(contact.get("phone") or "")
        self.contact_email_field.setText(contact.get("email") or "")

    def _clear_contact_inputs(self) -> None:
        self.contact_edit_index = None
        self.contact_name_field.setText("")
        self.contact_phone_field.setText("")
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

    def _save_customer_record(self) -> None:
        customer_name = self.customer_fields["customer_name"].text().strip()
        if not customer_name:
            QMessageBox.critical(self, "Validation Error", "Customer Name is required.")
            return

        payload = {
            "customer_name": customer_name,
            "address": self.customer_fields["address"].text().strip(),
            "street_name": self.customer_fields["street_name"].text().strip(),
            "city_name": self.customer_fields["city_name"].text().strip(),
            "postal_code": self.customer_fields["postal_code"].text().strip(),
            "email": self.customer_fields["email"].text().strip(),
            "phone": self.customer_fields["phone"].text().strip(),
        }

        try:
            saved_customer_id = save_customer(self.active_customer_id, payload, self.contacts)
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Failed to save customer:\n{exc}")
            return

        action = "updated" if self.active_customer_id else "saved"
        self.active_customer_id = saved_customer_id
        self.refresh_data()
        self._select_customer(saved_customer_id, switch_tab="editor")
        QMessageBox.information(self, "Success", f"Customer '{customer_name}' {action} successfully.")

    def _send_address_update_request(self) -> None:
        if not self.active_customer_id:
            QMessageBox.information(self, "Update Address", "Select or load a customer first.")
            return

        try:
            result = send_customer_address_verification_request(int(self.active_customer_id))
        except Exception as exc:
            QMessageBox.critical(self, "Address Request", f"Could not send the verification email:\n{exc}")
            return

        QMessageBox.information(
            self,
            "Address Request Sent",
            f"Verification email sent to {result['email']}.\n\n"
            "Argon will update the customer and site records when the customer replies.",
        )
