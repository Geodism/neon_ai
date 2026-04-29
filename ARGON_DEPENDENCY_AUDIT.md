# ARGON DEPENDENCY AUDIT

Scope audited: `D:\Neon_ai\src`

This audit identifies runtime dependencies in Neon that currently resolve into `D:\Argon_ai\app`.

## Summary

- `ensure_legacy_import_paths()` call sites found: `24`
- Legacy-path bootstrapper found: `1` core implementation in [`bootstrap.py`](d:/Neon_ai/src/neon_ai/bootstrap.py)
- Runtime import statements resolving to legacy Argon modules found: `56`
- `services.*` runtime imports found: `0`
- Highest-risk dependency class:
  - `database.*` imports loaded by app startup and page modules
  - `gateway.py`
  - top-level document helpers `invoice_generator.py` and `wo_exporter.py`
  - legacy `.env` loading through `bootstrap.py`

## 1. Bootstrap / Path / Env Dependencies

| Neon file | Imported module/function | Current legacy source path | Purpose | Replacement target path inside Neon_ai | Priority | Risk if removed now |
| --- | --- | --- | --- | --- | --- | --- |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:18) | `legacy_app_root()` candidate `Path(r"D:\Argon_ai\app")` | `D:\Argon_ai\app` | Resolves the legacy app root when no explicit env override is provided. | `D:\Neon_ai\src\neon_ai\bootstrap.py` | HIGH | Legacy imports stop resolving on machines that still depend on the default Argon path. |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:41) | `sys.path.insert(0, legacy_root)` | `D:\Argon_ai\app` when `legacy_root` resolves to default Argon | Makes `database.*`, `gateway`, `automation.local_brain`, `invoice_generator`, and `wo_exporter` importable. | `D:\Neon_ai\src\neon_ai\bootstrap.py` | HIGH | Removing it now will break most runtime imports immediately. |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:38) | `load_dotenv(legacy_env_file, override=False)` | `D:\Argon_ai\app\.env` | Loads legacy DB/mail/runtime configuration before Neon overrides. | `D:\Neon_ai\.env` and `D:\Neon_ai\src\neon_ai\bootstrap.py` | HIGH | Real DB, email, gateway, and automation credentials may disappear at runtime. |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:39) | `load_dotenv(neon_env_file, override=True)` | Neon-local, not Argon | Overrides legacy values with Neon-local values where present. Included here because it is part of the same dual-env runtime contract. | `D:\Neon_ai\.env` | MEDIUM | If Argon `.env` is still carrying required values, losing the override path can silently change behavior. |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:51) | `DB_URL` / `DATABASE_URL` normalization | Legacy `.env` often supplies one side of this pair | Keeps legacy and Neon DB env names interchangeable. | `D:\Neon_ai\src\neon_ai\bootstrap.py` | HIGH | DB clients may fail if only one env var name is populated. |

## 2. `ensure_legacy_import_paths()` Call Sites

These files explicitly opt into the legacy Argon import/runtime contract.

| Neon file | Line |
| --- | --- |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:12) | `12` |
| [private_brain_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/private_brain_dialog.py:20) | `20` |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:24) | `24` |
| [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py:25) | `25` |
| [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py:18) | `18` |
| [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:25) | `25` |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:30) | `30` |
| [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py:25) | `25` |
| [estimate_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_viewer_page.py:25) | `25` |
| [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:25) | `25` |
| [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:26) | `26` |
| [material_page.py](d:/Neon_ai/src/neon_ai/ui/pages/material_page.py:25) | `25` |
| [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py:20) | `20` |
| [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:19) | `19` |
| [purchase_order_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_viewer_page.py:23) | `23` |
| [receiving_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/receiving_viewer_page.py:24) | `24` |
| [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py:34) | `34` |
| [site_page.py](d:/Neon_ai/src/neon_ai/ui/pages/site_page.py:23) | `23` |
| [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:31) | `31` |
| [time_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/time_entry_page.py:18) | `18` |
| [vendor_invoice_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_invoice_page.py:27) | `27` |
| [vendor_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_page.py:25) | `25` |
| [workorder_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_form_page.py:21) | `21` |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:26) | `26` |

## 3. Database Helper Imports Resolving to `D:\Argon_ai\app\database`

Recommended replacement convention for this entire section: `D:\Neon_ai\src\neon_ai\database\<module>.py`

