from __future__ import annotations

from dataclasses import dataclass

from neon_ai.services.automation_policy_service import evaluate_action_policy


ALLOWED_DIRECT_WRITE_ENTITIES = frozenset(
    {
        "automationsetting",
        "automationrun",
        "automationevent",
        "automationmemory",
        "automationproposal",
        "automationquestion",
        "inboundmessage",
        "inboundattachment",
    }
)

FORBIDDEN_DIRECT_WRITE_BUSINESS_ENTITIES = frozenset(
    {
        "customer",
        "site",
        "estimate",
        "workorder",
        "estimatedocumentdraft",
        "materialcall",
        "pricerequest",
        "pricerequestitem",
        "rfqcarriedselection",
        "purchaseorder",
        "purchaseorderitem",
        "purchaseorderreceipt",
        "purchaseorderreceiptitem",
        "purchaseorderitemsourcelink",
        "customerinvoice",
        "vendorinvoice",
        "generateddocument",
        "outboundmessagelog",
        "material",
        "internalprice",
        "material.internalprice",
    }
)

ACTION_REQUIREMENTS = {
    "quote_update": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "receive_quotes_update": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "carry_price": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "lock_record": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "send_email": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "send_document": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "mark_paid": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "update_catalogue_baseline": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "create_po": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "receive_goods": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "create_invoice": {
        "required_service_path": True,
        "requires_proposal": True,
    },
    "update_locked_record": {
        "required_service_path": True,
        "requires_proposal": True,
    },
}


class AutomationWriteBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoundaryDecision:
    allowed: bool
    reason: str
    required_approval: bool
    required_service_path: bool
    risk_level: str
    policy_key: str


def is_direct_write_allowed(entity_name: str) -> bool:
    return _normalize_entity_name(entity_name) in ALLOWED_DIRECT_WRITE_ENTITIES


def require_direct_write_allowed(entity_name: str) -> BoundaryDecision:
    normalized = _normalize_entity_name(entity_name)
    if normalized in ALLOWED_DIRECT_WRITE_ENTITIES:
        return BoundaryDecision(
            allowed=True,
            reason=f"Direct agent write allowed for control-plane entity '{entity_name}'.",
            required_approval=False,
            required_service_path=False,
            risk_level="low",
            policy_key="direct_write.allowed",
        )
    if normalized in FORBIDDEN_DIRECT_WRITE_BUSINESS_ENTITIES:
        raise AutomationWriteBoundaryError(
            f"Direct agent write forbidden for business entity '{entity_name}'."
        )
    raise AutomationWriteBoundaryError(
        f"Direct agent write not recognized as an allowed control-plane entity: '{entity_name}'."
    )


def is_business_entity(entity_name: str) -> bool:
    return _normalize_entity_name(entity_name) in FORBIDDEN_DIRECT_WRITE_BUSINESS_ENTITIES


def require_proposal_for_business_mutation(
    action_type: str,
    target_type: str,
    *,
    proposal_present: bool = True,
) -> BoundaryDecision:
    action_policy = evaluate_action_policy(action_type, target_type=target_type)
    requirement = _requirements_for_action(action_type)
    if action_policy.blocked_reason and action_policy.blocked_reason != "approval_required":
        raise AutomationWriteBoundaryError(action_policy.reason)
    if not is_business_entity(target_type):
        return BoundaryDecision(
            allowed=True,
            reason=f"Target '{target_type}' is not classified as a forbidden direct-write business entity.",
            required_approval=action_policy.requires_approval,
            required_service_path=bool(requirement["required_service_path"]),
            risk_level=action_policy.risk_level,
            policy_key=action_policy.policy_key,
        )
    if not bool(requirement["requires_proposal"]):
        return BoundaryDecision(
            allowed=True,
            reason=f"Action '{action_type}' does not require a proposal under current policy.",
            required_approval=action_policy.requires_approval,
            required_service_path=bool(requirement["required_service_path"]),
            risk_level=action_policy.risk_level,
            policy_key=action_policy.policy_key,
        )
    if not proposal_present:
        raise AutomationWriteBoundaryError(
            f"Business mutation '{action_type}' for '{target_type}' requires a structured JSON proposal before any workflow service call."
        )
    return BoundaryDecision(
        allowed=True,
        reason=(
            f"Business mutation '{action_type}' for '{target_type}' is proposal-backed and may continue "
            "to workflow-service and approval checks."
        ),
        required_approval=action_policy.requires_approval,
        required_service_path=bool(requirement["required_service_path"]),
        risk_level=action_policy.risk_level,
        policy_key=action_policy.policy_key,
    )


