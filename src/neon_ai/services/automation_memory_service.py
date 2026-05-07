from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import json
from typing import Any

from neon_ai.database.connection import get_connection


_AUTOMATION_MEMORY_SCHEMA_READY = False


@dataclass(frozen=True)
class AutomationMemoryRecord:
    automation_memory_id: int
    scope_type: str
    scope_id: str | None
    memory_type: str
    content_json: dict[str, Any] | list[Any] | str | None
    content_text: str | None
    source: str | None
    confidence: float | None
    created_by: str | None
    created_at: datetime | None
    expires_at: datetime | None
    is_active: bool


@dataclass(frozen=True)
class AutomationMemorySummary:
    total_count: int
    active_count: int
    inactive_count: int
    expiring_count: int


def ensure_automation_memory_schema(*, force: bool = False) -> None:
    global _AUTOMATION_MEMORY_SCHEMA_READY
    if _AUTOMATION_MEMORY_SCHEMA_READY and not force:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."AutomationMemory" (
                "AutomationMemoryID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "ScopeType" text NOT NULL,
                "ScopeID" text,
                "MemoryType" text NOT NULL,
                "ContentJson" jsonb,
                "ContentText" text,
                "Source" text,
                "Confidence" numeric(5,4),
                "CreatedBy" text,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "ExpiresAt" timestamptz,
                "IsActive" boolean NOT NULL DEFAULT true
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationMemory_Scope"
            ON public."AutomationMemory" ("ScopeType", "ScopeID", "IsActive", "CreatedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationMemory_Type"
            ON public."AutomationMemory" ("MemoryType", "IsActive", "CreatedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_AutomationMemory_Source"
            ON public."AutomationMemory" ("Source", "CreatedAt" DESC)
            """
        )
        conn.commit()
        _AUTOMATION_MEMORY_SCHEMA_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_memory(
    *,
    scope_type: str,
    scope_id: str | int | None = None,
    memory_type: str,
    content_json: Any = None,
    content_text: str | None = None,
    source: str | None = None,
    confidence: float | Decimal | int | str | None = None,
    created_by: str | None = None,
    expires_at: datetime | None = None,
    is_active: bool = True,
) -> AutomationMemoryRecord:
    ensure_automation_memory_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."AutomationMemory" (
                "ScopeType",
                "ScopeID",
                "MemoryType",
                "ContentJson",
                "ContentText",
                "Source",
                "Confidence",
                "CreatedBy",
                "ExpiresAt",
                "IsActive"
            )
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(scope_type or "").strip(),
                None if scope_id is None else str(scope_id),
                str(memory_type or "").strip(),
                _serialize_json(content_json),
                content_text,
                source,
                _coerce_decimal_or_none(confidence),
                created_by,
                expires_at,
                bool(is_active),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _memory_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_memory(
    *,
    limit: int = 200,
    active_only: bool = False,
    scope_type: str | None = None,
    memory_type: str | None = None,
) -> list[AutomationMemoryRecord]:
    ensure_automation_memory_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        clauses = []
        params: list[Any] = []
        if active_only:
            clauses.append('"IsActive" = TRUE')
        if scope_type:
            clauses.append('"ScopeType" = %s')
            params.append(str(scope_type).strip())
        if memory_type:
            clauses.append('"MemoryType" = %s')
            params.append(str(memory_type).strip())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(
            f'''
            SELECT *
            FROM public."AutomationMemory"
            {where_sql}
            ORDER BY "CreatedAt" DESC, "AutomationMemoryID" DESC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_memory_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_memory_for_scope(
    scope_type: str,
    scope_id: str | int | None,
    *,
    active_only: bool = False,
    limit: int = 200,
) -> list[AutomationMemoryRecord]:
    ensure_automation_memory_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if scope_id is None:
            scope_clause = '"ScopeID" IS NULL'
            params: list[Any] = [str(scope_type).strip()]
        else:
            scope_clause = '"ScopeID" = %s'
            params = [str(scope_type).strip(), str(scope_id)]
        active_clause = 'AND "IsActive" = TRUE' if active_only else ""
        if scope_id is None:
            cur.execute(
                f'''
                SELECT *
                FROM public."AutomationMemory"
                WHERE "ScopeType" = %s
                  AND {scope_clause}
                  {active_clause}
                ORDER BY "CreatedAt" DESC, "AutomationMemoryID" DESC
                LIMIT %s
                ''',
                (*params, int(limit)),
            )
        else:
            cur.execute(
                f'''
                SELECT *
                FROM public."AutomationMemory"
                WHERE "ScopeType" = %s
                  AND {scope_clause}
                  {active_clause}
                ORDER BY "CreatedAt" DESC, "AutomationMemoryID" DESC
                LIMIT %s
                ''',
                (*params, int(limit)),
            )
        return [_memory_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_memory(memory_id: int) -> AutomationMemoryRecord | None:
    ensure_automation_memory_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."AutomationMemory"
            WHERE "AutomationMemoryID" = %s
            LIMIT 1
            """,
            (int(memory_id),),
        )
        row = cur.fetchone()
        return _memory_from_row(row) if row else None
    finally:
        conn.close()


def deactivate_memory(memory_id: int) -> AutomationMemoryRecord | None:
    ensure_automation_memory_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."AutomationMemory"
            SET "IsActive" = FALSE
            WHERE "AutomationMemoryID" = %s
            RETURNING *
            """,
            (int(memory_id),),
        )
        row = cur.fetchone()
        conn.commit()
        return _memory_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def search_memory_text(query: str, *, limit: int = 100, active_only: bool = False) -> list[AutomationMemoryRecord]:
    ensure_automation_memory_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        search_value = f"%{str(query or '').strip()}%"
        active_clause = 'AND "IsActive" = TRUE' if active_only else ""
        cur.execute(
            f'''
            SELECT *
            FROM public."AutomationMemory"
            WHERE (
                COALESCE("ContentText", '') ILIKE %s
                OR COALESCE("ContentJson"::text, '') ILIKE %s
                OR COALESCE("Source", '') ILIKE %s
            )
            {active_clause}
            ORDER BY "CreatedAt" DESC, "AutomationMemoryID" DESC
            LIMIT %s
            ''',
            (search_value, search_value, search_value, int(limit)),
        )
        return [_memory_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_active_memory_summary(
    *,
    scope_type: str | None = None,
    scope_id: str | int | None = None,
) -> AutomationMemorySummary:
    ensure_automation_memory_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        clauses = []
        params: list[Any] = []
        if scope_type:
            clauses.append('"ScopeType" = %s')
            params.append(str(scope_type).strip())
        if scope_id is None:
            if scope_type is not None:
                clauses.append('"ScopeID" IS NULL')
        else:
            clauses.append('"ScopeID" = %s')
            params.append(str(scope_id))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(
            f'''
            SELECT *
            FROM public."AutomationMemory"
            {where_sql}
            ''',
            tuple(params),
        )
        rows = cur.fetchall()
        total_count = len(rows)
        active_count = 0
        inactive_count = 0
        expiring_count = 0
        now = datetime.now().astimezone()
        for row in rows:
            is_active = bool(row.get("IsActive"))
            if is_active:
                active_count += 1
            else:
                inactive_count += 1
            expires_at = row.get("ExpiresAt")
            if is_active and expires_at is not None and expires_at <= now:
                expiring_count += 1
        return AutomationMemorySummary(
            total_count=total_count,
            active_count=active_count,
            inactive_count=inactive_count,
            expiring_count=expiring_count,
        )
    finally:
        conn.close()


def _memory_from_row(row: dict[str, Any]) -> AutomationMemoryRecord:
    return AutomationMemoryRecord(
        automation_memory_id=int(row.get("AutomationMemoryID") or 0),
        scope_type=str(row.get("ScopeType") or ""),
        scope_id=None if row.get("ScopeID") is None else str(row.get("ScopeID")),
        memory_type=str(row.get("MemoryType") or ""),
        content_json=row.get("ContentJson"),
        content_text=row.get("ContentText"),
        source=row.get("Source"),
        confidence=float(row.get("Confidence")) if row.get("Confidence") is not None else None,
        created_by=row.get("CreatedBy"),
        created_at=row.get("CreatedAt"),
        expires_at=row.get("ExpiresAt"),
        is_active=bool(row.get("IsActive")),
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