| Neon file | Imported module/function | Current legacy source path | Purpose | Replacement target path inside Neon_ai | Priority | Risk if removed now |
| --- | --- | --- | --- | --- | --- | --- |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:16) | `database.automation`: `sweep_for_aging_unsent_estimates`, `sweep_for_completed_rfq_estimates`, `sweep_for_deposit_invoice_reminders`, `sweep_for_locked_estimates`, `sweep_for_ready_leads`, `sweep_for_sent_estimate_followups` | `D:\Argon_ai\app\database\automation.py` | Gateway sweep loop. | `D:\Neon_ai\src\neon_ai\database\automation.py` | HIGH | Background automation loop breaks. |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:24) | `database.purchases`: `sweep_for_backordered_purchase_orders`, `sweep_for_locked_purchase_orders`, `sweep_for_po_eta_followups` | `D:\Argon_ai\app\database\purchases.py` | Purchase-order follow-up sweeps. | `D:\Neon_ai\src\neon_ai\database\purchases.py` | HIGH | Background purchasing automation breaks. |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:29) | `database.rfq`: `sweep_for_outstanding_rfq_followups` | `D:\Argon_ai\app\database\rfq.py` | RFQ follow-up sweeps. | `D:\Neon_ai\src\neon_ai\database\rfq.py` | HIGH | RFQ automation loop breaks. |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:30) | `database.vendor_invoices`: `sweep_for_ready_to_pay_vendor_invoices` | `D:\Argon_ai\app\database\vendor_invoices.py` | Vendor-invoice sweep loop. | `D:\Neon_ai\src\neon_ai\database\vendor_invoices.py` | HIGH | Payables automation loop breaks. |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:26) | `database.timer`: `get_employees`, `get_tasks`, `get_todays_task_time`, `insert_time` | `D:\Argon_ai\app\database\timer.py` | Tiber staffing/task/time capture. | `D:\Neon_ai\src\neon_ai\database\timer.py` | HIGH | Tiber dialog stops loading and saving time. |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:27) | `database.workorders`: `get_open_workorders`, `get_open_workorders_dict` | `D:\Argon_ai\app\database\workorders.py` | Tiber work-order selection. | `D:\Neon_ai\src\neon_ai\database\workorders.py` | HIGH | Tiber WO routing breaks. |
| [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py:27) | `database.automation`: `send_customer_address_verification_request` | `D:\Argon_ai\app\database\automation.py` | Customer verification workflow. | `D:\Neon_ai\src\neon_ai\database\automation.py` | HIGH | Customer verification action fails. |
| [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py:28) | `database.customers`: `get_customer_by_id`, `get_customer_contacts`, `get_customer_pipeline`, `get_customer_portfolio`, `save_customer` | `D:\Argon_ai\app\database\customers.py` | Customer CRUD and portfolio view. | `D:\Neon_ai\src\neon_ai\database\customers.py` | HIGH | Customer page breaks at startup/use. |
| [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py:20) | `database.estimates`: `get_dashboard_estimates` | `D:\Argon_ai\app\database\estimates.py` | Dashboard estimate pipeline. | `D:\Neon_ai\src\neon_ai\database\estimates.py` | HIGH | Dashboard estimate grid breaks. |
| [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py:21) | `database.metrics`: `get_dashboard_metrics` | `D:\Argon_ai\app\database\metrics.py` | Dashboard metrics panel. | `D:\Neon_ai\src\neon_ai\database\metrics.py` | HIGH | Dashboard financials break. |
| [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:27) | `database.employees`: `get_all_employees`, `save_employee` | `D:\Argon_ai\app\database\employees.py` | Employee directory/profile CRUD. | `D:\Neon_ai\src\neon_ai\database\employees.py` | HIGH | Employee manager breaks. |
| [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:28) | `database.roles`: `add_standard_role`, `get_standard_roles` | `D:\Argon_ai\app\database\roles.py` | Estimating role management. | `D:\Neon_ai\src\neon_ai\database\roles.py` | HIGH | Role configuration breaks. |
| [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:29) | `database.timesheets`: `add_standard_task`, `delete_standard_task`, `get_standard_tasks` | `D:\Argon_ai\app\database\timesheets.py` | Standard task catalog management. | `D:\Neon_ai\src\neon_ai\database\timesheets.py` | HIGH | Employee/task management breaks. |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:32) | `database.automation`: `build_client_estimate_doc`, `get_client_estimate_doc_path` | `D:\Argon_ai\app\database\automation.py` | Client-copy estimate document generation and lookup. | `D:\Neon_ai\src\neon_ai\database\automation.py` | HIGH | Estimate document workflow breaks. |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:33) | `database.estimates`: `get_dashboard_estimates`, `get_detailed_estimate_data`, `recalculate_estimate_totals`, `update_estimate_status` | `D:\Argon_ai\app\database\estimates.py` | Estimate review, total recalculation, status updates. | `D:\Neon_ai\src\neon_ai\database\estimates.py` | HIGH | Estimate doc view breaks. |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:39) | `database.rfq`: `create_and_send_rfq_batch`, `get_estimate_rfq_status`, `get_vendor_choices` | `D:\Argon_ai\app\database\rfq.py` | RFQ batch generation from estimate doc view. | `D:\Neon_ai\src\neon_ai\database\rfq.py` | HIGH | RFQ creation from estimate doc view breaks. |
| [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py:27) | `database.customers`: `get_all_customers`, `get_sites_for_customer` | `D:\Argon_ai\app\database\customers.py` | Customer/site selection for estimates. | `D:\Neon_ai\src\neon_ai\database\customers.py` | HIGH | Estimate form breaks. |
| [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py:28) | `database.estimates`: `get_all_estimate_summaries`, `get_detailed_estimate_data`, `insert_full_estimate`, `sync_estimate_pricing_from_sources`, `update_draft_estimate` | `D:\Argon_ai\app\database\estimates.py` | Estimate draft load/save/update. | `D:\Neon_ai\src\neon_ai\database\estimates.py` | HIGH | Estimate persistence breaks. |
| [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py:35) | `database.materials`: `get_carried_price`, `search_materials` | `D:\Argon_ai\app\database\materials.py` | Estimate material search and carried pricing. | `D:\Neon_ai\src\neon_ai\database\materials.py` | HIGH | Material staging breaks. |
| [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py:36) | `database.roles`: `get_standard_roles` | `D:\Argon_ai\app\database\roles.py` | Labor role/rate hydration. | `D:\Neon_ai\src\neon_ai\database\roles.py` | HIGH | Labor role staging breaks. |
| [estimate_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_viewer_page.py:27) | `database.estimates`: `convert_estimate_to_workorder`, `get_dashboard_estimates`, `get_estimate_notes`, `get_full_estimate`, `insert_estimate_note` | `D:\Argon_ai\app\database\estimates.py` | Estimate review, notes, WO conversion. | `D:\Neon_ai\src\neon_ai\database\estimates.py` | HIGH | Estimate viewer breaks. |
| [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:27) | `database.invoices`: `get_invoice_detail`, `get_invoice_header_data`, `get_unbilled_labor`, `get_unbilled_materials`, `get_unbilled_workorders`, `lock_and_export_invoice`, `mark_invoice_sent`, `save_invoice_draft` | `D:\Argon_ai\app\database\invoices.py` | Invoice creation and export workflow. | `D:\Neon_ai\src\neon_ai\database\invoices.py` | HIGH | Invoice creation breaks. |
| [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:28) | `database.connection`: `get_connection` | `D:\Argon_ai\app\database\connection.py` | Direct DB connection for paid-state operations. | `D:\Neon_ai\src\neon_ai\database\connection.py` | HIGH | Invoice viewer paid-state flow breaks. |
| [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:29) | `database.invoices`: `get_accounts_receivable`, `get_invoice_detail`, `mark_invoice_sent` | `D:\Argon_ai\app\database\invoices.py` | A/R pipeline and invoice status workflow. | `D:\Neon_ai\src\neon_ai\database\invoices.py` | HIGH | Invoice viewer breaks. |
| [material_page.py](d:/Neon_ai/src/neon_ai/ui/pages/material_page.py:27) | `database.materials`: `get_carry_source_choices`, `get_material_by_id`, `get_material_pipeline`, `get_material_price_history`, `save_material` | `D:\Argon_ai\app\database\materials.py` | Material catalog CRUD and price history. | `D:\Neon_ai\src\neon_ai\database\materials.py` | HIGH | Material page breaks. |
| [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py:22) | `database.estimates`: `add_single_material`, `get_all_estimates`, `get_estimate_materials` | `D:\Argon_ai\app\database\estimates.py` | Estimate material staging for price request workflow. | `D:\Neon_ai\src\neon_ai\database\estimates.py` | HIGH | Price request page breaks. |
| [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py:23) | `database.rfq`: `get_active_rfqs`, `get_all_vendors`, `get_quoted_vendors_for_estimate`, `save_rfq_package` | `D:\Argon_ai\app\database\rfq.py` | Vendor quote staging around estimates. | `D:\Neon_ai\src\neon_ai\database\rfq.py` | HIGH | RFQ-related estimate staging breaks. |
| [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:21) | `database.purchases`: `get_next_po_id`, `get_vendors`, `insert_purchase_order` | `D:\Argon_ai\app\database\purchases.py` | Direct PO form creation. | `D:\Neon_ai\src\neon_ai\database\purchases.py` | HIGH | PO form breaks. |
| [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:22) | `database.timer`: `get_employees` | `D:\Argon_ai\app\database\timer.py` | Employee selector on PO form. | `D:\Neon_ai\src\neon_ai\database\timer.py` | HIGH | PO form staffing selection breaks. |
| [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:23) | `database.workorders`: `get_open_workorders_dict` | `D:\Argon_ai\app\database\workorders.py` | Open WO selector on PO form. | `D:\Neon_ai\src\neon_ai\database\workorders.py` | HIGH | PO form WO routing breaks. |
| [purchase_order_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_viewer_page.py:25) | `database.purchases`: `archive_purchase_order_docx`, `get_po_export_data`, `get_po_followup_status`, `get_po_items`, `get_purchase_orders`, `send_purchase_order_now` | `D:\Argon_ai\app\database\purchases.py` | PO review, archive, send, and pipeline view. | `D:\Neon_ai\src\neon_ai\database\purchases.py` | HIGH | PO viewer breaks. |
| [receiving_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/receiving_viewer_page.py:26) | `database.purchases`: `get_po_items_with_receiving`, `get_purchase_orders_for_pipeline`, `log_material_receipt_batch`, `search_po_items_by_part` | `D:\Argon_ai\app\database\purchases.py` | Receiving pipeline and receipt logging. | `D:\Neon_ai\src\neon_ai\database\purchases.py` | HIGH | Receiving page breaks. |
| [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py:36) | `database.estimates`: `get_all_estimates`, `get_estimate_materials` | `D:\Argon_ai\app\database\estimates.py` | RFQ source estimate loading. | `D:\Neon_ai\src\neon_ai\database\estimates.py` | HIGH | RFQ page breaks. |
| [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py:37) | `database.materials`: `get_material_price_for_vendor`, `get_or_create_material_from_estimate`, `record_purchase_order_price`, `search_materials` | `D:\Argon_ai\app\database\materials.py` | RFQ quotes and direct PO pricing persistence. | `D:\Neon_ai\src\neon_ai\database\materials.py` | HIGH | RFQ/direct-PO workflow breaks. |
| [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py:43) | `database.purchases`: `archive_purchase_order_docx`, `get_carried_items_for_po_builder`, `get_existing_po_details`, `get_po_export_data`, `get_po_followup_status`, `get_po_items`, `get_po_items_with_receiving`, `get_purchase_order_edit_items`, `get_purchase_orders_for_pipeline`, `get_vendor_email_for_po`, `get_vendors`, `lock_purchase_order_record`, `log_material_receipt_batch`, `save_new_purchase_order`, `save_purchase_order_draft`, `send_purchase_order_now` | `D:\Argon_ai\app\database\purchases.py` | RFQ-derived PO, direct PO, receiving, and PO document workflows. | `D:\Neon_ai\src\neon_ai\database\purchases.py` | HIGH | Major RFQ/PO/receiving workflows break. |
| [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py:61) | `database.rfq`: `build_rfq_preview_text`, `get_active_rfqs`, `get_all_vendors`, `get_quoted_vendors_for_estimate`, `get_rfq_header_data`, `get_rfq_items_for_matrix`, `get_wo_resolution_for_rfq`, `save_quote_and_carry_items`, `save_rfq_package`, `send_rfq_by_id`, `update_rfq_item_price` | `D:\Argon_ai\app\database\rfq.py` | RFQ pipeline, preview, send, bid matrix, and carry-to-estimate workflow. | `D:\Neon_ai\src\neon_ai\database\rfq.py` | HIGH | RFQ workflow breaks. |
| [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py:74) | `database.timesheets`: `get_open_workorder_choices` | `D:\Argon_ai\app\database\timesheets.py` | Direct PO WO choice list. | `D:\Neon_ai\src\neon_ai\database\timesheets.py` | HIGH | Direct PO workflow breaks. |
| [site_page.py](d:/Neon_ai/src/neon_ai/ui/pages/site_page.py:25) | `database.customers`: `get_customers`, `get_sites`, `save_site`, `delete_site` | `D:\Argon_ai\app\database\customers.py` | Site CRUD. | `D:\Neon_ai\src\neon_ai\database\customers.py` | HIGH | Site page breaks. |
| [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:33) | `database.employees`: `get_all_employees` | `D:\Argon_ai\app\database\employees.py` | Employee list for timesheets. | `D:\Neon_ai\src\neon_ai\database\employees.py` | HIGH | Timesheet page breaks. |
| [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:34) | `database.timesheets`: `add_manual_time`, `get_employee_timesheet`, `get_open_workorder_choices`, `get_standard_tasks`, `lock_week_timesheet` | `D:\Argon_ai\app\database\timesheets.py` | Timesheet review, manual entry, and locking. | `D:\Neon_ai\src\neon_ai\database\timesheets.py` | HIGH | Timesheet workflow breaks. |
| [time_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/time_entry_page.py:20) | `database.timer`: `get_employees`, `get_tasks`, `insert_time` | `D:\Argon_ai\app\database\timer.py` | Manual time entry form. | `D:\Neon_ai\src\neon_ai\database\timer.py` | HIGH | Time entry breaks. |
| [time_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/time_entry_page.py:21) | `database.timesheets`: `get_open_workorder_choices` | `D:\Argon_ai\app\database\timesheets.py` | WO choice list on time form. | `D:\Neon_ai\src\neon_ai\database\timesheets.py` | HIGH | Time entry WO selection breaks. |
| [vendor_invoice_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_invoice_page.py:29) | `database.vendor_invoices`: `apply_vendor_invoice_adjustment`, `create_vendor_invoice_draft`, `generate_vendor_invoice_report`, `get_received_purchase_order_choices`, `get_vendor_invoice_creation_context`, `get_vendor_invoice_pipeline`, `get_vendor_invoice_workspace_by_invoice_id`, `get_vendor_payables_summary`, `lock_vendor_invoice_ready_to_pay`, `mark_vendor_invoice_paid`, `save_vendor_invoice_draft` | `D:\Argon_ai\app\database\vendor_invoices.py` | Vendor invoice workspace, reconciliation, reporting, locking, and payment. | `D:\Neon_ai\src\neon_ai\database\vendor_invoices.py` | HIGH | Vendor invoice page breaks. |
| [vendor_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_page.py:27) | `database.vendors`: `get_vendor_by_id`, `get_vendor_contacts`, `get_vendor_overview`, `get_vendor_pipeline`, `save_vendor` | `D:\Argon_ai\app\database\vendors.py` | Vendor CRUD and overview. | `D:\Neon_ai\src\neon_ai\database\vendors.py` | HIGH | Vendor page breaks. |
| [workorder_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_form_page.py:23) | `database.customers`: `get_customers`, `get_sites` | `D:\Argon_ai\app\database\customers.py` | Customer/site selection for work orders. | `D:\Neon_ai\src\neon_ai\database\customers.py` | HIGH | Workorder form breaks. |
| [workorder_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_form_page.py:24) | `database.workorders`: `approve_work_order`, `get_next_workorder_id`, `insert_workorder` | `D:\Argon_ai\app\database\workorders.py` | Workorder create/approve flow. | `D:\Neon_ai\src\neon_ai\database\workorders.py` | HIGH | Workorder form breaks. |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:28) | `database.automation`: `add_note_to_active_job` | `D:\Argon_ai\app\database\automation.py` | Add note to active WO. | `D:\Neon_ai\src\neon_ai\database\automation.py` | HIGH | WO notes workflow breaks. |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:29) | `database.folders`: `get_target_folder` | `D:\Argon_ai\app\database\folders.py` | Resolve target folder for WO exports. | `D:\Neon_ai\src\neon_ai\database\folders.py` | HIGH | WO export path resolution breaks. |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:30) | `database.workorders`: `check_closure_requirements`, `close_work_order`, `get_dashboard_work_orders`, `get_work_order_telemetry`, `get_wo_export_data` | `D:\Argon_ai\app\database\workorders.py` | WO pipeline, telemetry, export, and closure. | `D:\Neon_ai\src\neon_ai\database\workorders.py` | HIGH | WO viewer breaks. |

