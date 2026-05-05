from __future__ import annotations

import datetime
import os
import time
from typing import Any

from psycopg2.extras import RealDictCursor

from neon_ai.database.connection import get_connection


def _perf_log(area: str, name: str, started_at: float, **fields: Any) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    extras: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            normalized = "true" if value else "false"
        else:
            normalized = str(value).replace(" ", "_")
        extras.append(f"{key}={normalized}")
    suffix = f" {' '.join(extras)}" if extras else ""
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}{suffix}")


LEGACY_LOOSE_RFQ_DEBUG_ENV = "NEON_ENABLE_LEGACY_LOOSE_RFQ_DEBUG"
_MATERIAL_CALL_SCHEMA_READY = False


def legacy_loose_rfq_debug_enabled() -> bool:
    value = str(os.getenv(LEGACY_LOOSE_RFQ_DEBUG_ENV, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _normalize_material_call_source_type(source_type: str) -> str:
    normalized = str(source_type or "").strip().lower().replace("_", "").replace(" ", "")
    if normalized == "estimate":
        return "Estimate"
    if normalized == "workorder":
        return "WorkOrder"
    raise ValueError("Material Call SourceType must be Estimate or Work Order.")


def _resolve_material_call_source_context(
    source_type: str,
    source_id: int,
    *,
    cur,
    require_eligible: bool,
) -> dict[str, Any]:
    from neon_ai.database.rfq import resolve_material_request_source_context

    if source_id in (None, "", 0, "0"):
        raise ValueError("Material Calls must be tied to a source Estimate or open Work Order.")

    normalized_source_type = _normalize_material_call_source_type(source_type)
    return resolve_material_request_source_context(
        normalized_source_type,
        int(source_id),
        cur=cur,
        require_eligible=require_eligible,
    )


def _tree_source_document_label(source_type: str | None, source_id: Any) -> str:
    normalized_source_type = str(source_type or "").strip()
    try:
        normalized_source_id = int(source_id or 0)
    except (TypeError, ValueError):
        normalized_source_id = 0
    if normalized_source_type == "WorkOrder":
        return f"Work Order #{normalized_source_id}" if normalized_source_id else "Work Order"
    return f"Estimate #{normalized_source_id}" if normalized_source_id else "Estimate"


def _tree_selector_label(source_type: str | None) -> str:
    return "Work Order #" if str(source_type or "").strip() == "WorkOrder" else "Estimate #"


def _verify_material_call_schema(cur) -> None:
    global _MATERIAL_CALL_SCHEMA_READY
    if _MATERIAL_CALL_SCHEMA_READY:
        return
    cur.execute(
        '''
        SELECT
            to_regclass(%s) AS "material_call_table",
            to_regclass(%s) AS "material_call_item_table",
            to_regclass(%s) AS "rfq_carried_selection_table",
            to_regclass(%s) AS "po_source_link_table"
        ''',
        (
            'public."MaterialCall"',
            'public."MaterialCallItem"',
            'public."RFQCarriedSelection"',
            'public."PurchaseOrderItemSourceLink"',
        ),
    )
    row = cur.fetchone()
    row_get = row.get if isinstance(row, dict) else None
    missing_objects = []
    for key, label in (
        ("material_call_table", "MaterialCall"),
        ("material_call_item_table", "MaterialCallItem"),
        ("rfq_carried_selection_table", "RFQCarriedSelection"),
        ("po_source_link_table", "PurchaseOrderItemSourceLink"),
    ):
        value = row_get(key) if row_get else None
        if row_get is None:
            raise RuntimeError("Unexpected schema verification cursor shape for Material Call verification.")
        if not value:
            missing_objects.append(label)
    if missing_objects:
        raise RuntimeError(
            "Material Call schema is missing required tables: "
            + ", ".join(missing_objects)
            + ". Run the Material Call migration/bootstrap before opening RFQ Center."
        )

    cur.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (
              (table_name = 'PriceRequest' AND column_name IN ('MaterialCallID'))
              OR (table_name = 'PriceRequestItem' AND column_name IN ('MaterialCallItemID'))
              OR (table_name = 'RFQCarriedSelection' AND column_name IN ('MaterialCallID', 'MaterialCallItemID'))
              OR (table_name = 'PurchaseOrderItemSourceLink' AND column_name IN ('MaterialCallID', 'MaterialCallItemID'))
          )
        """
    )
    found = {
        (
            str(result.get("table_name") if isinstance(result, dict) else result[0]),
            str(result.get("column_name") if isinstance(result, dict) else result[1]),
        )
        for result in cur.fetchall()
    }
    required = {
        ("PriceRequest", "MaterialCallID"),
        ("PriceRequestItem", "MaterialCallItemID"),
        ("RFQCarriedSelection", "MaterialCallID"),
        ("RFQCarriedSelection", "MaterialCallItemID"),
        ("PurchaseOrderItemSourceLink", "MaterialCallID"),
        ("PurchaseOrderItemSourceLink", "MaterialCallItemID"),
    }
    missing_columns = sorted(required - found)
    if missing_columns:
        formatted = ", ".join(f'{table}.{column}' for table, column in missing_columns)
        raise RuntimeError(
            "Material Call schema is missing required columns: "
            + formatted
            + ". Run the Material Call migration/bootstrap before opening RFQ Center."
        )
    _MATERIAL_CALL_SCHEMA_READY = True


def ensure_material_call_schema(cur=None, *, force: bool = False, verify_only: bool = False) -> None:
    global _MATERIAL_CALL_SCHEMA_READY
    if _MATERIAL_CALL_SCHEMA_READY and not force:
        return
    owns_connection = cur is None
    conn = None
    if owns_connection:
        conn = get_connection()
        cur = conn.cursor()

    try:
        from neon_ai.database.purchases import ensure_purchase_schema
        from neon_ai.database.rfq import ensure_rfq_bid_compare_schema

        if verify_only:
            _verify_material_call_schema(cur)
        else:
            ensure_rfq_bid_compare_schema(cur=cur, force=force)
            ensure_purchase_schema(cur=cur, force=force)

            cur.execute('CREATE SEQUENCE IF NOT EXISTS "MaterialCall_MaterialCallID_seq"')
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS public."MaterialCall" (
                    "MaterialCallID" integer NOT NULL DEFAULT nextval('"MaterialCall_MaterialCallID_seq"'::regclass),
                    "MaterialCallNumber" text NOT NULL,
                    "SourceType" text NOT NULL,
                    "SourceID" integer NOT NULL,
                    "EstimateID" integer,
                    "WorkOrderID" integer,
                    "Status" text DEFAULT 'Open',
                    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "Notes" text,
                    CONSTRAINT "MaterialCall_pkey" PRIMARY KEY ("MaterialCallID"),
                    CONSTRAINT "MaterialCall_MaterialCallNumber_key" UNIQUE ("MaterialCallNumber"),
                    CONSTRAINT "MaterialCall_EstimateID_fkey"
                        FOREIGN KEY ("EstimateID") REFERENCES public."Estimate"("EstimateID"),
                    CONSTRAINT "MaterialCall_WorkOrderID_fkey"
                        FOREIGN KEY ("WorkOrderID") REFERENCES public."WorkOrder"("WorkOrderID")
                )
                '''
            )
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_materialcall_source"
                ON public."MaterialCall" ("SourceType", "SourceID")
                '''
            )
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_materialcall_estimateid"
                ON public."MaterialCall" ("EstimateID")
                '''
            )
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_materialcall_workorderid"
                ON public."MaterialCall" ("WorkOrderID")
                '''
            )

            cur.execute('CREATE SEQUENCE IF NOT EXISTS "MaterialCallItem_MaterialCallItemID_seq"')
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS public."MaterialCallItem" (
                    "MaterialCallItemID" integer NOT NULL DEFAULT nextval('"MaterialCallItem_MaterialCallItemID_seq"'::regclass),
                    "MaterialCallID" integer NOT NULL,
                    "MaterialID" integer,
                    "EstimateItemID" integer,
                    "Description" text NOT NULL,
                    "PartNumber" text,
                    "Quantity" numeric DEFAULT 0.00,
                    "Unit" text,
                    "Notes" text,
                    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "MaterialCallItem_pkey" PRIMARY KEY ("MaterialCallItemID"),
                    CONSTRAINT "MaterialCallItem_MaterialCallID_fkey"
                        FOREIGN KEY ("MaterialCallID") REFERENCES public."MaterialCall"("MaterialCallID"),
                    CONSTRAINT "MaterialCallItem_MaterialID_fkey"
                        FOREIGN KEY ("MaterialID") REFERENCES public."Material"("ItemID"),
                    CONSTRAINT "MaterialCallItem_EstimateItemID_fkey"
                        FOREIGN KEY ("EstimateItemID") REFERENCES public."EstimateMaterial"("EstimateMaterialID")
                )
                '''
            )
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_materialcallitem_materialcallid"
                ON public."MaterialCallItem" ("MaterialCallID")
                '''
            )

            cur.execute('ALTER TABLE IF EXISTS public."PriceRequest" ADD COLUMN IF NOT EXISTS "MaterialCallID" integer')
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_pricerequest_materialcallid"
                ON public."PriceRequest" ("MaterialCallID")
                '''
            )

            cur.execute('ALTER TABLE IF EXISTS public."PriceRequestItem" ADD COLUMN IF NOT EXISTS "MaterialCallItemID" integer')
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_pritem_materialcallitemid"
                ON public."PriceRequestItem" ("MaterialCallItemID")
                '''
            )

            cur.execute('ALTER TABLE IF EXISTS public."RFQCarriedSelection" ADD COLUMN IF NOT EXISTS "MaterialCallID" integer')
            cur.execute('ALTER TABLE IF EXISTS public."RFQCarriedSelection" ADD COLUMN IF NOT EXISTS "MaterialCallItemID" integer')
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_rfqcarriedselection_materialcallid"
                ON public."RFQCarriedSelection" ("MaterialCallID")
                '''
            )

            cur.execute('ALTER TABLE IF EXISTS public."PurchaseOrderItemSourceLink" ADD COLUMN IF NOT EXISTS "MaterialCallID" integer')
            cur.execute('ALTER TABLE IF EXISTS public."PurchaseOrderItemSourceLink" ADD COLUMN IF NOT EXISTS "MaterialCallItemID" integer')
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_poitemsourcelink_materialcallid"
                ON public."PurchaseOrderItemSourceLink" ("MaterialCallID")
                '''
            )

        if owns_connection and conn is not None:
            conn.commit()
        _MATERIAL_CALL_SCHEMA_READY = True
    except Exception:
        if owns_connection and conn is not None:
            conn.rollback()
        raise
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _next_material_call_number(cur) -> str:
    cur.execute(
        '''
        SELECT "MaterialCallNumber"
        FROM "MaterialCall"
        WHERE "MaterialCallNumber" ~ '^MC-[0-9]+$'
        ORDER BY CAST(SUBSTRING("MaterialCallNumber" FROM 4) AS integer) DESC
        LIMIT 1
        '''
    )
    row = cur.fetchone()
    next_number = 1
    if row and row.get("MaterialCallNumber"):
        next_number = int(str(row["MaterialCallNumber"])[3:]) + 1
    return f"MC-{next_number:06d}"


def get_next_material_call_number_preview() -> str:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur, verify_only=True)
        return _next_material_call_number(cur)
    finally:
        conn.close()


def _material_call_status_text(status: str | None) -> str:
    value = str(status or "").strip()
    return value or "Open"


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_optional_int(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    return int(value)


def _normalize_positive_quantity(value: Any, *, error_label: str) -> float:
    quantity_value = float(value or 0)
    if quantity_value <= 0:
        raise ValueError(f"{error_label} quantity must be greater than zero.")
    return quantity_value


def _insert_material_call(
    cur,
    source_context: dict[str, Any],
    *,
    status: str | None = None,
    notes: str | None = None,
) -> int:
    cur.execute('LOCK TABLE public."MaterialCall" IN EXCLUSIVE MODE')
    material_call_number = _next_material_call_number(cur)
    cur.execute(
        '''
        INSERT INTO "MaterialCall" (
            "MaterialCallNumber",
            "SourceType",
            "SourceID",
            "EstimateID",
            "WorkOrderID",
            "Status",
            "Notes"
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING "MaterialCallID"
        ''',
        (
            material_call_number,
            str(source_context["source_type"]),
            int(source_context["source_id"]),
            int(source_context["estimate_id"]) if source_context.get("estimate_id") is not None else None,
            int(source_context["work_order_id"]) if source_context.get("work_order_id") is not None else None,
            _material_call_status_text(status),
            _normalize_optional_text(notes),
        ),
    )
    return int(cur.fetchone()["MaterialCallID"])


def _insert_material_call_item(cur, material_call_id: int, row: dict[str, Any]) -> int:
    if material_call_id in (None, "", 0, "0"):
        raise ValueError("MaterialCallID is required.")

    cur.execute(
        'SELECT 1 FROM "MaterialCall" WHERE "MaterialCallID" = %s',
        (int(material_call_id),),
    )
    if not cur.fetchone():
        raise ValueError(f"Material Call #{material_call_id} was not found.")

    description_text = str(row.get("description") or "").strip()
    if not description_text:
        raise ValueError("Material Call item description is required.")

    quantity_value = _normalize_positive_quantity(
        row.get("quantity"),
        error_label="Material Call item",
    )

    material_id = _normalize_optional_int(
        row.get("material_id")
        if row.get("material_id") not in (None, "", 0, "0")
        else row.get("catalog_item_id"),
    )
    estimate_item_id = _normalize_optional_int(row.get("estimate_item_id"))

    if material_id is not None:
        cur.execute(
            'SELECT 1 FROM "Material" WHERE "ItemID" = %s',
            (material_id,),
        )
        if not cur.fetchone():
            raise ValueError(f"Material catalog item #{material_id} was not found.")

    if estimate_item_id is not None:
        cur.execute(
            'SELECT 1 FROM "EstimateMaterial" WHERE "EstimateMaterialID" = %s',
            (estimate_item_id,),
        )
        if not cur.fetchone():
            raise ValueError(f"Estimate material row #{estimate_item_id} was not found.")

    cur.execute(
        '''
        INSERT INTO "MaterialCallItem" (
            "MaterialCallID",
            "MaterialID",
            "EstimateItemID",
            "Description",
            "PartNumber",
            "Quantity",
            "Unit",
            "Notes"
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING "MaterialCallItemID"
        ''',
        (
            int(material_call_id),
            material_id,
            estimate_item_id,
            description_text,
            _normalize_optional_text(row.get("part_number")),
            quantity_value,
            _normalize_optional_text(row.get("unit")),
            _normalize_optional_text(row.get("notes")),
        ),
    )
    return int(cur.fetchone()["MaterialCallItemID"])


def _load_selected_estimate_material_rows(
    cur,
    estimate_id: int,
    material_ids_or_estimate_item_ids: list[Any],
) -> list[dict[str, Any]]:
    selected_ids: list[int] = []
    seen_ids: set[int] = set()
    for value in material_ids_or_estimate_item_ids or []:
        if value in (None, "", 0, "0"):
            continue
        item_id = int(value)
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        selected_ids.append(item_id)

    if not selected_ids:
        raise ValueError("Select at least one estimate material row before creating a Material Call.")

    cur.execute(
        '''
        SELECT
            em."EstimateMaterialID" AS "EstimateItemID",
            em."ItemID" AS "MaterialID",
            COALESCE(NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') AS "Description",
            COALESCE(NULLIF(TRIM(em."PartNumber"), ''), NULLIF(TRIM(m."PartNumber"), ''), '') AS "PartNumber",
            COALESCE(em."Quantity", 0) AS "Quantity",
            COALESCE(NULLIF(TRIM(m."Unit"), ''), '') AS "Unit"
        FROM "EstimateMaterial" em
        LEFT JOIN "Material" m ON em."ItemID" = m."ItemID"
        WHERE em."EstimateID" = %s
          AND em."EstimateMaterialID" = ANY(%s)
        ''',
        (int(estimate_id), selected_ids),
    )
    rows = [dict(row) for row in cur.fetchall()]
    row_map = {int(row["EstimateItemID"]): row for row in rows}

    missing_ids = [item_id for item_id in selected_ids if item_id not in row_map]
    if missing_ids:
        raise ValueError(
            f"Estimate #{estimate_id} is missing the selected material row(s): {', '.join(str(item_id) for item_id in missing_ids)}."
        )

    normalized_rows: list[dict[str, Any]] = []
    for item_id in selected_ids:
        row = row_map[item_id]
        description_text = str(row.get("Description") or "").strip()
        if not description_text:
            raise ValueError(f"Estimate material row #{item_id} is missing a description.")
        normalized_rows.append(
            {
                "estimate_item_id": int(row["EstimateItemID"]),
                "material_id": _normalize_optional_int(row.get("MaterialID")),
                "description": description_text,
                "part_number": _normalize_optional_text(row.get("PartNumber")),
                "quantity": _normalize_positive_quantity(
                    row.get("Quantity"),
                    error_label=f"Estimate material row #{item_id}",
                ),
                "unit": _normalize_optional_text(row.get("Unit")),
                "notes": None,
            }
        )
    return normalized_rows


def _normalize_manual_material_call_rows(cur, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    if not rows:
        raise ValueError("Add at least one material row before creating a Material Call.")

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Material Call manual row #{index} is not a valid row object.")

        material_id = _normalize_optional_int(
            row.get("material_id")
            if row.get("material_id") not in (None, "", 0, "0")
            else row.get("catalog_item_id")
            if row.get("catalog_item_id") not in (None, "", 0, "0")
            else row.get("item_id"),
        )
        estimate_item_id = _normalize_optional_int(row.get("estimate_item_id"))
        description_text = _normalize_optional_text(row.get("description"))
        part_number = _normalize_optional_text(row.get("part_number"))
        unit = _normalize_optional_text(row.get("unit"))
        notes = _normalize_optional_text(row.get("notes"))

        if material_id is not None:
            cur.execute(
                '''
                SELECT
                    "ItemID",
                    COALESCE(NULLIF(TRIM("Description"), ''), '') AS "Description",
                    COALESCE(NULLIF(TRIM("PartNumber"), ''), '') AS "PartNumber",
                    COALESCE(NULLIF(TRIM("Unit"), ''), '') AS "Unit"
                FROM "Material"
                WHERE "ItemID" = %s
                ''',
                (material_id,),
            )
            material_row = cur.fetchone()
            if not material_row:
                raise ValueError(f"Material catalog item #{material_id} was not found for manual row #{index}.")
            if not description_text:
                description_text = _normalize_optional_text(material_row.get("Description"))
            if not part_number:
                part_number = _normalize_optional_text(material_row.get("PartNumber"))
            if not unit:
                unit = _normalize_optional_text(material_row.get("Unit"))

        if not description_text:
            raise ValueError(f"Material Call manual row #{index} is missing a description.")

        normalized_rows.append(
            {
                "estimate_item_id": estimate_item_id,
                "material_id": material_id,
                "description": description_text,
                "part_number": part_number,
                "quantity": _normalize_positive_quantity(
                    row.get("quantity") if row.get("quantity") not in (None, "") else row.get("qty"),
                    error_label=f"Material Call manual row #{index}",
                ),
                "unit": unit,
                "notes": notes,
            }
        )
    return normalized_rows


def _create_material_call_with_items(
    cur,
    *,
    source_context: dict[str, Any],
    item_rows: list[dict[str, Any]],
    status: str | None = None,
    notes: str | None = None,
) -> int:
    if not item_rows:
        raise ValueError("Material Calls created from row selections must contain at least one item.")

    material_call_id = _insert_material_call(
        cur,
        source_context,
        status=status,
        notes=notes,
    )
    for row in item_rows:
        _insert_material_call_item(cur, material_call_id, row)
    return material_call_id


def _normalize_material_call_due_date(due_date: Any) -> datetime.date | None:
    if due_date in (None, ""):
        return None
    if isinstance(due_date, datetime.datetime):
        return due_date.date()
    if isinstance(due_date, datetime.date):
        return due_date
    raw_text = str(due_date).strip()
    if not raw_text:
        return None
    return datetime.date.fromisoformat(raw_text)


def _load_material_call_row(cur, material_call_id: int) -> dict[str, Any] | None:
    cur.execute(
        '''
        SELECT
            mc.*,
            (
                SELECT COUNT(*)
                FROM "MaterialCallItem" mci
                WHERE mci."MaterialCallID" = mc."MaterialCallID"
            ) AS "ItemCount"
        FROM "MaterialCall" mc
        WHERE mc."MaterialCallID" = %s
        ''',
        (int(material_call_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _load_material_call_items_for_update(cur, material_call_id: int) -> list[dict[str, Any]]:
    cur.execute(
        '''
        SELECT *
        FROM "MaterialCallItem"
        WHERE "MaterialCallID" = %s
        ORDER BY "MaterialCallItemID" ASC
        ''',
        (int(material_call_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def _validate_vendor(cur, vendor_id: int) -> dict[str, Any]:
    if vendor_id in (None, "", 0, "0"):
        raise ValueError("Select a Vendor before creating the RFQ.")
    cur.execute(
        '''
        SELECT "VendorID", COALESCE(NULLIF(TRIM("VendorName"), ''), '') AS "VendorName"
        FROM "Vendor"
        WHERE "VendorID" = %s
        ''',
        (int(vendor_id),),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Vendor #{vendor_id} was not found.")
    return dict(row)


def _build_material_call_rfq_notes(
    *,
    material_call: dict[str, Any],
    item_rows: list[dict[str, Any]],
    notes: str | None = None,
) -> str:
    material_call_number = str(material_call.get("MaterialCallNumber") or f"MC#{material_call['MaterialCallID']}")
    source_type = str(material_call.get("SourceType") or "Estimate").strip() or "Estimate"
    source_id = int(material_call.get("SourceID") or 0)

    note_lines = [
        f"Created from Material Call {material_call_number}.",
        f"SourceType={source_type}",
        f"SourceID={source_id}",
        f"Source Document: {'Work Order' if source_type == 'WorkOrder' else 'Estimate'} #{source_id}",
    ]
    clean_notes = _normalize_optional_text(notes)
    if clean_notes:
        note_lines.append(f"RFQ Notes: {clean_notes}")
    for index, row in enumerate(item_rows, start=1):
        row_note = _normalize_optional_text(row.get("Notes") if "Notes" in row else row.get("notes"))
        if row_note:
            note_lines.append(f"Row {index} note: {row_note}")
    return "\n".join(note_lines)


def _ensure_material_call_item_compatibility_estimate_row(
    cur,
    *,
    estimate_id: int,
    material_call_item: dict[str, Any],
) -> int:
    existing_estimate_item_id = _normalize_optional_int(material_call_item.get("EstimateItemID"))
    if existing_estimate_item_id is not None:
        cur.execute(
            '''
            SELECT 1
            FROM "EstimateMaterial"
            WHERE "EstimateMaterialID" = %s
            ''',
            (existing_estimate_item_id,),
        )
        if cur.fetchone():
            return int(existing_estimate_item_id)

    from neon_ai.database.rfq import _insert_manual_estimate_material

    estimate_material_id = _insert_manual_estimate_material(
        cur,
        int(estimate_id),
        {
            "description": material_call_item.get("Description"),
            "qty": material_call_item.get("Quantity"),
            "part_number": material_call_item.get("PartNumber"),
            "catalog_item_id": material_call_item.get("MaterialID"),
            "unit_cost": 0,
        },
    )
    cur.execute(
        '''
        UPDATE "MaterialCallItem"
        SET "EstimateItemID" = %s,
            "UpdatedAt" = CURRENT_TIMESTAMP
        WHERE "MaterialCallItemID" = %s
        ''',
        (int(estimate_material_id), int(material_call_item["MaterialCallItemID"])),
    )
    material_call_item["EstimateItemID"] = int(estimate_material_id)
    return int(estimate_material_id)


def create_material_call(
    source_type: str,
    source_id: int,
    *,
    status: str | None = None,
    notes: str | None = None,
) -> int:
    _normalize_material_call_source_type(source_type)
    if source_id in (None, "", 0, "0"):
        raise ValueError("Material Calls must be tied to a source Estimate or open Work Order.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur)
        source_context = _resolve_material_call_source_context(
            source_type,
            int(source_id),
            cur=cur,
            require_eligible=True,
        )
        material_call_id = _insert_material_call(
            cur,
            source_context,
            status=status,
            notes=notes,
        )
        conn.commit()
        return material_call_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_or_create_material_call_for_source(
    source_type: str,
    source_id: int,
    *,
    status: str | None = None,
    notes: str | None = None,
) -> int:
    _normalize_material_call_source_type(source_type)
    if source_id in (None, "", 0, "0"):
        raise ValueError("Material Calls must be tied to a source Estimate or open Work Order.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur)
        source_context = _resolve_material_call_source_context(
            source_type,
            int(source_id),
            cur=cur,
            require_eligible=True,
        )
        cur.execute(
            '''
            SELECT "MaterialCallID"
            FROM "MaterialCall"
            WHERE "SourceType" = %s
              AND "SourceID" = %s
              AND UPPER(TRIM(COALESCE("Status", 'OPEN'))) IN ('OPEN', 'DRAFT')
            ORDER BY "MaterialCallID" DESC
            LIMIT 1
            ''',
            (
                str(source_context["source_type"]),
                int(source_context["source_id"]),
            ),
        )
        row = cur.fetchone()
        if row and row.get("MaterialCallID") is not None:
            return int(row["MaterialCallID"])

        material_call_id = _insert_material_call(
            cur,
            source_context,
            status=status,
            notes=notes,
        )
        conn.commit()
        return material_call_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_material_call_item(
    material_call_id: int,
    *,
    description: str,
    quantity: float,
    part_number: str | None = None,
    unit: str | None = None,
    notes: str | None = None,
    material_id: int | None = None,
    estimate_item_id: int | None = None,
) -> int:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur)
        item_id = _insert_material_call_item(
            cur,
            int(material_call_id),
            {
                "material_id": material_id,
                "estimate_item_id": estimate_item_id,
                "description": description,
                "part_number": part_number,
                "quantity": quantity,
                "unit": unit,
                "notes": notes,
            },
        )
        conn.commit()
        return item_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_material_call_status_and_notes(
    material_call_id: int,
    *,
    status: str | None = None,
    notes: str | None = None,
) -> None:
    if material_call_id in (None, "", 0, "0"):
        raise ValueError("MaterialCallID is required.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur)
        cur.execute(
            '''
            UPDATE "MaterialCall"
            SET
                "Status" = %s,
                "Notes" = %s
            WHERE "MaterialCallID" = %s
            ''',
            (
                _material_call_status_text(status),
                _normalize_optional_text(notes),
                int(material_call_id),
            ),
        )
        if cur.rowcount <= 0:
            raise ValueError(f"Material Call #{material_call_id} was not found.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_material_call_from_estimate_items(
    estimate_id: int,
    material_ids_or_estimate_item_ids: list[Any],
    notes: str | None = None,
    status: str | None = "Draft",
) -> int:
    if estimate_id in (None, "", 0, "0"):
        raise ValueError("Estimate source is required for estimate-based Material Calls.")
    if not any(value not in (None, "", 0, "0") for value in (material_ids_or_estimate_item_ids or [])):
        raise ValueError("Select at least one estimate material row before creating a Material Call.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur)
        source_context = _resolve_material_call_source_context(
            "Estimate",
            int(estimate_id),
            cur=cur,
            require_eligible=True,
        )
        item_rows = _load_selected_estimate_material_rows(
            cur,
            int(estimate_id),
            material_ids_or_estimate_item_ids,
        )
        material_call_id = _create_material_call_with_items(
            cur,
            source_context=source_context,
            item_rows=item_rows,
            status=status,
            notes=notes,
        )
        conn.commit()
        return material_call_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_material_call_from_manual_rows(
    source_type: str,
    source_id: int,
    rows: list[dict[str, Any]],
    notes: str | None = None,
    status: str | None = "Draft",
) -> int:
    _normalize_material_call_source_type(source_type)
    if source_id in (None, "", 0, "0"):
        raise ValueError("Material Calls must be tied to a source Estimate or open Work Order.")
    if not rows:
        raise ValueError("Add at least one material row before creating a Material Call.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur)
        source_context = _resolve_material_call_source_context(
            source_type,
            int(source_id),
            cur=cur,
            require_eligible=True,
        )
        item_rows = _normalize_manual_material_call_rows(cur, rows)
        material_call_id = _create_material_call_with_items(
            cur,
            source_context=source_context,
            item_rows=item_rows,
            status=status,
            notes=notes,
        )
        conn.commit()
        return material_call_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_vendor_rfq_for_material_call(
    material_call_id: int,
    vendor_id: int,
    due_date: Any = None,
    notes: str | None = None,
) -> dict[str, Any]:
    if material_call_id in (None, "", 0, "0"):
        raise ValueError("Create a Material Call first.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur)
        from neon_ai.database.rfq import ensure_material_request_source_schema

        ensure_material_request_source_schema(cur=cur)
        material_call = _load_material_call_row(cur, int(material_call_id))
        if not material_call:
            raise ValueError(f"Material Call #{material_call_id} was not found.")

        material_call_items = _load_material_call_items_for_update(cur, int(material_call_id))
        if not material_call_items:
            material_call_number = str(material_call.get("MaterialCallNumber") or f"MC#{material_call_id}")
            raise ValueError(f"Material Call {material_call_number} has no items to package into an RFQ.")

        vendor_row = _validate_vendor(cur, int(vendor_id))
        source_context = _resolve_material_call_source_context(
            str(material_call.get("SourceType") or ""),
            int(material_call.get("SourceID") or 0),
            cur=cur,
            require_eligible=False,
        )
        estimate_id = _normalize_optional_int(material_call.get("EstimateID"))
        if estimate_id is None:
            estimate_id = _normalize_optional_int(source_context.get("estimate_id"))
        if estimate_id is None:
            raise ValueError(
                f"Material Call #{material_call_id} is missing estimate compatibility context for RFQ creation."
            )

        normalized_due_date = _normalize_material_call_due_date(due_date)
        notes_payload = _build_material_call_rfq_notes(
            material_call=material_call,
            item_rows=material_call_items,
            notes=notes,
        )
        cur.execute(
            '''
            INSERT INTO "PriceRequest" (
                "EstimateID",
                "VendorID",
                "DueDate",
                "DateSent",
                "Status",
                "Notes",
                "MaterialRequestSourceType",
                "MaterialRequestSourceID",
                "MaterialCallID"
            )
            VALUES (%s, %s, %s, NULL, 'Draft', %s, %s, %s, %s)
            RETURNING "PriceRequestID"
            ''',
            (
                int(estimate_id),
                int(vendor_row["VendorID"]),
                normalized_due_date,
                notes_payload,
                str(material_call.get("SourceType") or source_context.get("source_type") or "Estimate"),
                int(material_call.get("SourceID") or source_context.get("source_id") or estimate_id),
                int(material_call["MaterialCallID"]),
            ),
        )
        rfq_id = int(cur.fetchone()["PriceRequestID"])

        for item_row in material_call_items:
            compatibility_estimate_item_id = _ensure_material_call_item_compatibility_estimate_row(
                cur,
                estimate_id=int(estimate_id),
                material_call_item=item_row,
            )
            cur.execute(
                '''
                INSERT INTO "PriceRequestItem" (
                    "PriceRequestID",
                    "MaterialCallItemID",
                    "MaterialID",
                    "QuantityOverride",
                    "QuotedUnitPrice",
                    "IsSubstitute",
                    "SubstituteNotes",
                    "IsCarried"
                )
                VALUES (%s, %s, %s, %s, NULL, FALSE, NULL, FALSE)
                ''',
                (
                    int(rfq_id),
                    int(item_row["MaterialCallItemID"]),
                    int(compatibility_estimate_item_id),
                    float(item_row.get("Quantity") or 0),
                ),
            )

        conn.commit()
        material_call_number = str(
            material_call.get("MaterialCallNumber") or f"MC#{material_call['MaterialCallID']}"
        )
        return {
            "rfq_id": int(rfq_id),
            "material_call_id": int(material_call["MaterialCallID"]),
            "material_call_number": material_call_number,
            "estimate_id": int(estimate_id),
            "source_type": str(material_call.get("SourceType") or source_context.get("source_type") or "Estimate"),
            "source_id": int(material_call.get("SourceID") or source_context.get("source_id") or estimate_id),
            "vendor_id": int(vendor_row["VendorID"]),
            "vendor_name": str(vendor_row.get("VendorName") or ""),
            "item_count": len(material_call_items),
            "status": "Draft",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _decorate_material_call_row(row: dict[str, Any], *, cur=None) -> dict[str, Any]:
    if not row:
        return row

    decorated = dict(row)
    try:
        source_context = _resolve_material_call_source_context(
            decorated.get("SourceType"),
            decorated.get("SourceID"),
            cur=cur,
            require_eligible=False,
        )
        decorated["SourceDocumentLabel"] = source_context.get("source_document_label") or ""
        decorated["SelectorLabel"] = source_context.get("selector_label") or "Source Document #"
    except Exception:
        decorated["SourceDocumentLabel"] = ""
        decorated["SelectorLabel"] = "Source Document #"
    return decorated


def get_material_call(material_call_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur, verify_only=True)
        cur.execute(
            '''
            SELECT
                mc.*,
                (
                    SELECT COUNT(*)
                    FROM "MaterialCallItem" mci
                    WHERE mci."MaterialCallID" = mc."MaterialCallID"
                ) AS "ItemCount"
            FROM "MaterialCall" mc
            WHERE mc."MaterialCallID" = %s
            ''',
            (int(material_call_id),),
        )
        row = cur.fetchone()
        return _decorate_material_call_row(row, cur=cur) if row else None
    finally:
        conn.close()


def get_material_call_items(material_call_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur, verify_only=True)
        cur.execute(
            '''
            SELECT *
            FROM "MaterialCallItem"
            WHERE "MaterialCallID" = %s
            ORDER BY "MaterialCallItemID" ASC
            ''',
            (int(material_call_id),),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_material_calls_for_source(source_type: str, source_id: int) -> list[dict[str, Any]]:
    normalized_source_type = _normalize_material_call_source_type(source_type)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur, verify_only=True)
        cur.execute(
            '''
            SELECT mc.*
            FROM "MaterialCall" mc
            WHERE mc."SourceType" = %s
              AND mc."SourceID" = %s
            ORDER BY mc."MaterialCallID" DESC
            ''',
            (normalized_source_type, int(source_id)),
        )
        return [_decorate_material_call_row(dict(row), cur=cur) for row in cur.fetchall()]
    finally:
        conn.close()


def list_material_calls_for_estimate(estimate_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur, verify_only=True)
        cur.execute(
            '''
            SELECT mc.*
            FROM "MaterialCall" mc
            WHERE mc."EstimateID" = %s
            ORDER BY mc."MaterialCallID" DESC
            ''',
            (int(estimate_id),),
        )
        return [_decorate_material_call_row(dict(row), cur=cur) for row in cur.fetchall()]
    finally:
        conn.close()


def list_material_calls_for_work_order(work_order_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur, verify_only=True)
        cur.execute(
            '''
            SELECT mc.*
            FROM "MaterialCall" mc
            WHERE mc."WorkOrderID" = %s
            ORDER BY mc."MaterialCallID" DESC
            ''',
            (int(work_order_id),),
        )
        return [_decorate_material_call_row(dict(row), cur=cur) for row in cur.fetchall()]
    finally:
        conn.close()


def get_material_call_rfq_pipeline(*, include_legacy_loose_rfqs: bool | None = None) -> dict[str, Any]:
    started_at = time.perf_counter()
    include_legacy_loose_rfqs = (
        legacy_loose_rfq_debug_enabled()
        if include_legacy_loose_rfqs is None
        else bool(include_legacy_loose_rfqs)
    )
    pipeline_material_calls: list[dict[str, Any]] = []
    legacy_rfqs: list[dict[str, Any]] = []
    rfq_rows: list[dict[str, Any]] = []
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_call_schema(cur=cur, verify_only=True)
        parent_query_started_at = time.perf_counter()
        cur.execute(
            '''
            SELECT
                mc."MaterialCallID",
                mc."MaterialCallNumber",
                mc."SourceType",
                mc."SourceID",
                mc."EstimateID",
                mc."WorkOrderID",
                mc."Status",
                mc."CreatedAt",
                mc."UpdatedAt",
                mc."Notes",
                COALESCE(mci_counts."ItemCount", 0) AS "ItemCount",
                COALESCE(pr_counts."RFQCount", 0) AS "RFQCount",
                c."CustomerName",
                s."SiteName"
            FROM "MaterialCall" mc
            LEFT JOIN (
                SELECT
                    mci."MaterialCallID",
                    COUNT(*) AS "ItemCount"
                FROM "MaterialCallItem" mci
                GROUP BY mci."MaterialCallID"
            ) mci_counts ON mci_counts."MaterialCallID" = mc."MaterialCallID"
            LEFT JOIN (
                SELECT
                    pr."MaterialCallID",
                    COUNT(*) AS "RFQCount"
                FROM "PriceRequest" pr
                WHERE pr."MaterialCallID" IS NOT NULL
                GROUP BY pr."MaterialCallID"
            ) pr_counts ON pr_counts."MaterialCallID" = mc."MaterialCallID"
            LEFT JOIN "Estimate" e ON mc."EstimateID" = e."EstimateID"
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            ORDER BY mc."CreatedAt" DESC, mc."MaterialCallID" DESC
            '''
        )
        material_call_rows = [dict(row) for row in cur.fetchall()]
        _perf_log(
            "db",
            "material_call_pipeline.parent_query",
            parent_query_started_at,
            row_count=len(material_call_rows),
        )

        enrichment_started_at = time.perf_counter()
        for row in material_call_rows:
            row["SourceDocumentLabel"] = _tree_source_document_label(
                row.get("SourceType"),
                row.get("SourceID"),
            )
            row["SelectorLabel"] = _tree_selector_label(row.get("SourceType"))
            row["rfqs"] = []
        _perf_log(
            "db",
            "material_call_pipeline.enrichment",
            enrichment_started_at,
            row_count=len(material_call_rows),
            n_plus_one_removed=True,
        )

        child_query_started_at = time.perf_counter()
        rfq_sql = '''
            SELECT
                pr."PriceRequestID",
                pr."MaterialCallID",
                pr."VendorID",
                pr."DateSent",
                pr."DueDate",
                pr."Status",
                v."VendorName"
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
        '''
        if include_legacy_loose_rfqs:
            rfq_sql += '''
            ORDER BY
                pr."MaterialCallID" NULLS LAST,
                pr."DateSent" DESC NULLS LAST,
                pr."PriceRequestID" DESC
            '''
        else:
            rfq_sql += '''
            WHERE pr."MaterialCallID" IS NOT NULL
            ORDER BY
                pr."MaterialCallID" DESC,
                pr."DateSent" DESC NULLS LAST,
                pr."PriceRequestID" DESC
            '''
        cur.execute(rfq_sql)
        rfq_rows = [dict(row) for row in cur.fetchall()]
        _perf_log(
            "db",
            "material_call_pipeline.child_query",
            child_query_started_at,
            row_count=len(rfq_rows),
            include_legacy_loose_rfqs=include_legacy_loose_rfqs,
        )
        children_by_material_call: dict[int, list[dict[str, Any]]] = {}
        for row in rfq_rows:
            material_call_id = row.get("MaterialCallID")
            if material_call_id not in (None, "", 0, "0"):
                children_by_material_call.setdefault(int(material_call_id), []).append(row)
            elif include_legacy_loose_rfqs:
                legacy_rfqs.append(row)

        for row in material_call_rows:
            material_call_id = int(row["MaterialCallID"])
            row["rfqs"] = children_by_material_call.get(material_call_id, [])
            pipeline_material_calls.append(row)

        result = {
            "material_calls": pipeline_material_calls,
            "legacy_rfqs": legacy_rfqs,
            "include_legacy_loose_rfqs": include_legacy_loose_rfqs,
        }
        return result
    finally:
        _perf_log(
            "db",
            "material_call_pipeline.total",
            started_at,
            material_call_count=len(pipeline_material_calls),
            legacy_rfq_count=len(legacy_rfqs),
            row_count=len(rfq_rows),
            include_legacy_loose_rfqs=include_legacy_loose_rfqs,
            legacy_loading_skipped=not include_legacy_loose_rfqs,
        )
        _perf_log(
            "db",
            "material_calls.get_material_call_rfq_pipeline",
            started_at,
            material_call_count=len(pipeline_material_calls),
            legacy_rfq_count=len(legacy_rfqs),
            row_count=len(rfq_rows),
            include_legacy_loose_rfqs=include_legacy_loose_rfqs,
            legacy_loading_skipped=not include_legacy_loose_rfqs,
        )
        conn.close()
