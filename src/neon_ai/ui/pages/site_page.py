from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QLabel,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QPushButton,
    QLineEdit,
    QGroupBox,
    QHeaderView,
)

from neon_ai.database.customers import get_customers, get_sites, save_site, delete_site


class SitePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.customer_dict = {}
        self.site_lookup = {}
        self.active_site_id = None
        self._loading_site = False
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        # Title
        title = QLabel("Site Command")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)
        
        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        
        # Left panel: Sites list
        left_group = QGroupBox("Sites")
        left_layout = QVBoxLayout(left_group)
        
        self.sites_table = QTableWidget(0, 4)
        self.sites_table.setHorizontalHeaderLabels(["ID", "Customer", "Site", "Address"])
        self.sites_table.setColumnHidden(0, True)
        self.sites_table.setSortingEnabled(True)
        self.sites_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sites_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sites_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        
        header = self.sites_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.sites_table.setColumnWidth(2, 170)
        self.sites_table.setColumnWidth(3, 280)
        
        self.sites_table.itemSelectionChanged.connect(self._on_site_select)
        left_layout.addWidget(self.sites_table)
        splitter.addWidget(left_group)
        splitter.setStretchFactor(0, 2)
        
        # Right panel: Create/Edit site
        right_group = QGroupBox("Create / Edit Site")
        right_layout = QVBoxLayout(right_group)
        
        self.mode_label = QLabel("Mode: New site")
        self.mode_label.setStyleSheet("font-weight: 700;")
        right_layout.addWidget(self.mode_label)
        
        # Customer field
        customer_frame = QHBoxLayout()
        customer_frame.addWidget(QLabel("Customer *"))
        customer_frame.setContentsMargins(0, 0, 0, 0)
        self.customer_combo = QComboBox()
        customer_frame.addWidget(self.customer_combo)
        customer_frame.addStretch(1)
        right_layout.addLayout(customer_frame)
        
        # Site Name field
        sitename_frame = QHBoxLayout()
        sitename_frame.addWidget(QLabel("Site Name *"))
        sitename_frame.setContentsMargins(0, 0, 0, 0)
        self.site_name_field = QLineEdit()
        sitename_frame.addWidget(self.site_name_field)
        sitename_frame.addStretch(1)
        right_layout.addLayout(sitename_frame)
        
        # Street Number field
        street_num_frame = QHBoxLayout()
        street_num_frame.addWidget(QLabel("Street Number"))
        street_num_frame.setContentsMargins(0, 0, 0, 0)
        self.street_number_field = QLineEdit()
        street_num_frame.addWidget(self.street_number_field)
        street_num_frame.addStretch(1)
        self.street_number_field.textChanged.connect(self._update_address_preview)
        right_layout.addLayout(street_num_frame)
        
        # Street Name field
        street_name_frame = QHBoxLayout()
        street_name_frame.addWidget(QLabel("Street Name"))
        street_name_frame.setContentsMargins(0, 0, 0, 0)
        self.street_name_field = QLineEdit()
        street_name_frame.addWidget(self.street_name_field)
        street_name_frame.addStretch(1)
        self.street_name_field.textChanged.connect(self._update_address_preview)
        right_layout.addLayout(street_name_frame)
        
        # City field
        city_frame = QHBoxLayout()
        city_frame.addWidget(QLabel("City"))
        city_frame.setContentsMargins(0, 0, 0, 0)
        self.city_field = QLineEdit()
        city_frame.addWidget(self.city_field)
        city_frame.addStretch(1)
        self.city_field.textChanged.connect(self._update_address_preview)
        right_layout.addLayout(city_frame)
        
        # Address preview
        self.address_preview = QLabel("Address Preview: ")
        self.address_preview.setStyleSheet("color: gray;")
        right_layout.addWidget(self.address_preview)
        
        right_layout.addStretch(1)
        
        # Button bar
        button_frame = QHBoxLayout()
        button_frame.setContentsMargins(0, 0, 0, 0)
        button_frame.addStretch(1)
        
        new_btn = QPushButton("New Site")
        new_btn.clicked.connect(self._prepare_new_site)
        button_frame.addWidget(new_btn)
        
        delete_btn = QPushButton("Delete Site")
        delete_btn.clicked.connect(self._delete_selected_site)
        button_frame.addWidget(delete_btn)
        
        save_btn = QPushButton("Save Site")
        save_btn.clicked.connect(self._save_site_record)
        button_frame.addWidget(save_btn)
        
        right_layout.addLayout(button_frame)
        splitter.addWidget(right_group)
        splitter.setStretchFactor(1, 3)
        
        self.refresh_data()
    
    def refresh_data(self) -> None:
        """Load customers and sites."""
        self.customer_dict = {
            f"{row['CustomerName']} (ID {row['CustomerID']})": row["CustomerID"]
            for row in get_customers()
        }
        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        self.customer_combo.addItems(self.customer_dict.keys())
        self.customer_combo.blockSignals(False)
        
        self.site_lookup.clear()
        self.sites_table.setRowCount(0)
        
        for row in get_sites():
            site_id = row["SiteID"]
            self.site_lookup[str(site_id)] = row
            
            table_row = self.sites_table.rowCount()
            self.sites_table.insertRow(table_row)
            
            self.sites_table.setItem(table_row, 0, QTableWidgetItem(str(site_id)))
            self.sites_table.setItem(table_row, 1, QTableWidgetItem(row.get("CustomerName") or ""))
            self.sites_table.setItem(table_row, 2, QTableWidgetItem(row.get("SiteName") or ""))
            self.sites_table.setItem(table_row, 3, QTableWidgetItem(self._format_address(row)))
        
        self._prepare_new_site()
    
    def _format_address(self, row: dict) -> str:
        """Format address from components."""
        return " ".join(
            part
            for part in [
                str(row.get("StreetNumber") or "").strip(),
                str(row.get("StreetName") or "").strip(),
                str(row.get("City") or "").strip(),
            ]
            if part
        )
    
    def _prepare_new_site(self) -> None:
        """Clear form for new site entry."""
        self.active_site_id = None
        self._loading_site = False
        self.customer_combo.setCurrentIndex(-1)
        self.site_name_field.setText("")
        self.street_number_field.setText("")
        self.street_name_field.setText("")
        self.city_field.setText("")
        self.mode_label.setText("Mode: New site")
        self._update_address_preview()
        self.sites_table.clearSelection()
    
    def _on_site_select(self) -> None:
        """Handle site selection."""
        selection = self.sites_table.selectedItems()
        if not selection:
            return
        
        row = self.sites_table.row(selection[0])
        site_id_str = self.sites_table.item(row, 0).text()
        row_data = self.site_lookup.get(site_id_str)
        
        if not row_data:
            return
        
        self._loading_site = True
        self.active_site_id = int(row_data["SiteID"])
        customer_id = int(row_data["CustomerID"])
        customer_label = next(
            (label for label, value in self.customer_dict.items() if int(value) == customer_id),
            ""
        )
        
        self.customer_combo.setCurrentText(customer_label)
        self.site_name_field.setText(row_data.get("SiteName") or "")
        self.street_number_field.setText(row_data.get("StreetNumber") or "")
        self.street_name_field.setText(row_data.get("StreetName") or "")
        self.city_field.setText(row_data.get("City") or "")
        self.mode_label.setText(f"Mode: Editing site #{self.active_site_id}")
        self._update_address_preview()
        self._loading_site = False
    
    def _update_address_preview(self) -> None:
        """Update address preview label."""
        preview = " ".join(
            part
            for part in [
                self.street_number_field.text().strip(),
                self.street_name_field.text().strip(),
                self.city_field.text().strip(),
            ]
            if part
        )
        self.address_preview.setText(f"Address Preview: {preview or '(blank)'}")
    
    def _save_site_record(self) -> None:
        """Save site to database."""
        customer_label = self.customer_combo.currentText().strip()
        site_name = self.site_name_field.text().strip()
        street_number = self.street_number_field.text().strip()
        street_name = self.street_name_field.text().strip()
        city = self.city_field.text().strip()
        
        if not customer_label:
            QMessageBox.critical(self, "Validation Error", "Please select a customer.")
            return
        if not site_name:
            QMessageBox.critical(self, "Validation Error", "Site Name is required.")
            return
        
        customer_id = self.customer_dict.get(customer_label)
        if not customer_id:
            QMessageBox.critical(self, "Validation Error", "Please choose a valid customer from the list.")
            return
        
        try:
            saved_site_id = save_site(
                self.active_site_id,
                customer_id,
                site_name,
                street_number,
                street_name,
                city,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", f"Could not save site:\n{exc}")
            return
        
        QMessageBox.information(self, "Success", f"Site #{saved_site_id} saved successfully.")
        self.refresh_data()
        if str(saved_site_id) in self.site_lookup:
            for row in range(self.sites_table.rowCount()):
                if self.sites_table.item(row, 0).text() == str(saved_site_id):
                    self.sites_table.selectRow(row)
                    break
    
    def _delete_selected_site(self) -> None:
        """Delete selected site."""
        if not self.active_site_id:
            QMessageBox.warning(self, "No Selection", "Please select a site first.")
            return
        
        site_name = self.site_name_field.text().strip() or f"#{self.active_site_id}"
        confirmed = QMessageBox.question(
            self, "Delete Site",
            f"Delete site {site_name}?\n\nThis only works when the site is not linked to estimates or work orders."
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        
        try:
            delete_site(self.active_site_id)
        except Exception as exc:
            QMessageBox.critical(self, "Delete Blocked", str(exc))
            return
        
        QMessageBox.information(self, "Deleted", f"Site {site_name} was deleted.")
        self.refresh_data()