## 4. Automation / Gateway Imports Resolving to `D:\Argon_ai\app`

Recommended replacement targets:

- `gateway.*` -> `D:\Neon_ai\src\neon_ai\gateway.py`
- `automation.local_brain` -> `D:\Neon_ai\src\neon_ai\automation\local_brain.py`

| Neon file | Imported module/function | Current legacy source path | Purpose | Replacement target path inside Neon_ai | Priority | Risk if removed now |
| --- | --- | --- | --- | --- | --- | --- |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:15) | `gateway`: `check_for_instructions` | `D:\Argon_ai\app\gateway.py` | Inbox polling in the gateway loop. | `D:\Neon_ai\src\neon_ai\gateway.py` | HIGH | Gateway loop breaks immediately. |
| [private_brain_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/private_brain_dialog.py:22) | `automation.local_brain`: `brain_instance` | `D:\Argon_ai\app\automation\local_brain.py` | Private Brain dialog chat engine. | `D:\Neon_ai\src\neon_ai\automation\local_brain.py` | HIGH | Private Brain dialog fails to load. |
| [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:473) | `gateway`: `send_to_user` | `D:\Argon_ai\app\gateway.py` | Sends completed invoice to customer/user. | `D:\Neon_ai\src\neon_ai\gateway.py` | MEDIUM | Invoice send action breaks, but app still boots. |
| [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:262) | `gateway`: `send_to_user` | `D:\Argon_ai\app\gateway.py` | Resend invoice action. | `D:\Neon_ai\src\neon_ai\gateway.py` | MEDIUM | Invoice resend action breaks, but app still boots. |
| [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:541) | `gateway`: `MY_EMAIL`, `send_to_user` | `D:\Argon_ai\app\gateway.py` | Timesheet send-to-employee/folder email flow. | `D:\Neon_ai\src\neon_ai\gateway.py` | MEDIUM | Timesheet email/send action breaks, but app still boots. |

