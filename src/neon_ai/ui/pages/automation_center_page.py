from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neon_ai.services.automation_inventory_service import (
    AutomationPreset,
    automation_inventory_banner_lines,
    default_automation_setting_for_preset,
    list_automation_presets,
)
from neon_ai.services.automation_control_service import (
    Automation24hSummary,
    AutomationEventRecord,
    AutomationRunRecord,
    AutomationSettingRecord,
    get_automation_24h_summary,
    list_automation_events_for_run,
    list_automation_settings,
    list_events_since,
    list_recent_automation_runs,
    list_runs_since,
)
from neon_ai.services.llm_provider_service import (
    LLMHealthCheckResult,
    LLMProviderConfig,
    ModelLaneConfig,
    get_llm_provider_config,
    health_check,
)
from neon_ai.services.automation_policy_service import (
    AutomationPolicyDefinition,
    list_policy_definitions,
)
from neon_ai.services.automation_memory_service import (
    AutomationMemorySummary,
    AutomationMemoryRecord,
    get_active_memory_summary,
    list_memory,
)
from neon_ai.services.automation_proposal_service import (
    AutomationPendingCounts,
    AutomationProposalRecord,
    AutomationQuestionRecord,
    answer_question_structured,
    dismiss_question_with_reason,
    get_pending_counts,
    list_proposals,
    list_questions,
    list_questions_for_target,
)
from neon_ai.services.automation_proposal_apply_service import (
    apply_approved_proposal,
    approve_proposal,
    reject_proposal,
    supports_apply_action,
)
from neon_ai.services.approved_outbound_send_service import send_approved_prepared_outbound_draft
from neon_ai.services.inbound_intake_service import (
    InboundAttachmentRecord,
    InboundMessageRecord,
    list_inbound_attachments,
    list_inbound_messages,
)
from neon_ai.services.inbound_attachment_text_extraction_service import (
    attachment_requires_review,
    create_attachment_review_question,
    extract_text_for_attachment,
    get_active_attachment_review_question,
    get_attachment_text_summary,
)
from neon_ai.services.outbound_message_log_service import get_outbound_messages_for_entity
from neon_ai.services.outbound_draft_review_service import (
    OutboundDraftReviewRecord,
    build_outbound_draft_detail,
    filter_outbound_draft_reviews,
    get_outbound_draft_filter_options,
    list_outbound_drafts_for_review,
    outbound_draft_send_action_enabled,
)
from neon_ai.services.workflow_obligation_service import (
    WorkflowObligationChangeLogRecord,
    WorkflowObligationRecord,
    add_obligation_note,
    build_obligation_operator_summary,
    dismiss_obligation,
    filter_workflow_obligations_for_operator_view,
    list_obligation_change_log,
    list_obligations_for_review,
    mark_obligation_satisfied_manual,
    summarize_workflow_obligation_operator_views,
    snooze_obligation,
    update_obligation_fields,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_DUE_SOON,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_LABELS,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_NEEDS_HUMAN,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_ORDER,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_OVERDUE,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_TODAY,
)
from neon_ai.services.packing_slip_resolution_service import (
    resolve_packing_slip_question_to_proposal,
)
from neon_ai.services.po_eta_resolution_service import (
    resolve_po_eta_question_to_proposal,
)
from neon_ai.services.vendor_invoice_reconciliation_resolution_service import (
    resolve_vendor_invoice_reconciliation_question_to_proposal,
)


QUEUE_WORKFLOW_OPTIONS = (
    "lead_intake",
    "customer_scheduling",
    "estimate",
    "receive_quotes",
    "po_receiving",
    "vendor_invoice_payables",
    "customer_invoice_ar",
    "unknown_review",
    "automation/control",
)
QUEUE_SORT_OPTIONS = (
    "Priority Queue",
    "Severity / urgency first",
    "Due date oldest first",
    "Created newest first",
    "Created oldest first",
    "Cash-flow risk first",
    "Workflow grouping",
)
PROPOSAL_CASH_FLOW_ACTION_TYPES = {
    "customer_invoice_due_observation",
    "vendor_invoice_reconciliation_due_observation",
    "customer_invoice_payment_followup_due_observation",
    "vendor_invoice_intake_observation",
    "vendor_invoice_customer_billing_review_observation",
}
QUESTION_CASH_FLOW_WORKFLOWS = {"vendor_invoice_payables", "customer_invoice_ar", "cash_flow_review"}
OBLIGATION_CASH_FLOW_EVENTS = {
    "customer_invoice_creation_due",
    "vendor_invoice_reconciled_to_po_receipt",
    "customer_invoice_payment_or_followup",
}
QUESTION_DISAMBIGUATION_TYPES = {
    "customer_disambiguation",
    "site_disambiguation",
    "po_disambiguation",
    "vendor_disambiguation",
    "estimate_disambiguation",
    "work_order_disambiguation",
}
RISK_ORDER = {"critical": 4, "high": 3, "medium": 2, "normal": 2, "low": 1}
URGENCY_ORDER = {"critical": 5, "urgent": 4, "high": 3, "medium": 2, "normal": 2, "low": 1}


@dataclass(frozen=True)
class QueueFilterState:
    search_text: str = ""
    workflow: str = "All workflows"
    item_type: str = "All types"
    status: str = "All statuses"
    priority: str = "All priorities"
    overdue_only: bool = False
    cash_flow_only: bool = False
    source_text: str = ""
    sort_mode: str = "Priority Queue"


def build_proposal_queue_metadata(
    proposal: AutomationProposalRecord,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    evidence = proposal.evidence_json if isinstance(proposal.evidence_json, dict) else {}
    source_summary = " / ".join(
        part
        for part in (
            _first_non_empty(
                proposed_change.get("customer_name"),
                proposed_change.get("contact_name"),
                proposed_change.get("vendor_name"),
                proposed_change.get("company_name"),
                evidence.get("sender_name"),
                evidence.get("sender"),
            ),
            proposed_change.get("sender_email") or evidence.get("sender_email") or evidence.get("sender"),
        )
        if part
    ) or "-"
    target_summary = _target_summary(
        proposal.target_type,
        proposal.target_id,
        _first_non_empty(
            proposed_change.get("invoice_number"),
            proposed_change.get("estimate_reference"),
            proposed_change.get("po_number"),
            proposed_change.get("packing_slip_number"),
            proposed_change.get("customer_name"),
            proposed_change.get("vendor_name"),
        ),
    )
    uncertainty_notes = _collect_uncertainty_notes(proposed_change, evidence)
    due_at = _coerce_datetime(
        proposed_change.get("expected_by")
        or proposed_change.get("due_date")
        or proposed_change.get("delivery_date")
    )
    days_overdue = proposed_change.get("days_overdue")
    if days_overdue is None:
        days_overdue = _days_overdue(due_at, current)
    age_days = _days_old(proposal.created_at, current)
    cash_flow = is_cash_flow_critical_proposal(proposal)
    return {
        "workflow": proposal.workflow,
        "item_type": proposal.action_type,
        "status": proposal.status,
        "priority": proposal.risk_level or "Medium",
        "created_at": proposal.created_at,
        "age_days": age_days,
        "age_text": _age_text(age_days),
        "source_summary": source_summary,
        "target_summary": target_summary,
        "cash_flow_critical": cash_flow,
        "cash_flow_text": "Cash Flow" if cash_flow else "-",
        "uncertainty_summary": uncertainty_notes[0] if uncertainty_notes else "-",
        "operator_action_needed": proposal.status in {"Pending", "Approved"},
        "suggested_next_step": _proposal_next_step_text(proposal.action_type),
        "due_at": due_at,
        "overdue_days": int(days_overdue or 0),
        "is_overdue": int(days_overdue or 0) > 0,
        "search_blob": _queue_search_blob(
            proposal.workflow,
            proposal.action_type,
            proposal.summary,
            source_summary,
            target_summary,
            proposal.risk_level,
            proposal.status,
            uncertainty_notes,
            proposed_change,
            evidence,
        ),
    }


def build_question_queue_metadata(
    question: AutomationQuestionRecord,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    extracted_fields = choices.get("extracted_fields") if isinstance(choices.get("extracted_fields"), dict) else {}
    is_attachment_review = question.automation_key == "attachment_text_extraction"
    warnings = _coerce_list_of_text(choices.get("warnings"))
    errors = _coerce_list_of_text(choices.get("errors"))
    source_summary = " / ".join(
        part
        for part in (
            _first_non_empty(
                choices.get("parent_message_sender") if is_attachment_review else None,
                extracted_fields.get("customer_name"),
                extracted_fields.get("contact_name"),
                extracted_fields.get("vendor_name"),
                extracted_fields.get("company_name"),
                choices.get("sender_name"),
                choices.get("sender"),
            ),
            (
                choices.get("parent_message_subject")
                if is_attachment_review
                else extracted_fields.get("sender_email") or choices.get("sender")
            ),
        )
        if part
    ) or "-"
    target_summary = _target_summary(
        question.target_type,
        question.target_id,
        _first_non_empty(
            choices.get("filename") if is_attachment_review else None,
            extracted_fields.get("estimate_reference"),
            extracted_fields.get("invoice_number"),
            extracted_fields.get("po_number"),
            extracted_fields.get("site_address_text"),
        ),
    )
    uncertainty_notes = _coerce_list_of_text(choices.get("uncertainty_notes"))
    age_days = _days_old(question.created_at, current)
    cash_flow = is_cash_flow_critical_question(question)
    return {
        "workflow": question.workflow,
        "item_type": question.question_type,
        "status": question.status,
        "priority": question.urgency or "Normal",
        "created_at": question.created_at,
        "age_days": age_days,
        "age_text": _age_text(age_days),
        "source_summary": source_summary,
        "target_summary": target_summary,
        "cash_flow_critical": cash_flow,
        "cash_flow_text": "Cash Flow" if cash_flow else "-",
        "uncertainty_summary": _first_non_empty(
            choices.get("uncertainty_reason"),
            errors[0] if errors else None,
            warnings[0] if warnings else None,
            uncertainty_notes[0] if uncertainty_notes else None,
            choices.get("review_reason"),
        )
        or "-",
        "operator_action_needed": question.status.lower() == "open",
        "suggested_next_step": _question_next_step_text(question.question_type, question.workflow),
        "due_at": None,
        "overdue_days": 0,
        "is_overdue": False,
        "search_blob": _queue_search_blob(
            question.workflow,
            question.question_type,
            question.question_text,
            source_summary,
            target_summary,
            question.urgency,
            question.status,
            uncertainty_notes,
            choices,
            extracted_fields,
        ),
    }


def build_obligation_queue_metadata(
    obligation: WorkflowObligationRecord,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    evidence = obligation.evidence_json if isinstance(obligation.evidence_json, dict) else {}
    source_summary = _target_summary(
        obligation.source_record_type,
        obligation.source_record_id,
        _first_non_empty(
            evidence.get("customer_name"),
            evidence.get("vendor_name"),
            evidence.get("invoice_number"),
            evidence.get("po_number"),
            evidence.get("source_estimate_id"),
        ),
    )
    effective_due = _coerce_datetime(obligation.snooze_until or obligation.expected_by)
    overdue_days = _days_overdue(effective_due, current)
    cash_flow = is_cash_flow_critical_obligation(obligation)
    uncertainty_notes = _coerce_list_of_text(evidence.get("uncertainty_notes"))
    return {
        "workflow": obligation.workflow_type,
        "item_type": obligation.expected_event_type,
        "status": obligation.status,
        "priority": obligation.severity or "Medium",
        "created_at": obligation.created_at,
        "age_days": _days_old(obligation.created_at, current),
        "age_text": _age_text(_days_old(obligation.created_at, current)),
        "source_summary": source_summary,
        "owner_summary": " / ".join(
            part for part in (obligation.owner_role or "-", obligation.owner_user_id or "-") if part
        ),
        "cash_flow_critical": cash_flow,
        "cash_flow_text": "Cash Flow" if cash_flow else "-",
        "uncertainty_summary": uncertainty_notes[0] if uncertainty_notes else "-",
        "operator_action_needed": obligation.status in {"waiting", "overdue", "proposal_created", "blocked", "failed"},
        "suggested_next_step": _obligation_next_step_text(obligation.expected_event_type),
        "due_at": effective_due,
        "overdue_days": overdue_days,
        "is_overdue": overdue_days > 0 or obligation.status == "overdue",
        "last_status_summary": (
            f"Proposal #{obligation.last_proposal_id}" if obligation.last_proposal_id else "No proposal yet"
        ),
        "search_blob": _queue_search_blob(
            obligation.workflow_type,
            obligation.expected_event_type,
            obligation.title,
            obligation.description,
            source_summary,
            obligation.severity,
            obligation.status,
            uncertainty_notes,
            evidence,
        ),
    }


def filter_and_sort_proposals(
    proposals: list[AutomationProposalRecord],
    filter_state: QueueFilterState,
    *,
    now: datetime | None = None,
) -> list[AutomationProposalRecord]:
    current = now or datetime.now(timezone.utc)
    filtered = [
        proposal
        for proposal in proposals
        if _matches_queue_filter(build_proposal_queue_metadata(proposal, now=current), filter_state)
    ]
    return sorted(filtered, key=lambda proposal: _proposal_sort_key(proposal, filter_state.sort_mode, current))


def filter_and_sort_questions(
    questions: list[AutomationQuestionRecord],
    filter_state: QueueFilterState,
    *,
    now: datetime | None = None,
) -> list[AutomationQuestionRecord]:
    current = now or datetime.now(timezone.utc)
    filtered = [
        question
        for question in questions
        if _matches_queue_filter(build_question_queue_metadata(question, now=current), filter_state)
    ]
    return sorted(filtered, key=lambda question: _question_sort_key(question, filter_state.sort_mode, current))


def filter_and_sort_obligations(
    obligations: list[WorkflowObligationRecord],
    filter_state: QueueFilterState,
    *,
    now: datetime | None = None,
) -> list[WorkflowObligationRecord]:
    current = now or datetime.now(timezone.utc)
    filtered = [
        obligation
        for obligation in obligations
        if _matches_queue_filter(build_obligation_queue_metadata(obligation, now=current), filter_state)
    ]
    return sorted(filtered, key=lambda obligation: _obligation_sort_key(obligation, filter_state.sort_mode, current))


def summarize_queue_counts(
    proposals: list[AutomationProposalRecord],
    questions: list[AutomationQuestionRecord],
    obligations: list[WorkflowObligationRecord],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or datetime.now(timezone.utc)
    proposal_meta = [build_proposal_queue_metadata(record, now=current) for record in proposals]
    question_meta = [build_question_queue_metadata(record, now=current) for record in questions]
    obligation_meta = [build_obligation_queue_metadata(record, now=current) for record in obligations]
    return {
        "open_proposals": sum(1 for record in proposals if record.status not in {"Rejected", "Applied"}),
        "open_questions": sum(1 for record in questions if record.status.lower() == "open"),
        "overdue_obligations": sum(1 for meta in obligation_meta if meta["is_overdue"]),
        "high_urgency_questions": sum(
            1 for meta in question_meta if _priority_rank(meta["priority"], urgency=True) >= _priority_rank("high", urgency=True)
        ),
        "cash_flow_critical_items": sum(
            1 for meta in [*proposal_meta, *question_meta, *obligation_meta] if meta["cash_flow_critical"]
        ),
        "items_due_today": sum(1 for meta in obligation_meta if _is_due_today(meta["due_at"], current)),
        "items_overdue_gt_7": sum(
            1 for meta in [*proposal_meta, *obligation_meta] if int(meta["overdue_days"] or 0) > 7
        ),
        "items_awaiting_operator_answer": sum(
            1 for record in questions if record.status.lower() == "open" and record.required_before_action
        ),
    }


def is_cash_flow_critical_proposal(proposal: AutomationProposalRecord) -> bool:
    if proposal.action_type in PROPOSAL_CASH_FLOW_ACTION_TYPES:
        return True
    if proposal.workflow in {"vendor_invoice_payables", "customer_invoice_ar"}:
        return True
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    flags = _coerce_list_of_text(proposed_change.get("cash_flow_flags")) + _coerce_list_of_text(
        proposed_change.get("reconciliation_flags")
    )
    return any(flags)


def is_cash_flow_critical_question(question: AutomationQuestionRecord) -> bool:
    if question.workflow in QUESTION_CASH_FLOW_WORKFLOWS:
        return True
    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    if bool(choices.get("cash_flow_critical")):
        return True
    flags = _coerce_list_of_text(choices.get("cash_flow_flags")) + _coerce_list_of_text(
        choices.get("reconciliation_flags")
    )
    return any(flags)


def is_cash_flow_critical_obligation(obligation: WorkflowObligationRecord) -> bool:
    if obligation.expected_event_type in OBLIGATION_CASH_FLOW_EVENTS:
        return True
    if obligation.workflow_type in {"vendor_invoice_payables", "customer_invoice_ar"}:
        return True
    evidence = obligation.evidence_json if isinstance(obligation.evidence_json, dict) else {}
    flags = _coerce_list_of_text(evidence.get("cash_flow_flags")) + _coerce_list_of_text(
        evidence.get("reconciliation_flags")
    )
    return any(flags)


def _matches_queue_filter(metadata: dict[str, Any], filter_state: QueueFilterState) -> bool:
    search_text = filter_state.search_text.strip().lower()
    if search_text and search_text not in str(metadata.get("search_blob") or "").lower():
        return False
    if filter_state.source_text.strip():
        if filter_state.source_text.strip().lower() not in str(metadata.get("source_summary") or "").lower():
            return False
    if filter_state.workflow != "All workflows" and metadata.get("workflow") != filter_state.workflow:
        return False
    if filter_state.item_type != "All types" and metadata.get("item_type") != filter_state.item_type:
        return False
    if filter_state.status != "All statuses" and metadata.get("status") != filter_state.status:
        return False
    if filter_state.priority != "All priorities" and str(metadata.get("priority") or "") != filter_state.priority:
        return False
    if filter_state.overdue_only and not bool(metadata.get("is_overdue")):
        return False
    if filter_state.cash_flow_only and not bool(metadata.get("cash_flow_critical")):
        return False
    return True


def _proposal_sort_key(proposal: AutomationProposalRecord, sort_mode: str, now: datetime) -> tuple:
    meta = build_proposal_queue_metadata(proposal, now=now)
    risk_rank = _priority_rank(meta["priority"])
    created_rank = _timestamp_rank(meta["created_at"])
    due_rank = _timestamp_rank(meta["due_at"], default_high=True)
    if sort_mode == "Created newest first":
        return (-created_rank, proposal.automation_proposal_id)
    if sort_mode == "Created oldest first":
        return (created_rank, proposal.automation_proposal_id)
    if sort_mode == "Due date oldest first":
        return (due_rank, -risk_rank, proposal.automation_proposal_id)
    if sort_mode == "Cash-flow risk first":
        return (-int(meta["cash_flow_critical"]), -risk_rank, -int(meta["overdue_days"]), -created_rank)
    if sort_mode == "Workflow grouping":
        return (str(meta["workflow"]), -risk_rank, -created_rank, proposal.automation_proposal_id)
    return (
        -risk_rank,
        -int(meta["is_overdue"]),
        -int(meta["cash_flow_critical"]),
        -int(meta["overdue_days"]),
        -created_rank,
        proposal.automation_proposal_id,
    )


def _question_sort_key(question: AutomationQuestionRecord, sort_mode: str, now: datetime) -> tuple:
    meta = build_question_queue_metadata(question, now=now)
    urgency_rank = _priority_rank(meta["priority"], urgency=True)
    created_rank = _timestamp_rank(meta["created_at"])
    if sort_mode == "Created newest first":
        return (-created_rank, question.automation_question_id)
    if sort_mode == "Created oldest first":
        return (created_rank, question.automation_question_id)
    if sort_mode == "Cash-flow risk first":
        return (-int(meta["cash_flow_critical"]), -urgency_rank, -created_rank, question.automation_question_id)
    if sort_mode == "Workflow grouping":
        return (str(meta["workflow"]), -urgency_rank, -created_rank, question.automation_question_id)
    return (
        -urgency_rank,
        -int(meta["cash_flow_critical"]),
        -created_rank,
        question.automation_question_id,
    )


def _obligation_sort_key(obligation: WorkflowObligationRecord, sort_mode: str, now: datetime) -> tuple:
    meta = build_obligation_queue_metadata(obligation, now=now)
    severity_rank = _priority_rank(meta["priority"])
    created_rank = _timestamp_rank(meta["created_at"])
    due_rank = _timestamp_rank(meta["due_at"], default_high=True)
    if sort_mode == "Created newest first":
        return (-created_rank, obligation.obligation_id)
    if sort_mode == "Created oldest first":
        return (created_rank, obligation.obligation_id)
    if sort_mode == "Due date oldest first":
        return (due_rank, -severity_rank, obligation.obligation_id)
    if sort_mode == "Cash-flow risk first":
        return (-int(meta["cash_flow_critical"]), -int(meta["is_overdue"]), -severity_rank, due_rank)
    if sort_mode == "Workflow grouping":
        return (str(meta["workflow"]), -int(meta["is_overdue"]), -severity_rank, due_rank, obligation.obligation_id)
    return (
        -int(meta["is_overdue"]),
        -severity_rank,
        -int(meta["cash_flow_critical"]),
        due_rank,
        obligation.obligation_id,
    )


def _collect_uncertainty_notes(*payloads: Any) -> list[str]:
    notes: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        notes.extend(_coerce_list_of_text(payload.get("uncertainty_notes")))
        if payload.get("uncertainty_reason"):
            notes.append(str(payload.get("uncertainty_reason")))
        if payload.get("review_reason"):
            notes.append(str(payload.get("review_reason")))
    cleaned: list[str] = []
    seen: set[str] = set()
    for note in notes:
        key = note.strip()
        if not key:
            continue
        normalized = key.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(key)
    return cleaned


def _coerce_list_of_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _queue_search_blob(*parts: Any) -> str:
    flattened: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, dict):
            flattened.append(json.dumps(part, sort_keys=True, default=str))
        elif isinstance(part, list):
            flattened.append(" ".join(str(item) for item in part))
        else:
            flattened.append(str(part))
    return " | ".join(flattened)


def _target_summary(target_type: Any, target_id: Any, trailing_hint: Any = None) -> str:
    base = " / ".join(part for part in (str(target_type).strip() if target_type else None, str(target_id).strip() if target_id else None) if part) or "-"
    hint = _first_non_empty(trailing_hint)
    if hint and hint not in base:
        return f"{base} | {hint}"
    return base


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, "", "-"):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _days_old(value: datetime | None, now: datetime) -> int:
    if value is None:
        return 0
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0, (now.date() - current.date()).days)


def _days_overdue(value: datetime | None, now: datetime) -> int:
    if value is None:
        return 0
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0, (now.date() - current.date()).days)


