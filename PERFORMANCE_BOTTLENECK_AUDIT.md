# Neon_ai Performance Bottleneck Audit

Date: 2026-04-29

Scope: audit only. No application behavior, workflow, schema, query, email, or Ollama changes were made.

## Executive Summary

- Screen-to-screen navigation is currently blocked on the Qt UI thread because `show_page()` calls `refresh_data()` before the page is shown in `src/neon_ai/ui/main_window.py:254-259`.
- Startup imports are heavier than they appear. `src/neon_ai/main.py` imports the gateway and several database automation modules up front, and `src/neon_ai/ui/main_window.py` imports every page module plus `PrivateBrainDialog`.
- The Private Brain import path is startup-heavy. `src/neon_ai/ui/dialogs/private_brain_dialog.py:18` imports `brain_instance`, and `src/neon_ai/automation/local_brain.py:2` imports `ollama` and instantiates `brain_instance = ArgonLocalAI()` at `:185`.
- The gateway import path is also startup-heavy. `src/neon_ai/gateway.py:18` imports `ArgonLocalAI`, and `public_router = ArgonLocalAI()` runs at `:57`.
- Startup refresh is duplicated for the dashboard. `NeonMainWindow.__init__` calls `load_sub_menu("METRICS")` at `src/neon_ai/ui/main_window.py:108`, and `load_sub_menu()` itself auto-calls `show_page(actions[0].page_key)` at `:252`, then `__init__` immediately calls `show_page("DashboardFrame")` again at `:109`.
- Several pages also do a first-load double refresh because their constructors call `self.refresh_data()` and `show_page()` calls it again on first navigation:
  - `src/neon_ai/ui/pages/customer_page.py:60`
  - `src/neon_ai/ui/pages/site_page.py:151`
  - `src/neon_ai/ui/pages/material_page.py:57`
  - `src/neon_ai/ui/pages/vendor_page.py:58`
  - `src/neon_ai/ui/pages/employee_manager_page.py:50`
- Purchase-related refresh paths are likely paying an extra hidden cost because `ensure_purchase_schema()` in `src/neon_ai/database/purchases.py:15` executes `ALTER TABLE` / `CREATE TABLE IF NOT EXISTS` statements, and many purchase read functions call it inline.
- Every database helper opens a fresh PostgreSQL connection through `src/neon_ai/database/connection.py:9`, so pages with multi-query refreshes pay repeated connect overhead.

## 1. Startup Path

### What imports run at startup?

`src/neon_ai/main.py` imports all of the following before `main()` runs:

- `NeonMainWindow` from `src/neon_ai/ui/main_window.py`
- `check_for_instructions` from `src/neon_ai/gateway.py`
- sweeper functions from:
  - `src/neon_ai/database/automation.py`
  - `src/neon_ai/database/purchases.py`
  - `src/neon_ai/database/rfq.py`
  - `src/neon_ai/database/vendor_invoices.py`

`src/neon_ai/ui/main_window.py` then imports, at module import time:

- `PrivateBrainDialog` from `src/neon_ai/ui/dialogs/private_brain_dialog.py:18`
- `launch_tiber` from `src/neon_ai/ui/dialogs/tiber_dialog.py`
- every page module in `src/neon_ai/ui/pages`

### Does startup import gateway, Private Brain, Ollama, database modules, or heavy document generators?

Yes.

- Gateway: yes. `src/neon_ai/main.py:12`
- Database modules: yes. `src/neon_ai/main.py:13-27`
- Private Brain UI: yes. `src/neon_ai/ui/main_window.py:17`
- Ollama: yes, transitively through Private Brain and gateway:
  - `src/neon_ai/automation/local_brain.py:2`
  - `src/neon_ai/ui/dialogs/private_brain_dialog.py:18`
  - `src/neon_ai/gateway.py:18`
- AI object instantiation at import time:
  - `src/neon_ai/automation/local_brain.py:185`
  - `src/neon_ai/gateway.py:57`