## 5. Document / Report Helper Imports Resolving to `D:\Argon_ai\app`

Note:

- Database-backed document/report functions such as `build_client_estimate_doc`, `generate_vendor_invoice_report`, `archive_purchase_order_docx`, and `send_purchase_order_now` are already listed in the database table above because they currently arrive through `database.*`.
- This section covers top-level non-database helper modules that still resolve directly into the legacy app root.

| Neon file | Imported module/function | Current legacy source path | Purpose | Replacement target path inside Neon_ai | Priority | Risk if removed now |
| --- | --- | --- | --- | --- | --- | --- |
| [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:37) | `invoice_generator`: `generate_invoice_docx` | `D:\Argon_ai\app\invoice_generator.py` | Invoice DOCX generation before review/send. | `D:\Neon_ai\src\neon_ai\invoice_generator.py` | HIGH | Invoice creator import or export flow breaks. |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:37) | `wo_exporter`: `generate_workorder_docx` | `D:\Argon_ai\app\wo_exporter.py` | Field work-order DOCX export. | `D:\Neon_ai\src\neon_ai\wo_exporter.py` | HIGH | WO export flow breaks. |

## 6. `services.*` Imports

No runtime `services.*` imports resolving to `D:\Argon_ai\app` were found under `D:\Neon_ai\src`.

## 7. Immediate Risk Readout

Highest-risk removals right now:

1. `bootstrap.py` legacy path / `.env` behavior
   - This is the bridge that makes nearly every Argon dependency importable.
2. `gateway.py`
   - Used by `main.py` and several send/resend flows.
3. `database.*` modules
   - These back nearly every loaded page and several startup-time imports.
4. `invoice_generator.py` and `wo_exporter.py`
   - These are still direct top-level legacy imports, not database-mediated helpers.
5. `automation.local_brain`
   - Breaks the Private Brain dialog at import time.

Safest migration order inside Neon:

1. Create Neon-local replacements for `gateway.py`, `invoice_generator.py`, `wo_exporter.py`, and `automation/local_brain.py`.
2. Internalize `database.connection.py`, `database.automation.py`, `database.estimates.py`, `database.purchases.py`, `database.rfq.py`, and `database.vendor_invoices.py`.
3. Internalize the remaining `database.*` modules used by CRUD/admin pages.
4. Remove the `D:\Argon_ai\app` fallback from `bootstrap.py`.
5. Remove legacy `.env` loading after all required secrets/config are carried by Neon-local config.

## 8. Phase 3A Completed

Phase 3A internalized the database slice needed by the completed parity pages:

- [vendor_invoice_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_invoice_page.py)
- [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py)
- [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py)

### Database modules internalized into `neon_ai.database`

Primary modules copied/ported into `D:\Neon_ai\src\neon_ai\database`:

- [vendor_invoices.py](d:/Neon_ai/src/neon_ai/database/vendor_invoices.py)
- [estimates.py](d:/Neon_ai/src/neon_ai/database/estimates.py)
- [rfq.py](d:/Neon_ai/src/neon_ai/database/rfq.py)
- [purchases.py](d:/Neon_ai/src/neon_ai/database/purchases.py)
- [materials.py](d:/Neon_ai/src/neon_ai/database/materials.py)
- [customers.py](d:/Neon_ai/src/neon_ai/database/customers.py)
- [roles.py](d:/Neon_ai/src/neon_ai/database/roles.py)

Support modules internalized during the same pass so the changed files no longer rely on bare `database.*` imports:

- [timesheets.py](d:/Neon_ai/src/neon_ai/database/timesheets.py)
- [folders.py](d:/Neon_ai/src/neon_ai/database/folders.py)
- [automation.py](d:/Neon_ai/src/neon_ai/database/automation.py)
- [vendors.py](d:/Neon_ai/src/neon_ai/database/vendors.py)

All of the above were rewired to use `from neon_ai.database.connection import get_connection` instead of `from database.connection import get_connection`.

### Pages updated to use `neon_ai.database` imports

The following completed parity pages were updated from bare `database.*` imports to `neon_ai.database.*` imports:

- [vendor_invoice_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_invoice_page.py)
- [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py)
- [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py)

### Remaining non-database dependencies discovered

Phase 3A did not complete total Argon separation. The updated procurement/estimate slice still reaches legacy non-database helpers through transitive imports:

- `gateway`
  - still used for send/email flows inside internalized modules such as [rfq.py](d:/Neon_ai/src/neon_ai/database/rfq.py), [purchases.py](d:/Neon_ai/src/neon_ai/database/purchases.py), [vendor_invoices.py](d:/Neon_ai/src/neon_ai/database/vendor_invoices.py), and [automation.py](d:/Neon_ai/src/neon_ai/database/automation.py)
- `purchase_order_generator`
  - still used by [purchases.py](d:/Neon_ai/src/neon_ai/database/purchases.py) for PO document generation/export helpers
- `doc_generator`
  - still used by [automation.py](d:/Neon_ai/src/neon_ai/database/automation.py) for estimate/customer document workflows

These are now the main non-database blockers for this internalized workflow slice.

### Remaining `ensure_legacy_import_paths()` usage

Phase 3A did not remove the legacy path bridge.

- `ensure_legacy_import_paths()` is still present across the Neon shell, dialogs, and page modules listed in Section 2.
- It is still required for unresolved legacy imports outside the Phase 3A database slice, especially:
  - `gateway.py`
  - `automation.local_brain`
  - document/report helper modules still imported from the Argon app root

This means the application is not yet fully separated from `D:\Argon_ai\app`.

### Next recommended phase

Recommended next step: Phase 3B should internalize the remaining non-database helper chain used by the completed procurement/estimate pages.

Suggested order:

1. Create Neon-local replacements for `gateway.py`, `purchase_order_generator.py`, and `doc_generator.py`.
2. Repoint the already-internalized procurement/estimate database modules to those Neon-local helpers.
3. Re-audit `ensure_legacy_import_paths()` usage after those helper migrations.
4. Only then begin internalizing the next group of page-specific `database.*` modules for still-legacy CRUD/admin pages.

Phase 3A reduced direct `database.*` dependency for the completed procurement/estimate pages, but total separation is not complete yet.

## 9. Phase 4A Completed

Phase 4A internalized the non-database helper chain that was still hanging off the Phase 3A procurement and estimate slice.

### Helper modules internalized into Neon

The following legacy app-root helpers were internalized into Neon-owned modules:

- `D:\Argon_ai\app\gateway.py` -> [neon_ai.gateway](d:/Neon_ai/src/neon_ai/gateway.py)
- `D:\Argon_ai\app\purchase_order_generator.py` -> [neon_ai.purchase_order_generator](d:/Neon_ai/src/neon_ai/purchase_order_generator.py)
- `D:\Argon_ai\app\doc_generator.py` -> [neon_ai.doc_generator](d:/Neon_ai/src/neon_ai/doc_generator.py)

### Phase 3A modules updated to use Neon-owned helpers

The Phase 3A database slice was updated to import these Neon-local helpers instead of resolving them from `D:\Argon_ai\app`:

- [rfq.py](d:/Neon_ai/src/neon_ai/database/rfq.py)
- [purchases.py](d:/Neon_ai/src/neon_ai/database/purchases.py)
- [vendor_invoices.py](d:/Neon_ai/src/neon_ai/database/vendor_invoices.py)
- [automation.py](d:/Neon_ai/src/neon_ai/database/automation.py)

This means the previously internalized procurement and estimate workflow no longer depends on legacy `gateway.py`, `purchase_order_generator.py`, or `doc_generator.py` through those database modules.

