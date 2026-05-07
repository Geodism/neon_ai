from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_TOKENS_PER_RUN = 8000
DEFAULT_MAX_EMAILS_PER_RUN = 0
DEFAULT_MAX_ACTIONS_PER_RUN = 5
DEFAULT_ALLOWED_WORKFLOW_SCOPES = (
    "lead_customer_site",
    "estimate",
    "estimate_document",
    "material_call_rfq",
    "receive_quotes",
    "bid_compare",
    "purchase_order",
    "receive_goods",
    "vendor_invoice",
    "customer_invoice",
    "document_control",
    "reporting",
)


@dataclass(frozen=True)
class AutomationPolicyDefinition:
    policy_key: str
    value: Any
    description: str
    enforcement_status: str
    notes: str


@dataclass(frozen=True)
class AutomationPolicyResult:
    allowed: bool
    policy_key: str
    value: Any
    reason: str
    risk_level: str
    requires_approval: bool
    blocked_reason: str | None
    recommended_next_action: str


def get_default_policy_set() -> dict[str, AutomationPolicyDefinition]:
    return {
        "email_send_requires_approval": AutomationPolicyDefinition(
            policy_key="email_send_requires_approval",
            value=True,
            description="Every external email send requires explicit approval.",
            enforcement_status="enforced",
            notes="Level 2 may draft or propose but not send.",
        ),
        "escalation_requires_approval": AutomationPolicyDefinition(
            policy_key="escalation_requires_approval",
            value=True,
            description="Every escalation requires explicit approval.",
            enforcement_status="enforced",
            notes="Escalations stay proposal-gated.",
        ),
        "locked_record_mutation": AutomationPolicyDefinition(
            policy_key="locked_record_mutation",
            value="deny",
            description="Mutation of locked workflow records is denied.",
            enforcement_status="enforced",
            notes="Locked records require a future explicit exception model.",
        ),
        "carry_price_outside_bid_compare": AutomationPolicyDefinition(
            policy_key="carry_price_outside_bid_compare",
            value="deny",
            description="Price carry is denied outside the Bid Compare workflow.",
            enforcement_status="enforced",
            notes="Carry remains route- and workflow-specific.",
        ),
        "mark_paid_without_approval": AutomationPolicyDefinition(
            policy_key="mark_paid_without_approval",
            value="deny",
            description="Mark-paid actions are denied without explicit approval.",
            enforcement_status="enforced",
            notes="No autonomous paid-state changes at Level 2.",
        ),
        "unknown_confidence_action": AutomationPolicyDefinition(
            policy_key="unknown_confidence_action",
            value="question",
            description="Unknown or low-confidence automation outcomes become questions.",
            enforcement_status="enforced",
            notes="Low-confidence routes should ask rather than act.",
        ),
        "max_tokens_per_run": AutomationPolicyDefinition(
            policy_key="max_tokens_per_run",
            value=DEFAULT_MAX_TOKENS_PER_RUN,
            description="Maximum total tokens permitted for one automation run.",
            enforcement_status="advisory_enforced",
            notes="Static default until per-route budgets are introduced.",
        ),
        "max_emails_per_run": AutomationPolicyDefinition(
            policy_key="max_emails_per_run",
            value=DEFAULT_MAX_EMAILS_PER_RUN,
            description="Maximum external emails that an automation run may send.",
            enforcement_status="enforced",
            notes="Zero for current Level 2 slices.",
        ),
        "max_actions_per_run": AutomationPolicyDefinition(
            policy_key="max_actions_per_run",
            value=DEFAULT_MAX_ACTIONS_PER_RUN,
            description="Maximum business actions permitted in one automation run.",
            enforcement_status="advisory_enforced",
            notes="Static low default until per-route limits are introduced.",
        ),
        "allowed_workflow_scopes": AutomationPolicyDefinition(
            policy_key="allowed_workflow_scopes",
            value=DEFAULT_ALLOWED_WORKFLOW_SCOPES,
            description="Workflow scopes currently recognized by the Level 2 architecture.",
            enforcement_status="reference",
            notes="Future route handlers should declare one of these scopes.",
        ),
        "quote_update_requires_approval": AutomationPolicyDefinition(
            policy_key="quote_update_requires_approval",
            value=True,
            description="Quote updates require approval before business mutation.",
            enforcement_status="enforced",
            notes="Use Receive Quotes workflow service path only.",
        ),
        "receive_quotes_update_requires_approval": AutomationPolicyDefinition(
            policy_key="receive_quotes_update_requires_approval",
            value=True,
            description="Receive Quotes updates require approval before applying vendor quote data.",
            enforcement_status="enforced",
            notes="Approval-gated RFQ proposal apply is the current supported path.",
        ),
        "update_catalogue_baseline_requires_approval": AutomationPolicyDefinition(
            policy_key="update_catalogue_baseline_requires_approval",
            value=True,
            description="Catalogue/internal baseline price updates require explicit approval.",
            enforcement_status="enforced",
            notes="Normal quote save/apply must not silently update InternalPrice.",
        ),
        "external_action_requires_approval": AutomationPolicyDefinition(
            policy_key="external_action_requires_approval",
            value=True,
            description="External actions require approval before execution.",
            enforcement_status="enforced",
            notes="Applies to sends, escalations, and future third-party actions.",
        ),
    }


