from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from neon_ai.database.purchases import (
    get_po_export_data,
    set_purchase_order_expected_arrival,
)
from neon_ai.services.automation_memory_service import create_memory, list_memory_for_scope


TERMINAL_PO_STATUSES = {"retired", "fullyreceived", "closed", "cancelled", "canceled"}
OBSERVATION_MEMORY_TYPE = "po_eta_status_observation"
OBSERVATION_MEMORY_SOURCE = "proposal_apply"


@dataclass(frozen=True)
class POEtaStatusApplyResult:
    created_observation_id: int | None
    used_existing_observation_id: int | None
    updated_purchase_order_id: int
    eta_date: str | None
    normalized_intent: str
    status: str
    warnings: list[str]
    source_proposal_id: int
    applied_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_observation_id": self.created_observation_id,
            "used_existing_observation_id": self.used_existing_observation_id,
            "updated_purchase_order_id": self.updated_purchase_order_id,
            "eta_date": self.eta_date,
            "normalized_intent": self.normalized_intent,
            "status": self.status,
            "warnings": list(self.warnings),
            "source_proposal_id": self.source_proposal_id,
            "applied_by": self.applied_by,
        }


def apply_po_eta_status_observation(
    *,
    proposal_id: int,
    proposed_change_json: dict[str, Any],
    evidence_json: dict[str, Any],
    target_type: str,
    target_id: int | None,
    applied_by: str,
) -> POEtaStatusApplyResult:
    normalized_target_type = str(target_type or "").strip()
    if normalized_target_type != "PurchaseOrder":
        raise ValueError("PO ETA/status apply currently supports PurchaseOrder-targeted proposals only.")

    po_id = _coerce_int(target_id)
    if po_id is None:
        raise ValueError("PO ETA/status apply requires a matched PurchaseOrder target.")

    po_header = get_po_export_data(po_id)
    if not po_header:
        raise ValueError(f"PurchaseOrder #{po_id} could not be found.")

    current_status = str(po_header.get("Status") or "").strip()
    if current_status.casefold() in TERMINAL_PO_STATUSES:
        raise ValueError(
            f"PurchaseOrder #{po_id} is in terminal status '{current_status}' and is not eligible for ETA/status apply."
        )

    existing_memory = _find_existing_observation(po_id=po_id, proposal_id=proposal_id)
    if existing_memory is not None:
        return POEtaStatusApplyResult(
            created_observation_id=None,
            used_existing_observation_id=existing_memory.automation_memory_id,
            updated_purchase_order_id=po_id,
            eta_date=_normalized_eta_date(proposed_change_json.get("eta_date")),
            normalized_intent=str(proposed_change_json.get("normalized_intent") or "").strip().upper(),
            status="Recorded",
            warnings=["Existing PO ETA/status observation reused for this proposal."],
            source_proposal_id=proposal_id,
            applied_by=applied_by,
        )

    warnings: list[str] = []
    normalized_intent = str(proposed_change_json.get("normalized_intent") or "").strip().upper()
    normalized_eta_date = _normalized_eta_date(proposed_change_json.get("eta_date"))
    summary_text = str(proposed_change_json.get("vendor_message_summary") or "").strip()
    note_text = _build_planning_note(
        normalized_intent=normalized_intent,
        eta_date=normalized_eta_date,
        parts_ready=_coerce_bool(proposed_change_json.get("parts_ready")),
        backorder_hint=str(proposed_change_json.get("backorder_hint") or "").strip(),
        pickup_note=str(proposed_change_json.get("pickup_note") or "").strip(),
        vendor_message_summary=summary_text,
    )

    current_eta_value = po_header.get("ExpectedArrivalDate")
    current_eta = current_eta_value.isoformat() if hasattr(current_eta_value, "isoformat") else None
    current_note = str(po_header.get("ExpectedArrivalNote") or "").strip()
    desired_eta = normalized_eta_date or current_eta
    desired_note = _merge_expected_arrival_note(current_note, note_text)

    if not normalized_eta_date:
        warnings.append("No ETA date was present; recorded note-only observation.")
    if desired_eta == current_eta and desired_note == current_note:
        warnings.append("PurchaseOrder planning fields were unchanged; observation was recorded only in AutomationMemory.")
    else:
        set_purchase_order_expected_arrival(
            po_id,
            expected_date=_iso_date_or_none(desired_eta),
            expected_note=desired_note,
        )

    memory = create_memory(
        scope_type="PurchaseOrder",
        scope_id=po_id,
        memory_type=OBSERVATION_MEMORY_TYPE,
        content_json={
            "source_proposal_id": int(proposal_id),
            "purchase_order_id": int(po_id),
            "normalized_intent": normalized_intent,
            "eta_date": normalized_eta_date,
            "parts_ready": _coerce_bool(proposed_change_json.get("parts_ready")),
            "backorder_hint": str(proposed_change_json.get("backorder_hint") or "").strip() or None,
            "pickup_note": str(proposed_change_json.get("pickup_note") or "").strip() or None,
            "vendor_message_summary": summary_text or None,
            "sender": evidence_json.get("sender"),
            "subject": evidence_json.get("subject"),
            "source_inbound_message_id": _coerce_int(evidence_json.get("inbound_message_id")),
            "matched_po_evidence": evidence_json.get("matched_po_evidence"),
            "extracted_po_number": evidence_json.get("extracted_po_number"),
            "matched_snippets": evidence_json.get("matched_snippets"),
            "uncertainty_notes": evidence_json.get("uncertainty_notes"),
        },
        content_text=note_text or summary_text or f"PO ETA/status observation for PO #{po_id}.",
        source=OBSERVATION_MEMORY_SOURCE,
        confidence=proposed_change_json.get("confidence"),
        created_by=applied_by,
        is_active=True,
    )

    return POEtaStatusApplyResult(
        created_observation_id=memory.automation_memory_id,
        used_existing_observation_id=None,
        updated_purchase_order_id=po_id,
        eta_date=normalized_eta_date,
        normalized_intent=normalized_intent,
        status="Recorded",
        warnings=warnings,
        source_proposal_id=proposal_id,
        applied_by=applied_by,
    )


