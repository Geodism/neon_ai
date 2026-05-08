from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import re
from typing import Any

from neon_ai.services.automation_memory_service import (
    AutomationMemoryRecord,
    create_memory,
    list_memory,
)


MEMORY_TYPE_OBLIGATION_BEHAVIOR_PARAMETER = "obligation_behavior_parameter"

ALLOWED_PARAMETER_KEYS = {
    "due_after_days",
    "default_priority",
    "default_owner_role",
    "default_snooze_days",
    "operator_nudge_enabled",
    "operator_nudge_after_overdue_hours",
    "preferred_action",
    "escalation_after_days",
    "cash_flow_amount_threshold",
    "nudge_subject_template",
    "nudge_body_template",
}

FORBIDDEN_AUTHORITY_KEYS = {
    "auto_apply",
    "auto_apply_enabled",
    "send_authority",
    "email_send_authority",
    "payment_authority",
    "ready_to_pay_authority",
    "mark_paid_authority",
    "receiving_authority",
    "create_payment",
    "create_payment_record",
    "mark_paid",
    "mark_ready_to_pay",
    "receive_goods",
}

FORBIDDEN_ACTION_TERMS = {
    "auto_apply",
    "autosend",
    "auto_send",
    "bulk_send",
    "send_email",
    "send_invoice",
    "send_po",
    "send_rfq",
    "send_payment_reminder",
    "mark_paid",
    "mark_ready_to_pay",
    "ready_to_pay",
    "create_payment",
    "receive_goods",
    "apply_receiving",
    "create_non_po_vendor_invoice",
}

SAFE_TEMPLATE_TOKENS = {
    "Amount",
    "CustomerName",
    "DueSummary",
    "InvoiceNumber",
    "ObligationType",
    "SiteName",
    "SourceID",
    "VendorName",
    "WorkflowType",
}

PRIORITY_ALIASES = {
    "LOW": "Low",
    "LOW_PRIORITY": "Low",
    "MEDIUM": "Medium",
    "NORMAL": "Medium",
    "HIGH": "High",
    "URGENT": "Urgent",
    "CRITICAL": "Urgent",
}

FOUNDATION_PRIORITY_BY_LABEL = {
    "Low": "LOW",
    "Medium": "NORMAL",
    "High": "HIGH",
    "Urgent": "URGENT",
}

ALLOWED_ACTION_CATALOG: dict[str, set[str]] = {
    "CUSTOMER_RESPONSE_REQUIRED": {
        "prepare_estimate_followup_draft",
        "review_customer_reply",
        "mark_followup_satisfied_manual",
    },
    "BILLING_REVIEW": {
        "create_customer_invoice_draft_if_ready",
        "review_customer_billing_status",
        "mark_billing_reviewed_manual",
    },
    "PAYABLE_REVIEW": {
        "review_po_receipt_invoice_match",
        "send_to_accounting_review",
        "mark_reconciliation_reviewed_manual",
    },
    "PAYABLE_EXCEPTION": {
        "resolve_missing_po",
        "dispute_vendor_invoice",
        "mark_not_payable_review_only",
        "send_to_accounting_review",
    },
    "RECEIVING_REQUIRED": {
        "review_staged_receipt",
        "review_receiving_evidence",
    },
    "DOCUMENT_REVIEW": {
        "review_attachment_text",
        "request_readable_document",
    },
    "DOCUMENT_REQUIRED": {
        "review_attachment_text",
        "request_readable_document",
    },
}

PREFERRED_ACTION_SUMMARIES = {
    "prepare_estimate_followup_draft": "Review estimate follow-up context and prepare an approved follow-up draft if appropriate.",
    "review_customer_reply": "Review the customer reply and decide the next safe estimate/customer action.",
    "mark_followup_satisfied_manual": "Confirm follow-up evidence and manually satisfy the obligation if appropriate.",
    "create_customer_invoice_draft_if_ready": "Review customer billing status and create/apply Draft Customer Invoice if appropriate.",
    "review_customer_billing_status": "Review customer billing status and confirm whether invoice drafting is ready.",
    "mark_billing_reviewed_manual": "Confirm billing evidence and manually satisfy the obligation if appropriate.",
    "review_po_receipt_invoice_match": "Reconcile vendor invoice against PO, receipt, and customer billing context.",
    "send_to_accounting_review": "Send to accounting review without creating payable/payment authority.",
    "mark_reconciliation_reviewed_manual": "Confirm reconciliation evidence and manually satisfy the obligation if appropriate.",
    "resolve_missing_po": "Resolve payable exception. No PO, no money. Find/link PO, dispute, or mark not payable.",
    "dispute_vendor_invoice": "Review payable exception and prepare dispute/review path without payable authority.",
    "mark_not_payable_review_only": "Mark the exception reviewed/not payable only through an approved review path.",
    "review_staged_receipt": "Review staged receipt evidence. Receiving authority still requires the approved receiving wrapper.",
    "review_receiving_evidence": "Review packing slip/receipt evidence before any approved receiving action.",
    "review_attachment_text": "Review attachment extraction/OCR issue and supply readable document text if needed.",
    "request_readable_document": "Request or supply readable document text through an approved operator path.",
}