- Heavy document/generator imports at startup:
  - `src/neon_ai/database/automation.py:6` imports `docx.Document`
  - `src/neon_ai/database/automation.py:9` imports `export_estimate_doc`
  - `src/neon_ai/ui/pages/invoice_creator_page.py:33` imports `generate_invoice_docx`
  - `src/neon_ai/ui/pages/workorder_viewer_page.py:33` imports `generate_workorder_docx`

### Does startup instantiate all pages or only lazy-load one page?

- Page instances are lazy-loaded through `_get_or_create_page()` in `src/neon_ai/ui/main_window.py:216-225`.
- Only `DashboardFrame` is instantiated at startup.
- However, all page modules are still imported at startup because `main_window.py` imports every page class at module level.

### Does startup call `refresh_data()` on any page?

Yes.

- `DashboardFrame` is refreshed on startup.
- It is refreshed twice:
  - once via `load_sub_menu("METRICS")` -> `show_page(actions[0].page_key)`
  - once again via explicit `show_page("DashboardFrame")`

### Additional startup contention

- `main.py` starts the gateway thread before `QApplication` is created:
  - `src/neon_ai/main.py:74-79`
- The gateway thread does not block the UI thread directly, but it can add network, file, database, and AI contention during startup because `run_gateway_loop()` immediately calls `check_for_instructions()`.

## 2. Page Navigation Path

### How `show_page()` works

From `src/neon_ai/ui/main_window.py:254-259`:

1. `show_page()` calls `_get_or_create_page(page_key)`.
2. If the page has a `refresh_data()` method, it is called synchronously.
3. Only after refresh completes does `self.stack.setCurrentWidget(page)` run.

### Does it create the page before showing it?

Yes.

- First visit: instantiate page first, then refresh it, then show it.
- Later visits: reuse cached page instance, refresh it again, then show it.

### Does it call `refresh_data()` before or after showing the page?

Before showing the page.

This is the core reason navigation feels slow: the user waits on the full refresh before the page switch becomes visible.

### Does `refresh_data()` block the UI thread?

Yes.

- `show_page()` calls it directly.
- No `QThread`, worker object, `QtConcurrent`, async task, or deferred event handoff is used for page refresh.
- The refresh runs inside the main Qt event loop thread.

### Is there any refresh throttling or caching?

- No navigation refresh throttle or freshness cache was found.
- No `last_refresh`, timestamp gate, or dirty-flag pattern was found in `main_window.py`.
- A few pages do have 250 ms search timers, but those only debounce search boxes, not page navigation refresh:
  - `src/neon_ai/ui/pages/material_page.py`
  - `src/neon_ai/ui/pages/estimate_entry_page.py`
  - `src/neon_ai/ui/pages/rfq_viewer_page.py`

## 3. Page-by-Page `refresh_data()` Audit

All of the following refreshes run on every `show_page()` navigation.