def _is_due_today(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.date() == now.date()


def _age_text(days: int) -> str:
    if days <= 0:
        return "Today"
    if days == 1:
        return "1 day"
    return f"{days} days"


def _priority_rank(value: Any, *, urgency: bool = False) -> int:
    text = str(value or "").strip().lower()
    ordering = URGENCY_ORDER if urgency else RISK_ORDER
    return ordering.get(text, 0)


def _timestamp_rank(value: datetime | None, *, default_high: bool = False) -> float:
    if value is None:
        return float("inf") if default_high else 0.0
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.timestamp()


def _proposal_next_step_text(action_type: str) -> str:
    mapping = {
        "lead_intake_observation": "Review customer intake",
        "customer_scheduling_service_observation": "Review customer reply before sending response",
        "estimate_reply_observation": "Review customer reply before sending response",
        "estimate_acceptance_observation": "Create work-order draft from estimate acceptance",
        "estimate_revision_observation": "Review requested estimate revision",
        "estimate_rejection_observation": "Review estimate rejection / lost signal",
        "estimate_schedule_request_observation": "Review estimate scheduling request",
        "staged_receipt_observation": "Review packing slip before receiving",
        "staged_receipt_proposal": "Review packing slip before receiving",
        "vendor_invoice_intake_observation": "Review vendor invoice intake",
        "vendor_invoice_reconciliation_due_observation": "Reconcile vendor invoice to PO/receipt",
        "vendor_invoice_customer_billing_review_observation": "Review customer billing vs vendor invoice exposure",
        "customer_invoice_due_observation": "Create/send customer invoice",
        "customer_invoice_payment_followup_due_observation": "Follow up on unpaid customer invoice",
        "estimate_followup_due_observation": "Create follow-up email draft",
        "po_eta_status_observation": "Record internal PO ETA / parts-ready observation",
    }
    return mapping.get(action_type, "Review proposal")


def _question_next_step_text(question_type: str, workflow: str) -> str:
    mapping = {
        "attachment_review_required": "Review attachment manually",
        "attachment_text_extraction_required": "Review attachment text extraction",
        "attachment_needs_ocr": "Review scanned PDF / OCR need",
        "unsupported_attachment_review": "Review unsupported attachment manually",
        "missing_attachment_file_review": "Locate or reattach missing file",
        "inbound_routing_review": "Answer routing question",
        "vendor_invoice_review_required": "Review vendor invoice intake",
        "vendor_disambiguation": "Resolve vendor match",
        "po_disambiguation": "Resolve PO match",
        "duplicate_invoice_review": "Review duplicate invoice risk",
        "non_po_expense_review": "Review non-PO vendor invoice exception",
        "non_po_vendor_invoice_exception_review": "Review non-PO vendor invoice exception",
        "invoice_missing_required_fields": "Confirm missing invoice fields",
        "customer_billing_status_review": "Review customer billing status",
        "cash_flow_relationship_review": "Resolve customer billing relationship",
        "vendor_invoice_customer_billing_disambiguation": "Resolve vendor invoice billing relationship",
        "customer_invoice_disambiguation": "Resolve customer invoice link",
        "no_customer_billing_found_review": "Review missing customer billing",
        "customer_disambiguation": "Resolve customer match",
        "site_disambiguation": "Resolve site match",
        "work_order_disambiguation": "Resolve work-order match",
        "reply_required_review": "Review customer reply before sending response",
        "customer_scheduling_review_required": "Review customer intake",
        "new_service_inquiry_review": "Review customer intake",
        "urgent_service_review": "Review urgent service inquiry",
        "lead_intake_review_required": "Review customer intake",
        "missing_contact_detail": "Request or locate contact detail",
        "missing_project_detail": "Request or locate project detail",
        "urgent_lead_review": "Review urgent lead intake",
        "estimate_disambiguation": "Resolve estimate match",
        "urgent_customer_reply_review": "Review urgent customer reply",
        "existing_workflow_reply_review": "Route message to the correct workflow",
    }
    if workflow in {"vendor_invoice_payables", "customer_invoice_ar"}:
        return mapping.get(question_type, "Review cash-flow item")
    return mapping.get(question_type, "Review question")


def _obligation_next_step_text(expected_event_type: str) -> str:
    mapping = {
        "estimate_customer_reply_or_followup_due": "Review estimate follow-up",
        "customer_invoice_creation_due": "Create/send customer invoice",
        "vendor_invoice_reconciled_to_po_receipt": "Reconcile vendor invoice to PO/receipt",
        "customer_invoice_payment_or_followup": "Follow up on unpaid customer invoice",
    }
    return mapping.get(expected_event_type, "Review obligation")


class AutomationCenterPage(QWidget):
    def __init__(self, main_window=None, *, surface_key: str = "dashboard") -> None:
        parent = main_window if isinstance(main_window, QWidget) else None
        super().__init__(parent)
        self.main_window = main_window
        self._presets_by_key: dict[str, AutomationPreset] = {}
        self._settings_by_key: dict[str, AutomationSettingRecord] = {}
        self._latest_runs_by_key: dict[str, AutomationRunRecord] = {}
        self._recent_runs_24h: list[AutomationRunRecord] = []
        self._recent_events_24h: list[AutomationEventRecord] = []
        self._selected_run_events: list[AutomationEventRecord] = []
        self._memory_records: list[AutomationMemoryRecord] = []
        self._obligation_records: list[WorkflowObligationRecord] = []
        self._selected_obligation_change_log: list[WorkflowObligationChangeLogRecord] = []
        self._proposal_records: list[AutomationProposalRecord] = []
        self._question_records: list[AutomationQuestionRecord] = []
        self._outbound_draft_records: list[OutboundDraftReviewRecord] = []
        self._inbound_messages: list[InboundMessageRecord] = []
        self._selected_message_attachments: list[InboundAttachmentRecord] = []
        self._policy_definitions: list[AutomationPolicyDefinition] = []
        self._filtered_obligation_records: list[WorkflowObligationRecord] = []
        self._filtered_proposal_records: list[AutomationProposalRecord] = []
        self._filtered_question_records: list[AutomationQuestionRecord] = []
        self._filtered_outbound_draft_records: list[OutboundDraftReviewRecord] = []
        self._obligation_error: str | None = None
        self._proposal_error: str | None = None
        self._question_error: str | None = None
        self._outbound_draft_error: str | None = None
        self._memory_summary = AutomationMemorySummary(0, 0, 0, 0)
        self._pending_counts = AutomationPendingCounts(0, 0)
        self._summary = Automation24hSummary(0, 0, 0, 0, 0, 0, 0, 0, 0.0)
        disabled_lane = ModelLaneConfig(
            lane="disabled",
            provider="disabled",
            model=None,
            allow_live=False,
            key_present=False,
            available=False,
            max_calls_per_run=None,
            max_input_chars=None,
            notes="LLM provider status not loaded yet.",
        )
        self._llm_config = LLMProviderConfig(
            provider="disabled",
            model=None,
            safe_mode=True,
            primary_local_provider="ollama",
            primary_remote_provider="openai",
            primary_remote_fast_provider="openai",
            primary_remote_strong_provider="openai",
            local_model=None,
            remote_model=None,
            remote_fast_model=None,
            remote_strong_model=None,
            allow_live_local=False,
            allow_live_remote=False,
            allow_live_remote_fast=False,
            allow_live_remote_strong=False,
            remote_escalation_only=True,
            max_remote_calls_per_run=0,
            max_remote_strong_calls_per_run=0,
            max_remote_input_chars=0,
            openai_key_present=False,
            ollama_model=None,
            openai_model=None,
            notes="LLM provider status not loaded yet.",
            lanes={
                "local": disabled_lane,
                "remote_fast": disabled_lane,
                "remote_strong": disabled_lane,
                "disabled": disabled_lane,
            },
        )
        self._llm_health = LLMHealthCheckResult(
            success=True,
            provider="disabled",
            model=None,
            status="not_loaded",
            safe_mode=True,
            openai_key_present=False,
            error=None,
        )
        self._surface_definitions: list[tuple[str, str, str]] = [
            ("dashboard", "Dashboard / Summary", "_build_inventory_tab"),
            ("pending_approvals", "Pending Approvals", "_build_pending_approvals_tab"),
            ("questions", "Questions", "_build_questions_tab"),
            ("obligations", "Workflow Obligations", "_build_obligations_tab"),
            ("outbound_drafts", "Outbound Drafts", "_build_outbound_drafts_tab"),
            ("intake", "Inbound Intake", "_build_intake_tab"),
            ("activity", "Runs / Events", "_build_activity_tab"),
            ("memory", "Automation Memory", "_build_memory_tab"),
            ("policies", "Policies", "_build_policies_tab"),
            ("provider", "Provider Status", "_build_provider_tab"),
        ]
        if surface_key not in {key for key, _label, _builder_name in self._surface_definitions}:
            raise KeyError(f"Unknown Automation Center surface: {surface_key}")
        self._surface_widgets: dict[str, QWidget] = {}
        self._surface_stack_indexes: dict[str, int] = {}
        self._current_surface_key = surface_key

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel(f"Automation: {self._surface_label(self._current_surface_key)}")
        heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Structured read-only control-plane view of the next-generation automation catalogue and activity ledger. "
            "This slice remains non-executing and exists to make policy, scope, and recent logged activity inspectable."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        self.banner = QFrame()
        self.banner.setStyleSheet(
            "QFrame { background: #fff7d6; border: 1px solid #e5cf79; border-radius: 8px; }"
        )
        banner_layout = QVBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 10, 12, 10)
        banner_layout.setSpacing(4)
        for line in automation_inventory_banner_lines():
            label = QLabel(line)
            label.setWordWrap(True)
            label.setStyleSheet("font-weight: 600; color: #5b4b00;")
            banner_layout.addWidget(label)
        layout.addWidget(self.banner)

        self.surface_shell = self._build_surface_shell()
        layout.addWidget(self.surface_shell, 1)

        self._show_surface(self._current_surface_key, refresh=False)

    def _build_surface_shell(self) -> QWidget:
        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.surface_stack = QStackedWidget()
        shell_layout.addWidget(self.surface_stack, 1)
        return shell

    def _surface_label(self, surface_key: str) -> str:
        for key, label, _builder_name in self._surface_definitions:
            if key == surface_key:
                return label
        return surface_key

    def _surface_builder_name(self, surface_key: str) -> str:
        for key, _label, builder_name in self._surface_definitions:
            if key == surface_key:
                return builder_name
        raise KeyError(f"Unknown Automation Center surface: {surface_key}")

    def _ensure_surface(self, surface_key: str) -> QWidget:
        if surface_key in self._surface_widgets:
            return self._surface_widgets[surface_key]
        builder = getattr(self, self._surface_builder_name(surface_key))
        content = builder()
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel(self._surface_label(surface_key))
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(lambda checked=False, key=surface_key: self.refresh_data(key))
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh_button)
        wrapper_layout.addLayout(header)
        wrapper_layout.addWidget(content, 1)
        index = self.surface_stack.addWidget(wrapper)
        self._surface_stack_indexes[surface_key] = index
        self._surface_widgets[surface_key] = wrapper
        return wrapper

    def _show_surface(self, surface_key: str, *, refresh: bool = True) -> None:
        self._ensure_surface(surface_key)
        self._current_surface_key = surface_key
        self.surface_stack.setCurrentIndex(self._surface_stack_indexes[surface_key])
        if refresh:
            self.refresh_data(surface_key)

    def _build_queue_summary_group(self) -> QGroupBox:
        group = QGroupBox("Operator Queue Summary")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(20)
        layout.setVerticalSpacing(8)
        self.queue_summary_labels: dict[str, QLabel] = {}
        fields = [
            ("Open Proposals", "open_proposals"),
            ("Open Questions", "open_questions"),
            ("Overdue Obligations", "overdue_obligations"),
            ("High Urgency Questions", "high_urgency_questions"),
            ("Cash-Flow Critical Items", "cash_flow_critical_items"),
            ("Items Due Today", "items_due_today"),
            ("Items Overdue > 7 Days", "items_overdue_gt_7"),
            ("Awaiting Operator Answer", "items_awaiting_operator_answer"),
        ]
        for index, (title, key) in enumerate(fields):
            title_label = QLabel(title)
            title_label.setStyleSheet("font-weight: 600;")
            value_label = QLabel("0")
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.queue_summary_labels[key] = value_label
            row = index // 4
            column = (index % 4) * 2
            layout.addWidget(title_label, row, column)
            layout.addWidget(value_label, row, column + 1)
        note = QLabel(
            "Queue management in this slice is triage-only: filters, sorting, grouping, and safe obligation edits. "
            "No bulk apply, no new workflow mutations, and no external send behavior were added."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")
        layout.addWidget(note, 2, 0, 1, 8)
        return group

    def _build_queue_controls(
        self,
        *,
        item_type_label: str,
        priority_label: str,
        controls: dict[str, QWidget],
        apply_callback,
    ) -> QGroupBox:
        group = QGroupBox("Queue Controls")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        search_input = QLineEdit()
        search_input.setPlaceholderText("Search summary, sender, customer, vendor, notes")
        workflow_combo = QComboBox()
        type_combo = QComboBox()
        status_combo = QComboBox()
        priority_combo = QComboBox()
        source_input = QLineEdit()
        source_input.setPlaceholderText("Source sender / customer / vendor")
        overdue_only = QCheckBox("Overdue only")
        cash_flow_only = QCheckBox("Cash Flow only")
        sort_combo = QComboBox()
        refresh_button = QPushButton("Refresh")
        clear_button = QPushButton("Clear Filters")

        workflow_combo.addItem("All workflows")
        for workflow in QUEUE_WORKFLOW_OPTIONS:
            workflow_combo.addItem(workflow)
        type_combo.addItem("All types")
        status_combo.addItem("All statuses")
        priority_combo.addItem("All priorities")
        sort_combo.addItems(QUEUE_SORT_OPTIONS)

        controls.update(
            {
                "search": search_input,
                "workflow": workflow_combo,
                "item_type": type_combo,
                "status": status_combo,
                "priority": priority_combo,
                "source": source_input,
                "overdue_only": overdue_only,
                "cash_flow_only": cash_flow_only,
                "sort": sort_combo,
                "refresh": refresh_button,
                "clear": clear_button,
            }
        )

        layout.addWidget(QLabel("Search"), 0, 0)
        layout.addWidget(search_input, 0, 1)
        layout.addWidget(QLabel("Workflow"), 0, 2)
        layout.addWidget(workflow_combo, 0, 3)
        layout.addWidget(QLabel(item_type_label), 0, 4)
        layout.addWidget(type_combo, 0, 5)

        layout.addWidget(QLabel("Status"), 1, 0)
        layout.addWidget(status_combo, 1, 1)
        layout.addWidget(QLabel(priority_label), 1, 2)
        layout.addWidget(priority_combo, 1, 3)
        layout.addWidget(QLabel("Source"), 1, 4)
        layout.addWidget(source_input, 1, 5)

        layout.addWidget(overdue_only, 2, 0)
        layout.addWidget(cash_flow_only, 2, 1)
        layout.addWidget(QLabel("Sort"), 2, 2)
        layout.addWidget(sort_combo, 2, 3)
        layout.addWidget(refresh_button, 2, 4)
        layout.addWidget(clear_button, 2, 5)

        search_input.textChanged.connect(lambda *_: apply_callback())
        workflow_combo.currentIndexChanged.connect(lambda *_: apply_callback())
        type_combo.currentIndexChanged.connect(lambda *_: apply_callback())
        status_combo.currentIndexChanged.connect(lambda *_: apply_callback())
        priority_combo.currentIndexChanged.connect(lambda *_: apply_callback())
        source_input.textChanged.connect(lambda *_: apply_callback())
        overdue_only.toggled.connect(lambda *_: apply_callback())
        cash_flow_only.toggled.connect(lambda *_: apply_callback())
        sort_combo.currentIndexChanged.connect(lambda *_: apply_callback())
        refresh_button.clicked.connect(lambda *_: self.refresh_data())
        clear_button.clicked.connect(lambda: self._clear_queue_filters(controls, apply_callback))
        return group

    def _clear_queue_filters(self, controls: dict[str, QWidget], apply_callback) -> None:
        for key, widget in controls.items():
            widget.blockSignals(True)
            if isinstance(widget, QLineEdit):
                widget.clear()
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(0)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(False)
            widget.blockSignals(False)
        apply_callback()

    def _queue_filter_state(self, controls: dict[str, QWidget]) -> QueueFilterState:
        search_input = controls["search"]
        workflow_combo = controls["workflow"]
        type_combo = controls["item_type"]
        status_combo = controls["status"]
        priority_combo = controls["priority"]
        overdue_only = controls["overdue_only"]
        cash_flow_only = controls["cash_flow_only"]
        source_input = controls["source"]
        sort_combo = controls["sort"]
        return QueueFilterState(
            search_text=search_input.text() if isinstance(search_input, QLineEdit) else "",
            workflow=workflow_combo.currentText() if isinstance(workflow_combo, QComboBox) else "All workflows",
            item_type=type_combo.currentText() if isinstance(type_combo, QComboBox) else "All types",
            status=status_combo.currentText() if isinstance(status_combo, QComboBox) else "All statuses",
            priority=priority_combo.currentText() if isinstance(priority_combo, QComboBox) else "All priorities",
            overdue_only=overdue_only.isChecked() if isinstance(overdue_only, QCheckBox) else False,
            cash_flow_only=cash_flow_only.isChecked() if isinstance(cash_flow_only, QCheckBox) else False,
            source_text=source_input.text() if isinstance(source_input, QLineEdit) else "",
            sort_mode=sort_combo.currentText() if isinstance(sort_combo, QComboBox) else "Priority Queue",
        )

    def _selected_obligation_operator_view(self) -> str:
        combo = getattr(self, "obligation_operator_view_combo", None)
        if isinstance(combo, QComboBox):
            value = combo.currentData()
            if isinstance(value, str) and value.strip():
                return value
        return WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL

    def _refresh_obligation_operator_view_counts(self, counts: dict[str, int]) -> None:
        combo = getattr(self, "obligation_operator_view_combo", None)
        if isinstance(combo, QComboBox):
            for index in range(combo.count()):
                view_name = combo.itemData(index)
                if not isinstance(view_name, str):
                    continue
                label = WORKFLOW_OBLIGATION_OPERATOR_VIEW_LABELS.get(view_name, view_name.title())
                combo.setItemText(index, f"{label} ({int(counts.get(view_name, 0))})")
        label_widget = getattr(self, "obligation_operator_view_counts_label", None)
        if isinstance(label_widget, QLabel):
            label_widget.setText(
                " | ".join(
                    [
                        f"Today {int(counts.get(WORKFLOW_OBLIGATION_OPERATOR_VIEW_TODAY, 0))}",
                        f"Overdue {int(counts.get(WORKFLOW_OBLIGATION_OPERATOR_VIEW_OVERDUE, 0))}",
                        f"Due Soon {int(counts.get(WORKFLOW_OBLIGATION_OPERATOR_VIEW_DUE_SOON, 0))}",
                        f"Needs Human {int(counts.get(WORKFLOW_OBLIGATION_OPERATOR_VIEW_NEEDS_HUMAN, 0))}",
                    ]
                )
            )

    def _refresh_queue_summary_cards(self) -> None:
        counts = summarize_queue_counts(self._proposal_records, self._question_records, self._obligation_records)
        for key, label in self.queue_summary_labels.items():
            label.setText(str(counts.get(key, 0)))

    def _refresh_queue_filter_options(self) -> None:
        if hasattr(self, "proposal_queue_controls"):
            self._populate_queue_filter_options(
                self.proposal_queue_controls,
                workflows=[record.workflow for record in self._proposal_records],
                item_types=[record.action_type for record in self._proposal_records],
                statuses=[record.status for record in self._proposal_records],
                priorities=[record.risk_level for record in self._proposal_records],
                preferred_status="Pending",
            )
        if hasattr(self, "question_queue_controls"):
            self._populate_queue_filter_options(
                self.question_queue_controls,
                workflows=[record.workflow for record in self._question_records],
                item_types=[record.question_type for record in self._question_records],
                statuses=[record.status for record in self._question_records],
                priorities=[record.urgency or "Normal" for record in self._question_records],
                preferred_status="Open",
            )
        if hasattr(self, "obligation_queue_controls"):
            self._populate_queue_filter_options(
                self.obligation_queue_controls,
                workflows=[record.workflow_type for record in self._obligation_records],
                item_types=[record.expected_event_type for record in self._obligation_records],
                statuses=[record.status for record in self._obligation_records],
                priorities=[record.severity for record in self._obligation_records],
            )

    def _populate_queue_filter_options(
        self,
        controls: dict[str, QWidget],
        *,
        workflows: list[str],
        item_types: list[str],
        statuses: list[str],
        priorities: list[str],
        preferred_status: str | None = None,
    ) -> None:
        self._reset_combo_values(controls["workflow"], ["All workflows", *self._ordered_workflows(workflows)])
        self._reset_combo_values(controls["item_type"], ["All types", *self._ordered_values(item_types)])
        self._reset_combo_values(
            controls["status"],
            ["All statuses", *self._ordered_values(statuses)],
            preferred_value=preferred_status,
        )
        self._reset_combo_values(controls["priority"], ["All priorities", *self._ordered_priorities(priorities)])
        self._reset_combo_values(controls["sort"], list(QUEUE_SORT_OPTIONS))

    def _reset_combo_values(
        self,
        widget: QWidget,
        values: list[str],
        *,
        preferred_value: str | None = None,
    ) -> None:
        if not isinstance(widget, QComboBox):
            return
        current = widget.currentText()
        unique_values = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            lower = text.lower()
            if lower in seen:
                continue
            seen.add(lower)
            unique_values.append(text)
        widget.blockSignals(True)
        widget.clear()
        widget.addItems(unique_values)
        if current and current in unique_values:
            widget.setCurrentText(current)
        elif preferred_value and preferred_value in unique_values:
            widget.setCurrentText(preferred_value)
        else:
            widget.setCurrentIndex(0)
        widget.blockSignals(False)

    def _ordered_workflows(self, workflows: list[str]) -> list[str]:
        ordered = [workflow for workflow in QUEUE_WORKFLOW_OPTIONS if workflow in workflows]
        extras = sorted(
            {
                str(workflow).strip()
                for workflow in workflows
                if str(workflow or "").strip() and workflow not in QUEUE_WORKFLOW_OPTIONS
            }
        )
        return [*ordered, *extras]

    def _ordered_values(self, values: list[str]) -> list[str]:
        return sorted({str(value).strip() for value in values if str(value or "").strip()})

    def _ordered_priorities(self, values: list[str]) -> list[str]:
        unique = {str(value).strip() for value in values if str(value or "").strip()}
        ordered = [name for name in ("Low", "Normal", "Medium", "High", "Urgent", "Critical") if name in unique]
        extras = sorted(unique.difference(ordered))
        return [*ordered, *extras]

    def _build_inventory_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.queue_summary_group = self._build_queue_summary_group()
        layout.addWidget(self.queue_summary_group)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        catalogue_group = QGroupBox("Preset Automation Catalogue")
        catalogue_layout = QVBoxLayout(catalogue_group)
        self.preset_tree = QTreeWidget()
        self.preset_tree.setColumnCount(17)
        self.preset_tree.setHeaderLabels(
            [
                "Automation Key",
                "Display Name",
                "Workflow",
                "Purpose / Description",
                "Level 2 Mode",
                "Level 3 Mode",
                "Risk",
                "Current Runtime State",
                "Legacy / Fenced",
                "Approval Required",
                "Email Send Allowed",
                "External Action Allowed",
                "Env / Policy Control",
                "Setting State",
                "Last Run",
                "Last Error",
                "Notes",
            ]
        )
        self.preset_tree.setAlternatingRowColors(True)
        self.preset_tree.setRootIsDecorated(False)
        self.preset_tree.setUniformRowHeights(True)
        self.preset_tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.preset_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.preset_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preset_tree.itemSelectionChanged.connect(self._handle_inventory_selection_changed)
        catalogue_layout.addWidget(self.preset_tree, 1)
        splitter.addWidget(catalogue_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        summary_group = QGroupBox("Preset Detail")
        summary_layout = QFormLayout(summary_group)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setSpacing(8)
        self.detail_key = QLabel("-")
        self.detail_name = QLabel("Select a preset")
        self.detail_workflow = QLabel("-")
        self.detail_risk = QLabel("-")
        self.detail_runtime = QLabel("-")
        self.detail_legacy = QLabel("-")
        self.detail_env = QLabel("-")
        self.detail_status = QLabel("-")
        self.detail_setting_state = QLabel("-")
        self.detail_last_run = QLabel("-")
        self.detail_last_error = QLabel("-")
        for label in (
            self.detail_key,
            self.detail_name,
            self.detail_workflow,
            self.detail_risk,
            self.detail_runtime,
            self.detail_legacy,
            self.detail_env,
            self.detail_status,
            self.detail_setting_state,
            self.detail_last_run,
            self.detail_last_error,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary_layout.addRow("Automation Key", self.detail_key)
        summary_layout.addRow("Display Name", self.detail_name)
        summary_layout.addRow("Workflow", self.detail_workflow)
        summary_layout.addRow("Risk Level", self.detail_risk)
        summary_layout.addRow("Current Runtime State", self.detail_runtime)
        summary_layout.addRow("Legacy / Fenced State", self.detail_legacy)
        summary_layout.addRow("Env / Policy Control", self.detail_env)
        summary_layout.addRow("Persisted / Default Setting State", self.detail_setting_state)
        summary_layout.addRow("Last Run", self.detail_last_run)
        summary_layout.addRow("Last Error", self.detail_last_error)
        summary_layout.addRow("Next Implementation Status", self.detail_status)
        detail_layout.addWidget(summary_group)

        detail_sections_group = QGroupBox("Level 2 / Level 3 Detail")
        detail_sections_layout = QVBoxLayout(detail_sections_group)
        detail_sections_layout.setContentsMargins(12, 12, 12, 12)
        detail_sections_layout.setSpacing(8)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setAcceptRichText(True)
        detail_sections_layout.addWidget(self.detail_text, 1)
        detail_layout.addWidget(detail_sections_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return tab

    def _build_activity_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        summary_group = QGroupBox("Last 24 Hours Summary")
        summary_layout = QGridLayout(summary_group)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setHorizontalSpacing(20)
        summary_layout.setVerticalSpacing(8)
        self.summary_labels: dict[str, QLabel] = {}
        summary_fields = [
            ("Runs in Last 24h", "runs"),
            ("Successful Runs", "success"),
            ("Failed Runs", "failed"),
            ("Proposals Created", "proposals"),
            ("Questions Created", "questions"),
            ("Actions Created", "actions"),
            ("Input Tokens", "input_tokens"),
            ("Output Tokens", "output_tokens"),
            ("Estimated Cost", "cost"),
        ]
        for index, (title, key) in enumerate(summary_fields):
            row = index // 3
            column = (index % 3) * 2
            title_label = QLabel(title)
            title_label.setStyleSheet("font-weight: 600;")
            value_label = QLabel("0")
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.summary_labels[key] = value_label
            summary_layout.addWidget(title_label, row, column)
            summary_layout.addWidget(value_label, row, column + 1)
        layout.addWidget(summary_group)

        self.activity_empty_label = QLabel(
            "No automation runs recorded yet. Legacy automation is disabled and Level 2 execution is not enabled."
        )
        self.activity_empty_label.setWordWrap(True)
        self.activity_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.activity_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        runs_group = QGroupBox("Recent Runs")
        runs_layout = QVBoxLayout(runs_group)
        self.runs_table = QTableWidget(0, 12)
        self.runs_table.setHorizontalHeaderLabels(
            [
                "Run ID",
                "Started At",
                "Automation Key",
                "Status",
                "Trigger",
                "Provider / Model",
                "Proposals",
                "Questions",
                "Actions",
                "Input Tokens",
                "Output Tokens",
                "Est. Cost",
            ]
        )
        self.runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.runs_table.setAlternatingRowColors(True)
        self.runs_table.itemSelectionChanged.connect(self._handle_run_selection_changed)
        runs_layout.addWidget(self.runs_table, 1)
        splitter.addWidget(runs_group)

        event_host = QWidget()
        event_layout = QVBoxLayout(event_host)
        event_layout.setContentsMargins(0, 0, 0, 0)
        event_layout.setSpacing(12)

        self.event_scope_label = QLabel("Recent events for the last 24 hours")
        self.event_scope_label.setStyleSheet("font-weight: 600;")
        event_layout.addWidget(self.event_scope_label)

        events_group = QGroupBox("Event Log")
        events_layout = QVBoxLayout(events_group)
        self.events_table = QTableWidget(0, 6)
        self.events_table.setHorizontalHeaderLabels(
            [
                "Timestamp",
                "Automation Key",
                "Event Type",
                "Summary",
                "Target",
                "Event JSON Preview",
            ]
        )
        self.events_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.events_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.events_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.events_table.setAlternatingRowColors(True)
        self.events_table.itemSelectionChanged.connect(self._handle_event_selection_changed)
        events_layout.addWidget(self.events_table, 1)
        event_layout.addWidget(events_group, 1)

        preview_group = QGroupBox("Event JSON Detail")
        preview_layout = QVBoxLayout(preview_group)
        self.event_json_preview = QTextEdit()
        self.event_json_preview.setReadOnly(True)
        preview_layout.addWidget(self.event_json_preview, 1)
        event_layout.addWidget(preview_group, 1)

        splitter.addWidget(event_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        return tab

    def _build_provider_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        status_group = QGroupBox("LLM Provider Status")
        status_layout = QFormLayout(status_group)
        status_layout.setContentsMargins(12, 12, 12, 12)
        status_layout.setSpacing(8)
        self.provider_name_label = QLabel("-")
        self.provider_model_label = QLabel("-")
        self.provider_safe_mode_label = QLabel("-")
        self.provider_openai_key_label = QLabel("-")
        self.provider_openai_model_label = QLabel("-")
        self.provider_ollama_model_label = QLabel("-")
        self.provider_health_label = QLabel("-")
        self.provider_error_label = QLabel("-")
        for label in (
            self.provider_name_label,
            self.provider_model_label,
            self.provider_safe_mode_label,
            self.provider_openai_key_label,
            self.provider_openai_model_label,
            self.provider_ollama_model_label,
            self.provider_health_label,
            self.provider_error_label,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_layout.addRow("Configured Provider", self.provider_name_label)
        status_layout.addRow("Configured Model", self.provider_model_label)
        status_layout.addRow("Safe Mode", self.provider_safe_mode_label)
        status_layout.addRow("OpenAI Key Present", self.provider_openai_key_label)
        status_layout.addRow("OpenAI Model", self.provider_openai_model_label)
        status_layout.addRow("Ollama Model", self.provider_ollama_model_label)
        status_layout.addRow("Health Check Status", self.provider_health_label)
        status_layout.addRow("Health Check Error", self.provider_error_label)
        layout.addWidget(status_group)

        notes_group = QGroupBox("Provider Notes")
        notes_layout = QVBoxLayout(notes_group)
        self.provider_notes_text = QTextEdit()
        self.provider_notes_text.setReadOnly(True)
        notes_layout.addWidget(self.provider_notes_text, 1)
        layout.addWidget(notes_group, 1)
        return tab

    def _build_policies_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.policies_empty_label = QLabel(
            "Automation policies are currently static defaults. Editing is not enabled in this slice."
        )
        self.policies_empty_label.setWordWrap(True)
        self.policies_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.policies_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        policies_group = QGroupBox("Policy Set")
        policies_layout = QVBoxLayout(policies_group)
        self.policies_table = QTableWidget(0, 5)
        self.policies_table.setHorizontalHeaderLabels(
            [
                "Policy Key",
                "Value",
                "Description",
                "Enforcement Status",
                "Notes",
            ]
        )
        self.policies_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.policies_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.policies_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.policies_table.setAlternatingRowColors(True)
        self.policies_table.itemSelectionChanged.connect(self._handle_policy_selection_changed)
        policies_layout.addWidget(self.policies_table, 1)
        splitter.addWidget(policies_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        detail_group = QGroupBox("Policy Detail")
        detail_form = QFormLayout(detail_group)
        detail_form.setContentsMargins(12, 12, 12, 12)
        detail_form.setSpacing(8)
        self.policy_detail_key = QLabel("-")
        self.policy_detail_value = QLabel("-")
        self.policy_detail_enforcement = QLabel("-")
        for label in (
            self.policy_detail_key,
            self.policy_detail_value,
            self.policy_detail_enforcement,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_form.addRow("Policy Key", self.policy_detail_key)
        detail_form.addRow("Value", self.policy_detail_value)
        detail_form.addRow("Enforcement", self.policy_detail_enforcement)
        detail_layout.addWidget(detail_group)

        description_group = QGroupBox("Description")
        description_layout = QVBoxLayout(description_group)
        self.policy_description_text = QTextEdit()
        self.policy_description_text.setReadOnly(True)
        description_layout.addWidget(self.policy_description_text, 1)
        detail_layout.addWidget(description_group, 1)

        notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes_group)
        self.policy_notes_text = QTextEdit()
        self.policy_notes_text.setReadOnly(True)
        notes_layout.addWidget(self.policy_notes_text, 1)
        detail_layout.addWidget(notes_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return tab

    def _build_memory_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        summary_group = QGroupBox("Automation Memory Summary")
        summary_layout = QGridLayout(summary_group)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setHorizontalSpacing(20)
        summary_layout.setVerticalSpacing(8)
        self.memory_summary_labels: dict[str, QLabel] = {}
        memory_fields = [
            ("Total Records", "total"),
            ("Active Records", "active"),
            ("Inactive Records", "inactive"),
            ("Expired Active Records", "expiring"),
        ]
        for index, (title, key) in enumerate(memory_fields):
            title_label = QLabel(title)
            title_label.setStyleSheet("font-weight: 600;")
            value_label = QLabel("0")
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.memory_summary_labels[key] = value_label
            summary_layout.addWidget(title_label, 0, index * 2)
            summary_layout.addWidget(value_label, 0, index * 2 + 1)
        layout.addWidget(summary_group)

        self.memory_empty_label = QLabel(
            "No automation memory records yet. Level 2 memory writes are not enabled."
        )
        self.memory_empty_label.setWordWrap(True)
        self.memory_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.memory_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        memory_group = QGroupBox("Memory Records")
        memory_layout = QVBoxLayout(memory_group)
        self.memory_table = QTableWidget(0, 10)
        self.memory_table.setHorizontalHeaderLabels(
            [
                "Scope Type",
                "Scope ID",
                "Memory Type",
                "Content Summary",
                "Source",
                "Confidence",
                "Created By",
                "Created At",
                "Expires At",
                "Active",
            ]
        )
        self.memory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.memory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.memory_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.memory_table.setAlternatingRowColors(True)
        self.memory_table.itemSelectionChanged.connect(self._handle_memory_selection_changed)
        memory_layout.addWidget(self.memory_table, 1)
        splitter.addWidget(memory_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        detail_group = QGroupBox("Memory Detail")
        detail_form = QFormLayout(detail_group)
        detail_form.setContentsMargins(12, 12, 12, 12)
        detail_form.setSpacing(8)
        self.memory_detail_scope = QLabel("-")
        self.memory_detail_type = QLabel("-")
        self.memory_detail_source = QLabel("-")
        self.memory_detail_confidence = QLabel("-")
        self.memory_detail_lifecycle = QLabel("-")
        for label in (
            self.memory_detail_scope,
            self.memory_detail_type,
            self.memory_detail_source,
            self.memory_detail_confidence,
            self.memory_detail_lifecycle,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_form.addRow("Scope", self.memory_detail_scope)
        detail_form.addRow("Memory Type", self.memory_detail_type)
        detail_form.addRow("Source", self.memory_detail_source)
        detail_form.addRow("Confidence", self.memory_detail_confidence)
        detail_form.addRow("Lifecycle", self.memory_detail_lifecycle)
        detail_layout.addWidget(detail_group)

        text_group = QGroupBox("Content Text")
        text_layout = QVBoxLayout(text_group)
        self.memory_content_text = QTextEdit()
        self.memory_content_text.setReadOnly(True)
        text_layout.addWidget(self.memory_content_text, 1)
        detail_layout.addWidget(text_group, 1)

        json_group = QGroupBox("Content JSON")
        json_layout = QVBoxLayout(json_group)
        self.memory_content_json = QTextEdit()
        self.memory_content_json.setReadOnly(True)
        json_layout.addWidget(self.memory_content_json, 1)
        detail_layout.addWidget(json_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return tab

    def _build_pending_approvals_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        summary_group = QGroupBox("Pending Approval Summary")
        summary_layout = QGridLayout(summary_group)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setHorizontalSpacing(20)
        summary_layout.setVerticalSpacing(8)
        self.pending_summary_labels: dict[str, QLabel] = {}
        pending_fields = [
            ("Pending Proposals", "pending_proposals"),
            ("Open Questions", "open_questions"),
        ]
        for index, (title, key) in enumerate(pending_fields):
            title_label = QLabel(title)
            title_label.setStyleSheet("font-weight: 600;")
            value_label = QLabel("0")
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.pending_summary_labels[key] = value_label
            summary_layout.addWidget(title_label, 0, index * 2)
            summary_layout.addWidget(value_label, 0, index * 2 + 1)
        layout.addWidget(summary_group)

        self.proposal_queue_controls: dict[str, QWidget] = {}
        layout.addWidget(
            self._build_queue_controls(
                item_type_label="Action Type",
                priority_label="Risk",
                controls=self.proposal_queue_controls,
                apply_callback=self._apply_proposal_queue_filters,
            )
        )

        self.pending_empty_label = QLabel("No automation proposals recorded yet.")
        self.pending_empty_label.setWordWrap(True)
        self.pending_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.pending_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        proposals_group = QGroupBox("Proposal Review Queue")
        proposals_layout = QVBoxLayout(proposals_group)
        self.proposals_table = QTableWidget(0, 11)
        self.proposals_table.setHorizontalHeaderLabels(
            [
                "Proposal ID",
                "Created",
                "Workflow",
                "Action Type",
                "Target Summary",
                "Risk",
                "Confidence",
                "Cash Flow",
                "Source",
                "Status",
                "Age",
            ]
        )
        self.proposals_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.proposals_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.proposals_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.proposals_table.setAlternatingRowColors(True)
        self.proposals_table.itemSelectionChanged.connect(self._handle_proposal_selection_changed)
        proposals_layout.addWidget(self.proposals_table, 1)
        splitter.addWidget(proposals_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        detail_group = QGroupBox("Proposal Detail")
        detail_form = QFormLayout(detail_group)
        detail_form.setContentsMargins(12, 12, 12, 12)
        detail_form.setSpacing(8)
        self.proposal_detail_meta = QLabel("-")
        self.proposal_detail_approval = QLabel("-")
        self.proposal_detail_blocked = QLabel("-")
        self.proposal_detail_status = QLabel("-")
        for label in (
            self.proposal_detail_meta,
            self.proposal_detail_approval,
            self.proposal_detail_blocked,
            self.proposal_detail_status,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_form.addRow("Proposal", self.proposal_detail_meta)
        detail_form.addRow("Approval / Level 2", self.proposal_detail_approval)
        detail_form.addRow("Blocked Reason", self.proposal_detail_blocked)
        detail_form.addRow("Lifecycle", self.proposal_detail_status)
        detail_layout.addWidget(detail_group)

        action_group = QGroupBox("Approval Actions")
        action_layout = QHBoxLayout(action_group)
        action_layout.setContentsMargins(12, 12, 12, 12)
        self.proposal_approve_button = QPushButton("Approve")
        self.proposal_reject_button = QPushButton("Reject")
        self.proposal_apply_button = QPushButton("Apply Approved Proposal")
        self.proposal_approve_button.clicked.connect(self._handle_proposal_approve_clicked)
        self.proposal_reject_button.clicked.connect(self._handle_proposal_reject_clicked)
        self.proposal_apply_button.clicked.connect(self._handle_proposal_apply_clicked)
        for button in (
            self.proposal_approve_button,
            self.proposal_reject_button,
            self.proposal_apply_button,
        ):
            button.setEnabled(False)
            action_layout.addWidget(button)
        detail_layout.addWidget(action_group)

        review_group = QGroupBox("Receive Quotes Review")
        review_layout = QVBoxLayout(review_group)
        self.proposal_review_summary = QTextEdit()
        self.proposal_review_summary.setReadOnly(True)
        review_layout.addWidget(self.proposal_review_summary, 1)
        detail_layout.addWidget(review_group, 1)

        proposed_change_group = QGroupBox("ProposedChangeJson")
        proposed_change_layout = QVBoxLayout(proposed_change_group)
        self.proposal_change_json = QTextEdit()
        self.proposal_change_json.setReadOnly(True)
        proposed_change_layout.addWidget(self.proposal_change_json, 1)
        detail_layout.addWidget(proposed_change_group, 1)

        evidence_group = QGroupBox("EvidenceJson")
        evidence_layout = QVBoxLayout(evidence_group)
        self.proposal_evidence_json = QTextEdit()
        self.proposal_evidence_json.setReadOnly(True)
        evidence_layout.addWidget(self.proposal_evidence_json, 1)
        detail_layout.addWidget(evidence_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return tab

    def _build_obligations_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        operator_view_group = QGroupBox("Operator Views")
        operator_view_layout = QHBoxLayout(operator_view_group)
        operator_view_layout.setContentsMargins(12, 12, 12, 12)
        operator_view_layout.setSpacing(8)
        operator_view_layout.addWidget(QLabel("View"))
        self.obligation_operator_view_combo = QComboBox()
        for view_name in WORKFLOW_OBLIGATION_OPERATOR_VIEW_ORDER:
            self.obligation_operator_view_combo.addItem(
                WORKFLOW_OBLIGATION_OPERATOR_VIEW_LABELS.get(view_name, view_name.title()),
                userData=view_name,
            )
        self.obligation_operator_view_combo.currentIndexChanged.connect(self._apply_obligation_queue_filters)
        operator_view_layout.addWidget(self.obligation_operator_view_combo, 1)
        self.obligation_operator_view_counts_label = QLabel("Today 0 | Overdue 0 | Due Soon 0 | Needs Human 0")
        self.obligation_operator_view_counts_label.setWordWrap(True)
        self.obligation_operator_view_counts_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        operator_view_layout.addWidget(self.obligation_operator_view_counts_label, 2)
        layout.addWidget(operator_view_group)

        self.obligation_queue_controls: dict[str, QWidget] = {}
        layout.addWidget(
            self._build_queue_controls(
                item_type_label="Expected Event",
                priority_label="Severity",
                controls=self.obligation_queue_controls,
                apply_callback=self._apply_obligation_queue_filters,
            )
        )

        self.obligations_empty_label = QLabel(
            "No active workflow obligations recorded yet. This board tracks expected next events and missing follow-up."
        )
        self.obligations_empty_label.setWordWrap(True)
        self.obligations_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.obligations_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        obligations_group = QGroupBox("Workflow Obligation Review Board")
        obligations_layout = QVBoxLayout(obligations_group)
        self.obligations_table = QTableWidget(0, 11)
        self.obligations_table.setHorizontalHeaderLabels(
            [
                "Obligation ID",
                "Expected By",
                "Overdue Age",
                "Workflow",
                "Expected Event",
                "Source Summary",
                "Severity",
                "Owner",
                "Status",
                "Last Proposal / Check",
                "Cash Flow",
            ]
        )
        self.obligations_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.obligations_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.obligations_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.obligations_table.setAlternatingRowColors(True)
        self.obligations_table.itemSelectionChanged.connect(self._handle_obligation_selection_changed)
        obligations_layout.addWidget(self.obligations_table, 1)
        splitter.addWidget(obligations_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        detail_group = QGroupBox("Obligation Detail")
        detail_form = QFormLayout(detail_group)
        detail_form.setContentsMargins(12, 12, 12, 12)
        detail_form.setSpacing(8)
        self.obligation_detail_meta = QLabel("-")
        self.obligation_detail_source = QLabel("-")
        self.obligation_detail_lifecycle = QLabel("-")
        self.obligation_detail_owner = QLabel("-")
        for label in (
            self.obligation_detail_meta,
            self.obligation_detail_source,
            self.obligation_detail_lifecycle,
            self.obligation_detail_owner,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_form.addRow("Obligation", self.obligation_detail_meta)
        detail_form.addRow("Linked Source", self.obligation_detail_source)
        detail_form.addRow("Lifecycle", self.obligation_detail_lifecycle)
        detail_form.addRow("Owner / Escalation", self.obligation_detail_owner)
        detail_layout.addWidget(detail_group)

        action_group = QGroupBox("Safe Obligation Actions")
        action_layout = QGridLayout(action_group)
        action_layout.setContentsMargins(12, 12, 12, 12)
        action_layout.setHorizontalSpacing(8)
        action_layout.setVerticalSpacing(8)
        self.obligation_edit_due_button = QPushButton("Edit Due Date")
        self.obligation_edit_severity_button = QPushButton("Edit Severity")
        self.obligation_edit_text_button = QPushButton("Edit Title / Description")
        self.obligation_snooze_button = QPushButton("Snooze")
        self.obligation_mark_satisfied_button = QPushButton("Mark Satisfied")
        self.obligation_dismiss_button = QPushButton("Dismiss")
        self.obligation_add_note_button = QPushButton("Add Note")
        obligation_buttons = [
            self.obligation_edit_due_button,
            self.obligation_edit_severity_button,
            self.obligation_edit_text_button,
            self.obligation_snooze_button,
            self.obligation_mark_satisfied_button,
            self.obligation_dismiss_button,
            self.obligation_add_note_button,
        ]
        for button in obligation_buttons:
            button.setEnabled(False)
        self.obligation_edit_due_button.clicked.connect(self._handle_obligation_edit_due_clicked)
        self.obligation_edit_severity_button.clicked.connect(self._handle_obligation_edit_severity_clicked)
        self.obligation_edit_text_button.clicked.connect(self._handle_obligation_edit_text_clicked)
        self.obligation_snooze_button.clicked.connect(self._handle_obligation_snooze_clicked)
        self.obligation_mark_satisfied_button.clicked.connect(self._handle_obligation_mark_satisfied_clicked)
        self.obligation_dismiss_button.clicked.connect(self._handle_obligation_dismiss_clicked)
        self.obligation_add_note_button.clicked.connect(self._handle_obligation_add_note_clicked)
        for index, button in enumerate(obligation_buttons):
            action_layout.addWidget(button, index // 3, index % 3)
        detail_layout.addWidget(action_group)

        review_group = QGroupBox("Obligation Review Summary")
        review_layout = QVBoxLayout(review_group)
        self.obligation_review_summary = QTextEdit()
        self.obligation_review_summary.setReadOnly(True)
        review_layout.addWidget(self.obligation_review_summary, 1)
        detail_layout.addWidget(review_group, 1)

        change_group = QGroupBox("Change Log Summary")
        change_layout = QVBoxLayout(change_group)
        self.obligation_change_log_text = QTextEdit()
        self.obligation_change_log_text.setReadOnly(True)
        change_layout.addWidget(self.obligation_change_log_text, 1)
        detail_layout.addWidget(change_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return tab

    def _build_outbound_drafts_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        controls_group = QGroupBox("Outbound Draft Filters")
        controls_layout = QHBoxLayout(controls_group)
        controls_layout.setContentsMargins(12, 12, 12, 12)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(QLabel("Status"))
        self.outbound_draft_status_combo = QComboBox()
        self.outbound_draft_status_combo.currentIndexChanged.connect(self._apply_outbound_draft_filters)
        controls_layout.addWidget(self.outbound_draft_status_combo)
        controls_layout.addWidget(QLabel("Template"))
        self.outbound_draft_template_combo = QComboBox()
        self.outbound_draft_template_combo.currentIndexChanged.connect(self._apply_outbound_draft_filters)
        controls_layout.addWidget(self.outbound_draft_template_combo, 1)
        controls_layout.addWidget(QLabel("Recipient"))
        self.outbound_draft_recipient_search = QLineEdit()
        self.outbound_draft_recipient_search.setPlaceholderText("Search recipient")
        self.outbound_draft_recipient_search.textChanged.connect(self._apply_outbound_draft_filters)
        controls_layout.addWidget(self.outbound_draft_recipient_search, 1)
        layout.addWidget(controls_group)

        self.outbound_drafts_empty_label = QLabel("No Level 3 outbound drafts recorded yet.")
        self.outbound_drafts_empty_label.setWordWrap(True)
        self.outbound_drafts_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.outbound_drafts_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        drafts_group = QGroupBox("Prepared Outbound Drafts")
        drafts_layout = QVBoxLayout(drafts_group)
        self.outbound_drafts_table = QTableWidget(0, 10)
        self.outbound_drafts_table.setHorizontalHeaderLabels(
            [
                "Log ID",
                "Created",
                "Template",
                "Status",
                "Recipient",
                "Subject",
                "Source",
                "SentAt",
                "Provider ID?",
                "Allowlist",
            ]
        )
        self.outbound_drafts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.outbound_drafts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.outbound_drafts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.outbound_drafts_table.setAlternatingRowColors(True)
        self.outbound_drafts_table.itemSelectionChanged.connect(self._handle_outbound_draft_selection_changed)
        drafts_layout.addWidget(self.outbound_drafts_table, 1)
        splitter.addWidget(drafts_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        detail_group = QGroupBox("Outbound Draft Detail")
        detail_form = QFormLayout(detail_group)
        detail_form.setContentsMargins(12, 12, 12, 12)
        detail_form.setSpacing(8)
        self.outbound_draft_detail_meta = QLabel("-")
        self.outbound_draft_detail_recipient = QLabel("-")
        self.outbound_draft_detail_source = QLabel("-")
        self.outbound_draft_detail_status = QLabel("-")
        self.outbound_draft_detail_safety = QLabel("-")
        for label in (
            self.outbound_draft_detail_meta,
            self.outbound_draft_detail_recipient,
            self.outbound_draft_detail_source,
            self.outbound_draft_detail_status,
            self.outbound_draft_detail_safety,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_form.addRow("Draft", self.outbound_draft_detail_meta)
        detail_form.addRow("Recipient", self.outbound_draft_detail_recipient)
        detail_form.addRow("Source", self.outbound_draft_detail_source)
        detail_form.addRow("Status", self.outbound_draft_detail_status)
        detail_form.addRow("Safety", self.outbound_draft_detail_safety)
        detail_layout.addWidget(detail_group)

        send_action_group = QGroupBox("Approved Send Action")
        send_action_layout = QVBoxLayout(send_action_group)
        send_action_layout.setContentsMargins(12, 12, 12, 12)
        self.outbound_draft_send_button = QPushButton("Send Selected Draft")
        self.outbound_draft_send_button.setEnabled(False)
        self.outbound_draft_send_button.clicked.connect(self._handle_outbound_draft_send_clicked)
        send_action_note = QLabel(
            "Single selected Prepared draft only. The approved-send wrapper still enforces test mode, allowlist, "
            "recipient/body checks, and supported template/source rules."
        )
        send_action_note.setWordWrap(True)
        send_action_note.setStyleSheet("color: #555;")
        send_action_layout.addWidget(self.outbound_draft_send_button)
        send_action_layout.addWidget(send_action_note)
        detail_layout.addWidget(send_action_group)

        body_group = QGroupBox("Body Preview")
        body_layout = QVBoxLayout(body_group)
        self.outbound_draft_body_preview = QTextEdit()
        self.outbound_draft_body_preview.setReadOnly(True)
        body_layout.addWidget(self.outbound_draft_body_preview, 1)
        detail_layout.addWidget(body_group, 1)

        json_group = QGroupBox("Review Summary")
        json_layout = QVBoxLayout(json_group)
        self.outbound_draft_review_json = QTextEdit()
        self.outbound_draft_review_json.setReadOnly(True)
        json_layout.addWidget(self.outbound_draft_review_json, 1)
        detail_layout.addWidget(json_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return tab

    def _build_questions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.question_queue_controls: dict[str, QWidget] = {}
        layout.addWidget(
            self._build_queue_controls(
                item_type_label="Question Type",
                priority_label="Urgency",
                controls=self.question_queue_controls,
                apply_callback=self._apply_question_queue_filters,
            )
        )

        self.questions_empty_label = QLabel("No open automation questions.")
        self.questions_empty_label.setWordWrap(True)
        self.questions_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.questions_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        questions_group = QGroupBox("Automation Questions")
        questions_layout = QVBoxLayout(questions_group)
        self.questions_table = QTableWidget(0, 10)
        self.questions_table.setHorizontalHeaderLabels(
            [
                "Question ID",
                "Created",
                "Workflow",
                "Question Type",
                "Urgency",
                "Target Summary",
                "Source",
                "Uncertainty",
                "Status",
                "Age",
            ]
        )
        self.questions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.questions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.questions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.questions_table.setAlternatingRowColors(True)
        self.questions_table.itemSelectionChanged.connect(self._handle_question_selection_changed)
        questions_layout.addWidget(self.questions_table, 1)
        splitter.addWidget(questions_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        detail_group = QGroupBox("Question Detail")
        detail_form = QFormLayout(detail_group)
        detail_form.setContentsMargins(12, 12, 12, 12)
        detail_form.setSpacing(8)
        self.question_detail_meta = QLabel("-")
        self.question_detail_requirements = QLabel("-")
        self.question_detail_answer = QLabel("-")
        for label in (
            self.question_detail_meta,
            self.question_detail_requirements,
            self.question_detail_answer,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_form.addRow("Question", self.question_detail_meta)
        detail_form.addRow("Requirements", self.question_detail_requirements)
        detail_form.addRow("Answer", self.question_detail_answer)
        detail_layout.addWidget(detail_group)

        choices_group = QGroupBox("ChoicesJson")
        choices_layout = QVBoxLayout(choices_group)
        self.question_choices_json = QTextEdit()
        self.question_choices_json.setReadOnly(True)
        choices_layout.addWidget(self.question_choices_json, 1)
        detail_layout.addWidget(choices_group, 1)

        answer_group = QGroupBox("Answer Controls")
        answer_layout = QFormLayout(answer_group)
        answer_layout.setContentsMargins(12, 12, 12, 12)
        answer_layout.setSpacing(8)
        self.question_selected_option_combo = QComboBox()
        self.question_selected_workflow_combo = QComboBox()
        self.question_selected_workflow_combo.setEditable(True)
        self.question_selected_workflow_combo.addItem("")
        for workflow in QUEUE_WORKFLOW_OPTIONS:
            self.question_selected_workflow_combo.addItem(workflow)
        self.question_selected_intent_input = QLineEdit()
        self.question_selected_target_type_input = QLineEdit()
        self.question_selected_target_id_input = QLineEdit()
        self.question_save_routing_memory_checkbox = QCheckBox("Save as routing memory")
        self.question_followup_required_checkbox = QCheckBox("Follow-up required")
        self.question_request_more_info_checkbox = QCheckBox("Request more info needed")
        self.question_operator_note_text = QTextEdit()
        self.question_operator_note_text.setPlaceholderText("Operator note / how I know")
        self.question_operator_note_text.setMaximumHeight(110)
        self.question_receipt_resolution_json = QTextEdit()
        self.question_receipt_resolution_json.setPlaceholderText(
            '{\n'
            '  "answer_type": "packing_slip_receipt_resolution",\n'
            '  "selected_purchase_order_id": 0,\n'
            '  "line_resolutions": [\n'
            '    {\n'
            '      "packing_slip_line_ref": "line-1",\n'
            '      "selected_purchase_order_item_id": 0,\n'
            '      "received_qty": 0.0,\n'
            '      "resolution_basis": "operator_confirmed_match",\n'
            '      "operator_note": "Why this line matches.",\n'
            '      "allow_overage": false\n'
            '    }\n'
            '  ],\n'
            '  "packing_slip_number": "",\n'
            '  "received_date": "YYYY-MM-DD",\n'
            '  "operator_note": "",\n'
            '  "answered_by": "Automation Center"\n'
            '}'
        )
        self.question_receipt_resolution_json.setMaximumHeight(180)
        self.question_po_eta_resolution_json = QTextEdit()
        self.question_po_eta_resolution_json.setPlaceholderText(
            '{\n'
            '  "answer_type": "po_eta_status_resolution",\n'
            '  "selected_purchase_order_id": 0,\n'
            '  "normalized_intent": "ETA_UPDATE",\n'
            '  "eta_date": "YYYY-MM-DD",\n'
            '  "parts_ready": false,\n'
            '  "backorder_hint": false,\n'
            '  "pickup_note": "",\n'
            '  "vendor_message_summary": "",\n'
            '  "operator_note": "",\n'
            '  "how_i_know": "",\n'
            '  "answered_by": "Automation Center"\n'
            '}'
        )
        self.question_po_eta_resolution_json.setMaximumHeight(180)
        self.question_vendor_invoice_resolution_json = QTextEdit()
        self.question_vendor_invoice_resolution_json.setPlaceholderText(
            '{\n'
            '  "answer_type": "vendor_invoice_reconciliation_resolution",\n'
            '  "resolution_mode": "po_backed",\n'
            '  "selected_vendor_id": 0,\n'
            '  "selected_purchase_order_id": 0,\n'
            '  "selected_receipt_ids": [],\n'
            '  "invoice_number": "",\n'
            '  "invoice_total": 0.0,\n'
            '  "packing_slip_number": "",\n'
            '  "reconciliation_outcome": "MATCHED_PO_ONLY",\n'
            '  "customer_billing_status": "unknown",\n'
            '  "operator_note": "",\n'
            '  "how_i_know": "",\n'
            '  "answered_by": "Automation Center"\n'
            '}'
        )
        self.question_vendor_invoice_resolution_json.setMaximumHeight(180)
        answer_layout.addRow("Selected Option", self.question_selected_option_combo)
        answer_layout.addRow("Selected Workflow", self.question_selected_workflow_combo)
        answer_layout.addRow("Selected Intent", self.question_selected_intent_input)
        answer_layout.addRow("Selected Target Type", self.question_selected_target_type_input)
        answer_layout.addRow("Selected Target ID", self.question_selected_target_id_input)
        answer_layout.addRow("", self.question_save_routing_memory_checkbox)
        answer_layout.addRow("", self.question_followup_required_checkbox)
        answer_layout.addRow("", self.question_request_more_info_checkbox)
        answer_layout.addRow("Operator Note", self.question_operator_note_text)
        answer_layout.addRow("Receipt Resolution JSON", self.question_receipt_resolution_json)
        answer_layout.addRow("PO ETA Resolution JSON", self.question_po_eta_resolution_json)
        answer_layout.addRow("Vendor Invoice Resolution JSON", self.question_vendor_invoice_resolution_json)
        answer_button_row = QHBoxLayout()
        self.question_answer_button = QPushButton("Answer Question")
        self.question_dismiss_button = QPushButton("Dismiss Question")
        self.question_create_revised_receipt_button = QPushButton("Create Revised Receipt Proposal")
        self.question_create_revised_po_eta_button = QPushButton("Create Revised PO ETA Proposal")
        self.question_create_revised_vendor_invoice_button = QPushButton("Create Revised Vendor Invoice Proposal")
        self.question_answer_button.clicked.connect(self._handle_question_answer_clicked)
        self.question_dismiss_button.clicked.connect(self._handle_question_dismiss_clicked)
        self.question_create_revised_receipt_button.clicked.connect(self._handle_question_create_revised_receipt_clicked)
        self.question_create_revised_po_eta_button.clicked.connect(self._handle_question_create_revised_po_eta_clicked)
        self.question_create_revised_vendor_invoice_button.clicked.connect(
            self._handle_question_create_revised_vendor_invoice_clicked
        )
        answer_button_row.addWidget(self.question_answer_button)
        answer_button_row.addWidget(self.question_dismiss_button)
        answer_button_row.addWidget(self.question_create_revised_receipt_button)
        answer_button_row.addWidget(self.question_create_revised_po_eta_button)
        answer_button_row.addWidget(self.question_create_revised_vendor_invoice_button)
        answer_layout.addRow("", answer_button_row)
        detail_layout.addWidget(answer_group)

        answer_json_group = QGroupBox("Answer JSON")
        answer_json_layout = QVBoxLayout(answer_json_group)
        self.question_answer_json = QTextEdit()
        self.question_answer_json.setReadOnly(True)
        answer_json_layout.addWidget(self.question_answer_json, 1)
        detail_layout.addWidget(answer_json_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return tab

    def _build_intake_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.intake_empty_label = QLabel("No inbound messages recorded yet. Inbox polling is not enabled.")
        self.intake_empty_label.setWordWrap(True)
        self.intake_empty_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.intake_empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        messages_group = QGroupBox("Inbound Messages")
        messages_layout = QVBoxLayout(messages_group)
        self.inbound_messages_table = QTableWidget(0, 9)
        self.inbound_messages_table.setHorizontalHeaderLabels(
            [
                "Message ID",
                "Sender",
                "Subject",
                "Workflow Guess",
                "Intent Guess",
                "Confidence",
                "Status",
                "Received / Imported",
                "Attachments",
            ]
        )
        self.inbound_messages_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.inbound_messages_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.inbound_messages_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.inbound_messages_table.setAlternatingRowColors(True)
        self.inbound_messages_table.itemSelectionChanged.connect(self._handle_inbound_message_selection_changed)
        messages_layout.addWidget(self.inbound_messages_table, 1)
        splitter.addWidget(messages_group)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        message_group = QGroupBox("Inbound Message Detail")
        message_form = QFormLayout(message_group)
        message_form.setContentsMargins(12, 12, 12, 12)
        message_form.setSpacing(8)
        self.inbound_message_detail_meta = QLabel("-")
        self.inbound_message_detail_timing = QLabel("-")
        self.inbound_message_detail_status = QLabel("-")
        for label in (
            self.inbound_message_detail_meta,
            self.inbound_message_detail_timing,
            self.inbound_message_detail_status,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message_form.addRow("Message", self.inbound_message_detail_meta)
        message_form.addRow("Timing", self.inbound_message_detail_timing)
        message_form.addRow("Status", self.inbound_message_detail_status)
        detail_layout.addWidget(message_group)

        excerpt_group = QGroupBox("Body Excerpt")
        excerpt_layout = QVBoxLayout(excerpt_group)
        self.inbound_body_excerpt = QTextEdit()
        self.inbound_body_excerpt.setReadOnly(True)
        excerpt_layout.addWidget(self.inbound_body_excerpt, 1)
        detail_layout.addWidget(excerpt_group, 1)

        classification_group = QGroupBox("ClassificationJson")
        classification_layout = QVBoxLayout(classification_group)
        self.inbound_classification_json = QTextEdit()
        self.inbound_classification_json.setReadOnly(True)
        classification_layout.addWidget(self.inbound_classification_json, 1)
        detail_layout.addWidget(classification_group, 1)

        attachments_group = QGroupBox("Attachments")
        attachments_layout = QVBoxLayout(attachments_group)
        self.inbound_attachments_table = QTableWidget(0, 6)
        self.inbound_attachments_table.setHorizontalHeaderLabels(
            [
                "Attachment ID",
                "Filename",
                "Mime Type",
                "File Size",
                "Status",
                "Imported At",
            ]
        )
        self.inbound_attachments_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.inbound_attachments_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.inbound_attachments_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.inbound_attachments_table.setAlternatingRowColors(True)
        self.inbound_attachments_table.itemSelectionChanged.connect(self._handle_inbound_attachment_selection_changed)
        attachments_layout.addWidget(self.inbound_attachments_table, 1)
        attachments_button_row = QHBoxLayout()
        attachments_button_row.addStretch(1)
        self.inbound_create_attachment_review_button = QPushButton("Create Attachment Review Question")
        self.inbound_create_attachment_review_button.clicked.connect(self._handle_create_attachment_review_clicked)
        attachments_button_row.addWidget(self.inbound_create_attachment_review_button)
        self.inbound_extract_attachment_button = QPushButton("Extract Text")
        self.inbound_extract_attachment_button.clicked.connect(self._handle_extract_attachment_clicked)
        attachments_button_row.addWidget(self.inbound_extract_attachment_button)
        attachments_layout.addLayout(attachments_button_row)
        detail_layout.addWidget(attachments_group, 1)

        extraction_group = QGroupBox("ExtractionJson")
        extraction_layout = QVBoxLayout(extraction_group)
        self.inbound_extraction_json = QTextEdit()
        self.inbound_extraction_json.setReadOnly(True)
        extraction_layout.addWidget(self.inbound_extraction_json, 1)
        detail_layout.addWidget(extraction_group, 1)

        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 3)
        return tab

    def refresh(self) -> None:
        self.refresh_data()

    def refresh_data(self, surface_key: str | None = None) -> None:
        active_surface = surface_key or self._current_surface_key or "dashboard"
        self._ensure_surface(active_surface)
        refresher = getattr(self, f"_refresh_{active_surface}_surface", None)
        if refresher is None:
            raise KeyError(f"Unknown Automation Center refresh surface: {active_surface}")
        refresher()

    def _refresh_dashboard_surface(self) -> None:
        selected_key = self._selected_preset_key()
        presets: list[AutomationPreset] = []
        inventory_error: str | None = None
        try:
            presets = list_automation_presets()
            self._presets_by_key = {preset.key: preset for preset in presets}
            self._settings_by_key = {setting.automation_key: setting for setting in list_automation_settings()}
            latest_runs: dict[str, AutomationRunRecord] = {}
            for run in list_recent_automation_runs(limit=100):
                latest_runs.setdefault(run.automation_key, run)
            self._latest_runs_by_key = latest_runs
        except Exception as exc:
            inventory_error = str(exc)
        try:
            self._proposal_records = list_proposals(limit=300)
        except Exception:
            self._proposal_records = []
        try:
            self._question_records = list_questions(limit=300)
        except Exception:
            self._question_records = []
        try:
            self._obligation_records = list_obligations_for_review(limit=300, include_terminal=False)
        except Exception:
            self._obligation_records = []
        self._refresh_queue_summary_cards()
        self._populate_inventory_tab(presets, selected_key, inventory_error)

    def _refresh_activity_surface(self) -> None:
        selected_run_id = self._selected_run_id()
        selected_event_id = self._selected_event_id()
        activity_error: str | None = None
        self._recent_runs_24h = []
        self._recent_events_24h = []
        self._selected_run_events = []
        self._summary = Automation24hSummary(0, 0, 0, 0, 0, 0, 0, 0, 0.0)
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            self._summary = get_automation_24h_summary()
            self._recent_runs_24h = list_runs_since(since, limit=200)
            self._recent_events_24h = list_events_since(since, limit=500)
        except Exception as exc:
            activity_error = str(exc)
        self._populate_activity_tab(selected_run_id, selected_event_id, activity_error)

    def _refresh_provider_surface(self) -> None:
        provider_error: str | None = None
        try:
            self._llm_config = get_llm_provider_config()
            self._llm_health = health_check(allow_live_call=False)
        except Exception as exc:
            provider_error = str(exc)
        self._populate_provider_tab(provider_error)

    def _refresh_policies_surface(self) -> None:
        selected_policy_key = self._selected_policy_key()
        policy_error: str | None = None
        self._policy_definitions = []
        try:
            self._policy_definitions = list_policy_definitions()
        except Exception as exc:
            policy_error = str(exc)
        self._populate_policies_tab(selected_policy_key, policy_error)

    def _refresh_memory_surface(self) -> None:
        selected_memory_id = self._selected_memory_id()
        memory_error: str | None = None
        self._memory_records = []
        self._memory_summary = AutomationMemorySummary(0, 0, 0, 0)
        try:
            self._memory_records = list_memory(limit=300)
            self._memory_summary = get_active_memory_summary()
        except Exception as exc:
            memory_error = str(exc)
        self._populate_memory_tab(selected_memory_id, memory_error)

    def _refresh_obligations_surface(self) -> None:
        selected_obligation_id = self._selected_obligation_id()
        obligation_error: str | None = None
        self._obligation_records = []
        self._selected_obligation_change_log = []
        try:
            self._obligation_records = list_obligations_for_review(limit=300, include_terminal=False)
        except Exception as exc:
            obligation_error = str(exc)
        self._obligation_error = obligation_error
        self._refresh_queue_filter_options()
        self._populate_obligations_tab(selected_obligation_id, obligation_error)

    def _refresh_pending_approvals_surface(self) -> None:
        selected_proposal_id = self._selected_proposal_id()
        proposal_error: str | None = None
        self._proposal_records = []
        self._pending_counts = AutomationPendingCounts(0, 0)
        try:
            self._proposal_records = list_proposals(limit=300)
            self._pending_counts = get_pending_counts()
        except Exception as exc:
            proposal_error = str(exc)
        self._proposal_error = proposal_error
        self._refresh_queue_filter_options()
        self._populate_pending_approvals_tab(selected_proposal_id, proposal_error)

    def _refresh_questions_surface(self) -> None:
        selected_question_id = self._selected_question_id()
        question_error: str | None = None
        self._question_records = []
        try:
            self._question_records = list_questions(limit=300)
        except Exception as exc:
            question_error = str(exc)
        self._question_error = question_error
        self._refresh_queue_filter_options()
        self._populate_questions_tab(selected_question_id, question_error)

    def _refresh_outbound_drafts_surface(self) -> None:
        selected_outbound_draft_id = self._selected_outbound_draft_id()
        outbound_draft_error: str | None = None
        self._outbound_draft_records = []
        try:
            self._outbound_draft_records = list_outbound_drafts_for_review(limit=300)
        except Exception as exc:
            outbound_draft_error = str(exc)
        self._outbound_draft_error = outbound_draft_error
        self._populate_outbound_drafts_tab(selected_outbound_draft_id, outbound_draft_error)

    def _refresh_intake_surface(self) -> None:
        selected_inbound_message_id = self._selected_inbound_message_id()
        selected_inbound_attachment_id = self._selected_inbound_attachment_id()
        intake_error: str | None = None
        self._inbound_messages = []
        self._selected_message_attachments = []
        try:
            self._inbound_messages = list_inbound_messages(limit=300)
        except Exception as exc:
            intake_error = str(exc)
        self._populate_intake_tab(selected_inbound_message_id, selected_inbound_attachment_id, intake_error)

    def _populate_inventory_tab(
        self,
        presets: list[AutomationPreset],
        selected_key: str | None,
        inventory_error: str | None,
    ) -> None:
        self.preset_tree.clear()
        for preset in presets:
            setting = self._settings_by_key.get(preset.key) or default_automation_setting_for_preset(preset)
            latest_run = self._latest_runs_by_key.get(preset.key)
            runtime_state = preset.runtime_state
            if inventory_error:
                runtime_state = f"{preset.runtime_state} | Persistence unavailable: {inventory_error}"
            item = QTreeWidgetItem(
                [
                    preset.key,
                    preset.display_name,
                    preset.workflow,
                    preset.description,
                    preset.level_2_mode,
                    preset.level_3_mode,
                    preset.risk_level,
                    runtime_state,
                    preset.legacy_state,
                    self._bool_text(preset.approval_required),
                    self._bool_text(preset.email_send_allowed),
                    self._bool_text(preset.external_action_allowed),
                    preset.env_control,
                    self._setting_state_text(setting),
                    self._format_timestamp(latest_run.started_at if latest_run else None),
                    latest_run.error_message if latest_run and latest_run.error_message else "-",
                    preset.notes,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, preset.key)
            item.setToolTip(3, preset.notes)
            item.setToolTip(7, runtime_state)
            item.setToolTip(8, preset.legacy_state)
            item.setToolTip(12, preset.env_control)
            item.setToolTip(13, self._setting_state_detail(setting))
            item.setToolTip(16, preset.notes)
            self.preset_tree.addTopLevelItem(item)

        for column in range(self.preset_tree.columnCount()):
            self.preset_tree.resizeColumnToContents(column)

        if presets:
            target_key = selected_key if selected_key in self._presets_by_key else presets[0].key
            self._select_preset_key(target_key)
        else:
            self._render_preset_detail(None)

    def _apply_obligation_queue_filters(self) -> None:
        self._populate_obligations_tab(self._selected_obligation_id(), self._obligation_error)

    def _apply_proposal_queue_filters(self) -> None:
        self._populate_pending_approvals_tab(self._selected_proposal_id(), self._proposal_error)

    def _apply_question_queue_filters(self) -> None:
        self._populate_questions_tab(self._selected_question_id(), self._question_error)

    def _apply_outbound_draft_filters(self) -> None:
        self._populate_outbound_drafts_tab(self._selected_outbound_draft_id(), self._outbound_draft_error)

    def _populate_activity_tab(
        self,
        selected_run_id: int | None,
        selected_event_id: int | None,
        activity_error: str | None,
    ) -> None:
        self.summary_labels["runs"].setText(str(self._summary.runs_in_last_24h))
        self.summary_labels["success"].setText(str(self._summary.successful_runs))
        self.summary_labels["failed"].setText(str(self._summary.failed_runs))
        self.summary_labels["proposals"].setText(str(self._summary.proposals_created))
        self.summary_labels["questions"].setText(str(self._summary.questions_created))
        self.summary_labels["actions"].setText(str(self._summary.actions_created))
        self.summary_labels["input_tokens"].setText(str(self._summary.input_tokens))
        self.summary_labels["output_tokens"].setText(str(self._summary.output_tokens))
        self.summary_labels["cost"].setText(self._format_cost(self._summary.estimated_cost))

        if activity_error:
            self.activity_empty_label.setText(f"Automation activity unavailable: {activity_error}")
            self.activity_empty_label.show()
        elif not self._recent_runs_24h and not self._recent_events_24h:
            self.activity_empty_label.setText(
                "No automation runs recorded yet. Legacy automation is disabled and Level 2 execution is not enabled."
            )
            self.activity_empty_label.show()
        else:
            self.activity_empty_label.hide()

        self.runs_table.setRowCount(0)
        for row_index, run in enumerate(self._recent_runs_24h):
            self.runs_table.insertRow(row_index)
            self._set_table_item(self.runs_table, row_index, 0, str(run.automation_run_id), user_data=run.automation_run_id)
            self._set_table_item(self.runs_table, row_index, 1, self._format_timestamp(run.started_at))
            self._set_table_item(self.runs_table, row_index, 2, run.automation_key)
            self._set_table_item(self.runs_table, row_index, 3, run.status)
            self._set_table_item(self.runs_table, row_index, 4, run.trigger_type or "-")
            provider_model = " / ".join(part for part in (run.model_provider, run.model_name) if part) or "-"
            self._set_table_item(self.runs_table, row_index, 5, provider_model)
            self._set_table_item(self.runs_table, row_index, 6, str(run.proposals_created), align_right=True)
            self._set_table_item(self.runs_table, row_index, 7, str(run.questions_created), align_right=True)
            self._set_table_item(self.runs_table, row_index, 8, str(run.actions_created), align_right=True)
            self._set_table_item(self.runs_table, row_index, 9, str(run.input_tokens), align_right=True)
            self._set_table_item(self.runs_table, row_index, 10, str(run.output_tokens), align_right=True)
            self._set_table_item(self.runs_table, row_index, 11, self._format_cost(run.estimated_cost), align_right=True)
            self.runs_table.setToolTip(run.error_message or "")
        self.runs_table.resizeColumnsToContents()

        if self._recent_runs_24h:
            target_run_id = selected_run_id if any(run.automation_run_id == selected_run_id for run in self._recent_runs_24h) else self._recent_runs_24h[0].automation_run_id
            self._select_run_id(target_run_id)
            self._populate_events_for_selected_run(target_run_id, selected_event_id)
        else:
            self._populate_event_table(self._recent_events_24h, selected_event_id=selected_event_id)
            self.event_scope_label.setText("Recent events for the last 24 hours")

    def _populate_provider_tab(self, provider_error: str | None) -> None:
        if provider_error:
            self.provider_name_label.setText("error")
            self.provider_model_label.setText("-")
            self.provider_safe_mode_label.setText("-")
            self.provider_openai_key_label.setText("-")
            self.provider_openai_model_label.setText("-")
            self.provider_ollama_model_label.setText("-")
            self.provider_health_label.setText("provider_status_unavailable")
            self.provider_error_label.setText(provider_error)
            self.provider_notes_text.setPlainText(
                "Unable to load provider configuration.\n\n"
                f"Error: {provider_error}"
            )
            return

        self.provider_name_label.setText(self._llm_config.provider)
        self.provider_model_label.setText(self._llm_config.model or "-")
        self.provider_safe_mode_label.setText(self._bool_text(self._llm_config.safe_mode))
        self.provider_openai_key_label.setText(self._bool_text(self._llm_config.openai_key_present))
        self.provider_openai_model_label.setText(self._llm_config.openai_model or "-")
        self.provider_ollama_model_label.setText(self._llm_config.ollama_model or "-")
        self.provider_health_label.setText(self._llm_health.status)
        self.provider_error_label.setText(self._llm_health.error or "-")
        notes = [
            f"Provider: {self._llm_config.provider}",
            f"Configured model: {self._llm_config.model or '-'}",
            f"Safe mode: {self._bool_text(self._llm_config.safe_mode)}",
            f"OpenAI key present: {self._bool_text(self._llm_config.openai_key_present)}",
            "",
            f"Notes: {self._llm_config.notes}",
            "",
            "This slice adds a central adapter only.",
            "No model call button exists here.",
            "No watcher is connected to the adapter yet.",
            "Health check is non-networking in safe/read-only mode.",
        ]
        if self._llm_health.error:
            notes.extend(("", f"Health check error: {self._llm_health.error}"))
        self.provider_notes_text.setPlainText("\n".join(notes))

    def _populate_policies_tab(self, selected_policy_key: str | None, policy_error: str | None) -> None:
        if policy_error:
            self.policies_empty_label.setText(f"Automation policy surface unavailable: {policy_error}")
            self.policies_empty_label.show()
        elif not self._policy_definitions:
            self.policies_empty_label.setText("No automation policies are defined.")
            self.policies_empty_label.show()
        else:
            self.policies_empty_label.setText(
                "Automation policies are currently static defaults. Editing is not enabled in this slice."
            )
            self.policies_empty_label.show()

        self.policies_table.setRowCount(0)
        for row_index, policy in enumerate(self._policy_definitions):
            self.policies_table.insertRow(row_index)
            self._set_table_item(self.policies_table, row_index, 0, policy.policy_key, user_data=policy.policy_key)
            self._set_table_item(self.policies_table, row_index, 1, self._policy_value_text(policy.value))
            self._set_table_item(self.policies_table, row_index, 2, policy.description)
            self._set_table_item(self.policies_table, row_index, 3, policy.enforcement_status)
            self._set_table_item(self.policies_table, row_index, 4, policy.notes)
        self.policies_table.resizeColumnsToContents()

        if self._policy_definitions:
            available_keys = {policy.policy_key for policy in self._policy_definitions}
            target_key = selected_policy_key if selected_policy_key in available_keys else self._policy_definitions[0].policy_key
            self._select_policy_key(target_key)
        else:
            self._render_policy_detail(None)

    def _populate_memory_tab(self, selected_memory_id: int | None, memory_error: str | None) -> None:
        self.memory_summary_labels["total"].setText(str(self._memory_summary.total_count))
        self.memory_summary_labels["active"].setText(str(self._memory_summary.active_count))
        self.memory_summary_labels["inactive"].setText(str(self._memory_summary.inactive_count))
        self.memory_summary_labels["expiring"].setText(str(self._memory_summary.expiring_count))

        if memory_error:
            self.memory_empty_label.setText(f"Automation memory unavailable: {memory_error}")
            self.memory_empty_label.show()
        elif not self._memory_records:
            self.memory_empty_label.setText("No automation memory records yet. Level 2 memory writes are not enabled.")
            self.memory_empty_label.show()
        else:
            self.memory_empty_label.hide()

        self.memory_table.setRowCount(0)
        for row_index, memory in enumerate(self._memory_records):
            self.memory_table.insertRow(row_index)
            self._set_table_item(self.memory_table, row_index, 0, memory.scope_type, user_data=memory.automation_memory_id)
            self._set_table_item(self.memory_table, row_index, 1, memory.scope_id or "-")
            self._set_table_item(self.memory_table, row_index, 2, memory.memory_type)
            self._set_table_item(self.memory_table, row_index, 3, self._memory_summary_text(memory))
            self._set_table_item(self.memory_table, row_index, 4, memory.source or "-")
            self._set_table_item(self.memory_table, row_index, 5, self._format_confidence(memory.confidence), align_right=True)
            self._set_table_item(self.memory_table, row_index, 6, memory.created_by or "-")
            self._set_table_item(self.memory_table, row_index, 7, self._format_timestamp(memory.created_at))
            self._set_table_item(self.memory_table, row_index, 8, self._format_timestamp(memory.expires_at))
            self._set_table_item(self.memory_table, row_index, 9, self._bool_text(memory.is_active))
        self.memory_table.resizeColumnsToContents()

        if self._memory_records:
            target_memory_id = (
                selected_memory_id
                if any(record.automation_memory_id == selected_memory_id for record in self._memory_records)
                else self._memory_records[0].automation_memory_id
            )
            self._select_memory_id(target_memory_id)
        else:
            self._render_memory_detail(None)

    def _populate_obligations_tab(self, selected_obligation_id: int | None, obligation_error: str | None) -> None:
        current = datetime.now(timezone.utc)
        operator_view = self._selected_obligation_operator_view()
        operator_view_counts = summarize_workflow_obligation_operator_views(self._obligation_records, now=current)
        self._refresh_obligation_operator_view_counts(operator_view_counts)
        operator_view_records = filter_workflow_obligations_for_operator_view(
            self._obligation_records,
            operator_view,
            now=current,
        )
        self._filtered_obligation_records = filter_and_sort_obligations(
            operator_view_records,
            self._queue_filter_state(self.obligation_queue_controls),
            now=current,
        )

        if obligation_error:
            self.obligations_empty_label.setText(f"Workflow obligations unavailable: {obligation_error}")
            self.obligations_empty_label.show()
        elif not self._obligation_records:
            self.obligations_empty_label.setText(
                "No active workflow obligations recorded yet. This board tracks expected next events and missing follow-up."
            )
            self.obligations_empty_label.show()
        elif not operator_view_records:
            view_label = WORKFLOW_OBLIGATION_OPERATOR_VIEW_LABELS.get(operator_view, operator_view.title())
            self.obligations_empty_label.setText(f"No workflow obligations are currently in the {view_label} view.")
            self.obligations_empty_label.show()
        elif not self._filtered_obligation_records:
            self.obligations_empty_label.setText("No workflow obligations match the current queue filters.")
            self.obligations_empty_label.show()
        else:
            self.obligations_empty_label.hide()

        self.obligations_table.setRowCount(0)
        for row_index, obligation in enumerate(self._filtered_obligation_records):
            meta = build_obligation_queue_metadata(obligation, now=current)
            self.obligations_table.insertRow(row_index)
            self._set_table_item(
                self.obligations_table,
                row_index,
                0,
                str(obligation.obligation_id),
                user_data=obligation.obligation_id,
            )
            self._set_table_item(self.obligations_table, row_index, 1, self._format_timestamp(meta["due_at"]))
            self._set_table_item(
                self.obligations_table,
                row_index,
                2,
                _age_text(int(meta["overdue_days"])) if meta["is_overdue"] else "-",
            )
            self._set_table_item(self.obligations_table, row_index, 3, obligation.workflow_type)
            self._set_table_item(self.obligations_table, row_index, 4, obligation.expected_event_type)
            self._set_table_item(self.obligations_table, row_index, 5, str(meta["source_summary"]))
            self._set_table_item(self.obligations_table, row_index, 6, obligation.severity)
            self._set_table_item(self.obligations_table, row_index, 7, str(meta["owner_summary"]))
            self._set_table_item(self.obligations_table, row_index, 8, obligation.status)
            self._set_table_item(
                self.obligations_table,
                row_index,
                9,
                f"{meta['last_status_summary']} | Checked {self._format_timestamp(obligation.last_checked_at)}",
            )
            self._set_table_item(self.obligations_table, row_index, 10, str(meta["cash_flow_text"]))
        self.obligations_table.resizeColumnsToContents()

        if self._filtered_obligation_records:
            target_obligation_id = (
                selected_obligation_id
                if any(record.obligation_id == selected_obligation_id for record in self._filtered_obligation_records)
                else self._filtered_obligation_records[0].obligation_id
            )
            self._select_obligation_id(target_obligation_id)
        else:
            self._selected_obligation_change_log = []
            self._render_obligation_detail(None)

    def _populate_pending_approvals_tab(
        self,
        selected_proposal_id: int | None,
        proposal_error: str | None,
    ) -> None:
        self._filtered_proposal_records = filter_and_sort_proposals(
            self._proposal_records,
            self._queue_filter_state(self.proposal_queue_controls),
        )
        self.pending_summary_labels["pending_proposals"].setText(str(self._pending_counts.pending_proposals))
        self.pending_summary_labels["open_questions"].setText(str(self._pending_counts.open_questions))

        if proposal_error:
            self.pending_empty_label.setText(f"Automation proposals unavailable: {proposal_error}")
            self.pending_empty_label.show()
        elif not self._proposal_records:
            self.pending_empty_label.setText("No automation proposals recorded yet.")
            self.pending_empty_label.show()
        elif not self._filtered_proposal_records:
            self.pending_empty_label.setText("No automation proposals match the current queue filters.")
            self.pending_empty_label.show()
        else:
            self.pending_empty_label.hide()

        self.proposals_table.setRowCount(0)
        for row_index, proposal in enumerate(self._filtered_proposal_records):
            meta = build_proposal_queue_metadata(proposal)
            self.proposals_table.insertRow(row_index)
            self._set_table_item(
                self.proposals_table,
                row_index,
                0,
                str(proposal.automation_proposal_id),
                user_data=proposal.automation_proposal_id,
            )
            self._set_table_item(self.proposals_table, row_index, 1, self._format_timestamp(proposal.created_at))
            self._set_table_item(self.proposals_table, row_index, 2, proposal.workflow)
            self._set_table_item(self.proposals_table, row_index, 3, proposal.action_type)
            self._set_table_item(self.proposals_table, row_index, 4, str(meta["target_summary"]))
            self._set_table_item(self.proposals_table, row_index, 5, proposal.risk_level)
            self._set_table_item(
                self.proposals_table,
                row_index,
                6,
                self._format_confidence(proposal.confidence),
                align_right=True,
            )
            self._set_table_item(self.proposals_table, row_index, 7, str(meta["cash_flow_text"]))
            self._set_table_item(self.proposals_table, row_index, 8, str(meta["source_summary"]))
            self._set_table_item(self.proposals_table, row_index, 9, proposal.status)
            self._set_table_item(self.proposals_table, row_index, 10, str(meta["age_text"]))
        self.proposals_table.resizeColumnsToContents()

        if self._filtered_proposal_records:
            target_proposal_id = (
                selected_proposal_id
                if any(record.automation_proposal_id == selected_proposal_id for record in self._filtered_proposal_records)
                else self._filtered_proposal_records[0].automation_proposal_id
            )
            self._select_proposal_id(target_proposal_id)
        else:
            self._render_proposal_detail(None)

    def _populate_outbound_drafts_tab(
        self,
        selected_outbound_draft_id: int | None,
        outbound_draft_error: str | None,
    ) -> None:
        options = get_outbound_draft_filter_options(self._outbound_draft_records)
        self._reset_combo_values(
            self.outbound_draft_status_combo,
            ["All statuses", *options.get("statuses", [])],
            preferred_value="Prepared",
        )
        self._reset_combo_values(
            self.outbound_draft_template_combo,
            ["All templates", *options.get("templates", [])],
        )
        self._filtered_outbound_draft_records = filter_outbound_draft_reviews(
            self._outbound_draft_records,
            status=self.outbound_draft_status_combo.currentText(),
            template_code=self.outbound_draft_template_combo.currentText(),
            recipient_search=self.outbound_draft_recipient_search.text(),
        )

        if outbound_draft_error:
            self.outbound_drafts_empty_label.setText(f"Outbound draft review unavailable: {outbound_draft_error}")
            self.outbound_drafts_empty_label.show()
        elif not self._outbound_draft_records:
            self.outbound_drafts_empty_label.setText("No Level 3 outbound drafts recorded yet.")
            self.outbound_drafts_empty_label.show()
        elif not self._filtered_outbound_draft_records:
            self.outbound_drafts_empty_label.setText("No outbound drafts match the current filters.")
            self.outbound_drafts_empty_label.show()
        else:
            self.outbound_drafts_empty_label.hide()

        self.outbound_drafts_table.setRowCount(0)
        for row_index, draft in enumerate(self._filtered_outbound_draft_records):
            self.outbound_drafts_table.insertRow(row_index)
            self._set_table_item(
                self.outbound_drafts_table,
                row_index,
                0,
                str(draft.outbound_message_log_id),
                user_data=draft.outbound_message_log_id,
            )
            self._set_table_item(self.outbound_drafts_table, row_index, 1, self._format_timestamp(draft.created_at))
            self._set_table_item(self.outbound_drafts_table, row_index, 2, draft.template_code or "-")
            self._set_table_item(self.outbound_drafts_table, row_index, 3, draft.send_status)
            self._set_table_item(self.outbound_drafts_table, row_index, 4, draft.recipient_email or "-")
            self._set_table_item(self.outbound_drafts_table, row_index, 5, draft.subject or "-")
            self._set_table_item(self.outbound_drafts_table, row_index, 6, draft.source_summary)
            self._set_table_item(self.outbound_drafts_table, row_index, 7, self._format_timestamp(draft.sent_at))
            self._set_table_item(
                self.outbound_drafts_table,
                row_index,
                8,
                self._bool_text(draft.provider_message_id_present),
            )
            self._set_table_item(
                self.outbound_drafts_table,
                row_index,
                9,
                self._bool_text(draft.allowlist_eligible),
            )
        self.outbound_drafts_table.resizeColumnsToContents()

        if self._filtered_outbound_draft_records:
            target_draft_id = (
                selected_outbound_draft_id
                if any(
                    record.outbound_message_log_id == selected_outbound_draft_id
                    for record in self._filtered_outbound_draft_records
                )
                else self._filtered_outbound_draft_records[0].outbound_message_log_id
            )
            self._select_outbound_draft_id(target_draft_id)
        else:
            self._render_outbound_draft_detail(None)

    def _populate_questions_tab(
        self,
        selected_question_id: int | None,
        question_error: str | None,
    ) -> None:
        self._filtered_question_records = filter_and_sort_questions(
            self._question_records,
            self._queue_filter_state(self.question_queue_controls),
        )
        if question_error:
            self.questions_empty_label.setText(f"Automation questions unavailable: {question_error}")
            self.questions_empty_label.show()
        elif not self._question_records:
            self.questions_empty_label.setText("No open automation questions.")
            self.questions_empty_label.show()
        elif not self._filtered_question_records:
            self.questions_empty_label.setText("No automation questions match the current queue filters.")
            self.questions_empty_label.show()
        else:
            self.questions_empty_label.hide()

        self.questions_table.setRowCount(0)
        for row_index, question in enumerate(self._filtered_question_records):
            meta = build_question_queue_metadata(question)
            self.questions_table.insertRow(row_index)
            self._set_table_item(
                self.questions_table,
                row_index,
                0,
                str(question.automation_question_id),
                user_data=question.automation_question_id,
            )
            self._set_table_item(self.questions_table, row_index, 1, self._format_timestamp(question.created_at))
            self._set_table_item(self.questions_table, row_index, 2, question.workflow)
            self._set_table_item(self.questions_table, row_index, 3, question.question_type)
            self._set_table_item(self.questions_table, row_index, 4, question.urgency or "-")
            self._set_table_item(self.questions_table, row_index, 5, str(meta["target_summary"]))
            self._set_table_item(self.questions_table, row_index, 6, str(meta["source_summary"]))
            self._set_table_item(self.questions_table, row_index, 7, str(meta["uncertainty_summary"]))
            self._set_table_item(self.questions_table, row_index, 8, question.status)
            self._set_table_item(self.questions_table, row_index, 9, str(meta["age_text"]))
        self.questions_table.resizeColumnsToContents()

        if self._filtered_question_records:
            target_question_id = (
                selected_question_id
                if any(record.automation_question_id == selected_question_id for record in self._filtered_question_records)
                else self._filtered_question_records[0].automation_question_id
            )
            self._select_question_id(target_question_id)
        else:
            self._render_question_detail(None)

    def _populate_intake_tab(
        self,
        selected_message_id: int | None,
        selected_attachment_id: int | None,
        intake_error: str | None,
    ) -> None:
        if intake_error:
            self.intake_empty_label.setText(f"Inbound intake unavailable: {intake_error}")
            self.intake_empty_label.show()
        elif not self._inbound_messages:
            self.intake_empty_label.setText("No inbound messages recorded yet. Inbox polling is not enabled.")
            self.intake_empty_label.show()
        else:
            self.intake_empty_label.hide()

        self.inbound_messages_table.setRowCount(0)
        for row_index, message in enumerate(self._inbound_messages):
            self.inbound_messages_table.insertRow(row_index)
            self._set_table_item(
                self.inbound_messages_table,
                row_index,
                0,
                str(message.inbound_message_id),
                user_data=message.inbound_message_id,
            )
            sender_text = message.sender_name or message.sender or "-"
            self._set_table_item(self.inbound_messages_table, row_index, 1, sender_text)
            self._set_table_item(self.inbound_messages_table, row_index, 2, message.subject or "-")
            self._set_table_item(self.inbound_messages_table, row_index, 3, message.workflow_guess or "-")
            self._set_table_item(self.inbound_messages_table, row_index, 4, message.intent_guess or "-")
            self._set_table_item(
                self.inbound_messages_table,
                row_index,
                5,
                self._format_confidence(message.confidence),
                align_right=True,
            )
            self._set_table_item(self.inbound_messages_table, row_index, 6, message.status)
            timing_text = self._format_timestamp(message.received_at)
            if timing_text == "-":
                timing_text = self._format_timestamp(message.imported_at)
            else:
                timing_text = f"{timing_text} / {self._format_timestamp(message.imported_at)}"
            self._set_table_item(self.inbound_messages_table, row_index, 7, timing_text)
            self._set_table_item(
                self.inbound_messages_table,
                row_index,
                8,
                str(message.attachment_count),
                align_right=True,
            )
        self.inbound_messages_table.resizeColumnsToContents()

        if self._inbound_messages:
            target_message_id = (
                selected_message_id
                if any(record.inbound_message_id == selected_message_id for record in self._inbound_messages)
                else self._inbound_messages[0].inbound_message_id
            )
            self._select_inbound_message_id(target_message_id, selected_attachment_id=selected_attachment_id)
        else:
            self._selected_message_attachments = []
            self._populate_attachment_table(None)
            self._render_inbound_message_detail(None)
            self._render_inbound_attachment_detail(None)

    def _selected_preset_key(self) -> str | None:
        item = self.preset_tree.currentItem()
        if item is None:
            return None
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _selected_run_id(self) -> int | None:
        row = self.runs_table.currentRow()
        if row < 0:
            return None
        item = self.runs_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_policy_key(self) -> str | None:
        row = self.policies_table.currentRow()
        if row < 0:
            return None
        item = self.policies_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        text = str(data or "").strip()
        return text or None

    def _selected_event_id(self) -> int | None:
        row = self.events_table.currentRow()
        if row < 0:
            return None
        item = self.events_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_memory_id(self) -> int | None:
        row = self.memory_table.currentRow()
        if row < 0:
            return None
        item = self.memory_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_obligation_id(self) -> int | None:
        row = self.obligations_table.currentRow()
        if row < 0:
            return None
        item = self.obligations_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_proposal_id(self) -> int | None:
        row = self.proposals_table.currentRow()
        if row < 0:
            return None
        item = self.proposals_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_question_id(self) -> int | None:
        row = self.questions_table.currentRow()
        if row < 0:
            return None
        item = self.questions_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_outbound_draft_id(self) -> int | None:
        row = self.outbound_drafts_table.currentRow()
        if row < 0:
            return None
        item = self.outbound_drafts_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_inbound_message_id(self) -> int | None:
        row = self.inbound_messages_table.currentRow()
        if row < 0:
            return None
        item = self.inbound_messages_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _selected_inbound_attachment_id(self) -> int | None:
        row = self.inbound_attachments_table.currentRow()
        if row < 0:
            return None
        item = self.inbound_attachments_table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _select_preset_key(self, preset_key: str) -> None:
        for index in range(self.preset_tree.topLevelItemCount()):
            item = self.preset_tree.topLevelItem(index)
            if str(item.data(0, Qt.ItemDataRole.UserRole) or "") == preset_key:
                self.preset_tree.setCurrentItem(item)
                self._render_preset_detail(self._presets_by_key.get(preset_key))
                return

    def _select_run_id(self, run_id: int) -> None:
        for row in range(self.runs_table.rowCount()):
            item = self.runs_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(run_id):
                    self.runs_table.setCurrentCell(row, 0)
                    return
            except (TypeError, ValueError):
                continue

    def _select_policy_key(self, policy_key: str) -> None:
        for row in range(self.policies_table.rowCount()):
            item = self.policies_table.item(row, 0)
            if item is None:
                continue
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == str(policy_key):
                self.policies_table.setCurrentCell(row, 0)
                self._handle_policy_selection_changed()
                return

    def _select_event_id(self, event_id: int) -> None:
        for row in range(self.events_table.rowCount()):
            item = self.events_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(event_id):
                    self.events_table.setCurrentCell(row, 0)
                    self._handle_event_selection_changed()
                    return
            except (TypeError, ValueError):
                continue

    def _select_memory_id(self, memory_id: int) -> None:
        for row in range(self.memory_table.rowCount()):
            item = self.memory_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(memory_id):
                    self.memory_table.setCurrentCell(row, 0)
                    self._handle_memory_selection_changed()
                    return
            except (TypeError, ValueError):
                continue

    def _select_obligation_id(self, obligation_id: int) -> None:
        for row in range(self.obligations_table.rowCount()):
            item = self.obligations_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(obligation_id):
                    self.obligations_table.setCurrentCell(row, 0)
                    self._handle_obligation_selection_changed()
                    return
            except (TypeError, ValueError):
                continue

    def _select_proposal_id(self, proposal_id: int) -> None:
        for row in range(self.proposals_table.rowCount()):
            item = self.proposals_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(proposal_id):
                    self.proposals_table.setCurrentCell(row, 0)
                    self._handle_proposal_selection_changed()
                    return
            except (TypeError, ValueError):
                continue

    def _select_question_id(self, question_id: int) -> None:
        for row in range(self.questions_table.rowCount()):
            item = self.questions_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(question_id):
                    self.questions_table.setCurrentCell(row, 0)
                    self._handle_question_selection_changed()
                    return
            except (TypeError, ValueError):
                continue

    def _select_outbound_draft_id(self, outbound_draft_id: int) -> None:
        for row in range(self.outbound_drafts_table.rowCount()):
            item = self.outbound_drafts_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(outbound_draft_id):
                    self.outbound_drafts_table.setCurrentCell(row, 0)
                    self._handle_outbound_draft_selection_changed()
                    return
            except (TypeError, ValueError):
                continue

    def _select_inbound_message_id(self, message_id: int, *, selected_attachment_id: int | None = None) -> None:
        for row in range(self.inbound_messages_table.rowCount()):
            item = self.inbound_messages_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(message_id):
                    self.inbound_messages_table.setCurrentCell(row, 0)
                    self._handle_inbound_message_selection_changed(selected_attachment_id=selected_attachment_id)
                    return
            except (TypeError, ValueError):
                continue

    def _select_inbound_attachment_id(self, attachment_id: int) -> None:
        for row in range(self.inbound_attachments_table.rowCount()):
            item = self.inbound_attachments_table.item(row, 0)
            if item is None:
                continue
            try:
                if int(item.data(Qt.ItemDataRole.UserRole)) == int(attachment_id):
                    self.inbound_attachments_table.setCurrentCell(row, 0)
                    self._handle_inbound_attachment_selection_changed()
                    return
            except (TypeError, ValueError):
                continue

    def _handle_inventory_selection_changed(self) -> None:
        self._render_preset_detail(self._presets_by_key.get(self._selected_preset_key() or ""))

    def _handle_run_selection_changed(self) -> None:
        run_id = self._selected_run_id()
        if run_id is None:
            self.event_scope_label.setText("Recent events for the last 24 hours")
            self._populate_event_table(self._recent_events_24h)
            return
        self._populate_events_for_selected_run(run_id)

    def _handle_policy_selection_changed(self) -> None:
        policy_key = self._selected_policy_key()
        if policy_key is None:
            self._render_policy_detail(None)
            return
        for policy in self._policy_definitions:
            if policy.policy_key == policy_key:
                self._render_policy_detail(policy)
                return
        self._render_policy_detail(None)

    def _populate_events_for_selected_run(self, run_id: int, selected_event_id: int | None = None) -> None:
        try:
            self._selected_run_events = list_automation_events_for_run(run_id)
            self.event_scope_label.setText(f"Events for selected run #{run_id}")
            self._populate_event_table(self._selected_run_events, selected_event_id=selected_event_id)
        except Exception as exc:
            self.event_scope_label.setText(f"Event log unavailable for run #{run_id}: {exc}")
            self._selected_run_events = []
            self._populate_event_table([])

    def _populate_event_table(
        self,
        events: list[AutomationEventRecord],
        *,
        selected_event_id: int | None = None,
    ) -> None:
        self.events_table.setRowCount(0)
        for row_index, event in enumerate(events):
            self.events_table.insertRow(row_index)
            self._set_table_item(
                self.events_table,
                row_index,
                0,
                self._format_timestamp(event.created_at),
                user_data=event.automation_event_id,
            )
            self._set_table_item(self.events_table, row_index, 1, event.automation_key)
            self._set_table_item(self.events_table, row_index, 2, event.event_type)
            self._set_table_item(self.events_table, row_index, 3, event.summary)
            target_text = self._event_target_text(event)
            self._set_table_item(self.events_table, row_index, 4, target_text)
            self._set_table_item(self.events_table, row_index, 5, self._event_json_preview_text(event))
        self.events_table.resizeColumnsToContents()

        if events:
            target_event_id = selected_event_id if any(event.automation_event_id == selected_event_id for event in events) else events[0].automation_event_id
            self._select_event_id(target_event_id)
        else:
            self.event_json_preview.setPlainText("No event log entries to display.")

    def _handle_event_selection_changed(self) -> None:
        row = self.events_table.currentRow()
        if row < 0:
            self.event_json_preview.setPlainText("Select an event to inspect its JSON detail.")
            return
        event = self._event_for_row(row)
        if event is None:
            self.event_json_preview.setPlainText("Select an event to inspect its JSON detail.")
            return
        self.event_json_preview.setPlainText(self._event_json_detail_text(event))

    def _handle_memory_selection_changed(self) -> None:
        row = self.memory_table.currentRow()
        if row < 0:
            self._render_memory_detail(None)
            return
        item = self.memory_table.item(row, 0)
        if item is None:
            self._render_memory_detail(None)
            return
        memory_id = item.data(Qt.ItemDataRole.UserRole)
        for memory in self._memory_records:
            if memory.automation_memory_id == memory_id:
                self._render_memory_detail(memory)
                return
        self._render_memory_detail(None)

    def _handle_obligation_selection_changed(self) -> None:
        row = self.obligations_table.currentRow()
        if row < 0:
            self._selected_obligation_change_log = []
            self._render_obligation_detail(None)
            return
        item = self.obligations_table.item(row, 0)
        if item is None:
            self._selected_obligation_change_log = []
            self._render_obligation_detail(None)
            return
        obligation_id = item.data(Qt.ItemDataRole.UserRole)
        for obligation in self._obligation_records:
            if obligation.obligation_id == obligation_id:
                try:
                    self._selected_obligation_change_log = list_obligation_change_log(obligation.obligation_id, limit=50)
                except Exception as exc:
                    self._selected_obligation_change_log = []
                    self.obligation_change_log_text.setPlainText(f"WorkflowObligation change log unavailable: {exc}")
                self._render_obligation_detail(obligation)
                return
        self._selected_obligation_change_log = []
        self._render_obligation_detail(None)

    def _handle_obligation_edit_due_clicked(self) -> None:
        obligation = self._selected_obligation_record()
        if obligation is None:
            return
        initial = self._format_timestamp(obligation.expected_by) if obligation.expected_by else ""
        text, accepted = QInputDialog.getText(
            self,
            "Edit Obligation Due Date",
            "Enter the internal follow-up due date/time in ISO format.\nThis does not change the linked business record date.",
            text=initial,
        )
        if not accepted:
            return
        try:
            updated = update_obligation_fields(
                obligation.obligation_id,
                fields={"expected_by": str(text).strip()},
                changed_by="Automation Center",
                change_source="ui",
                reason="Edited internal obligation due date.",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Edit Obligation Due Date", str(exc))
            return
        self.refresh_data()
        self._select_obligation_id(updated.obligation_id)

    def _handle_obligation_edit_severity_clicked(self) -> None:
        obligation = self._selected_obligation_record()
        if obligation is None:
            return
        choices = ["Low", "Medium", "High", "Critical"]
        current = obligation.severity if obligation.severity in choices else "Medium"
        value, accepted = QInputDialog.getItem(
            self,
            "Edit Obligation Severity",
            "Choose the internal follow-up severity.",
            choices,
            current=choices.index(current),
            editable=False,
        )
        if not accepted:
            return
        try:
            updated = update_obligation_fields(
                obligation.obligation_id,
                fields={"severity": value},
                changed_by="Automation Center",
                change_source="ui",
                reason="Edited obligation severity.",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Edit Obligation Severity", str(exc))
            return
        self.refresh_data()
        self._select_obligation_id(updated.obligation_id)

    def _handle_obligation_edit_text_clicked(self) -> None:
        obligation = self._selected_obligation_record()
        if obligation is None:
            return
        title, accepted = QInputDialog.getText(
            self,
            "Edit Obligation Title",
            "Edit the internal obligation title.",
            text=obligation.title or "",
        )
        if not accepted:
            return
        description, accepted = QInputDialog.getMultiLineText(
            self,
            "Edit Obligation Description",
            "Edit the internal obligation description.",
            text=obligation.description or "",
        )
        if not accepted:
            return
        try:
            updated = update_obligation_fields(
                obligation.obligation_id,
                fields={"title": title, "description": description},
                changed_by="Automation Center",
                change_source="ui",
                reason="Edited obligation title/description.",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Edit Obligation Text", str(exc))
            return
        self.refresh_data()
        self._select_obligation_id(updated.obligation_id)

    def _handle_obligation_snooze_clicked(self) -> None:
        obligation = self._selected_obligation_record()
        if obligation is None:
            return
        initial = self._format_timestamp(obligation.snooze_until) if obligation.snooze_until else ""
        text, accepted = QInputDialog.getText(
            self,
            "Snooze Obligation",
            "Enter the snooze-until date/time in ISO format.\nThe obligation will pause until this internal follow-up time.",
            text=initial,
        )
        if not accepted:
            return
        note, accepted = QInputDialog.getMultiLineText(
            self,
            "Snooze Note",
            "Optional note for the snooze action.",
            text="",
        )
        if not accepted:
            return
        try:
            updated = snooze_obligation(
                obligation.obligation_id,
                snooze_until=str(text).strip(),
                note=note or None,
                changed_by="Automation Center",
                change_source="ui",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Snooze Obligation", str(exc))
            return
        self.refresh_data()
        self._select_obligation_id(updated.obligation_id)

    def _handle_obligation_mark_satisfied_clicked(self) -> None:
        obligation = self._selected_obligation_record()
        if obligation is None:
            return
        note, accepted = QInputDialog.getMultiLineText(
            self,
            "Mark Obligation Satisfied",
            "Enter the internal reason/note for manual satisfaction.",
            text="",
        )
        if not accepted:
            return
        try:
            updated = mark_obligation_satisfied_manual(
                obligation.obligation_id,
                note=note,
                changed_by="Automation Center",
                change_source="ui",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Mark Obligation Satisfied", str(exc))
            return
        self.refresh_data()
        self._select_obligation_id(updated.obligation_id)

    def _handle_obligation_dismiss_clicked(self) -> None:
        obligation = self._selected_obligation_record()
        if obligation is None:
            return
        note, accepted = QInputDialog.getMultiLineText(
            self,
            "Dismiss Obligation",
            "Enter the internal reason/note for dismissal.",
            text="",
        )
        if not accepted:
            return
        try:
            updated = dismiss_obligation(
                obligation.obligation_id,
                note=note,
                changed_by="Automation Center",
                change_source="ui",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Dismiss Obligation", str(exc))
            return
        self.refresh_data()
        self._select_obligation_id(updated.obligation_id)

    def _handle_obligation_add_note_clicked(self) -> None:
        obligation = self._selected_obligation_record()
        if obligation is None:
            return
        note, accepted = QInputDialog.getMultiLineText(
            self,
            "Add Obligation Note",
            "Add internal note context. Existing note history will be preserved.",
            text="",
        )
        if not accepted:
            return
        try:
            updated = add_obligation_note(
                obligation.obligation_id,
                note=note,
                changed_by="Automation Center",
                change_source="ui",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Add Obligation Note", str(exc))
            return
        self.refresh_data()
        self._select_obligation_id(updated.obligation_id)

    def _handle_proposal_selection_changed(self) -> None:
        row = self.proposals_table.currentRow()
        if row < 0:
            self._render_proposal_detail(None)
            return
        item = self.proposals_table.item(row, 0)
        if item is None:
            self._render_proposal_detail(None)
            return
        proposal_id = item.data(Qt.ItemDataRole.UserRole)
        for proposal in self._proposal_records:
            if proposal.automation_proposal_id == proposal_id:
                self._render_proposal_detail(proposal)
                return
        self._render_proposal_detail(None)

    def _handle_outbound_draft_selection_changed(self) -> None:
        row = self.outbound_drafts_table.currentRow()
        if row < 0:
            self._render_outbound_draft_detail(None)
            return
        item = self.outbound_drafts_table.item(row, 0)
        if item is None:
            self._render_outbound_draft_detail(None)
            return
        draft_id = item.data(Qt.ItemDataRole.UserRole)
        for draft in self._outbound_draft_records:
            if draft.outbound_message_log_id == draft_id:
                self._render_outbound_draft_detail(draft)
                return
        self._render_outbound_draft_detail(None)

    def _handle_outbound_draft_send_clicked(self) -> None:
        draft = self._selected_outbound_draft_record()
        if draft is None:
            QMessageBox.warning(self, "Send Selected Draft", "Select one outbound draft first.")
            return
        if draft.send_status != "Prepared":
            QMessageBox.warning(
                self,
                "Send Selected Draft",
                "Only Prepared outbound drafts can be sent through this action.",
            )
            return
        recipient = draft.recipient_email or "(missing recipient)"
        response = QMessageBox.question(
            self,
            "Send Selected Draft",
            (
                f"Send this prepared draft to {recipient}?\n\n"
                "External email cannot be unsent.\n\n"
                "This will call the Level 3 approved-send wrapper for this one selected draft only. "
                "The wrapper will block the send unless test mode, allowlist, recipient, subject/body, "
                "template, source, and status checks pass."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        try:
            result = send_approved_prepared_outbound_draft(
                draft.outbound_message_log_id,
                approved_by="Automation Center",
                operator_approved=True,
                allow_live_send=True,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Send Selected Draft", str(exc))
            return

        draft_id = draft.outbound_message_log_id
        self.refresh_data()
        self._select_outbound_draft_id(draft_id)
        if result.sent:
            QMessageBox.information(
                self,
                "Send Selected Draft",
                f"Outbound draft #{draft_id} was sent successfully.",
            )
        elif result.blocked:
            QMessageBox.warning(
                self,
                "Send Selected Draft",
                f"Approved send was blocked.\n\n{result.message}",
            )
        else:
            QMessageBox.warning(
                self,
                "Send Selected Draft",
                f"Approved send did not complete.\n\n{result.message}",
            )

    def _handle_proposal_approve_clicked(self) -> None:
        proposal = self._selected_proposal_record()
        if proposal is None:
            return
        try:
            updated = approve_proposal(proposal.automation_proposal_id, approved_by="Automation Center")
        except Exception as exc:
            QMessageBox.warning(self, "Approve Proposal", str(exc))
            return
        self.refresh_data()
        self._select_proposal_id(updated.automation_proposal_id)
        QMessageBox.information(
            self,
            "Approve Proposal",
            f"Proposal #{updated.automation_proposal_id} is now Approved.",
        )

    def _handle_proposal_reject_clicked(self) -> None:
        proposal = self._selected_proposal_record()
        if proposal is None:
            return
        try:
            updated = reject_proposal(proposal.automation_proposal_id, rejected_by="Automation Center")
        except Exception as exc:
            QMessageBox.warning(self, "Reject Proposal", str(exc))
            return
        self.refresh_data()
        self._select_proposal_id(updated.automation_proposal_id)
        QMessageBox.information(
            self,
            "Reject Proposal",
            f"Proposal #{updated.automation_proposal_id} is now Rejected.",
        )

    def _handle_proposal_apply_clicked(self) -> None:
        proposal = self._selected_proposal_record()
        if proposal is None:
            return
        try:
            result = apply_approved_proposal(proposal.automation_proposal_id, applied_by="Automation Center")
        except Exception as exc:
            QMessageBox.warning(self, "Apply Proposal", str(exc))
            return
        self.refresh_data()
        self._select_proposal_id(result.proposal.automation_proposal_id)
        if result.success:
            apply_message = (
                f"Proposal #{result.proposal.automation_proposal_id} follow-up draft reused successfully."
                if result.result_code == "existing_draft_reused"
                else f"Proposal #{result.proposal.automation_proposal_id} applied successfully."
            )
            if result.outbound_message_log_id is not None:
                apply_message += (
                    f"\n\nOutbound Draft Log: #{result.outbound_message_log_id}"
                    f"\nDraft Status: {result.draft_status or '-'}"
                )
            if isinstance(result.save_result, dict):
                lead_apply_result = result.save_result.get("LeadIntakeApplyResult")
                if isinstance(lead_apply_result, dict):
                    apply_message += "\n\nLead Intake Draft Result:"
                    if lead_apply_result.get("created_customer_id"):
                        apply_message += f"\nCustomer: created #{lead_apply_result.get('created_customer_id')}"
                    elif lead_apply_result.get("used_existing_customer_id"):
                        apply_message += f"\nCustomer: reused #{lead_apply_result.get('used_existing_customer_id')}"
                    else:
                        apply_message += "\nCustomer: none"
                    if lead_apply_result.get("created_site_id"):
                        apply_message += f"\nSite: created #{lead_apply_result.get('created_site_id')}"
                    elif lead_apply_result.get("used_existing_site_id"):
                        apply_message += f"\nSite: reused #{lead_apply_result.get('used_existing_site_id')}"
                    else:
                        apply_message += "\nSite: none"
                    if lead_apply_result.get("created_estimate_id"):
                        apply_message += f"\nEstimate: created #{lead_apply_result.get('created_estimate_id')}"
                    elif lead_apply_result.get("used_existing_estimate_id"):
                        apply_message += f"\nEstimate: reused #{lead_apply_result.get('used_existing_estimate_id')}"
                    else:
                        apply_message += "\nEstimate: none"
                    warnings = lead_apply_result.get("warnings") if isinstance(lead_apply_result.get("warnings"), list) else []
                    if warnings:
                        apply_message += "\nWarnings: " + "; ".join(str(item) for item in warnings)
                customer_invoice_apply_result = result.save_result.get("CustomerInvoiceDueApplyResult")
                if isinstance(customer_invoice_apply_result, dict):
                    apply_message += "\n\nCustomer Invoice Draft Result:"
                    if customer_invoice_apply_result.get("created_invoice_id"):
                        apply_message += f"\nInvoice: created #{customer_invoice_apply_result.get('created_invoice_id')}"
                    elif customer_invoice_apply_result.get("used_existing_invoice_id"):
                        apply_message += f"\nInvoice: reused #{customer_invoice_apply_result.get('used_existing_invoice_id')}"
                    else:
                        apply_message += "\nInvoice: none"
                    apply_message += f"\nInvoice Number: {customer_invoice_apply_result.get('invoice_number') or '-'}"
                    apply_message += f"\nStatus: {customer_invoice_apply_result.get('invoice_status') or '-'}"
                    apply_message += f"\nWork Order: #{customer_invoice_apply_result.get('source_work_order_id') or '-'}"
                    warnings = (
                        customer_invoice_apply_result.get("warnings")
                        if isinstance(customer_invoice_apply_result.get("warnings"), list)
                        else []
                    )
                    if warnings:
                        apply_message += "\nWarnings: " + "; ".join(str(item) for item in warnings)
                estimate_acceptance_apply_result = result.save_result.get("EstimateAcceptanceApplyResult")
                if isinstance(estimate_acceptance_apply_result, dict):
                    apply_message += "\n\nWorkOrder Draft Result:"
                    if estimate_acceptance_apply_result.get("created_work_order_id"):
                        apply_message += (
                            f"\nWorkOrder: created #{estimate_acceptance_apply_result.get('created_work_order_id')}"
                        )
                    elif estimate_acceptance_apply_result.get("used_existing_work_order_id"):
                        apply_message += (
                            f"\nWorkOrder: reused #{estimate_acceptance_apply_result.get('used_existing_work_order_id')}"
                        )
                    else:
                        apply_message += "\nWorkOrder: none"
                    apply_message += f"\nStatus: {estimate_acceptance_apply_result.get('job_status') or '-'}"
                    apply_message += (
                        f"\nApproved: {self._bool_text(bool(estimate_acceptance_apply_result.get('is_approved')))}"
                    )
                    apply_message += (
                        f"\nSource Estimate: #{estimate_acceptance_apply_result.get('source_estimate_id') or '-'}"
                    )
                    warnings = (
                        estimate_acceptance_apply_result.get("warnings")
                        if isinstance(estimate_acceptance_apply_result.get("warnings"), list)
                        else []
                    )
                    if warnings:
                        apply_message += "\nWarnings: " + "; ".join(str(item) for item in warnings)
                po_eta_apply_result = result.save_result.get("POEtaStatusApplyResult")
                if isinstance(po_eta_apply_result, dict):
                    apply_message += "\n\nPO ETA / Status Observation Result:"
                    if po_eta_apply_result.get("created_observation_id"):
                        apply_message += (
                            f"\nObservation: created #{po_eta_apply_result.get('created_observation_id')}"
                        )
                    elif po_eta_apply_result.get("used_existing_observation_id"):
                        apply_message += (
                            f"\nObservation: reused #{po_eta_apply_result.get('used_existing_observation_id')}"
                        )
                    else:
                        apply_message += "\nObservation: none"
                    apply_message += (
                        f"\nPurchase Order: #{po_eta_apply_result.get('updated_purchase_order_id') or '-'}"
                    )
                    apply_message += f"\nETA Date: {po_eta_apply_result.get('eta_date') or '-'}"
                    apply_message += (
                        f"\nNormalized Intent: {po_eta_apply_result.get('normalized_intent') or '-'}"
                    )
                    apply_message += f"\nStatus: {po_eta_apply_result.get('status') or '-'}"
                    warnings = (
                        po_eta_apply_result.get("warnings")
                        if isinstance(po_eta_apply_result.get("warnings"), list)
                        else []
                    )
                    if warnings:
                        apply_message += "\nWarnings: " + "; ".join(str(item) for item in warnings)
                staged_receipt_apply_result = result.save_result.get("StagedReceiptApplyResult")
                if isinstance(staged_receipt_apply_result, dict):
                    apply_message += "\n\nStaged Receipt Result:"
                    if staged_receipt_apply_result.get("created_receipt_id"):
                        apply_message += (
                            f"\nReceipt: created #{staged_receipt_apply_result.get('created_receipt_id')}"
                        )
                    elif staged_receipt_apply_result.get("used_existing_receipt_id"):
                        apply_message += (
                            f"\nReceipt: reused #{staged_receipt_apply_result.get('used_existing_receipt_id')}"
                        )
                    else:
                        apply_message += "\nReceipt: none"
                    apply_message += (
                        f"\nPurchase Order: #{staged_receipt_apply_result.get('updated_purchase_order_id') or '-'}"
                    )
                    apply_message += (
                        f"\nPacking Slip: {staged_receipt_apply_result.get('packing_slip_number') or '-'}"
                    )
                    apply_message += f"\nReceive Date: {staged_receipt_apply_result.get('receive_date') or '-'}"
                    apply_message += (
                        f"\nReceived Item Count: {staged_receipt_apply_result.get('received_item_count') or '-'}"
                    )
                    apply_message += (
                        f"\nPO Status: {staged_receipt_apply_result.get('purchase_order_status') or '-'}"
                    )
                    warnings = (
                        staged_receipt_apply_result.get("warnings")
                        if isinstance(staged_receipt_apply_result.get("warnings"), list)
                        else []
                    )
                    if warnings:
                        apply_message += "\nWarnings: " + "; ".join(str(item) for item in warnings)
            QMessageBox.information(
                self,
                "Apply Proposal",
                apply_message,
            )
        else:
            QMessageBox.warning(
                self,
                "Apply Proposal",
                result.error_message or "Proposal could not be applied.",
            )

    def _handle_question_selection_changed(self) -> None:
        row = self.questions_table.currentRow()
        if row < 0:
            self._render_question_detail(None)
            return
        item = self.questions_table.item(row, 0)
        if item is None:
            self._render_question_detail(None)
            return
        question_id = item.data(Qt.ItemDataRole.UserRole)
        for question in self._question_records:
            if question.automation_question_id == question_id:
                self._render_question_detail(question)
                return
        self._render_question_detail(None)

    def _selected_question_record(self) -> AutomationQuestionRecord | None:
        question_id = self._selected_question_id()
        if question_id is None:
            return None
        for question in self._question_records:
            if question.automation_question_id == question_id:
                return question
        return None

    def _handle_question_answer_clicked(self) -> None:
        question = self._selected_question_record()
        if question is None:
            return
        try:
            updated = answer_question_structured(
                question.automation_question_id,
                answer_json=self._build_question_answer_payload(question),
                answered_by="Automation Center",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Answer Question", str(exc))
            return
        self.refresh_data()
        QMessageBox.information(
            self,
            "Answer Question",
            f"Question #{question.automation_question_id} is now {updated.status if updated else 'Answered'}.",
        )

    def _handle_question_dismiss_clicked(self) -> None:
        question = self._selected_question_record()
        if question is None:
            return
        reason = self.question_operator_note_text.toPlainText().strip()
        if not reason:
            reason, accepted = QInputDialog.getMultiLineText(
                self,
                "Dismiss Question",
                "Enter the dismissal reason.",
                text="",
            )
            if not accepted:
                return
            reason = str(reason or "").strip()
        try:
            updated = dismiss_question_with_reason(
                question.automation_question_id,
                reason=reason,
                dismissed_by="Automation Center",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Dismiss Question", str(exc))
            return
        self.refresh_data()
        QMessageBox.information(
            self,
            "Dismiss Question",
            f"Question #{question.automation_question_id} is now {updated.status if updated else 'Dismissed'}.",
        )

    def _handle_question_create_revised_receipt_clicked(self) -> None:
        question = self._selected_question_record()
        if question is None:
            return
        try:
            answer_json = self._build_receipt_resolution_bridge_payload(question)
            result = resolve_packing_slip_question_to_proposal(
                question.automation_question_id,
                answer_json=answer_json,
                answered_by="Automation Center",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Create Revised Receipt Proposal", str(exc))
            return
        self.refresh_data()
        QMessageBox.information(
            self,
            "Create Revised Receipt Proposal",
            (
                f"Receipt proposal #{result.proposal.automation_proposal_id} "
                f"{'reused' if result.proposal_reused else 'created'}.\n"
                "It still requires approval before the staged receipt apply wrapper can receive goods."
            ),
        )

    def _handle_question_create_revised_po_eta_clicked(self) -> None:
        question = self._selected_question_record()
        if question is None:
            return
        try:
            answer_json = self._build_po_eta_resolution_bridge_payload(question)
            result = resolve_po_eta_question_to_proposal(
                question.automation_question_id,
                answer_json=answer_json,
                answered_by="Automation Center",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Create Revised PO ETA Proposal", str(exc))
            return
        self.refresh_data()
        QMessageBox.information(
            self,
            "Create Revised PO ETA Proposal",
            (
                f"PO ETA proposal #{result.proposal.automation_proposal_id} "
                f"{'reused' if result.proposal_reused else 'created'}.\n"
                "It still requires approval before the PO ETA/status apply wrapper can update planning fields."
            ),
        )

    def _handle_question_create_revised_vendor_invoice_clicked(self) -> None:
        question = self._selected_question_record()
        if question is None:
            return
        try:
            answer_json = self._build_vendor_invoice_resolution_bridge_payload(question)
            result = resolve_vendor_invoice_reconciliation_question_to_proposal(
                question.automation_question_id,
                answer_json=answer_json,
                answered_by="Automation Center",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Create Revised Vendor Invoice Proposal", str(exc))
            return
        self.refresh_data()
        QMessageBox.information(
            self,
            "Create Revised Vendor Invoice Proposal",
            (
                f"Vendor invoice proposal #{result.proposal.automation_proposal_id} "
                f"{'reused' if result.proposal_reused else 'created'}.\n"
                "It still requires approval before any Draft VendorInvoice or reconciliation apply path can mutate payables."
            ),
        )

    def _build_question_answer_payload(self, question: AutomationQuestionRecord) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "answer_type": question.question_type,
            "answered_by": "Automation Center",
            "operator_note": self.question_operator_note_text.toPlainText().strip() or None,
            "selected_option": self.question_selected_option_combo.currentData(Qt.ItemDataRole.UserRole)
            if self.question_selected_option_combo.currentIndex() >= 0
            else None,
            "selected_workflow": self.question_selected_workflow_combo.currentText().strip() or None,
            "selected_intent": self.question_selected_intent_input.text().strip() or None,
            "selected_target_type": self.question_selected_target_type_input.text().strip() or None,
            "selected_target_id": self.question_selected_target_id_input.text().strip() or None,
            "confidence_after_review": 1.0,
            "save_as_memory": self.question_save_routing_memory_checkbox.isChecked(),
            "save_as_routing_memory": self.question_save_routing_memory_checkbox.isChecked(),
            "followup_required": self.question_followup_required_checkbox.isChecked(),
            "request_more_info_needed": self.question_request_more_info_checkbox.isChecked(),
        }
        if question.question_type == "inbound_routing_review":
            payload["how_i_know"] = payload.get("operator_note")
        selected_option_text = self.question_selected_option_combo.currentText().strip()
        if payload["selected_option"] is None and selected_option_text:
            payload["selected_option"] = selected_option_text
        if payload["selected_target_id"] in {"", "-", "None"}:
            payload["selected_target_id"] = None
        if payload["selected_target_type"] in {"", "-", "None"}:
            payload["selected_target_type"] = None
        if payload["selected_intent"] in {"", "-", "None"}:
            payload["selected_intent"] = None
        if payload["selected_workflow"] in {"", "-", "None"}:
            payload["selected_workflow"] = None
        return payload

    def _build_receipt_resolution_bridge_payload(self, question: AutomationQuestionRecord) -> dict[str, Any]:
        raw_text = self.question_receipt_resolution_json.toPlainText().strip()
        if raw_text:
            try:
                parsed = json.loads(raw_text)
            except Exception as exc:
                raise ValueError(f"Receipt Resolution JSON is invalid: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("Receipt Resolution JSON must be an object.")
            if not parsed.get("operator_note"):
                parsed["operator_note"] = self.question_operator_note_text.toPlainText().strip() or None
            parsed.setdefault("answered_by", "Automation Center")
            parsed.setdefault("answer_type", "packing_slip_receipt_resolution")
            return parsed
        raise ValueError("Receipt Resolution JSON is required to create a revised receipt proposal.")

    def _build_po_eta_resolution_bridge_payload(self, question: AutomationQuestionRecord) -> dict[str, Any]:
        raw_text = self.question_po_eta_resolution_json.toPlainText().strip()
        if raw_text:
            try:
                parsed = json.loads(raw_text)
            except Exception as exc:
                raise ValueError(f"PO ETA Resolution JSON is invalid: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("PO ETA Resolution JSON must be an object.")
            operator_note = self.question_operator_note_text.toPlainText().strip()
            if not parsed.get("operator_note"):
                parsed["operator_note"] = operator_note or None
            if not parsed.get("how_i_know"):
                parsed["how_i_know"] = operator_note or None
            parsed.setdefault("answered_by", "Automation Center")
            parsed.setdefault("answer_type", "po_eta_status_resolution")
            return parsed
        raise ValueError("PO ETA Resolution JSON is required to create a revised PO ETA proposal.")

    def _build_vendor_invoice_resolution_bridge_payload(self, question: AutomationQuestionRecord) -> dict[str, Any]:
        raw_text = self.question_vendor_invoice_resolution_json.toPlainText().strip()
        if raw_text:
            try:
                parsed = json.loads(raw_text)
            except Exception as exc:
                raise ValueError(f"Vendor Invoice Resolution JSON is invalid: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("Vendor Invoice Resolution JSON must be an object.")
            operator_note = self.question_operator_note_text.toPlainText().strip()
            if not parsed.get("operator_note"):
                parsed["operator_note"] = operator_note or None
            if not parsed.get("how_i_know"):
                parsed["how_i_know"] = operator_note or None
            parsed.setdefault("answered_by", "Automation Center")
            parsed.setdefault("answer_type", "vendor_invoice_reconciliation_resolution")
            return parsed
        raise ValueError("Vendor Invoice Resolution JSON is required to create a revised vendor invoice proposal.")

    def _handle_inbound_message_selection_changed(self, *, selected_attachment_id: int | None = None) -> None:
        row = self.inbound_messages_table.currentRow()
        if row < 0:
            self._selected_message_attachments = []
            self._populate_attachment_table(None)
            self._render_inbound_message_detail(None)
            self._render_inbound_attachment_detail(None)
            return
        item = self.inbound_messages_table.item(row, 0)
        if item is None:
            self._selected_message_attachments = []
            self._populate_attachment_table(None)
            self._render_inbound_message_detail(None)
            self._render_inbound_attachment_detail(None)
            return
        message_id = item.data(Qt.ItemDataRole.UserRole)
        selected_message = None
        for message in self._inbound_messages:
            if message.inbound_message_id == message_id:
                selected_message = message
                break
        self._render_inbound_message_detail(selected_message)
        if selected_message is None:
            self._selected_message_attachments = []
            self._populate_attachment_table(None)
            self._render_inbound_attachment_detail(None)
            return
        try:
            self._selected_message_attachments = list_inbound_attachments(inbound_message_id=selected_message.inbound_message_id, limit=100)
        except Exception as exc:
            self._selected_message_attachments = []
            self._populate_attachment_table(None)
            self.inbound_extraction_json.setPlainText(f"Inbound attachments unavailable: {exc}")
            return
        self._populate_attachment_table(selected_attachment_id)

    def _populate_attachment_table(self, selected_attachment_id: int | None) -> None:
        self.inbound_attachments_table.setRowCount(0)
        for row_index, attachment in enumerate(self._selected_message_attachments):
            self.inbound_attachments_table.insertRow(row_index)
            self._set_table_item(
                self.inbound_attachments_table,
                row_index,
                0,
                str(attachment.inbound_attachment_id),
                user_data=attachment.inbound_attachment_id,
            )
            self._set_table_item(self.inbound_attachments_table, row_index, 1, attachment.filename or "-")
            self._set_table_item(self.inbound_attachments_table, row_index, 2, attachment.mime_type or "-")
            self._set_table_item(
                self.inbound_attachments_table,
                row_index,
                3,
                self._nullable_text(attachment.file_size),
                align_right=True,
            )
            self._set_table_item(self.inbound_attachments_table, row_index, 4, attachment.status)
            self._set_table_item(self.inbound_attachments_table, row_index, 5, self._format_timestamp(attachment.imported_at))
        self.inbound_attachments_table.resizeColumnsToContents()
        if self._selected_message_attachments:
            target_attachment_id = (
                selected_attachment_id
                if any(record.inbound_attachment_id == selected_attachment_id for record in self._selected_message_attachments)
                else self._selected_message_attachments[0].inbound_attachment_id
            )
            self._select_inbound_attachment_id(target_attachment_id)
        else:
            self._render_inbound_attachment_detail(None)

    def _handle_inbound_attachment_selection_changed(self) -> None:
        row = self.inbound_attachments_table.currentRow()
        if row < 0:
            self._render_inbound_attachment_detail(None)
            return
        item = self.inbound_attachments_table.item(row, 0)
        if item is None:
            self._render_inbound_attachment_detail(None)
            return
        attachment_id = item.data(Qt.ItemDataRole.UserRole)
        for attachment in self._selected_message_attachments:
            if attachment.inbound_attachment_id == attachment_id:
                self._render_inbound_attachment_detail(attachment)
                return
        self._render_inbound_attachment_detail(None)

    def _handle_extract_attachment_clicked(self) -> None:
        attachment_id = self._selected_inbound_attachment_id()
        if attachment_id is None:
            QMessageBox.information(self, "Extract Text", "Select an inbound attachment first.")
            return
        try:
            extract_text_for_attachment(int(attachment_id), force=False, source="manual_ui")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.warning(self, "Extract Text", str(exc))

    def _handle_create_attachment_review_clicked(self) -> None:
        attachment_id = self._selected_inbound_attachment_id()
        if attachment_id is None:
            QMessageBox.information(self, "Attachment Review", "Select an inbound attachment first.")
            return
        try:
            question = create_attachment_review_question(int(attachment_id), source="manual_ui")
            self.refresh_data()
        except Exception as exc:
            QMessageBox.warning(self, "Attachment Review", str(exc))
            return
        QMessageBox.information(
            self,
            "Attachment Review",
            f"Created/located attachment review question #{question.automation_question_id}.",
        )

    def _event_for_row(self, row: int) -> AutomationEventRecord | None:
        item = self.events_table.item(row, 0)
        if item is None:
            return None
        event_id = item.data(Qt.ItemDataRole.UserRole)
        for event in self._selected_run_events or self._recent_events_24h:
            if event.automation_event_id == event_id:
                return event
        return None

    def _render_preset_detail(self, preset: AutomationPreset | None) -> None:
        if preset is None:
            self.detail_key.setText("-")
            self.detail_name.setText("Select a preset")
            self.detail_workflow.setText("-")
            self.detail_risk.setText("-")
            self.detail_runtime.setText("-")
            self.detail_legacy.setText("-")
            self.detail_env.setText("-")
            self.detail_status.setText("-")
            self.detail_setting_state.setText("-")
            self.detail_last_run.setText("-")
            self.detail_last_error.setText("-")
            self.detail_text.setHtml("<p>Select a preset from the catalogue to inspect its Level 2 / Level 3 plan.</p>")
            return

        setting = self._settings_by_key.get(preset.key) or default_automation_setting_for_preset(preset)
        latest_run = self._latest_runs_by_key.get(preset.key)
        self.detail_key.setText(preset.key)
        self.detail_name.setText(preset.display_name)
        self.detail_workflow.setText(preset.workflow)
        self.detail_risk.setText(preset.risk_level)
        self.detail_runtime.setText(preset.runtime_state)
        self.detail_legacy.setText(preset.legacy_state)
        self.detail_env.setText(preset.env_control)
        self.detail_setting_state.setText(self._setting_state_detail(setting))
        self.detail_last_run.setText(self._last_run_summary(latest_run))
        self.detail_last_error.setText(latest_run.error_message if latest_run and latest_run.error_message else "-")
        self.detail_status.setText(preset.next_implementation_status)
        self.detail_text.setHtml(self._detail_html(preset, setting, latest_run))

    def _detail_html(
        self,
        preset: AutomationPreset,
        setting: AutomationSettingRecord,
        latest_run: AutomationRunRecord | None,
    ) -> str:
        parts = [
            f"<h3>{self._escape(preset.display_name)}</h3>",
            f"<p>{self._escape(preset.description)}</p>",
            f"<p><b>Level 2 Mode:</b> {self._escape(preset.level_2_mode)}<br>"
            f"<b>Level 3 Mode:</b> {self._escape(preset.level_3_mode)}<br>"
            f"<b>Approval Required:</b> {self._escape(self._bool_text(preset.approval_required))}<br>"
            f"<b>Email Send Allowed:</b> {self._escape(self._bool_text(preset.email_send_allowed))}<br>"
            f"<b>External Action Allowed:</b> {self._escape(self._bool_text(preset.external_action_allowed))}</p>",
            (
                f"<h4>Persisted / Default Setting State</h4>"
                f"<p>{self._escape(self._setting_state_detail(setting))}<br>"
                f"<b>Frequency Minutes:</b> {self._escape(self._nullable_text(setting.frequency_minutes))}<br>"
                f"<b>Max Tokens Per Run:</b> {self._escape(self._nullable_text(setting.max_tokens_per_run))}<br>"
                f"<b>Max Actions Per Run:</b> {self._escape(self._nullable_text(setting.max_actions_per_run))}<br>"
                f"<b>Updated At:</b> {self._escape(self._format_timestamp(setting.updated_at))}<br>"
                f"<b>Setting Notes:</b> {self._escape(setting.notes or '-')}</p>"
            ),
            (
                f"<h4>Last Run / Last Error</h4>"
                f"<p><b>Last Run:</b> {self._escape(self._last_run_summary(latest_run))}<br>"
                f"<b>Last Error:</b> {self._escape(latest_run.error_message if latest_run and latest_run.error_message else '-')}</p>"
            ),
            self._section_html("Allowed Level 2 Reads", preset.allowed_reads),
            self._section_html("Allowed Level 2 Writes", preset.allowed_writes),
            self._section_html("Disallowed Actions", preset.disallowed_actions),
            self._section_html("Planned Memory Scopes", preset.memory_scopes),
            self._section_html("Planned Proposal Types", preset.proposal_types),
            self._section_html("Planned Question Types", preset.question_types),
            f"<h4>Notes</h4><p>{self._escape(preset.notes)}</p>",
            f"<h4>Next Implementation Status</h4><p>{self._escape(preset.next_implementation_status)}</p>",
        ]
        return "".join(parts)

    def _section_html(self, title: str, values: tuple[str, ...]) -> str:
        if not values:
            return f"<h4>{self._escape(title)}</h4><p>None in this slice.</p>"
        items = "".join(f"<li>{self._escape(value)}</li>" for value in values)
        return f"<h4>{self._escape(title)}</h4><ul>{items}</ul>"

    def _set_table_item(
        self,
        table: QTableWidget,
        row: int,
        column: int,
        value: str,
        *,
        user_data=None,
        align_right: bool = False,
    ) -> None:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if user_data is not None:
            item.setData(Qt.ItemDataRole.UserRole, user_data)
        if align_right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        table.setItem(row, column, item)

    def _event_target_text(self, event: AutomationEventRecord) -> str:
        if event.target_type and event.target_id:
            return f"{event.target_type} #{event.target_id}"
        if event.target_type:
            return event.target_type
        if event.target_id:
            return str(event.target_id)
        return "-"

    def _event_json_preview_text(self, event: AutomationEventRecord) -> str:
        if event.event_json is None:
            return "-"
        if isinstance(event.event_json, str):
            return event.event_json[:120]
        try:
            return json.dumps(event.event_json, sort_keys=True)[:120]
        except Exception:
            return str(event.event_json)[:120]

    def _event_json_detail_text(self, event: AutomationEventRecord) -> str:
        lines = [
            f"AutomationEventID: {event.automation_event_id}",
            f"AutomationRunID: {event.automation_run_id if event.automation_run_id is not None else '-'}",
            f"AutomationKey: {event.automation_key}",
            f"EventType: {event.event_type}",
            f"CreatedAt: {self._format_timestamp(event.created_at)}",
            f"Target: {self._event_target_text(event)}",
            "",
            f"Summary: {event.summary}",
            "",
            "EventJson:",
        ]
        if event.event_json is None:
            lines.append("-")
        elif isinstance(event.event_json, str):
            lines.append(event.event_json)
        else:
            lines.append(json.dumps(event.event_json, indent=2, sort_keys=True))
        return "\n".join(lines)

    def _json_detail_text(self, value) -> str:
        if value is None:
            return "-"
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, indent=2, sort_keys=True)
        except Exception:
            return str(value)

    def _render_policy_detail(self, policy: AutomationPolicyDefinition | None) -> None:
        if policy is None:
            self.policy_detail_key.setText("-")
            self.policy_detail_value.setText("-")
            self.policy_detail_enforcement.setText("-")
            self.policy_description_text.setPlainText(
                "Select a policy to inspect its current default value and enforcement notes."
            )
            self.policy_notes_text.setPlainText(
                "Select a policy to inspect implementation notes."
            )
            return

        self.policy_detail_key.setText(policy.policy_key)
        self.policy_detail_value.setText(self._policy_value_text(policy.value))
        self.policy_detail_enforcement.setText(policy.enforcement_status)
        self.policy_description_text.setPlainText(policy.description or "-")
        self.policy_notes_text.setPlainText(policy.notes or "-")

    def _render_memory_detail(self, memory: AutomationMemoryRecord | None) -> None:
        if memory is None:
            self.memory_detail_scope.setText("-")
            self.memory_detail_type.setText("-")
            self.memory_detail_source.setText("-")
            self.memory_detail_confidence.setText("-")
            self.memory_detail_lifecycle.setText("-")
            self.memory_content_text.setPlainText("Select a memory record to inspect its text content.")
            self.memory_content_json.setPlainText("Select a memory record to inspect its JSON content.")
            return

        self.memory_detail_scope.setText(f"{memory.scope_type} / {memory.scope_id or '-'}")
        self.memory_detail_type.setText(memory.memory_type)
        self.memory_detail_source.setText(memory.source or "-")
        self.memory_detail_confidence.setText(self._format_confidence(memory.confidence))
        self.memory_detail_lifecycle.setText(
            f"Active: {self._bool_text(memory.is_active)} | "
            f"Created: {self._format_timestamp(memory.created_at)} | "
            f"Expires: {self._format_timestamp(memory.expires_at)}"
        )
        self.memory_content_text.setPlainText(memory.content_text or "-")
        if memory.content_json is None:
            self.memory_content_json.setPlainText("-")
        elif isinstance(memory.content_json, str):
            self.memory_content_json.setPlainText(memory.content_json)
        else:
            self.memory_content_json.setPlainText(json.dumps(memory.content_json, indent=2, sort_keys=True))

    def _render_obligation_detail(self, obligation: WorkflowObligationRecord | None) -> None:
        if obligation is None:
            self.obligation_detail_meta.setText("-")
            self.obligation_detail_source.setText("-")
            self.obligation_detail_lifecycle.setText("-")
            self.obligation_detail_owner.setText("-")
            self.obligation_review_summary.setPlainText(
                "Select a workflow obligation to inspect the internal expected-event board and its safe edit history."
            )
            self.obligation_change_log_text.setPlainText("Select a workflow obligation to inspect its change log.")
            self._update_obligation_action_buttons(None)
            return

        operator_summary = build_obligation_operator_summary(obligation)
        self.obligation_detail_meta.setText(
            f"#{obligation.obligation_id} | {obligation.obligation_type or '-'} | "
            f"Status: {obligation.status} | Priority: {obligation.priority or obligation.severity or '-'} | "
            f"{operator_summary['due_summary']}"
        )
        self.obligation_detail_source.setText(operator_summary["source_summary"])
        self.obligation_detail_lifecycle.setText(
            f"Workflow: {obligation.workflow_type or '-'} | "
            f"ExpectedEvent: {obligation.expected_event_type or '-'} | "
            f"Created: {self._format_timestamp(obligation.created_at)} | "
            f"Updated: {self._format_timestamp(obligation.updated_at)} | "
            f"LastChecked: {self._format_timestamp(obligation.last_checked_at)} | "
            f"LastProposalID: {self._nullable_text(obligation.last_proposal_id)} | "
            f"SatisfiedAt: {self._format_timestamp(obligation.satisfied_at)}"
        )
        self.obligation_detail_owner.setText(
            f"Owner: {operator_summary['owner_summary']} | "
            f"Cash-Flow Critical: {self._bool_text(bool(operator_summary['cash_flow_critical']))} | "
            f"Needs Human: {self._bool_text(bool(operator_summary['needs_human']))} | "
            f"Overdue: {self._bool_text(bool(operator_summary['overdue']))}"
        )
        metadata = build_obligation_queue_metadata(obligation)
        recent_history = operator_summary["recent_history_summary"] if isinstance(operator_summary["recent_history_summary"], list) else []
        queue_lines = [
            "Operator Summary:",
            f"Age: {metadata['age_text']}",
            f"Due: {operator_summary['due_summary']}",
            f"Owner: {operator_summary['owner_summary']}",
            f"Cash-Flow Critical: {self._bool_text(bool(operator_summary['cash_flow_critical']))}",
            f"Needs Human: {self._bool_text(bool(operator_summary['needs_human']))}",
            f"Risk / Flags: {operator_summary['risk_summary']}",
            "",
            "Next Operator Action:",
            str(operator_summary["next_operator_action"]),
            "",
            "Recent Obligation History:",
        ]
        if recent_history:
            for entry in recent_history:
                queue_lines.append(f"  - {entry}")
        else:
            queue_lines.append("  - No recent obligation history.")
        queue_lines.append("")
        self.obligation_review_summary.setPlainText(
            "\n".join(queue_lines) + self._workflow_obligation_detail_text(obligation)
        )
        self.obligation_change_log_text.setPlainText(self._workflow_obligation_change_log_text())
        self._update_obligation_action_buttons(obligation)

    def _selected_obligation_record(self) -> WorkflowObligationRecord | None:
        obligation_id = self._selected_obligation_id()
        if obligation_id is None:
            return None
        for obligation in self._obligation_records:
            if obligation.obligation_id == obligation_id:
                return obligation
        return None

    def _update_obligation_action_buttons(self, obligation: WorkflowObligationRecord | None) -> None:
        enabled = obligation is not None
        for button in (
            self.obligation_edit_due_button,
            self.obligation_edit_severity_button,
            self.obligation_edit_text_button,
            self.obligation_snooze_button,
            self.obligation_mark_satisfied_button,
            self.obligation_dismiss_button,
            self.obligation_add_note_button,
        ):
            button.setEnabled(enabled)

    def _render_proposal_detail(self, proposal: AutomationProposalRecord | None) -> None:
        if proposal is None:
            self.proposal_detail_meta.setText("-")
            self.proposal_detail_approval.setText("-")
            self.proposal_detail_blocked.setText("-")
            self.proposal_detail_status.setText("-")
            self.proposal_review_summary.setPlainText("Select a proposal to inspect quote header, proposed line prices, and supporting evidence.")
            self.proposal_change_json.setPlainText("Select a proposal to inspect its structured change payload.")
            self.proposal_evidence_json.setPlainText("Select a proposal to inspect its structured evidence payload.")
            self._update_proposal_action_buttons(None)
            return

        self.proposal_detail_meta.setText(
            f"#{proposal.automation_proposal_id} | {proposal.automation_key} | "
            f"{proposal.workflow} | {proposal.action_type} | "
            f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}"
        )
        self.proposal_detail_approval.setText(
            f"RequiresApproval: {self._bool_text(proposal.requires_approval)} | "
            f"CanAutoApplyLevel2: {self._bool_text(proposal.can_auto_apply_level_2)} | "
            f"Confidence: {self._format_confidence(proposal.confidence)} | "
            f"Risk: {proposal.risk_level}"
        )
        self.proposal_detail_blocked.setText(proposal.blocked_reason or "-")
        self.proposal_detail_status.setText(
            f"Status: {proposal.status} | Created: {self._format_timestamp(proposal.created_at)} | "
            f"Approved: {self._format_timestamp(proposal.approved_at)} | "
            f"Rejected: {self._format_timestamp(proposal.rejected_at)} | "
            f"Applied: {self._format_timestamp(proposal.applied_at)} | "
            f"Error: {proposal.error_message or '-'}"
        )
        metadata = build_proposal_queue_metadata(proposal)
        queue_lines = [
            "Queue Metadata:",
            f"Age: {metadata['age_text']}",
            f"Overdue Age: {_age_text(int(metadata['overdue_days'])) if metadata['is_overdue'] else '-'}",
            f"Cash-Flow Critical: {self._bool_text(bool(metadata['cash_flow_critical']))}",
            f"Operator Action Needed: {self._bool_text(bool(metadata['operator_action_needed']))}",
            f"Suggested Next Step: {metadata['suggested_next_step']}",
            "",
        ]
        self.proposal_review_summary.setPlainText(
            "\n".join(queue_lines) + self._proposal_review_summary_text(proposal)
        )
        self.proposal_change_json.setPlainText(self._json_detail_text(proposal.proposed_change_json))
        self.proposal_evidence_json.setPlainText(self._json_detail_text(proposal.evidence_json))
        self._update_proposal_action_buttons(proposal)

    def _selected_proposal_record(self) -> AutomationProposalRecord | None:
        proposal_id = self._selected_proposal_id()
        if proposal_id is None:
            return None
        for proposal in self._proposal_records:
            if proposal.automation_proposal_id == proposal_id:
                return proposal
        return None

    def _render_outbound_draft_detail(self, draft: OutboundDraftReviewRecord | None) -> None:
        if draft is None:
            self.outbound_draft_detail_meta.setText("-")
            self.outbound_draft_detail_recipient.setText("-")
            self.outbound_draft_detail_source.setText("-")
            self.outbound_draft_detail_status.setText("-")
            self.outbound_draft_detail_safety.setText("-")
            self.outbound_draft_body_preview.setPlainText(
                "Select an outbound draft to inspect the read-only prepared/sent/failed record."
            )
            self.outbound_draft_review_json.setPlainText(
                "Select a Prepared outbound draft to inspect or send one approved, allowlisted test-mode email."
            )
            self._update_outbound_draft_action_buttons(None)
            return

        detail = build_outbound_draft_detail(draft)
        operator_summary = detail.get("operator_summary") if isinstance(detail.get("operator_summary"), dict) else {}
        blocked_reasons = operator_summary.get("blocked_reasons")
        if not isinstance(blocked_reasons, list):
            blocked_reasons = []
        blocked_text = "; ".join(str(reason) for reason in blocked_reasons) if blocked_reasons else "-"
        self.outbound_draft_detail_meta.setText(
            f"#{draft.outbound_message_log_id} | {draft.template_code or '-'} | "
            f"Created: {self._format_timestamp(draft.created_at)} | CreatedBy: {draft.created_by or '-'}"
        )
        self.outbound_draft_detail_recipient.setText(
            f"To: {draft.recipient_email or '-'} | Original: {draft.original_recipient_email or '-'} | "
            f"CC: {draft.cc_email or '-'}"
        )
        self.outbound_draft_detail_source.setText(
            f"{draft.source_summary} | Attachment: {draft.attachment_path or '-'}"
        )
        self.outbound_draft_detail_status.setText(
            "\n".join(
                [
                    f"Status: {operator_summary.get('status_summary') or draft.send_status}",
                    f"Eligible to send: {self._bool_text(bool(operator_summary.get('send_eligible')))}",
                    f"Blocked reasons: {blocked_text}",
                    f"Failure: {operator_summary.get('failure_summary') or '-'}",
                    f"SentAt: {self._format_timestamp(draft.sent_at)}",
                    f"ProviderMessageID present: {self._bool_text(draft.provider_message_id_present)}",
                    f"Next action: {operator_summary.get('next_operator_action') or '-'}",
                ]
            )
        )
        self.outbound_draft_detail_safety.setText(
            "\n".join(
                [
                    str(operator_summary.get("test_mode_summary") or "-"),
                    str(operator_summary.get("allowlist_summary") or "-"),
                    str(operator_summary.get("provider_summary") or "-"),
                    str(operator_summary.get("safety_summary") or "-"),
                ]
            )
        )
        self.outbound_draft_body_preview.setPlainText(
            "\n".join(
                [
                    f"Subject: {draft.subject or '-'}",
                    "",
                    str(detail.get("body_preview") or "-"),
                ]
            )
        )
        self.outbound_draft_review_json.setPlainText(json.dumps(detail, indent=2, sort_keys=True, default=str))
        self._update_outbound_draft_action_buttons(draft)

    def _selected_outbound_draft_record(self) -> OutboundDraftReviewRecord | None:
        outbound_draft_id = self._selected_outbound_draft_id()
        if outbound_draft_id is None:
            return None
        for draft in self._outbound_draft_records:
            if draft.outbound_message_log_id == outbound_draft_id:
                return draft
        return None

    def _update_outbound_draft_action_buttons(self, draft: OutboundDraftReviewRecord | None) -> None:
        self.outbound_draft_send_button.setEnabled(outbound_draft_send_action_enabled(draft))

    def _update_proposal_action_buttons(self, proposal: AutomationProposalRecord | None) -> None:
        if proposal is None:
            self.proposal_approve_button.setEnabled(False)
            self.proposal_reject_button.setEnabled(False)
            self.proposal_apply_button.setEnabled(False)
            self.proposal_apply_button.setText("Apply Approved Proposal")
            return
        is_supported = supports_apply_action(proposal.action_type)
        can_approve = proposal.status == "Pending"
        can_reject = proposal.status in {"Pending", "Approved"}
        can_apply = is_supported and proposal.status in {"Approved", "Applied"}
        self.proposal_approve_button.setEnabled(can_approve)
        self.proposal_reject_button.setEnabled(can_reject)
        self.proposal_apply_button.setEnabled(can_apply)
        if proposal.action_type == "estimate_followup_due_observation":
            self.proposal_apply_button.setText("Create Follow-up Draft")
        elif proposal.action_type == "estimate_acceptance_observation":
            self.proposal_apply_button.setText("Create WorkOrder Draft")
        elif proposal.action_type == "customer_invoice_due_observation":
            self.proposal_apply_button.setText("Create Customer Invoice Draft")
        elif proposal.action_type == "po_eta_status_observation":
            self.proposal_apply_button.setText("Record PO ETA Observation")
        elif proposal.action_type == "staged_receipt_observation":
            self.proposal_apply_button.setText("Apply Staged Receipt")
        elif proposal.action_type == "lead_intake_observation":
            self.proposal_apply_button.setText("Create Intake Draft Records")
        elif proposal.action_type == "vendor_invoice_intake_observation":
            self.proposal_apply_button.setText("Create Vendor Invoice Draft")
        else:
            self.proposal_apply_button.setText("Apply Approved Proposal")

    def _proposal_review_summary_text(self, proposal: AutomationProposalRecord) -> str:
        proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
        evidence = proposal.evidence_json if isinstance(proposal.evidence_json, dict) else {}
        if proposal.action_type == "po_eta_status_observation":
            return self._po_eta_proposal_review_summary_text(proposal, proposed_change, evidence)
        if proposal.action_type in {"staged_receipt_observation", "staged_receipt_proposal"}:
            return self._staged_receipt_proposal_review_summary_text(proposal, proposed_change, evidence)
        if proposal.action_type == "vendor_invoice_intake_observation":
            return self._vendor_invoice_proposal_review_summary_text(proposal, proposed_change, evidence)
        if proposal.action_type == "vendor_invoice_customer_billing_review_observation":
            return self._vendor_invoice_customer_billing_review_summary_text(proposal, proposed_change, evidence)
        if proposal.action_type == "customer_scheduling_service_observation":
            return self._customer_scheduling_proposal_review_summary_text(proposal, proposed_change, evidence)
        if proposal.action_type == "lead_intake_observation":
            return self._lead_intake_proposal_review_summary_text(proposal, proposed_change, evidence)
        if proposal.action_type in {
            "estimate_reply_observation",
            "estimate_acceptance_observation",
            "estimate_revision_observation",
            "estimate_rejection_observation",
            "estimate_schedule_request_observation",
        }:
            return self._estimate_followup_proposal_review_summary_text(proposal, proposed_change, evidence)
        if proposal.action_type in {
            "estimate_followup_due_observation",
            "customer_invoice_due_observation",
        }:
            return self._workflow_obligation_proposal_review_summary_text(proposal, proposed_change, evidence)
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Proposed Quote Number: {proposed_change.get('vendor_quote_number') or '-'}")
        lines.append(f"Proposed Quote Date: {proposed_change.get('vendor_quote_date') or '-'}")
        lines.append("Proposed Line Prices:")
        quoted_lines = proposed_change.get("quoted_unit_prices") if isinstance(proposed_change.get("quoted_unit_prices"), list) else []
        if not quoted_lines:
            lines.append("  - none")
        else:
            for item in quoted_lines:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"PRItemID {item.get('pr_item_id') or '-'} | "
                    f"MaterialCallItemID {item.get('material_call_item_id') or '-'} | "
                    f"Part {item.get('part_number') or '-'} | "
                    f"{item.get('description') or '-'} | "
                    f"Quote Price {item.get('quoted_unit_price')}"
                )
        lines.append("")
        lines.append(f"Inbound Message ID: {evidence.get('inbound_message_id') or '-'}")
        lines.append(f"External Message ID: {evidence.get('external_message_id') or '-'}")
        lines.append(f"Sender: {evidence.get('sender_name') or evidence.get('sender') or '-'}")
        lines.append(f"Attachments: {', '.join(str(name) for name in (evidence.get('attachment_filenames') or [])) or '-'}")
        lines.append("Excerpt:")
        lines.append(str(evidence.get("source_excerpt") or "-"))
        return "\n".join(lines)

    def _workflow_obligation_detail_text(self, obligation: WorkflowObligationRecord) -> str:
        operator_summary = build_obligation_operator_summary(obligation)
        lines = []
        lines.append(f"Obligation ID: {obligation.obligation_id}")
        lines.append(f"Obligation Type: {obligation.obligation_type or '-'}")
        lines.append(f"Workflow: {obligation.workflow_type}")
        lines.append(f"Expected Event: {obligation.expected_event_type}")
        lines.append(f"Linked Source: {operator_summary['source_summary']}")
        lines.append(f"Due Summary: {operator_summary['due_summary']}")
        lines.append(f"Expected By: {self._format_timestamp(obligation.expected_by)}")
        lines.append(f"Snooze Until: {self._format_timestamp(obligation.snooze_until)}")
        lines.append(f"Priority / Severity: {obligation.priority or '-'} / {obligation.severity}")
        lines.append(f"Status: {obligation.status}")
        lines.append(f"Owner Summary: {operator_summary['owner_summary']}")
        lines.append(f"Next Operator Action: {operator_summary['next_operator_action']}")
        lines.append(f"Cash-Flow Critical: {self._bool_text(bool(operator_summary['cash_flow_critical']))}")
        lines.append(f"Needs Human: {self._bool_text(bool(operator_summary['needs_human']))}")
        lines.append(f"Overdue: {self._bool_text(bool(operator_summary['overdue']))}")
        lines.append(f"Last Checked: {self._format_timestamp(obligation.last_checked_at)}")
        lines.append(f"Last Proposal ID: {self._nullable_text(obligation.last_proposal_id)}")
        lines.append("")
        lines.append("Board Safety Note:")
        lines.append(
            "Editing this obligation changes Neon’s internal follow-up board only. "
            "It does not change the linked business record date, contractual due date, invoice due date, estimate sent date, or PO ETA."
        )
        lines.append("")
        lines.append(f"Title: {obligation.title or '-'}")
        lines.append("Description:")
        lines.append(obligation.description or "-")
        lines.append("")
        lines.append("Resolution Notes:")
        lines.append(obligation.resolution_notes or "-")
        lines.append("")
        lines.append("Notes:")
        lines.append(obligation.notes or "-")
        if isinstance(obligation.evidence_json, dict):
            evidence = obligation.evidence_json
            lines.append("")
            lines.append("Evidence Snapshot:")
            for key in (
                "customer_name",
                "site_name",
                "estimate_status",
                "job_status",
                "is_closed",
                "submit_date",
                "created_date",
                "source_estimate_id",
                "invoice_status",
                "invoice_number",
                "invoice_date",
                "sent_or_exported_date",
                "due_date",
                "payment_status",
                "followup_status",
                "vendor_name",
                "invoice_number",
                "total_amount",
                "po_number",
                "packing_slip_number",
                "checked_signals",
            ):
                if key in evidence:
                    lines.append(f"- {key}: {evidence.get(key)}")
            cash_flow_flags = evidence.get("cash_flow_flags") if isinstance(evidence.get("cash_flow_flags"), list) else []
            if cash_flow_flags:
                lines.append(f"- cash_flow_flags: {', '.join(str(flag) for flag in cash_flow_flags)}")
            reconciliation_flags = evidence.get("reconciliation_flags") if isinstance(evidence.get("reconciliation_flags"), list) else []
            if reconciliation_flags:
                lines.append(f"- reconciliation_flags: {', '.join(str(flag) for flag in reconciliation_flags)}")
            customer_billing = evidence.get("customer_billing_evidence") if isinstance(evidence.get("customer_billing_evidence"), dict) else {}
            if customer_billing:
                lines.append(
                    "- customer_billing: "
                    f"status={customer_billing.get('status') or '-'} | "
                    f"invoice_id={customer_billing.get('customer_invoice_id') or '-'} | "
                    f"invoice_date={customer_billing.get('customer_invoice_date') or '-'}"
                )
            missing_evidence = evidence.get("missing_evidence_list") if isinstance(evidence.get("missing_evidence_list"), list) else []
            if missing_evidence:
                lines.append(f"- missing_evidence: {', '.join(str(item) for item in missing_evidence)}")
        return "\n".join(lines)

    def _workflow_obligation_change_log_text(self) -> str:
        if not self._selected_obligation_change_log:
            return "No WorkflowObligation change-log rows recorded yet for this selection."
        lines: list[str] = []
        for entry in self._selected_obligation_change_log:
            lines.append(
                f"#{entry.change_id} | {self._format_timestamp(entry.changed_at)} | "
                f"{entry.change_source or '-'} / {entry.changed_by or '-'} | {entry.reason or '-'}"
            )
            old_status = entry.old_json.get("status") if isinstance(entry.old_json, dict) else None
            new_status = entry.new_json.get("status") if isinstance(entry.new_json, dict) else None
            old_due = entry.old_json.get("expected_by") if isinstance(entry.old_json, dict) else None
            new_due = entry.new_json.get("expected_by") if isinstance(entry.new_json, dict) else None
            old_snooze = entry.old_json.get("snooze_until") if isinstance(entry.old_json, dict) else None
            new_snooze = entry.new_json.get("snooze_until") if isinstance(entry.new_json, dict) else None
            if old_status != new_status or old_due != new_due or old_snooze != new_snooze:
                lines.append(
                    f"  status: {old_status or '-'} -> {new_status or '-'} | "
                    f"expected_by: {old_due or '-'} -> {new_due or '-'} | "
                    f"snooze_until: {old_snooze or '-'} -> {new_snooze or '-'}"
                )
            lines.append("")
        return "\n".join(lines).rstrip()

    def _workflow_obligation_proposal_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Source Record: {proposed_change.get('source_record_type') or '-'} / {proposed_change.get('source_record_id') or '-'}")
        lines.append(f"Expected Event: {proposed_change.get('expected_event_type') or '-'}")
        lines.append(f"Due By: {proposed_change.get('expected_by') or '-'}")
        lines.append(f"Days Overdue: {proposed_change.get('days_overdue') if proposed_change.get('days_overdue') is not None else '-'}")
        lines.append(f"Recommended Action: {proposed_change.get('recommended_action') or '-'}")
        if proposal.action_type == "estimate_followup_due_observation":
            lines.append(f"Estimate #: {proposed_change.get('estimate_id') or proposed_change.get('source_record_id') or '-'}")
            lines.append(f"Estimate Status: {proposed_change.get('estimate_status') or '-'}")
            lines.append(f"Sent Date: {proposed_change.get('sent_date') or '-'}")
            lines.append(f"Customer: {proposed_change.get('customer_name') or '-'}")
            lines.append(f"Site: {proposed_change.get('site_name') or '-'}")
            draft_logs = get_outbound_messages_for_entity("AutomationProposal", proposal.automation_proposal_id)
            draft_log = draft_logs[0] if draft_logs else None
            if draft_log:
                lines.append(f"Draft Log ID: {draft_log.get('OutboundMessageLogID') or '-'}")
                lines.append(f"Draft Status: {draft_log.get('SendStatus') or '-'}")
                lines.append(
                    f"Draft Recipient: "
                    f"{draft_log.get('RecipientEmail') or draft_log.get('OriginalRecipientEmail') or '-'}"
                )
                lines.append(f"Draft Subject: {draft_log.get('Subject') or '-'}")
            elif proposal.status == "Approved":
                lines.append("Apply Result: Awaiting draft creation.")
            elif proposal.status == "Applied":
                lines.append("Apply Result: Proposal is marked Applied but no prepared draft was found.")
            elif proposal.error_message:
                lines.append(f"Apply Result: Blocked - {proposal.error_message}")
        elif proposal.action_type == "customer_invoice_due_observation":
            lines.append(f"Work Order #: {proposed_change.get('work_order_id') or proposed_change.get('source_record_id') or '-'}")
            lines.append(f"Job Status: {proposed_change.get('job_status') or '-'}")
            if proposed_change.get("is_closed") is not None:
                lines.append(f"Closed: {self._bool_text(bool(proposed_change.get('is_closed')))}")
            lines.append(f"Customer: {proposed_change.get('customer_name') or '-'}")
            lines.append(f"Site: {proposed_change.get('site_name') or '-'}")
            if proposed_change.get("source_estimate_id") is not None:
                lines.append(f"Source Estimate #: {proposed_change.get('source_estimate_id')}")
        elif proposal.action_type == "customer_invoice_payment_followup_due_observation":
            lines.append(f"Customer: {proposed_change.get('customer_name') or '-'}")
            lines.append(f"Invoice #: {proposed_change.get('invoice_number') or proposed_change.get('source_record_id') or '-'}")
            lines.append(f"Invoice Date: {proposed_change.get('invoice_date') or '-'}")
            lines.append(f"Sent/Exported Date: {proposed_change.get('sent_or_exported_date') or '-'}")
            lines.append(f"Due Date: {proposed_change.get('due_date') or '-'}")
            lines.append(f"Amount: {self._format_currency(proposed_change.get('total_amount'))}")
            lines.append(f"Payment Status: {proposed_change.get('payment_status') or '-'}")
            lines.append(f"Follow-up Status: {proposed_change.get('followup_status') or '-'}")
            cash_flow_flags = proposed_change.get("cash_flow_flags") if isinstance(proposed_change.get("cash_flow_flags"), list) else []
            if cash_flow_flags:
                lines.append(f"Cash-Flow Flags: {', '.join(str(flag) for flag in cash_flow_flags)}")
        elif proposal.action_type == "vendor_invoice_reconciliation_due_observation":
            lines.append(f"Vendor: {proposed_change.get('vendor_name') or '-'}")
            lines.append(f"Invoice #: {proposed_change.get('invoice_number') or '-'}")
            lines.append(f"Invoice Date: {proposed_change.get('invoice_date') or '-'}")
            lines.append(f"Vendor Due Date: {proposed_change.get('due_date') or '-'}")
            lines.append(f"Amount: {self._format_currency(proposed_change.get('total_amount'))}")
            lines.append(f"PO #: {proposed_change.get('po_number') or '-'}")
            lines.append(f"Packing Slip #: {proposed_change.get('packing_slip_number') or '-'}")
            lines.append(f"Match Outcome: {proposed_change.get('match_outcome') or '-'}")
            lines.append(f"Customer Billing Status: {proposed_change.get('customer_billing_status') or '-'}")
            reconciliation_flags = proposed_change.get("reconciliation_flags") if isinstance(proposed_change.get("reconciliation_flags"), list) else []
            if reconciliation_flags:
                lines.append(f"Reconciliation Flags: {', '.join(str(flag) for flag in reconciliation_flags)}")
        uncertainty_notes = proposed_change.get("uncertainty_notes") if isinstance(proposed_change.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        evidence_obj = proposed_change.get("evidence") if isinstance(proposed_change.get("evidence"), dict) else {}
        if evidence_obj:
            checked_signals = evidence_obj.get("checked_signals") if isinstance(evidence_obj.get("checked_signals"), list) else []
            if checked_signals:
                lines.append(f"Checked Signals: {', '.join(str(item) for item in checked_signals if str(item).strip())}")
        lines.append("")
        lines.append(f"Obligation ID: {evidence.get('obligation_id') or proposed_change.get('obligation_id') or '-'}")
        lines.append(f"Satisfaction Reason: {evidence.get('satisfaction_reason') or '-'}")
        evidence_notes = evidence.get("uncertainty_notes") if isinstance(evidence.get("uncertainty_notes"), list) else []
        if evidence_notes:
            lines.append(f"Evidence Notes: {'; '.join(str(note) for note in evidence_notes)}")
        satisfaction_evidence = evidence.get("satisfaction_evidence") if isinstance(evidence.get("satisfaction_evidence"), dict) else {}
        if satisfaction_evidence:
            lines.append("Evidence Snapshot:")
            for key in (
                "estimate_status",
                "submit_date",
                "created_date",
                "customer_name",
                "site_name",
                "job_status",
                "invoice_status",
                "customer_invoice_date",
                "draft_status",
                "invoice_number",
                "invoice_date",
                "sent_or_exported_date",
                "due_date",
                "payment_status",
                "followup_status",
                "vendor_name",
                "invoice_number",
                "total_amount",
                "po_number",
                "packing_slip_number",
            ):
                value = satisfaction_evidence.get(key)
                if value not in (None, "", []):
                    lines.append(f"  {key}: {value}")
            cash_flow_flags = satisfaction_evidence.get("cash_flow_flags") if isinstance(satisfaction_evidence.get("cash_flow_flags"), list) else []
            if cash_flow_flags:
                lines.append("  cash_flow_flags: " + ", ".join(str(flag) for flag in cash_flow_flags))
            reconciliation_flags = satisfaction_evidence.get("reconciliation_flags") if isinstance(satisfaction_evidence.get("reconciliation_flags"), list) else []
            if reconciliation_flags:
                lines.append("  reconciliation_flags: " + ", ".join(str(flag) for flag in reconciliation_flags))
            customer_billing = satisfaction_evidence.get("customer_billing_evidence") if isinstance(satisfaction_evidence.get("customer_billing_evidence"), dict) else {}
            if customer_billing:
                lines.append(
                    "  customer_billing: "
                    f"status={customer_billing.get('status') or '-'} | "
                    f"invoice_id={customer_billing.get('customer_invoice_id') or '-'} | "
                    f"invoice_date={customer_billing.get('customer_invoice_date') or '-'}"
                )
        return "\n".join(lines)

    def _vendor_invoice_proposal_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Vendor: {proposed_change.get('vendor_name') or '-'}")
        lines.append(f"Invoice #: {proposed_change.get('invoice_number') or '-'}")
        lines.append(f"Invoice Date: {proposed_change.get('invoice_date') or '-'}")
        lines.append(f"Due Date: {proposed_change.get('due_date') or '-'}")
        lines.append(f"Subtotal: {self._format_currency(proposed_change.get('subtotal_amount'))}")
        lines.append(f"Tax: {self._format_currency(proposed_change.get('tax_amount'))}")
        lines.append(f"Total: {self._format_currency(proposed_change.get('total_amount'))}")
        lines.append(f"PO #: {proposed_change.get('po_number') or '-'}")
        lines.append(f"Packing Slip #: {proposed_change.get('packing_slip_number') or '-'}")
        lines.append(f"Work Order Ref: {proposed_change.get('work_order_reference') or '-'}")
        lines.append(f"Match Outcome: {proposed_change.get('match_outcome') or '-'}")
        uncertainty_notes = proposed_change.get("uncertainty_notes") if isinstance(proposed_change.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        line_candidates = proposed_change.get("line_candidates") if isinstance(proposed_change.get("line_candidates"), list) else []
        lines.append(f"Line Candidates: {len(line_candidates)}")
        lines.append("")
        lines.append(f"Inbound Message ID: {evidence.get('inbound_message_id') or '-'}")
        lines.append(f"External Message ID: {evidence.get('external_message_id') or '-'}")
        lines.append(f"Sender: {evidence.get('sender_name') or evidence.get('sender') or '-'}")
        lines.append(f"Subject: {evidence.get('subject') or '-'}")
        lines.append(f"Attachments: {', '.join(str(name) for name in (evidence.get('attachment_filenames') or [])) or '-'}")
        duplicate_result = evidence.get("duplicate_check_result") if isinstance(evidence.get("duplicate_check_result"), dict) else {}
        if duplicate_result:
            lines.append(
                "Duplicate Check: "
                f"supported={self._bool_text(bool(duplicate_result.get('supported')))} | "
                f"checked={self._bool_text(bool(duplicate_result.get('checked')))} | "
                f"duplicate_found={self._bool_text(bool(duplicate_result.get('duplicate_found')))}"
            )
        candidate_po_list = evidence.get("candidate_po_list") if isinstance(evidence.get("candidate_po_list"), list) else []
        if candidate_po_list:
            lines.append(
                "Candidate POs: "
                + "; ".join(
                    f"PO #{item.get('PurchaseOrderID') or '-'} ({item.get('Status') or '-'})"
                    for item in candidate_po_list
                    if isinstance(item, dict)
                )
            )
        candidate_vendor_list = evidence.get("candidate_vendor_list") if isinstance(evidence.get("candidate_vendor_list"), list) else []
        if candidate_vendor_list:
            lines.append(
                "Candidate Vendors: "
                + "; ".join(
                    f"{item.get('vendor_name') or '-'} ({item.get('score') or '-'})"
                    for item in candidate_vendor_list
                    if isinstance(item, dict)
                )
            )
        lines.append("Excerpt:")
        lines.append(str(evidence.get("source_excerpt") or "-"))
        return "\n".join(lines)

    def _vendor_invoice_customer_billing_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Vendor: {proposed_change.get('vendor_name') or '-'}")
        lines.append(f"Vendor Invoice #: {proposed_change.get('vendor_invoice_number') or '-'}")
        lines.append(f"Vendor Amount: {self._format_currency(proposed_change.get('vendor_invoice_total'))}")
        lines.append(f"Vendor Due Date: {proposed_change.get('vendor_invoice_due_date') or '-'}")
        lines.append(f"PO #: {proposed_change.get('purchase_order_number') or proposed_change.get('purchase_order_id') or '-'}")
        lines.append(f"Work Order #: {proposed_change.get('work_order_id') or '-'}")
        lines.append(f"Customer Billing Status: {proposed_change.get('customer_billing_status') or '-'}")
        lines.append(f"Recommended Operator Action: {proposed_change.get('recommended_operator_action') or '-'}")
        if str(proposed_change.get("proposal_type") or "").strip() == "customer_billing_status_review_resolution":
            lines.append(f"Resolution Outcome: {proposed_change.get('resolution_outcome') or '-'}")
            lines.append(f"Resolution Source Question ID: {proposed_change.get('resolution_source_question_id') or '-'}")
            lines.append(f"Selected Customer Invoice ID: {proposed_change.get('selected_customer_invoice_id') or '-'}")
            lines.append(f"Selected Work Order ID: {proposed_change.get('selected_work_order_id') or '-'}")
            lines.append(f"Resolved By: {proposed_change.get('resolved_by') or '-'}")
            lines.append(f"Resolved At: {proposed_change.get('resolved_at') or '-'}")
        cash_flow_flags = proposed_change.get("cash_flow_flags") if isinstance(proposed_change.get("cash_flow_flags"), list) else []
        if cash_flow_flags:
            lines.append(f"Cash-Flow Flags: {', '.join(str(flag) for flag in cash_flow_flags)}")
        customer_invoice_ids = proposed_change.get("customer_invoice_ids") if isinstance(proposed_change.get("customer_invoice_ids"), list) else []
        if customer_invoice_ids:
            lines.append(f"Customer Invoice IDs: {', '.join(str(item) for item in customer_invoice_ids)}")
        uncertainty_notes = proposed_change.get("uncertainty_notes") if isinstance(proposed_change.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        if proposed_change.get("operator_note"):
            lines.append(f"Operator Note: {proposed_change.get('operator_note')}")
        lines.append("")
        lines.append(f"Source Record: {evidence.get('source_record_type') or '-'} / {evidence.get('source_record_id') or '-'}")
        lines.append(f"Source VendorInvoice ID: {evidence.get('source_vendor_invoice_id') or '-'}")
        lines.append(f"Source Proposal ID: {evidence.get('source_proposal_id') or '-'}")
        lines.append(f"Source Inbound Message ID: {evidence.get('source_inbound_message_id') or '-'}")
        lines.append(f"Source Question ID: {evidence.get('source_question_id') or '-'}")
        missing_evidence = evidence.get("missing_evidence_list") if isinstance(evidence.get("missing_evidence_list"), list) else []
        if missing_evidence:
            lines.append(f"Missing Evidence: {', '.join(str(item) for item in missing_evidence)}")
        lines.append("Warning:")
        lines.append(str(evidence.get("warning") or "Review only. No payable authority is granted here."))
        return "\n".join(lines)

    def _po_eta_proposal_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Proposed ETA Date: {proposed_change.get('eta_date') or '-'}")
        lines.append(f"Parts Ready: {self._bool_text(bool(proposed_change.get('parts_ready'))) if proposed_change.get('parts_ready') is not None else '-'}")
        lines.append(f"Backorder Hint: {proposed_change.get('backorder_hint') or '-'}")
        lines.append(f"Pickup Note: {proposed_change.get('pickup_note') or '-'}")
        lines.append(f"Normalized Intent: {proposed_change.get('normalized_intent') or '-'}")
        lines.append("Vendor Message Summary:")
        lines.append(str(proposed_change.get("vendor_message_summary") or "-"))
        lines.append("")
        lines.append("Apply Path:")
        lines.append("- records internal PO ETA/status observation only")
        lines.append("- may update ExpectedArrivalDate / ExpectedArrivalNote planning fields only")
        lines.append("- no receiving")
        lines.append("- no quantity mutation")
        lines.append("- no PO status mutation")
        lines.append("- no invoice")
        lines.append("- no outbound message")
        lines.append("")
        lines.append(f"Inbound Message ID: {evidence.get('inbound_message_id') or '-'}")
        lines.append(f"External Message ID: {evidence.get('external_message_id') or '-'}")
        lines.append(f"Sender: {evidence.get('sender_name') or evidence.get('sender') or '-'}")
        lines.append(f"Subject: {evidence.get('subject') or '-'}")
        lines.append(f"Attachments: {', '.join(str(name) for name in (evidence.get('attachment_filenames') or [])) or '-'}")
        lines.append(f"Matched PO Evidence: {self._json_inline_text(evidence.get('matched_po_evidence'))}")
        lines.append(f"Matched Snippets: {', '.join(str(item) for item in (evidence.get('matched_snippets') or [])) or '-'}")
        lines.append("Excerpt:")
        lines.append(str(evidence.get("source_excerpt") or "-"))
        return "\n".join(lines)

    def _staged_receipt_proposal_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"PO #: {proposal.target_id or proposed_change.get('target_id') or '-'}")
        lines.append(f"Packing Slip #: {proposed_change.get('packing_slip_number') or '-'}")
        lines.append(f"Delivery Date: {proposed_change.get('delivery_date') or '-'}")
        lines.append(f"Shipment Reference: {proposed_change.get('shipment_reference') or '-'}")
        lines.append(
            f"Quote Lineage Available: {self._bool_text(bool(evidence.get('quote_line_available')))}"
        )
        lines.append(
            f"Overall Confidence: {self._format_confidence(evidence.get('overall_confidence')) if evidence.get('overall_confidence') is not None else self._format_confidence(proposal.confidence)}"
        )
        line_candidates = proposed_change.get("line_candidates") if isinstance(proposed_change.get("line_candidates"), list) else []
        lines.append(f"Line Candidates: {len(line_candidates)}")
        for item in line_candidates:
            if not isinstance(item, dict):
                continue
            lines.append(
                "  - "
                f"POItemID {item.get('purchase_order_item_id') or '-'} | "
                f"Packing Slip Part {item.get('packing_slip_part_number') or '-'} | "
                f"{item.get('packing_slip_description') or '-'} | "
                f"PO Part {item.get('po_line_part_number') or '-'} | "
                f"{item.get('po_line_description') or '-'} | "
                f"Quote Part {item.get('vendor_quote_part_number') or '-'} | "
                f"{item.get('vendor_quote_description') or '-'} | "
                f"Qty {item.get('received_qty_candidate') or '-'} | "
                f"Quote Available {self._bool_text(bool(item.get('quote_line_available')))} | "
                f"Basis {item.get('matching_basis') or '-'} | "
                f"Outcome {item.get('match_outcome') or '-'} | "
                f"Operator Resolution {self._bool_text(bool(item.get('operator_resolution_required')))} | "
                f"Confidence {item.get('confidence') if item.get('confidence') is not None else '-'}"
            )
            if item.get("discrepancy_reason"):
                lines.append(f"    discrepancy: {item.get('discrepancy_reason')}")
            uncertainty_notes = item.get("uncertainty_notes") if isinstance(item.get("uncertainty_notes"), list) else []
            if uncertainty_notes:
                lines.append(f"    uncertainty: {'; '.join(str(note) for note in uncertainty_notes)}")
        lines.append("")
        lines.append("Apply Path:")
        lines.append("- create or reuse one PurchaseOrderReceipt only")
        lines.append("- update received quantities only through the Receive Goods wrapper")
        lines.append("- block ambiguity, mismatch, and overage")
        lines.append("- no invoice")
        lines.append("- no outbound message")
        lines.append("")
        lines.append(f"Inbound Message ID: {evidence.get('inbound_message_id') or '-'}")
        lines.append(f"External Message ID: {evidence.get('external_message_id') or '-'}")
        lines.append(f"Sender: {evidence.get('sender_name') or evidence.get('sender') or '-'}")
        lines.append(f"Attachments: {', '.join(str(name) for name in (evidence.get('attachment_filenames') or [])) or '-'}")
        lines.append(f"Matched PO Item IDs: {', '.join(str(item) for item in (evidence.get('matched_po_item_ids') or [])) or '-'}")
        uncertainty_notes = evidence.get("uncertainty_notes") if isinstance(evidence.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Overall Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        lines.append("Excerpt:")
        lines.append(str(evidence.get("source_excerpt") or "-"))
        return "\n".join(lines)

    def _customer_scheduling_proposal_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Intent: {proposed_change.get('normalized_intent') or '-'}")
        lines.append(f"Customer: {proposed_change.get('customer_name') or '-'}")
        lines.append(f"Sender Email: {proposed_change.get('sender_email') or '-'}")
        lines.append(f"Phone: {proposed_change.get('phone_number') or '-'}")
        lines.append(f"Target Site/Address: {proposed_change.get('site_address_text') or '-'}")
        lines.append(f"Requested Date: {proposed_change.get('requested_date_text') or '-'}")
        lines.append(f"Requested Time: {proposed_change.get('requested_time_text') or '-'}")
        lines.append(f"Match Outcome: {proposed_change.get('match_outcome') or '-'}")
        lines.append(f"Access Instructions: {proposed_change.get('access_instructions') or '-'}")
        lines.append("Service Description:")
        lines.append(str(proposed_change.get("service_description") or "-"))
        urgency_hints = proposed_change.get("urgency_hints") if isinstance(proposed_change.get("urgency_hints"), list) else []
        if urgency_hints:
            lines.append(f"Urgency Hints: {', '.join(str(item) for item in urgency_hints)}")
        uncertainty_notes = proposed_change.get("uncertainty_notes") if isinstance(proposed_change.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        if proposal.status == "Applied":
            lines.append("Apply Result: Draft intake records were created or safely reused. Review Automation Events for exact record IDs.")
        lines.append("")
        lines.append(f"Inbound Message ID: {evidence.get('inbound_message_id') or '-'}")
        lines.append(f"External Message ID: {evidence.get('external_message_id') or '-'}")
        lines.append(f"Sender: {evidence.get('sender_name') or evidence.get('sender') or '-'}")
        lines.append(f"Subject: {evidence.get('subject') or '-'}")
        lines.append(f"Attachments: {', '.join(str(name) for name in (evidence.get('attachment_filenames') or [])) or '-'}")
        candidate_customers = evidence.get("candidate_customer_list") if isinstance(evidence.get("candidate_customer_list"), list) else []
        if candidate_customers:
            lines.append(
                "Candidate Customers: "
                + "; ".join(
                    f"{item.get('customer_name') or '-'} ({item.get('score') or '-'})"
                    for item in candidate_customers
                    if isinstance(item, dict)
                )
            )
        candidate_sites = evidence.get("candidate_site_list") if isinstance(evidence.get("candidate_site_list"), list) else []
        if candidate_sites:
            lines.append(
                "Candidate Sites: "
                + "; ".join(
                    f"{item.get('site_name') or '-'} ({item.get('score') or '-'})"
                    for item in candidate_sites
                    if isinstance(item, dict)
                )
            )
        candidate_work_orders = evidence.get("candidate_work_order_list") if isinstance(evidence.get("candidate_work_order_list"), list) else []
        if candidate_work_orders:
            lines.append(
                "Candidate Work Orders: "
                + "; ".join(
                    f"WO #{item.get('WorkOrderID') or item.get('work_order_id') or '-'} ({item.get('JobStatus') or item.get('job_status') or '-'})"
                    for item in candidate_work_orders
                    if isinstance(item, dict)
                )
            )
        lines.append("Excerpt:")
        lines.append(str(evidence.get("source_excerpt") or "-"))
        return "\n".join(lines)

    def _lead_intake_proposal_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Intent: {proposed_change.get('normalized_intent') or '-'}")
        lines.append(
            f"Contact / Company: {proposed_change.get('contact_name') or '-'} / "
            f"{proposed_change.get('company_name') or '-'}"
        )
        lines.append(f"Sender Email: {proposed_change.get('sender_email') or '-'}")
        lines.append(f"Phone: {proposed_change.get('phone_number') or '-'}")
        lines.append(f"Site/Address: {proposed_change.get('site_address_text') or '-'}")
        lines.append(f"Requested Timeline: {proposed_change.get('requested_timeline') or '-'}")
        lines.append(f"Trade / Work Type: {proposed_change.get('trade_work_type') or '-'}")
        lines.append(f"Source Hint: {proposed_change.get('source_hint') or '-'}")
        lines.append(f"Match Outcome: {proposed_change.get('match_outcome') or '-'}")
        lines.append("Project Description:")
        lines.append(str(proposed_change.get("project_description") or "-"))
        urgency_hints = proposed_change.get("urgency_hints") if isinstance(proposed_change.get("urgency_hints"), list) else []
        if urgency_hints:
            lines.append(f"Urgency Hints: {', '.join(str(item) for item in urgency_hints)}")
        uncertainty_notes = proposed_change.get("uncertainty_notes") if isinstance(proposed_change.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        lines.append("")
        lines.append(f"Inbound Message ID: {evidence.get('inbound_message_id') or '-'}")
        lines.append(f"External Message ID: {evidence.get('external_message_id') or '-'}")
        lines.append(f"Sender: {evidence.get('sender_name') or evidence.get('sender') or '-'}")
        lines.append(f"Subject: {evidence.get('subject') or '-'}")
        lines.append(f"Attachments: {', '.join(str(name) for name in (evidence.get('attachment_filenames') or [])) or '-'}")
        candidate_customers = evidence.get("candidate_customer_list") if isinstance(evidence.get("candidate_customer_list"), list) else []
        if candidate_customers:
            lines.append(
                "Candidate Customers: "
                + "; ".join(
                    f"{item.get('customer_name') or '-'} ({item.get('score') or '-'})"
                    for item in candidate_customers
                    if isinstance(item, dict)
                )
            )
        candidate_sites = evidence.get("candidate_site_list") if isinstance(evidence.get("candidate_site_list"), list) else []
        if candidate_sites:
            lines.append(
                "Candidate Sites: "
                + "; ".join(
                    f"{item.get('site_name') or '-'} ({item.get('score') or '-'})"
                    for item in candidate_sites
                    if isinstance(item, dict)
                )
            )
        candidate_estimates = evidence.get("candidate_estimate_list") if isinstance(evidence.get("candidate_estimate_list"), list) else []
        if candidate_estimates:
            lines.append(
                "Candidate Estimates: "
                + "; ".join(
                    f"Estimate #{item.get('EstimateID') or '-'} ({item.get('Status') or '-'})"
                    for item in candidate_estimates
                    if isinstance(item, dict)
                )
            )
        lines.append("Excerpt:")
        lines.append(str(evidence.get("source_excerpt") or "-"))
        return "\n".join(lines)

    def _estimate_followup_proposal_review_summary_text(
        self,
        proposal: AutomationProposalRecord,
        proposed_change: dict[str, object],
        evidence: dict[str, object],
    ) -> str:
        lines = []
        lines.append(f"Proposal ID: {proposal.automation_proposal_id}")
        lines.append(f"Automation Key: {proposal.automation_key}")
        lines.append(f"Workflow: {proposal.workflow}")
        lines.append(f"Action Type: {proposal.action_type}")
        lines.append(f"Target: {proposal.target_type or '-'} / {proposal.target_id or '-'}")
        lines.append(f"Status: {proposal.status}")
        lines.append(f"Risk Level: {proposal.risk_level}")
        lines.append(f"Confidence: {self._format_confidence(proposal.confidence)}")
        lines.append("")
        lines.append(f"Intent: {proposed_change.get('normalized_intent') or '-'}")
        lines.append(
            f"Customer / Contact / Company: "
            f"{proposed_change.get('customer_name') or '-'} / "
            f"{proposed_change.get('contact_name') or '-'} / "
            f"{proposed_change.get('company_name') or '-'}"
        )
        lines.append(f"Sender Email: {proposed_change.get('sender_email') or '-'}")
        lines.append(f"Phone: {proposed_change.get('phone_number') or '-'}")
        lines.append(f"Matched CustomerID: {proposed_change.get('matched_customer_id') or '-'}")
        lines.append(f"Matched SiteID: {proposed_change.get('matched_site_id') or '-'}")
        lines.append(f"Matched EstimateID: {proposed_change.get('matched_estimate_id') or '-'}")
        lines.append(f"Estimate Reference: {proposed_change.get('estimate_reference') or '-'}")
        lines.append(f"Site/Address: {proposed_change.get('site_address_text') or '-'}")
        lines.append(f"Match Outcome: {proposed_change.get('match_outcome') or '-'}")
        lines.append("Customer Reply Summary:")
        lines.append(str(proposed_change.get("customer_reply_summary") or "-"))
        lines.append("Requested Change / Question:")
        lines.append(str(proposed_change.get("requested_change_or_question") or "-"))
        if proposal.action_type == "estimate_acceptance_observation":
            lines.append("Apply Path:")
            lines.append(
                "Create or reuse one non-operational WorkOrder Draft only. "
                "No OPEN work order, no estimate conversion, no scheduling, no PO, no invoice, and no outbound message."
            )
        urgency_hints = proposed_change.get("urgency_hints") if isinstance(proposed_change.get("urgency_hints"), list) else []
        if urgency_hints:
            lines.append(f"Urgency Hints: {', '.join(str(item) for item in urgency_hints)}")
        uncertainty_notes = proposed_change.get("uncertainty_notes") if isinstance(proposed_change.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        lines.append("")
        lines.append(f"Inbound Message ID: {evidence.get('inbound_message_id') or '-'}")
        lines.append(f"External Message ID: {evidence.get('external_message_id') or '-'}")
        lines.append(f"Sender: {evidence.get('sender_name') or evidence.get('sender') or '-'}")
        lines.append(f"Subject: {evidence.get('subject') or '-'}")
        lines.append(f"Attachments: {', '.join(str(name) for name in (evidence.get('attachment_filenames') or [])) or '-'}")
        candidate_customers = evidence.get("candidate_customer_list") if isinstance(evidence.get("candidate_customer_list"), list) else []
        if candidate_customers:
            lines.append(
                "Candidate Customers: "
                + "; ".join(
                    f"{item.get('customer_name') or '-'} ({item.get('score') or '-'})"
                    for item in candidate_customers
                    if isinstance(item, dict)
                )
            )
        candidate_sites = evidence.get("candidate_site_list") if isinstance(evidence.get("candidate_site_list"), list) else []
        if candidate_sites:
            lines.append(
                "Candidate Sites: "
                + "; ".join(
                    f"{item.get('site_name') or '-'} ({item.get('score') or '-'})"
                    for item in candidate_sites
                    if isinstance(item, dict)
                )
            )
        candidate_estimates = evidence.get("candidate_estimate_list") if isinstance(evidence.get("candidate_estimate_list"), list) else []
        if candidate_estimates:
            lines.append(
                "Candidate Estimates: "
                + "; ".join(
                    f"Estimate #{item.get('EstimateID') or '-'} ({item.get('Status') or '-'})"
                    for item in candidate_estimates
                    if isinstance(item, dict)
                )
            )
        lines.append("Excerpt:")
        lines.append(str(evidence.get("source_excerpt") or "-"))
        return "\n".join(lines)

    def _render_question_detail(self, question: AutomationQuestionRecord | None) -> None:
        if question is None:
            self.question_detail_meta.setText("-")
            self.question_detail_requirements.setText("-")
            self.question_detail_answer.setText("-")
            self.question_answer_json.setPlainText("Select a question to inspect or submit structured answer JSON.")
            self.question_choices_json.setPlainText("Select a question to inspect its structured choices payload.")
            self._populate_question_answer_form(None)
            self._update_question_action_buttons(None)
            return

        self.question_detail_meta.setText(
            f"#{question.automation_question_id} | {question.automation_key} | "
            f"{question.workflow} | {question.question_type} | "
            f"Target: {question.target_type or '-'} / {question.target_id or '-'}"
        )
        self.question_detail_requirements.setText(
            f"RequiredBeforeAction: {self._bool_text(question.required_before_action)} | "
            f"Urgency: {question.urgency or '-'} | "
            f"Status: {question.status} | "
            f"RelatedProposalID: {self._nullable_text(question.related_proposal_id)} | "
            f"AnsweredAt: {self._format_timestamp(question.answered_at)}"
        )
        answer_summary = self._question_answer_summary(question.answer)
        self.question_detail_answer.setText(answer_summary)
        metadata = build_question_queue_metadata(question)
        queue_lines = [
            "Queue Metadata:",
            f"Age: {metadata['age_text']}",
            f"Cash-Flow Critical: {self._bool_text(bool(metadata['cash_flow_critical']))}",
            f"Operator Action Needed: {self._bool_text(bool(metadata['operator_action_needed']))}",
            f"Suggested Next Step: {metadata['suggested_next_step']}",
            "",
        ]
        self.question_answer_json.setPlainText(self._question_answer_detail_text(question.answer))
        self._populate_question_answer_form(question)
        self._update_question_action_buttons(question)
        if question.question_type == "inbound_routing_review" and isinstance(question.choices_json, dict):
            self.question_choices_json.setPlainText(
                "\n".join(queue_lines) + self._inbound_routing_review_question_text(question)
            )
            return
        if question.question_type in {
            "attachment_review_required",
            "attachment_text_extraction_required",
            "attachment_needs_ocr",
            "unsupported_attachment_review",
            "missing_attachment_file_review",
        } and isinstance(question.choices_json, dict):
            self.question_choices_json.setPlainText(
                "\n".join(queue_lines) + self._attachment_review_question_text(question)
            )
            return
        if question.question_type in {
            "vendor_invoice_review_required",
            "vendor_disambiguation",
            "po_disambiguation",
            "duplicate_invoice_review",
            "non_po_expense_review",
            "non_po_vendor_invoice_exception_review",
            "invoice_missing_required_fields",
            "customer_billing_status_review",
            "cash_flow_relationship_review",
            "vendor_invoice_customer_billing_disambiguation",
            "customer_invoice_disambiguation",
            "no_customer_billing_found_review",
        } and isinstance(question.choices_json, dict):
            self.question_choices_json.setPlainText(
                "\n".join(queue_lines) + self._vendor_invoice_question_text(question)
            )
            return
        if question.automation_key == "lead_intake_watcher" and isinstance(question.choices_json, dict):
            self.question_choices_json.setPlainText(
                "\n".join(queue_lines) + self._lead_intake_question_text(question)
            )
            return
        if question.automation_key == "estimate_followup_reply_watcher" and isinstance(question.choices_json, dict):
            self.question_choices_json.setPlainText(
                "\n".join(queue_lines) + self._estimate_followup_question_text(question)
            )
            return
        if question.question_type in {
            "customer_scheduling_review_required",
            "customer_disambiguation",
            "site_disambiguation",
            "work_order_disambiguation",
            "new_service_inquiry_review",
            "urgent_service_review",
            "reply_required_review",
        } and isinstance(question.choices_json, dict):
            self.question_choices_json.setPlainText(
                "\n".join(queue_lines) + self._customer_scheduling_question_text(question)
            )
            return
        self.question_choices_json.setPlainText("\n".join(queue_lines) + self._json_detail_text(question.choices_json))

    def _populate_question_answer_form(self, question: AutomationQuestionRecord | None) -> None:
        controls = (
            self.question_selected_option_combo,
            self.question_selected_workflow_combo,
            self.question_selected_intent_input,
            self.question_selected_target_type_input,
            self.question_selected_target_id_input,
            self.question_save_routing_memory_checkbox,
            self.question_followup_required_checkbox,
            self.question_request_more_info_checkbox,
            self.question_operator_note_text,
            self.question_receipt_resolution_json,
            self.question_po_eta_resolution_json,
            self.question_vendor_invoice_resolution_json,
        )
        for control in controls:
            control.blockSignals(True)
        self.question_selected_option_combo.clear()
        self.question_selected_option_combo.addItem("")
        self.question_selected_workflow_combo.clear()
        self.question_selected_workflow_combo.addItem("")
        for workflow in QUEUE_WORKFLOW_OPTIONS:
            self.question_selected_workflow_combo.addItem(workflow)
        current_workflow_options = [self.question_selected_workflow_combo.itemText(i) for i in range(self.question_selected_workflow_combo.count())]
        if question is not None and question.question_type == "inbound_routing_review":
            choices = question.choices_json if isinstance(question.choices_json, dict) else {}
            for choice in self._question_choice_options(choices):
                if isinstance(choice, str):
                    if choice not in current_workflow_options:
                        self.question_selected_workflow_combo.addItem(choice)
                        current_workflow_options.append(choice)
                elif isinstance(choice, dict):
                    label = str(choice.get("label") or choice.get("value") or "").strip()
                    if label and label not in current_workflow_options:
                        self.question_selected_workflow_combo.addItem(label)
                        current_workflow_options.append(label)
        self.question_selected_workflow_combo.setCurrentText("")
        self.question_selected_intent_input.clear()
        self.question_selected_target_type_input.clear()
        self.question_selected_target_id_input.clear()
        self.question_save_routing_memory_checkbox.setChecked(False)
        self.question_followup_required_checkbox.setChecked(False)
        self.question_request_more_info_checkbox.setChecked(False)
        self.question_operator_note_text.clear()
        self.question_receipt_resolution_json.clear()
        self.question_po_eta_resolution_json.clear()
        self.question_vendor_invoice_resolution_json.clear()
        if question is None:
            for control in controls:
                control.blockSignals(False)
            return

        choices = question.choices_json if isinstance(question.choices_json, dict) else {}
        for choice in self._question_choice_options(choices):
            if isinstance(choice, dict):
                label = str(choice.get("label") or choice.get("value") or "-")
                self.question_selected_option_combo.addItem(label, choice.get("value") or label)
            else:
                text = str(choice)
                self.question_selected_option_combo.addItem(text, text)
        answer_payload = self._parse_question_answer_json(question.answer)
        if answer_payload:
            selected_option = answer_payload.get("selected_option")
            for index in range(self.question_selected_option_combo.count()):
                if self.question_selected_option_combo.itemData(index, Qt.ItemDataRole.UserRole) == selected_option:
                    self.question_selected_option_combo.setCurrentIndex(index)
                    break
            self.question_selected_workflow_combo.setCurrentText(str(answer_payload.get("selected_workflow") or ""))
            self.question_selected_intent_input.setText(str(answer_payload.get("selected_intent") or ""))
            self.question_selected_target_type_input.setText(str(answer_payload.get("selected_target_type") or ""))
            self.question_selected_target_id_input.setText(str(answer_payload.get("selected_target_id") or ""))
            self.question_save_routing_memory_checkbox.setChecked(bool(answer_payload.get("save_as_memory")))
            self.question_followup_required_checkbox.setChecked(bool(answer_payload.get("followup_required")))
            self.question_request_more_info_checkbox.setChecked(bool(answer_payload.get("request_more_info_needed")))
            self.question_operator_note_text.setPlainText(
                str(answer_payload.get("operator_note") or answer_payload.get("how_i_know") or "")
            )
        elif question.question_type in QUESTION_DISAMBIGUATION_TYPES:
            self.question_selected_target_type_input.setText(question.target_type or "")
            self.question_selected_target_id_input.setText(question.target_id or "")
        if self._supports_receipt_resolution_bridge(question):
            answer_payload = self._parse_question_answer_json(question.answer) or {}
            resolution_payload = self._default_receipt_resolution_payload(question)
            if isinstance(answer_payload, dict) and answer_payload.get("answer_type") == "packing_slip_receipt_resolution":
                resolution_payload = dict(answer_payload)
            self.question_receipt_resolution_json.setPlainText(
                json.dumps(resolution_payload, indent=2, sort_keys=True)
            )
        if self._supports_po_eta_resolution_bridge(question):
            answer_payload = self._parse_question_answer_json(question.answer) or {}
            resolution_payload = self._default_po_eta_resolution_payload(question)
            if isinstance(answer_payload, dict) and answer_payload.get("answer_type") == "po_eta_status_resolution":
                resolution_payload = dict(answer_payload)
            self.question_po_eta_resolution_json.setPlainText(
                json.dumps(resolution_payload, indent=2, sort_keys=True)
            )
        if self._supports_vendor_invoice_resolution_bridge(question):
            answer_payload = self._parse_question_answer_json(question.answer) or {}
            resolution_payload = self._default_vendor_invoice_resolution_payload(question)
            if isinstance(answer_payload, dict) and answer_payload.get("answer_type") == "vendor_invoice_reconciliation_resolution":
                resolution_payload = dict(answer_payload)
            self.question_vendor_invoice_resolution_json.setPlainText(
                json.dumps(resolution_payload, indent=2, sort_keys=True)
            )
        for control in controls:
            control.blockSignals(False)

    def _update_question_action_buttons(self, question: AutomationQuestionRecord | None) -> None:
        can_answer = question is not None and question.status == "Open"
        self.question_answer_button.setEnabled(can_answer)
        self.question_dismiss_button.setEnabled(can_answer)
        self.question_selected_option_combo.setEnabled(can_answer)
        self.question_selected_workflow_combo.setEnabled(can_answer)
        self.question_selected_intent_input.setEnabled(can_answer)
        self.question_selected_target_type_input.setEnabled(can_answer)
        self.question_selected_target_id_input.setEnabled(can_answer)
        self.question_followup_required_checkbox.setEnabled(can_answer)
        self.question_request_more_info_checkbox.setEnabled(can_answer)
        self.question_operator_note_text.setEnabled(can_answer)
        self.question_save_routing_memory_checkbox.setEnabled(
            can_answer and question is not None and question.question_type == "inbound_routing_review"
        )
        can_bridge = (
            question is not None
            and self._supports_receipt_resolution_bridge(question)
            and question.status in {"Open", "Answered"}
        )
        self.question_receipt_resolution_json.setEnabled(can_bridge)
        self.question_create_revised_receipt_button.setEnabled(can_bridge)
        can_po_eta_bridge = (
            question is not None
            and self._supports_po_eta_resolution_bridge(question)
            and question.status in {"Open", "Answered"}
        )
        self.question_po_eta_resolution_json.setEnabled(can_po_eta_bridge)
        self.question_create_revised_po_eta_button.setEnabled(can_po_eta_bridge)
        can_vendor_invoice_bridge = (
            question is not None
            and self._supports_vendor_invoice_resolution_bridge(question)
            and question.status in {"Open", "Answered"}
        )
        self.question_vendor_invoice_resolution_json.setEnabled(can_vendor_invoice_bridge)
        self.question_create_revised_vendor_invoice_button.setEnabled(can_vendor_invoice_bridge)

    def _question_choice_options(self, choices_json: dict[str, Any]) -> list[Any]:
        choices = choices_json.get("choices")
        if isinstance(choices, list):
            return choices
        return []

    def _supports_receipt_resolution_bridge(self, question: AutomationQuestionRecord | None) -> bool:
        if question is None:
            return False
        return question.workflow == "receive_goods" and question.question_type in {
            "receiving_line_match_review",
            "packing_slip_review_required",
            "po_disambiguation",
            "attachment_text_extraction_required",
            "staged_receipt_review_required",
            "ambiguous_line_match",
            "packing_slip_discrepancy_review",
        }

    def _supports_po_eta_resolution_bridge(self, question: AutomationQuestionRecord | None) -> bool:
        if question is None:
            return False
        return question.workflow in {"po_receiving", "po_eta_parts_ready"} and question.question_type in {
            "po_disambiguation",
            "po_eta_review_required",
            "po_eta_status_review_required",
            "po_eta_intent_review",
            "po_eta_missing_po_review",
            "po_eta_ambiguous_match",
            "po_eta_parts_ready_review",
            "po_eta_parts_ready_review_required",
        }

    def _supports_vendor_invoice_resolution_bridge(self, question: AutomationQuestionRecord | None) -> bool:
        if question is None:
            return False
        return question.workflow in {"vendor_invoice_payables", "vendor_invoice_intake"} and question.question_type in {
            "vendor_invoice_review_required",
            "vendor_disambiguation",
            "po_disambiguation",
            "duplicate_invoice_review",
            "non_po_expense_review",
            "non_po_vendor_invoice_exception_review",
            "invoice_missing_required_fields",
            "vendor_invoice_reconciliation_review",
            "vendor_invoice_reconciliation_due_review",
            "vendor_invoice_reconciliation_ambiguity",
            "vendor_invoice_duplicate_review",
            "customer_billing_status_review",
        }

    def _default_receipt_resolution_payload(self, question: AutomationQuestionRecord) -> dict[str, Any]:
        choices = question.choices_json if isinstance(question.choices_json, dict) else {}
        candidate_lines = choices.get("candidate_lines") if isinstance(choices.get("candidate_lines"), list) else []
        line_resolutions: list[dict[str, Any]] = []
        for index, item in enumerate(candidate_lines):
            if not isinstance(item, dict):
                continue
            line_resolutions.append(
                {
                    "packing_slip_line_ref": f"line-{index + 1}",
                    "selected_purchase_order_item_id": None,
                    "received_qty": item.get("received_qty_candidate") or 0.0,
                    "resolution_basis": "operator_confirmed_match",
                    "operator_note": "",
                    "allow_overage": False,
                }
            )
        selected_po_id = None
        if question.target_type == "PurchaseOrder":
            selected_po_id = question.target_id
        elif isinstance(choices.get("selected_purchase_order_id"), (int, str)):
            selected_po_id = choices.get("selected_purchase_order_id")
        payload = {
            "answer_type": "packing_slip_receipt_resolution",
            "selected_purchase_order_id": _coerce_int(selected_po_id) or 0,
            "line_resolutions": line_resolutions or [
                {
                    "packing_slip_line_ref": "line-1",
                    "selected_purchase_order_item_id": 0,
                    "received_qty": 0.0,
                    "resolution_basis": "operator_confirmed_match",
                    "operator_note": "",
                    "allow_overage": False,
                }
            ],
            "packing_slip_number": choices.get("packing_slip_number") or "",
            "received_date": choices.get("received_date") or "",
            "operator_note": self.question_operator_note_text.toPlainText().strip() or "",
            "answered_by": "Automation Center",
        }
        return payload

    def _default_po_eta_resolution_payload(self, question: AutomationQuestionRecord) -> dict[str, Any]:
        choices = question.choices_json if isinstance(question.choices_json, dict) else {}
        extracted_payload = choices.get("extracted_payload") if isinstance(choices.get("extracted_payload"), dict) else {}
        selected_po_id = None
        if question.target_type == "PurchaseOrder":
            selected_po_id = question.target_id
        elif isinstance(choices.get("selected_purchase_order_id"), (int, str)):
            selected_po_id = choices.get("selected_purchase_order_id")
        payload = {
            "answer_type": "po_eta_status_resolution",
            "selected_purchase_order_id": _coerce_int(selected_po_id) or 0,
            "normalized_intent": str(
                choices.get("normalized_intent")
                or extracted_payload.get("normalized_intent")
                or "ETA_UPDATE"
            ).strip()
            or "ETA_UPDATE",
            "eta_date": choices.get("eta_date") or extracted_payload.get("eta_date") or "",
            "parts_ready": bool(
                choices.get("parts_ready")
                if choices.get("parts_ready") is not None
                else extracted_payload.get("parts_ready")
            ),
            "backorder_hint": bool(extracted_payload.get("backorder_hint")),
            "pickup_note": choices.get("pickup_note") or extracted_payload.get("pickup_note") or "",
            "vendor_message_summary": (
                choices.get("vendor_message_summary")
                or extracted_payload.get("vendor_message_summary")
                or choices.get("source_excerpt")
                or ""
            ),
            "operator_note": self.question_operator_note_text.toPlainText().strip() or "",
            "how_i_know": self.question_operator_note_text.toPlainText().strip() or "",
            "answered_by": "Automation Center",
        }
        return payload

    def _default_vendor_invoice_resolution_payload(self, question: AutomationQuestionRecord) -> dict[str, Any]:
        choices = question.choices_json if isinstance(question.choices_json, dict) else {}
        extracted_fields = choices.get("extracted_fields") if isinstance(choices.get("extracted_fields"), dict) else {}
        duplicate_result = choices.get("duplicate_result") if isinstance(choices.get("duplicate_result"), dict) else {}
        receipt_choices = choices.get("receipt_choices") if isinstance(choices.get("receipt_choices"), list) else []

        selected_po_id = None
        if question.target_type == "PurchaseOrder":
            selected_po_id = question.target_id
        elif isinstance(choices.get("selected_purchase_order_id"), (int, str)):
            selected_po_id = choices.get("selected_purchase_order_id")
        vendor_candidates = choices.get("candidate_vendors") if isinstance(choices.get("candidate_vendors"), list) else []
        selected_vendor_id = None
        if vendor_candidates and isinstance(vendor_candidates[0], dict):
            selected_vendor_id = _coerce_int(vendor_candidates[0].get("vendor_id"))

        resolution_mode = "po_backed"
        if question.question_type in {"duplicate_invoice_review", "vendor_invoice_duplicate_review"}:
            resolution_mode = "duplicate"
        elif question.question_type in {"non_po_expense_review", "non_po_vendor_invoice_exception_review"}:
            resolution_mode = "non_po_expense"
        elif question.question_type == "invoice_missing_required_fields":
            resolution_mode = "field_correction"

        payload = {
            "answer_type": "vendor_invoice_reconciliation_resolution",
            "resolution_mode": resolution_mode,
            "selected_vendor_id": selected_vendor_id or 0,
            "selected_purchase_order_id": _coerce_int(selected_po_id) or 0,
            "selected_receipt_ids": [
                _coerce_int(item.get("ReceiptID"))
                for item in receipt_choices
                if isinstance(item, dict) and _coerce_int(item.get("ReceiptID")) is not None
            ][:1],
            "invoice_number": extracted_fields.get("invoice_number") or "",
            "invoice_date": extracted_fields.get("invoice_date") or "",
            "due_date": extracted_fields.get("due_date") or "",
            "invoice_total": extracted_fields.get("total_amount") or 0.0,
            "tax_amount": extracted_fields.get("tax_amount") or 0.0,
            "packing_slip_number": extracted_fields.get("packing_slip_number") or "",
            "reconciliation_outcome": "MATCHED_PO_ONLY",
            "customer_billing_status": "unknown",
            "duplicate_of_vendor_invoice_id": _coerce_int(duplicate_result.get("matched_vendor_invoice_id")) or 0,
            "expense_category_hint": "",
            "vendor_message_summary": (
                choices.get("source_excerpt")
                or extracted_fields.get("invoice_number")
                or question.question_text
                or ""
            ),
            "operator_note": self.question_operator_note_text.toPlainText().strip() or "",
            "how_i_know": self.question_operator_note_text.toPlainText().strip() or "",
            "answered_by": "Automation Center",
        }
        return payload

    def _parse_question_answer_json(self, answer: str | None) -> dict[str, Any] | None:
        if not answer:
            return None
        try:
            parsed = json.loads(answer)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _question_answer_summary(self, answer: str | None) -> str:
        parsed = self._parse_question_answer_json(answer)
        if not parsed:
            return answer or "-"
        parts = [
            str(parsed.get("answer_type") or "answered"),
            str(parsed.get("selected_workflow") or parsed.get("selected_option") or parsed.get("selected_target_id") or "-"),
            str(parsed.get("operator_note") or parsed.get("dismiss_reason") or "-"),
        ]
        return " | ".join(parts)

    def _question_answer_detail_text(self, answer: str | None) -> str:
        parsed = self._parse_question_answer_json(answer)
        if parsed is None:
            return answer or "-"
        return json.dumps(parsed, indent=2, sort_keys=True)

    def _inbound_routing_review_question_text(self, question: AutomationQuestionRecord) -> str:
        payload = question.choices_json if isinstance(question.choices_json, dict) else {}
        lines = []
        lines.append(f"Question ID: {question.automation_question_id}")
        lines.append(f"Automation Key: {question.automation_key}")
        lines.append(f"Workflow: {question.workflow}")
        lines.append(f"Question Type: {question.question_type}")
        lines.append(f"Target: {question.target_type or '-'} / {question.target_id or '-'}")
        lines.append("")
        lines.append(f"Sender: {payload.get('sender_name') or payload.get('sender') or '-'}")
        lines.append(f"Subject: {payload.get('subject') or '-'}")
        lines.append(f"Uncertainty Reason: {payload.get('uncertainty_reason') or '-'}")
        lines.append("")
        lines.append("Body Excerpt:")
        lines.append(str(payload.get("body_excerpt") or "-"))
        lines.append("")
        lines.append("Attachments:")
        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        if not attachments:
            lines.append("  - none")
        else:
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                lines.append(
                    "  - "
                    f"{attachment.get('filename') or '-'} | "
                    f"{attachment.get('mime_type') or '-'} | "
                    f"Status {attachment.get('status') or '-'}"
                )
        lines.append("")
        lines.append("Candidate Workflows:")
        candidates = payload.get("candidate_workflows") if isinstance(payload.get("candidate_workflows"), list) else []
        if not candidates:
            lines.append("  - none")
        else:
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                lines.append(
                    "  - "
                    f"{candidate.get('workflow') or candidate.get('intent') or '-'} | "
                    f"Confidence {candidate.get('confidence') if candidate.get('confidence') is not None else '-'} | "
                    f"Matched {', '.join(str(item) for item in (candidate.get('matched_phrases') or [])) or '-'}"
                )
        lines.append("")
        lines.append("Likely Workflow Choices:")
        choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
        if not choices:
            lines.append("  - none")
        else:
            for choice in choices:
                if isinstance(choice, dict):
                    lines.append(f"  - {choice.get('label') or choice.get('value') or '-'}")
                else:
                    lines.append(f"  - {choice}")
        lines.append("")
        lines.append("Matched Snippets:")
        matched = payload.get("matched_phrases") if isinstance(payload.get("matched_phrases"), list) else []
        if not matched:
            lines.append("  - none")
        else:
            for phrase in matched:
                lines.append(f"  - {phrase}")
        lines.append("")
        lines.append("Required Operator Fields:")
        required_fields = payload.get("required_operator_fields") if isinstance(payload.get("required_operator_fields"), list) else []
        if not required_fields:
            lines.append("  - none")
        else:
            for field_name in required_fields:
                lines.append(f"  - {field_name}")
        lines.append("")
        lines.append("Structured Payload:")
        lines.append(self._json_detail_text(payload))
        return "\n".join(lines)

    def _attachment_review_question_text(self, question: AutomationQuestionRecord) -> str:
        payload = question.choices_json if isinstance(question.choices_json, dict) else {}
        actions = payload.get("suggested_operator_actions") if isinstance(payload.get("suggested_operator_actions"), list) else []
        lines = []
        lines.append(f"Question ID: {question.automation_question_id}")
        lines.append(f"Automation Key: {question.automation_key}")
        lines.append(f"Workflow: {question.workflow}")
        lines.append(f"Question Type: {question.question_type}")
        lines.append(f"Target: {question.target_type or '-'} / {question.target_id or '-'}")
        lines.append("")
        lines.append(f"Attachment ID: {payload.get('attachment_id') or '-'}")
        lines.append(f"Message ID: {payload.get('inbound_message_id') or '-'}")
        lines.append(f"Filename: {payload.get('filename') or '-'}")
        lines.append(f"Mime Type: {payload.get('mime_type') or '-'}")
        lines.append(f"File Path: {payload.get('file_path') or '-'}")
        lines.append(f"Extraction Status: {payload.get('extraction_status') or '-'}")
        lines.append(f"Requires OCR: {self._bool_text(bool(payload.get('requires_ocr')))}")
        lines.append(f"Text Length: {self._nullable_text(payload.get('text_length'))}")
        lines.append(f"Parent Sender: {payload.get('parent_message_sender') or '-'}")
        lines.append(f"Parent Subject: {payload.get('parent_message_subject') or '-'}")
        lines.append(f"Parent Workflow Guess: {payload.get('parent_workflow_guess') or '-'}")
        lines.append(f"Cash-Flow Critical: {self._bool_text(bool(payload.get('cash_flow_critical')))}")
        lines.append(f"Warnings: {', '.join(str(item) for item in (payload.get('warnings') or [])) or '-'}")
        lines.append(f"Errors: {', '.join(str(item) for item in (payload.get('errors') or [])) or '-'}")
        lines.append(f"Suggested Operator Actions: {', '.join(str(item) for item in actions) or '-'}")
        lines.append("")
        lines.append("Structured Payload:")
        lines.append(self._json_detail_text(payload))
        return "\n".join(lines)

    def _vendor_invoice_question_text(self, question: AutomationQuestionRecord) -> str:
        payload = question.choices_json if isinstance(question.choices_json, dict) else {}
        lines = []
        lines.append(f"Question ID: {question.automation_question_id}")
        lines.append(f"Automation Key: {question.automation_key}")
        lines.append(f"Workflow: {question.workflow}")
        lines.append(f"Question Type: {question.question_type}")
        lines.append(f"Target: {question.target_type or '-'} / {question.target_id or '-'}")
        lines.append("")
        extracted_fields = payload.get("extracted_fields") if isinstance(payload.get("extracted_fields"), dict) else {}
        lines.append(f"Vendor: {extracted_fields.get('vendor_name') or '-'}")
        lines.append(f"Invoice #: {extracted_fields.get('invoice_number') or '-'}")
        lines.append(f"Invoice Date: {extracted_fields.get('invoice_date') or '-'}")
        lines.append(f"Due Date: {extracted_fields.get('due_date') or '-'}")
        lines.append(f"Total: {self._format_currency(extracted_fields.get('total_amount'))}")
        lines.append(f"PO #: {extracted_fields.get('po_number') or '-'}")
        lines.append(f"Packing Slip #: {extracted_fields.get('packing_slip_number') or '-'}")
        lines.append(f"Work Order Ref: {extracted_fields.get('work_order_reference') or '-'}")
        lines.append(f"Match Outcome: {payload.get('match_outcome') or '-'}")
        lines.append(f"Customer Billing Status: {payload.get('customer_billing_status') or '-'}")
        cash_flow_flags = payload.get("cash_flow_flags") if isinstance(payload.get("cash_flow_flags"), list) else []
        if cash_flow_flags:
            lines.append(f"Cash-Flow Flags: {', '.join(str(item) for item in cash_flow_flags)}")
        missing_fields = payload.get("missing_fields") if isinstance(payload.get("missing_fields"), list) else []
        if missing_fields:
            lines.append(f"Missing Fields: {', '.join(str(item) for item in missing_fields)}")
        uncertainty_notes = payload.get("uncertainty_notes") if isinstance(payload.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        candidate_vendors = payload.get("candidate_vendors") if isinstance(payload.get("candidate_vendors"), list) else []
        if candidate_vendors:
            lines.append("Candidate Vendors:")
            for item in candidate_vendors:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"{item.get('vendor_name') or '-'} | "
                    f"VendorID {item.get('vendor_id') or '-'} | "
                    f"Score {item.get('score') or '-'} | "
                    f"{item.get('contact_email') or '-'}"
                )
        candidate_pos = payload.get("candidate_purchase_orders") if isinstance(payload.get("candidate_purchase_orders"), list) else []
        if candidate_pos:
            lines.append("Candidate POs:")
            for item in candidate_pos:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"PO #{item.get('PurchaseOrderID') or '-'} | "
                    f"{item.get('SiteName') or '-'} | "
                    f"{item.get('Status') or '-'}"
                )
        body_snippets = payload.get("body_snippets") if isinstance(payload.get("body_snippets"), list) else []
        if body_snippets:
            lines.append("Evidence Snippets:")
            for snippet in body_snippets:
                lines.append(f"  - {snippet}")
        suggestions = payload.get("suggested_operator_actions") if isinstance(payload.get("suggested_operator_actions"), list) else []
        if suggestions:
            lines.append("Suggested Operator Actions:")
            for suggestion in suggestions:
                lines.append(f"  - {suggestion}")
        candidate_customer_invoices = payload.get("candidate_customer_invoices") if isinstance(payload.get("candidate_customer_invoices"), list) else []
        if candidate_customer_invoices:
            lines.append("Candidate Customer Invoices:")
            for item in candidate_customer_invoices:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"Invoice #{item.get('customer_invoice_id') or '-'} | "
                    f"{item.get('invoice_status') or '-'} | "
                    f"{self._format_currency(item.get('amount'))} | "
                    f"{item.get('customer_invoice_date') or '-'}"
                )
        return "\n".join(lines)

    def _customer_scheduling_question_text(self, question: AutomationQuestionRecord) -> str:
        payload = question.choices_json if isinstance(question.choices_json, dict) else {}
        lines = []
        lines.append(f"Question ID: {question.automation_question_id}")
        lines.append(f"Automation Key: {question.automation_key}")
        lines.append(f"Workflow: {question.workflow}")
        lines.append(f"Question Type: {question.question_type}")
        lines.append(f"Target: {question.target_type or '-'} / {question.target_id or '-'}")
        lines.append("")
        extracted_fields = payload.get("extracted_fields") if isinstance(payload.get("extracted_fields"), dict) else {}
        lines.append(f"Intent: {extracted_fields.get('normalized_intent') or '-'}")
        lines.append(f"Customer: {extracted_fields.get('customer_name') or '-'}")
        lines.append(f"Sender Email: {extracted_fields.get('sender_email') or '-'}")
        lines.append(f"Phone: {extracted_fields.get('phone_number') or '-'}")
        lines.append(f"Site/Address: {extracted_fields.get('site_address_text') or '-'}")
        lines.append(f"Requested Date: {extracted_fields.get('requested_date_text') or '-'}")
        lines.append(f"Requested Time: {extracted_fields.get('requested_time_text') or '-'}")
        lines.append(f"Access Instructions: {extracted_fields.get('access_instructions') or '-'}")
        lines.append("Service Description:")
        lines.append(str(extracted_fields.get("service_description") or "-"))
        lines.append(f"Estimate Ref: {extracted_fields.get('estimate_reference') or '-'}")
        lines.append(f"Work Order Ref: {extracted_fields.get('work_order_reference') or '-'}")
        lines.append(f"Match Outcome: {payload.get('match_outcome') or '-'}")
        uncertainty_notes = payload.get("uncertainty_notes") if isinstance(payload.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        candidate_customers = payload.get("candidate_customers") if isinstance(payload.get("candidate_customers"), list) else []
        if candidate_customers:
            lines.append("Candidate Customers:")
            for item in candidate_customers:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"{item.get('customer_name') or '-'} | "
                    f"CustomerID {item.get('customer_id') or '-'} | "
                    f"Score {item.get('score') or '-'} | "
                    f"{item.get('email') or '-'}"
                )
        candidate_sites = payload.get("candidate_sites") if isinstance(payload.get("candidate_sites"), list) else []
        if candidate_sites:
            lines.append("Candidate Sites:")
            for item in candidate_sites:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"{item.get('site_name') or '-'} | "
                    f"SiteID {item.get('site_id') or '-'} | "
                    f"Score {item.get('score') or '-'} | "
                    f"{item.get('street_number') or ''} {item.get('street_name') or ''} {item.get('city') or ''}".strip()
                )
        candidate_work_orders = payload.get("candidate_work_orders") if isinstance(payload.get("candidate_work_orders"), list) else []
        if candidate_work_orders:
            lines.append("Candidate Work Orders:")
            for item in candidate_work_orders:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"WO #{item.get('WorkOrderID') or item.get('work_order_id') or '-'} | "
                    f"{item.get('JobStatus') or item.get('job_status') or '-'} | "
                    f"{item.get('Description') or item.get('description') or '-'}"
                )
        body_snippets = payload.get("body_snippets") if isinstance(payload.get("body_snippets"), list) else []
        if body_snippets:
            lines.append("Evidence Snippets:")
            for snippet in body_snippets:
                lines.append(f"  - {snippet}")
        suggestions = payload.get("suggested_operator_actions") if isinstance(payload.get("suggested_operator_actions"), list) else []
        if suggestions:
            lines.append("Suggested Operator Actions:")
            for suggestion in suggestions:
                lines.append(f"  - {suggestion}")
        return "\n".join(lines)

    def _lead_intake_question_text(self, question: AutomationQuestionRecord) -> str:
        payload = question.choices_json if isinstance(question.choices_json, dict) else {}
        lines = []
        lines.append(f"Question ID: {question.automation_question_id}")
        lines.append(f"Automation Key: {question.automation_key}")
        lines.append(f"Workflow: {question.workflow}")
        lines.append(f"Question Type: {question.question_type}")
        lines.append(f"Target: {question.target_type or '-'} / {question.target_id or '-'}")
        lines.append("")
        extracted_fields = payload.get("extracted_fields") if isinstance(payload.get("extracted_fields"), dict) else {}
        lines.append(f"Intent: {extracted_fields.get('normalized_intent') or '-'}")
        lines.append(f"Contact: {extracted_fields.get('contact_name') or '-'}")
        lines.append(f"Sender Email: {extracted_fields.get('sender_email') or '-'}")
        lines.append(f"Phone: {extracted_fields.get('phone_number') or '-'}")
        lines.append(f"Company: {extracted_fields.get('company_name') or '-'}")
        lines.append(f"Site/Address: {extracted_fields.get('site_address_text') or '-'}")
        lines.append(f"Requested Timeline: {extracted_fields.get('requested_timeline') or '-'}")
        lines.append(f"Trade / Work Type: {extracted_fields.get('trade_work_type') or '-'}")
        lines.append(f"Source Hint: {extracted_fields.get('source_hint') or '-'}")
        lines.append("Project Description:")
        lines.append(str(extracted_fields.get("project_description") or "-"))
        lines.append(f"Match Outcome: {payload.get('match_outcome') or '-'}")
        uncertainty_notes = payload.get("uncertainty_notes") if isinstance(payload.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        candidate_customers = payload.get("candidate_customers") if isinstance(payload.get("candidate_customers"), list) else []
        if candidate_customers:
            lines.append("Candidate Customers:")
            for item in candidate_customers:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"{item.get('customer_name') or '-'} | "
                    f"CustomerID {item.get('customer_id') or '-'} | "
                    f"Score {item.get('score') or '-'} | "
                    f"{item.get('email') or '-'}"
                )
        candidate_sites = payload.get("candidate_sites") if isinstance(payload.get("candidate_sites"), list) else []
        if candidate_sites:
            lines.append("Candidate Sites:")
            for item in candidate_sites:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"{item.get('site_name') or '-'} | "
                    f"SiteID {item.get('site_id') or '-'} | "
                    f"Score {item.get('score') or '-'} | "
                    f"{item.get('street_number') or ''} {item.get('street_name') or ''} {item.get('city') or ''}".strip()
                )
        candidate_estimates = payload.get("candidate_estimates") if isinstance(payload.get("candidate_estimates"), list) else []
        if candidate_estimates:
            lines.append("Candidate Estimates:")
            for item in candidate_estimates:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"Estimate #{item.get('EstimateID') or item.get('estimate_id') or '-'} | "
                    f"{item.get('Status') or item.get('status') or '-'} | "
                    f"{item.get('CustomerName') or item.get('customer_name') or '-'}"
                )
        body_snippets = payload.get("body_snippets") if isinstance(payload.get("body_snippets"), list) else []
        if body_snippets:
            lines.append("Evidence Snippets:")
            for snippet in body_snippets:
                lines.append(f"  - {snippet}")
        suggestions = payload.get("suggested_operator_actions") if isinstance(payload.get("suggested_operator_actions"), list) else []
        if suggestions:
            lines.append("Suggested Operator Actions:")
            for suggestion in suggestions:
                lines.append(f"  - {suggestion}")
        return "\n".join(lines)

    def _estimate_followup_question_text(self, question: AutomationQuestionRecord) -> str:
        payload = question.choices_json if isinstance(question.choices_json, dict) else {}
        lines = []
        lines.append(f"Question ID: {question.automation_question_id}")
        lines.append(f"Automation Key: {question.automation_key}")
        lines.append(f"Workflow: {question.workflow}")
        lines.append(f"Question Type: {question.question_type}")
        lines.append(f"Target: {question.target_type or '-'} / {question.target_id or '-'}")
        lines.append("")
        extracted_fields = payload.get("extracted_fields") if isinstance(payload.get("extracted_fields"), dict) else {}
        lines.append(f"Intent: {extracted_fields.get('normalized_intent') or '-'}")
        lines.append(f"Customer: {extracted_fields.get('customer_name') or '-'}")
        lines.append(f"Contact: {extracted_fields.get('contact_name') or '-'}")
        lines.append(f"Company: {extracted_fields.get('company_name') or '-'}")
        lines.append(f"Sender Email: {extracted_fields.get('sender_email') or '-'}")
        lines.append(f"Phone: {extracted_fields.get('phone_number') or '-'}")
        lines.append(f"Site/Address: {extracted_fields.get('site_address_text') or '-'}")
        lines.append(f"Estimate Ref: {extracted_fields.get('estimate_reference') or '-'}")
        lines.append("Reply Summary:")
        lines.append(str(extracted_fields.get("customer_reply_summary") or "-"))
        lines.append("Requested Change / Question:")
        lines.append(str(extracted_fields.get("requested_change_or_question") or "-"))
        lines.append(f"Match Outcome: {payload.get('match_outcome') or '-'}")
        uncertainty_notes = payload.get("uncertainty_notes") if isinstance(payload.get("uncertainty_notes"), list) else []
        if uncertainty_notes:
            lines.append(f"Uncertainty Notes: {'; '.join(str(note) for note in uncertainty_notes)}")
        candidate_customers = payload.get("candidate_customers") if isinstance(payload.get("candidate_customers"), list) else []
        if candidate_customers:
            lines.append("Candidate Customers:")
            for item in candidate_customers:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"{item.get('customer_name') or '-'} | "
                    f"CustomerID {item.get('customer_id') or '-'} | "
                    f"Score {item.get('score') or '-'} | "
                    f"{item.get('email') or '-'}"
                )
        candidate_sites = payload.get("candidate_sites") if isinstance(payload.get("candidate_sites"), list) else []
        if candidate_sites:
            lines.append("Candidate Sites:")
            for item in candidate_sites:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"{item.get('site_name') or '-'} | "
                    f"SiteID {item.get('site_id') or '-'} | "
                    f"Score {item.get('score') or '-'} | "
                    f"{item.get('street_number') or ''} {item.get('street_name') or ''} {item.get('city') or ''}".strip()
                )
        candidate_estimates = payload.get("candidate_estimates") if isinstance(payload.get("candidate_estimates"), list) else []
        if candidate_estimates:
            lines.append("Candidate Estimates:")
            for item in candidate_estimates:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    f"Estimate #{item.get('EstimateID') or item.get('estimate_id') or '-'} | "
                    f"{item.get('Status') or item.get('status') or '-'} | "
                    f"{item.get('CustomerName') or item.get('customer_name') or '-'}"
                )
        body_snippets = payload.get("body_snippets") if isinstance(payload.get("body_snippets"), list) else []
        if body_snippets:
            lines.append("Evidence Snippets:")
            for snippet in body_snippets:
                lines.append(f"  - {snippet}")
        suggestions = payload.get("suggested_operator_actions") if isinstance(payload.get("suggested_operator_actions"), list) else []
        if suggestions:
            lines.append("Suggested Operator Actions:")
            for suggestion in suggestions:
                lines.append(f"  - {suggestion}")
        return "\n".join(lines)

    def _render_inbound_message_detail(self, message: InboundMessageRecord | None) -> None:
        if message is None:
            self.inbound_message_detail_meta.setText("-")
            self.inbound_message_detail_timing.setText("-")
            self.inbound_message_detail_status.setText("-")
            self.inbound_body_excerpt.setPlainText("Select an inbound message to inspect its body excerpt.")
            self.inbound_classification_json.setPlainText("Select an inbound message to inspect its ClassificationJson.")
            return

        self.inbound_message_detail_meta.setText(
            f"#{message.inbound_message_id} | {message.source_system} | "
            f"Mailbox: {message.mailbox or '-'} | Sender: {message.sender_name or message.sender or '-'} | "
            f"Subject: {message.subject or '-'}"
        )
        self.inbound_message_detail_timing.setText(
            f"Received: {self._format_timestamp(message.received_at)} | "
            f"Imported: {self._format_timestamp(message.imported_at)} | "
            f"Processed: {self._format_timestamp(message.processed_at)}"
        )
        self.inbound_message_detail_status.setText(
            f"Status: {message.status} | WorkflowGuess: {message.workflow_guess or '-'} | "
            f"IntentGuess: {message.intent_guess or '-'} | Confidence: {self._format_confidence(message.confidence)} | "
            f"Attachments: {message.attachment_count} | Error: {message.error_message or '-'}"
        )
        self.inbound_body_excerpt.setPlainText(message.body_excerpt or message.body_text or "-")
        self.inbound_classification_json.setPlainText(self._json_detail_text(message.classification_json))

    def _render_inbound_attachment_detail(self, attachment: InboundAttachmentRecord | None) -> None:
        if attachment is None:
            self.inbound_create_attachment_review_button.setEnabled(False)
            self.inbound_extraction_json.setPlainText("Select an attachment to inspect its ExtractionJson.")
            return
        summary = get_attachment_text_summary(attachment)
        latest_question = None
        try:
            questions = list_questions_for_target(
                target_type="InboundAttachment",
                target_id=attachment.inbound_attachment_id,
                limit=1,
                automation_key="attachment_text_extraction",
            )
            latest_question = questions[0] if questions else None
        except Exception:
            latest_question = None
        active_question = get_active_attachment_review_question(int(attachment.inbound_attachment_id))
        self.inbound_create_attachment_review_button.setEnabled(
            bool(attachment_requires_review(summary) and active_question is None)
        )

        lines = [
            f"AttachmentID: {attachment.inbound_attachment_id}",
            f"MessageID: {attachment.inbound_message_id}",
            f"Filename: {attachment.filename or '-'}",
            f"MimeType: {attachment.mime_type or '-'}",
            f"FilePath: {attachment.file_path or '-'}",
            f"FileSize: {self._nullable_text(attachment.file_size)}",
            f"Status: {attachment.status}",
            f"ImportedAt: {self._format_timestamp(attachment.imported_at)}",
            f"Error: {attachment.error_message or '-'}",
            f"Method: {summary.get('method') or '-'}",
            f"Success: {self._bool_text(bool(summary.get('success')))}",
            f"Text Length: {self._nullable_text(summary.get('text_length'))}",
            f"Page Count: {self._nullable_text(summary.get('page_count'))}",
            f"Requires OCR: {self._bool_text(bool(summary.get('requires_ocr')))}",
            f"Active Review Question: #{active_question.automation_question_id}" if active_question else "Active Review Question: -",
            f"Active Review Status: {active_question.status}" if active_question else "Active Review Status: -",
            f"Latest Review Question: #{latest_question.automation_question_id}" if latest_question else "Latest Review Question: -",
            f"Latest Review Status: {latest_question.status}" if latest_question else "Latest Review Status: -",
            f"Warnings: {', '.join(str(item) for item in (summary.get('warnings') or [])) or '-'}",
            f"Errors: {', '.join(str(item) for item in (summary.get('errors') or [])) or '-'}",
            "",
            "ContentText:",
            attachment.content_text or "-",
            "",
            "ExtractionJson:",
            self._json_detail_text(attachment.extraction_json),
        ]
        self.inbound_extraction_json.setPlainText("\n".join(lines))

    def _bool_text(self, value: bool) -> str:
        return "Yes" if value else "No"

    def _nullable_text(self, value) -> str:
        if value is None or value == "":
            return "-"
        return str(value)

    def _format_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "-"
        return str(value)

    def _format_cost(self, value: float) -> str:
        return f"${float(value or 0):.2f}"

    def _format_currency(self, value: object) -> str:
        try:
            if value in (None, ""):
                return "-"
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value or "-")

    def _format_confidence(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{float(value):.2f}"

    def _policy_value_text(self, value) -> str:
        if value is None:
            return "-"
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item) for item in value) or "-"
        return str(value)

    def _memory_summary_text(self, memory: AutomationMemoryRecord) -> str:
        text = str(memory.content_text or "").strip()
        if text:
            return text[:120]
        if memory.content_json is None:
            return "-"
        if isinstance(memory.content_json, str):
            return memory.content_json[:120]
        try:
            return json.dumps(memory.content_json, sort_keys=True)[:120]
        except Exception:
            return str(memory.content_json)[:120]

    def _setting_state_text(self, setting: AutomationSettingRecord) -> str:
        return (
            f"Enabled {self._bool_text(setting.enabled)} | "
            f"Safe {self._bool_text(setting.safe_mode)} | "
            f"Notify {self._bool_text(setting.notify_only)} | "
            f"Draft {self._bool_text(setting.draft_only)}"
        )

    def _setting_state_detail(self, setting: AutomationSettingRecord) -> str:
        return (
            f"Enabled: {self._bool_text(setting.enabled)}; "
            f"SafeMode: {self._bool_text(setting.safe_mode)}; "
            f"NotifyOnly: {self._bool_text(setting.notify_only)}; "
            f"DraftOnly: {self._bool_text(setting.draft_only)}; "
            f"ApprovalRequired: {self._bool_text(setting.approval_required)}"
        )

    def _last_run_summary(self, latest_run: AutomationRunRecord | None) -> str:
        if latest_run is None:
            return "-"
        return (
            f"{self._format_timestamp(latest_run.started_at)} | "
            f"Status: {latest_run.status} | Trigger: {latest_run.trigger_type or '-'}"
        )

    def _escape(self, text: str) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class AutomationDashboardPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="dashboard")


class AutomationPendingApprovalsPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="pending_approvals")


class AutomationQuestionsPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="questions")


class AutomationObligationsPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="obligations")


class AutomationOutboundDraftsPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="outbound_drafts")


class AutomationIntakePage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="intake")


class AutomationEventsPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="activity")


class AutomationMemoryPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="memory")


class AutomationPolicyPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="policies")


class AutomationProviderStatusPage(AutomationCenterPage):
    def __init__(self, main_window=None) -> None:
        super().__init__(main_window, surface_key="provider")
