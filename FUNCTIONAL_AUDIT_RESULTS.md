# Neon_ai Functional Audit Results

Audit date: 2026-04-29
Workspace: `D:\Neon_ai`
Result: Functional audit executed with user-approved live test database
Recommendation: ready for continued testing

## Executive Summary

A 3-month UI-driven simulation was executed against the approved connected Supabase database with `NEON_DISABLE_GATEWAY=1` for normal runtime safety. Test customers, sites, vendors, materials, roles, tasks, employees, estimates, RFQs, purchase orders, receipts, time, invoices, and vendor bills were created using the real PySide6 workflows as much as practical, with all outbound emails guarded to `miltonjaycox@gmail.com` only.

## Post-Fix Verification

- Root cause fixed: after export, the invoice creator page cleared its active invoice context and the invoice header query only reloaded `Draft` invoices, so the just-exported invoice could not be found by the send action.
- Verification record used: existing `TEST_3MO_` invoice `#3` on work order `#3`.
- Paid-before-sent guard verified: attempting `Mark as PAID` on draft invoice `#3` showed `Invoice cannot be marked paid until it has been sent.` and left the invoice in `Draft`.
- Export verified: invoice `#3` exported successfully and persisted `CustomerInvoiceDocPath` to `D:/Projects/503 Jobsite Road 3, Burnaby - TEST_3MO_Customer_03/3\Invoice_3_TEST_3MO_Customer_03.docx`.
- Send verified: invoice `#3` was sent to `miltonjaycox@gmail.com` only, `InvoiceStatus` moved to `Sent`, and `CustomerInvoiceSentAt` was populated.
- Paid-after-sent verified: invoice `#3` was then marked `Paid`, with `CustomerInvoiceSentAt` preserved.
- Historical test data note: invoices `#1` and `#2` remain in an inconsistent `Paid`/unsent state because they were created before this fix. They demonstrate the original bug and were not backfilled.

## Final Test Data Summary

- Test records created: `86`
- Existing non-test records modified: `0`
- Existing non-test records deleted: `0`
- Emails sent: `4`
- UI transactions executed: `84`

## Records Created By Table/Type

| Table / Type | Count Created | Notes |
| --- | ---: | --- |
| Customer | 12 | TEST_3MO_Customer_01, TEST_3MO_Customer_02, TEST_3MO_Customer_03 |
| Site | 12 | TEST_3MO_Customer_01_Site, TEST_3MO_Customer_02_Site, TEST_3MO_Customer_03_Site |
| Vendor | 4 | TEST_3MO_Nedco, TEST_3MO_Gescan, TEST_3MO_Eecol |
| Material | 25 | TEST_3MO_Material_01, TEST_3MO_Material_02, TEST_3MO_Material_03 |
| Employee | 4 | TEST_3MO_Emp_01, TEST_3MO_Emp_02, TEST_3MO_Emp_03 |
| StandardRole | 3 | TEST_3MO_Apprentice, TEST_3MO_Foreman, TEST_3MO_Journeyman |
| Task | 4 | TEST_3MO_Service, TEST_3MO_Install, TEST_3MO_Paperwork |
| Estimate | 5 | Created through UI unless noted otherwise |
| WorkOrder | 3 | Created through UI unless noted otherwise |
| PriceRequest | 2 | Created through UI unless noted otherwise |
| PurchaseOrder | 2 | Created through UI unless noted otherwise |
| Invoice | 3 | Created through UI unless noted otherwise |
| VendorInvoice | 2 | Created through UI unless noted otherwise |
| Time | 5 | Created through UI unless noted otherwise |

## Emails Sent To `miltonjaycox@gmail.com`

| Subject | Recipient | CC |
| --- | --- | --- |
| Invoice #3 - Work Order #3 | miltonjaycox@gmail.com | None |
| Timesheet Review Needed - TEST_3MO_Emp_01 - Week of 2026-04-27 | miltonjaycox@gmail.com | None |
| Pay Vendor Invoice: TEST_3MO_VINV_001 | miltonjaycox@gmail.com | None |
| Pay Vendor Invoice: TEST_3MO_VINV_002 | miltonjaycox@gmail.com | None |

## Workflows Passed

- Customer creation
- Site creation
- Vendor creation
- Material creation
- Employee, role, and task setup
- Estimate creation
- Estimate revision before approval
- Estimate approval and work order conversion
- RFQ creation and send
- Vendor quote entry and carry pricing
- Purchase order creation, export, and send
- Full receiving
- Partial/backordered receiving
- Time entry
- Timesheet review, export, lock, and send
- Customer invoice draft save
- Customer invoice export, send, sent timestamp persistence, and paid-state guard
- Vendor invoice save, adjustment, ready-to-pay, and paid
- Work order closeout on completed jobs
- Dashboard review

## Workflows Failed

- Historical TEST_3MO invoices `#1` and `#2` remain `Paid` with null `CustomerInvoiceSentAt` because they were created before the invoice-state fix and were left unchanged as audit evidence.

## Bugs / Findings

| Severity | Area | Finding |
| --- | --- | --- |
| MEDIUM | Historical Test Data | TEST_3MO invoices `#1` and `#2` remain `Paid` while `CustomerInvoiceSentAt` is null because they were created before the invoice-state fix. |

## Confusing UI Areas

- Estimate approval uses a modal dialog with required PO/document inputs and no non-modal breadcrumb back to the originating estimate.
- RFQ/PO/receiving flow is dense and split across several tabs with limited state summaries when switching selections.

## Missing Validation

- Customer and vendor email safety depends on entered record data; there is no built-in test-mode recipient override.
- RFQ/PO send actions rely on vendor contact email presence but do not expose a final recipient confirmation step on the send action itself.
- Vendor account numbers reject non-digit input only after save; the form does not constrain or validate this before submission.

## Incorrect Totals / Statuses

- Historical invoices `#1` and `#2` remain in `Paid` status with null `CustomerInvoiceSentAt`; post-fix verification on invoice `#3` followed the correct `Draft -> Exported -> Sent -> Paid` path.

## Slow Pages Or Freezes

- InvoiceCreatorFrame: 2989 ms
- InvoiceCreatorFrame: 3018 ms
- VendorInvoiceFrame: 2198 ms
- VendorInvoiceFrame: 14802 ms

## Stale Data Caused By Refresh Throttling

None noted.

## Background Worker Issues

- Main app still auto-starts the gateway thread on normal launch; audit run required `NEON_DISABLE_GATEWAY=1`.

## Connection Pool Issues

None noted.

## Document Generation Issues

- The invoice generator's non-ASCII console success print was replaced with ASCII output, and post-fix export verification completed without the prior charmap failure.

## Email Behavior Issues

- The customer invoice send flow now reloads the exported invoice correctly and was verified by sending invoice `#3` to `miltonjaycox@gmail.com` only.

## Database Consistency Issues

- New verification on invoice `#3` followed the enforced state rule `Draft -> Exported -> Sent -> Paid`.
- Historical invoices `#1` and `#2` still show the original bad state (`Paid` with null `CustomerInvoiceSentAt`) and can be cleaned up separately if desired.

## Direct DB Operations Used

- No UI path was found to mark a draft estimate as lost during the audit. | Table/Area: Estimate | Fields/Update: Status = "Lost" | IDs: 4
