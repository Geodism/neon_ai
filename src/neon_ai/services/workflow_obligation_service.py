from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from typing import Any

from neon_ai.database.connection import get_connection


_WORKFLOW_OBLIGATION_SCHEMA_READY = False

FOUNDATION_STATUS_DETECTED = "detected"
FOUNDATION_STATUS_PROPOSED = "proposed"
FOUNDATION_STATUS_ACCEPTED = "accepted"
FOUNDATION_STATUS_SCHEDULED = "scheduled"
FOUNDATION_STATUS_IN_PROGRESS = "in_progress"
FOUNDATION_STATUS_WAITING_ON_EXTERNAL = "waiting_on_external"
FOUNDATION_STATUS_NEEDS_HUMAN = "needs_human"
FOUNDATION_STATUS_COMPLETED = "completed"
FOUNDATION_STATUS_ESCALATED = "escalated"

FOUNDATION_PRIORITY_LOW = "LOW"
FOUNDATION_PRIORITY_NORMAL = "NORMAL"
FOUNDATION_PRIORITY_HIGH = "HIGH"
FOUNDATION_PRIORITY_URGENT = "URGENT"
FOUNDATION_PRIORITIES = {
    FOUNDATION_PRIORITY_LOW,
    FOUNDATION_PRIORITY_NORMAL,
    FOUNDATION_PRIORITY_HIGH,
    FOUNDATION_PRIORITY_URGENT,
}

FOUNDATION_OBLIGATION_TYPES = {
    "FOLLOW_UP",
    "DEADLINE",
    "APPROVAL_REQUIRED",
    "PAYMENT_DUE",
    "CUSTOMER_INTAKE_REVIEW",
    "MATERIAL_ETA",
    "BACKORDER",
    "PICKUP_READY",
    "RECEIVING_REQUIRED",
    "WORK_ORDER_SETUP_REVIEW",
    "PRICE_EXPIRY",
    "CUSTOMER_RESPONSE_REQUIRED",
    "CUSTOMER_REPLY_REVIEW",
    "VENDOR_RESPONSE_REQUIRED",
    "SCHEDULE_CONSTRAINT",
    "ACCESS_CONSTRAINT",
    "PERMIT_ACTION",
    "INSPECTION_ACTION",
    "DOCUMENT_REQUIRED",
    "DOCUMENT_REVIEW",
    "CHANGE_REQUEST",
    "RISK_FLAG",
    "QUALITY_DEFICIENCY",
    "BILLING_REVIEW",
    "CASH_FLOW_REVIEW",
    "PAYABLE_REVIEW",
    "PAYABLE_EXCEPTION",
    "UNKNOWN_NOVEL",
}

FOUNDATION_RESOLUTION_STATUSES = {
    "PROPOSED",
    "APPROVED",
    "COMPLETED",
    "REJECTED",
    "CANCELLED",
}

WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL = "all"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_TODAY = "today"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_OVERDUE = "overdue"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_DUE_SOON = "due_soon"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_NEEDS_HUMAN = "needs_human"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_CASH_FLOW = "cash_flow"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_RECEIVING = "receiving"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_PAYABLES = "payables"
WORKFLOW_OBLIGATION_OPERATOR_VIEW_DOCUMENTS = "documents"

WORKFLOW_OBLIGATION_OPERATOR_VIEW_LABELS = {
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL: "All",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_TODAY: "Today",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_OVERDUE: "Overdue",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_DUE_SOON: "Due Soon",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_NEEDS_HUMAN: "Needs Human",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_CASH_FLOW: "Cash Flow",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_RECEIVING: "Receiving",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_PAYABLES: "Payables",
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_DOCUMENTS: "Documents",
}
WORKFLOW_OBLIGATION_OPERATOR_VIEW_ORDER = (
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_TODAY,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_OVERDUE,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_DUE_SOON,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_NEEDS_HUMAN,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_CASH_FLOW,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_RECEIVING,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_PAYABLES,
    WORKFLOW_OBLIGATION_OPERATOR_VIEW_DOCUMENTS,
)

STATUS_WAITING = "waiting"
STATUS_SATISFIED = "satisfied"
STATUS_OVERDUE = "overdue"
STATUS_PROPOSAL_CREATED = "proposal_created"
STATUS_SNOOZED = "snoozed"
STATUS_DISMISSED = "dismissed"
STATUS_CANCELLED = "cancelled"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"

ACTIVE_WORKFLOW_OBLIGATION_STATUSES = (
    STATUS_WAITING,
    STATUS_OVERDUE,
    STATUS_PROPOSAL_CREATED,
    STATUS_SNOOZED,
    STATUS_BLOCKED,
    STATUS_FAILED,
    FOUNDATION_STATUS_DETECTED,
    FOUNDATION_STATUS_PROPOSED,
    FOUNDATION_STATUS_ACCEPTED,
    FOUNDATION_STATUS_SCHEDULED,
    FOUNDATION_STATUS_IN_PROGRESS,
    FOUNDATION_STATUS_WAITING_ON_EXTERNAL,
    FOUNDATION_STATUS_NEEDS_HUMAN,
    FOUNDATION_STATUS_ESCALATED,
)
TERMINAL_WORKFLOW_OBLIGATION_STATUSES = (
    STATUS_SATISFIED,
    STATUS_DISMISSED,
    STATUS_CANCELLED,
    FOUNDATION_STATUS_COMPLETED,
)
ALL_WORKFLOW_OBLIGATION_STATUSES = ACTIVE_WORKFLOW_OBLIGATION_STATUSES + TERMINAL_WORKFLOW_OBLIGATION_STATUSES

EDITABLE_OBLIGATION_FIELDS = {
    "title": "Title",
    "description": "Description",
    "expected_by": "ExpectedBy",
    "severity": "Severity",
    "owner_role": "OwnerRole",
    "owner_user_id": "OwnerUserID",
    "escalation_policy_code": "EscalationPolicyCode",
    "resolution_notes": "ResolutionNotes",
    "notes": "Notes",
}
GUARDED_OBLIGATION_FIELDS = {
    "obligation_id",
    "source_record_type",
    "source_record_id",
    "workflow_type",
    "expected_event_type",
    "created_at",
    "satisfied_at",
    "satisfied_by_record_type",
    "satisfied_by_record_id",
    "last_proposal_id",
    "status",
    "last_checked_at",
    "updated_at",
    "snooze_until",
}


@dataclass(frozen=True)
class WorkflowObligationRecord:
    obligation_id: int
    obligation_type: str | None = None
    source_record_type: str = ""
    source_record_id: str = ""
    source_entity_type: str | None = None
    source_entity_id: str | None = None
    source_event_id: str | None = None
    source_message_id: str | None = None
    source_automation_run_id: int | None = None
    source_automation_proposal_id: int | None = None
    source_automation_question_id: int | None = None
    workflow_type: str = ""
    expected_event_type: str = ""
    expected_by: datetime | None = None
    due_date: date | None = None
    due_time: time | None = None
    due_window: str | None = None
    severity: str = ""
    priority: str | None = None
    status: str = ""
    confidence: float | None = None
    requires_human_review: bool = True
    requires_approval: bool = False
    can_auto_resolve: bool = False
    owner_role: str | None = None
    owner_user_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None
    satisfied_at: datetime | None = None
    satisfied_by_record_type: str | None = None
    satisfied_by_record_id: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    last_checked_at: datetime | None = None
    last_proposal_id: int | None = None
    escalation_level: int = 0
    escalation_policy_code: str | None = None
    title: str | None = None
    description: str | None = None
    evidence_json: dict[str, Any] | list[Any] | str | None = None
    resolution_type: str | None = None
    resolution_notes: str | None = None
    suggested_resolution: str | None = None
    notes: str | None = None
    snooze_until: datetime | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class WorkflowObligationChangeLogRecord:
    change_id: int
    obligation_id: int
    changed_at: datetime | None
    changed_by: str | None
    change_source: str | None
    old_json: dict[str, Any] | list[Any] | str | None
    new_json: dict[str, Any] | list[Any] | str | None
    reason: str | None


@dataclass(frozen=True)
class WorkflowObligationEventRecord:
    workflow_obligation_event_id: int
    obligation_id: int
    event_type: str
    event_message: str
    event_json: dict[str, Any] | list[Any] | str | None
    created_by: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class WorkflowObligationLinkRecord:
    workflow_obligation_link_id: int
    obligation_id: int
    linked_entity_type: str
    linked_entity_id: str
    link_role: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class WorkflowObligationResolutionRecord:
    workflow_obligation_resolution_id: int
    obligation_id: int
    resolution_type: str
    resolution_status: str
    resolution_json: dict[str, Any] | list[Any] | str | None
    resolved_by: str | None
    resolved_at: datetime | None
    notes: str | None


