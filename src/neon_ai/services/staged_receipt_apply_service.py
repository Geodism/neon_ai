from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from neon_ai.database.purchases import (
    get_po_export_data,
    get_po_items_with_receiving_match_context,
    get_purchase_order_receipt_choices,
    get_purchase_order_receipt_quantities,
    log_material_receipt_batch,
)


ALLOWED_MATCH_OUTCOMES = {
    "MATCHED_PO_LINE_EXACT",
    "MATCHED_VENDOR_QUOTE_LINE",
    "MATCHED_PO_CONTEXT_NO_QUOTE",
    "MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT",
    "OPERATOR_CONFIRMED_MATCH",
}
BLOCKED_MATCH_OUTCOMES = {
    "MATERIAL_CATALOGUE_ONLY_MATCH",
    "VENDOR_QUOTE_PACKING_SLIP_MISMATCH",
    "PO_PACKING_SLIP_MISMATCH",
    "AMBIGUOUS_LINE_MATCH",
    "NO_MATCH",
}
TERMINAL_PO_STATUSES = {"retired", "fullyreceived", "closed", "cancelled", "canceled"}
PO_NUMBER_AND_QUANTITY_MIN_CONFIDENCE = 0.85


@dataclass(frozen=True)
class StagedReceiptApplyResult:
    created_receipt_id: int | None
    used_existing_receipt_id: int | None
    updated_purchase_order_id: int
    purchase_order_status: str | None
    packing_slip_number: str
    receive_date: str
    received_item_count: int
    warnings: list[str]
    source_proposal_id: int
    applied_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_receipt_id": self.created_receipt_id,
            "used_existing_receipt_id": self.used_existing_receipt_id,
            "updated_purchase_order_id": self.updated_purchase_order_id,
            "purchase_order_status": self.purchase_order_status,
            "packing_slip_number": self.packing_slip_number,
            "receive_date": self.receive_date,
            "received_item_count": self.received_item_count,
            "warnings": list(self.warnings),
            "source_proposal_id": self.source_proposal_id,
            "applied_by": self.applied_by,
        }


def apply_staged_receipt_observation(
    *,
    proposal_id: int,
    proposed_change_json: dict[str, Any],
    evidence_json: dict[str, Any],
    target_type: str,
    target_id: int | None,
    applied_by: str,
) -> StagedReceiptApplyResult:
    normalized_target_type = str(target_type or "").strip()
    if normalized_target_type != "PurchaseOrder":
        raise ValueError("Staged receipt apply currently supports PurchaseOrder-targeted proposals only.")

    po_id = _coerce_int(target_id) or _coerce_int(proposed_change_json.get("purchase_order_id"))
    if po_id is None:
        raise ValueError("Staged receipt apply requires a matched PurchaseOrder target.")

    po_header = get_po_export_data(po_id)
    if not po_header:
        raise ValueError(f"PurchaseOrder #{po_id} could not be found.")

    current_status = str(po_header.get("Status") or "").strip()
    if current_status.casefold() in TERMINAL_PO_STATUSES:
        raise ValueError(
            f"PurchaseOrder #{po_id} is in terminal status '{current_status}' and is not eligible for staged receipt apply."
        )

    document_ref = _normalize_document_ref(
        proposed_change_json.get("packing_slip_number"),
        fallback=proposed_change_json.get("shipment_reference"),
    )
    if not document_ref:
        raise ValueError(
            "Staged receipt apply requires a packing slip number or shipment reference for duplicate-safe receipt creation."
        )

    receive_date = _normalize_receive_date(proposed_change_json.get("delivery_date"))
    if not receive_date:
        raise ValueError("Staged receipt apply requires an ISO delivery_date / receive date.")

    po_rows = [dict(row) for row in get_po_items_with_receiving_match_context(po_id) or []]
    if not po_rows:
        raise ValueError(f"PurchaseOrder #{po_id} has no receivable line rows.")
    po_row_map = {int(row.get("POItemID")): row for row in po_rows if _coerce_int(row.get("POItemID")) is not None}

    proposed_lines = proposed_change_json.get("line_candidates")
    if not isinstance(proposed_lines, list) or not proposed_lines:
        raise ValueError("Staged receipt apply requires one or more validated line_candidates.")

    proposed_quantities = _aggregate_receipt_lines(
        proposed_lines,
        po_row_map,
        enforce_remaining=False,
    )

    existing_receipt = _find_existing_receipt_by_ref(po_id, document_ref)
    if existing_receipt is not None:
        existing_quantities = get_purchase_order_receipt_quantities(po_id, existing_receipt["key"])
        proposed_quantity_map = {item["po_item_id"]: float(item["received_qty"]) for item in proposed_quantities}
        if _quantities_match(existing_quantities, proposed_quantity_map):
            refreshed_po = get_po_export_data(po_id) or po_header
            return StagedReceiptApplyResult(
                created_receipt_id=None,
                used_existing_receipt_id=existing_receipt["receipt_id"],
                updated_purchase_order_id=po_id,
                purchase_order_status=str(refreshed_po.get("Status") or "").strip() or None,
                packing_slip_number=document_ref,
                receive_date=receive_date,
                received_item_count=len(proposed_quantities),
                warnings=["Existing receipt with matching document reference and quantities was reused."],
                source_proposal_id=proposal_id,
                applied_by=applied_by,
            )
        if existing_receipt["source"] == "legacy":
            raise ValueError(
                "This packing slip reference already exists only as legacy line history; manual receiving review is required before automation apply."
            )
        raise ValueError(
            "This packing slip already exists on the PurchaseOrder with different received quantities; automation apply is blocked."
        )

    aggregated_items = _aggregate_receipt_lines(
        proposed_lines,
        po_row_map,
        enforce_remaining=True,
    )

    items_arriving = [(int(item["po_item_id"]), float(item["received_qty"])) for item in aggregated_items]
    log_material_receipt_batch(document_ref, receive_date, items_arriving)

    created_receipt = _find_existing_receipt_by_ref(po_id, document_ref)
    if created_receipt is None or created_receipt["receipt_id"] is None:
        raise RuntimeError("Receipt apply completed but no receipt header could be resolved afterward.")

    refreshed_po = get_po_export_data(po_id) or po_header
    return StagedReceiptApplyResult(
        created_receipt_id=created_receipt["receipt_id"],
        used_existing_receipt_id=None,
        updated_purchase_order_id=po_id,
        purchase_order_status=str(refreshed_po.get("Status") or "").strip() or None,
        packing_slip_number=document_ref,
        receive_date=receive_date,
        received_item_count=len(aggregated_items),
        warnings=[],
        source_proposal_id=proposal_id,
        applied_by=applied_by,
    )


