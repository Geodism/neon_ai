# Neon_ai Manual Smoke Test Checklist

This checklist is for manual validation of the completed Neon_ai parity pass.

Do not mark items passed automatically.
Do not treat this checklist as proof the app is production-ready.
Record what you actually observe in the pass/fail table at the end.

## 1. App startup

### Test 1. Launch Neon_ai
- Action:
  - Launch the Neon_ai application normally.
- Expected result:
  - The app opens without an immediate crash or import error.
  - A main window is shown.

### Test 2. Confirm shell title
- Action:
  - Look at the main window title bar.
- Expected result:
  - The title is `Argon Operations Command`.

### Test 3. Confirm menu categories
- Action:
  - Inspect the left category column.
- Expected result:
  - Categories appear with the legacy wording:
  - `📈 METRICS`
  - `👥 STAKEHOLDERS`
  - `📋 ESTIMATING`
  - `📋 PROJECT TRACKING`
  - `📦 MATERIALS`
  - `🛠️ EMPLOYEES`
  - `🧾 INVOICING`
  - `💸 PAYABLES`

### Test 4. Confirm Tiber / Private Brain labels
- Action:
  - Inspect the global tool buttons at the top of the category column.
- Expected result:
  - Buttons are labeled:
  - `⏱️ Tiber`
  - `🧠 Private Brain`

### Test 5. Confirm pages load without crash
- Action:
  - Click through each menu category and at least one page in each submenu.
- Expected result:
  - The selected page opens without a crash.
  - Navigation continues to work after each click.
  - Pages with `refresh_data()` load live content instead of showing stale startup state.

## 2. Gateway loop

Do not run destructive mailbox or database actions unless explicitly approved.

### Test 1. Confirm startup banner wording
- Action:
  - Launch Neon_ai from a console where stdout is visible.
- Expected result:
  - The console shows:
  - `🚀 ARGON GATEWAY: Multi-Tasking Active`

### Test 2. Confirm heartbeat wording
- Action:
  - Wait for the first loop output.
- Expected result:
  - The console shows:
  - `💓 [HEARTBEAT] Checking Inbox at ...`

### Test 3. Confirm sweeper wording
- Action:
  - After warmup has elapsed, observe the next sweep cycle.
- Expected result:
  - The console shows:
  - `🔍 [SWEEPER] Checking for Locked Estimates...`

### Test 4. Confirm warmup wording
- Action:
  - Observe the loop before the initial 60-second warmup finishes.
- Expected result:
  - The console shows:
  - `⏳ [WARMUP] Sweeper cooling down... ...s left.`

## 3. Vendor Invoice page

Page target:
- `PAYABLES` -> `Enter Supplier Bill`

### Test 1. Refresh pipeline
- Action:
  - Open the Vendor Invoice page.
  - If needed, navigate away and back to force `refresh_data()`.
- Expected DB function/path:
  - `get_vendor_invoice_pipeline`
  - `get_received_purchase_order_choices`
  - `get_vendor_payables_summary`
- Expected visible result:
  - The left invoice pipeline fills with vendor invoice rows.
  - The PO selector fills with received PO choices.
  - `Currently Owed` updates with live total owed and invoice count.

### Test 2. Select received PO
- Action:
  - Choose a PO from the `Purchase Order` dropdown.
- Expected DB function/path:
  - `get_vendor_invoice_creation_context`
- Expected visible result:
  - The workspace context line updates to the selected PO/vendor/site/status.
  - Receiving paperwork choices populate.
  - `New Invoice` becomes enabled.

### Test 3. Create new invoice draft
- Action:
  - Select receiving paperwork.
  - Click `New Invoice`.
- Expected DB function/path:
  - `create_vendor_invoice_draft`
  - then `get_vendor_invoice_workspace_by_invoice_id`
- Expected visible result:
  - A new draft invoice loads into the workspace.
  - Header fields become editable.
  - Line items appear in the reconciliation grid.

### Test 4. Save draft
- Action:
  - Enter invoice number and invoice date.
  - Click `Save`.
- Expected DB function/path:
  - `save_vendor_invoice_draft`
  - `generate_vendor_invoice_report(force=True)`
  - then page `refresh_data()`
  - then `get_vendor_invoice_workspace_by_invoice_id`
- Expected visible result:
  - Success message appears.
  - Pipeline refreshes.
  - Saved draft reloads with the same invoice data.
  - Report path becomes available for viewing.

### Test 5. View report
- Action:
  - Click `View Report`.