| Page file | Refresh path | DB functions called from refresh path | Load | First-visit double refresh? | Safe to throttle? | Better refresh trigger |
| --- | --- | --- | --- | --- | --- | --- |
| `src/neon_ai/ui/pages/base.py:40` | placeholder | none | LIGHT | No | N/A | first load only |
| `src/neon_ai/ui/pages/dashboard_page.py:81` | dashboard lists | `metrics.get_dashboard_metrics`, `estimates.get_dashboard_estimates` | HEAVY | No | Yes | first load, 30s throttle, after estimate/WO/time mutations, manual refresh |
| `src/neon_ai/ui/pages/customer_page.py:299` | customer pipeline + selected customer detail | `customers.get_customer_pipeline`; when a previous selection exists also `customers.get_customer_by_id`, `customers.get_customer_contacts`, `customers.get_customer_portfolio` | HEAVY | Yes | Yes | first load, after customer/site/WO mutation, manual refresh |
| `src/neon_ai/ui/pages/employee_manager_page.py:221` | employee pipeline + role dropdown | `roles.get_standard_roles`, `employees.get_all_employees` | MEDIUM | Yes | Yes | first load, after employee/role/task mutation, manual refresh |
| `src/neon_ai/ui/pages/estimate_doc_view_page.py:216` | estimate list for doc workflow | `estimates.get_dashboard_estimates` | MEDIUM | No | Yes | first load, after estimate status/doc mutation, manual refresh |
| `src/neon_ai/ui/pages/estimate_entry_page.py:230` | draft/customer/role loaders | `customers.get_all_customers`, `estimates.get_all_estimate_summaries`, `roles.get_standard_roles` | MEDIUM | No | Yes | first load, after customer/role/draft mutation, manual refresh |
| `src/neon_ai/ui/pages/estimate_viewer_page.py:159` | estimate review list | `estimates.get_dashboard_estimates` | MEDIUM | No | Yes | first load, after estimate mutation, manual refresh |
| `src/neon_ai/ui/pages/invoice_creator_page.py:499` | unbilled work order pipeline | `invoices.get_unbilled_workorders` | MEDIUM | No | Yes | first load, after invoice save/export/send, manual refresh |
| `src/neon_ai/ui/pages/invoice_viewer_page.py:112` | A/R pipeline | `invoices.get_accounts_receivable` | MEDIUM | No | Yes | first load, 30s throttle, after invoice mutation, manual refresh |
| `src/neon_ai/ui/pages/material_page.py:238` | material catalog + selected detail/history | `materials.get_material_pipeline`; when selection is preserved also `materials.get_material_by_id`, `materials.get_material_price_history` | HEAVY | Yes | Yes | first load, search/filter change, after material mutation, manual refresh |
| `src/neon_ai/ui/pages/price_request_page.py:122` | estimate chooser reset | `estimates.get_all_estimates` | LIGHT / MEDIUM | No | Yes | first load, after estimate/material mutation, manual refresh |
| `src/neon_ai/ui/pages/purchase_order_form_page.py:71` | PO form dropdowns | `purchases.get_next_po_id`, `workorders.get_open_workorders_dict`, `purchases.get_vendors`, `timer.get_employees` | MEDIUM | No | Yes | first load, after PO save, manual refresh |
| `src/neon_ai/ui/pages/purchase_order_viewer_page.py:197` | PO pipeline + follow-up panel reset/selection | `purchases.get_purchase_orders`; follow-up panel path uses `purchases.get_po_followup_status` | HEAVY | No | Yes | first load, 30s throttle, after PO/send/receiving mutation, manual refresh |
| `src/neon_ai/ui/pages/receiving_viewer_page.py:137` | receiving PO pipeline | `purchases.get_purchase_orders_for_pipeline` | MEDIUM | No | Yes | first load, 30s throttle, after receiving mutation, manual refresh |
| `src/neon_ai/ui/pages/rfq_viewer_page.py:757` | estimate list + RFQ pipeline + PO pipeline + direct PO dropdowns | `estimates.get_all_estimates`, `rfq.get_active_rfqs`, `purchases.get_purchase_orders_for_pipeline`, `timesheets.get_open_workorder_choices`, `purchases.get_vendors` | HEAVY | No | Yes | first load, 30s throttle, after RFQ/PO/receiving mutation, manual refresh |
| `src/neon_ai/ui/pages/site_page.py:153` | customer dropdown + site pipeline | `customers.get_customers`, `customers.get_sites` | MEDIUM | Yes | Yes | first load, after site/customer mutation, manual refresh |
| `src/neon_ai/ui/pages/time_entry_page.py:67` | employee/task/WO choices | `timer.get_employees`, `timer.get_tasks`, `timesheets.get_open_workorder_choices` | MEDIUM | No | Yes | first load, after employee/task/WO mutation, manual refresh |
| `src/neon_ai/ui/pages/timesheet_manager_page.py:557` | employee/week/task choice reset | `employees.get_all_employees`, `timesheets.get_open_workorder_choices`, `timesheets.get_standard_tasks` | MEDIUM | No | Yes | first load, after employee/task/WO mutation, manual refresh |
| `src/neon_ai/ui/pages/vendor_invoice_page.py:197` | vendor invoice pipeline + PO choices + payables summary; may also load PO context or invoice workspace | `vendor_invoices.get_vendor_invoice_pipeline`, `vendor_invoices.get_received_purchase_order_choices`, `vendor_invoices.get_vendor_payables_summary`, and conditionally `vendor_invoices.get_vendor_invoice_creation_context` or `vendor_invoices.get_vendor_invoice_workspace_by_invoice_id` | HEAVY | No | Yes | first load, 30s throttle, after receiving/PO/vendor-invoice mutation, manual refresh |
| `src/neon_ai/ui/pages/vendor_page.py:291` | vendor pipeline + selected vendor detail | `vendors.get_vendor_pipeline`; when a previous selection exists also `vendors.get_vendor_by_id`, `vendors.get_vendor_contacts`, `vendors.get_vendor_overview` | HEAVY | Yes | Yes | first load, after vendor/RFQ/PO/invoice mutation, manual refresh |
| `src/neon_ai/ui/pages/workorder_form_page.py:93` | work order form dropdowns | `workorders.get_next_workorder_id`, `customers.get_customers`, `customers.get_sites` | MEDIUM | No | Yes | first load, after WO save, manual refresh |
| `src/neon_ai/ui/pages/workorder_viewer_page.py:194` | work order pipeline | `workorders.get_dashboard_work_orders` | MEDIUM / HEAVY | No | Yes | first load, 30s throttle, after WO/time/PO mutation, manual refresh |

