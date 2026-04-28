# PORT AUDIT

Audited against:

- `D:\Argon_ai\app\main.py`
- `D:\Argon_ai\app\vendorinvoice_form.py`
- `D:\Argon_ai\app\estimate_form.py`
- `D:\Argon_ai\app\rfq_viewer.py`
- `D:\Argon_ai\app\database`

Audited current port:

- `D:\Neon_ai\src\neon_ai\main.py`
- `D:\Neon_ai\src\neon_ai\ui\main_window.py`
- `D:\Neon_ai\src\neon_ai\ui\pages\vendor_invoice_page.py`
- `D:\Neon_ai\src\neon_ai\ui\pages\estimate_entry_page.py`
- `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py`

## Summary Verdicts

| Target | Verdict | Why |
| --- | --- | --- |
| `main.py` | COMPLETE | Source-level gateway loop wording and sweep order now match legacy; live mailbox/database integration was not executed in this audit. |
| `main_window.py` | COMPLETE | Source-level menu keys, tool labels, default submenu startup, refresh behavior, and Tiber callback now match legacy; live visual inspection was not performed in this audit. |
| `vendor_invoice_page.py` | COMPLETE | Matching workspace fields, button actions, DB calls, report generation, locking, payment, and refresh flow. |
| `estimate_entry_page.py` | COMPLETE | Matching draft load, material/labor staging, pricing sync, save/update, lock behavior, and refresh flow. |
| `rfq_viewer_page.py` | COMPLETE | Matching RFQ, bid matrix, PO build, direct PO, receiving, and document workflows with the same DB helpers and side effects. |

## 1. `D:\Neon_ai\src\neon_ai\main.py`

### 1. Legacy behavior inventory

- Fields:
  - No page form fields. This file defines the background gateway loop and launches the UI.
- Buttons:
  - None.
- Functions:
  - `run_gateway_loop()`
  - `__main__` startup path launching the gateway thread and Tk app.
- Database calls:
  - `check_for_instructions`
  - `sweep_for_ready_leads`
  - `sweep_for_locked_estimates`
  - `sweep_for_sent_estimate_followups`
  - `sweep_for_completed_rfq_estimates`
  - `sweep_for_aging_unsent_estimates`
  - `sweep_for_deposit_invoice_reminders`
  - `sweep_for_outstanding_rfq_followups`
  - `sweep_for_locked_purchase_orders`
  - `sweep_for_po_eta_followups`
  - `sweep_for_backordered_purchase_orders`
  - `sweep_for_ready_to_pay_vendor_invoices`
- Status transitions:
  - Warmup state for first 60 seconds.
  - Repeating heartbeat and sweeper cycle every 120 seconds.
- Side effects:
  - Prints startup banner and recurring operational logs.
  - Starts a daemon gateway thread before launching the app.
- Document/report generation:
  - None directly.
- Refresh behavior:
  - None directly.

### 2. Neon implementation inventory

- Matching fields:
  - None needed.
- Matching buttons:
  - None needed.
- Matching functions:
  - `run_gateway_loop()`
  - `main()` startup path launching the gateway thread and Qt app.
- Matching database calls:
  - Same sweep and inbox call set as legacy.
- Matching status transitions:
  - Same 60-second warmup and 120-second loop timing.
- Matching side effects:
  - Starts a daemon gateway thread before UI launch in the default path.
  - Emits the same legacy banner, heartbeat, warmup, inbox error, and sweeper error wording.
- Matching document/report generation:
  - None needed.
- Matching refresh behavior:
  - None needed.

### 3. Parity verdict

`COMPLETE`

### 4. Evidence

- Legacy loop and startup:
  - `D:\Argon_ai\app\main.py:39-85` `run_gateway_loop`
  - `D:\Argon_ai\app\main.py:358-365` `__main__`
- Neon loop and startup:
  - `D:\Neon_ai\src\neon_ai\main.py:33-70` `run_gateway_loop`
  - `D:\Neon_ai\src\neon_ai\main.py:73-84` `main` and `__main__`