DEFAULT_PARAMETER_SETS: dict[str, dict[str, Any]] = {
    "CUSTOMER_RESPONSE_REQUIRED": {
        "due_after_days": 2,
        "default_priority": "Medium",
        "default_owner_role": "Sales",
        "default_snooze_days": 2,
        "operator_nudge_enabled": True,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "prepare_estimate_followup_draft",
        "escalation_after_days": 2,
        "cash_flow_amount_threshold": 0.0,
        "nudge_subject_template": "Neon estimate follow-up: {CustomerName} needs review",
        "nudge_body_template": "Estimate follow-up obligation for {CustomerName} is due. Review the allowed next action.",
    },
    "BILLING_REVIEW": {
        "due_after_days": 0,
        "default_priority": "High",
        "default_owner_role": "Billing",
        "default_snooze_days": 1,
        "operator_nudge_enabled": True,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "create_customer_invoice_draft_if_ready",
        "escalation_after_days": 1,
        "cash_flow_amount_threshold": 1000.0,
        "nudge_subject_template": "Neon billing review: {CustomerName} may need invoice drafting",
        "nudge_body_template": "Billing review obligation is due for {CustomerName}. Review invoice readiness.",
    },
    "PAYABLE_REVIEW": {
        "due_after_days": 1,
        "default_priority": "High",
        "default_owner_role": "Accounting",
        "default_snooze_days": 1,
        "operator_nudge_enabled": True,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "review_po_receipt_invoice_match",
        "escalation_after_days": 1,
        "cash_flow_amount_threshold": 0.0,
        "nudge_subject_template": "Neon payable review: {VendorName} invoice needs reconciliation",
        "nudge_body_template": "Vendor invoice obligation for {VendorName} needs PO/receipt/invoice review.",
    },
    "PAYABLE_EXCEPTION": {
        "due_after_days": 0,
        "default_priority": "High",
        "default_owner_role": "Accounting",
        "default_snooze_days": 1,
        "operator_nudge_enabled": True,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "resolve_missing_po",
        "escalation_after_days": 0,
        "cash_flow_amount_threshold": 0.0,
        "nudge_subject_template": "Neon payable exception: {VendorName} invoice needs review",
        "nudge_body_template": "Payable exception for {VendorName} needs review. No PO, no money.",
    },
    "RECEIVING_REQUIRED": {
        "due_after_days": 0,
        "default_priority": "Medium",
        "default_owner_role": "Receiving",
        "default_snooze_days": 1,
        "operator_nudge_enabled": False,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "review_staged_receipt",
        "escalation_after_days": 1,
        "cash_flow_amount_threshold": 0.0,
        "nudge_subject_template": "Neon receiving review: {SourceID}",
        "nudge_body_template": "Receiving review is due. Review staged receipt evidence before any approved receiving action.",
    },
    "DOCUMENT_REVIEW": {
        "due_after_days": 1,
        "default_priority": "Medium",
        "default_owner_role": "Office",
        "default_snooze_days": 1,
        "operator_nudge_enabled": False,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "review_attachment_text",
        "escalation_after_days": 2,
        "cash_flow_amount_threshold": 0.0,
        "nudge_subject_template": "Neon document review: {SourceID}",
        "nudge_body_template": "Document review is due. Review attachment text/OCR state.",
    },
    "DOCUMENT_REQUIRED": {
        "due_after_days": 1,
        "default_priority": "Medium",
        "default_owner_role": "Office",
        "default_snooze_days": 1,
        "operator_nudge_enabled": False,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "review_attachment_text",
        "escalation_after_days": 2,
        "cash_flow_amount_threshold": 0.0,
        "nudge_subject_template": "Neon document review: {SourceID}",
        "nudge_body_template": "Document review is due. Review attachment text/OCR state.",
    },
}