def ensure_workflow_obligation_schema(*, force: bool = False) -> None:
    global _WORKFLOW_OBLIGATION_SCHEMA_READY
    if _WORKFLOW_OBLIGATION_SCHEMA_READY and not force:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."WorkflowObligation" (
                "ObligationID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "SourceRecordType" text NOT NULL,
                "SourceRecordID" text NOT NULL,
                "WorkflowType" text NOT NULL,
                "ExpectedEventType" text NOT NULL,
                "ExpectedBy" timestamptz,
                "Severity" text NOT NULL DEFAULT 'Medium',
                "Status" text NOT NULL DEFAULT 'waiting',
                "OwnerRole" text,
                "OwnerUserID" text,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "UpdatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "SatisfiedAt" timestamptz,
                "SatisfiedByRecordType" text,
                "SatisfiedByRecordID" text,
                "LastCheckedAt" timestamptz,
                "LastProposalID" integer,
                "EscalationLevel" integer NOT NULL DEFAULT 0,
                "EscalationPolicyCode" text,
                "Title" text,
                "Description" text,
                "EvidenceJson" jsonb,
                "ResolutionNotes" text,
                "Notes" text,
                "SnoozeUntil" timestamptz
            )
            """
        )
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "ResolutionNotes" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "Notes" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SnoozeUntil" timestamptz')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "ObligationType" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SourceEntityType" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SourceEntityID" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SourceEventID" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SourceMessageID" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SourceAutomationRunID" integer')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SourceAutomationProposalID" integer')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SourceAutomationQuestionID" integer')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "Priority" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "DueDate" date')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "DueTime" time')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "DueWindow" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "Confidence" numeric(6,4)')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "RequiresHumanReview" boolean NOT NULL DEFAULT TRUE')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "RequiresApproval" boolean NOT NULL DEFAULT FALSE')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "CanAutoResolve" boolean NOT NULL DEFAULT FALSE')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "ResolutionType" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "SuggestedResolution" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "CreatedBy" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "ResolvedAt" timestamptz')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "ResolvedBy" text')
        cur.execute('ALTER TABLE public."WorkflowObligation" ADD COLUMN IF NOT EXISTS "IdempotencyKey" text')
        cur.execute(
            """
            UPDATE public."WorkflowObligation"
            SET "SourceEntityType" = COALESCE(NULLIF("SourceEntityType", ''), "SourceRecordType"),
                "SourceEntityID" = COALESCE(NULLIF("SourceEntityID", ''), "SourceRecordID"),
                "Priority" = COALESCE(NULLIF("Priority", ''), CASE
                    WHEN UPPER(COALESCE("Severity", '')) = 'LOW' THEN 'LOW'
                    WHEN UPPER(COALESCE("Severity", '')) = 'HIGH' THEN 'HIGH'
                    WHEN UPPER(COALESCE("Severity", '')) = 'CRITICAL' THEN 'URGENT'
                    ELSE 'NORMAL'
                END),
                "DueDate" = COALESCE("DueDate", ("ExpectedBy" AT TIME ZONE 'UTC')::date),
                "DueTime" = COALESCE("DueTime", ("ExpectedBy" AT TIME ZONE 'UTC')::time),
                "RequiresHumanReview" = COALESCE("RequiresHumanReview", TRUE),
                "RequiresApproval" = COALESCE("RequiresApproval", FALSE),
                "CanAutoResolve" = COALESCE("CanAutoResolve", FALSE)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."WorkflowObligationChangeLog" (
                "ChangeID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "ObligationID" integer NOT NULL REFERENCES public."WorkflowObligation"("ObligationID") ON DELETE CASCADE,
                "ChangedAt" timestamptz NOT NULL DEFAULT NOW(),
                "ChangedBy" text,
                "ChangeSource" text,
                "OldJson" jsonb,
                "NewJson" jsonb,
                "Reason" text
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."WorkflowObligationEvent" (
                "WorkflowObligationEventID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "ObligationID" integer NOT NULL REFERENCES public."WorkflowObligation"("ObligationID") ON DELETE CASCADE,
                "EventType" text NOT NULL,
                "EventMessage" text NOT NULL,
                "EventJson" jsonb,
                "CreatedBy" text,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."WorkflowObligationLink" (
                "WorkflowObligationLinkID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "ObligationID" integer NOT NULL REFERENCES public."WorkflowObligation"("ObligationID") ON DELETE CASCADE,
                "LinkedEntityType" text NOT NULL,
                "LinkedEntityID" text NOT NULL,
                "LinkRole" text,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."WorkflowObligationResolution" (
                "WorkflowObligationResolutionID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "ObligationID" integer NOT NULL REFERENCES public."WorkflowObligation"("ObligationID") ON DELETE CASCADE,
                "ResolutionType" text NOT NULL,
                "ResolutionStatus" text NOT NULL,
                "ResolutionJson" jsonb,
                "ResolvedBy" text,
                "ResolvedAt" timestamptz NOT NULL DEFAULT NOW(),
                "Notes" text
            )
            """
        )
        cur.execute(
            """
            UPDATE public."WorkflowObligation"
            SET "Status" = CASE LOWER(COALESCE("Status", ''))
                WHEN 'active' THEN 'waiting'
                WHEN 'overdue' THEN 'overdue'
                WHEN 'satisfied' THEN 'satisfied'
                WHEN 'proposal_created' THEN 'proposal_created'
                WHEN 'snoozed' THEN 'snoozed'
                WHEN 'dismissed' THEN 'dismissed'
                WHEN 'cancelled' THEN 'cancelled'
                WHEN 'blocked' THEN 'blocked'
                WHEN 'failed' THEN 'failed'
                ELSE COALESCE(NULLIF(LOWER("Status"), ''), 'waiting')
            END
            WHERE COALESCE("Status", '') <> CASE LOWER(COALESCE("Status", ''))
                WHEN 'active' THEN 'waiting'
                WHEN 'overdue' THEN 'overdue'
                WHEN 'satisfied' THEN 'satisfied'
                WHEN 'proposal_created' THEN 'proposal_created'
                WHEN 'snoozed' THEN 'snoozed'
                WHEN 'dismissed' THEN 'dismissed'
                WHEN 'cancelled' THEN 'cancelled'
                WHEN 'blocked' THEN 'blocked'
                WHEN 'failed' THEN 'failed'
                ELSE COALESCE(NULLIF(LOWER("Status"), ''), 'waiting')
            END
            """
        )
        cur.execute('DROP INDEX IF EXISTS public."uidx_WorkflowObligation_Active_Source_Event"')
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS "uidx_WorkflowObligation_Active_Source_Event"
            ON public."WorkflowObligation" ("SourceRecordType", "SourceRecordID", "ExpectedEventType")
            WHERE "Status" IN ('waiting', 'overdue', 'proposal_created', 'snoozed', 'blocked', 'failed')
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligation_Status_ExpectedBy"
            ON public."WorkflowObligation" ("Status", "ExpectedBy" ASC, "ObligationID" ASC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligation_Source_Event"
            ON public."WorkflowObligation" ("SourceRecordType", "SourceRecordID", "ExpectedEventType", "ObligationID" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligationChangeLog_Obligation"
            ON public."WorkflowObligationChangeLog" ("ObligationID", "ChangeID" DESC)
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS "uidx_WorkflowObligation_IdempotencyKey"
            ON public."WorkflowObligation" ("IdempotencyKey")
            WHERE "IdempotencyKey" IS NOT NULL
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligation_SourceAutomationProposalID"
            ON public."WorkflowObligation" ("SourceAutomationProposalID", "ObligationID" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligation_SourceAutomationQuestionID"
            ON public."WorkflowObligation" ("SourceAutomationQuestionID", "ObligationID" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligation_SourceEntity"
            ON public."WorkflowObligation" ("SourceEntityType", "SourceEntityID", "ObligationID" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligationEvent_Obligation"
            ON public."WorkflowObligationEvent" ("ObligationID", "WorkflowObligationEventID" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligationLink_Obligation"
            ON public."WorkflowObligationLink" ("ObligationID", "WorkflowObligationLinkID" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligationLink_Entity"
            ON public."WorkflowObligationLink" ("LinkedEntityType", "LinkedEntityID", "WorkflowObligationLinkID" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_WorkflowObligationResolution_Obligation"
            ON public."WorkflowObligationResolution" ("ObligationID", "WorkflowObligationResolutionID" DESC)
            """
        )
        conn.commit()
        _WORKFLOW_OBLIGATION_SCHEMA_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_workflow_obligation(
    *,
    obligation_type: str,
    title: str,
    description: str | None = None,
    source_entity_type: str | None = None,
    source_entity_id: str | int | None = None,
    source_event_id: str | int | None = None,
    source_message_id: str | int | None = None,
    source_automation_run_id: int | None = None,
    source_automation_proposal_id: int | None = None,
    source_automation_question_id: int | None = None,
    status: str = "DETECTED",
    priority: str = FOUNDATION_PRIORITY_NORMAL,
    owner_user_id: str | None = None,
    owner_role: str | None = None,
    due_date: date | datetime | str | None = None,
    due_time: time | datetime | str | None = None,
    due_window: str | None = None,
    confidence: float | None = None,
    requires_human_review: bool = True,
    requires_approval: bool = False,
    can_auto_resolve: bool = False,
    resolution_type: str | None = None,
    suggested_resolution: str | None = None,
    evidence_json: Any = None,
    notes: str | None = None,
    created_by: str | None = "Operator",
    workflow_type: str | None = None,
    expected_event_type: str | None = None,
    resolution_notes: str | None = None,
    idempotency_key: str | None = None,
) -> WorkflowObligationRecord:
    ensure_workflow_obligation_schema()
    normalized_title = str(title or "").strip()
    if not normalized_title:
        raise ValueError("WorkflowObligation title is required.")
    normalized_type = _normalize_obligation_type(obligation_type)
    normalized_status = _normalize_status(status)
    normalized_priority = _normalize_priority(priority)
    normalized_due_date = _coerce_date_only(due_date)
    normalized_due_time = _coerce_time_only(due_time)
    normalized_due_window = None if due_window in (None, "") else str(due_window)
    normalized_confidence = _coerce_confidence(confidence)
    normalized_resolution_type = None if resolution_type in (None, "") else str(resolution_type)
    legacy_source_type, legacy_source_id = _derive_legacy_source_fields(
        source_entity_type=source_entity_type,
        source_entity_id=source_entity_id,
        source_automation_proposal_id=source_automation_proposal_id,
        source_automation_question_id=source_automation_question_id,
        source_automation_run_id=source_automation_run_id,
        source_event_id=source_event_id,
        source_message_id=source_message_id,
    )
    normalized_workflow_type = str(workflow_type or "workflow_obligation").strip()
    normalized_expected_event_type = str(expected_event_type or normalized_type).strip()
    normalized_idempotency_key = None if idempotency_key in (None, "") else str(idempotency_key).strip()

    if normalized_idempotency_key:
        existing = get_workflow_obligation_by_idempotency_key(normalized_idempotency_key)
        if existing is not None:
            return existing

    expected_by = _combine_due_datetime(normalized_due_date, normalized_due_time)
    severity = _priority_to_severity(normalized_priority)

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."WorkflowObligation" (
                "ObligationType",
                "Title",
                "Description",
                "SourceRecordType",
                "SourceRecordID",
                "SourceEntityType",
                "SourceEntityID",
                "SourceEventID",
                "SourceMessageID",
                "SourceAutomationRunID",
                "SourceAutomationProposalID",
                "SourceAutomationQuestionID",
                "WorkflowType",
                "ExpectedEventType",
                "ExpectedBy",
                "DueDate",
                "DueTime",
                "DueWindow",
                "Severity",
                "Priority",
                "Status",
                "OwnerUserID",
                "OwnerRole",
                "Confidence",
                "RequiresHumanReview",
                "RequiresApproval",
                "CanAutoResolve",
                "ResolutionType",
                "SuggestedResolution",
                "EvidenceJson",
                "Notes",
                "CreatedBy",
                "ResolutionNotes",
                "IdempotencyKey"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                normalized_type,
                normalized_title,
                description,
                legacy_source_type,
                legacy_source_id,
                None if source_entity_type in (None, "") else str(source_entity_type).strip(),
                None if source_entity_id in (None, "") else str(source_entity_id),
                None if source_event_id in (None, "") else str(source_event_id),
                None if source_message_id in (None, "") else str(source_message_id),
                int(source_automation_run_id) if source_automation_run_id is not None else None,
                int(source_automation_proposal_id) if source_automation_proposal_id is not None else None,
                int(source_automation_question_id) if source_automation_question_id is not None else None,
                normalized_workflow_type,
                normalized_expected_event_type,
                expected_by,
                normalized_due_date,
                normalized_due_time,
                normalized_due_window,
                severity,
                normalized_priority,
                normalized_status,
                owner_user_id,
                owner_role,
                normalized_confidence,
                bool(requires_human_review),
                bool(requires_approval),
                bool(can_auto_resolve),
                normalized_resolution_type,
                suggested_resolution,
                _serialize_json(evidence_json),
                notes,
                created_by,
                resolution_notes,
                normalized_idempotency_key,
            ),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("WorkflowObligation foundation insert returned no row.")
        record = _obligation_from_row(row)
        _insert_change_log(
            cur,
            obligation_id=record.obligation_id,
            changed_by=created_by,
            change_source="foundation",
            old_json=None,
            new_json=_record_json(record),
            reason="Created workflow obligation foundation record.",
        )
        _insert_obligation_event_row(
            cur,
            obligation_id=record.obligation_id,
            event_type="obligation_created",
            event_message=f"Created workflow obligation '{record.title or normalized_type}'.",
            event_json={
                "obligation_type": record.obligation_type,
                "priority": record.priority,
                "status": record.status,
                "no_business_mutation_occurred": True,
            },
            created_by=created_by,
        )
        conn.commit()
        return record
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_workflow_obligation_by_idempotency_key(idempotency_key: str) -> WorkflowObligationRecord | None:
    ensure_workflow_obligation_schema()
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligation"
            WHERE "IdempotencyKey" = %s
            LIMIT 1
            """,
            (key,),
        )
        row = cur.fetchone()
        return _obligation_from_row(row) if row else None
    finally:
        conn.close()


def list_open_workflow_obligations(
    *,
    limit: int = 500,
    source_entity_type: str | None = None,
    owner_role: str | None = None,
    requires_human_review: bool | None = None,
) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    clauses = ['"Status" = ANY(%s)']
    params: list[Any] = [list(ACTIVE_WORKFLOW_OBLIGATION_STATUSES)]
    if source_entity_type:
        clauses.append('COALESCE("SourceEntityType", "SourceRecordType") = %s')
        params.append(str(source_entity_type).strip())
    if owner_role:
        clauses.append('COALESCE("OwnerRole", \'\') = %s')
        params.append(str(owner_role).strip())
    if requires_human_review is not None:
        clauses.append('"RequiresHumanReview" = %s')
        params.append(bool(requires_human_review))
    return _list_obligation_rows(where_clauses=clauses, params=params, limit=limit, order_sql='COALESCE("SnoozeUntil", "ExpectedBy") ASC NULLS LAST, "ObligationID" DESC')


def list_due_workflow_obligations(
    *,
    limit: int = 200,
    now: datetime | None = None,
    source_entity_type: str | None = None,
) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    current = _coerce_datetime(now or datetime.now(timezone.utc)) or datetime.now(timezone.utc)
    clauses = [
        '"Status" = ANY(%s)',
        'COALESCE("SnoozeUntil", "ExpectedBy") IS NOT NULL',
        'COALESCE("SnoozeUntil", "ExpectedBy") <= %s',
    ]
    params: list[Any] = [list(ACTIVE_WORKFLOW_OBLIGATION_STATUSES), current]
    if source_entity_type:
        clauses.append('COALESCE("SourceEntityType", "SourceRecordType") = %s')
        params.append(str(source_entity_type).strip())
    return _list_obligation_rows(where_clauses=clauses, params=params, limit=limit, order_sql='COALESCE("SnoozeUntil", "ExpectedBy") ASC, "ObligationID" DESC')


def list_obligations_for_entity(source_entity_type: str, source_entity_id: str | int) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    return _list_obligation_rows(
        where_clauses=['COALESCE("SourceEntityType", "SourceRecordType") = %s', 'COALESCE("SourceEntityID", "SourceRecordID") = %s'],
        params=[str(source_entity_type).strip(), str(source_entity_id)],
        limit=500,
        order_sql='"ObligationID" DESC',
    )


def list_obligations_for_automation_proposal(proposal_id: int) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    return _list_obligation_rows(
        where_clauses=['"SourceAutomationProposalID" = %s'],
        params=[int(proposal_id)],
        limit=500,
        order_sql='"ObligationID" DESC',
    )


def list_obligations_for_automation_question(question_id: int) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    return _list_obligation_rows(
        where_clauses=['"SourceAutomationQuestionID" = %s'],
        params=[int(question_id)],
        limit=500,
        order_sql='"ObligationID" DESC',
    )


def filter_workflow_obligations_for_operator_view(
    obligations: list[WorkflowObligationRecord],
    view_name: str,
    *,
    now: datetime | None = None,
) -> list[WorkflowObligationRecord]:
    current = _coerce_datetime(now or datetime.now(timezone.utc)) or datetime.now(timezone.utc)
    normalized_view = _normalize_operator_view_name(view_name)
    return [
        obligation
        for obligation in obligations
        if _workflow_obligation_matches_operator_view(obligation, normalized_view, current)
    ]


def summarize_workflow_obligation_operator_views(
    obligations: list[WorkflowObligationRecord],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    current = _coerce_datetime(now or datetime.now(timezone.utc)) or datetime.now(timezone.utc)
    return {
        view_name: sum(
            1
            for obligation in obligations
            if _workflow_obligation_matches_operator_view(obligation, view_name, current)
        )
        for view_name in WORKFLOW_OBLIGATION_OPERATOR_VIEW_ORDER
    }


def list_obligations_for_operator_view(
    view_name: str,
    *,
    limit: int = 500,
    now: datetime | None = None,
) -> list[WorkflowObligationRecord]:
    normalized_view = _normalize_operator_view_name(view_name)
    obligations = list_workflow_obligations(limit=limit)
    filtered = filter_workflow_obligations_for_operator_view(
        obligations,
        normalized_view,
        now=now,
    )
    current = _coerce_datetime(now or datetime.now(timezone.utc)) or datetime.now(timezone.utc)
    return sorted(
        filtered,
        key=lambda obligation: _operator_view_sort_key(obligation, current),
    )


def summarize_obligation_next_action(obligation: WorkflowObligationRecord) -> str:
    obligation_type = str(obligation.obligation_type or "").strip().upper()
    workflow_type = str(obligation.workflow_type or "").strip().lower()
    expected_event_type = str(obligation.expected_event_type or "").strip().lower()
    try:
        from neon_ai.services.obligation_behavior_parameter_service import build_obligation_behavior_context

        behavior_context = build_obligation_behavior_context(obligation)
        preferred_summary = str(behavior_context.get("preferred_action_summary") or "").strip()
        if preferred_summary:
            return preferred_summary
    except Exception:
        pass

    if obligation_type == "CUSTOMER_INTAKE_REVIEW":
        return "Review lead intake and apply draft Customer/Site/Estimate if appropriate."
    if obligation_type == "WORK_ORDER_SETUP_REVIEW":
        return "Review accepted estimate and apply/create WorkOrder Draft if appropriate."
    if obligation_type == "BILLING_REVIEW":
        return "Review customer billing status and create/apply Draft Customer Invoice if appropriate."
    if obligation_type == "CASH_FLOW_REVIEW":
        return "Review customer billing/payment exposure before vendor payment pressure increases."
    if obligation_type == "PAYABLE_REVIEW":
        return "Reconcile vendor invoice against PO, receipt, and customer billing context."
    if obligation_type == "PAYABLE_EXCEPTION":
        return "Resolve payable exception. No PO, no money. Find/link PO, dispute, or mark not payable."
    if obligation_type == "RECEIVING_REQUIRED":
        return "Review packing slip/receipt evidence and apply receiving only if safe."
    if obligation_type == "MATERIAL_ETA":
        return "Review vendor ETA/status and apply PO ETA/status observation if appropriate."
    if obligation_type == "PICKUP_READY":
        return "Review pickup-ready notice and coordinate material pickup."
    if obligation_type == "BACKORDER":
        return "Review backorder/partial availability and update material planning."
    if obligation_type in {"DOCUMENT_REVIEW", "DOCUMENT_REQUIRED"}:
        return "Review attachment extraction/OCR issue and supply readable document text if needed."
    if obligation_type == "CUSTOMER_REPLY_REVIEW":
        return "Review customer reply and decide whether estimate/revision/follow-up action is needed."

    if workflow_type == "cash_flow_review":
        return "Review cash-flow exposure and confirm the next operator step."
    if workflow_type == "vendor_invoice_payables":
        return "Review vendor-invoice payables context and resolve the exception or reconciliation need."
    if workflow_type == "receive_goods":
        return "Review staged receiving context and confirm whether the route is safe to continue."
    if workflow_type == "attachment_intake":
        return "Review attachment context and resolve the document issue."
    if expected_event_type == "customer_reply_reviewed":
        return "Review the customer reply and decide the next estimate/customer action."
    return "Review obligation and decide the next safe operator step."


def build_obligation_operator_summary(
    obligation: WorkflowObligationRecord,
    *,
    now: datetime | None = None,
    links: list[WorkflowObligationLinkRecord] | None = None,
    recent_events: list[WorkflowObligationEventRecord] | None = None,
    recent_event_limit: int = 5,
) -> dict[str, Any]:
    current = _coerce_datetime(now or datetime.now(timezone.utc)) or datetime.now(timezone.utc)
    link_records = list(links) if links is not None else list_workflow_obligation_links(obligation.obligation_id)
    event_records = (
        list(recent_events)
        if recent_events is not None
        else list_workflow_obligation_events(obligation.obligation_id)[: max(1, int(recent_event_limit or 5))]
    )
    effective_due = _workflow_obligation_effective_due(obligation)
    cash_flow_critical = _workflow_obligation_is_cash_flow(obligation)
    needs_human = _workflow_obligation_needs_human(obligation)
    overdue = bool(effective_due is not None and effective_due < current)
    source_summary = _workflow_obligation_source_summary(obligation, link_records)
    return {
        "owner_summary": _workflow_obligation_owner_summary(obligation),
        "due_summary": _workflow_obligation_due_summary(obligation, current),
        "next_operator_action": summarize_obligation_next_action(obligation),
        "cash_flow_critical": cash_flow_critical,
        "needs_human": needs_human,
        "source_summary": source_summary,
        "source_linked": bool(link_records),
        "overdue": overdue,
        "risk_summary": (
            f"Priority: {obligation.priority or obligation.severity or '-'} | "
            f"Cash-flow critical: {'Yes' if cash_flow_critical else 'No'} | "
            f"Needs human: {'Yes' if needs_human else 'No'} | "
            f"Overdue: {'Yes' if overdue or str(obligation.status).strip().lower() == 'overdue' else 'No'} | "
            f"Source linked: {'Yes' if source_summary != '-' else 'No'}"
        ),
        "recent_history_summary": _workflow_obligation_recent_history_summary(
            event_records,
            limit=max(1, int(recent_event_limit or 5)),
        ),
    }


def accept_workflow_obligation(obligation_id: int, accepted_by: str, notes: str | None = None) -> WorkflowObligationRecord:
    updated = _apply_obligation_update(
        obligation_id,
        updates={
            "Status": FOUNDATION_STATUS_ACCEPTED,
            "Notes": _append_note_text(_require_obligation(obligation_id).notes, notes, changed_by=accepted_by, change_source="foundation") if notes else _require_obligation(obligation_id).notes,
        },
        changed_by=accepted_by,
        change_source="foundation",
        reason="Workflow obligation accepted.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be accepted.")
    log_workflow_obligation_event(
        obligation_id,
        event_type="accepted",
        event_message="Workflow obligation accepted.",
        event_json={"notes": notes},
        created_by=accepted_by,
    )
    return updated


def mark_workflow_obligation_in_progress(obligation_id: int, user_id: str | None = None, notes: str | None = None) -> WorkflowObligationRecord:
    actor = user_id or "Operator"
    current = _require_obligation(obligation_id)
    updated = _apply_obligation_update(
        obligation_id,
        updates={
            "Status": FOUNDATION_STATUS_IN_PROGRESS,
            "OwnerUserID": actor,
            "Notes": _append_note_text(current.notes, notes, changed_by=actor, change_source="foundation") if notes else current.notes,
        },
        changed_by=actor,
        change_source="foundation",
        reason="Workflow obligation marked in progress.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be marked in progress.")
    log_workflow_obligation_event(
        obligation_id,
        event_type="in_progress",
        event_message="Workflow obligation marked in progress.",
        event_json={"notes": notes},
        created_by=actor,
    )
    return updated


def complete_workflow_obligation(obligation_id: int, resolved_by: str, notes: str | None = None) -> WorkflowObligationRecord:
    current = _require_obligation(obligation_id)
    updated = _apply_obligation_update(
        obligation_id,
        updates={
            "Status": FOUNDATION_STATUS_COMPLETED,
            "ResolvedAt": datetime.now(timezone.utc),
            "ResolvedBy": resolved_by,
            "ResolutionNotes": notes,
            "Notes": _append_note_text(current.notes, notes, changed_by=resolved_by, change_source="foundation") if notes else current.notes,
        },
        changed_by=resolved_by,
        change_source="foundation",
        reason="Workflow obligation completed.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be completed.")
    create_workflow_obligation_resolution(
        obligation_id=obligation_id,
        resolution_type=updated.resolution_type or "MANUAL_COMPLETION",
        resolution_status="COMPLETED",
        resolution_json={"notes": notes, "completed_via": "complete_workflow_obligation"},
        resolved_by=resolved_by,
        notes=notes,
    )
    log_workflow_obligation_event(
        obligation_id,
        event_type="completed",
        event_message="Workflow obligation completed.",
        event_json={"notes": notes},
        created_by=resolved_by,
    )
    return updated


def dismiss_workflow_obligation(obligation_id: int, dismissed_by: str, reason: str) -> WorkflowObligationRecord:
    updated = dismiss_obligation(
        obligation_id,
        note=reason,
        changed_by=dismissed_by,
        change_source="foundation",
    )
    log_workflow_obligation_event(
        obligation_id,
        event_type="dismissed",
        event_message="Workflow obligation dismissed.",
        event_json={"reason": reason},
        created_by=dismissed_by,
    )
    return updated


def escalate_workflow_obligation(obligation_id: int, escalated_by: str, reason: str) -> WorkflowObligationRecord:
    current = _require_obligation(obligation_id)
    updated = _apply_obligation_update(
        obligation_id,
        updates={
            "Status": FOUNDATION_STATUS_ESCALATED,
            "EscalationLevel": int(current.escalation_level or 0) + 1,
            "ResolutionNotes": reason,
            "Notes": _append_note_text(current.notes, reason, changed_by=escalated_by, change_source="foundation"),
        },
        changed_by=escalated_by,
        change_source="foundation",
        reason="Workflow obligation escalated.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be escalated.")
    log_workflow_obligation_event(
        obligation_id,
        event_type="escalated",
        event_message="Workflow obligation escalated.",
        event_json={"reason": reason},
        created_by=escalated_by,
    )
    return updated


def cancel_workflow_obligation(obligation_id: int, cancelled_by: str, reason: str) -> WorkflowObligationRecord:
    current = _require_obligation(obligation_id)
    updated = _apply_obligation_update(
        obligation_id,
        updates={
            "Status": STATUS_CANCELLED,
            "ResolutionNotes": reason,
            "ResolvedAt": datetime.now(timezone.utc),
            "ResolvedBy": cancelled_by,
            "Notes": _append_note_text(current.notes, reason, changed_by=cancelled_by, change_source="foundation"),
        },
        changed_by=cancelled_by,
        change_source="foundation",
        reason="Workflow obligation cancelled.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be cancelled.")
    log_workflow_obligation_event(
        obligation_id,
        event_type="cancelled",
        event_message="Workflow obligation cancelled.",
        event_json={"reason": reason},
        created_by=cancelled_by,
    )
    return updated


def log_workflow_obligation_event(
    obligation_id: int,
    event_type: str,
    event_message: str,
    event_json: Any = None,
    created_by: str | None = None,
) -> WorkflowObligationEventRecord:
    ensure_workflow_obligation_schema()
    if not str(event_type or "").strip():
        raise ValueError("event_type is required.")
    if not str(event_message or "").strip():
        raise ValueError("event_message is required.")
    conn = get_connection()
    cur = conn.cursor()
    try:
        row = _insert_obligation_event_row(
            cur,
            obligation_id=int(obligation_id),
            event_type=str(event_type).strip(),
            event_message=str(event_message).strip(),
            event_json=event_json,
            created_by=created_by,
        )
        conn.commit()
        return _event_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_workflow_obligation_link(obligation_id: int, linked_entity_type: str, linked_entity_id: str | int, link_role: str) -> WorkflowObligationLinkRecord:
    ensure_workflow_obligation_schema()
    normalized_entity_type = str(linked_entity_type or "").strip()
    normalized_link_role = str(link_role or "").strip()
    if not normalized_entity_type:
        raise ValueError("linked_entity_type is required.")
    if linked_entity_id in (None, ""):
        raise ValueError("linked_entity_id is required.")
    if not normalized_link_role:
        raise ValueError("link_role is required.")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."WorkflowObligationLink" (
                "ObligationID",
                "LinkedEntityType",
                "LinkedEntityID",
                "LinkRole"
            )
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (int(obligation_id), normalized_entity_type, str(linked_entity_id), normalized_link_role),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("WorkflowObligationLink insert returned no row.")
        conn.commit()
        return _link_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_workflow_obligation_events(obligation_id: int) -> list[WorkflowObligationEventRecord]:
    ensure_workflow_obligation_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligationEvent"
            WHERE "ObligationID" = %s
            ORDER BY "WorkflowObligationEventID" DESC
            """,
            (int(obligation_id),),
        )
        return [_event_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_workflow_obligation_links(obligation_id: int) -> list[WorkflowObligationLinkRecord]:
    ensure_workflow_obligation_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligationLink"
            WHERE "ObligationID" = %s
            ORDER BY "WorkflowObligationLinkID" DESC
            """,
            (int(obligation_id),),
        )
        return [_link_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def create_workflow_obligation_resolution(
    *,
    obligation_id: int,
    resolution_type: str,
    resolution_status: str,
    resolution_json: Any = None,
    resolved_by: str | None = None,
    resolved_at: datetime | None = None,
    notes: str | None = None,
) -> WorkflowObligationResolutionRecord:
    ensure_workflow_obligation_schema()
    normalized_type = str(resolution_type or "").strip()
    normalized_status = _normalize_resolution_status(resolution_status)
    if not normalized_type:
        raise ValueError("resolution_type is required.")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."WorkflowObligationResolution" (
                "ObligationID",
                "ResolutionType",
                "ResolutionStatus",
                "ResolutionJson",
                "ResolvedBy",
                "ResolvedAt",
                "Notes"
            )
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
            RETURNING *
            """,
            (
                int(obligation_id),
                normalized_type,
                normalized_status,
                _serialize_json(resolution_json),
                resolved_by,
                _coerce_datetime(resolved_at or datetime.now(timezone.utc)),
                notes,
            ),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("WorkflowObligationResolution insert returned no row.")
        conn.commit()
        return _resolution_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_workflow_obligation_resolutions(obligation_id: int) -> list[WorkflowObligationResolutionRecord]:
    ensure_workflow_obligation_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligationResolution"
            WHERE "ObligationID" = %s
            ORDER BY "WorkflowObligationResolutionID" DESC
            """,
            (int(obligation_id),),
        )
        return [_resolution_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def create_obligation_from_automation_proposal(
    proposal_id: int,
    *,
    obligation_type: str,
    title: str,
    description: str | None = None,
    status: str = "PROPOSED",
    priority: str = FOUNDATION_PRIORITY_NORMAL,
    requires_human_review: bool = True,
    requires_approval: bool = True,
    evidence_json: Any = None,
    created_by: str | None = "Operator",
    idempotency_key: str | None = None,
) -> WorkflowObligationRecord:
    from neon_ai.services.automation_proposal_service import get_proposal

    proposal = get_proposal(int(proposal_id))
    if proposal is None:
        raise ValueError(f"AutomationProposal #{proposal_id} was not found.")
    record = create_workflow_obligation(
        obligation_type=obligation_type,
        title=title,
        description=description or proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id if proposal.target_id is not None else proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status=status,
        priority=priority,
        requires_human_review=requires_human_review,
        requires_approval=requires_approval,
        evidence_json=evidence_json
        or {
            "source_proposal_id": proposal.automation_proposal_id,
            "workflow": proposal.workflow,
            "action_type": proposal.action_type,
            "target_type": proposal.target_type,
            "target_id": proposal.target_id,
            "no_business_mutation_occurred": True,
        },
        created_by=created_by,
        workflow_type=proposal.workflow,
        expected_event_type=proposal.action_type,
        idempotency_key=idempotency_key or f"proposal:{proposal.automation_proposal_id}:{_normalize_obligation_type(obligation_type)}",
    )
    add_workflow_obligation_link(record.obligation_id, "AutomationProposal", proposal.automation_proposal_id, "source_proposal")
    return record


def create_obligation_from_automation_question(
    question_id: int,
    *,
    obligation_type: str,
    title: str,
    description: str | None = None,
    status: str = "NEEDS_HUMAN",
    priority: str = FOUNDATION_PRIORITY_NORMAL,
    requires_human_review: bool = True,
    requires_approval: bool = False,
    evidence_json: Any = None,
    created_by: str | None = "Operator",
    idempotency_key: str | None = None,
) -> WorkflowObligationRecord:
    from neon_ai.services.automation_proposal_service import get_question

    question = get_question(int(question_id))
    if question is None:
        raise ValueError(f"AutomationQuestion #{question_id} was not found.")
    record = create_workflow_obligation(
        obligation_type=obligation_type,
        title=title,
        description=description or question.question_text,
        source_entity_type=question.target_type or "AutomationQuestion",
        source_entity_id=question.target_id if question.target_id is not None else question.automation_question_id,
        source_automation_question_id=question.automation_question_id,
        status=status,
        priority=priority,
        requires_human_review=requires_human_review,
        requires_approval=requires_approval,
        evidence_json=evidence_json
        or {
            "source_question_id": question.automation_question_id,
            "workflow": question.workflow,
            "question_type": question.question_type,
            "target_type": question.target_type,
            "target_id": question.target_id,
            "no_business_mutation_occurred": True,
        },
        created_by=created_by,
        workflow_type=question.workflow,
        expected_event_type=question.question_type,
        idempotency_key=idempotency_key or f"question:{question.automation_question_id}:{_normalize_obligation_type(obligation_type)}",
    )
    add_workflow_obligation_link(record.obligation_id, "AutomationQuestion", question.automation_question_id, "source_question")
    return record


def create_obligation_from_automation_event(
    automation_event_id: int,
    *,
    obligation_type: str,
    title: str,
    description: str | None = None,
    status: str = "DETECTED",
    priority: str = FOUNDATION_PRIORITY_NORMAL,
    requires_human_review: bool = True,
    requires_approval: bool = False,
    evidence_json: Any = None,
    created_by: str | None = "Operator",
    idempotency_key: str | None = None,
) -> WorkflowObligationRecord:
    event = _get_automation_event_record(int(automation_event_id))
    if event is None:
        raise ValueError(f"AutomationEvent #{automation_event_id} was not found.")
    record = create_workflow_obligation(
        obligation_type=obligation_type,
        title=title,
        description=description or event.summary,
        source_entity_type=event.target_type or "AutomationEvent",
        source_entity_id=event.target_id or event.automation_event_id,
        source_event_id=event.automation_event_id,
        source_automation_run_id=event.automation_run_id,
        status=status,
        priority=priority,
        requires_human_review=requires_human_review,
        requires_approval=requires_approval,
        evidence_json=evidence_json
        or {
            "source_automation_event_id": event.automation_event_id,
            "automation_key": event.automation_key,
            "event_type": event.event_type,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "event_json": event.event_json,
            "no_business_mutation_occurred": True,
        },
        created_by=created_by,
        workflow_type=event.automation_key,
        expected_event_type=event.event_type,
        idempotency_key=idempotency_key or f"event:{event.automation_event_id}:{_normalize_obligation_type(obligation_type)}",
    )
    add_workflow_obligation_link(record.obligation_id, "AutomationEvent", event.automation_event_id, "source_event")
    return record


def create_obligation_if_missing(
    *,
    source_record_type: str,
    source_record_id: str | int,
    workflow_type: str,
    expected_event_type: str,
    expected_by: datetime | date | None,
    obligation_type: str | None = None,
    severity: str = "Medium",
    priority: str | None = None,
    status: str = STATUS_WAITING,
    owner_role: str | None = None,
    owner_user_id: str | None = None,
    requires_human_review: bool = True,
    requires_approval: bool = False,
    can_auto_resolve: bool = False,
    source_entity_type: str | None = None,
    source_entity_id: str | int | None = None,
    idempotency_key: str | None = None,
    escalation_level: int = 0,
    escalation_policy_code: str | None = None,
    title: str | None = None,
    description: str | None = None,
    evidence_json: Any = None,
    resolution_notes: str | None = None,
    notes: str | None = None,
    snooze_until: datetime | date | None = None,
    changed_by: str | None = "WorkflowObligation watcher",
    change_source: str = "watcher",
    reason: str | None = "Created obligation if missing.",
) -> WorkflowObligationRecord:
    ensure_workflow_obligation_schema()
    existing = get_active_obligation(
        source_record_type=str(source_record_type or "").strip(),
        source_record_id=str(source_record_id),
        expected_event_type=str(expected_event_type or "").strip(),
    )
    if existing is not None:
        return existing
    latest = get_latest_obligation(
        source_record_type=str(source_record_type or "").strip(),
        source_record_id=str(source_record_id),
        expected_event_type=str(expected_event_type or "").strip(),
    )
    if latest is not None:
        return latest

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."WorkflowObligation" (
                "ObligationType",
                "SourceRecordType",
                "SourceRecordID",
                "SourceEntityType",
                "SourceEntityID",
                "WorkflowType",
                "ExpectedEventType",
                "ExpectedBy",
                "DueDate",
                "DueTime",
                "Severity",
                "Priority",
                "Status",
                "RequiresHumanReview",
                "RequiresApproval",
                "CanAutoResolve",
                "OwnerRole",
                "OwnerUserID",
                "CreatedBy",
                "EscalationLevel",
                "EscalationPolicyCode",
                "Title",
                "Description",
                "EvidenceJson",
                "ResolutionNotes",
                "Notes",
                "SnoozeUntil",
                "IdempotencyKey"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT ("SourceRecordType", "SourceRecordID", "ExpectedEventType")
            WHERE "Status" IN ('waiting', 'overdue', 'proposal_created', 'snoozed', 'blocked', 'failed')
            DO NOTHING
            RETURNING *
            """,
            (
                _normalize_obligation_type(obligation_type) if obligation_type else None,
                str(source_record_type or "").strip(),
                str(source_record_id),
                str(source_entity_type or source_record_type or "").strip() or None,
                str(source_entity_id if source_entity_id not in (None, "") else source_record_id),
                str(workflow_type or "").strip(),
                str(expected_event_type or "").strip(),
                _coerce_datetime(expected_by),
                (_coerce_datetime(expected_by).date() if _coerce_datetime(expected_by) is not None else None),
                (_coerce_datetime(expected_by).time() if _coerce_datetime(expected_by) is not None else None),
                str(severity or "Medium").strip() or "Medium",
                str(priority or _priority_from_severity(severity)).strip() or _priority_from_severity(severity),
                _normalize_status(status or STATUS_WAITING),
                bool(requires_human_review),
                bool(requires_approval),
                bool(can_auto_resolve),
                owner_role,
                owner_user_id,
                changed_by,
                int(escalation_level or 0),
                escalation_policy_code,
                title,
                description,
                _serialize_json(evidence_json),
                resolution_notes,
                notes,
                _coerce_datetime(snooze_until),
                idempotency_key,
            ),
        )
        row = cur.fetchone()
        if row is not None:
            record = _obligation_from_row(row)
            _insert_change_log(
                cur,
                obligation_id=record.obligation_id,
                changed_by=changed_by,
                change_source=change_source,
                old_json=None,
                new_json=_record_json(record),
                reason=reason or "Created obligation.",
            )
            conn.commit()
            return record
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    existing = get_active_obligation(
        source_record_type=str(source_record_type or "").strip(),
        source_record_id=str(source_record_id),
        expected_event_type=str(expected_event_type or "").strip(),
    )
    if existing is None:
        raise RuntimeError("WorkflowObligation insert did not return a row and no active matching record could be found.")
    return existing


def _priority_from_severity(severity: str | None) -> str:
    normalized = str(severity or "").strip().upper()
    if normalized in {"LOW", "LOW_PRIORITY"}:
        return FOUNDATION_PRIORITY_LOW
    if normalized in {"HIGH", "CRITICAL", "URGENT"}:
        return FOUNDATION_PRIORITY_HIGH if normalized == "HIGH" else FOUNDATION_PRIORITY_URGENT
    return FOUNDATION_PRIORITY_NORMAL


def list_obligations_for_review(
    *,
    limit: int = 300,
    workflow_type: str | None = None,
    source_record_type: str | None = None,
    include_terminal: bool = False,
) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    clauses: list[str] = []
    params: list[Any] = []
    if not include_terminal:
        clauses.append('"Status" = ANY(%s)')
        params.append(list(ACTIVE_WORKFLOW_OBLIGATION_STATUSES))
    if workflow_type:
        clauses.append('"WorkflowType" = %s')
        params.append(str(workflow_type).strip())
    if source_record_type:
        clauses.append('"SourceRecordType" = %s')
        params.append(str(source_record_type).strip())
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT *
            FROM public."WorkflowObligation"
            {where_sql}
            ORDER BY
                CASE
                    WHEN "Status" = 'overdue' THEN 0
                    WHEN "Status" = 'proposal_created' THEN 1
                    WHEN "Status" = 'waiting' THEN 2
                    WHEN "Status" = 'snoozed' THEN 3
                    ELSE 4
                END,
                COALESCE("SnoozeUntil", "ExpectedBy", "CreatedAt") ASC,
                "ObligationID" DESC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_obligation_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_due_obligations(
    *,
    limit: int = 200,
    workflow_type: str | None = None,
    expected_event_type: str | None = None,
    source_record_type: str | None = None,
    source_record_ids: list[str | int] | None = None,
    now: datetime | None = None,
) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    current = _coerce_datetime(now or datetime.now(timezone.utc))
    clauses = [
        '''
        (
            "Status" IN ('waiting', 'overdue', 'proposal_created', 'blocked', 'failed')
            OR ("Status" = 'snoozed' AND COALESCE("SnoozeUntil", "ExpectedBy") <= %s)
        )
        ''',
        '"ExpectedBy" IS NOT NULL',
        '"ExpectedBy" <= %s',
    ]
    params: list[Any] = [current, current]
    if workflow_type:
        clauses.append('"WorkflowType" = %s')
        params.append(str(workflow_type).strip())
    if expected_event_type:
        clauses.append('"ExpectedEventType" = %s')
        params.append(str(expected_event_type).strip())
    if source_record_type:
        clauses.append('"SourceRecordType" = %s')
        params.append(str(source_record_type).strip())
    if source_record_ids:
        clauses.append('"SourceRecordID" = ANY(%s)')
        params.append([str(item) for item in source_record_ids])

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT *
            FROM public."WorkflowObligation"
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE("SnoozeUntil", "ExpectedBy") ASC, "ObligationID" ASC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_obligation_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_workflow_obligations(
    *,
    limit: int = 500,
    workflow_type: str | None = None,
    expected_event_type: str | None = None,
    status: str | None = None,
    source_record_type: str | None = None,
    source_record_ids: list[str | int] | None = None,
    source_entity_type: str | None = None,
    source_entity_id: str | int | None = None,
    source_automation_proposal_id: int | None = None,
    source_automation_question_id: int | None = None,
) -> list[WorkflowObligationRecord]:
    ensure_workflow_obligation_schema()
    clauses: list[str] = []
    params: list[Any] = []
    if workflow_type:
        clauses.append('"WorkflowType" = %s')
        params.append(str(workflow_type).strip())
    if expected_event_type:
        clauses.append('"ExpectedEventType" = %s')
        params.append(str(expected_event_type).strip())
    if status:
        clauses.append('"Status" = %s')
        params.append(_normalize_status(status))
    if source_record_type:
        clauses.append('"SourceRecordType" = %s')
        params.append(str(source_record_type).strip())
    if source_record_ids:
        clauses.append('"SourceRecordID" = ANY(%s)')
        params.append([str(item) for item in source_record_ids])
    if source_entity_type:
        clauses.append('COALESCE("SourceEntityType", "SourceRecordType") = %s')
        params.append(str(source_entity_type).strip())
    if source_entity_id is not None:
        clauses.append('COALESCE("SourceEntityID", "SourceRecordID") = %s')
        params.append(str(source_entity_id))
    if source_automation_proposal_id is not None:
        clauses.append('"SourceAutomationProposalID" = %s')
        params.append(int(source_automation_proposal_id))
    if source_automation_question_id is not None:
        clauses.append('"SourceAutomationQuestionID" = %s')
        params.append(int(source_automation_question_id))
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT *
            FROM public."WorkflowObligation"
            {where_sql}
            ORDER BY "ObligationID" DESC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_obligation_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_obligation_change_log(obligation_id: int, *, limit: int = 100) -> list[WorkflowObligationChangeLogRecord]:
    ensure_workflow_obligation_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligationChangeLog"
            WHERE "ObligationID" = %s
            ORDER BY "ChangeID" DESC
            LIMIT %s
            """,
            (int(obligation_id), int(limit)),
        )
        return [_change_log_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_active_obligation(
    *,
    source_record_type: str,
    source_record_id: str | int,
    expected_event_type: str,
) -> WorkflowObligationRecord | None:
    ensure_workflow_obligation_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligation"
            WHERE "SourceRecordType" = %s
              AND "SourceRecordID" = %s
              AND "ExpectedEventType" = %s
              AND "Status" = ANY(%s)
            ORDER BY "ObligationID" DESC
            LIMIT 1
            """,
            (
                str(source_record_type).strip(),
                str(source_record_id),
                str(expected_event_type).strip(),
                list(ACTIVE_WORKFLOW_OBLIGATION_STATUSES),
            ),
        )
        row = cur.fetchone()
        return _obligation_from_row(row) if row else None
    finally:
        conn.close()