### Pages with a first-visit double refresh

- `customer_page.py`
- `employee_manager_page.py`
- `material_page.py`
- `site_page.py`
- `vendor_page.py`

On the first visit, these pages refresh once in their constructor and again when `show_page()` runs.

## 4. Database Query Audit

### Cross-cutting database findings

- Every helper opens a fresh connection with `psycopg2.connect(...)` via `src/neon_ai/database/connection.py:9`.
- Many refresh paths use multiple helpers, so they also use multiple new connections per navigation.
- Purchase-related reads are unusually expensive because several of them call `ensure_purchase_schema()` before reading.
- No refresh-time database helper was found to call email, Ollama, or external network APIs directly.
- One refresh-related helper does perform file I/O: `purchases.get_po_followup_status()` reads `po_dispatch_log.json` through `load_po_dispatch_log()`.

### Refresh-related database functions

| Function | Query / fan-out summary | Obvious expensive patterns |
| --- | --- | --- |
| `customers.get_customers` | loads all customers for dropdowns | `SELECT *`, no `LIMIT`, repeated dropdown load |
| `customers.get_sites` | loads all sites joined to customers | `s.*`, no `LIMIT`, repeated pipeline/dropdown load |
| `customers.get_all_customers` | loads all customers for estimate entry | no `LIMIT`, repeated dropdown load |
| `customers.get_customer_pipeline` | grouped customer pipeline with site and WO counts | no `LIMIT`, repeated pipeline load |
| `customers.get_customer_by_id` | single-customer lookup by ID | `SELECT *` |
| `customers.get_customer_contacts` | contacts by customer | no `LIMIT`, per-selection detail load |
| `customers.get_customer_portfolio` | 3 queries: sites, estimates, work orders + invoice rollup | multi-query fan-out on refresh |
| `employees.get_all_employees` | active employee list | no `LIMIT`, repeated dropdown/pipeline load |
| `roles.get_standard_roles` | all role choices | repeated dropdown load |
| `estimates.get_all_estimates` | full estimate list | no `LIMIT`, repeated dropdown load |
| `estimates.get_all_estimate_summaries` | estimate summaries joined to site | no `LIMIT`, repeated dropdown load |
| `estimates.get_dashboard_estimates` | estimate dashboard list with correlated subqueries to sum labor/material totals | no `LIMIT`, repeated across 3 pages, correlated subqueries |
| `metrics.get_dashboard_metrics` | dashboard work order metrics with multiple joins and correlated subqueries | no `LIMIT`, correlated subqueries, likely high cost |
| `invoices.get_unbilled_workorders` | open WOs with `EXISTS` draft check | no `LIMIT`, repeated pipeline load |
| `invoices.get_accounts_receivable` | invoice/customer joined A/R list | repeated pipeline load |
| `materials.get_material_pipeline` | full material catalog with many price/vendor columns | full-table pipeline load, no `LIMIT`, repeated on navigation |
| `materials.get_material_by_id` | material by ID | `SELECT *` |
| `materials.get_material_price_history` | price history by item, ordered desc | bounded by `LIMIT`, detail-load only |
| `purchases.get_vendors` | vendor list | `SELECT *`, no `LIMIT`, repeated dropdown load |
| `purchases.get_next_po_id` | next PO number lookup | lightweight, but repeated every navigation |
| `purchases.get_purchase_orders` | non-retired PO pipeline | no `LIMIT`, repeated pipeline load, calls `ensure_purchase_schema()` |
| `purchases.get_purchase_orders_for_pipeline` | PO pipeline joined to WO/site | no `LIMIT`, repeated across pages, calls `ensure_purchase_schema()` |
| `purchases.get_po_followup_status` | wrapper calling `get_po_export_data`, `load_po_dispatch_log`, `po_has_any_packing_slip`, `po_is_fully_received` | helper fan-out, file I/O, repeated status checks, purchase helpers call `ensure_purchase_schema()` |
| `rfq.get_active_rfqs` | active RFQ list joined to vendor/estimate/site | no `LIMIT`, repeated pipeline load, duplicate function definitions in `rfq.py` |
| `timesheets.get_open_workorder_choices` | open work order dropdown list | repeated dropdown load across multiple pages |
| `timer.get_employees` | employee list for timer/time-entry pages | `SELECT *`, no `LIMIT`, repeated dropdown load |
| `timer.get_tasks` | task list | no `LIMIT`, repeated dropdown load |
| `timesheets.get_standard_tasks` | standard task list | no `LIMIT`, repeated dropdown load |
| `vendor_invoices.get_vendor_invoice_pipeline` | vendor invoice pipeline joined to PO/vendor/site | no `LIMIT`, repeated pipeline load |
| `vendor_invoices.get_received_purchase_order_choices` | all received-or-invoiced POs for dropdown | no `LIMIT`, repeated dropdown load |
| `vendor_invoices.get_vendor_invoice_creation_context` | wrapper around `get_po_export_data`, `get_purchase_order_receipt_choices`, `get_purchase_order_invoice_rollup` | multi-query fan-out, purchase helpers underneath |
| `vendor_invoices.get_vendor_invoice_workspace_by_invoice_id` | wrapper around `get_vendor_invoice_detail`, `get_po_export_data`, `_build_default_invoice_rows`, rollup, receipt choices | very heavy wrapper, multi-query fan-out, repeated when reloading selected invoice |
| `vendor_invoices.get_vendor_payables_summary` | aggregate owed total + count | lightweight aggregate, but still repeated on every navigation |
| `vendors.get_vendor_pipeline` | grouped vendor pipeline with PO/RFQ counts | no `LIMIT`, repeated pipeline load |
| `vendors.get_vendor_by_id` | vendor by ID | `SELECT *` |
| `vendors.get_vendor_contacts` | contacts by vendor | no `LIMIT`, per-selection detail load |
| `vendors.get_vendor_overview` | 3 queries: RFQs, POs, vendor invoices | multi-query fan-out on refresh |
| `workorders.get_next_workorder_id` | next WO number lookup | lightweight, but repeated every navigation |
| `workorders.get_dashboard_work_orders` | work order dashboard list with correlated subqueries for estimate/material/time totals | no `LIMIT`, correlated subqueries, likely high cost |
| `workorders.get_open_workorders_dict` | open WO dropdown list | no `LIMIT`, repeated dropdown load |

