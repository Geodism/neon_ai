from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
import json
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_memory_service import create_memory
from neon_ai.services.inbound_intake_service import (
    get_inbound_message,
    update_message_classification,
)


_AUTOMATION_PROPOSAL_SCHEMA_READY = False


@dataclass(frozen=True)
class AutomationProposalRecord:
    automation_proposal_id: int
    automation_key: str
    workflow: str
    action_type: str
    target_type: str | None
    target_id: str | None
    summary: str
    proposed_change_json: dict[str, Any] | list[Any] | str | None
    evidence_json: dict[str, Any] | list[Any] | str | None
    confidence: float | None
    risk_level: str
    requires_approval: bool
    can_auto_apply_level_2: bool
    blocked_reason: str | None
    status: str
    created_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    applied_at: datetime | None
    error_message: str | None


@dataclass(frozen=True)
class AutomationQuestionRecord:
    automation_question_id: int
    automation_key: str
    workflow: str
    question_type: str
    target_type: str | None
    target_id: str | None
    question_text: str
    choices_json: dict[str, Any] | list[Any] | str | None
    required_before_action: bool
    urgency: str | None
    status: str
    answer: str | None
    created_at: datetime | None
    answered_at: datetime | None
    related_proposal_id: int | None


@dataclass(frozen=True)
class AutomationPendingCounts:
    pending_proposals: int
    open_questions: int


INBOUND_ROUTING_REVIEW_WORKFLOWS = {
    "RFQ Reply / Receive Quotes": "receive_quotes",
    "PO ETA / Parts Ready": "po_receiving",
    "Packing Slip / Receiving": "po_receiving",
    "Vendor Invoice / Payables": "vendor_invoice_payables",
    "Customer Scheduling / Service Inquiry": "customer_scheduling",
    "Lead / New Customer Inquiry": "lead_intake",
    "Estimate Follow-up / Customer Reply": "estimate",
    "AHJ / Permit Status": "ahj_permit",
    "Document Control": "document_control",
    "Spam / Ignore": "spam_ignore",
    "Other / Explain": "other_review",
}
DISAMBIGUATION_QUESTION_TYPES = {
    "customer_disambiguation",
    "site_disambiguation",
    "po_disambiguation",
    "vendor_disambiguation",
    "estimate_disambiguation",
    "work_order_disambiguation",
}