def get_latest_obligation(
    *,
    source_record_type: str,
    source_record_id: str | int,
    expected_event_type: str,
) -> WorkflowObligationRecord | None:
    ensure_workflow_obligation_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligation"
            WHERE "SourceRecordType" = %s
              AND "SourceRecordID" = %s
              AND "ExpectedEventType" = %s
            ORDER BY "ObligationID" DESC
            LIMIT 1
            """,
            (
                str(source_record_type).strip(),
                str(source_record_id),
                str(expected_event_type).strip(),
            ),
        )
        row = cur.fetchone()
        return _obligation_from_row(row) if row else None
    finally:
        conn.close()


def get_workflow_obligation(obligation_id: int) -> WorkflowObligationRecord | None:
    ensure_workflow_obligation_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."WorkflowObligation"
            WHERE "ObligationID" = %s
            LIMIT 1
            """,
            (int(obligation_id),),
        )
        row = cur.fetchone()
        return _obligation_from_row(row) if row else None
    finally:
        conn.close()


def mark_satisfied(
    obligation_id: int,
    *,
    satisfied_at: datetime | None = None,
    satisfied_by_record_type: str | None = None,
    satisfied_by_record_id: str | int | None = None,
    evidence_json: Any = None,
    resolution_notes: str | None = None,
    changed_by: str | None = "WorkflowObligation watcher",
    change_source: str = "watcher",
    reason: str | None = "Watcher marked obligation satisfied.",
) -> WorkflowObligationRecord | None:
    return _apply_obligation_update(
        obligation_id,
        updates={
            "Status": STATUS_SATISFIED,
            "SatisfiedAt": _coerce_datetime(satisfied_at or datetime.now(timezone.utc)),
            "SatisfiedByRecordType": satisfied_by_record_type,
            "SatisfiedByRecordID": None if satisfied_by_record_id is None else str(satisfied_by_record_id),
            "LastCheckedAt": datetime.now(timezone.utc),
            "EvidenceJson": evidence_json,
            "ResolutionNotes": resolution_notes,
            "SnoozeUntil": None,
        },
        changed_by=changed_by,
        change_source=change_source,
        reason=reason,
    )