### Specific query hotspots worth instrumenting first

1. `metrics.get_dashboard_metrics`
2. `estimates.get_dashboard_estimates`
3. `workorders.get_dashboard_work_orders`
4. `materials.get_material_pipeline`
5. `customers.get_customer_portfolio`
6. `vendors.get_vendor_overview`
7. `vendor_invoices.get_vendor_invoice_workspace_by_invoice_id`
8. `purchases.get_purchase_orders`
9. `purchases.get_purchase_orders_for_pipeline`
10. `purchases.get_po_followup_status`

### Non-query bottleneck hiding inside purchase reads

`src/neon_ai/database/purchases.py:15` defines:

- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements
- `CREATE TABLE IF NOT EXISTS` statements

and many purchase read helpers call `ensure_purchase_schema()` inline, including refresh-related functions at:

- `src/neon_ai/database/purchases.py:331`
- `src/neon_ai/database/purchases.py:804`
- `src/neon_ai/database/purchases.py:821`
- `src/neon_ai/database/purchases.py:930`
- `src/neon_ai/database/purchases.py:1731`
- `src/neon_ai/database/purchases.py:1788`

This is likely one of the highest-impact bottlenecks in the entire navigation path.

## 5. UI-Thread Blocking Audit

### Navigation-time blockers