### Verification boundary

- No live email send was performed in this audit.
- Source-level helper replacement was the completed scope for Phase 4A.
- Total separation from `D:\Argon_ai\app` is still not complete.

### Remaining dependencies discovered

Phase 4A removed the main non-database helper chain for the completed procurement and estimate slice, but the following legacy dependencies remain:

- `automation.local_brain`
  - still needs to be internalized from `D:\Argon_ai\app\automation\local_brain.py`
  - still blocks full removal of the legacy path bridge for the Private Brain dialog
- `database.classification_feedback`
  - still needs to be internalized from `D:\Argon_ai\app\database\classification_feedback.py`
  - still represents a remaining direct legacy database dependency
- remaining `ensure_legacy_import_paths()` call sites
  - the bridge remains active across the shell, dialogs, and page modules listed in Section 2
  - this means Neon still retains a runtime fallback into `D:\Argon_ai\app`
- remaining page modules still importing bare `database.*`
  - Phase 4A did not finish the rest of the page-by-page database migration outside the already-internalized slice
  - those pages still depend on legacy-path resolution until their imports are moved to `neon_ai.database.*`

### Recommended next phase

Recommended next step: Phase 4B should internalize the next two remaining legacy modules that still block clean separation work.

Phase 4B targets:

1. `D:\Argon_ai\app\automation\local_brain.py` -> `D:\Neon_ai\src\neon_ai\automation\local_brain.py`
2. `D:\Argon_ai\app\database\classification_feedback.py` -> `D:\Neon_ai\src\neon_ai\database\classification_feedback.py`

After that, re-audit:

1. all remaining `ensure_legacy_import_paths()` call sites
2. all remaining bare `database.*` imports in page modules
3. whether `bootstrap.py` still requires the `D:\Argon_ai\app` fallback for active runtime code paths

Phase 4A materially reduced the helper-level Argon dependency surface, but it did not complete total separation.

## 10. Phase 4C Completed

Phase 4C continued the dependency internalization work by removing another small but important legacy bridge in the automation and read-model chain.

### Modules internalized in this phase

The following Neon-owned modules are now part of the internalized dependency surface:

- [neon_ai.database.read_model](d:/Neon_ai/src/neon_ai/database/read_model.py)
- [neon_ai.database.workorders](d:/Neon_ai/src/neon_ai/database/workorders.py)

`neon_ai.database.workorders` was internalized as a required dependency for the read-model and automation flow touched in this phase.

### Dependency rewiring completed

Phase 4C established the following source-level changes in the dependency graph:

- [neon_ai.automation.local_brain](d:/Neon_ai/src/neon_ai/automation/local_brain.py) now imports `neon_ai.database.read_model`
- direct legacy path insertion was removed from:
  - [local_brain.py](d:/Neon_ai/src/neon_ai/automation/local_brain.py)
  - [gateway.py](d:/Neon_ai/src/neon_ai/gateway.py)

### Legacy path mechanics removed from the changed files

For the files changed in this phase, the direct legacy path bridge is no longer part of the source:

- no `legacy_app_root()` usage
- no `sys.path.insert(...)`
- no `sys.path.append(...)`
- no hardcoded `D:\Argon_ai` path reference

This is a meaningful source-level reduction in Argon coupling for the local-brain and gateway-adjacent path.

### Separation status

Total separation is still not complete.

Phase 4C did not remove the broader legacy import bridge from the application because:

- `ensure_legacy_import_paths()` still remains elsewhere in the codebase
- some page modules still import bare `database.*`

That means Neon still retains remaining runtime paths that can resolve into legacy Argon code.

### Recommended next phase

Recommended next step: re-audit the current remaining Argon dependency surface after Phases 3A through 4C.

That re-audit should specifically verify:

1. which `ensure_legacy_import_paths()` call sites are still necessary
2. which files still import bare `database.*`
3. whether any remaining imports still resolve into `D:\Argon_ai\app`
4. whether additional helper or database modules still need Neon-local replacements

Phase 4C reduced direct legacy path usage in the updated helper chain, but it did not complete total separation.

## 11. Current Remaining Argon Dependencies After Phase 4C

Fresh source scan performed across `D:\Neon_ai\src` for:

- `ensure_legacy_import_paths()`
- `legacy_app_root()`
- `sys.path.insert`
- `sys.path.append`
- `D:\Argon_ai`
- bare `database.*` imports
- bare `automation.*` imports
- bare `gateway` imports
- bare `invoice_generator` imports
- bare `wo_exporter` imports
- bare `purchase_order_generator` imports
- `load_dotenv(...)`

### Current scan summary

- remaining `ensure_legacy_import_paths()` call sites: `24`
- remaining `legacy_app_root()` references: `3`
- remaining `sys.path.insert(...)` references: `1`
- remaining `sys.path.append(...)` references: `0`
- remaining hardcoded `D:\Argon_ai` references: `1`
- remaining bare `database.*` import sites: `40`
- remaining bare `automation.*` import sites: `0`
- remaining bare `gateway` import sites: `4`
- remaining bare `invoice_generator` import sites: `1`
- remaining bare `wo_exporter` import sites: `1`
- remaining bare `purchase_order_generator` import sites: `0`
- remaining `load_dotenv(...)` calls: `3`
  - direct legacy `.env` load still present: `1`
  - Neon-local / non-Argon `load_dotenv(...)` calls: `2`

### 11.1 Active bootstrap / path / env dependencies

| File | Line | Import / function | Why it remains | Neon replacement target | Priority |
| --- | --- | --- | --- | --- | --- |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:18) | `18` | `legacy_app_root()` | Central helper still resolves the legacy app root for the import bridge. | [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py) with Neon-only path logic after all callers are migrated | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:26) | `26` | `Path(r"D:\Argon_ai\app")` | Hardcoded fallback path to the legacy app is still present. | [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py) with fallback removed | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:37) | `37` | `legacy_root = legacy_app_root()` | The bootstrap bridge still activates the legacy root at runtime. | [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py) after legacy fallback removal | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:44) | `44` | `sys.path.insert(0, path_str)` | This is the active import bridge that makes bare legacy modules importable. | [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py) with bridge removed after import rewiring | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:47) | `47` | `load_dotenv(legacy_env_file, override=False)` | Legacy `.env` loading still participates in runtime config. | `D:\Neon_ai\.env` plus Neon-only bootstrap config | HIGH |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:41) | `41` | `RFQ_EMAIL_MEMORY_PATH = legacy_app_root() / "rfq_vendor_email_memory.json"` | This page still writes/reads a support JSON file from the legacy app root. | `D:\Neon_ai\src\neon_ai\resources\rfq_vendor_email_memory.json` or another Neon-local app data path | MEDIUM |

Notes:

- [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:49) still calls `load_dotenv(neon_env_file, override=True)`, but that is part of the Neon-local override path, not a remaining Argon dependency by itself.
- [config.py](d:/Neon_ai/src/neon_ai/config.py:41) still calls `load_dotenv(env_file, override=True)`, but that scan hit is Neon-local and not an Argon dependency by itself.

### 11.2 Remaining bare imports that still resolve through the legacy bridge

Recommended replacement convention:

- bare `database.<module>` -> `neon_ai.database.<module>`
- bare `gateway` -> `neon_ai.gateway`
- bare `invoice_generator` -> `neon_ai.invoice_generator`
- bare `wo_exporter` -> `neon_ai.wo_exporter`