def mark_obligation_satisfied_manual(
    obligation_id: int,
    *,
    note: str,
    changed_by: str | None = "Operator",
    change_source: str = "ui",
) -> WorkflowObligationRecord:
    text = str(note or "").strip()
    if not text:
        raise ValueError("Manual satisfaction requires a note or reason.")
    current = _require_obligation(obligation_id)
    resolution_notes = _append_note_text(current.resolution_notes, text, changed_by=changed_by, change_source=change_source)
    updated = mark_satisfied(
        obligation_id,
        satisfied_at=datetime.now(timezone.utc),
        satisfied_by_record_type="Manual",
        satisfied_by_record_id=None,
        evidence_json=current.evidence_json,
        resolution_notes=resolution_notes,
        changed_by=changed_by,
        change_source=change_source,
        reason="Manual satisfaction update.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be marked satisfied.")
    return updated


def dismiss_obligation(
    obligation_id: int,
    *,
    note: str,
    changed_by: str | None = "Operator",
    change_source: str = "ui",
) -> WorkflowObligationRecord:
    text = str(note or "").strip()
    if not text:
        raise ValueError("Dismissal requires a note or reason.")
    current = _require_obligation(obligation_id)
    resolution_notes = _append_note_text(current.resolution_notes, text, changed_by=changed_by, change_source=change_source)
    updated = _apply_obligation_update(
        obligation_id,
        updates={
            "Status": STATUS_DISMISSED,
            "ResolutionNotes": resolution_notes,
            "SnoozeUntil": None,
            "LastCheckedAt": datetime.now(timezone.utc),
        },
        changed_by=changed_by,
        change_source=change_source,
        reason="Obligation dismissed.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be dismissed.")
    return updated


