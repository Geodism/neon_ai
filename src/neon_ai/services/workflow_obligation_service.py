from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
import json
from typing import Any

from neon_ai.database.connection import get_connection


_WORKFLOW_OBLIGATION_SCHEMA_READY = False

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
)
TERMINAL_WORKFLOW_OBLIGATION_STATUSES = (
    STATUS_SATISFIED,
    STATUS_DISMISSED,
    STATUS_CANCELLED,
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
    source_record_type: str
    source_record_id: str
    workflow_type: str
    expected_event_type: str
    expected_by: datetime | None
    severity: str
    status: str
    owner_role: str | None
    owner_user_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    satisfied_at: datetime | None
    satisfied_by_record_type: str | None
    satisfied_by_record_id: str | None
    last_checked_at: datetime | None
    last_proposal_id: int | None
    escalation_level: int
    escalation_policy_code: str | None
    title: str | None
    description: str | None
    evidence_json: dict[str, Any] | list[Any] | str | None
    resolution_notes: str | None
    notes: str | None
    snooze_until: datetime | None


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
        conn.commit()
        _WORKFLOW_OBLIGATION_SCHEMA_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_obligation_if_missing(
    *,
    source_record_type: str,
    source_record_id: str | int,
    workflow_type: str,
    expected_event_type: str,
    expected_by: datetime | date | None,
    severity: str = "Medium",
    status: str = STATUS_WAITING,
    owner_role: str | None = None,
    owner_user_id: str | None = None,
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
                "SourceRecordType",
                "SourceRecordID",
                "WorkflowType",
                "ExpectedEventType",
                "ExpectedBy",
                "Severity",
                "Status",
                "OwnerRole",
                "OwnerUserID",
                "EscalationLevel",
                "EscalationPolicyCode",
                "Title",
                "Description",
                "EvidenceJson",
                "ResolutionNotes",
                "Notes",
                "SnoozeUntil"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT ("SourceRecordType", "SourceRecordID", "ExpectedEventType")
            WHERE "Status" IN ('waiting', 'overdue', 'proposal_created', 'snoozed', 'blocked', 'failed')
            DO NOTHING
            RETURNING *
            """,
            (
                str(source_record_type or "").strip(),
                str(source_record_id),
                str(workflow_type or "").strip(),
                str(expected_event_type or "").strip(),
                _coerce_datetime(expected_by),
                str(severity or "Medium").strip() or "Medium",
                _normalize_status(status or STATUS_WAITING),
                owner_role,
                owner_user_id,
                int(escalation_level or 0),
                escalation_policy_code,
                title,
                description,
                _serialize_json(evidence_json),
                resolution_notes,
                notes,
                _coerce_datetime(snooze_until),
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
        elif column_name in {"ExpectedBy", "SatisfiedAt", "LastCheckedAt", "SnoozeUntil"}:
            normalized[column_name] = _coerce_datetime(value)
        elif column_name == "LastProposalID":
            normalized[column_name] = None if value in (None, "") else int(value)
        elif column_name == "EscalationLevel":
            normalized[column_name] = int(value or 0)
        elif column_name == "EvidenceJson":
            normalized[column_name] = value
        elif column_name == "SatisfiedByRecordID":
            normalized[column_name] = None if value in (None, "") else str(value)
        elif column_name in {
            "Title",
            "Description",
            "Severity",
            "OwnerRole",
            "OwnerUserID",
            "EscalationPolicyCode",
            "ResolutionNotes",
            "Notes",
            "SatisfiedByRecordType",
        }:
            normalized[column_name] = None if value in (None, "") else str(value)
        else:
            normalized[column_name] = value
    return normalized


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
    if text == "active":
        text = STATUS_WAITING
    if text not in ALL_WORKFLOW_OBLIGATION_STATUSES:
        raise ValueError(f"Unsupported WorkflowObligation status: {value!r}")
    return text


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


def _obligation_from_row(row: dict[str, Any] | None) -> WorkflowObligationRecord:
    if row is None:
        raise ValueError("WorkflowObligation row is required.")
    return WorkflowObligationRecord(
        obligation_id=int(row["ObligationID"]),
        source_record_type=str(row.get("SourceRecordType") or ""),
        source_record_id=str(row.get("SourceRecordID") or ""),
        workflow_type=str(row.get("WorkflowType") or ""),
        expected_event_type=str(row.get("ExpectedEventType") or ""),
        expected_by=_coerce_datetime(row.get("ExpectedBy")),
        severity=str(row.get("Severity") or "Medium"),
        status=_normalize_status(row.get("Status") or STATUS_WAITING),
        owner_role=row.get("OwnerRole"),
        owner_user_id=row.get("OwnerUserID"),
        created_at=_coerce_datetime(row.get("CreatedAt")),
        updated_at=_coerce_datetime(row.get("UpdatedAt")),
        satisfied_at=_coerce_datetime(row.get("SatisfiedAt")),
        satisfied_by_record_type=row.get("SatisfiedByRecordType"),
        satisfied_by_record_id=row.get("SatisfiedByRecordID"),
        last_checked_at=_coerce_datetime(row.get("LastCheckedAt")),
        last_proposal_id=int(row["LastProposalID"]) if row.get("LastProposalID") not in (None, "", 0, "0") else None,
        escalation_level=int(row.get("EscalationLevel") or 0),
        escalation_policy_code=row.get("EscalationPolicyCode"),
        title=row.get("Title"),
        description=row.get("Description"),
        evidence_json=_deserialize_json(row.get("EvidenceJson")),
        resolution_notes=row.get("ResolutionNotes"),
        notes=row.get("Notes"),
        snooze_until=_coerce_datetime(row.get("SnoozeUntil")),
    )


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
    if isinstance(value, date):
        return value.isoformat()
    return value
