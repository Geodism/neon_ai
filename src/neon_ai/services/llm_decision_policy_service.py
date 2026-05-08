from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DECISION_LOCAL_ONLY = "local_only"
DECISION_LOCAL_THEN_REMOTE_FAST = "local_then_remote_fast_if_uncertain"
DECISION_REMOTE_FAST_REQUIRED = "remote_fast_required"
DECISION_REMOTE_FAST_THEN_STRONG = "remote_fast_then_strong_if_uncertain"
DECISION_REMOTE_STRONG_REQUIRED = "remote_strong_required"
DECISION_OPERATOR_REVIEW = "operator_review"
DECISION_BLOCKED = "blocked"


@dataclass(frozen=True)
class LLMPolicyDecision:
    decision: str
    first_lane: str
    escalation_lane: str
    strong_allowed: bool
    reason: str
    authority_allowed: bool = False
    final_meaning_source: str = "policy_only"
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = list(self.notes)
        return data


def choose_model_for_task(
    *,
    workflow: str,
    document_type: str | None = None,
    risk_level: str | None = None,
    local_confidence: float | None = None,
    required_fields_missing: bool = False,
    json_valid: bool = True,
    money_related: bool = False,
    receiving_related: bool = False,
    external_send_related: bool = False,
    operator_requested_stronger_model: bool = False,
) -> LLMPolicyDecision:
    workflow_norm = str(workflow or "").strip().lower()
    document_norm = str(document_type or "").strip().lower()
    risk_norm = str(risk_level or "").strip().lower()
    confidence = 0.0 if local_confidence is None else float(local_confidence)
    uncertain_local = (not json_valid) or required_fields_missing or confidence < 0.7
    severe_uncertainty = (not json_valid) or confidence < 0.5
    high_value = risk_norm in {"high", "critical"} or money_related

    if external_send_related:
        return LLMPolicyDecision(
            decision=DECISION_REMOTE_FAST_THEN_STRONG,
            first_lane="remote_fast",
            escalation_lane="remote_strong",
            strong_allowed=True,
            reason="External send drafting may use remote interpretation lanes for draft text only. Send authority remains blocked.",
            authority_allowed=False,
            final_meaning_source="policy_only",
            notes=("Drafting meaning is allowed; send authority is not.",),
        )

    if workflow_norm in {"send_email", "queue_email", "payment", "mark_paid", "ready_to_pay", "ready-to-pay"}:
        return LLMPolicyDecision(
            decision=DECISION_BLOCKED,
            first_lane="none",
            escalation_lane="none",
            strong_allowed=False,
            reason="Model authority is blocked for send/payment/ready-to-pay actions.",
            authority_allowed=False,
            final_meaning_source="blocked",
            notes=("Meaning only, not authority.",),
        )

    if workflow_norm in {"non_po_vendor_invoice", "non_po_vendor_invoice_exception_review"}:
        return LLMPolicyDecision(
            decision=DECISION_OPERATOR_REVIEW,
            first_lane="none",
            escalation_lane="none",
            strong_allowed=False,
            reason="No PO, no money. Non-PO vendor invoice remains operator review only.",
            authority_allowed=False,
            final_meaning_source="operator_review",
            notes=("No payable authority is granted.",),
        )

    if operator_requested_stronger_model:
        return LLMPolicyDecision(
            decision=DECISION_REMOTE_STRONG_REQUIRED,
            first_lane="remote_strong",
            escalation_lane="none",
            strong_allowed=True,
            reason="Operator explicitly requested the strongest remote model lane.",
            authority_allowed=False,
            final_meaning_source="remote_strong_model",
            notes=("Escalated by operator request.",),
        )

    if workflow_norm in {"lead_intake", "customer_scheduling_service_inquiry", "customer_scheduling"}:
        return LLMPolicyDecision(
            decision=DECISION_LOCAL_ONLY,
            first_lane="local",
            escalation_lane="none",
            strong_allowed=False,
            reason="Simple lead or scheduling text is appropriate for the local first-pass lane.",
            authority_allowed=False,
            final_meaning_source="local_model",
        )

    if workflow_norm in {"estimate_acceptance", "estimate_followup_customer_reply", "estimate_reply"}:
        if high_value and severe_uncertainty:
            return LLMPolicyDecision(
                decision=DECISION_REMOTE_STRONG_REQUIRED,
                first_lane="remote_strong",
                escalation_lane="none",
                strong_allowed=True,
                reason="High-value estimate acceptance or revision ambiguity requires the strongest remote lane.",
                authority_allowed=False,
                final_meaning_source="remote_strong_model",
            )
        return LLMPolicyDecision(
            decision=DECISION_LOCAL_THEN_REMOTE_FAST,
            first_lane="local",
            escalation_lane="remote_fast",
            strong_allowed=True,
            reason="Estimate reply interpretation should start local and escalate to remote_fast if uncertain.",
            authority_allowed=False,
            final_meaning_source="local_model" if not uncertain_local else "remote_fast_model",
        )

    if workflow_norm in {"po_eta_parts_ready", "po_eta_status_observation", "po_receiving"}:
        return LLMPolicyDecision(
            decision=DECISION_LOCAL_THEN_REMOTE_FAST,
            first_lane="local",
            escalation_lane="remote_fast",
            strong_allowed=False,
            reason="PO ETA / parts-ready interpretation should start local and escalate to remote_fast if uncertain.",
            authority_allowed=False,
            final_meaning_source="local_model" if not uncertain_local else "remote_fast_model",
        )

    if workflow_norm in {"vendor_invoice_intake", "vendor_invoice_payables", "vendor_invoice_reconciliation"}:
        if high_value and severe_uncertainty:
            return LLMPolicyDecision(
                decision=DECISION_REMOTE_STRONG_REQUIRED,
                first_lane="remote_strong",
                escalation_lane="none",
                strong_allowed=True,
                reason="Vendor invoice totals, PO, or line fields are conflicting or high-value and require the strongest remote lane.",
                authority_allowed=False,
                final_meaning_source="remote_strong_model",
            )
        if money_related or uncertain_local:
            return LLMPolicyDecision(
                decision=DECISION_REMOTE_FAST_REQUIRED,
                first_lane="remote_fast",
                escalation_lane="remote_strong",
                strong_allowed=True,
                reason="Vendor invoice header extraction is money-related and should use remote_fast first, with remote_strong available for conflicts.",
                authority_allowed=False,
                final_meaning_source="remote_fast_model",
            )
        return LLMPolicyDecision(
            decision=DECISION_LOCAL_THEN_REMOTE_FAST,
            first_lane="local",
            escalation_lane="remote_fast",
            strong_allowed=True,
            reason="Vendor invoice interpretation may start local only when the local pass is complete and stable.",
            authority_allowed=False,
            final_meaning_source="local_model",
        )

    if workflow_norm in {"packing_slip_receiving", "staged_receipt_observation"} or receiving_related:
        if severe_uncertainty or document_norm in {"packing_slip", "receipt"}:
            return LLMPolicyDecision(
                decision=DECISION_REMOTE_STRONG_REQUIRED,
                first_lane="remote_strong",
                escalation_lane="none",
                strong_allowed=True,
                reason="Packing slip quantities or PO/receipt match uncertainty requires the strongest remote lane.",
                authority_allowed=False,
                final_meaning_source="remote_strong_model",
            )
        return LLMPolicyDecision(
            decision=DECISION_REMOTE_FAST_THEN_STRONG,
            first_lane="remote_fast",
            escalation_lane="remote_strong",
            strong_allowed=True,
            reason="Packing slip / receiving extraction should use remote_fast and escalate to remote_strong if line extraction stays uncertain.",
            authority_allowed=False,
            final_meaning_source="remote_fast_model",
        )

    if workflow_norm in {"rfq_reply", "quote", "rfq_quote"} or document_norm in {"rfq_quote", "quote"}:
        if severe_uncertainty or risk_norm == "critical":
            return LLMPolicyDecision(
                decision=DECISION_REMOTE_STRONG_REQUIRED,
                first_lane="remote_strong",
                escalation_lane="none",
                strong_allowed=True,
                reason="Multi-line quote pricing and substitutions require the strongest remote lane.",
                authority_allowed=False,
                final_meaning_source="remote_strong_model",
            )
        return LLMPolicyDecision(
            decision=DECISION_REMOTE_FAST_THEN_STRONG,
            first_lane="remote_fast",
            escalation_lane="remote_strong",
            strong_allowed=True,
            reason="RFQ quote extraction should use remote_fast first and remote_strong for line ambiguity.",
            authority_allowed=False,
            final_meaning_source="remote_fast_model",
        )

    if workflow_norm in {"duplicate_invoice_review", "paid_invoice_duplicate_dispute_review", "duplicate_rebill_risk"}:
        if severe_uncertainty:
            return LLMPolicyDecision(
                decision=DECISION_OPERATOR_REVIEW,
                first_lane="none",
                escalation_lane="none",
                strong_allowed=True,
                reason="Duplicate or rebill risk is too inconsistent for model-only escalation and should stay operator-reviewed.",
                authority_allowed=False,
                final_meaning_source="operator_review",
            )
        return LLMPolicyDecision(
            decision=DECISION_REMOTE_STRONG_REQUIRED,
            first_lane="remote_strong",
            escalation_lane="none",
            strong_allowed=True,
            reason="Duplicate or rebill risk, if model-assisted at all, should use the strongest remote lane.",
            authority_allowed=False,
            final_meaning_source="remote_strong_model",
        )

    if not json_valid and workflow_norm not in {"non_po_vendor_invoice", "non_po_vendor_invoice_exception_review"}:
        return LLMPolicyDecision(
            decision=DECISION_LOCAL_THEN_REMOTE_FAST,
            first_lane="local",
            escalation_lane="remote_fast",
            strong_allowed=True,
            reason="Invalid local JSON should escalate to remote_fast unless the workflow is blocked or operator-only.",
            authority_allowed=False,
            final_meaning_source="remote_fast_model",
        )

    if severe_uncertainty:
        return LLMPolicyDecision(
            decision=DECISION_OPERATOR_REVIEW,
            first_lane="none",
            escalation_lane="none",
            strong_allowed=False,
            reason="Critical identifiers are missing or inconsistent enough that the work should return to operator review.",
            authority_allowed=False,
            final_meaning_source="operator_review",
        )

    return LLMPolicyDecision(
        decision=DECISION_LOCAL_ONLY,
        first_lane="local",
        escalation_lane="none",
        strong_allowed=False,
        reason="Default low-risk path uses the local lane first.",
        authority_allowed=False,
        final_meaning_source="local_model",
    )