- Matching sweep set:
  - Legacy calls at `D:\Argon_ai\app\main.py:67-77`
  - Neon calls at `D:\Neon_ai\src\neon_ai\main.py:54-64`
- Matching legacy wording restored in source:
  - Legacy banner/heartbeat/warmup/sweeper/error wording at `D:\Argon_ai\app\main.py:44-81`
  - Neon banner/heartbeat/warmup/sweeper/error wording at `D:\Neon_ai\src\neon_ai\main.py:35-68`

### 5. Gaps

- No blocking source-level parity gap was proven in this review.
- Live mailbox/database integration was not executed in this audit, so runtime behavior against real inbox and DB traffic remains unverified.

## 2. `D:\Neon_ai\src\neon_ai\ui\main_window.py`

### 1. Legacy behavior inventory

- Fields:
  - Two-column sidebar with category column and action column.
  - Main content container holding registered frames.
- Buttons:
  - Global tools: `⏱️ Tiber`, `🧠 Private Brain`
  - Category buttons generated from emoji-prefixed `menu_structure` keys.
  - Action buttons generated from submenu items.
- Functions:
  - `build_sidebar`
  - `load_sub_menu`
  - `show_frame`
  - `open_tiber`
  - `open_private_chat`
- Database calls:
  - None directly in this file.
- Status transitions:
  - Default submenu load to `📈 METRICS`
  - Default page show to first action in selected submenu
- Side effects:
  - `show_frame` calls `refresh_data()` on frames before raising them.
  - `open_tiber` passes dashboard refresh callback.
  - `open_private_chat` launches the private chat popup.
- Document/report generation:
  - None directly.
- Refresh behavior:
  - `show_frame` refreshes any page exposing `refresh_data`.
  - `open_tiber` forwards `dashboard.refresh_data` as the callback.

### 2. Neon implementation inventory

- Matching fields:
  - Same two-column sidebar layout and stacked page container.
  - Same page registry coverage for the menu-driven pages.
- Matching buttons:
  - Global tool buttons for `⏱️ Tiber` and `🧠 Private Brain` exist.
  - Category buttons and submenu action buttons exist.
- Matching functions:
  - `_build_sidebar`
  - `load_sub_menu`
  - `show_page`
  - `open_tiber`
  - `open_private_brain`
- Matching database calls:
  - None needed.
- Matching status transitions:
  - Default submenu load to `📈 METRICS` and default first-page behavior exist.
- Matching side effects:
  - `show_page` calls `refresh_data()` before switching pages.
  - `open_tiber` passes `dashboard.refresh_data`.
  - `open_private_brain` launches the dialog modally.
- Matching document/report generation:
  - None needed.
- Matching refresh behavior:
  - Preserved.

### 3. Parity verdict

`COMPLETE`

### 4. Evidence

- Legacy shell behavior:
  - `D:\Argon_ai\app\main.py:211-356` `ArgonApp`
  - `D:\Argon_ai\app\main.py:258-293` `menu_structure`
  - `D:\Argon_ai\app\main.py:303-343` sidebar and submenu building
  - `D:\Argon_ai\app\main.py:345-356` `show_frame`, `open_tiber`, `open_private_chat`
- Neon shell behavior:
  - `D:\Neon_ai\src\neon_ai\ui\main_window.py:48-257` `NeonMainWindow`
  - `D:\Neon_ai\src\neon_ai\ui\main_window.py:56-91` `menu_structure`
  - `D:\Neon_ai\src\neon_ai\ui\main_window.py:110-187` `_build_sidebar`
  - `D:\Neon_ai\src\neon_ai\ui\main_window.py:218-257` `load_sub_menu`, `show_page`, `open_tiber`, `open_private_brain`
- Restored source-level parity points:
  - Emoji-prefixed category keys restored at `D:\Neon_ai\src\neon_ai\ui\main_window.py:56-91`
  - `⏱️ Tiber` and `🧠 Private Brain` restored at `D:\Neon_ai\src\neon_ai\ui\main_window.py:129-137`
  - Default startup now loads `📈 METRICS` at `D:\Neon_ai\src\neon_ai\ui\main_window.py:106-108`
  - `show_page` still refreshes via `refresh_data()` at `D:\Neon_ai\src\neon_ai\ui\main_window.py:244-249`
  - `open_tiber` still passes `dashboard.refresh_data` at `D:\Neon_ai\src\neon_ai\ui\main_window.py:251-253`