Every page `refresh_data()` invoked through `show_page()` blocks the UI thread.

The heaviest navigation paths are:

- dashboard
- customer
- vendor
- material
- RFQ viewer
- purchase order viewer
- vendor invoice
- work order viewer

### Button/selection handlers likely to block the UI thread

#### Database-heavy synchronous handlers

- `estimate_doc_view_page.on_select` -> `estimates.get_detailed_estimate_data`
- `estimate_viewer_page._on_est_select` -> `estimates.get_full_estimate`
- `invoice_creator_page.on_wo_select` -> `invoices.get_invoice_header_data`
- `invoice_viewer_page._on_inv_select` -> `invoices.get_invoice_detail`
- `material_page._on_material_select` -> `materials.get_material_by_id`, `materials.get_material_price_history`
- `receiving_viewer_page._on_po_select` -> receiving detail query path
- `rfq_viewer_page.on_estimate_select` -> estimate materials + quoted vendors
- `rfq_viewer_page.on_rfq_select` -> RFQ header + matrix + PO builder data
- `vendor_invoice_page.load_po_context` / `load_workspace`
- `workorder_viewer_page._on_wo_select` -> `workorders.get_work_order_telemetry`

#### Document/report generation on the UI thread

- `estimate_doc_view_page.create_customer_copy` -> `build_client_estimate_doc`
- `invoice_creator_page.on_export_final` -> `generate_invoice_docx`
- `purchase_order_viewer_page._on_generate_docx` -> `archive_purchase_order_docx`
- `rfq_viewer_page.on_export_to_folder` -> `archive_purchase_order_docx`
- `timesheet_manager_page.on_save_timesheet`, `on_view_timesheet`, `on_send_to_folder`, `on_send_to_employee` -> `_export_timesheet_docx`
- `vendor_invoice_page.save_invoice`, `view_report`, `resend_report` -> `generate_vendor_invoice_report`
- `workorder_viewer_page.do_export` -> `generate_workorder_docx`

#### Email / outbound communication on the UI thread

- `customer_page._send_address_update_request`
- `estimate_doc_view_page.create_rfq`
- `invoice_creator_page.on_send_invoice`
- `invoice_viewer_page._on_resend_email`
- `purchase_order_viewer_page._on_send_po`
- `rfq_viewer_page.send_current_rfq`
- `rfq_viewer_page.on_send_po`
- `timesheet_manager_page.on_send_to_employee`

### Ollama / AI calls

- No page `refresh_data()` path calls Ollama.
- `PrivateBrainDialog` does AI work on a background Python thread, so its chat call is not a Qt navigation blocker.
- However, the Private Brain import path is a startup blocker because it imports `ollama` and constructs `ArgonLocalAI` at import time.

## 6. Instrumentation Plan

Use `time.perf_counter()` and log in `try/finally` blocks with this format:

```text
[PERF] area=<area> name=<name> elapsed_ms=<ms>
```

### Startup timing

Add logs around:

- `main.main()` total
- gateway-thread start block
- `QApplication(...)`
- `NeonMainWindow()` construction
- first dashboard `show_page`

Suggested names:

- `[PERF] area=startup name=main.total elapsed_ms=<ms>`
- `[PERF] area=startup name=gateway_thread_start elapsed_ms=<ms>`
- `[PERF] area=startup name=qapplication_create elapsed_ms=<ms>`
- `[PERF] area=startup name=main_window_init elapsed_ms=<ms>`

### Page creation timing

Add logs in `_get_or_create_page()`:

- page factory instantiate time
- `stack.addWidget(page)` time

Suggested names:

- `[PERF] area=ui name=create_page:DashboardFrame elapsed_ms=<ms>`
- `[PERF] area=ui name=create_page:VendorInvoiceFrame elapsed_ms=<ms>`

### `refresh_data()` timing

Add logs at the start/end of every page refresh method, especially:

- dashboard
- customer
- vendor
- material
- purchase_order_viewer
- receiving_viewer
- rfq_viewer
- vendor_invoice
- workorder_viewer

Suggested names:

- `[PERF] area=page name=dashboard_page.refresh_data elapsed_ms=<ms>`
- `[PERF] area=page name=vendor_invoice_page.refresh_data elapsed_ms=<ms>`

### Database function timing

Instrument the highest-traffic helpers first:

- `connection.get_connection`
- `metrics.get_dashboard_metrics`
- `estimates.get_dashboard_estimates`
- `workorders.get_dashboard_work_orders`
- `materials.get_material_pipeline`
- `customers.get_customer_portfolio`
- `vendors.get_vendor_overview`
- `purchases.get_purchase_orders`
- `purchases.get_purchase_orders_for_pipeline`
- `purchases.get_po_followup_status`
- `vendor_invoices.get_vendor_invoice_workspace_by_invoice_id`

Suggested names:

- `[PERF] area=db name=get_connection elapsed_ms=<ms>`
- `[PERF] area=db name=metrics.get_dashboard_metrics elapsed_ms=<ms>`
- `[PERF] area=db name=purchases.get_purchase_orders elapsed_ms=<ms>`

### Total `show_page()` timing

Add one outer timer around the full page switch:

- start before `_get_or_create_page()`
- end after `setCurrentWidget(page)`

Suggested name:

- `[PERF] area=ui name=show_page:RFQViewerFrame elapsed_ms=<ms>`

### Best low-risk implementation order for instrumentation

1. `show_page()`
2. `_get_or_create_page()`
3. top 8 page refreshes
4. top 10 DB helpers
5. `get_connection()`

## 7. Highest-Impact Fixes

| Rank | Bottleneck | Impact | Risk | Suggested fix | Files affected |
| --- | --- | --- | --- | --- | --- |
| 1 | Purchase read helpers call `ensure_purchase_schema()` and run DDL before reads | HIGH | LOW / MEDIUM | Move schema ensure to one-time startup/init path and keep read helpers read-only | `src/neon_ai/database/purchases.py`, `src/neon_ai/database/vendor_invoices.py` |
| 2 | `show_page()` refreshes before showing the page | HIGH | LOW | Show the page first, then refresh it immediately after paint or via queued callback | `src/neon_ai/ui/main_window.py` |
| 3 | Dashboard refreshes twice at startup | HIGH | LOW | Remove one of the two startup `show_page("DashboardFrame")` calls | `src/neon_ai/ui/main_window.py` |
| 4 | Several pages refresh twice on first visit because constructors call `self.refresh_data()` | HIGH | LOW | Remove constructor refresh or guard the first `show_page()` refresh | `customer_page.py`, `site_page.py`, `material_page.py`, `vendor_page.py`, `employee_manager_page.py` |
| 5 | Startup imports eagerly load gateway, AI, Ollama, docx, and generator code | HIGH | MEDIUM | Lazy-import gateway/Private Brain/page modules and instantiate AI only on first use | `main.py`, `main_window.py`, `gateway.py`, `automation/local_brain.py`, page modules |
| 6 | No refresh throttle or freshness cache on navigation | HIGH | LOW | Add a 30-second throttle plus dirty-flag bypass for mutation paths | `main_window.py`, heavy pages |
| 7 | Each helper opens a new PostgreSQL connection | HIGH | MEDIUM | Add connection pooling or per-refresh connection reuse | `src/neon_ai/database/connection.py`, many DB helpers |
| 8 | Dashboard/workorder/estimate queries use correlated subqueries and scan growing tables | HIGH | MEDIUM | Rewrite only the proven hot queries to pre-aggregated joins/CTEs after instrumentation | `metrics.py`, `estimates.py`, `workorders.py` |
| 9 | Customer/vendor/material/vendor-invoice refreshes combine pipeline reload with detail reload | MEDIUM / HIGH | LOW | Split list refresh from detail refresh; only reload detail when selection or dirty state changes | `customer_page.py`, `vendor_page.py`, `material_page.py`, `vendor_invoice_page.py` |
| 10 | Duplicate RFQ function definition and repeated shared dropdown loads across pages | MEDIUM | LOW | Remove duplicate `get_active_rfqs` definition and centralize cached choice loaders | `src/neon_ai/database/rfq.py`, `rfq_viewer_page.py`, `time_entry_page.py`, `timesheet_manager_page.py`, `purchase_order_form_page.py` |