| File | Line | Import / function | Why it remains | Neon replacement target | Priority |
| --- | --- | --- | --- | --- | --- |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:15) | `15` | `from gateway import check_for_instructions` | Startup gateway loop still imports the legacy-resolved gateway module by bare name. | `neon_ai.gateway` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:16) | `16` | `from database.automation import ...` | Startup sweep loop still uses a bare database import block. | `neon_ai.database.automation` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:24) | `24` | `from database.purchases import ...` | Startup sweep loop still uses a bare database import block. | `neon_ai.database.purchases` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:29) | `29` | `from database.rfq import sweep_for_outstanding_rfq_followups` | Startup sweep loop still resolves RFQ automation through the bridge. | `neon_ai.database.rfq` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:30) | `30` | `from database.vendor_invoices import sweep_for_ready_to_pay_vendor_invoices` | Startup sweep loop still resolves vendor-invoice automation through the bridge. | `neon_ai.database.vendor_invoices` | HIGH |
| [database/estimates.py](d:/Neon_ai/src/neon_ai/database/estimates.py:779) | `779` | `from database.estimates import get_estimate_materials` | Internalized module still contains one remaining bare self-import. | `neon_ai.database.estimates` | MEDIUM |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:26) | `26` | `from database.timer import ...` | Tiber still uses bare timer DB helpers. | `neon_ai.database.timer` | HIGH |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:27) | `27` | `from database.workorders import ...` | Tiber still uses bare workorder DB helpers. | `neon_ai.database.workorders` | HIGH |
| [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py:27) | `27` | `from database.automation import send_customer_address_verification_request` | Customer page still uses a bare automation DB import. | `neon_ai.database.automation` | HIGH |
| [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py:28) | `28` | `from database.customers import ...` | Customer page still uses bare customer DB imports. | `neon_ai.database.customers` | HIGH |
| [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py:20) | `20` | `from database.estimates import get_dashboard_estimates` | Dashboard still resolves estimates through the bridge. | `neon_ai.database.estimates` | HIGH |
| [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py:21) | `21` | `from database.metrics import get_dashboard_metrics` | Dashboard still resolves metrics through the bridge. | `neon_ai.database.metrics` | HIGH |
| [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:27) | `27` | `from database.employees import ...` | Employee manager still uses bare employee DB helpers. | `neon_ai.database.employees` | HIGH |
| [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:28) | `28` | `from database.roles import ...` | Employee manager still uses bare role DB helpers. | `neon_ai.database.roles` | HIGH |
| [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:29) | `29` | `from database.timesheets import ...` | Employee manager still uses bare timesheet DB helpers. | `neon_ai.database.timesheets` | HIGH |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:32) | `32` | `from database.automation import ...` | Estimate document workflow still uses bare automation DB helpers. | `neon_ai.database.automation` | HIGH |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:33) | `33` | `from database.estimates import ...` | Estimate document workflow still uses bare estimate DB helpers. | `neon_ai.database.estimates` | HIGH |
| [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:39) | `39` | `from database.rfq import ...` | Estimate document workflow still uses bare RFQ DB helpers. | `neon_ai.database.rfq` | HIGH |
| [estimate_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_viewer_page.py:27) | `27` | `from database.estimates import ...` | Estimate viewer still uses bare estimate DB helpers. | `neon_ai.database.estimates` | HIGH |
| [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:27) | `27` | `from database.invoices import ...` | Invoice creator still uses bare invoice DB helpers. | `neon_ai.database.invoices` | HIGH |
| [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:37) | `37` | `from invoice_generator import generate_invoice_docx` | Invoice DOCX generation still resolves through a bare top-level helper import. | `neon_ai.invoice_generator` | HIGH |
| [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:473) | `473` | `from gateway import send_to_user` | Invoice send flow still uses a bare gateway import inside the action path. | `neon_ai.gateway` | MEDIUM |
| [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:28) | `28` | `from database.connection import get_connection` | Invoice viewer still uses a bare connection import. | `neon_ai.database.connection` | HIGH |
| [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:29) | `29` | `from database.invoices import ...` | Invoice viewer still uses bare invoice DB helpers. | `neon_ai.database.invoices` | HIGH |
| [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:262) | `262` | `from gateway import send_to_user` | Invoice resend flow still uses a bare gateway import. | `neon_ai.gateway` | MEDIUM |
| [material_page.py](d:/Neon_ai/src/neon_ai/ui/pages/material_page.py:27) | `27` | `from database.materials import ...` | Material page still uses bare material DB helpers. | `neon_ai.database.materials` | HIGH |
| [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py:22) | `22` | `from database.estimates import ...` | Price request still uses bare estimate DB helpers. | `neon_ai.database.estimates` | HIGH |
| [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py:23) | `23` | `from database.rfq import ...` | Price request still uses bare RFQ DB helpers. | `neon_ai.database.rfq` | HIGH |
| [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:21) | `21` | `from database.purchases import ...` | PO form still uses bare purchase DB helpers. | `neon_ai.database.purchases` | HIGH |
| [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:22) | `22` | `from database.timer import get_employees` | PO form still uses a bare timer import. | `neon_ai.database.timer` | HIGH |
| [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:23) | `23` | `from database.workorders import get_open_workorders_dict` | PO form still uses a bare workorder import. | `neon_ai.database.workorders` | HIGH |
| [purchase_order_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_viewer_page.py:25) | `25` | `from database.purchases import ...` | PO viewer still uses bare purchase DB helpers. | `neon_ai.database.purchases` | HIGH |
| [receiving_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/receiving_viewer_page.py:26) | `26` | `from database.purchases import ...` | Receiving viewer still uses bare purchase DB helpers. | `neon_ai.database.purchases` | HIGH |
| [site_page.py](d:/Neon_ai/src/neon_ai/ui/pages/site_page.py:25) | `25` | `from database.customers import ...` | Site page still uses bare customer/site DB helpers. | `neon_ai.database.customers` | HIGH |
| [time_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/time_entry_page.py:20) | `20` | `from database.timer import ...` | Time entry still uses bare timer DB helpers. | `neon_ai.database.timer` | HIGH |
| [time_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/time_entry_page.py:21) | `21` | `from database.timesheets import get_open_workorder_choices` | Time entry still uses a bare timesheet import. | `neon_ai.database.timesheets` | HIGH |
| [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:33) | `33` | `from database.employees import get_all_employees` | Timesheet manager still uses a bare employee import. | `neon_ai.database.employees` | HIGH |
| [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:34) | `34` | `from database.timesheets import ...` | Timesheet manager still uses bare timesheet DB helpers. | `neon_ai.database.timesheets` | HIGH |
| [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:541) | `541` | `from gateway import MY_EMAIL, send_to_user` | Timesheet send flow still uses a bare gateway import inside the action path. | `neon_ai.gateway` | MEDIUM |
| [vendor_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_page.py:27) | `27` | `from database.vendors import ...` | Vendor page still uses bare vendor DB helpers. | `neon_ai.database.vendors` | HIGH |
| [workorder_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_form_page.py:23) | `23` | `from database.customers import get_customers, get_sites` | Workorder form still uses bare customer/site DB helpers. | `neon_ai.database.customers` | HIGH |
| [workorder_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_form_page.py:24) | `24` | `from database.workorders import ...` | Workorder form still uses bare workorder DB helpers. | `neon_ai.database.workorders` | HIGH |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:28) | `28` | `from database.automation import add_note_to_active_job` | Workorder viewer still uses a bare automation import. | `neon_ai.database.automation` | HIGH |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:29) | `29` | `from database.folders import get_target_folder` | Workorder viewer still uses a bare folders import. | `neon_ai.database.folders` | HIGH |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:30) | `30` | `from database.workorders import ...` | Workorder viewer still uses bare workorder DB helpers. | `neon_ai.database.workorders` | HIGH |
| [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:37) | `37` | `from wo_exporter import generate_workorder_docx` | WO document export still resolves through a bare top-level helper import. | `neon_ai.wo_exporter` | HIGH |

Confirmed absences in the current scan:

- no remaining bare `automation.*` imports were found
- no remaining bare `purchase_order_generator` imports were found

### 11.3 Remaining `ensure_legacy_import_paths()` call sites

