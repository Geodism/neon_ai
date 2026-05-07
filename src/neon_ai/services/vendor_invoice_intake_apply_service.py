from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neon_ai.database.purchases import get_po_export_data
from neon_ai.database.vendor_invoices import create_vendor_invoice_intake_draft


ALLOWED_MATCH_OUTCOMES = {
    "MATCHED_PO_AND_RECEIPT_CONTEXT",
    "MATCHED_PO_ONLY",
}


@dataclass(frozen=True)
class VendorInvoiceIntakeApplyResult:
    created_vendor_invoice_id: int | None
    reused_existing_vendor_invoice_id: int | None
    used_existing_vendor_id: int | None
    matched_purchase_order_id: int
    warnings: list[str]
    source_proposal_id: int
    source_inbound_message_id: int | None
    applied_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_vendor_invoice_id": self.created_vendor_invoice_id,
            "reused_existing_vendor_invoice_id": self.reused_existing_vendor_invoice_id,
            "used_existing_vendor_id": self.used_existing_vendor_id,
            "matched_purchase_order_id": self.matched_purchase_order_id,
            "warnings": list(self.warnings),
            "source_proposal_id": self.source_proposal_id,
            "source_inbound_message_id": self.source_inbound_message_id,
            "applied_by": self.applied_by,
        }


def apply_vendor_invoice_intake_draft(
    *,
    proposal_id: int,
    proposed_change_json: dict[str, Any],
    evidence_json: dict[str, Any],
    target_type: str,
    target_id: int | None,
    applied_by: str,
) -> VendorInvoiceIntakeApplyResult:
    normalized_target_type = str(target_type or "").strip()
    if normalized_target_type != "PurchaseOrder":
        raise ValueError("Vendor invoice draft apply currently supports PurchaseOrder-targeted proposals only.")

    if target_id is None:
        raise ValueError("Vendor invoice draft apply requires a matched PurchaseOrder target.")

    match_outcome = str(proposed_change_json.get("match_outcome") or "").strip()
    if match_outcome not in ALLOWED_MATCH_OUTCOMES:
        raise ValueError(
            "Vendor invoice draft apply is blocked unless the approved proposal is PO-backed "
            "with MATCHED_PO_ONLY or MATCHED_PO_AND_RECEIPT_CONTEXT."
        )

    invoice_number = str(proposed_change_json.get("invoice_number") or "").strip()
    vendor_name = str(proposed_change_json.get("vendor_name") or "").strip()
    if not invoice_number:
        raise ValueError("Vendor invoice draft apply requires invoice_number.")
    if not vendor_name:
        raise ValueError("Vendor invoice draft apply requires vendor_name.")

    po_header = get_po_export_data(int(target_id))
    if not po_header:
        raise ValueError(f"PurchaseOrder #{target_id} could not be found.")

    warnings: list[str] = []
    extracted_po_number = str(proposed_change_json.get("po_number") or "").strip()
    if extracted_po_number and extracted_po_number.isdigit() and int(extracted_po_number) != int(target_id):
        raise ValueError(
            f"Approved proposal target PurchaseOrder #{target_id} conflicts with extracted po_number {extracted_po_number}."
        )
    if str(po_header.get("Status") or "").strip().lower() in {"retired", "cancelled", "closed"}:
        raise ValueError(f"PurchaseOrder #{target_id} is not in a safe state for draft vendor invoice intake.")

    po_vendor_name = str(po_header.get("VendorName") or "").strip()
    if po_vendor_name and vendor_name and po_vendor_name.casefold() != vendor_name.casefold():
        warnings.append(
            f"Extracted vendor '{vendor_name}' differs from PO vendor '{po_vendor_name}'. PO context was retained."
        )

    intake_result = create_vendor_invoice_intake_draft(
        po_id=int(target_id),
        invoice_number=invoice_number,
        invoice_date=str(proposed_change_json.get("invoice_date") or "").strip(),
        due_date=str(proposed_change_json.get("due_date") or "").strip(),
        total_amount=float(proposed_change_json.get("total_amount") or 0.0),
        description=(
            "Vendor invoice draft created from approved automation intake proposal "
            f"#{proposal_id}."
        ),
        vendor_name=vendor_name,
        packing_slip_number=str(proposed_change_json.get("packing_slip_number") or "").strip(),
        line_candidates=proposed_change_json.get("line_candidates") if isinstance(proposed_change_json.get("line_candidates"), list) else [],
        source_proposal_id=int(proposal_id),
        source_inbound_message_id=_coerce_int(evidence_json.get("inbound_message_id")),
        applied_by=applied_by,
    )
    warnings.extend(str(item) for item in (intake_result.get("warnings") or []) if str(item or "").strip())

    invoice_row = intake_result.get("invoice") or {}
    invoice_id = _coerce_int(invoice_row.get("VendorInvoiceID"))
    reused = bool(intake_result.get("draft_reused"))
    vendor_id = _coerce_int(po_header.get("VendorID"))
    return VendorInvoiceIntakeApplyResult(
        created_vendor_invoice_id=None if reused else invoice_id,
        reused_existing_vendor_invoice_id=invoice_id if reused else None,
        used_existing_vendor_id=vendor_id,
        matched_purchase_order_id=int(target_id),
        warnings=warnings,
        source_proposal_id=int(proposal_id),
        source_inbound_message_id=_coerce_int(evidence_json.get("inbound_message_id")),
        applied_by=applied_by,
    )


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