@dataclass(frozen=True)
class ObligationBehaviorValidationResult:
    valid: bool
    parameters: dict[str, Any]
    warnings: list[str]
    errors: list[str]
    rejected_keys: list[str]


def get_default_obligation_behavior_parameters(
    obligation_type: str,
    workflow_type: str | None = None,
) -> dict[str, Any]:
    normalized_type = _normalize_obligation_type(obligation_type)
    _ = workflow_type
    defaults = DEFAULT_PARAMETER_SETS.get(normalized_type) or {
        "due_after_days": 1,
        "default_priority": "Medium",
        "default_owner_role": "Operator",
        "default_snooze_days": 1,
        "operator_nudge_enabled": False,
        "operator_nudge_after_overdue_hours": 0,
        "preferred_action": "",
        "escalation_after_days": 2,
        "cash_flow_amount_threshold": 0.0,
        "nudge_subject_template": "Neon obligation review: {ObligationType}",
        "nudge_body_template": "Obligation {ObligationType} needs review.",
    }
    return dict(defaults)


def get_obligation_behavior_parameters(
    obligation_type: str,
    workflow_type: str | None = None,
) -> dict[str, Any]:
    defaults = get_default_obligation_behavior_parameters(obligation_type, workflow_type=workflow_type)
    memories = _matching_parameter_memories(obligation_type, workflow_type=workflow_type)
    merged = dict(defaults)
    for memory in memories:
        content = memory.content_json if isinstance(memory.content_json, dict) else {}
        parameter_set = content.get("parameter_set") if isinstance(content.get("parameter_set"), dict) else {}
        merged = merge_obligation_parameters(merged, parameter_set, obligation_type=obligation_type)
    return merged


def merge_obligation_parameters(
    defaults: dict[str, Any],
    memory_parameters: dict[str, Any] | None,
    *,
    obligation_type: str | None = None,
    workflow_type: str | None = None,
) -> dict[str, Any]:
    merged = dict(defaults or {})
    candidate = dict(memory_parameters or {})
    validation = validate_obligation_behavior_parameters(
        obligation_type or "",
        workflow_type=workflow_type,
        parameter_set=candidate,
        allow_partial=True,
    )
    merged.update(validation.parameters)
    return merged


