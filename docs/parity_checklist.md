# Neon_ai Parity Checklist

| Legacy source | Neon_ai implementation | Exact parity status |
| --- | --- | --- |
| `app/main.py::ArgonApp` | `src/neon_ai/ui/main_window.py::NeonMainWindow` | `PARTIAL` - PySide6 shell exists, but the legacy menu order, category labels, button labels, and sidebar behavior do not yet match `ArgonApp` exactly. |
| `app/main.py::DashboardFrame` | `src/neon_ai/ui/pages/dashboard_page.py::DashboardPage` | `COMPLETE` - legacy data loading, two-pane layout, and numeric financial sorting behavior are now ported into the PySide6 dashboard. |
| `app/customer_form.py::CustomerFrame` | `src/neon_ai/ui/pages/customer_page.py::CustomerPage` | `COMPLETE` - the editor tab, customer portfolio tab, pipeline selection flow, address update action, and save/contact behavior now follow the legacy screen. |
| `app/site_form.py::SiteFrame` | `src/neon_ai/ui/pages/site_page.py::SitePage` | `COMPLETE` - page structure, fields, save/delete behavior, and record flow match the legacy screen closely enough for parity. |
| `app/estimate_form.py::EstimateEntryForm` | `src/neon_ai/ui/pages/estimate_entry_page.py::EstimateEntryPage` | `PLACEHOLDER` - page boundary only; no legacy widgets, data loading, or save workflow ported yet. |
| `app/estimate_viewer.py::EstimateViewerFrame` | `src/neon_ai/ui/pages/estimate_viewer_page.py::EstimateViewerPage` | `PLACEHOLDER` - page boundary only; no legacy review pipeline behavior ported yet. |
| `app/estimate_doc_view.py::EstimateDocView` | `src/neon_ai/ui/pages/estimate_doc_view_page.py::EstimateDocViewPage` | `PLACEHOLDER` - page boundary only; no legacy final document workflow ported yet. |
| `app/workorder_viewer.py::WorkOrderViewerFrame` | `src/neon_ai/ui/pages/workorder_viewer_page.py::WorkOrderViewerPage` | `PLACEHOLDER` - page boundary only; no legacy workorder viewer behavior ported yet. |
| `app/material_form.py::MaterialFrame` | `src/neon_ai/ui/pages/material_page.py::MaterialPage` | `PLACEHOLDER` - page boundary only; no legacy material catalog behavior ported yet. |
| `app/rfq_viewer.py::RFQViewerFrame` | `src/neon_ai/ui/pages/rfq_viewer_page.py::RFQViewerPage` | `PLACEHOLDER` - page boundary only; no legacy RFQ workflow ported yet. |
| `app/purchase_order_viewer.py::POViewerFrame` | `src/neon_ai/ui/pages/purchase_order_viewer_page.py::PurchaseOrderViewerPage` | `PLACEHOLDER` - page boundary only; no legacy PO viewer behavior ported yet. |
| `app/receiving_viewer.py::ReceivingViewerFrame` | `src/neon_ai/ui/pages/receiving_viewer_page.py::ReceivingViewerPage` | `PLACEHOLDER` - page boundary only; no legacy receiving workflow ported yet. |
| `app/employee_manager.py::EmployeeManagerFrame` | `src/neon_ai/ui/pages/employee_manager_page.py::EmployeeManagerPage` | `PLACEHOLDER` - page boundary only; no legacy employee management behavior ported yet. |
| `app/timesheet_manager.py::TimesheetManagerFrame` | `src/neon_ai/ui/pages/timesheet_manager_page.py::TimesheetManagerPage` | `PLACEHOLDER` - page boundary only; no legacy timesheet review behavior ported yet. |
| `app/invoice_creator.py::InvoiceCreatorFrame` | `src/neon_ai/ui/pages/invoice_creator_page.py::InvoiceCreatorPage` | `PLACEHOLDER` - page boundary only; no legacy customer invoice creation behavior ported yet. |
| `app/invoice_viewer.py::InvoiceViewerFrame` | `src/neon_ai/ui/pages/invoice_viewer_page.py::InvoiceViewerPage` | `PLACEHOLDER` - page boundary only; no legacy A/R tracking behavior ported yet. |
| `app/vendor_form.py::VendorFrame` | `src/neon_ai/ui/pages/vendor_page.py::VendorPage` | `PLACEHOLDER` - page boundary only; no legacy vendor management behavior ported yet. |
| `app/vendorinvoice_form.py::VendorInvoiceFrame` | `src/neon_ai/ui/pages/vendor_invoice_page.py::VendorInvoicePage` | `PLACEHOLDER` - page boundary only; no legacy vendor invoice behavior ported yet. |
| `app/time_form.py::TimeEntryFrame` | `src/neon_ai/ui/pages/time_entry_page.py::TimeEntryPage` | `PLACEHOLDER` - page boundary only; no legacy time entry workflow ported yet. |
| `app/price_request_form.py::PriceRequestForm` | `src/neon_ai/ui/pages/price_request_page.py::PriceRequestPage` | `PLACEHOLDER` - page boundary only; no legacy RFQ builder workflow ported yet. |
| `app/workorder_form.py::WorkOrderFrame` | `src/neon_ai/ui/pages/workorder_form_page.py::WorkOrderFormPage` | `PLACEHOLDER` - page boundary only; no legacy workorder form behavior ported yet. |
| `app/purchaseorder_form.py::PurchaseOrderFrame` | `src/neon_ai/ui/pages/purchase_order_form_page.py::PurchaseOrderFormPage` | `PLACEHOLDER` - page boundary only; no legacy purchase order form behavior ported yet. |
| `app/tiber.py::TiberTimerWindow` | `src/neon_ai/ui/dialogs/tiber_dialog.py::TiberDialog` | `PLACEHOLDER` - dialog shell only; no legacy timer workflow or database actions ported yet. |
| `app/private_chat_frame.py::PrivateChatWindow` | `src/neon_ai/ui/dialogs/private_brain_dialog.py::PrivateBrainDialog` | `PLACEHOLDER` - dialog shell only; no legacy private chat workflow ported yet. |
| `app/main.py::run_gateway_loop` | `src/neon_ai/main.py::run_gateway_loop` | `PARTIAL` - sweep cadence and calls are mostly preserved, but startup/log output still differs from the legacy loop. |