- Expected DB function/path:
  - `generate_vendor_invoice_report(force=False)`
  - local file open via `os.startfile(path)`
- Expected visible result:
  - The generated report file opens if it exists.

### Test 6. Re-send report to folder
- Action:
  - Click `Re-Send Report to Folder`.
- Expected DB function/path:
  - `generate_vendor_invoice_report(force=True)`
  - local folder open via `os.startfile(folder)`
- Expected visible result:
  - The report is regenerated.
  - The containing folder opens.

### Test 7. Lock ready to pay
- Action:
  - Click `Lock Ready to Pay`.
- Expected DB function/path:
  - `save_vendor_invoice_draft`
  - `lock_vendor_invoice_ready_to_pay`
  - then page `refresh_data()`
  - then `get_vendor_invoice_workspace_by_invoice_id`
- Expected visible result:
  - Success message appears.
  - Invoice reloads in locked state.
  - Editable actions become disabled as appropriate.

### Test 8. Mark paid
- Action:
  - Click `Mark Paid`.
  - Confirm the payment prompt.
- Expected DB function/path:
  - `mark_vendor_invoice_paid`
  - then page `refresh_data()`
  - then `get_vendor_invoice_workspace_by_invoice_id`
- Expected visible result:
  - Success message appears.
  - Invoice status updates to paid.
  - Edit/lock actions remain unavailable.

## 4. Estimate Entry page

Page target:
- `ESTIMATING` -> `New Estimate`

### Test 1. Customer preload
- Action:
  - Open the Estimate Entry page fresh.
- Expected DB function/path:
  - `get_all_customers`
- Expected visible result:
  - Customer choices are already loaded before manual refresh.

### Test 2. Customer to site dropdown
- Action:
  - Pick a customer.
- Expected DB function/path:
  - `get_sites_for_customer`
- Expected visible result:
  - Site dropdown repopulates for that customer only.

### Test 3. Material search
- Action:
  - Type at least 2 characters into the material description search.
- Expected DB function/path:
  - `search_materials`
- Expected visible result:
  - Matching material options appear in the dropdown.

### Test 4. Carried price lookup
- Action:
  - Select one material from the search results.
- Expected DB function/path:
  - `get_carried_price`
- Expected visible result:
  - Unit cost autofills from the carried-price source.
  - Hidden material identity is remembered for save.

### Test 5. Add labor
- Action:
  - Select a role.
  - Enter hours.
  - Click `Add Labor`.
- Expected DB function/path:
  - No immediate DB write.
  - Role rates are loaded by `get_standard_roles` during `refresh_data()`.
- Expected visible result:
  - The labor row is added.
  - `Cost Total` equals `Hours * Rate`.
  - `Sell Total` equals `Cost Total * (1 + labor markup/100)`.
  - Grand total increases.

### Test 6. Add material
- Action:
  - Enter/select a material, quantity, and unit cost.
  - Click `Add Material`.
- Expected DB function/path:
  - No immediate DB write.
- Expected visible result:
  - The material row is added.
  - `Cost Total` equals `Qty * Unit Cost`.
  - `Sell Total` equals `Cost Total * (1 + material markup/100)`.
  - Grand total increases.

### Test 7. Save new estimate
- Action:
  - Fill required customer/site/scope fields.
  - Click `Save / Update Estimate`.
- Expected DB function/path:
  - `insert_full_estimate`
  - then `get_all_estimate_summaries`
  - then `sync_estimate_pricing_from_sources`
  - then `get_detailed_estimate_data`
- Expected visible result:
  - Success message appears with a new estimate ID.
  - Draft list refreshes.
  - Saved estimate reloads immediately from persisted data.

### Test 8. Load draft
- Action:
  - Select a saved draft from `Load Draft`.
- Expected DB function/path:
  - `sync_estimate_pricing_from_sources`
  - `get_detailed_estimate_data`
  - customer/site support data already comes from `get_all_customers` and `get_sites_for_customer`
- Expected visible result:
  - Customer, site, billing type, scope, markups, labor rows, and material rows all reload.
  - Header changes to `ESTIMATE #... [STATUS]`.
  - Locked statuses disable editing.

### Test 9. Update draft
- Action:
  - Change scope, markups, or rows on a draft that is not locked.
  - Click `Save / Update Estimate`.
- Expected DB function/path:
  - `update_draft_estimate`
  - then `get_all_estimate_summaries`
  - then `sync_estimate_pricing_from_sources`
  - then `get_detailed_estimate_data`
- Expected visible result:
  - Success message appears.
  - Same draft reloads with updated persisted values.