### 5. Gaps

- No blocking source-level parity gap was proven in this review.
- Live visual inspection of the rendered Qt shell was not performed in this audit, so final visual equivalence to the Tk shell remains unverified.

## 3. `D:\Neon_ai\src\neon_ai\ui\pages\vendor_invoice_page.py`

### 1. Legacy behavior inventory

- Fields:
  - Pipeline view of vendor invoices.
  - PO selector.
  - Receiving paperwork selector.
  - Invoice number, invoice date, due date, notes.
  - Detail grid with estimate price, delivered qty, invoiced qty, invoice price, subtotal, backordered, match.
  - Total, PO coverage, and currently owed labels.
- Buttons:
  - `New Invoice`
  - `Mark Paid`
  - `Save`
  - `Lock Ready to Pay`
  - `View Report`
  - `Re-Send Report to Folder`
- Functions:
  - `refresh_data`
  - `reset_workspace`
  - `update_po_rollup_label`
  - `load_po_context`
  - `on_invoice_select`
  - `on_po_choice_select`
  - `on_receipt_choice_select`
  - `create_new_invoice`
  - `load_workspace`
  - `render_rows`
  - `_prompt_adjustment_note`
  - `on_detail_double_click`
  - `save_invoice`
  - `view_report`
  - `resend_report`
  - `lock_invoice`
  - `mark_paid`
- Database calls:
  - `get_vendor_invoice_pipeline`
  - `get_vendor_payables_summary`
  - `get_received_purchase_order_choices`
  - `get_vendor_invoice_creation_context`
  - `create_vendor_invoice_draft`
  - `get_vendor_invoice_workspace_by_invoice_id`
  - `apply_vendor_invoice_adjustment`
  - `generate_vendor_invoice_report`
  - `save_vendor_invoice_draft`
  - `lock_vendor_invoice_ready_to_pay`
  - `mark_vendor_invoice_paid`
- Status transitions:
  - New draft invoice creation.
  - Draft save.
  - Lock to `ReadyToPay`.
  - Mark to `Paid`.
  - Edit lockout when status is `ReadyToPay` or `Paid`.
- Side effects:
  - Adjustment writes back to invoice rows and estimate audit trail note path.
  - Save regenerates report.
  - Lock emails as ready to pay.
  - Resend opens folder containing regenerated report.
  - View opens report file.
- Document/report generation:
  - `generate_vendor_invoice_report(..., force=True/False)`
  - `lock_vendor_invoice_ready_to_pay(...)`
- Refresh behavior:
  - `refresh_data` reloads pipeline and PO choices.
  - Save, lock, and paid actions all call `refresh_data()` and reload workspace.

### 2. Neon implementation inventory

- Matching fields:
  - Same pipeline table, PO selector, receiving paperwork selector, header fields, detail table, and totals block.
- Matching buttons:
  - `New Invoice`
  - `Mark Paid`
  - `Save`
  - `Lock Ready to Pay`
  - `View Report`
  - `Re-Send Report to Folder`
- Matching functions:
  - Same workflow methods are present with the same responsibilities.
- Matching database calls:
  - Same vendor-invoice DB helpers are imported and called.
- Matching status transitions:
  - Same draft, lock, paid, and edit-lock behavior.
- Matching side effects:
  - Same report generation, folder open, file open, and refresh/reload behavior.
- Matching document/report generation:
  - Same `generate_vendor_invoice_report` and `lock_vendor_invoice_ready_to_pay` paths.
- Matching refresh behavior:
  - Same `refresh_data()` plus workspace reload pattern after mutating actions.

### 3. Parity verdict

`COMPLETE`

### 4. Evidence

- Legacy UI and button surface:
  - `D:\Argon_ai\app\vendorinvoice_form.py:21-121` `create_widgets`