def require_approval_for_action(
    action_type: str,
    target_type: str | None = None,
    *,
    approved: bool = True,
) -> BoundaryDecision:
    action_policy = evaluate_action_policy(action_type, target_type=target_type)
    requirement = _requirements_for_action(action_type)
    if action_policy.blocked_reason and action_policy.blocked_reason != "approval_required":
        raise AutomationWriteBoundaryError(action_policy.reason)
    if not action_policy.requires_approval:
        return BoundaryDecision(
            allowed=True,
            reason=f"Action '{action_type}' does not require approval under current policy.",
            required_approval=False,
            required_service_path=bool(requirement["required_service_path"]),
            risk_level=action_policy.risk_level,
            policy_key=action_policy.policy_key,
        )
    if not approved:
        raise AutomationWriteBoundaryError(
            f"Action '{action_type}'"
            + (f" for '{target_type}'" if target_type else "")
            + " requires approval before it can be applied."
        )
    return BoundaryDecision(
        allowed=True,
        reason=f"Action '{action_type}' has the required approval context.",
        required_approval=True,
        required_service_path=bool(requirement["required_service_path"]),
        risk_level=action_policy.risk_level,
        policy_key=action_policy.policy_key,
    )


def require_workflow_service_path(
    action_type: str,
    *,
    service_path: str | None = None,
) -> BoundaryDecision:
    action_policy = evaluate_action_policy(action_type)
    requirement = _requirements_for_action(action_type)
    if action_policy.blocked_reason and action_policy.blocked_reason != "approval_required":
        raise AutomationWriteBoundaryError(action_policy.reason)
    if not bool(requirement["required_service_path"]):
        return BoundaryDecision(
            allowed=True,
            reason=f"Action '{action_type}' does not require a workflow service path under current policy.",
            required_approval=action_policy.requires_approval,
            required_service_path=False,
            risk_level=action_policy.risk_level,
            policy_key=action_policy.policy_key,
        )
    if not str(service_path or "").strip():
        raise AutomationWriteBoundaryError(
            f"Action '{action_type}' must route through an approved Neon_ai workflow service path."
        )
    return BoundaryDecision(
        allowed=True,
        reason=f"Action '{action_type}' is routed through workflow service path '{service_path}'.",
        required_approval=action_policy.requires_approval,
        required_service_path=True,
        risk_level=action_policy.risk_level,
        policy_key=action_policy.policy_key,
    )


def assert_no_forbidden_agent_direct_write(
    entity_name: str,
    action_type: str | None = None,
    *,
    direct_write_attempted: bool = True,
    workflow_service_path: str | None = None,
) -> BoundaryDecision:
    normalized = _normalize_entity_name(entity_name)
    if normalized in ALLOWED_DIRECT_WRITE_ENTITIES:
        return BoundaryDecision(
            allowed=True,
            reason=f"Entity '{entity_name}' is in the allowed direct-write control-plane set.",
            required_approval=False,
            required_service_path=False,
            risk_level="low",
            policy_key="direct_write.allowed",
        )
    if normalized in FORBIDDEN_DIRECT_WRITE_BUSINESS_ENTITIES:
        if direct_write_attempted:
            raise AutomationWriteBoundaryError(
                f"Forbidden agent direct write attempted against business entity '{entity_name}'."
            )
        return BoundaryDecision(
            allowed=True,
            reason=(
                f"No direct write attempted against business entity '{entity_name}'. "
                f"Expected workflow service path: '{workflow_service_path or 'unspecified'}'."
            ),
            required_approval=evaluate_action_policy(action_type).requires_approval if action_type else False,
            required_service_path=True,
            risk_level=evaluate_action_policy(action_type).risk_level if action_type else "medium",
            policy_key=evaluate_action_policy(action_type).policy_key if action_type else "direct_write.forbidden",
        )
    raise AutomationWriteBoundaryError(
        f"Entity '{entity_name}' is not recognized by the write-boundary policy."
    )


def describe_boundary_decision(
    *,
    entity_name: str | None = None,
    action_type: str | None = None,
    target_type: str | None = None,
    direct_write_attempted: bool | None = None,
    proposal_present: bool | None = None,
    approved: bool | None = None,
    workflow_service_path: str | None = None,
) -> BoundaryDecision:
    if entity_name and direct_write_attempted is not None:
        return assert_no_forbidden_agent_direct_write(
            entity_name,
            action_type=action_type,
            direct_write_attempted=direct_write_attempted,
            workflow_service_path=workflow_service_path,
        )
    if action_type and target_type and proposal_present is not None:
        return require_proposal_for_business_mutation(
            action_type,
            target_type,
            proposal_present=proposal_present,
        )
    if action_type and approved is not None:
        return require_approval_for_action(
            action_type,
            target_type=target_type,
            approved=approved,
        )
    if action_type and workflow_service_path is not None:
        return require_workflow_service_path(
            action_type,
            service_path=workflow_service_path,
        )
    raise AutomationWriteBoundaryError("Insufficient inputs to describe a write-boundary decision.")


def _normalize_entity_name(entity_name: str) -> str:
    return str(entity_name or "").strip().replace("`", "").replace('"', "").replace(" ", "").lower()


def _normalize_action_type(action_type: str | None) -> str:
    return str(action_type or "").strip().lower()


def _requirements_for_action(action_type: str | None) -> dict[str, object]:
    normalized = _normalize_action_type(action_type)
    if normalized in ACTION_REQUIREMENTS:
        return ACTION_REQUIREMENTS[normalized]
    return {
        "required_service_path": True,
        "requires_proposal": True,
    }