def ensure_automation_proposal_schema(*, force: bool = False) -> None:
    global _AUTOMATION_PROPOSAL_SCHEMA_READY
    if _AUTOMATION_PROPOSAL_SCHEMA_READY and not force:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."AutomationProposal" (
                "AutomationProposalID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "AutomationKey" text NOT NULL,
                "Workflow" text NOT NULL,
                "ActionType" text NOT NULL,
                "TargetType" text,
                "TargetID" text,
                "Summary" text NOT NULL,
                "ProposedChangeJson" jsonb,
                "EvidenceJson" jsonb,
                "Confidence" numeric(5,4),
                "RiskLevel" text NOT NULL DEFAULT 'Medium',
                "RequiresApproval" boolean NOT NULL DEFAULT true,
                "CanAutoApplyLevel2" boolean NOT NULL DEFAULT false,
                "BlockedReason" text,
                "Status" text NOT NULL DEFAULT 'Pending',
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "ApprovedBy" text,
                "ApprovedAt" timestamptz,
                "RejectedBy" text,
                "RejectedAt" timestamptz,
                "AppliedAt" timestamptz,
                "ErrorMessage" text
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationProposal_Status_CreatedAt"
            ON public."AutomationProposal" ("Status", "CreatedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationProposal_Key_CreatedAt"
            ON public."AutomationProposal" ("AutomationKey", "CreatedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."AutomationQuestion" (
                "AutomationQuestionID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "AutomationKey" text NOT NULL,
                "Workflow" text NOT NULL,
                "QuestionType" text NOT NULL,
                "TargetType" text,
                "TargetID" text,
                "QuestionText" text NOT NULL,
                "ChoicesJson" jsonb,
                "RequiredBeforeAction" boolean NOT NULL DEFAULT false,
                "Urgency" text,
                "Status" text NOT NULL DEFAULT 'Open',
                "Answer" text,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "AnsweredAt" timestamptz,
                "RelatedProposalID" integer REFERENCES public."AutomationProposal"("AutomationProposalID") ON DELETE SET NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationQuestion_Status_CreatedAt"
            ON public."AutomationQuestion" ("Status", "CreatedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationQuestion_Key_CreatedAt"
            ON public."AutomationQuestion" ("AutomationKey", "CreatedAt" DESC)
            """
        )
        conn.commit()
        _AUTOMATION_PROPOSAL_SCHEMA_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_proposal(
    *,
    automation_key: str,
    workflow: str,
    action_type: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    summary: str,
    proposed_change_json: Any = None,
    evidence_json: Any = None,
    confidence: float | Decimal | int | str | None = None,
    risk_level: str = "Medium",
    requires_approval: bool = True,
    can_auto_apply_level_2: bool = False,
    blocked_reason: str | None = None,
    status: str = "Pending",
    approved_by: str | None = None,
    approved_at: datetime | None = None,
    rejected_by: str | None = None,
    rejected_at: datetime | None = None,
    applied_at: datetime | None = None,
    error_message: str | None = None,
) -> AutomationProposalRecord:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."AutomationProposal" (
                "AutomationKey",
                "Workflow",
                "ActionType",
                "TargetType",
                "TargetID",
                "Summary",
                "ProposedChangeJson",
                "EvidenceJson",
                "Confidence",
                "RiskLevel",
                "RequiresApproval",
                "CanAutoApplyLevel2",
                "BlockedReason",
                "Status",
                "ApprovedBy",
                "ApprovedAt",
                "RejectedBy",
                "RejectedAt",
                "AppliedAt",
                "ErrorMessage"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(automation_key or "").strip(),
                str(workflow or "").strip(),
                str(action_type or "").strip(),
                target_type,
                None if target_id is None else str(target_id),
                str(summary or "").strip(),
                _serialize_json(proposed_change_json),
                _serialize_json(evidence_json),
                _coerce_decimal_or_none(confidence),
                str(risk_level or "Medium").strip() or "Medium",
                bool(requires_approval),
                bool(can_auto_apply_level_2),
                blocked_reason,
                str(status or "Pending").strip() or "Pending",
                approved_by,
                approved_at,
                rejected_by,
                rejected_at,
                applied_at,
                error_message,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _proposal_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_proposals(
    *,
    limit: int = 200,
    status: str | None = None,
    automation_key: str | None = None,
) -> list[AutomationProposalRecord]:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append('"Status" = %s')
            params.append(str(status).strip())
        if automation_key:
            clauses.append('"AutomationKey" = %s')
            params.append(str(automation_key).strip())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(
            f'''
            SELECT *
            FROM public."AutomationProposal"
            {where_sql}
            ORDER BY "CreatedAt" DESC, "AutomationProposalID" DESC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_proposal_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_proposal(proposal_id: int) -> AutomationProposalRecord | None:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationProposal"
            WHERE "AutomationProposalID" = %s
            LIMIT 1
            """,
            (int(proposal_id),),
        )
        row = cur.fetchone()
        return _proposal_from_row(row) if row else None
    finally:
        conn.close()


def update_proposal_status(
    proposal_id: int,
    *,
    status: str,
    approved_by: str | None = None,
    approved_at: datetime | None = None,
    rejected_by: str | None = None,
    rejected_at: datetime | None = None,
    applied_at: datetime | None = None,
    blocked_reason: str | None = None,
    error_message: str | None = None,
) -> AutomationProposalRecord | None:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."AutomationProposal"
            SET "Status" = %s,
                "ApprovedBy" = COALESCE(%s, "ApprovedBy"),
                "ApprovedAt" = COALESCE(%s, "ApprovedAt"),
                "RejectedBy" = COALESCE(%s, "RejectedBy"),
                "RejectedAt" = COALESCE(%s, "RejectedAt"),
                "AppliedAt" = COALESCE(%s, "AppliedAt"),
                "BlockedReason" = COALESCE(%s, "BlockedReason"),
                "ErrorMessage" = COALESCE(%s, "ErrorMessage")
            WHERE "AutomationProposalID" = %s
            RETURNING *
            """,
            (
                str(status or "").strip(),
                approved_by,
                approved_at,
                rejected_by,
                rejected_at,
                applied_at,
                blocked_reason,
                error_message,
                int(proposal_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _proposal_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_question(
    *,
    automation_key: str,
    workflow: str,
    question_type: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    question_text: str,
    choices_json: Any = None,
    required_before_action: bool = False,
    urgency: str | None = None,
    status: str = "Open",
    answer: str | None = None,
    answered_at: datetime | None = None,
    related_proposal_id: int | None = None,
) -> AutomationQuestionRecord:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."AutomationQuestion" (
                "AutomationKey",
                "Workflow",
                "QuestionType",
                "TargetType",
                "TargetID",
                "QuestionText",
                "ChoicesJson",
                "RequiredBeforeAction",
                "Urgency",
                "Status",
                "Answer",
                "AnsweredAt",
                "RelatedProposalID"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(automation_key or "").strip(),
                str(workflow or "").strip(),
                str(question_type or "").strip(),
                target_type,
                None if target_id is None else str(target_id),
                str(question_text or "").strip(),
                _serialize_json(choices_json),
                bool(required_before_action),
                urgency,
                str(status or "Open").strip() or "Open",
                answer,
                answered_at,
                int(related_proposal_id) if related_proposal_id is not None else None,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _question_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_questions(
    *,
    limit: int = 200,
    status: str | None = None,
    automation_key: str | None = None,
) -> list[AutomationQuestionRecord]:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append('"Status" = %s')
            params.append(str(status).strip())
        if automation_key:
            clauses.append('"AutomationKey" = %s')
            params.append(str(automation_key).strip())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(
            f'''
            SELECT *
            FROM public."AutomationQuestion"
            {where_sql}
            ORDER BY "CreatedAt" DESC, "AutomationQuestionID" DESC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_question_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_questions_for_target(
    *,
    target_type: str,
    target_id: str | int,
    limit: int = 100,
    status: str | None = None,
    automation_key: str | None = None,
    question_type: str | None = None,
) -> list[AutomationQuestionRecord]:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        clauses = ['"TargetType" = %s', '"TargetID" = %s']
        params: list[Any] = [str(target_type or "").strip(), str(target_id)]
        if status:
            clauses.append('"Status" = %s')
            params.append(str(status).strip())
        if automation_key:
            clauses.append('"AutomationKey" = %s')
            params.append(str(automation_key).strip())
        if question_type:
            clauses.append('"QuestionType" = %s')
            params.append(str(question_type).strip())
        where_sql = f"WHERE {' AND '.join(clauses)}"
        cur.execute(
            f'''
            SELECT *
            FROM public."AutomationQuestion"
            {where_sql}
            ORDER BY "CreatedAt" DESC, "AutomationQuestionID" DESC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_question_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_active_question_for_target(
    *,
    target_type: str,
    target_id: str | int,
    automation_key: str | None = None,
    question_types: list[str] | tuple[str, ...] | set[str] | None = None,
) -> AutomationQuestionRecord | None:
    questions = list_questions_for_target(
        target_type=target_type,
        target_id=target_id,
        limit=50,
        automation_key=automation_key,
    )
    allowed_types = {str(item).strip() for item in (question_types or []) if str(item).strip()}
    for question in questions:
        if str(question.status or "").strip().lower() != "open":
            continue
        if allowed_types and str(question.question_type or "").strip() not in allowed_types:
            continue
        return question
    return None


def get_question(question_id: int) -> AutomationQuestionRecord | None:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationQuestion"
            WHERE "AutomationQuestionID" = %s
            LIMIT 1
            """,
            (int(question_id),),
        )
        row = cur.fetchone()
        return _question_from_row(row) if row else None
    finally:
        conn.close()


def answer_question(
    question_id: int,
    *,
    answer: str,
    answered_at: datetime | None = None,
    status: str = "Answered",
) -> AutomationQuestionRecord | None:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."AutomationQuestion"
            SET "Status" = %s,
                "Answer" = %s,
                "AnsweredAt" = COALESCE(%s, NOW())
            WHERE "AutomationQuestionID" = %s
            RETURNING *
            """,
            (
                str(status or "Answered").strip() or "Answered",
                str(answer or "").strip(),
                answered_at,
                int(question_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _question_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def answer_question_structured(
    question_id: int,
    *,
    answer_json: Any,
    answered_by: str = "Operator",
    answered_at: datetime | None = None,
    status: str = "Answered",
) -> AutomationQuestionRecord | None:
    ensure_automation_proposal_schema()
    question = get_question(question_id)
    if question is None:
        return None
    normalized = _normalize_structured_answer(question, answer_json, answered_by=answered_by, answered_at=answered_at)
    updated = answer_question(
        question_id,
        answer=json.dumps(normalized, sort_keys=True),
        answered_at=_coerce_datetime(normalized.get("answered_at")) or answered_at,
        status=status,
    )
    if updated is None:
        return None
    event_json = {
        "question_id": question_id,
        "question_type": question.question_type,
        "workflow": question.workflow,
        "status": updated.status,
        "answer_json": normalized,
    }
    routing_side_effects = _handle_question_side_effects(question, normalized)
    if routing_side_effects:
        normalized.update(routing_side_effects)
        updated = answer_question(
            question_id,
            answer=json.dumps(normalized, sort_keys=True),
            answered_at=_coerce_datetime(normalized.get("answered_at")) or answered_at,
            status=status,
        ) or updated
        event_json["side_effects"] = routing_side_effects
    log_automation_event(
        automation_run_id=None,
        automation_key=question.automation_key,
        event_type="question_answered",
        summary=f"AutomationQuestion #{question_id} answered by operator.",
        target_type="AutomationQuestion",
        target_id=str(question_id),
        event_json=event_json,
    )
    return get_question(question_id)


def dismiss_question_with_reason(
    question_id: int,
    *,
    reason: str,
    dismissed_by: str = "Operator",
    answered_at: datetime | None = None,
    status: str = "Dismissed",
) -> AutomationQuestionRecord | None:
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("Dismiss reason is required.")
    question = get_question(question_id)
    if question is None:
        return None
    payload = {
        "answer_type": "dismissed",
        "answered_by": dismissed_by,
        "operator_note": normalized_reason,
        "dismiss_reason": normalized_reason,
        "answered_at": _datetime_to_iso(answered_at or datetime.now(timezone.utc)),
    }
    updated = answer_question(
        question_id,
        answer=json.dumps(payload, sort_keys=True),
        answered_at=answered_at,
        status=status,
    )
    if updated is None:
        return None
    log_automation_event(
        automation_run_id=None,
        automation_key=question.automation_key,
        event_type="question_dismissed",
        summary=f"AutomationQuestion #{question_id} dismissed by operator.",
        target_type="AutomationQuestion",
        target_id=str(question_id),
        event_json={
            "question_id": question_id,
            "question_type": question.question_type,
            "workflow": question.workflow,
            "status": updated.status,
            "dismiss_reason": normalized_reason,
            "dismissed_by": dismissed_by,
        },
    )
    return get_question(question_id)


def dismiss_question(
    question_id: int,
    *,
    status: str = "Dismissed",
) -> AutomationQuestionRecord | None:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."AutomationQuestion"
            SET "Status" = %s
            WHERE "AutomationQuestionID" = %s
            RETURNING *
            """,
            (
                str(status or "Dismissed").strip() or "Dismissed",
                int(question_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _question_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_question_related_proposal(
    question_id: int,
    *,
    related_proposal_id: int | None,
) -> AutomationQuestionRecord | None:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."AutomationQuestion"
            SET "RelatedProposalID" = %s
            WHERE "AutomationQuestionID" = %s
            RETURNING *
            """,
            (
                int(related_proposal_id) if related_proposal_id is not None else None,
                int(question_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _question_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_counts() -> AutomationPendingCounts:
    ensure_automation_proposal_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN "Status" = 'Pending' THEN 1 ELSE 0 END), 0) AS "PendingProposals",
                0 AS "OpenQuestions"
            FROM public."AutomationProposal"
            """
        )
        proposal_row = cur.fetchone() or {}
        cur.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN "Status" = 'Open' THEN 1 ELSE 0 END), 0) AS "OpenQuestions"
            FROM public."AutomationQuestion"
            """
        )
        question_row = cur.fetchone() or {}
        return AutomationPendingCounts(
            pending_proposals=int(proposal_row.get("PendingProposals") or 0),
            open_questions=int(question_row.get("OpenQuestions") or 0),
        )
    finally:
        conn.close()


def _normalize_structured_answer(
    question: AutomationQuestionRecord,
    answer_json: Any,
    *,
    answered_by: str,
    answered_at: datetime | None,
) -> dict[str, Any]:
    if not isinstance(answer_json, dict):
        raise ValueError("Structured question answer must be a JSON object.")
    normalized = dict(answer_json)
    normalized["answer_type"] = str(normalized.get("answer_type") or question.question_type or "operator_answer").strip()
    normalized["answered_by"] = str(normalized.get("answered_by") or answered_by or "Operator").strip()
    normalized["answered_at"] = _datetime_to_iso(_coerce_datetime(normalized.get("answered_at")) or answered_at or datetime.now(timezone.utc))
    if "confidence_after_review" not in normalized:
        normalized["confidence_after_review"] = 1.0
    if "save_as_memory" not in normalized:
        normalized["save_as_memory"] = False
    if "save_as_routing_memory" in normalized and "save_as_memory" not in answer_json:
        normalized["save_as_memory"] = bool(normalized.get("save_as_routing_memory"))
    if normalized.get("selected_target_id") not in (None, "", "-"):
        normalized["selected_target_id"] = str(normalized.get("selected_target_id")).strip()
    if normalized.get("selected_target_type") not in (None, "", "-"):
        normalized["selected_target_type"] = str(normalized.get("selected_target_type")).strip()
    if normalized.get("selected_workflow"):
        normalized["selected_workflow"] = _normalize_workflow_choice(normalized.get("selected_workflow"))
    if question.question_type == "inbound_routing_review" or question.automation_key == "unknown_inbound_review":
        if not str(normalized.get("selected_workflow") or "").strip():
            raise ValueError("Inbound routing review answers require selected_workflow.")
        if not str(normalized.get("operator_note") or normalized.get("how_i_know") or "").strip():
            raise ValueError("Inbound routing review answers require how_i_know / operator_note.")
        normalized["how_i_know"] = str(
            normalized.get("how_i_know") or normalized.get("operator_note") or ""
        ).strip()
        normalized["operator_note"] = normalized["how_i_know"]
    elif question.question_type in DISAMBIGUATION_QUESTION_TYPES:
        if not str(normalized.get("selected_target_type") or "").strip():
            raise ValueError("Disambiguation answers require selected_target_type.")
        if not str(normalized.get("selected_target_id") or "").strip():
            raise ValueError("Disambiguation answers require selected_target_id.")
    elif not str(normalized.get("operator_note") or normalized.get("selected_option") or "").strip():
        raise ValueError("Answer must include operator_note or selected_option.")
    return normalized


def _handle_question_side_effects(
    question: AutomationQuestionRecord,
    normalized_answer: dict[str, Any],
) -> dict[str, Any] | None:
    if question.question_type != "inbound_routing_review" and question.automation_key != "unknown_inbound_review":
        return None
    if question.target_type != "InboundMessage" or not question.target_id:
        return None
    inbound_message = get_inbound_message(int(question.target_id))
    if inbound_message is None:
        raise ValueError("Inbound routing review target message no longer exists.")
    existing_classification = (
        dict(inbound_message.classification_json)
        if isinstance(inbound_message.classification_json, dict)
        else {}
    )
    operator_review_block = {
        "selected_workflow": normalized_answer.get("selected_workflow"),
        "selected_intent": normalized_answer.get("selected_intent"),
        "how_i_know": normalized_answer.get("how_i_know") or normalized_answer.get("operator_note"),
        "reviewed_by": normalized_answer.get("answered_by") or "Operator",
        "reviewed_at": normalized_answer.get("answered_at"),
        "source_question_id": question.automation_question_id,
        "source_message_id": inbound_message.inbound_message_id,
    }
    existing_classification["operator_review"] = operator_review_block
    updated_message = update_message_classification(
        inbound_message.inbound_message_id,
        classification_json=existing_classification,
        workflow_guess=str(normalized_answer.get("selected_workflow") or inbound_message.workflow_guess or "").strip() or None,
        intent_guess=str(normalized_answer.get("selected_intent") or inbound_message.intent_guess or "").strip() or None,
        confidence=1.0,
        status="OperatorRouted",
    )
    result: dict[str, Any] = {
        "inbound_message_id": inbound_message.inbound_message_id,
        "inbound_message_status": updated_message.status if updated_message else "OperatorRouted",
        "operator_review": operator_review_block,
    }
    if bool(normalized_answer.get("save_as_memory")):
        scope_id = inbound_message.sender or None
        scope_type = "Sender" if scope_id else "RoutingRule"
        memory = create_memory(
            scope_type=scope_type,
            scope_id=scope_id,
            memory_type="operator_routing_explanation",
            content_json={
                "selected_workflow": normalized_answer.get("selected_workflow"),
                "selected_intent": normalized_answer.get("selected_intent"),
                "sender": inbound_message.sender,
                "subject": inbound_message.subject,
                "matched_phrases": normalized_answer.get("matched_phrases"),
                "how_i_know": operator_review_block["how_i_know"],
                "confidence_after_review": 1.0,
                "source_question_id": question.automation_question_id,
                "source_message_id": inbound_message.inbound_message_id,
            },
            content_text=operator_review_block["how_i_know"],
            source="operator_review",
            confidence=1.0,
            created_by=normalized_answer.get("answered_by") or "Operator",
        )
        result["memory"] = {
            "automation_memory_id": memory.automation_memory_id,
            "scope_type": memory.scope_type,
            "scope_id": memory.scope_id,
        }
    return result


def _normalize_workflow_choice(value: Any) -> str:
    text = str(value or "").strip()
    return INBOUND_ROUTING_REVIEW_WORKFLOWS.get(text, text)


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.isoformat()


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _proposal_from_row(row: dict[str, Any]) -> AutomationProposalRecord:
    return AutomationProposalRecord(
        automation_proposal_id=int(row.get("AutomationProposalID") or 0),
        automation_key=str(row.get("AutomationKey") or ""),
        workflow=str(row.get("Workflow") or ""),
        action_type=str(row.get("ActionType") or ""),
        target_type=row.get("TargetType"),
        target_id=None if row.get("TargetID") is None else str(row.get("TargetID")),
        summary=str(row.get("Summary") or ""),
        proposed_change_json=row.get("ProposedChangeJson"),
        evidence_json=row.get("EvidenceJson"),
        confidence=float(row.get("Confidence")) if row.get("Confidence") is not None else None,
        risk_level=str(row.get("RiskLevel") or ""),
        requires_approval=bool(row.get("RequiresApproval")),
        can_auto_apply_level_2=bool(row.get("CanAutoApplyLevel2")),
        blocked_reason=row.get("BlockedReason"),
        status=str(row.get("Status") or ""),
        created_at=row.get("CreatedAt"),
        approved_by=row.get("ApprovedBy"),
        approved_at=row.get("ApprovedAt"),
        rejected_by=row.get("RejectedBy"),
        rejected_at=row.get("RejectedAt"),
        applied_at=row.get("AppliedAt"),
        error_message=row.get("ErrorMessage"),
    )


def _question_from_row(row: dict[str, Any]) -> AutomationQuestionRecord:
    return AutomationQuestionRecord(
        automation_question_id=int(row.get("AutomationQuestionID") or 0),
        automation_key=str(row.get("AutomationKey") or ""),
        workflow=str(row.get("Workflow") or ""),
        question_type=str(row.get("QuestionType") or ""),
        target_type=row.get("TargetType"),
        target_id=None if row.get("TargetID") is None else str(row.get("TargetID")),
        question_text=str(row.get("QuestionText") or ""),
        choices_json=row.get("ChoicesJson"),
        required_before_action=bool(row.get("RequiredBeforeAction")),
        urgency=row.get("Urgency"),
        status=str(row.get("Status") or ""),
        answer=row.get("Answer"),
        created_at=row.get("CreatedAt"),
        answered_at=row.get("AnsweredAt"),
        related_proposal_id=_coerce_int_or_none(row.get("RelatedProposalID")),
    )


def _serialize_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_json_safe(value), sort_keys=True)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(subvalue) for key, subvalue in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _coerce_decimal_or_none(value: float | Decimal | int | str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _coerce_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