- Neon UI and button surface:
  - `D:\Neon_ai\src\neon_ai\ui\pages\vendor_invoice_page.py:60-199` `_build_ui`
- Legacy refresh and PO context:
  - `D:\Argon_ai\app\vendorinvoice_form.py:123-265` `refresh_data`, `reset_workspace`, `update_po_rollup_label`, `load_po_context`
- Neon refresh and PO context:
  - `D:\Neon_ai\src\neon_ai\ui\pages\vendor_invoice_page.py:201-355` `refresh_data`, `reset_workspace`, `update_po_rollup_label`, `load_po_context`
- Legacy create/load/edit:
  - `D:\Argon_ai\app\vendorinvoice_form.py:267-467` `on_invoice_select`, `create_new_invoice`, `load_workspace`, `render_rows`, `_prompt_adjustment_note`, `on_detail_double_click`
- Neon create/load/edit:
  - `D:\Neon_ai\src\neon_ai\ui\pages\vendor_invoice_page.py:357-549` same method set
- Legacy save/report/lock/pay:
  - `D:\Argon_ai\app\vendorinvoice_form.py:469-567` `save_invoice`, `view_report`, `resend_report`, `lock_invoice`, `mark_paid`
- Neon save/report/lock/pay:
  - `D:\Neon_ai\src\neon_ai\ui\pages\vendor_invoice_page.py:551-643` same method set
- Matching DB helper imports:
  - Legacy helper usage appears across `D:\Argon_ai\app\vendorinvoice_form.py:128`, `220`, `302`, `314`, `451`, `476`, `502`, `515`, `532`, `560`
  - Neon imports the same helper set at `D:\Neon_ai\src\neon_ai\ui\pages\vendor_invoice_page.py:29-41` and calls them in the equivalent workflow methods

### 5. Gaps

- No blocking parity gap was proven in this review.
- The Neon page calls the same vendor-invoice database helpers, performs the same status transitions, and preserves the same report and refresh side effects.

## 4. `D:\Neon_ai\src\neon_ai\ui\pages\estimate_entry_page.py`

### 1. Legacy behavior inventory

- Fields:
  - Draft loader.
  - Customer, site, billing type.
  - Material markup, labor markup, combined margin label.
  - Scope of work text.
  - Labor staging inputs and labor ledger.
  - Material search, material staging inputs, and material ledger.
  - Grand total label.
- Buttons:
  - `➕ Add Labor`
  - `➕ Add Material`
  - `Save / Update Estimate`
  - `Clear Form`
- Functions:
  - `refresh_data`
  - `on_material_search`
  - `_perform_material_search`
  - `on_material_select`
  - `on_role_select`
  - `load_customers`
  - `refresh_draft_list`
  - `load_selected_draft`
  - `toggle_form_lock`
  - `load_sites`
  - `autocomplete_customer`
  - `autocomplete_site`
  - `recalculate_all_sell_prices`
  - `update_margin_display`
  - `update_grand_total`
  - `add_labor`
  - `add_material`
  - `remove_labor_line`
  - `remove_material_line`
  - `save_data`
  - `clear_form`
  - `load_saved_estimate`
- Database calls:
  - `search_materials`
  - `get_carried_price`
  - `get_all_customers`
  - `get_sites_for_customer`
  - `get_all_estimate_summaries`
  - `get_detailed_estimate_data`
  - `sync_estimate_pricing_from_sources`
  - `get_standard_roles`
  - `insert_full_estimate`
  - `update_draft_estimate`
- Status transitions:
  - Draft/new estimate state.
  - Locked UI state for `submitted`, `locked`, `sent`, `accepted`.
  - Update existing draft vs insert new estimate.
- Side effects:
  - Sync estimate pricing before loading draft.
  - Save writes labor/material lines including hidden IDs.
  - Save reloads the saved estimate so persisted values become visible immediately.
- Document/report generation:
  - None directly.
- Refresh behavior:
  - `refresh_data` reloads customers, drafts, and standard roles.
  - `load_saved_estimate` routes back through draft loading after save.

### 2. Neon implementation inventory