def snooze_obligation(
    obligation_id: int,
    *,
    snooze_until: datetime | date,
    note: str | None = None,
    changed_by: str | None = "Operator",
    change_source: str = "ui",
) -> WorkflowObligationRecord:
    snooze_target = _coerce_datetime(snooze_until)
    if snooze_target is None:
        raise ValueError("SnoozeUntil must be a valid date or datetime.")
    current = _require_obligation(obligation_id)
    notes_text = current.notes
    if note:
        notes_text = _append_note_text(notes_text, str(note), changed_by=changed_by, change_source=change_source)
    updated = _apply_obligation_update(
        obligation_id,
        updates={
            "Status": STATUS_SNOOZED,
            "SnoozeUntil": snooze_target,
            "Notes": notes_text,
            "LastCheckedAt": datetime.now(timezone.utc),
        },
        changed_by=changed_by,
        change_source=change_source,
        reason="Obligation snoozed.",
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be snoozed.")
    return updated


def update_obligation_fields(
    obligation_id: int,
    *,
    fields: dict[str, Any],
    changed_by: str | None = "Operator",
    change_source: str = "ui",
    reason: str | None = "Manual obligation field update.",
) -> WorkflowObligationRecord:
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty mapping of editable obligation fields.")
    normalized_updates: dict[str, Any] = {}
    blocked_fields: list[str] = []
    unknown_fields: list[str] = []
    for key, value in fields.items():
        field_key = str(key or "").strip()
        if field_key in EDITABLE_OBLIGATION_FIELDS:
            normalized_updates[EDITABLE_OBLIGATION_FIELDS[field_key]] = value
        elif field_key in GUARDED_OBLIGATION_FIELDS:
            blocked_fields.append(field_key)
        else:
            unknown_fields.append(field_key)
    if blocked_fields:
        raise ValueError(f"Guarded WorkflowObligation fields cannot be updated generically: {', '.join(sorted(blocked_fields))}.")
    if unknown_fields:
        raise ValueError(f"Unknown WorkflowObligation edit fields: {', '.join(sorted(unknown_fields))}.")
    if not normalized_updates:
        raise ValueError("No editable WorkflowObligation fields were provided.")

    updates: dict[str, Any] = {}
    for column_name, value in normalized_updates.items():
        if column_name == "ExpectedBy":
            updates[column_name] = _coerce_datetime(value)
        elif column_name in {"Title", "Description", "OwnerRole", "OwnerUserID", "EscalationPolicyCode", "ResolutionNotes", "Notes"}:
            updates[column_name] = None if value in (None, "") else str(value)
        elif column_name == "Severity":
            severity = str(value or "").strip()
            if not severity:
                raise ValueError("Severity cannot be blank.")
            updates[column_name] = severity
        else:
            updates[column_name] = value
    updated = _apply_obligation_update(
        obligation_id,
        updates=updates,
        changed_by=changed_by,
        change_source=change_source,
        reason=reason,
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation could not be updated.")
    return updated


def add_obligation_note(
    obligation_id: int,
    *,
    note: str,
    changed_by: str | None = "Operator",
    change_source: str = "ui",
    reason: str | None = "Added obligation note.",
) -> WorkflowObligationRecord:
    text = str(note or "").strip()
    if not text:
        raise ValueError("Note text is required.")
    current = _require_obligation(obligation_id)
    combined = _append_note_text(current.notes, text, changed_by=changed_by, change_source=change_source)
    updated = _apply_obligation_update(
        obligation_id,
        updates={"Notes": combined},
        changed_by=changed_by,
        change_source=change_source,
        reason=reason,
    )
    if updated is None:
        raise RuntimeError("WorkflowObligation note could not be added.")
    return updated


def record_obligation_check(
    obligation_id: int,
    *,
    status: str | None = None,
    last_checked_at: datetime | None = None,
    last_proposal_id: int | None = None,
    escalation_level: int | None = None,
    evidence_json: Any = None,
    resolution_notes: str | None = None,
    changed_by: str | None = "WorkflowObligation watcher",
    change_source: str = "watcher",
    reason: str | None = "Watcher recorded an obligation check.",
) -> WorkflowObligationRecord | None:
    updates: dict[str, Any] = {
        "LastCheckedAt": _coerce_datetime(last_checked_at or datetime.now(timezone.utc)),
    }
    if status is not None:
        updates["Status"] = _normalize_status(status)
        if updates["Status"] == STATUS_SNOOZED and "SnoozeUntil" not in updates:
            current = _require_obligation(obligation_id)
            updates["SnoozeUntil"] = current.snooze_until
    if last_proposal_id is not None:
        updates["LastProposalID"] = int(last_proposal_id)
    if escalation_level is not None:
        updates["EscalationLevel"] = int(escalation_level)
    if evidence_json is not None:
        updates["EvidenceJson"] = evidence_json
    if resolution_notes is not None:
        updates["ResolutionNotes"] = resolution_notes
    return _apply_obligation_update(
        obligation_id,
        updates=updates,
        changed_by=changed_by,
        change_source=change_source,
        reason=reason,
    )


def _apply_obligation_update(
    obligation_id: int,
    *,
    updates: dict[str, Any],
    changed_by: str | None,
    change_source: str,
    reason: str | None,
) -> WorkflowObligationRecord | None:
    ensure_workflow_obligation_schema()
    current = get_workflow_obligation(int(obligation_id))
    if current is None:
        return None
    normalized_updates = _normalize_update_columns(updates, current=current)
    if not normalized_updates:
        return current

    assignments = ['"UpdatedAt" = NOW()']
    params: list[Any] = []
    for column_name, value in normalized_updates.items():
        assignments.append(f'"{column_name}" = %s')
        if column_name == "EvidenceJson":
            params.append(_serialize_json(value))
        else:
            params.append(value)
    params.append(int(obligation_id))

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            UPDATE public."WorkflowObligation"
            SET {', '.join(assignments)}
            WHERE "ObligationID" = %s
            RETURNING *
            ''',
            tuple(params),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        updated = _obligation_from_row(row)
        if _record_json(current) != _record_json(updated):
            _insert_change_log(
                cur,
                obligation_id=updated.obligation_id,
                changed_by=changed_by,
                change_source=change_source,
                old_json=_record_json(current),
                new_json=_record_json(updated),
                reason=reason,
            )
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _normalize_update_columns(updates: dict[str, Any], *, current: WorkflowObligationRecord) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for column_name, value in updates.items():
        if column_name == "Status":
            normalized[column_name] = _normalize_status(value or current.status)
        elif column_name in {"ExpectedBy", "SatisfiedAt", "LastCheckedAt", "SnoozeUntil", "ResolvedAt"}:
            normalized[column_name] = _coerce_datetime(value)
        elif column_name == "DueDate":
            normalized[column_name] = _coerce_date_only(value)
        elif column_name == "DueTime":
            normalized[column_name] = _coerce_time_only(value)
        elif column_name == "LastProposalID":
            normalized[column_name] = None if value in (None, "") else int(value)
        elif column_name == "EscalationLevel":
            normalized[column_name] = int(value or 0)
        elif column_name == "EvidenceJson":
            normalized[column_name] = value
        elif column_name == "Confidence":
            normalized[column_name] = _coerce_confidence(value)
        elif column_name == "SatisfiedByRecordID":
            normalized[column_name] = None if value in (None, "") else str(value)
        elif column_name in {
            "ObligationType",
            "Title",
            "Description",
            "Severity",
            "Priority",
            "OwnerRole",
            "OwnerUserID",
            "EscalationPolicyCode",
            "ResolutionNotes",
            "Notes",
            "SatisfiedByRecordType",
            "ResolutionType",
            "SuggestedResolution",
            "SourceEntityType",
            "SourceEntityID",
            "SourceEventID",
            "SourceMessageID",
            "SourceAutomationRunID",
            "SourceAutomationProposalID",
            "SourceAutomationQuestionID",
            "DueWindow",
            "CreatedBy",
            "ResolvedBy",
            "IdempotencyKey",
        }:
            normalized[column_name] = None if value in (None, "") else str(value)
        elif column_name in {"RequiresHumanReview", "RequiresApproval", "CanAutoResolve"}:
            normalized[column_name] = bool(value)
        else:
            normalized[column_name] = value
    return normalized


def _list_obligation_rows(
    *,
    where_clauses: list[str],
    params: list[Any],
    limit: int,
    order_sql: str,
) -> list[WorkflowObligationRecord]:
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT *
            FROM public."WorkflowObligation"
            {where_sql}
            ORDER BY {order_sql}
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_obligation_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _insert_obligation_event_row(
    cur,
    *,
    obligation_id: int,
    event_type: str,
    event_message: str,
    event_json: Any,
    created_by: str | None,
):
    cur.execute(
        """
        INSERT INTO public."WorkflowObligationEvent" (
            "ObligationID",
            "EventType",
            "EventMessage",
            "EventJson",
            "CreatedBy"
        )
        VALUES (%s, %s, %s, %s::jsonb, %s)
        RETURNING *
        """,
        (
            int(obligation_id),
            str(event_type).strip(),
            str(event_message).strip(),
            _serialize_json(event_json),
            created_by,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("WorkflowObligationEvent insert returned no row.")
    return row


def _insert_change_log(
    cur,
    *,
    obligation_id: int,
    changed_by: str | None,
    change_source: str | None,
    old_json: Any,
    new_json: Any,
    reason: str | None,
) -> None:
    cur.execute(
        """
        INSERT INTO public."WorkflowObligationChangeLog" (
            "ObligationID",
            "ChangedBy",
            "ChangeSource",
            "OldJson",
            "NewJson",
            "Reason"
        )
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
        """,
        (
            int(obligation_id),
            changed_by,
            change_source,
            _serialize_json(old_json),
            _serialize_json(new_json),
            reason,
        ),
    )


def _require_obligation(obligation_id: int) -> WorkflowObligationRecord:
    obligation = get_workflow_obligation(int(obligation_id))
    if obligation is None:
        raise ValueError(f"WorkflowObligation #{obligation_id} was not found.")
    return obligation


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    status_aliases = {
        "active": STATUS_WAITING,
        "detected": FOUNDATION_STATUS_DETECTED,
        "proposed": FOUNDATION_STATUS_PROPOSED,
        "accepted": FOUNDATION_STATUS_ACCEPTED,
        "scheduled": FOUNDATION_STATUS_SCHEDULED,
        "in_progress": FOUNDATION_STATUS_IN_PROGRESS,
        "in progress": FOUNDATION_STATUS_IN_PROGRESS,
        "waiting_on_external": FOUNDATION_STATUS_WAITING_ON_EXTERNAL,
        "waiting on external": FOUNDATION_STATUS_WAITING_ON_EXTERNAL,
        "needs_human": FOUNDATION_STATUS_NEEDS_HUMAN,
        "needs human": FOUNDATION_STATUS_NEEDS_HUMAN,
        "completed": FOUNDATION_STATUS_COMPLETED,
        "escalated": FOUNDATION_STATUS_ESCALATED,
    }
    text = status_aliases.get(text, text)
    if text not in ALL_WORKFLOW_OBLIGATION_STATUSES:
        raise ValueError(f"Unsupported WorkflowObligation status: {value!r}")
    return text


def _normalize_obligation_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("obligation_type is required.")
    return text if text in FOUNDATION_OBLIGATION_TYPES else "UNKNOWN_NOVEL"


def _normalize_priority(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return FOUNDATION_PRIORITY_NORMAL
    if text not in FOUNDATION_PRIORITIES:
        raise ValueError(f"Unsupported WorkflowObligation priority: {value!r}")
    return text


def _normalize_resolution_status(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text not in FOUNDATION_RESOLUTION_STATUSES:
        raise ValueError(f"Unsupported WorkflowObligation resolution status: {value!r}")
    return text


def _priority_to_severity(priority: str) -> str:
    mapping = {
        FOUNDATION_PRIORITY_LOW: "Low",
        FOUNDATION_PRIORITY_NORMAL: "Medium",
        FOUNDATION_PRIORITY_HIGH: "High",
        FOUNDATION_PRIORITY_URGENT: "Critical",
    }
    return mapping.get(priority, "Medium")


def _derive_legacy_source_fields(
    *,
    source_entity_type: str | None,
    source_entity_id: str | int | None,
    source_automation_proposal_id: int | None,
    source_automation_question_id: int | None,
    source_automation_run_id: int | None,
    source_event_id: str | int | None,
    source_message_id: str | int | None,
) -> tuple[str, str]:
    if source_entity_type and source_entity_id not in (None, ""):
        return str(source_entity_type).strip(), str(source_entity_id)
    if source_automation_proposal_id is not None:
        return "AutomationProposal", str(source_automation_proposal_id)
    if source_automation_question_id is not None:
        return "AutomationQuestion", str(source_automation_question_id)
    if source_event_id not in (None, ""):
        return "AutomationEvent", str(source_event_id)
    if source_message_id not in (None, ""):
        return "InboundMessage", str(source_message_id)
    if source_automation_run_id is not None:
        return "AutomationRun", str(source_automation_run_id)
    return "WorkflowObligationFoundation", "0"


def _combine_due_datetime(due_date: date | None, due_time: time | None) -> datetime | None:
    if due_date is None:
        return None
    return datetime.combine(due_date, due_time or time.min, tzinfo=timezone.utc)


def _coerce_date_only(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        dt_value = _coerce_datetime(text)
        if dt_value is not None:
            return dt_value.date()
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def _coerce_time_only(value: Any) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.timetz().replace(tzinfo=None) if value.timetz() else value.time()
    if isinstance(value, time):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return time.fromisoformat(text.replace("Z", ""))
        except ValueError:
            return None
    return None


def _coerce_confidence(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid WorkflowObligation confidence: {value!r}") from None
    if numeric < 0 or numeric > 1:
        raise ValueError("WorkflowObligation confidence must be between 0.0 and 1.0.")
    return numeric


def _get_automation_event_record(automation_event_id: int):
    from neon_ai.services.automation_control_service import AutomationEventRecord

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationEvent"
            WHERE "AutomationEventID" = %s
            LIMIT 1
            """,
            (int(automation_event_id),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return AutomationEventRecord(
            automation_event_id=int(row["AutomationEventID"]),
            automation_run_id=int(row["AutomationRunID"]) if row.get("AutomationRunID") not in (None, "", 0, "0") else None,
            automation_key=str(row.get("AutomationKey") or ""),
            event_type=str(row.get("EventType") or ""),
            summary=str(row.get("Summary") or ""),
            target_type=row.get("TargetType"),
            target_id=str(row.get("TargetID")) if row.get("TargetID") not in (None, "") else None,
            event_json=_deserialize_json(row.get("EventJson")),
            created_at=_coerce_datetime(row.get("CreatedAt")),
        )
    finally:
        conn.close()


def _append_note_text(existing: str | None, new_entry: str, *, changed_by: str | None, change_source: str | None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    actor = str(changed_by or "Unknown")
    source = str(change_source or "unknown")
    block = f"[{timestamp}] ({source} / {actor}) {str(new_entry).strip()}"
    current = str(existing or "").strip()
    if not current:
        return block
    return f"{current}\n\n{block}"


def _record_json(record: WorkflowObligationRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return _json_safe(asdict(record))


def _change_log_from_row(row: dict[str, Any] | None) -> WorkflowObligationChangeLogRecord:
    if row is None:
        raise ValueError("WorkflowObligationChangeLog row is required.")
    return WorkflowObligationChangeLogRecord(
        change_id=int(row["ChangeID"]),
        obligation_id=int(row["ObligationID"]),
        changed_at=_coerce_datetime(row.get("ChangedAt")),
        changed_by=row.get("ChangedBy"),
        change_source=row.get("ChangeSource"),
        old_json=_deserialize_json(row.get("OldJson")),
        new_json=_deserialize_json(row.get("NewJson")),
        reason=row.get("Reason"),
    )


def _event_from_row(row: dict[str, Any] | None) -> WorkflowObligationEventRecord:
    if row is None:
        raise ValueError("WorkflowObligationEvent row is required.")
    return WorkflowObligationEventRecord(
        workflow_obligation_event_id=int(row["WorkflowObligationEventID"]),
        obligation_id=int(row["ObligationID"]),
        event_type=str(row.get("EventType") or ""),
        event_message=str(row.get("EventMessage") or ""),
        event_json=_deserialize_json(row.get("EventJson")),
        created_by=row.get("CreatedBy"),
        created_at=_coerce_datetime(row.get("CreatedAt")),
    )


def _link_from_row(row: dict[str, Any] | None) -> WorkflowObligationLinkRecord:
    if row is None:
        raise ValueError("WorkflowObligationLink row is required.")
    return WorkflowObligationLinkRecord(
        workflow_obligation_link_id=int(row["WorkflowObligationLinkID"]),
        obligation_id=int(row["ObligationID"]),
        linked_entity_type=str(row.get("LinkedEntityType") or ""),
        linked_entity_id=str(row.get("LinkedEntityID") or ""),
        link_role=row.get("LinkRole"),
        created_at=_coerce_datetime(row.get("CreatedAt")),
    )


def _resolution_from_row(row: dict[str, Any] | None) -> WorkflowObligationResolutionRecord:
    if row is None:
        raise ValueError("WorkflowObligationResolution row is required.")
    return WorkflowObligationResolutionRecord(
        workflow_obligation_resolution_id=int(row["WorkflowObligationResolutionID"]),
        obligation_id=int(row["ObligationID"]),
        resolution_type=str(row.get("ResolutionType") or ""),
        resolution_status=str(row.get("ResolutionStatus") or ""),
        resolution_json=_deserialize_json(row.get("ResolutionJson")),
        resolved_by=row.get("ResolvedBy"),
        resolved_at=_coerce_datetime(row.get("ResolvedAt")),
        notes=row.get("Notes"),
    )


def _obligation_from_row(row: dict[str, Any] | None) -> WorkflowObligationRecord:
    if row is None:
        raise ValueError("WorkflowObligation row is required.")
    return WorkflowObligationRecord(
        obligation_id=int(row["ObligationID"]),
        obligation_type=row.get("ObligationType"),
        source_record_type=str(row.get("SourceRecordType") or ""),
        source_record_id=str(row.get("SourceRecordID") or ""),
        source_entity_type=row.get("SourceEntityType") or row.get("SourceRecordType"),
        source_entity_id=str(row.get("SourceEntityID") or row.get("SourceRecordID") or ""),
        source_event_id=str(row.get("SourceEventID")) if row.get("SourceEventID") not in (None, "") else None,
        source_message_id=str(row.get("SourceMessageID")) if row.get("SourceMessageID") not in (None, "") else None,
        source_automation_run_id=int(row["SourceAutomationRunID"]) if row.get("SourceAutomationRunID") not in (None, "", 0, "0") else None,
        source_automation_proposal_id=int(row["SourceAutomationProposalID"]) if row.get("SourceAutomationProposalID") not in (None, "", 0, "0") else None,
        source_automation_question_id=int(row["SourceAutomationQuestionID"]) if row.get("SourceAutomationQuestionID") not in (None, "", 0, "0") else None,
        workflow_type=str(row.get("WorkflowType") or ""),
        expected_event_type=str(row.get("ExpectedEventType") or ""),
        expected_by=_coerce_datetime(row.get("ExpectedBy")),
        due_date=_coerce_date_only(row.get("DueDate")),
        due_time=_coerce_time_only(row.get("DueTime")),
        due_window=row.get("DueWindow"),
        severity=str(row.get("Severity") or "Medium"),
        priority=str(row.get("Priority") or FOUNDATION_PRIORITY_NORMAL),
        status=_normalize_status(row.get("Status") or STATUS_WAITING),
        confidence=float(row.get("Confidence")) if row.get("Confidence") not in (None, "") else None,
        requires_human_review=bool(row.get("RequiresHumanReview")) if row.get("RequiresHumanReview") is not None else True,
        requires_approval=bool(row.get("RequiresApproval")) if row.get("RequiresApproval") is not None else False,
        can_auto_resolve=bool(row.get("CanAutoResolve")) if row.get("CanAutoResolve") is not None else False,
        owner_role=row.get("OwnerRole"),
        owner_user_id=row.get("OwnerUserID"),
        created_at=_coerce_datetime(row.get("CreatedAt")),
        updated_at=_coerce_datetime(row.get("UpdatedAt")),
        created_by=row.get("CreatedBy"),
        satisfied_at=_coerce_datetime(row.get("SatisfiedAt")),
        satisfied_by_record_type=row.get("SatisfiedByRecordType"),
        satisfied_by_record_id=row.get("SatisfiedByRecordID"),
        resolved_at=_coerce_datetime(row.get("ResolvedAt")),
        resolved_by=row.get("ResolvedBy"),
        last_checked_at=_coerce_datetime(row.get("LastCheckedAt")),
        last_proposal_id=int(row["LastProposalID"]) if row.get("LastProposalID") not in (None, "", 0, "0") else None,
        escalation_level=int(row.get("EscalationLevel") or 0),
        escalation_policy_code=row.get("EscalationPolicyCode"),
        title=row.get("Title"),
        description=row.get("Description"),
        evidence_json=_deserialize_json(row.get("EvidenceJson")),
        resolution_type=row.get("ResolutionType"),
        resolution_notes=row.get("ResolutionNotes"),
        suggested_resolution=row.get("SuggestedResolution"),
        notes=row.get("Notes"),
        snooze_until=_coerce_datetime(row.get("SnoozeUntil")),
        idempotency_key=row.get("IdempotencyKey"),
    )


def _normalize_operator_view_name(view_name: str) -> str:
    normalized = str(view_name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"", "operator_view"}:
        return WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL
    return normalized if normalized in WORKFLOW_OBLIGATION_OPERATOR_VIEW_LABELS else WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL


def _operator_view_sort_key(obligation: WorkflowObligationRecord, now: datetime) -> tuple[float, int, int]:
    effective_due = _workflow_obligation_effective_due(obligation)
    due_rank = _timestamp_rank(effective_due, default_high=True)
    priority_rank = -_workflow_obligation_priority_rank(obligation)
    return (due_rank, priority_rank, -int(obligation.obligation_id))


def _workflow_obligation_matches_operator_view(
    obligation: WorkflowObligationRecord,
    view_name: str,
    now: datetime,
) -> bool:
    normalized_view = _normalize_operator_view_name(view_name)
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_ALL:
        return True

    is_active = _workflow_obligation_is_active(obligation)
    effective_due = _workflow_obligation_effective_due(obligation)
    due_date_only = effective_due.date() if effective_due is not None else None
    today = now.date()

    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_TODAY:
        return is_active and due_date_only == today
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_OVERDUE:
        return is_active and effective_due is not None and effective_due < now and due_date_only != today
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_DUE_SOON:
        if not is_active or due_date_only is None:
            return False
        return today < due_date_only <= (today + timedelta(days=7))
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_NEEDS_HUMAN:
        return _workflow_obligation_needs_human(obligation, is_active=is_active)
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_CASH_FLOW:
        return is_active and _workflow_obligation_is_cash_flow(obligation)
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_RECEIVING:
        return is_active and _workflow_obligation_is_receiving(obligation)
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_PAYABLES:
        return is_active and _workflow_obligation_is_payables(obligation)
    if normalized_view == WORKFLOW_OBLIGATION_OPERATOR_VIEW_DOCUMENTS:
        return is_active and _workflow_obligation_is_documents(obligation)
    return True


def _workflow_obligation_is_active(obligation: WorkflowObligationRecord) -> bool:
    return _normalize_status(obligation.status) in ACTIVE_WORKFLOW_OBLIGATION_STATUSES


def _workflow_obligation_effective_due(obligation: WorkflowObligationRecord) -> datetime | None:
    return (
        _coerce_datetime(obligation.snooze_until)
        or _coerce_datetime(obligation.expected_by)
        or _combine_due_datetime(obligation.due_date, obligation.due_time)
    )


def _workflow_obligation_priority_rank(obligation: WorkflowObligationRecord) -> int:
    text = str(obligation.priority or obligation.severity or "").strip().upper()
    if text in {FOUNDATION_PRIORITY_URGENT, "CRITICAL"}:
        return 4
    if text in {FOUNDATION_PRIORITY_HIGH, "HIGH"}:
        return 3
    if text in {FOUNDATION_PRIORITY_NORMAL, "MEDIUM"}:
        return 2
    if text in {FOUNDATION_PRIORITY_LOW, "LOW"}:
        return 1
    return 0


def _workflow_obligation_needs_human(
    obligation: WorkflowObligationRecord,
    *,
    is_active: bool | None = None,
) -> bool:
    active = _workflow_obligation_is_active(obligation) if is_active is None else is_active
    return bool(
        obligation.requires_human_review
        or obligation.requires_approval
        or (obligation.source_automation_question_id is not None and active)
        or (active and _workflow_obligation_priority_rank(obligation) >= 3)
    )


def _workflow_obligation_is_cash_flow(obligation: WorkflowObligationRecord) -> bool:
    obligation_type = str(obligation.obligation_type or "").strip().upper()
    workflow_type = str(obligation.workflow_type or "").strip().lower()
    expected_event_type = str(obligation.expected_event_type or "").strip().lower()
    return bool(
        obligation_type in {"CASH_FLOW_REVIEW", "BILLING_REVIEW", "PAYABLE_REVIEW", "PAYMENT_DUE"}
        or workflow_type in {"cash_flow_review", "vendor_invoice_payables", "customer_invoice_ar"}
        or expected_event_type
        in {
            "customer_billing_reviewed_for_vendor_invoice",
            "customer_invoice_creation_due",
            "customer_invoice_payment_or_followup",
            "vendor_invoice_reconciled_to_po_receipt",
        }
    )


def _workflow_obligation_is_receiving(obligation: WorkflowObligationRecord) -> bool:
    obligation_type = str(obligation.obligation_type or "").strip().upper()
    workflow_type = str(obligation.workflow_type or "").strip().lower()
    expected_event_type = str(obligation.expected_event_type or "").strip().lower()
    return bool(
        obligation_type == "RECEIVING_REQUIRED"
        or workflow_type == "receive_goods"
        or expected_event_type == "staged_receipt_reviewed_or_applied"
    )


def _workflow_obligation_is_payables(obligation: WorkflowObligationRecord) -> bool:
    obligation_type = str(obligation.obligation_type or "").strip().upper()
    workflow_type = str(obligation.workflow_type or "").strip().lower()
    expected_event_type = str(obligation.expected_event_type or "").strip().lower()
    return bool(
        obligation_type in {"PAYABLE_REVIEW", "PAYABLE_EXCEPTION"}
        or workflow_type == "vendor_invoice_payables"
        or expected_event_type
        in {
            "vendor_invoice_reconciled_to_po_receipt",
            "vendor_invoice_duplicate_or_accounting_review_completed",
            "non_po_vendor_invoice_exception_resolved",
        }
    )


def _workflow_obligation_is_documents(obligation: WorkflowObligationRecord) -> bool:
    obligation_type = str(obligation.obligation_type or "").strip().upper()
    workflow_type = str(obligation.workflow_type or "").strip().lower()
    expected_event_type = str(obligation.expected_event_type or "").strip().lower()
    evidence = obligation.evidence_json if isinstance(obligation.evidence_json, dict) else {}
    question_type = str(evidence.get("question_type") or "").strip().lower()
    return bool(
        obligation_type in {"DOCUMENT_REVIEW", "DOCUMENT_REQUIRED"}
        or workflow_type == "attachment_intake"
        or expected_event_type == "attachment_review_completed"
        or question_type
        in {
            "attachment_review_required",
            "attachment_text_extraction_required",
            "attachment_needs_ocr",
            "unsupported_attachment_review",
            "missing_attachment_file_review",
        }
    )


def _workflow_obligation_owner_summary(obligation: WorkflowObligationRecord) -> str:
    role = str(obligation.owner_role or "").strip()
    owner = str(obligation.owner_user_id or "").strip()
    if role and owner:
        return f"{role} / {owner}"
    if role:
        return f"Role: {role}"
    if owner:
        return f"Owner: {owner}"
    return "Unassigned"


def _workflow_obligation_due_summary(obligation: WorkflowObligationRecord, now: datetime) -> str:
    effective_due = _workflow_obligation_effective_due(obligation)
    if effective_due is None:
        return "No due date set"
    due_label = effective_due.strftime("%Y-%m-%d")
    delta_days = (effective_due.date() - now.date()).days
    prefix = "Snoozed until " if obligation.snooze_until is not None else "Due "
    if delta_days < 0:
        overdue_days = abs(delta_days)
        day_label = "day" if overdue_days == 1 else "days"
        return f"Overdue by {overdue_days} {day_label} (was due {due_label})"
    if delta_days == 0:
        return f"{prefix}today ({due_label})"
    if delta_days <= 7:
        day_label = "day" if delta_days == 1 else "days"
        return f"{prefix}in {delta_days} {day_label} ({due_label})"
    return f"{prefix}{due_label}"


def _workflow_obligation_source_summary(
    obligation: WorkflowObligationRecord,
    links: list[WorkflowObligationLinkRecord],
) -> str:
    evidence = obligation.evidence_json if isinstance(obligation.evidence_json, dict) else {}
    parts: list[str] = []
    if obligation.source_automation_proposal_id is not None:
        action_type = str(evidence.get("source_action_type") or evidence.get("action_type") or "").strip()
        parts.append(
            f"Proposal #{obligation.source_automation_proposal_id}"
            + (f" ({action_type})" if action_type else "")
        )
    if obligation.source_automation_question_id is not None:
        question_type = str(evidence.get("question_type") or evidence.get("source_question_type") or "").strip()
        parts.append(
            f"Question #{obligation.source_automation_question_id}"
            + (f" ({question_type})" if question_type else "")
        )
    source_message_id = evidence.get("source_inbound_message_id") or evidence.get("inbound_message_id") or obligation.source_message_id
    if source_message_id not in (None, ""):
        parts.append(f"InboundMessage #{source_message_id}")

    linked_bits: list[str] = []
    preferred_order = (
        "VendorInvoice",
        "PurchaseOrder",
        "PurchaseOrderReceipt",
        "WorkOrder",
        "Estimate",
        "Invoice",
        "Customer",
        "Site",
        "Vendor",
        "InboundMessage",
    )
    grouped: dict[str, list[str]] = {}
    for link in links:
        grouped.setdefault(str(link.linked_entity_type or ""), []).append(str(link.linked_entity_id or ""))
    for entity_type in preferred_order:
        ids = grouped.get(entity_type) or []
        if ids:
            linked_bits.append(f"{entity_type} #{', '.join(ids[:3])}")
    for entity_type, ids in grouped.items():
        if entity_type in preferred_order or not ids:
            continue
        linked_bits.append(f"{entity_type} #{', '.join(ids[:3])}")
    if linked_bits:
        parts.append("Linked: " + " | ".join(linked_bits))

    if not parts:
        source_record_type = obligation.source_record_type or obligation.source_entity_type or "-"
        source_record_id = obligation.source_record_id or obligation.source_entity_id or "-"
        return f"{source_record_type} / {source_record_id}"
    return " | ".join(parts)


def _workflow_obligation_recent_history_summary(
    events: list[WorkflowObligationEventRecord],
    *,
    limit: int = 5,
) -> list[str]:
    if not events:
        return []
    ordered = sorted(
        events,
        key=lambda item: _timestamp_rank(_coerce_datetime(item.created_at), default_high=False),
        reverse=True,
    )
    lines: list[str] = []
    for event in ordered[: max(1, int(limit or 5))]:
        created = _coerce_datetime(event.created_at)
        stamp = created.strftime("%Y-%m-%d %H:%M") if created is not None else "Unknown time"
        lines.append(f"{stamp} | {event.event_type} | {event.event_message}")
    return lines


def _timestamp_rank(value: datetime | None, *, default_high: bool = False) -> float:
    if value is None:
        return float("inf") if default_high else 0.0
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.timestamp()


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
        normalized = text.replace("Z", "+00:00")
        for parser in (datetime.fromisoformat,):
            try:
                parsed = parser(normalized)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _serialize_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_json_safe(value), sort_keys=True)


def _deserialize_json(value: Any) -> dict[str, Any] | list[Any] | str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value