def _aggregate_receipt_lines(
    line_candidates: list[dict[str, Any]],
    po_row_map: dict[int, dict[str, Any]],
    *,
    enforce_remaining: bool,
) -> list[dict[str, Any]]:
    aggregated: dict[int, dict[str, Any]] = {}
    for index, item in enumerate(line_candidates):
        if not isinstance(item, dict):
            raise ValueError(f"line_candidates[{index}] must be an object.")
        po_item_id = _coerce_int(item.get("purchase_order_item_id"))
        if po_item_id is None or po_item_id not in po_row_map:
            raise ValueError(f"line_candidates[{index}] references an invalid PurchaseOrderItem.")

        match_outcome = str(item.get("match_outcome") or "").strip()
        if match_outcome in BLOCKED_MATCH_OUTCOMES or match_outcome not in ALLOWED_MATCH_OUTCOMES:
            raise ValueError(
                f"line_candidates[{index}] has blocked or unsupported match_outcome '{match_outcome}'."
            )
        if bool(item.get("operator_resolution_required")):
            raise ValueError(f"line_candidates[{index}] still requires operator resolution and cannot be applied.")

        qty = _coerce_float(item.get("received_qty_candidate"))
        if qty is None or qty <= 0:
            raise ValueError(f"line_candidates[{index}] must have a positive received_qty_candidate.")

        confidence = _coerce_float(item.get("confidence"))
        if (
            match_outcome == "MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT"
            and (confidence is None or confidence < PO_NUMBER_AND_QUANTITY_MIN_CONFIDENCE)
        ):
            raise ValueError(
                "PO number and quantity context matches require higher confidence before receipt apply is allowed."
            )

        row = po_row_map[po_item_id]
        remaining = float(row.get("Remaining") or 0)
        if enforce_remaining and qty > remaining:
            raise ValueError(
                f"PO item #{po_item_id} would exceed remaining quantity ({qty} > {remaining}); automation apply blocks overage."
            )

        existing = aggregated.get(po_item_id)
        if existing is None:
            aggregated[po_item_id] = {
                "po_item_id": po_item_id,
                "received_qty": qty,
                "match_outcome": match_outcome,
                "confidence": confidence,
            }
            continue

        if existing["match_outcome"] != match_outcome:
            raise ValueError(
                f"PO item #{po_item_id} appears more than once with conflicting match outcomes; manual review is required."
            )
        combined_qty = float(existing["received_qty"]) + qty
        if enforce_remaining and combined_qty > remaining:
            raise ValueError(
                f"Combined staged receipt quantity for PO item #{po_item_id} would exceed remaining quantity; automation apply blocks overage."
            )
        existing["received_qty"] = combined_qty
        if confidence is not None:
            prior_conf = _coerce_float(existing.get("confidence"), default=confidence) or confidence
            existing["confidence"] = min(prior_conf, confidence)

    return list(aggregated.values())


def _find_existing_receipt_by_ref(po_id: int, document_ref: str) -> dict[str, Any] | None:
    normalized_ref = str(document_ref or "").strip().lower()
    if not normalized_ref:
        return None
    for choice in get_purchase_order_receipt_choices(int(po_id)) or []:
        ref = str(choice.get("DocumentRef") or "").strip().lower()
        if ref != normalized_ref:
            continue
        key = str(choice.get("Key") or "").strip()
        receipt_id = None
        source = "legacy"
        if key.startswith("receipt:"):
            source = "ledger"
            receipt_id = _coerce_int(choice.get("ReceiptID")) or _coerce_int(key.split(":", 1)[1])
        return {
            "key": key,
            "receipt_id": receipt_id,
            "source": source,
        }
    return None


def _quantities_match(existing: dict[int, float], proposed: dict[int, float]) -> bool:
    if set(existing.keys()) != set(proposed.keys()):
        return False
    for po_item_id, qty in proposed.items():
        if abs(float(existing.get(po_item_id, 0.0)) - float(qty)) > 0.0001:
            return False
    return True


def _normalize_document_ref(value: Any, *, fallback: Any = None) -> str:
    primary = str(value or "").strip()
    if primary:
        return primary[:255]
    secondary = str(fallback or "").strip()
    return secondary[:255]


def _normalize_receive_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any, *, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
