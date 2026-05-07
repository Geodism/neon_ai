from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from typing import Any

from neon_ai.database.connection import get_connection


_AUTOMATION_CONTROL_SCHEMA_READY = False


@dataclass(frozen=True)
class AutomationSettingRecord:
    automation_key: str
    display_name: str
    workflow: str
    enabled: bool
    safe_mode: bool
    notify_only: bool
    draft_only: bool
    approval_required: bool
    frequency_minutes: int | None
    max_tokens_per_run: int | None
    max_actions_per_run: int | None
    last_run_at: datetime | None
    next_run_at: datetime | None
    updated_at: datetime | None
    notes: str | None


@dataclass(frozen=True)
class AutomationRunRecord:
    automation_run_id: int
    automation_key: str
    started_at: datetime | None
    finished_at: datetime | None
    status: str
    trigger_type: str | None
    model_provider: str | None
    model_name: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    actions_created: int
    proposals_created: int
    questions_created: int
    error_message: str | None


@dataclass(frozen=True)
class AutomationEventRecord:
    automation_event_id: int
    automation_run_id: int | None
    automation_key: str
    event_type: str
    summary: str
    target_type: str | None
    target_id: str | None
    event_json: dict[str, Any] | list[Any] | str | None
    created_at: datetime | None


@dataclass(frozen=True)
class Automation24hSummary:
    runs_in_last_24h: int
    successful_runs: int
    failed_runs: int
    proposals_created: int
    questions_created: int
    actions_created: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float