## 8. Safe Implementation Plan

### Phase A: add timing instrumentation only

- Add `[PERF]` timers to:
  - `main.main()`
  - `NeonMainWindow._get_or_create_page()`
  - `NeonMainWindow.show_page()`
  - every heavy page `refresh_data()`
  - top database helpers listed above
- Do not change page order, query text, or worker behavior yet.

### Phase B: move `show_page` to display page before refresh

- Change navigation order from:
  - create page
  - refresh page
  - show page
- To:
  - create page
  - show page
  - refresh page
- This should improve perceived navigation speed even before deeper fixes land.

### Phase C: add 30-second refresh throttle

- Add per-page `last_refresh_at` and `dirty` flags.
- Skip navigation refresh if:
  - page was refreshed within 30 seconds
  - and the page is not marked dirty
- Always bypass throttle after data mutations.

Recommended first pages:

- dashboard
- customer
- vendor
- material
- purchase_order_viewer
- receiving_viewer
- rfq_viewer
- vendor_invoice
- workorder_viewer

### Phase D: add manual refresh buttons where needed

Add explicit refresh affordances to the heaviest read pages so users can force a reload without making every navigation pay the same cost.

Recommended first pages:

- dashboard
- customer
- vendor
- material
- RFQ viewer
- PO viewer
- vendor invoice
- workorder viewer

### Phase E: move heavy refreshes to background Qt workers

Move the slowest read paths off the UI thread first:

1. vendor invoice workspace/context loads
2. dashboard
3. material catalog
4. customer portfolio
5. vendor overview
6. RFQ viewer
7. PO viewer follow-up/status panel
8. work order telemetry/detail loads

### Phase F: optimize specific SQL queries

Only after Phase A timings confirm the hotspots:

1. Remove schema-DDL checks from purchase read paths.
2. Optimize:
   - `metrics.get_dashboard_metrics`
   - `estimates.get_dashboard_estimates`
   - `workorders.get_dashboard_work_orders`
   - `materials.get_material_pipeline`
   - `customers.get_customer_portfolio`
   - `vendors.get_vendor_overview`
   - `vendor_invoices.get_vendor_invoice_workspace_by_invoice_id`
3. Replace broad `SELECT *` reads in hot dropdown/pipeline loaders where practical.
4. Add selective limits or pagination where the page truly only needs a recent slice.

## Closing Assessment

The largest practical causes of slow navigation are not one single bad SQL statement. They are the combination of:

- synchronous refresh-before-show navigation
- repeated first-load refreshes
- full pipeline reloads on every navigation
- repeated fresh DB connections
- purchase-path schema checks running during reads
- heavy startup imports that pull gateway, AI, Ollama, and generator code into the GUI process immediately

The safest path is:

1. measure first
2. fix refresh order
3. throttle navigation refresh
4. background the worst pages
5. optimize only the proven hot queries and read helpers