def list_policy_definitions() -> list[AutomationPolicyDefinition]:
    return list(get_default_policy_set().values())


def get_policy_value(policy_key: str, scope: str | None = None) -> Any:
    definition = get_default_policy_set().get(str(policy_key).strip())
    if definition is None:
        raise KeyError(f"Unknown automation policy key: {policy_key}")
    return definition.value


def evaluate_action_policy(
    action_type: str,
    target_type: str | None = None,
    workflow: str | None = None,
    risk_level: str | None = None,
) -> AutomationPolicyResult:
    normalized = str(action_type or "").strip().lower()
    workflow_name = str(workflow or "").strip().lower()
    policy_key = _action_policy_key(normalized)
    value = get_policy_value(policy_key)
    effective_risk = risk_level or _default_risk_for_action(normalized)

    if normalized == "carry_price":
        if workflow_name in {"bid_compare", "bid compare"}:
            return AutomationPolicyResult(
                allowed=False,
                policy_key="carry_price_outside_bid_compare",
                value=get_policy_value("carry_price_outside_bid_compare"),
                reason="Carry price is only eligible through Bid Compare and still requires approval at Level 2.",
                risk_level=effective_risk,
                requires_approval=True,
                blocked_reason="approval_required",
                recommended_next_action="Represent the carry selection as a structured proposal and require approval before using the Bid Compare workflow service path.",
            )
        return AutomationPolicyResult(
            allowed=False,
            policy_key="carry_price_outside_bid_compare",
            value=get_policy_value("carry_price_outside_bid_compare"),
            reason="Carry price is denied outside Bid Compare and remains approval-gated.",
            risk_level=effective_risk,
            requires_approval=True,
            blocked_reason="carry_price_outside_bid_compare",
            recommended_next_action="Create a Bid Compare proposal or route the action through the Bid Compare workflow.",
        )
    if normalized == "mark_paid":
        return AutomationPolicyResult(
            allowed=False,
            policy_key="mark_paid_without_approval",
            value=get_policy_value("mark_paid_without_approval"),
            reason="Mark-paid actions are denied without explicit approval.",
            risk_level=effective_risk,
            requires_approval=True,
            blocked_reason="mark_paid_without_approval",
            recommended_next_action="Escalate to human approval and use the invoice workflow service path.",
        )

    if bool(value):
        return AutomationPolicyResult(
            allowed=False,
            policy_key=policy_key,
            value=value,
            reason=(
                f"Action '{normalized or action_type}'"
                + (f" for '{target_type}'" if target_type else "")
                + " requires approval under the current Level 2 policy set."
            ),
            risk_level=effective_risk,
            requires_approval=True,
            blocked_reason="approval_required",
            recommended_next_action="Represent the mutation as structured JSON and obtain approval before calling the workflow service.",
        )

    return AutomationPolicyResult(
        allowed=True,
        policy_key=policy_key,
        value=value,
        reason=f"Action '{normalized or action_type}' is permitted by current policy defaults.",
        risk_level=effective_risk,
        requires_approval=False,
        blocked_reason=None,
        recommended_next_action="Proceed within the declared workflow and write-boundary rules.",
    )