def _find_existing_observation(*, po_id: int, proposal_id: int):
    memories = list_memory_for_scope("PurchaseOrder", po_id, active_only=False, limit=200)
    for memory in memories:
        if str(memory.memory_type or "").strip() != OBSERVATION_MEMORY_TYPE:
            continue
        content = memory.content_json if isinstance(memory.content_json, dict) else {}
        if _coerce_int(content.get("source_proposal_id")) == int(proposal_id):
            return memory
    return None


def _build_planning_note(
    *,
    normalized_intent: str,
    eta_date: str | None,
    parts_ready: bool | None,
    backorder_hint: str,
    pickup_note: str,
    vendor_message_summary: str,
) -> str:
    parts: list[str] = []
    if normalized_intent:
        parts.append(normalized_intent.replace("_", " ").title())
    if eta_date:
        parts.append(f"ETA {eta_date}")
    if parts_ready:
        parts.append("Parts ready")
    if backorder_hint:
        parts.append(backorder_hint)
    if pickup_note:
        parts.append(pickup_note)
    if vendor_message_summary:
        parts.append(vendor_message_summary)
    combined = " | ".join(str(item).strip() for item in parts if str(item or "").strip())
    return combined[:255]


def _merge_expected_arrival_note(current_note: str, new_note: str) -> str | None:
    current = str(current_note or "").strip()
    fresh = str(new_note or "").strip()
    if not current and not fresh:
        return None
    if not current:
        return fresh[:255]
    if not fresh:
        return current[:255]
    if fresh.casefold() in current.casefold():
        return current[:255]
    merged = f"{current} | {fresh}"
    return merged[:255]


def _normalized_eta_date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _iso_date_or_none(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None
