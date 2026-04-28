# Legacy UI Inventory

This inventory treats `D:\Argon_ai\app` as the specification for the PySide6 port.

## Main shell

Legacy source: `app/main.py`

- Main window class: `ArgonApp`
- Home/dashboard frame: `DashboardFrame`
- Global popup tools:
- `tiber.launch_tiber`
- `private_chat_frame.PrivateChatWindow`
- Background automation loop:
- `gateway.check_for_instructions`
- `database.automation.sweep_for_ready_leads`
- `database.automation.sweep_for_locked_estimates`
- `database.automation.sweep_for_sent_estimate_followups`
- `database.automation.sweep_for_completed_rfq_estimates`
- `database.automation.sweep_for_aging_unsent_estimates`
- `database.automation.sweep_for_deposit_invoice_reminders`
- `database.rfq.sweep_for_outstanding_rfq_followups`
- `database.purchases.sweep_for_locked_purchase_orders`
- `database.purchases.sweep_for_po_eta_followups`
- `database.purchases.sweep_for_backordered_purchase_orders`
- `database.vendor_invoices.sweep_for_ready_to_pay_vendor_invoices`

## Navigation map

| Legacy label | Legacy frame/dialog | Legacy source | Primary DB modules | Neon_ai target |
| --- | --- | --- | --- | --- |
| Company Dashboard | `DashboardFrame` | `app/main.py` | `database.metrics`, `database.estimates` | `src/neon_ai/ui/pages/dashboard_page.py` |
| Review Workorders | `WorkOrderViewerFrame` | `app/workorder_viewer.py` | `database.workorders`, `database.automation`, `database.folders` | `src/neon_ai/ui/pages/workorder_viewer_page.py` |
| Review Estimates | `EstimateViewerFrame` | `app/estimate_viewer.py` | `database.estimates` | `src/neon_ai/ui/pages/estimate_viewer_page.py` |
| View POs | `POViewerFrame` | `app/purchase_order_viewer.py` | `database.purchases` | `src/neon_ai/ui/pages/purchase_order_viewer_page.py` |
| Add Customer | `CustomerFrame` | `app/customer_form.py` | `database.customers`, `database.automation` | `src/neon_ai/ui/pages/customer_page.py` |
| Add Site | `SiteFrame` | `app/site_form.py` | `database.customers` | `src/neon_ai/ui/pages/site_page.py` |
| Add Vendor | `VendorFrame` | `app/vendor_form.py` | `database.vendors` | `src/neon_ai/ui/pages/vendor_page.py` |
| New Estimate | `EstimateEntryForm` | `app/estimate_form.py` | `database.customers`, `database.estimates`, `database.materials`, `database.roles` | `src/neon_ai/ui/pages/estimate_entry_page.py` |
| Review Pipeline | `EstimateViewerFrame` | `app/estimate_viewer.py` | `database.estimates` | `src/neon_ai/ui/pages/estimate_viewer_page.py` |
| Final Doc View | `EstimateDocView` | `app/estimate_doc_view.py` | `database.estimates`, `database.automation`, `database.rfq` | `src/neon_ai/ui/pages/estimate_doc_view_page.py` |
| Review RFQs | `RFQViewerFrame` | `app/rfq_viewer.py` | `database.rfq`, `database.estimates`, `database.materials`, `database.purchases`, `database.timesheets` | `src/neon_ai/ui/pages/rfq_viewer_page.py` |
| Receive Goods | `ReceivingViewerFrame` | `app/receiving_viewer.py` | `database.purchases` | `src/neon_ai/ui/pages/receiving_viewer_page.py` |
| Materials Catalog | `MaterialFrame` | `app/material_form.py` | `database.materials` | `src/neon_ai/ui/pages/material_page.py` |
| Manage People | `EmployeeManagerFrame` | `app/employee_manager.py` | `database.employees`, `database.roles`, `database.timesheets` | `src/neon_ai/ui/pages/employee_manager_page.py` |
| Review Timesheets | `TimesheetManagerFrame` | `app/timesheet_manager.py` | `database.employees`, `database.timesheets`, `gateway` | `src/neon_ai/ui/pages/timesheet_manager_page.py` |
| Create Invoice | `InvoiceCreatorFrame` | `app/invoice_creator.py` | `database.invoices`, `gateway` | `src/neon_ai/ui/pages/invoice_creator_page.py` |
| A/R & Tracking | `InvoiceViewerFrame` | `app/invoice_viewer.py` | `database.invoices`, `database.connection`, `gateway` | `src/neon_ai/ui/pages/invoice_viewer_page.py` |
| Enter Supplier Bill | `VendorInvoiceFrame` | `app/vendorinvoice_form.py` | `database.vendor_invoices` | `src/neon_ai/ui/pages/vendor_invoice_page.py` |
| Time Entry | `TimeEntryFrame` | `app/time_form.py` | `database.timer`, `database.timesheets` | `src/neon_ai/ui/pages/time_entry_page.py` |
| RFQ Builder | `PriceRequestForm` | `app/price_request_form.py` | `database.rfq`, `database.estimates` | `src/neon_ai/ui/pages/price_request_page.py` |
| Work Order Form | `WorkOrderFrame` | `app/workorder_form.py` | `database.workorders` | `src/neon_ai/ui/pages/workorder_form_page.py` |
| Purchase Order Form | `PurchaseOrderFrame` | `app/purchaseorder_form.py` | `database.purchases` | `src/neon_ai/ui/pages/purchase_order_form_page.py` |
| Tiber | `TiberTimerWindow` | `app/tiber.py` | `database.workorders`, `database.timer` | `src/neon_ai/ui/dialogs/tiber_dialog.py` |
| Private Brain | `PrivateChatWindow` | `app/private_chat_frame.py` | `automation.local_brain` | `src/neon_ai/ui/dialogs/private_brain_dialog.py` |

## Notes

- The legacy navigation only mounts a subset of the available frame modules at startup.
- `WorkOrderFrame`, `PurchaseOrderFrame`, and `PriceRequestForm` exist as legacy modules and remain in scope for parity even where the main menu currently favors viewer workflows.
- Automation and memory/log JSON behavior remain in scope because the legacy app actively uses those files and background jobs.