def validate_obligation_behavior_parameters(
    obligation_type: str,
    *,
    workflow_type: str | None = None,
    parameter_set: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    allow_partial: bool = False,
) -> ObligationBehaviorValidationResult:
    normalized_type = _normalize_obligation_type(
        obligation_type or (payload or {}).get("obligation_type") or ""
    )
    known_types = _known_obligation_types()
    errors: list[str] = []
    warnings: list[str] = []
    rejected_keys: list[str] = []
    normalized: dict[str, Any] = {}

    if not normalized_type or normalized_type not in known_types:
        errors.append(f"Unknown obligation_type: {normalized_type or '<missing>'}")

    candidate = parameter_set
    if candidate is None and isinstance(payload, dict):
        candidate = payload.get("parameter_set") if isinstance(payload.get("parameter_set"), dict) else {}
    candidate = dict(candidate or {})

    for key, value in candidate.items():
        normalized_key = str(key or "").strip()
        lower_key = normalized_key.lower()
        if lower_key in FORBIDDEN_AUTHORITY_KEYS:
            errors.append(f"Forbidden authority parameter rejected: {normalized_key}")
            rejected_keys.append(normalized_key)
            continue
        if normalized_key not in ALLOWED_PARAMETER_KEYS:
            warnings.append(f"Unknown parameter ignored: {normalized_key}")
            rejected_keys.append(normalized_key)
            continue
        if normalized_key in {"due_after_days", "default_snooze_days", "escalation_after_days"}:
            number = _coerce_non_negative_float(value)
            if number is None or number > 365:
                errors.append(f"{normalized_key} must be a non-negative number no greater than 365.")
                rejected_keys.append(normalized_key)
                continue
            normalized[normalized_key] = int(number) if float(number).is_integer() else number
            continue
        if normalized_key == "operator_nudge_after_overdue_hours":
            number = _coerce_non_negative_float(value)
            if number is None or number > 8760:
                errors.append("operator_nudge_after_overdue_hours must be a non-negative number no greater than 8760.")
                rejected_keys.append(normalized_key)
                continue
            normalized[normalized_key] = int(number) if float(number).is_integer() else number
            continue
        if normalized_key == "cash_flow_amount_threshold":
            number = _coerce_non_negative_float(value)
            if number is None or number > 100000000:
                errors.append("cash_flow_amount_threshold must be a reasonable non-negative number.")
                rejected_keys.append(normalized_key)
                continue
            normalized[normalized_key] = float(number)
            continue
        if normalized_key == "default_priority":
            priority = _normalize_priority_label(value)
            if priority is None:
                errors.append("default_priority must be Low, Medium, High, or Urgent.")
                rejected_keys.append(normalized_key)
                continue
            normalized[normalized_key] = priority
            continue
        if normalized_key == "operator_nudge_enabled":
            if not isinstance(value, bool):
                errors.append("operator_nudge_enabled must be boolean.")
                rejected_keys.append(normalized_key)
                continue
            normalized[normalized_key] = bool(value)
            continue
        if normalized_key == "preferred_action":
            action = _normalize_action(value)
            if not action:
                normalized[normalized_key] = ""
                continue
            if _action_has_forbidden_authority(action):
                errors.append(f"preferred_action is forbidden authority or unsupported mutation: {action}")
                rejected_keys.append(normalized_key)
                continue
            allowed = get_allowed_actions_for_obligation_type(normalized_type)
            if allowed and action not in allowed:
                errors.append(f"preferred_action is not allowed for {normalized_type}: {action}")
                rejected_keys.append(normalized_key)
                continue
            normalized[normalized_key] = action
            continue
        if normalized_key in {"default_owner_role", "nudge_subject_template", "nudge_body_template"}:
            text = str(value or "").strip()
            if len(text) > 2000:
                errors.append(f"{normalized_key} is too long.")
                rejected_keys.append(normalized_key)
                continue
            if normalized_key.startswith("nudge_"):
                bad_tokens = _unknown_template_tokens(text)
                if bad_tokens:
                    errors.append(f"{normalized_key} contains unsupported tokens: {', '.join(bad_tokens)}")
                    rejected_keys.append(normalized_key)
                    continue
            normalized[normalized_key] = text

    if not allow_partial:
        full = get_default_obligation_behavior_parameters(normalized_type, workflow_type=workflow_type)
        full.update(normalized)
        normalized = {key: full[key] for key in ALLOWED_PARAMETER_KEYS if key in full}

    return ObligationBehaviorValidationResult(
        valid=not errors,
        parameters=normalized,
        warnings=warnings,
        errors=errors,
        rejected_keys=rejected_keys,
    )


def save_obligation_behavior_parameter_memory(
    *,
    obligation_type: str,
    workflow_type: str | None = None,
    parameter_set: dict[str, Any],
    operator_note: str | None = None,
    confidence: float | Decimal | int | str | None = 1.0,
    created_by: str | None = "operator",
    source: str | None = "operator_teach",
) -> AutomationMemoryRecord:
    normalized_type = _normalize_obligation_type(obligation_type)
    validation = validate_obligation_behavior_parameters(
        normalized_type,
        workflow_type=workflow_type,
        parameter_set=parameter_set,
        allow_partial=True,
    )
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    normalized_workflow = _normalize_workflow_type(workflow_type)
    payload = {
        "memory_type": MEMORY_TYPE_OBLIGATION_BEHAVIOR_PARAMETER,
        "obligation_type": normalized_type,
        "workflow_type": normalized_workflow,
        "parameter_set": validation.parameters,
        "operator_note": operator_note,
        "confidence": _coerce_float(confidence, default=1.0),
        "validation_warnings": validation.warnings,
        "authority_boundary": "parameters_only_no_new_authority",
    }
    return create_memory(
        scope_type="WorkflowObligationBehavior",
        scope_id=f"{normalized_type}:{normalized_workflow or '*'}",
        memory_type=MEMORY_TYPE_OBLIGATION_BEHAVIOR_PARAMETER,
        content_json=payload,
        content_text=_parameter_memory_text(payload),
        source=source,
        confidence=confidence,
        created_by=created_by,
        is_active=True,
    )


def build_obligation_behavior_context(obligation: Any) -> dict[str, Any]:
    obligation_type = _normalize_obligation_type(getattr(obligation, "obligation_type", "") or "")
    workflow_type = _normalize_workflow_type(getattr(obligation, "workflow_type", "") or "")
    parameters = get_obligation_behavior_parameters(obligation_type, workflow_type=workflow_type)
    preferred_action = _normalize_action(parameters.get("preferred_action"))
    allowed_actions = sorted(get_allowed_actions_for_obligation_type(obligation_type))
    return {
        "memory_type": MEMORY_TYPE_OBLIGATION_BEHAVIOR_PARAMETER,
        "obligation_type": obligation_type,
        "workflow_type": workflow_type,
        "parameters": parameters,
        "allowed_actions": allowed_actions,
        "preferred_action": preferred_action,
        "preferred_action_summary": summarize_preferred_action(obligation_type, preferred_action),
        "authority_allowed": False,
        "boundary": "behavior_hard_coded_parameters_memory_configurable",
    }