### Test 10. Clear form
- Action:
  - Click `Clear Form`.
- Expected DB function/path:
  - No DB write.
- Expected visible result:
  - Labor and material tables clear.
  - Customer/site clear.
  - Scope clears.
  - Markups reset to `20.0`.
  - Header returns to `NEW ESTIMATE ENTRY`.
  - Form unlocks.

## 5. RFQ Viewer page

Page target:
- `MATERIALS` -> `Review RFQs`

### RFQ package

#### Test 1. Refresh package workspace
- Action:
  - Open the RFQ Viewer page.
- Expected DB function/path:
  - `get_all_estimates`
  - `get_active_rfqs`
  - `get_purchase_orders_for_pipeline`
  - `get_open_workorder_choices`
  - `get_vendors`
- Expected visible result:
  - Estimate dropdown loads.
  - RFQ pipeline loads.
  - PO pipeline loads.
  - Direct PO workorder/vendor choices load.

#### Test 2. Select estimate and vendor
- Action:
  - Pick an estimate in `Source Estimate`.
  - Inspect vendor radio choices.
- Expected DB function/path:
  - `get_quoted_vendors_for_estimate`
  - `get_all_vendors`
  - `get_estimate_materials`
- Expected visible result:
  - Vendor radio list appears.
  - Previously-sent vendors show `(Sent)` and gray styling.
  - Master estimate material list populates.

#### Test 3. Build RFQ package
- Action:
  - Use `Add`, `Add All`, `Remove`, and `Remove All`.
- Expected DB function/path:
  - No immediate DB write.
  - Preview regeneration path uses `build_rfq_preview_text`.
- Expected visible result:
  - Materials move between master and package lists.
  - RFQ preview updates after each move.

#### Test 4. Save RFQ draft
- Action:
  - Choose a vendor.
  - Enter due date.
  - Package at least one item.
  - Click `Save RFQ Draft`.
- Expected DB function/path:
  - `save_rfq_package`
  - then `get_active_rfqs` via pipeline refresh
  - preview path `build_rfq_preview_text`
- Expected visible result:
  - Success message appears.
  - RFQ gets an ID and appears in the RFQ pipeline.
  - Preview remains available.

#### Test 5. Preview RFQ
- Action:
  - Click `Preview RFQ`.
- Expected DB function/path:
  - `build_rfq_preview_text`
- Expected visible result:
  - Preview panel shows the current RFQ draft text.

#### Test 6. Send RFQ
- Action:
  - Click `Send RFQ`.
- Expected DB function/path:
  - `send_rfq_by_id`
  - if draft not yet saved, `save_rfq_package` first
  - then `get_active_rfqs` via pipeline refresh
- Expected visible result:
  - Success message shows recipient email.
  - RFQ pipeline refreshes to current status.

### Quote matrix

#### Test 1. Select RFQ from pipeline
- Action:
  - Click an RFQ in the left RFQ pipeline.
- Expected DB function/path:
  - `get_rfq_header_data`
  - `get_rfq_items_for_matrix`
  - `get_wo_resolution_for_rfq`
  - `get_carried_items_for_po_builder`
- Expected visible result:
  - Quote fields load.
  - Matrix rows load.
  - Carry flags display as `[✓]` or `[ ]`.
  - PO builder tab hydrates from the same RFQ selection.

#### Test 2. Attach quote file
- Action:
  - Click `📎 Attach Quote`.
- Expected DB function/path:
  - Local file picker only.
  - Path later persists through `save_quote_and_carry_items`.
- Expected visible result:
  - Attached file label updates in green with file name.

#### Test 3. Edit quote price inline
- Action:
  - Double-click a `Quote Unit` cell.
  - Enter a new price inline.
- Expected DB function/path:
  - `update_rfq_item_price`
- Expected visible result:
  - Edited quote price updates in the grid.
  - `Quote Ext` recalculates immediately.

#### Test 4. Carry selected items to estimate
- Action:
  - Click the `Carry` column to toggle one or more rows.
  - Enter quote number.
  - Click `➡️ Carry Selected Items to Estimate`.
- Expected DB function/path:
  - `save_quote_and_carry_items`
- Expected visible result:
  - Success/saved message appears.
  - Draft estimates update pricing when allowed.
  - Frozen estimates report that the snapshot stayed frozen.

### PO builder

#### Test 1. RFQ-driven PO builder hydration
- Action:
  - Select an RFQ from the pipeline.
  - Switch to `🛒 3. Build PO`.