def ensure_automation_control_schema(*, force: bool = False) -> None:
    global _AUTOMATION_CONTROL_SCHEMA_READY
    if _AUTOMATION_CONTROL_SCHEMA_READY and not force:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."AutomationSetting" (
                "AutomationKey" text PRIMARY KEY,
                "DisplayName" text,
                "Workflow" text,
                "Enabled" boolean NOT NULL DEFAULT false,
                "SafeMode" boolean NOT NULL DEFAULT true,
                "NotifyOnly" boolean NOT NULL DEFAULT true,
                "DraftOnly" boolean NOT NULL DEFAULT true,
                "ApprovalRequired" boolean NOT NULL DEFAULT true,
                "FrequencyMinutes" integer,
                "MaxTokensPerRun" integer,
                "MaxActionsPerRun" integer,
                "LastRunAt" timestamptz,
                "NextRunAt" timestamptz,
                "UpdatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "Notes" text
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."AutomationRun" (
                "AutomationRunID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "AutomationKey" text NOT NULL,
                "StartedAt" timestamptz NOT NULL DEFAULT NOW(),
                "FinishedAt" timestamptz,
                "Status" text NOT NULL DEFAULT 'Started',
                "TriggerType" text,
                "ModelProvider" text,
                "ModelName" text,
                "InputTokens" integer NOT NULL DEFAULT 0,
                "OutputTokens" integer NOT NULL DEFAULT 0,
                "EstimatedCost" numeric(12,4) NOT NULL DEFAULT 0,
                "ActionsCreated" integer NOT NULL DEFAULT 0,
                "ProposalsCreated" integer NOT NULL DEFAULT 0,
                "QuestionsCreated" integer NOT NULL DEFAULT 0,
                "ErrorMessage" text
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationRun_Key_StartedAt"
            ON public."AutomationRun" ("AutomationKey", "StartedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."AutomationEvent" (
                "AutomationEventID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "AutomationRunID" integer REFERENCES public."AutomationRun"("AutomationRunID") ON DELETE CASCADE,
                "AutomationKey" text NOT NULL,
                "EventType" text NOT NULL,
                "Summary" text NOT NULL,
                "TargetType" text,
                "TargetID" text,
                "EventJson" jsonb,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationEvent_RunID"
            ON public."AutomationEvent" ("AutomationRunID", "CreatedAt")
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationEvent_Key_CreatedAt"
            ON public."AutomationEvent" ("AutomationKey", "CreatedAt" DESC)
            """
        )
        conn.commit()
        _AUTOMATION_CONTROL_SCHEMA_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_automation_settings() -> list[AutomationSettingRecord]:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationSetting"
            ORDER BY "DisplayName" NULLS LAST, "AutomationKey"
            """
        )
        return [_setting_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_automation_setting(automation_key: str) -> AutomationSettingRecord | None:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationSetting"
            WHERE "AutomationKey" = %s
            LIMIT 1
            """,
            (str(automation_key or "").strip(),),
        )
        row = cur.fetchone()
        return _setting_from_row(row) if row else None
    finally:
        conn.close()


def upsert_automation_setting(
    automation_key: str,
    *,
    display_name: str,
    workflow: str,
    enabled: bool = False,
    safe_mode: bool = True,
    notify_only: bool = True,
    draft_only: bool = True,
    approval_required: bool = True,
    frequency_minutes: int | None = None,
    max_tokens_per_run: int | None = None,
    max_actions_per_run: int | None = None,
    last_run_at: datetime | None = None,
    next_run_at: datetime | None = None,
    notes: str | None = None,
) -> AutomationSettingRecord:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."AutomationSetting" (
                "AutomationKey",
                "DisplayName",
                "Workflow",
                "Enabled",
                "SafeMode",
                "NotifyOnly",
                "DraftOnly",
                "ApprovalRequired",
                "FrequencyMinutes",
                "MaxTokensPerRun",
                "MaxActionsPerRun",
                "LastRunAt",
                "NextRunAt",
                "UpdatedAt",
                "Notes"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            ON CONFLICT ("AutomationKey") DO UPDATE
            SET "DisplayName" = EXCLUDED."DisplayName",
                "Workflow" = EXCLUDED."Workflow",
                "Enabled" = EXCLUDED."Enabled",
                "SafeMode" = EXCLUDED."SafeMode",
                "NotifyOnly" = EXCLUDED."NotifyOnly",
                "DraftOnly" = EXCLUDED."DraftOnly",
                "ApprovalRequired" = EXCLUDED."ApprovalRequired",
                "FrequencyMinutes" = EXCLUDED."FrequencyMinutes",
                "MaxTokensPerRun" = EXCLUDED."MaxTokensPerRun",
                "MaxActionsPerRun" = EXCLUDED."MaxActionsPerRun",
                "LastRunAt" = EXCLUDED."LastRunAt",
                "NextRunAt" = EXCLUDED."NextRunAt",
                "UpdatedAt" = NOW(),
                "Notes" = EXCLUDED."Notes"
            RETURNING *
            """,
            (
                str(automation_key or "").strip(),
                str(display_name or "").strip(),
                str(workflow or "").strip(),
                bool(enabled),
                bool(safe_mode),
                bool(notify_only),
                bool(draft_only),
                bool(approval_required),
                frequency_minutes,
                max_tokens_per_run,
                max_actions_per_run,
                last_run_at,
                next_run_at,
                notes,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _setting_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_automation_run(
    automation_key: str,
    *,
    status: str = "Started",
    trigger_type: str | None = None,
    model_provider: str | None = None,
    model_name: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost: float | Decimal = 0,
    actions_created: int = 0,
    proposals_created: int = 0,
    questions_created: int = 0,
    error_message: str | None = None,
) -> AutomationRunRecord:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."AutomationRun" (
                "AutomationKey",
                "Status",
                "TriggerType",
                "ModelProvider",
                "ModelName",
                "InputTokens",
                "OutputTokens",
                "EstimatedCost",
                "ActionsCreated",
                "ProposalsCreated",
                "QuestionsCreated",
                "ErrorMessage"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(automation_key or "").strip(),
                str(status or "Started").strip() or "Started",
                trigger_type,
                model_provider,
                model_name,
                int(input_tokens or 0),
                int(output_tokens or 0),
                _coerce_decimal(estimated_cost),
                int(actions_created or 0),
                int(proposals_created or 0),
                int(questions_created or 0),
                error_message,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _run_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_automation_run(
    automation_run_id: int,
    *,
    status: str,
    finished_at: datetime | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost: float | Decimal | None = None,
    actions_created: int | None = None,
    proposals_created: int | None = None,
    questions_created: int | None = None,
    error_message: str | None = None,
) -> AutomationRunRecord | None:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."AutomationRun"
            SET "Status" = %s,
                "FinishedAt" = COALESCE(%s, NOW()),
                "InputTokens" = COALESCE(%s, "InputTokens"),
                "OutputTokens" = COALESCE(%s, "OutputTokens"),
                "EstimatedCost" = COALESCE(%s, "EstimatedCost"),
                "ActionsCreated" = COALESCE(%s, "ActionsCreated"),
                "ProposalsCreated" = COALESCE(%s, "ProposalsCreated"),
                "QuestionsCreated" = COALESCE(%s, "QuestionsCreated"),
                "ErrorMessage" = %s
            WHERE "AutomationRunID" = %s
            RETURNING *
            """,
            (
                str(status or "Finished").strip() or "Finished",
                finished_at,
                input_tokens,
                output_tokens,
                _coerce_decimal(estimated_cost) if estimated_cost is not None else None,
                actions_created,
                proposals_created,
                questions_created,
                error_message,
                int(automation_run_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _run_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_automation_event(
    *,
    automation_run_id: int | None,
    automation_key: str,
    event_type: str,
    summary: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    event_json: Any = None,
) -> AutomationEventRecord:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."AutomationEvent" (
                "AutomationRunID",
                "AutomationKey",
                "EventType",
                "Summary",
                "TargetType",
                "TargetID",
                "EventJson"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING *
            """,
            (
                int(automation_run_id) if automation_run_id is not None else None,
                str(automation_key or "").strip(),
                str(event_type or "").strip(),
                str(summary or "").strip(),
                target_type,
                None if target_id is None else str(target_id),
                _serialize_event_json(event_json),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _event_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_recent_automation_runs(*, limit: int = 20, automation_key: str | None = None) -> list[AutomationRunRecord]:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if automation_key:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationRun"
                WHERE "AutomationKey" = %s
                ORDER BY "StartedAt" DESC, "AutomationRunID" DESC
                LIMIT %s
                """,
                (str(automation_key).strip(), int(limit)),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationRun"
                ORDER BY "StartedAt" DESC, "AutomationRunID" DESC
                LIMIT %s
                """,
                (int(limit),),
            )
        return [_run_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_runs_since(
    since: datetime,
    *,
    limit: int = 200,
    automation_key: str | None = None,
) -> list[AutomationRunRecord]:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if automation_key:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationRun"
                WHERE "StartedAt" >= %s
                  AND "AutomationKey" = %s
                ORDER BY "StartedAt" DESC, "AutomationRunID" DESC
                LIMIT %s
                """,
                (_coerce_datetime(since), str(automation_key).strip(), int(limit)),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationRun"
                WHERE "StartedAt" >= %s
                ORDER BY "StartedAt" DESC, "AutomationRunID" DESC
                LIMIT %s
                """,
                (_coerce_datetime(since), int(limit)),
            )
        return [_run_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_recent_events(*, limit: int = 200, automation_key: str | None = None) -> list[AutomationEventRecord]:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if automation_key:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationEvent"
                WHERE "AutomationKey" = %s
                ORDER BY "CreatedAt" DESC, "AutomationEventID" DESC
                LIMIT %s
                """,
                (str(automation_key).strip(), int(limit)),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationEvent"
                ORDER BY "CreatedAt" DESC, "AutomationEventID" DESC
                LIMIT %s
                """,
                (int(limit),),
            )
        return [_event_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_events_since(
    since: datetime,
    *,
    limit: int = 500,
    automation_key: str | None = None,
) -> list[AutomationEventRecord]:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if automation_key:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationEvent"
                WHERE "CreatedAt" >= %s
                  AND "AutomationKey" = %s
                ORDER BY "CreatedAt" DESC, "AutomationEventID" DESC
                LIMIT %s
                """,
                (_coerce_datetime(since), str(automation_key).strip(), int(limit)),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM public."AutomationEvent"
                WHERE "CreatedAt" >= %s
                ORDER BY "CreatedAt" DESC, "AutomationEventID" DESC
                LIMIT %s
                """,
                (_coerce_datetime(since), int(limit)),
            )
        return [_event_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_automation_events_for_run(automation_run_id: int) -> list[AutomationEventRecord]:
    ensure_automation_control_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationEvent"
            WHERE "AutomationRunID" = %s
            ORDER BY "CreatedAt" ASC, "AutomationEventID" ASC
            """,
            (int(automation_run_id),),
        )
        return [_event_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_automation_24h_summary(*, now: datetime | None = None) -> Automation24hSummary:
    ensure_automation_control_schema()
    effective_now = _coerce_datetime(now) if now is not None else datetime.now(timezone.utc)
    since = effective_now - timedelta(hours=24)
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationRun"
            WHERE "StartedAt" >= %s
            ORDER BY "StartedAt" DESC, "AutomationRunID" DESC
            """,
            (since,),
        )
        rows = cur.fetchall()
        successful = 0
        failed = 0
        proposals = 0
        questions = 0
        actions = 0
        input_tokens = 0
        output_tokens = 0
        estimated_cost = 0.0
        for row in rows:
            status = str(row.get("Status") or "").strip().lower()
            if status in {"completed", "success", "succeeded"}:
                successful += 1
            if status in {"failed", "error", "errored", "cancelled"}:
                failed += 1
            proposals += int(row.get("ProposalsCreated") or 0)
            questions += int(row.get("QuestionsCreated") or 0)
            actions += int(row.get("ActionsCreated") or 0)
            input_tokens += int(row.get("InputTokens") or 0)
            output_tokens += int(row.get("OutputTokens") or 0)
            estimated_cost += float(row.get("EstimatedCost") or 0)
        return Automation24hSummary(
            runs_in_last_24h=len(rows),
            successful_runs=successful,
            failed_runs=failed,
            proposals_created=proposals,
            questions_created=questions,
            actions_created=actions,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
        )
    finally:
        conn.close()


def _setting_from_row(row: dict[str, Any]) -> AutomationSettingRecord:
    return AutomationSettingRecord(
        automation_key=str(row.get("AutomationKey") or ""),
        display_name=str(row.get("DisplayName") or ""),
        workflow=str(row.get("Workflow") or ""),
        enabled=bool(row.get("Enabled")),
        safe_mode=bool(row.get("SafeMode")),
        notify_only=bool(row.get("NotifyOnly")),
        draft_only=bool(row.get("DraftOnly")),
        approval_required=bool(row.get("ApprovalRequired")),
        frequency_minutes=_coerce_int_or_none(row.get("FrequencyMinutes")),
        max_tokens_per_run=_coerce_int_or_none(row.get("MaxTokensPerRun")),
        max_actions_per_run=_coerce_int_or_none(row.get("MaxActionsPerRun")),
        last_run_at=row.get("LastRunAt"),
        next_run_at=row.get("NextRunAt"),
        updated_at=row.get("UpdatedAt"),
        notes=row.get("Notes"),
    )


def _run_from_row(row: dict[str, Any]) -> AutomationRunRecord:
    return AutomationRunRecord(
        automation_run_id=int(row.get("AutomationRunID") or 0),
        automation_key=str(row.get("AutomationKey") or ""),
        started_at=row.get("StartedAt"),
        finished_at=row.get("FinishedAt"),
        status=str(row.get("Status") or ""),
        trigger_type=row.get("TriggerType"),
        model_provider=row.get("ModelProvider"),
        model_name=row.get("ModelName"),
        input_tokens=int(row.get("InputTokens") or 0),
        output_tokens=int(row.get("OutputTokens") or 0),
        estimated_cost=float(row.get("EstimatedCost") or 0),
        actions_created=int(row.get("ActionsCreated") or 0),
        proposals_created=int(row.get("ProposalsCreated") or 0),
        questions_created=int(row.get("QuestionsCreated") or 0),
        error_message=row.get("ErrorMessage"),
    )


def _event_from_row(row: dict[str, Any]) -> AutomationEventRecord:
    return AutomationEventRecord(
        automation_event_id=int(row.get("AutomationEventID") or 0),
        automation_run_id=_coerce_int_or_none(row.get("AutomationRunID")),
        automation_key=str(row.get("AutomationKey") or ""),
        event_type=str(row.get("EventType") or ""),
        summary=str(row.get("Summary") or ""),
        target_type=row.get("TargetType"),
        target_id=None if row.get("TargetID") is None else str(row.get("TargetID")),
        event_json=row.get("EventJson"),
        created_at=row.get("CreatedAt"),
    )


def _coerce_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_decimal(value: float | Decimal | int | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _coerce_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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


def _serialize_event_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_json_safe(value), sort_keys=True)