#### Higher-priority call sites still paired with active bare imports or direct legacy path usage

These callers still have active same-file bare imports and/or direct legacy path usage, so the bridge is still doing real work:

- [main.py](d:/Neon_ai/src/neon_ai/main.py:12)
- [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:24)
- [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py:25)
- [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py:18)
- [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py:25)
- [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py:30)
- [estimate_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_viewer_page.py:25)
- [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py:25)
- [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py:26)
- [material_page.py](d:/Neon_ai/src/neon_ai/ui/pages/material_page.py:25)
- [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py:20)
- [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py:19)
- [purchase_order_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_viewer_page.py:23)
- [receiving_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/receiving_viewer_page.py:24)
- [site_page.py](d:/Neon_ai/src/neon_ai/ui/pages/site_page.py:23)
- [time_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/time_entry_page.py:18)
- [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py:31)
- [vendor_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_page.py:25)
- [workorder_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_form_page.py:21)
- [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py:26)

Why they remain:

- these files still import bare `database.*`, bare `gateway`, or bare top-level document helpers
- removing the bridge before import rewiring would break runtime imports immediately

Migration target:

- remove these call sites only after the same file is fully rewired to `neon_ai.*`

Priority:

- HIGH

#### Likely stale or no-longer-necessary call sites after the completed rewires

These callers no longer showed any same-file bare import or direct `D:\Argon_ai` usage in the current scan:

- [private_brain_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/private_brain_dialog.py:20)
- [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py:25)
- [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py:34)
- [vendor_invoice_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_invoice_page.py:27)

Why they remain:

- these appear to be leftover bridge calls from earlier migration phases rather than active same-file import needs
- they may still be retained conservatively for transitive runtime compatibility, but that was not proven by this scan

Migration target:

- remove from the file after a focused runtime verification pass confirms no transitive legacy dependency remains

Priority:

- MEDIUM

### 11.4 Files now clean and no longer depend on Argon

The following files were specifically re-checked against the current scan pattern set and showed no remaining:

- `ensure_legacy_import_paths()`
- `legacy_app_root()`
- `sys.path.insert(...)`
- `sys.path.append(...)`
- hardcoded `D:\Argon_ai`
- bare `database.*`
- bare `automation.*`
- bare `gateway`
- bare `invoice_generator`
- bare `wo_exporter`
- bare `purchase_order_generator`
- `load_dotenv(...)`

Verified clean files:

- [gateway.py](d:/Neon_ai/src/neon_ai/gateway.py)
- [local_brain.py](d:/Neon_ai/src/neon_ai/automation/local_brain.py)
- [read_model.py](d:/Neon_ai/src/neon_ai/database/read_model.py)
- [workorders.py](d:/Neon_ai/src/neon_ai/database/workorders.py)
- [automation.py](d:/Neon_ai/src/neon_ai/database/automation.py)
- [rfq.py](d:/Neon_ai/src/neon_ai/database/rfq.py)
- [purchases.py](d:/Neon_ai/src/neon_ai/database/purchases.py)
- [vendor_invoices.py](d:/Neon_ai/src/neon_ai/database/vendor_invoices.py)
- [purchase_order_generator.py](d:/Neon_ai/src/neon_ai/purchase_order_generator.py)
- [doc_generator.py](d:/Neon_ai/src/neon_ai/doc_generator.py)

### 11.5 Next safest migration cluster

Safest next cluster:

1. remove likely-stale `ensure_legacy_import_paths()` calls from already-rewired files
   - [private_brain_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/private_brain_dialog.py)
   - [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py)
   - [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py)
   - [vendor_invoice_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_invoice_page.py)
2. rewire remaining completed/read-mostly pages that already have clear Neon database replacements
   - [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py)
   - [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py)
   - [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py)
   - [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py)
   - [estimate_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_viewer_page.py)
   - [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py)
   - [purchase_order_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_viewer_page.py)
   - [receiving_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/receiving_viewer_page.py)
   - [site_page.py](d:/Neon_ai/src/neon_ai/ui/pages/site_page.py)
   - [vendor_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_page.py)
   - [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py)

Why this is the safest cluster:

- these are mostly import-rewire tasks rather than large workflow ports
- several already have Neon-owned database replacements available
- this cluster can shrink both the bare `database.*` count and the active `ensure_legacy_import_paths()` footprint without redesigning behavior

Current conclusion:

Argon runtime dependency surface is smaller after Phases 3A through 4C, but total separation is not complete.

## 12. Current Remaining Argon Dependencies After Phase 5E

Fresh source scan performed across `D:\Neon_ai\src` for:

- `ensure_legacy_import_paths()`
- `legacy_app_root()`
- `sys.path.insert`
- `sys.path.append`
- `D:\Argon_ai`
- imports from bare `database.*`
- imports from bare `automation.*`
- imports from bare `gateway`
- imports from bare `invoice_generator`
- imports from bare `wo_exporter`
- imports from bare `purchase_order_generator`
- `load_dotenv(...)`

### 12.1 Current scan summary

- remaining `ensure_legacy_import_paths()` runtime call sites: `2`
  - [main.py](d:/Neon_ai/src/neon_ai/main.py:12)
  - [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:24)
- remaining `legacy_app_root()` references: `2` runtime references plus `1` function definition
  - all in [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py)
- remaining `sys.path.insert(...)` references: `1`
  - [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:44)
- remaining `sys.path.append(...)` references: `0`
- remaining hardcoded `D:\Argon_ai` references: `1`
  - [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:26)
- remaining bare `database.*` import sites: `6`
  - [main.py](d:/Neon_ai/src/neon_ai/main.py:16)
  - [main.py](d:/Neon_ai/src/neon_ai/main.py:24)
  - [main.py](d:/Neon_ai/src/neon_ai/main.py:29)
  - [main.py](d:/Neon_ai/src/neon_ai/main.py:30)
  - [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:26)
  - [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:27)
- remaining bare `automation.*` import sites: `0`
- remaining bare `gateway` import sites: `1`
  - [main.py](d:/Neon_ai/src/neon_ai/main.py:15)
- remaining bare `invoice_generator` import sites: `0`
- remaining bare `wo_exporter` import sites: `0`
- remaining bare `purchase_order_generator` import sites: `0`
- remaining `load_dotenv(...)` calls: `3`
  - direct legacy `.env` load still present: `1`
    - [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:47)
  - Neon-local / non-Argon `load_dotenv(...)` calls: `2`
    - [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:49)
    - [config.py](d:/Neon_ai/src/neon_ai/config.py:41)

### 12.2 Active remaining Argon dependencies

