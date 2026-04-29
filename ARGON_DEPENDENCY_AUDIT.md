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