- Expected DB function/path:
  - `get_wo_resolution_for_rfq`
  - `get_carried_items_for_po_builder`
  - `get_existing_po_details`
- Expected visible result:
  - Work order field resolves from RFQ context.
  - Vendor label fills.
  - PO line items populate from carried items not yet fully ordered.
  - Status label reflects whether a PO already exists.

#### Test 2. Edit buy-now qty inline
- Action:
  - Double-click `Buy Now` on a PO row.
  - Enter a value inline.
- Expected DB function/path:
  - No immediate DB write.
- Expected visible result:
  - Quantity clamps to `0..remaining`.
  - Grid updates in place.

#### Test 3. Create & Lock PO
- Action:
  - Click `Create & Lock PO`.
- Expected DB function/path:
  - `save_new_purchase_order`
  - then `get_purchase_orders_for_pipeline`
  - then `get_po_export_data`
  - `get_po_followup_status`
  - `get_po_items_with_receiving`
  - `get_vendor_email_for_po`
  - `get_po_items`
- Expected visible result:
  - PO is created and locked.
  - PO pipeline refreshes.
  - Status label turns green.
  - Document tab becomes available.

#### Test 4. Preview PO
- Action:
  - Click `Preview PO`.
- Expected DB function/path:
  - `get_po_export_data`
  - `get_po_items`
- Expected visible result:
  - PO document preview tab shows internal review text.

#### Test 5. Generate DOCX to Folder
- Action:
  - Click `Generate DOCX to Folder`.
- Expected DB function/path:
  - `archive_purchase_order_docx`
- Expected visible result:
  - Success message shows archive path.
  - Target folder opens if allowed locally.
  - Status label updates to archived wording.

#### Test 6. Send PO to Vendor
- Action:
  - Click `Send PO to Vendor`.
- Expected DB function/path:
  - `send_purchase_order_now`
  - then `get_purchase_orders_for_pipeline`
  - then `get_po_export_data`
  - `get_po_followup_status`
  - `get_po_items_with_receiving`
  - `get_vendor_email_for_po`
- Expected visible result:
  - Success message shows recipient email and attachment path.
  - PO pipeline refreshes.
  - PO detail area reloads with send/follow-up information.

### Receiving

#### Test 1. Load receiving rows from an existing PO
- Action:
  - Select a non-draft PO from the left PO pipeline.
  - Switch to `🚚 4. Receiving`.
- Expected DB function/path:
  - `get_po_items_with_receiving`
- Expected visible result:
  - Receiving grid populates with ordered, received, remaining, and last-arrived values.

#### Test 2. Start receiving session
- Action:
  - Enter a packing slip.
  - Click `Start Receiving`.
- Expected DB function/path:
  - No immediate DB write.
- Expected visible result:
  - Button text changes to `Receiving Session Active`.
  - `Save Receipt to Database` becomes enabled.
  - Packing slip field becomes locked.

#### Test 3. Edit arriving-now inline
- Action:
  - Double-click `Arriving Now`.
  - Enter a quantity inline.
- Expected DB function/path:
  - No immediate DB write.
- Expected visible result:
  - Quantity updates in the grid.
  - Overage confirmation appears only when appropriate.

#### Test 4. Save receipt to database
- Action:
  - Click `Save Receipt to Database`.
- Expected DB function/path:
  - `log_material_receipt_batch`
  - then `get_po_items_with_receiving`
  - then `get_purchase_orders_for_pipeline`
- Expected visible result:
  - Success message appears.
  - Receiving grid reloads with updated received totals.
  - Receiving session resets.
  - PO pipeline refreshes.

### Direct-PO actions

#### Test 1. Refresh direct PO choices
- Action:
  - Open the RFQ page or navigate away and back.
- Expected DB function/path:
  - `get_open_workorder_choices`
  - `get_vendors`
- Expected visible result:
  - Direct PO work order and vendor dropdowns populate.

#### Test 2. Direct material search and vendor price lookup
- Action:
  - Select a vendor.
  - Type at least 2 characters in direct material description.
  - Select a material.
- Expected DB function/path:
  - `search_materials`
  - `get_material_price_for_vendor`
- Expected visible result:
  - Matching materials appear.
  - Unit price autofills for the selected vendor/material.

#### Test 3. Add material and edit inline
- Action:
  - Enter qty and use `Add Material`.
  - Double-click qty or unit price to edit inline.
- Expected DB function/path:
  - No immediate DB write.
- Expected visible result:
  - Row appears in the direct PO grid.
  - Line total recalculates after inline edit.
  - Direct PO total updates.