- Matching fields:
  - Same draft selector, customer/site/billing fields, markup fields, scope, labor/material staging areas, ledgers, margin label, and total label.
- Matching buttons:
  - `Add Labor`
  - `Add Material`
  - `Save / Update Estimate`
  - `Clear Form`
- Matching functions:
  - Same workflow methods and helper responsibilities.
- Matching database calls:
  - Same estimate, customer, materials, and roles helpers are imported and used.
- Matching status transitions:
  - Same locked-status detection and UI disable behavior.
  - Same update-existing vs insert-new branching.
- Matching side effects:
  - Same pricing sync before draft load.
  - Same save payload structure for labor and material rows, including hidden IDs.
  - Same post-save `refresh_draft_list()` plus `load_saved_estimate(...)`.
- Matching document/report generation:
  - None needed.
- Matching refresh behavior:
  - Preserved.

### 3. Parity verdict

`COMPLETE`

### 4. Evidence

- Legacy UI and button surface:
  - `D:\Argon_ai\app\estimate_form.py:38-199` `create_widgets`
- Neon UI and button surface:
  - `D:\Neon_ai\src\neon_ai\ui\pages\estimate_entry_page.py:70-230` `_build_ui`
- Legacy loaders and draft hydration:
  - `D:\Argon_ai\app\estimate_form.py:207-359` `on_material_search`, `_perform_material_search`, `on_material_select`, `refresh_data`, `on_role_select`, `load_customers`, `refresh_draft_list`, `load_selected_draft`
- Neon loaders and draft hydration:
  - `D:\Neon_ai\src\neon_ai\ui\pages\estimate_entry_page.py:233-377` same method set
- Legacy lock, math, add/remove, save, clear:
  - `D:\Argon_ai\app\estimate_form.py:365-628`
- Neon lock, math, add/remove, save, clear:
  - `D:\Neon_ai\src\neon_ai\ui\pages\estimate_entry_page.py:379-677`
- Matching DB helper imports:
  - Legacy imports at `D:\Argon_ai\app\estimate_form.py:3-13` and role import inside `refresh_data` at `:257`
  - Neon imports at `D:\Neon_ai\src\neon_ai\ui\pages\estimate_entry_page.py:27-36`
- Matching save write path:
  - Legacy `update_draft_estimate` / `insert_full_estimate` at `D:\Argon_ai\app\estimate_form.py:589-597`
  - Neon `update_draft_estimate` / `insert_full_estimate` at `D:\Neon_ai\src\neon_ai\ui\pages\estimate_entry_page.py:626-649`

### 5. Gaps

- No blocking parity gap was proven in this review.
- The Neon page preserves the same database write paths, lock behavior, and post-save reload behavior as legacy.

## 5. `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py`

### 1. Legacy behavior inventory

- Fields:
  - Dual pipeline: active RFQs and purchase orders.
  - Direct PO workspace with work order, vendor, ETA, ETA note, staged materials, direct total.
  - RFQ package workspace with estimate selector, date sent, due date, vendor selection, master estimate list, RFQ package list, preview text.
  - Bid matrix with carry toggles, quote number/date, quote attachment.
  - PO build workspace with work order, vendor, PO status, sent-to, sent-on, ETA, editable PO item list.
  - Receiving workspace with packing slip, date arrived, receiving grid.
  - PO document preview workspace.
- Buttons:
  - `↳ Build PO from selected RFQ`
  - Direct PO: `Add Material`, `Clear Draft`, `Remove Selected`, `Send to Wholesaler`, `Send to Folder`, `View Document`, `Lock PO`, `Save Draft`
  - RFQ package: `Add All`, `Add`, `Remove`, `Remove All`, `Preview RFQ`, `Send RFQ`, `Save RFQ Draft`
  - Bid matrix: `Attach Quote`, `Carry Selected Items to Estimate`
  - PO build: `Generate DOCX to Folder`, `Send PO to Vendor`, `Preview PO`, `Create & Lock PO`
  - Receiving: `Start Receiving`, `Save Receipt to Database`