def evaluate_confidence_policy(confidence: float | int | None, workflow: str | None = None) -> AutomationPolicyResult:
    value = get_policy_value("unknown_confidence_action")
    try:
        score = float(confidence if confidence is not None else 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < 0.75:
        return AutomationPolicyResult(
            allowed=False,
            policy_key="unknown_confidence_action",
            value=value,
            reason="Confidence is below the current Level 2 threshold for autonomous draft progression.",
            risk_level="medium",
            requires_approval=False,
            blocked_reason="low_confidence",
            recommended_next_action="Create an AutomationQuestion and require operator review.",
        )
    return AutomationPolicyResult(
        allowed=True,
        policy_key="unknown_confidence_action",
        value=value,
        reason="Confidence meets the current Level 2 threshold for the requested route.",
        risk_level="low",
        requires_approval=False,
        blocked_reason=None,
        recommended_next_action="Continue within proposal or workflow-service guardrails.",
    )


def evaluate_token_budget(
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost: float | None = None,
) -> AutomationPolicyResult:
    max_tokens = int(get_policy_value("max_tokens_per_run"))
    total = int(input_tokens or 0) + int(output_tokens or 0)
    if total > max_tokens:
        return AutomationPolicyResult(
            allowed=False,
            policy_key="max_tokens_per_run",
            value=max_tokens,
            reason=f"Token budget exceeded: {total} > {max_tokens}.",
            risk_level="medium",
            requires_approval=False,
            blocked_reason="token_budget_exceeded",
            recommended_next_action="Reduce prompt scope, trim context, or split the run into smaller steps.",
        )
    return AutomationPolicyResult(
        allowed=True,
        policy_key="max_tokens_per_run",
        value=max_tokens,
        reason=f"Token budget check passed: {total} <= {max_tokens}.",
        risk_level="low",
        requires_approval=False,
        blocked_reason=None,
        recommended_next_action="Continue within the configured token budget.",
    )


def evaluate_email_policy(send_requested: bool = False, workflow: str | None = None) -> AutomationPolicyResult:
    requires_approval = bool(get_policy_value("email_send_requires_approval"))
    if send_requested:
        return AutomationPolicyResult(
            allowed=False,
            policy_key="email_send_requires_approval",
            value=requires_approval,
            reason="External email send remains approval-gated at Level 2.",
            risk_level="high",
            requires_approval=requires_approval,
            blocked_reason="approval_required",
            recommended_next_action="Create a proposal or draft message for human approval instead of sending.",
        )
    return AutomationPolicyResult(
        allowed=True,
        policy_key="email_send_requires_approval",
        value=requires_approval,
        reason="No external email send was requested.",
        risk_level="low",
        requires_approval=requires_approval,
        blocked_reason=None,
        recommended_next_action="No send action will be taken.",
    )


def evaluate_locked_record_policy(
    target_type: str | None = None,
    is_locked: bool = False,
) -> AutomationPolicyResult:
    value = get_policy_value("locked_record_mutation")
    if is_locked:
        return AutomationPolicyResult(
            allowed=False,
            policy_key="locked_record_mutation",
            value=value,
            reason=(
                "Locked record mutation is denied under the current Level 2 policy set."
                + (f" Target: {target_type}." if target_type else "")
            ),
            risk_level="high",
            requires_approval=True,
            blocked_reason="locked_record_mutation_denied",
            recommended_next_action="Stop and require explicit operator handling or a future exception workflow.",
        )
    return AutomationPolicyResult(
        allowed=True,
        policy_key="locked_record_mutation",
        value=value,
        reason="Target is not locked, so the locked-record policy does not block this action.",
        risk_level="low",
        requires_approval=False,
        blocked_reason=None,
        recommended_next_action="Continue to route-specific boundary and approval checks.",
    )


def summarize_policy_set() -> list[dict[str, Any]]:
    return [
        {
            "policy_key": definition.policy_key,
            "value": definition.value,
            "description": definition.description,
            "enforcement_status": definition.enforcement_status,
            "notes": definition.notes,
        }
        for definition in list_policy_definitions()
    ]


def _action_policy_key(action_type: str) -> str:
    if action_type in {"send_email", "send_document"}:
        return "external_action_requires_approval"
    if action_type == "quote_update":
        return "quote_update_requires_approval"
    if action_type == "receive_quotes_update":
        return "receive_quotes_update_requires_approval"
    if action_type == "update_catalogue_baseline":
        return "update_catalogue_baseline_requires_approval"
    if action_type == "escalation":
        return "escalation_requires_approval"
    if action_type in {"mark_paid"}:
        return "mark_paid_without_approval"
    if action_type in {"carry_price"}:
        return "carry_price_outside_bid_compare"
    return "external_action_requires_approval"


def _default_risk_for_action(action_type: str) -> str:
    if action_type in {"send_email", "send_document", "mark_paid", "carry_price", "update_catalogue_baseline"}:
        return "high"
    if action_type in {"receive_quotes_update", "quote_update", "create_po", "receive_goods"}:
        return "medium"
    return "medium"
