from __future__ import annotations

from dataclasses import dataclass
import time

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from neon_ai.ui.dialogs.private_brain_dialog import PrivateBrainDialog
from neon_ai.ui.dialogs.tiber_dialog import launch_tiber
from neon_ai.ui.pages.customer_page import CustomerPage
from neon_ai.ui.pages.dashboard_page import DashboardPage
from neon_ai.ui.pages.employee_manager_page import EmployeeManagerPage
from neon_ai.ui.pages.estimate_doc_view_page import EstimateDocViewPage
from neon_ai.ui.pages.estimate_entry_page import EstimateEntryPage
from neon_ai.ui.pages.estimate_viewer_page import EstimateViewerPage
from neon_ai.ui.pages.invoice_creator_page import InvoiceCreatorPage
from neon_ai.ui.pages.invoice_viewer_page import InvoiceViewerPage
from neon_ai.ui.pages.material_page import MaterialPage
from neon_ai.ui.pages.price_request_page import PriceRequestPage
from neon_ai.ui.pages.purchase_order_form_page import PurchaseOrderFormPage
from neon_ai.ui.pages.purchase_order_viewer_page import PurchaseOrderViewerPage
from neon_ai.ui.pages.receiving_viewer_page import ReceivingViewerPage
from neon_ai.ui.pages.rfq_viewer_page import RFQViewerPage
from neon_ai.ui.pages.site_page import SitePage
from neon_ai.ui.pages.time_entry_page import TimeEntryPage
from neon_ai.ui.pages.timesheet_manager_page import TimesheetManagerPage
from neon_ai.ui.pages.vendor_invoice_page import VendorInvoicePage
from neon_ai.ui.pages.vendor_page import VendorPage
from neon_ai.ui.pages.workorder_form_page import WorkOrderFormPage
from neon_ai.ui.pages.workorder_viewer_page import WorkOrderViewerPage


@dataclass(frozen=True)
class MenuAction:
    label: str
    page_key: str


def _perf_log(area: str, name: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}")


def _perf_log_skipped(page_key: str) -> None:
    print(f"[PERF] area=ui name=refresh_skipped:{page_key} elapsed_ms=0 reason=throttle")


class NeonMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Argon Operations Command")
        self.resize(1400, 850)
        self.setMinimumSize(1000, 700)

        self.pages: dict[str, QWidget] = {}
        self.page_factories: dict[str, type[QWidget]] = {}
        self.page_last_refresh_at: dict[str, float] = {}
        self.page_refresh_interval_s = 30.0
        self.menu_structure: dict[str, list[MenuAction]] = {
            "📈 METRICS": [
                MenuAction("Company Dashboard", "DashboardFrame"),
                MenuAction("Review Estimates", "EstimateViewerFrame"),
                MenuAction("View POs", "POViewerFrame"),
            ],
            "👥 STAKEHOLDERS": [
                MenuAction("Add Customer", "CustomerFrame"),
                MenuAction("Add Site", "SiteFrame"),
                MenuAction("Add Vendor", "VendorFrame"),
            ],
            "📋 ESTIMATING": [
                MenuAction("New Estimate", "EstimateEntryForm"),
                MenuAction("Review Pipeline", "EstimateViewerFrame"),
                MenuAction("Final Doc View", "EstimateDocView"),
            ],
            "📋 PROJECT TRACKING": [
                MenuAction("Review Workorders", "WorkOrderViewerFrame"),
            ],
            "📦 MATERIALS": [
                MenuAction("Materials Catalog", "MaterialFrame"),
                MenuAction("Review RFQs", "RFQViewerFrame"),
                MenuAction("Receive Goods", "ReceivingViewerFrame"),
            ],
            "🛠️ EMPLOYEES": [
                MenuAction("Manage People", "EmployeeManagerFrame"),
                MenuAction("Review Timesheets", "TimesheetManagerFrame"),
            ],
            "🧾 INVOICING": [
                MenuAction("Create Invoice", "InvoiceCreatorFrame"),
                MenuAction("A/R & Tracking", "InvoiceViewerFrame"),
            ],
            "💸 PAYABLES": [
                MenuAction("Enter Supplier Bill", "VendorInvoiceFrame"),
            ],
        }

        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = self._build_sidebar()
        layout.addWidget(self.sidebar, 0)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self._register_pages()
        self.load_sub_menu("📈 METRICS")
        print(f"[startup] Cached pages after startup: {list(self.pages)}")

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(300)

        layout = QHBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.category_column = QFrame()
        self.category_column.setFixedWidth(120)
        cat_layout = QVBoxLayout(self.category_column)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(2)

        title = QLabel("ARGON")
        title.setObjectName("sidebarTitle")
        cat_layout.addWidget(title)

        tiber_button = QPushButton("⏱️ Tiber")
        tiber_button.setObjectName("toolButton")
        tiber_button.clicked.connect(self.open_tiber)
        cat_layout.addWidget(tiber_button)

        brain_button = QPushButton("🧠 Private Brain")
        brain_button.setObjectName("toolButton")
        brain_button.clicked.connect(self.open_private_brain)
        cat_layout.addWidget(brain_button)

        for category_name in self.menu_structure:
            button = QPushButton(category_name.split()[-1])
            button.clicked.connect(lambda checked=False, name=category_name: self.load_sub_menu(name))
            cat_layout.addWidget(button)

        cat_layout.addStretch(1)

        self.action_column = QFrame()
        self.action_column.setFixedWidth(180)
        self.action_column.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.action_layout = QVBoxLayout(self.action_column)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(2)

        layout.addWidget(self.category_column)
        layout.addWidget(self.action_column)
        sidebar.setStyleSheet(
            """
            QFrame#sidebar { background-color: #1e272e; }
            QLabel#sidebarTitle {
                color: white;
                font-size: 14px;
                font-weight: 700;
                padding: 10px;
                background-color: #1e272e;
            }
            QFrame { background-color: #1e272e; }
            QPushButton {
                text-align: left;
                padding: 8px;
                color: white;
                background-color: #2f3640;
                border: 1px solid #485460;
                border-radius: 0px;
                font-size: 10pt;
                margin-left: 5px;
                margin-right: 5px;
                margin-top: 2px;
                margin-bottom: 2px;
            }
            QPushButton#toolButton {
                font-weight: 700;
                color: #27ae60;
                margin-top: 8px;
            }
            QPushButton:hover { background-color: #3d4752; }
            """
        )
        return sidebar

    def _register_pages(self) -> None:
        self.page_factories = {
            "DashboardFrame": DashboardPage,
            "CustomerFrame": CustomerPage,
            "SiteFrame": SitePage,
            "EstimateEntryForm": EstimateEntryPage,
            "EstimateViewerFrame": EstimateViewerPage,
            "EstimateDocView": EstimateDocViewPage,
            "WorkOrderViewerFrame": WorkOrderViewerPage,
            "MaterialFrame": MaterialPage,
            "RFQViewerFrame": RFQViewerPage,
            "POViewerFrame": PurchaseOrderViewerPage,
            "ReceivingViewerFrame": ReceivingViewerPage,
            "EmployeeManagerFrame": EmployeeManagerPage,
            "TimesheetManagerFrame": TimesheetManagerPage,
            "InvoiceCreatorFrame": InvoiceCreatorPage,
            "InvoiceViewerFrame": InvoiceViewerPage,
            "VendorFrame": VendorPage,
            "VendorInvoiceFrame": VendorInvoicePage,
            "TimeEntryFrame": TimeEntryPage,
            "PriceRequestForm": PriceRequestPage,
            "WorkOrderFrame": WorkOrderFormPage,
            "PurchaseOrderFrame": PurchaseOrderFormPage,
        }

    def _get_or_create_page(self, page_key: str) -> QWidget:
        page = self.pages.get(page_key)
        if page is not None:
            return page

        page_class = self.page_factories[page_key]
        print(f"[lazy-load] Instantiating page: {page_key}")
        started_at = time.perf_counter()
        try:
            page = page_class(self)
            self.pages[page_key] = page
            self.stack.addWidget(page)
            return page
        finally:
            _perf_log("ui", f"create_page:{page_key}", started_at)

    def load_sub_menu(self, category_name: str) -> None:
        while self.action_layout.count():
            item = self.action_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        heading = QLabel(category_name)
        heading.setStyleSheet("color: white; font-size: 10pt; font-weight: 700; padding: 10px;")
        self.action_layout.addWidget(heading)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #485460; min-height: 1px; max-height: 1px; margin-left: 10px; margin-right: 10px;")
        self.action_layout.addWidget(separator)

        actions = self.menu_structure[category_name]
        for action in actions:
            button = QPushButton(action.label)
            button.clicked.connect(lambda checked=False, key=action.page_key: self.show_page(key))
            self.action_layout.addWidget(button)

        self.action_layout.addStretch(1)
        if actions:
            self.show_page(actions[0].page_key)

    def show_page(self, page_key: str) -> None:
        started_at = time.perf_counter()
        try:
            page = self._get_or_create_page(page_key)
            refresh = getattr(page, "refresh_data", None)
            if callable(refresh):
                now = time.perf_counter()
                last_refresh_at = self.page_last_refresh_at.get(page_key)
                if last_refresh_at is None or (now - last_refresh_at) >= self.page_refresh_interval_s:
                    refresh()
                    self.page_last_refresh_at[page_key] = time.perf_counter()
                else:
                    _perf_log_skipped(page_key)
            self.stack.setCurrentWidget(page)
        finally:
            _perf_log("ui", f"show_page:{page_key}", started_at)

    def open_tiber(self) -> None:
        dashboard = self._get_or_create_page("DashboardFrame")
        launch_tiber(self, on_time_logged_callback=dashboard.refresh_data)

    def open_private_brain(self) -> None:
        dialog = PrivateBrainDialog(self)
        dialog.exec()