- Functions:
  - `load_pipeline`
  - `refresh_data`
  - `on_rfq_select`
  - `on_po_select`
  - direct PO helpers including `_persist_direct_purchase_order`, `load_direct_purchase_order`, `send_direct_purchase_order`
  - RFQ package helpers including `load_estimates`, `on_estimate_select`, `generate_and_save_rfq`, `send_current_rfq`, `render_rfq_preview_for_selection`
  - matrix helpers including `on_matrix_double_click`, `on_carry_click`, `on_attach_file`
  - PO build helpers including `refresh_po_state_for_current_selection`, `on_create_po`, `load_existing_purchase_order`, `render_review_document`, `on_export_to_folder`, `on_send_po`
  - receiving helpers including `start_receiving_session`, `on_receiving_double_click`, `save_receiving_batch`, `reset_receiving_session`
- Database calls:
  - `save_rfq_package`
  - `get_all_vendors`
  - `get_quoted_vendors_for_estimate`
  - `get_active_rfqs`
  - `get_rfq_header_data`
  - `get_rfq_items_for_matrix`
  - `build_rfq_preview_text`
  - `send_rfq_by_id`
  - `update_rfq_item_price`
  - `save_quote_and_carry_items`
  - `get_all_estimates`
  - `get_estimate_materials`
  - `get_material_price_for_vendor`
  - `get_or_create_material_from_estimate`
  - `record_purchase_order_price`
  - `search_materials`
  - `get_purchase_orders_for_pipeline`
  - `get_carried_items_for_po_builder`
  - `get_vendors`
  - `get_open_workorder_choices`
  - `save_purchase_order_draft`
  - `lock_purchase_order_record`
  - `get_po_export_data`
  - `get_purchase_order_edit_items`
  - `get_po_followup_status`
  - `get_po_items`
  - `get_po_items_with_receiving`
  - `get_vendor_email_for_po`
  - `get_existing_po_details`
  - `save_new_purchase_order`
  - `archive_purchase_order_docx`
  - `send_purchase_order_now`
  - `log_material_receipt_batch`
  - `get_wo_resolution_for_rfq`
- Status transitions:
  - RFQ draft saved, then sent.
  - Quote saved and optionally carried into estimate pricing.
  - Direct PO draft, locked, archived, or sent.
  - RFQ-derived PO created and locked.
  - Receiving session active/inactive.
  - Existing PO read-only review mode.
- Side effects:
  - RFQ preview text generation.
  - Quote attachment path capture.
  - Quote carry updates estimate pricing depending on estimate status.
  - Direct PO persistence also records vendor-specific purchase pricing.
  - PO archive opens target folder.
  - PO send triggers vendor send workflow and updates pipeline.
  - Receiving save writes receipt batch and reloads receiving state.
- Document/report generation:
  - RFQ preview text through `build_rfq_preview_text`
  - PO internal review rendering through `render_review_document`
  - PO archive through `archive_purchase_order_docx`
  - PO send through `send_purchase_order_now`
- Refresh behavior:
  - `refresh_data` reloads estimates, both pipelines, and direct PO choices.
  - Save/send/archive/receiving actions repopulate affected workspaces and/or pipelines.

### 2. Neon implementation inventory

- Matching fields:
  - Same dual pipeline, six-tab workspace, RFQ package tables, bid matrix fields, PO detail/review fields, receiving fields, and direct PO fields.
- Matching buttons:
  - Same functional action set exists across direct PO, RFQ package, bid matrix, PO build, and receiving sections.
- Matching functions:
  - Same major workflow methods are present for RFQ, PO, direct PO, and receiving.
- Matching database calls:
  - Same RFQ, purchasing, materials, estimates, and timesheet helpers are imported and used.
- Matching status transitions:
  - Same RFQ draft/send flow, PO draft/lock/send flow, existing-PO read-only flow, and receiving-session state flow.
- Matching side effects:
  - Same preview generation, attachment capture, purchase-price recording, PO archive/send behavior, and receiving batch write/reload behavior.
- Matching document/report generation:
  - Same RFQ preview builder and PO document/archive/send paths.