| File | Line | Import / function | Why it remains | Neon replacement target | Priority |
| --- | --- | --- | --- | --- | --- |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:18) | `18` | `legacy_app_root()` | Central helper still resolves the legacy app root for the import bridge. | `bootstrap.py` with Neon-only path logic after all runtime callers are removed | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:26) | `26` | `Path(r"D:\Argon_ai\app")` | Hardcoded legacy fallback path still exists. | `bootstrap.py` with Argon fallback removed | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:37) | `37` | `legacy_root = legacy_app_root()` | The legacy bridge still activates the Argon app root at runtime. | `bootstrap.py` with legacy bridge removed after callers are rewired | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:44) | `44` | `sys.path.insert(0, path_str)` | This is the remaining import bridge that makes bare legacy modules importable. | `bootstrap.py` with bridge removed after remaining bare imports are gone | HIGH |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:47) | `47` | `load_dotenv(legacy_env_file, override=False)` | Legacy `.env` loading still participates in runtime config. | Neon-only env loading via [config.py](d:/Neon_ai/src/neon_ai/config.py) / bootstrap cleanup | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:12) | `12` | `ensure_legacy_import_paths()` | `main.py` still needs the bridge because it keeps bare `gateway` and bare `database.*` imports. | Rewire `main.py` to `neon_ai.gateway` and `neon_ai.database.*`, then remove the call | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:15) | `15` | `from gateway import check_for_instructions` | Startup gateway loop still resolves `gateway` through the legacy bridge. | `from neon_ai.gateway import check_for_instructions` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:16) | `16` | `from database.automation import ...` | Startup sweep loop still imports automation helpers by bare legacy name. | `from neon_ai.database.automation import ...` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:24) | `24` | `from database.purchases import ...` | Startup sweep loop still imports purchase helpers by bare legacy name. | `from neon_ai.database.purchases import ...` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:29) | `29` | `from database.rfq import sweep_for_outstanding_rfq_followups` | RFQ sweep still resolves through the legacy bridge. | `from neon_ai.database.rfq import sweep_for_outstanding_rfq_followups` | HIGH |
| [main.py](d:/Neon_ai/src/neon_ai/main.py:30) | `30` | `from database.vendor_invoices import sweep_for_ready_to_pay_vendor_invoices` | Vendor-invoice sweep still resolves through the legacy bridge. | `from neon_ai.database.vendor_invoices import sweep_for_ready_to_pay_vendor_invoices` | HIGH |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:24) | `24` | `ensure_legacy_import_paths()` | `Tiber` still needs the bridge because it keeps bare `database.*` imports. | Rewire `tiber_dialog.py` to `neon_ai.database.timer` and `neon_ai.database.workorders`, then remove the call | HIGH |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:26) | `26` | `from database.timer import ...` | Tiber still imports timer DB helpers by bare legacy name. | `from neon_ai.database.timer import ...` | HIGH |
| [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py:27) | `27` | `from database.workorders import ...` | Tiber still imports workorder DB helpers by bare legacy name. | `from neon_ai.database.workorders import ...` | HIGH |

### 12.3 Non-Argon scan hits that still appeared

These scan hits remain in the source tree, but they are not themselves unresolved Argon dependencies:

| File | Line | Import / function | Why it remains | Neon replacement target | Priority |
| --- | --- | --- | --- | --- | --- |
| [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py:49) | `49` | `load_dotenv(neon_env_file, override=True)` | Neon-local env load used to override config with `D:\Neon_ai\.env`. | None needed unless bootstrap is later consolidated into `config.py` | LOW |
| [config.py](d:/Neon_ai/src/neon_ai/config.py:41) | `41` | `load_dotenv(env_file, override=True)` | Neon-local env load used by the Neon-owned config helper. | None needed | LOW |

### 12.4 Files now clean

The following files were re-checked against the Phase 5E scan pattern set and showed no remaining:

- `ensure_legacy_import_paths()`
- `legacy_app_root()`
- `sys.path.insert(...)`
- `sys.path.append(...)`
- hardcoded `D:\Argon_ai`
- bare `database.*`
- bare `automation.*`
- bare `gateway`
- bare `invoice_generator`
- bare `wo_exporter`
- bare `purchase_order_generator`
- `load_dotenv(...)`

Verified clean files:

- [gateway.py](d:/Neon_ai/src/neon_ai/gateway.py)
- [local_brain.py](d:/Neon_ai/src/neon_ai/automation/local_brain.py)
- [read_model.py](d:/Neon_ai/src/neon_ai/database/read_model.py)
- [workorders.py](d:/Neon_ai/src/neon_ai/database/workorders.py)
- [automation.py](d:/Neon_ai/src/neon_ai/database/automation.py)
- [classification_feedback.py](d:/Neon_ai/src/neon_ai/database/classification_feedback.py)
- [connection.py](d:/Neon_ai/src/neon_ai/database/connection.py)
- [customers.py](d:/Neon_ai/src/neon_ai/database/customers.py)
- [employees.py](d:/Neon_ai/src/neon_ai/database/employees.py)
- [estimates.py](d:/Neon_ai/src/neon_ai/database/estimates.py)
- [folders.py](d:/Neon_ai/src/neon_ai/database/folders.py)
- [invoices.py](d:/Neon_ai/src/neon_ai/database/invoices.py)
- [materials.py](d:/Neon_ai/src/neon_ai/database/materials.py)
- [metrics.py](d:/Neon_ai/src/neon_ai/database/metrics.py)
- [purchases.py](d:/Neon_ai/src/neon_ai/database/purchases.py)
- [rfq.py](d:/Neon_ai/src/neon_ai/database/rfq.py)
- [roles.py](d:/Neon_ai/src/neon_ai/database/roles.py)
- [timer.py](d:/Neon_ai/src/neon_ai/database/timer.py)
- [timesheets.py](d:/Neon_ai/src/neon_ai/database/timesheets.py)
- [vendor_invoices.py](d:/Neon_ai/src/neon_ai/database/vendor_invoices.py)
- [vendors.py](d:/Neon_ai/src/neon_ai/database/vendors.py)
- [doc_generator.py](d:/Neon_ai/src/neon_ai/doc_generator.py)
- [invoice_generator.py](d:/Neon_ai/src/neon_ai/invoice_generator.py)
- [purchase_order_generator.py](d:/Neon_ai/src/neon_ai/purchase_order_generator.py)
- [wo_exporter.py](d:/Neon_ai/src/neon_ai/wo_exporter.py)
- [private_brain_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/private_brain_dialog.py)
- [customer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/customer_page.py)
- [dashboard_page.py](d:/Neon_ai/src/neon_ai/ui/pages/dashboard_page.py)
- [employee_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/employee_manager_page.py)
- [estimate_doc_view_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_doc_view_page.py)
- [estimate_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_entry_page.py)
- [estimate_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/estimate_viewer_page.py)
- [invoice_creator_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_creator_page.py)
- [invoice_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/invoice_viewer_page.py)
- [material_page.py](d:/Neon_ai/src/neon_ai/ui/pages/material_page.py)
- [price_request_page.py](d:/Neon_ai/src/neon_ai/ui/pages/price_request_page.py)
- [purchase_order_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_form_page.py)
- [purchase_order_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/purchase_order_viewer_page.py)
- [receiving_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/receiving_viewer_page.py)
- [rfq_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/rfq_viewer_page.py)
- [site_page.py](d:/Neon_ai/src/neon_ai/ui/pages/site_page.py)
- [time_entry_page.py](d:/Neon_ai/src/neon_ai/ui/pages/time_entry_page.py)
- [timesheet_manager_page.py](d:/Neon_ai/src/neon_ai/ui/pages/timesheet_manager_page.py)
- [vendor_invoice_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_invoice_page.py)
- [vendor_page.py](d:/Neon_ai/src/neon_ai/ui/pages/vendor_page.py)
- [workorder_form_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_form_page.py)
- [workorder_viewer_page.py](d:/Neon_ai/src/neon_ai/ui/pages/workorder_viewer_page.py)

### 12.5 Bootstrap / page conclusion

- `bootstrap.py` is now the only remaining source of direct `D:\Argon_ai` path logic.
- `bootstrap.py` is also the only remaining source of direct legacy `.env` loading.
- Among app pages/dialogs, only [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py) still actively requires `ensure_legacy_import_paths()` based on current same-file bare imports.
- [main.py](d:/Neon_ai/src/neon_ai/main.py) still requires `ensure_legacy_import_paths()` for the remaining startup bridge imports, but it is application bootstrap code rather than a page module.

### 12.6 Current conclusion

Argon runtime dependency surface is now concentrated in three places:

1. [bootstrap.py](d:/Neon_ai/src/neon_ai/bootstrap.py)
2. [main.py](d:/Neon_ai/src/neon_ai/main.py)
3. [tiber_dialog.py](d:/Neon_ai/src/neon_ai/ui/dialogs/tiber_dialog.py)

The scan does not prove total separation yet. It does prove that most migrated pages and internalized support modules are now clean of the audited Argon dependency patterns.