def get_allowed_actions_for_obligation_type(obligation_type: str) -> set[str]:
    return set(ALLOWED_ACTION_CATALOG.get(_normalize_obligation_type(obligation_type), set()))


def summarize_preferred_action(obligation_type: str, preferred_action: str | None) -> str:
    action = _normalize_action(preferred_action)
    if not action:
        return ""
    if action == "resolve_missing_po":
        return "Resolve payable exception. No PO, no money. Find/link PO, dispute, or mark not payable."
    return PREFERRED_ACTION_SUMMARIES.get(action, "")


def foundation_priority_from_parameter(priority: Any, *, default: str = "NORMAL") -> str:
    normalized = _normalize_priority_label(priority)
    if normalized is None:
        return default
    return FOUNDATION_PRIORITY_BY_LABEL.get(normalized, default)


def calculate_due_from_behavior(
    base_datetime: datetime | date | str | None,
    parameters: dict[str, Any] | None,
    *,
    fallback_due: datetime | date | str | None = None,
) -> datetime | None:
    base = _coerce_datetime(base_datetime)
    fallback = _coerce_datetime(fallback_due)
    if base is None:
        return fallback
    due_after_days = _coerce_non_negative_float((parameters or {}).get("due_after_days"))
    if due_after_days is None:
        return fallback or base
    return base + timedelta(days=float(due_after_days))


def validation_result_to_dict(result: ObligationBehaviorValidationResult) -> dict[str, Any]:
    return asdict(result)


def _matching_parameter_memories(
    obligation_type: str,
    *,
    workflow_type: str | None,
) -> list[AutomationMemoryRecord]:
    normalized_type = _normalize_obligation_type(obligation_type)
    normalized_workflow = _normalize_workflow_type(workflow_type)
    memories = list_memory(
        limit=500,
        active_only=True,
        memory_type=MEMORY_TYPE_OBLIGATION_BEHAVIOR_PARAMETER,
    )
    matches: list[AutomationMemoryRecord] = []
    for memory in memories:
        content = memory.content_json if isinstance(memory.content_json, dict) else {}
        if _normalize_obligation_type(content.get("obligation_type") or "") != normalized_type:
            continue
        memory_workflow = _normalize_workflow_type(content.get("workflow_type") or "")
        if memory_workflow and normalized_workflow and memory_workflow != normalized_workflow:
            continue
        if memory_workflow and not normalized_workflow:
            continue
        matches.append(memory)
    return sorted(
        matches,
        key=lambda memory: (
            1 if _normalize_workflow_type((memory.content_json or {}).get("workflow_type") if isinstance(memory.content_json, dict) else "") else 0,
            memory.created_at or datetime.min.replace(tzinfo=timezone.utc),
            memory.automation_memory_id,
        ),
    )


def _known_obligation_types() -> set[str]:
    try:
        from neon_ai.services.workflow_obligation_service import FOUNDATION_OBLIGATION_TYPES

        return set(FOUNDATION_OBLIGATION_TYPES)
    except Exception:
        return set(DEFAULT_PARAMETER_SETS) | set(ALLOWED_ACTION_CATALOG)


def _normalize_obligation_type(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_workflow_type(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_action(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_priority_label(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return PRIORITY_ALIASES.get(normalized)


def _coerce_non_negative_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _action_has_forbidden_authority(action: str) -> bool:
    normalized = _normalize_action(action)
    return any(term in normalized for term in FORBIDDEN_ACTION_TERMS)


def _unknown_template_tokens(template: str) -> list[str]:
    tokens = re.findall(r"{([^{}]+)}", str(template or ""))
    return sorted({token for token in tokens if token not in SAFE_TEMPLATE_TOKENS})


def _parameter_memory_text(payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "memory_type": payload.get("memory_type"),
            "obligation_type": payload.get("obligation_type"),
            "workflow_type": payload.get("workflow_type"),
            "operator_note": payload.get("operator_note"),
            "parameter_keys": sorted((payload.get("parameter_set") or {}).keys()),
        },
        sort_keys=True,
    )