#### Test 4. Save draft
- Action:
  - Select work order and vendor.
  - Add at least one line.
  - Click `Save Draft`.
- Expected DB function/path:
  - `save_purchase_order_draft`
  - `record_purchase_order_price`
  - if needed, `get_or_create_material_from_estimate`
  - then `get_purchase_orders_for_pipeline`
- Expected visible result:
  - Draft PO is saved.
  - PO appears in the PO pipeline.
  - Status text updates to `PO #... saved as Draft`.

#### Test 5. Lock PO
- Action:
  - Click `Lock PO`.
- Expected DB function/path:
  - `lock_purchase_order_record`
  - `record_purchase_order_price`
  - if needed, `get_or_create_material_from_estimate`
  - then `get_purchase_orders_for_pipeline`
- Expected visible result:
  - PO status updates to locked.
  - Pipeline refreshes.

#### Test 6. View document
- Action:
  - Click `View Document`.
- Expected DB function/path:
  - persistence path through `save_purchase_order_draft` or `lock_purchase_order_record`
  - then `get_po_export_data`
  - `get_po_items`
- Expected visible result:
  - Document preview tab opens with PO text.

#### Test 7. Send to folder
- Action:
  - Click `Send to Folder`.
- Expected DB function/path:
  - persistence path through `save_purchase_order_draft` or `lock_purchase_order_record`
  - then `archive_purchase_order_docx`
- Expected visible result:
  - Folder opens if allowed locally.
  - Status text updates to archived wording.

#### Test 8. Send to wholesaler
- Action:
  - Click `Send to Wholesaler`.
- Expected DB function/path:
  - `lock_purchase_order_record`
  - `record_purchase_order_price`
  - `send_purchase_order_now`
  - then `get_purchase_orders_for_pipeline`
- Expected visible result:
  - Success path runs.
  - Status text updates to sent wording.
  - PO pipeline refreshes.

## 6. Pass/fail table

| Area | Test | Expected result | Actual result | Pass/Fail | Notes |
| --- | --- | --- | --- | --- | --- |
| App startup | Launch Neon_ai | Main window opens without crash |  |  |  |
| App startup | Shell title | `Argon Operations Command` |  |  |  |
| App startup | Menu categories | Legacy category labels visible |  |  |  |
| App startup | Tool labels | `⏱️ Tiber` and `🧠 Private Brain` visible |  |  |  |
| Gateway loop | Startup banner | Legacy banner wording visible |  |  |  |
| Gateway loop | Heartbeat | Legacy heartbeat wording visible |  |  |  |
| Gateway loop | Sweeper | Legacy sweeper wording visible |  |  |  |
| Gateway loop | Warmup | Legacy warmup wording visible |  |  |  |
| Vendor Invoice | Refresh pipeline | Pipeline, PO choices, owed summary load |  |  |  |
| Vendor Invoice | New invoice draft | Draft invoice workspace opens |  |  |  |
| Vendor Invoice | Save draft | Draft saves and reloads |  |  |  |
| Vendor Invoice | View report | Report opens |  |  |  |
| Vendor Invoice | Re-send report | Folder opens with regenerated report |  |  |  |
| Vendor Invoice | Lock ready to pay | Invoice locks and reloads |  |  |  |
| Vendor Invoice | Mark paid | Invoice marks paid and reloads |  |  |  |
| Estimate Entry | Customer preload | Customer list available at page open |  |  |  |
| Estimate Entry | Customer to site | Site list filters by customer |  |  |  |
| Estimate Entry | Material search | Matching materials appear |  |  |  |
| Estimate Entry | Carried price | Unit cost autofills |  |  |  |
| Estimate Entry | Add labor | Labor row and totals update |  |  |  |
| Estimate Entry | Add material | Material row and totals update |  |  |  |
| Estimate Entry | Save new estimate | New estimate saves and reloads |  |  |  |
| Estimate Entry | Load draft | Draft hydrates correctly |  |  |  |
| Estimate Entry | Update draft | Draft updates and reloads |  |  |  |
| Estimate Entry | Clear form | Form resets to new-entry state |  |  |  |
| RFQ Viewer | RFQ package | Package build/save/send flow works |  |  |  |
| RFQ Viewer | Quote matrix | Quote edit/carry flow works |  |  |  |
| RFQ Viewer | PO builder | PO create/preview/export/send flow works |  |  |  |
| RFQ Viewer | Receiving | Receiving session/save flow works |  |  |  |
| RFQ Viewer | Direct PO | Direct PO draft/lock/view/export/send flow works |  |  |  |