- Matching refresh behavior:
  - Same top-level refresh and pipeline reload behavior.

### 3. Parity verdict

`COMPLETE`

### 4. Evidence

- Legacy top-level page and pipeline builders:
  - `D:\Argon_ai\app\rfq_viewer.py:37-130` `load_pipeline`, `create_widgets`
- Neon top-level page and pipeline builders:
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:101-247` `_build_ui`, `load_pipeline`
- Legacy direct PO persistence and side effects:
  - `D:\Argon_ai\app\rfq_viewer.py:733-807` `_persist_direct_purchase_order`, `preview_direct_purchase_order`, `export_direct_purchase_order`, `send_direct_purchase_order`
- Neon direct PO persistence and side effects:
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:929-1000` same method set
- Legacy existing/direct PO workspace loading:
  - `D:\Argon_ai\app\rfq_viewer.py:824-949` `load_direct_purchase_order`, `load_existing_purchase_order`
- Neon existing/direct PO workspace loading:
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:1014-1122` same method set
- Legacy RFQ workflow methods:
  - `D:\Argon_ai\app\rfq_viewer.py:411-482` `on_rfq_select`
  - `D:\Argon_ai\app\rfq_viewer.py:501-504` `refresh_data`
  - `D:\Argon_ai\app\rfq_viewer.py:993-1045` `generate_and_save_rfq`, `send_current_rfq`
  - `D:\Argon_ai\app\rfq_viewer.py:1081-1133` `on_carry_click`
  - `D:\Argon_ai\app\rfq_viewer.py:1135-1214` `on_create_po`, `on_view_po`, `render_review_document`, `on_export_to_folder`, `on_send_po`
  - `D:\Argon_ai\app\rfq_viewer.py:1216-1313` receiving workflow
- Neon RFQ workflow methods:
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:597-709` `on_rfq_select`
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:724-726` `refresh_data`
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:1235-1284` `generate_and_save_rfq`, `send_current_rfq`
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:1316-1350` `on_carry_click`
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:1392-1500` `on_create_po`, `on_view_po`, `render_review_document`, `on_export_to_folder`, `on_send_po`
  - `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:1502-1601` receiving workflow
- Matching DB helper usage examples:
  - Direct PO price recording:
    - Legacy `D:\Argon_ai\app\rfq_viewer.py:746-753`
    - Neon `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:940-947`
  - Existing PO follow-up hydration:
    - Legacy `D:\Argon_ai\app\rfq_viewer.py:882-891`
    - Neon `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:1060-1069`
  - RFQ send path:
    - Legacy `D:\Argon_ai\app\rfq_viewer.py:1032-1045`
    - Neon `D:\Neon_ai\src\neon_ai\ui\pages\rfq_viewer_page.py:1271-1284`

### 5. Gaps

- No blocking parity gap was proven in this review.
- The Neon file is large, but the reviewed write paths, status transitions, and side effects line up with the legacy file and call the same database helpers.

## Next Builder Prompt

```text
You are the builder agent for the Neon_ai PySide6 parity port.

Do not redesign.
Do not modify application code unless verification exposes a concrete parity mismatch.

Highest-priority remaining gap:
Runtime verification, not source implementation.

Task:
Perform a verification pass for the two source-level COMPLETE shell targets:
- D:\Neon_ai\src\neon_ai\main.py
- D:\Neon_ai\src\neon_ai\ui\main_window.py

Verification goals:
1. Visually inspect the Qt shell against D:\Argon_ai\app\main.py
   - Confirm the sidebar, submenu heading text, global tool labels, startup submenu, and action-column behavior match the legacy app as rendered, not just in source.

2. Run a controlled runtime check for run_gateway_loop
   - Confirm the banner, heartbeat, warmup, sweeper, inbox error, and sweeper error wording match the legacy loop at runtime.
   - Do not claim live mailbox/database integration is complete unless you actually exercised those integrations.

3. If and only if verification exposes a concrete mismatch, make the smallest possible source fix.

Return:
- What you visually verified
- What you runtime-verified
- Any concrete mismatches still present
- Whether any code change was actually necessary
```
