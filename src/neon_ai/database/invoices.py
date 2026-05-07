import datetime
import json
import os
from decimal import Decimal

from psycopg2.extras import Json, RealDictCursor

from neon_ai.database.connection import get_connection


CUSTOMER_INVOICE_MEMORY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "customer_invoice_memory.json")
_INVOICE_SCHEMA_READY = False
_NON_OPERATIONAL_WORK_ORDER_STATUSES = {"DRAFT", "PENDINGSTART"}


def ensure_invoice_schema():
    global _INVOICE_SCHEMA_READY
    if _INVOICE_SCHEMA_READY:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('ALTER TABLE "Time" ADD COLUMN IF NOT EXISTS "CustomerInvoiceId" integer')
        cur.execute('ALTER TABLE "PurchaseOrderItem" ADD COLUMN IF NOT EXISTS "CustomerInvoiceId" integer')
        cur.execute('ALTER TABLE "Invoice" ADD COLUMN IF NOT EXISTS "CustomerInvoiceDocPath" text')
        cur.execute('ALTER TABLE "Invoice" ADD COLUMN IF NOT EXISTS "CustomerInvoiceSentAt" timestamptz')
        cur.execute('ALTER TABLE "Invoice" ADD COLUMN IF NOT EXISTS "CustomerInvoiceMemory" jsonb')
        conn.commit()
        _INVOICE_SCHEMA_READY = True
    finally:
        conn.close()


def _load_legacy_customer_invoice_memory():
    if not os.path.exists(CUSTOMER_INVOICE_MEMORY_PATH):
        return []
    try:
        with open(CUSTOMER_INVOICE_MEMORY_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _json_db_ready(value):
    if isinstance(value, dict):
        return {str(key): _json_db_ready(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_db_ready(item) for item in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _decode_invoice_memory(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _is_non_operational_work_order_status(status: str | None) -> bool:
    text = str(status or "").strip()
    if not text:
        return False
    compact = text.replace("_", "").replace(" ", "").upper()
    return compact in _NON_OPERATIONAL_WORK_ORDER_STATUSES


def _read_customer_invoice_memory_from_db(invoice_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            'SELECT "CustomerInvoiceMemory" FROM "Invoice" WHERE "CustomerInvoiceId" = %s',
            (invoice_id,),
        )
        row = cur.fetchone() or {}
        return _decode_invoice_memory(row.get("CustomerInvoiceMemory"))
    finally:
        conn.close()


def _write_customer_invoice_memory_to_db(invoice_id: int, payload: dict):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            UPDATE "Invoice"
            SET "CustomerInvoiceMemory" = %s
            WHERE "CustomerInvoiceId" = %s
            ''',
            (Json(_json_db_ready(payload or {})), invoice_id),
        )
        conn.commit()
    finally:
        conn.close()


def load_customer_invoice_memory():
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT "CustomerInvoiceId", "CustomerInvoiceMemory"
            FROM "Invoice"
            WHERE "CustomerInvoiceMemory" IS NOT NULL
            ORDER BY "CustomerInvoiceId" ASC
            '''
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    entries = []
    for row in rows:
        payload = _decode_invoice_memory(row.get("CustomerInvoiceMemory"))
        if payload:
            payload.setdefault("invoice_id", row.get("CustomerInvoiceId"))
            entries.append(payload)
    return entries or _load_legacy_customer_invoice_memory()


def save_customer_invoice_memory(entries):
    for entry in entries or []:
        invoice_id = int(entry.get("invoice_id") or 0)
        if invoice_id:
            _write_customer_invoice_memory_to_db(invoice_id, entry)


def upsert_customer_invoice_memory(invoice_id: int, updates: dict):
    entry = dict(get_customer_invoice_memory(invoice_id) or {})
    entry["invoice_id"] = int(invoice_id)
    entry.update(_json_db_ready(updates or {}))
    _write_customer_invoice_memory_to_db(invoice_id, entry)
    return entry


def get_customer_invoice_memory(invoice_id: int):
    payload = _read_customer_invoice_memory_from_db(invoice_id)
    if payload:
        payload.setdefault("invoice_id", int(invoice_id))
        return payload

    legacy = next((row for row in _load_legacy_customer_invoice_memory() if int(row.get("invoice_id") or 0) == int(invoice_id)), {})
    if legacy:
        legacy = _json_db_ready(dict(legacy))
        legacy["invoice_id"] = int(invoice_id)
        _write_customer_invoice_memory_to_db(invoice_id, legacy)
    return legacy


def _fetch_latest_invoice(wo_id: int, statuses=None):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = '''
            SELECT *
            FROM "Invoice"
            WHERE "WorkOrderID" = %s
        '''
        params = [wo_id]
        if statuses:
            sql += ' AND "InvoiceStatus" = ANY(%s)'
            params.append(statuses)
        sql += ' ORDER BY "CustomerInvoiceId" DESC LIMIT 1'
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        conn.close()


def get_latest_invoice_for_workorder(wo_id: int, statuses=None):
    return _fetch_latest_invoice(wo_id, statuses=statuses)


def get_invoice_header_data(wo_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                wo."WorkOrderID",
                wo."Description" AS "Scope",
                wo."BillingType",
                wo."SourceEstimateID" AS "EstimateID",
                e."LaborMarkUp",
                e."MaterialMarkUp",
                e."TotalAmount" AS "EstimateTotal",
                c."CustomerName",
                c."Email" AS "CustomerEmail",
                s."SiteName",
                CONCAT_WS(', ', CONCAT_WS(' ', s."StreetNumber", s."StreetName"), s."City") AS "SiteAddress",
                inv."CustomerInvoiceId",
                inv."InvoiceStatus",
                inv."CustomerInvoiceAmount",
                inv."LaborPercent",
                inv."MaterialPercent",
                inv."LaborMilestoneNote",
                inv."MaterialMilestoneNote",
                inv."ScopeOfWork",
                inv."BillingMode",
                inv."CustomerInvoiceDate",
                inv."CustomerInvoiceDocPath"
            FROM "WorkOrder" wo
            JOIN "Site" s ON wo."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            LEFT JOIN "Estimate" e ON e."EstimateID" = wo."SourceEstimateID"
            LEFT JOIN (
                SELECT DISTINCT ON ("WorkOrderID")
                    "WorkOrderID",
                    "CustomerInvoiceId",
                    "InvoiceStatus",
                    "CustomerInvoiceAmount",
                    "LaborPercent",
                    "MaterialPercent",
                    "LaborMilestoneNote",
                    "MaterialMilestoneNote",
                    "ScopeOfWork",
                    "BillingMode",
                    "CustomerInvoiceDate",
                    "CustomerInvoiceDocPath",
                    "CustomerInvoiceSentAt"
                FROM "Invoice"
                WHERE COALESCE("InvoiceStatus", '') != 'Retired'
                ORDER BY "WorkOrderID", "CustomerInvoiceId" DESC
            ) inv ON inv."WorkOrderID" = wo."WorkOrderID"
            WHERE wo."WorkOrderID" = %s
            ''',
            (wo_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        row["PreviouslyInvoicedAmount"] = get_total_invoiced_amount(wo_id)
        row["PreviouslyInvoicedStipulatedPercent"] = get_total_stipulated_percent(wo_id)
        return row
    finally:
        conn.close()


def get_total_invoiced_amount(wo_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT COALESCE(SUM("CustomerInvoiceAmount"::numeric), 0) AS "Total"
            FROM "Invoice"
            WHERE "WorkOrderID" = %s
              AND COALESCE("InvoiceStatus", '') != 'Draft'
            ''',
            (wo_id,),
        )
        row = cur.fetchone()
        return float(row["Total"] or 0)
    finally:
        conn.close()


def get_total_stipulated_percent(wo_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                COALESCE(SUM(COALESCE("LaborPercent", 0) + COALESCE("MaterialPercent", 0)), 0) AS "TotalPercent"
            FROM "Invoice"
            WHERE "WorkOrderID" = %s
              AND COALESCE("InvoiceStatus", '') != 'Draft'
              AND UPPER(TRIM(COALESCE("BillingMode", ''))) = 'STIPULATED'
            ''',
            (wo_id,),
        )
        row = cur.fetchone()
        return float(row["TotalPercent"] or 0)
    finally:
        conn.close()


def _get_workorder_estimate_markups(cur, wo_id: int):
    cur.execute(
        '''
        SELECT
            COALESCE(e."LaborMarkUp", 0) AS "LaborMarkUp",
            COALESCE(e."MaterialMarkUp", 0) AS "MaterialMarkUp"
        FROM "WorkOrder" wo
        LEFT JOIN "Estimate" e ON e."EstimateID" = wo."SourceEstimateID"
        WHERE wo."WorkOrderID" = %s
        ''',
        (wo_id,),
    )
    return cur.fetchone() or {"LaborMarkUp": 0, "MaterialMarkUp": 0}


def get_unbilled_labor(wo_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        markups = _get_workorder_estimate_markups(cur, wo_id)
        labor_markup = float(markups.get("LaborMarkUp") or 0)
        cur.execute(
            '''
            SELECT
                t."TimeID",
                t."DateWorked",
                COALESCE(emp."EmployeeName", 'Employee') AS "EmployeeName",
                COALESCE(emp."EmployeeClass", '') AS "EmployeeClass",
                COALESCE(t."TaskId", '') AS "TaskName",
                COALESCE(t."HoursWorked", 0) AS "HoursWorked",
                COALESCE(NULLIF(t."HourlyRate", 0), NULLIF(emp."EmployeeRate", 0), 0) AS "BaseRate",
                COALESCE(emp."EmployeeBurden", 0) AS "BurdenPercent"
            FROM "Time" t
            LEFT JOIN "Employee" emp ON t."WorkerID" = emp."EmployeeID"
            WHERE t."WorkOrderID" = %s
              AND COALESCE(t."CustomerInvoiceId", 0) = 0
              AND COALESCE(t."Status", '') IN ('Approved', 'Exported', 'Pending')
            ORDER BY t."DateWorked" ASC, t."TimeID" ASC
            ''',
            (wo_id,),
        )
        rows = cur.fetchall()
        for row in rows:
            hours = float(row.get("HoursWorked") or 0)
            base_rate = float(row.get("BaseRate") or 0)
            burden_percent = float(row.get("BurdenPercent") or 0)
            burdened_rate = base_rate * (1 + (burden_percent / 100.0))
            billed_rate = burdened_rate * (1 + (labor_markup / 100.0))
            row["BaseRate"] = base_rate
            row["BurdenPercent"] = burden_percent
            row["LaborMarkUp"] = labor_markup
            row["BurdenedRate"] = burdened_rate
            row["HourlyRate"] = billed_rate
            row["LineTotal"] = hours * billed_rate
        return rows
    finally:
        conn.close()


def get_unbilled_materials(wo_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        markups = _get_workorder_estimate_markups(cur, wo_id)
        material_markup = float(markups.get("MaterialMarkUp") or 0)
        cur.execute(
            '''
            SELECT
                poi."POItemID",
                po."PurchaseOrderID",
                COALESCE(poi."Description", em."Description", m."Description") AS "Description",
                COALESCE(poi."QuantityReceived", 0) AS "Qty",
                COALESCE(
                    NULLIF(poi."UnitPriceAtOrder", 0),
                    NULLIF(em."CommittedUnitCost", 0),
                    NULLIF(em."AwardedUnitCost", 0),
                    NULLIF(em."UnitCost", 0),
                    CASE COALESCE(m."CarryPriceSource", 'Internal')
                        WHEN 'Nedco' THEN NULLIF(m."NedcoPrice", 0)
                        WHEN 'Gescan' THEN NULLIF(m."GescanPrice", 0)
                        WHEN 'Eecol' THEN NULLIF(m."EecolPrice", 0)
                        WHEN 'Guillevin' THEN NULLIF(m."GuillevinPrice", 0)
                        ELSE NULLIF(m."InternalPrice", 0)
                    END,
                    0
                ) AS "BaseCost"
            FROM "PurchaseOrderItem" poi
            JOIN "PurchaseOrder" po ON poi."PurchaseOrderID" = po."PurchaseOrderID"
            LEFT JOIN "EstimateMaterial" em ON poi."MaterialID" = em."EstimateMaterialID"
            LEFT JOIN "Material" m ON poi."CatalogItemID" = m."ItemID"
            WHERE po."WorkOrderID" = %s
              AND COALESCE(poi."QuantityReceived", 0) > 0
              AND COALESCE(poi."CustomerInvoiceId", 0) = 0
            ORDER BY po."PurchaseOrderID" ASC, poi."POItemID" ASC
            ''',
            (wo_id,),
        )
        rows = cur.fetchall()
        for row in rows:
            qty = float(row.get("Qty") or 0)
            base_cost = float(row.get("BaseCost") or 0)
            billed_cost = base_cost * (1 + (material_markup / 100.0))
            row["BaseCost"] = base_cost
            row["MaterialMarkUp"] = material_markup
            row["Cost"] = billed_cost
            row["LineTotal"] = qty * billed_cost
        return rows
    finally:
        conn.close()


def _create_or_get_draft_invoice(wo_id: int, data: dict):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('UPDATE "WorkOrder" SET "Description" = %s WHERE "WorkOrderID" = %s', (data["scope"], wo_id))
        cur.execute(
            '''
            SELECT *
            FROM "Invoice"
            WHERE "WorkOrderID" = %s AND "InvoiceStatus" = 'Draft'
            ORDER BY "CustomerInvoiceId" DESC
            LIMIT 1
            ''',
            (wo_id,),
        )
        draft = cur.fetchone()
        if draft:
            cur.execute(
                '''
                UPDATE "Invoice"
                SET "CustomerInvoiceAmount" = %s,
                    "LaborPercent" = %s,
                    "MaterialPercent" = %s,
                    "LaborMilestoneNote" = %s,
                    "MaterialMilestoneNote" = %s,
                    "ScopeOfWork" = %s,
                    "BillingMode" = %s
                WHERE "CustomerInvoiceId" = %s
                RETURNING *
                ''',
                (
                    data["total"],
                    data["l_perc"],
                    data["m_perc"],
                    data["l_note"],
                    data["m_note"],
                    data["scope"],
                    data["mode"],
                    draft["CustomerInvoiceId"],
                ),
            )
            draft = cur.fetchone()
        else:
            cur.execute(
                '''
                INSERT INTO "Invoice"
                ("WorkOrderID", "CustomerInvoiceAmount", "InvoiceStatus", "LaborPercent", "MaterialPercent",
                 "LaborMilestoneNote", "MaterialMilestoneNote", "ScopeOfWork", "BillingMode", "CustomerInvoiceDate")
                VALUES (%s, %s, 'Draft', %s, %s, %s, %s, %s, %s, CURRENT_DATE)
                RETURNING *
                ''',
                (
                    wo_id,
                    data["total"],
                    data["l_perc"],
                    data["m_perc"],
                    data["l_note"],
                    data["m_note"],
                    data["scope"],
                    data["mode"],
                ),
            )
            draft = cur.fetchone()
        conn.commit()
        return draft
    finally:
        conn.close()


def get_invoice_detail(invoice_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('SELECT * FROM "Invoice" WHERE "CustomerInvoiceId" = %s', (invoice_id,))
        invoice = cur.fetchone()
    finally:
        conn.close()
    if not invoice:
        return None
    memory = get_customer_invoice_memory(invoice_id)
    return {
        "invoice": invoice,
        "memory": memory,
        "header": get_invoice_header_data(invoice["WorkOrderID"]),
    }


def save_invoice_draft(wo_id: int, data: dict):
    ensure_invoice_schema()
    if data["mode"] == "Stipulated":
        if get_total_stipulated_percent(wo_id) + float(data["l_perc"] or 0) + float(data["m_perc"] or 0) > 100.0001:
            raise ValueError("This progress claim would invoice more than 100% of the stipulated-price contract.")

    draft = _create_or_get_draft_invoice(wo_id, data)
    upsert_customer_invoice_memory(
        draft["CustomerInvoiceId"],
        {
            "wo_id": wo_id,
            "mode": data["mode"],
            "scope": data["scope"],
            "labor_lines": data.get("labor_lines", []),
            "material_lines": data.get("material_lines", []),
            "review_summary": data.get("review_summary", ""),
            "last_saved_at": datetime.datetime.now().isoformat(),
        },
    )
    return draft


def _normalize_invoice_billing_mode(raw_value) -> str:
    text = str(raw_value or "").strip().lower()
    if text in {"t&m", "time and materials", "time & materials"}:
        return "T&M"
    if text in {"stipulated", "stipulated price", "fixed price"}:
        return "Stipulated"
    if "time" in text and "material" in text:
        return "T&M"
    return "Stipulated"


def _list_draft_invoices_for_workorder(cur, wo_id: int) -> list[dict]:
    cur.execute(
        '''
        SELECT *
        FROM "Invoice"
        WHERE "WorkOrderID" = %s
          AND COALESCE("InvoiceStatus", '') = 'Draft'
        ORDER BY "CustomerInvoiceId" DESC
        ''',
        (wo_id,),
    )
    return list(cur.fetchall() or [])


def _draft_invoice_has_export_or_send_evidence(invoice_row: dict | None) -> bool:
    row = invoice_row or {}
    return bool(
        str(row.get("CustomerInvoiceDocPath") or "").strip()
        or row.get("CustomerInvoiceSentAt")
        or str(row.get("InvoiceStatus") or "").strip() in {"Exported", "Sent", "Paid"}
    )


def _derive_safe_customer_invoice_draft_amount(work_order_id: int, billing_mode: str) -> tuple[float, list[str]]:
    warnings: list[str] = []
    normalized_mode = _normalize_invoice_billing_mode(billing_mode)
    if normalized_mode == "T&M":
        labor_total = round(sum(float(row.get("LineTotal") or 0.0) for row in get_unbilled_labor(work_order_id)), 2)
        material_total = round(sum(float(row.get("LineTotal") or 0.0) for row in get_unbilled_materials(work_order_id)), 2)
        total_amount = round(labor_total + material_total, 2)
        if total_amount <= 0:
            warnings.append("No unbilled T&M labor or material value was available, so the draft invoice amount defaulted to 0.00.")
        return total_amount, warnings

    financial_summary = get_work_order_invoice_financial_summary(work_order_id)
    amount_still_to_invoice = round(float(financial_summary.get("AmountStillToInvoice") or 0.0), 2)
    if amount_still_to_invoice < 0:
        warnings.append("Amount still to invoice was negative, so the draft invoice amount defaulted to 0.00.")
        return 0.0, warnings
    if amount_still_to_invoice == 0:
        warnings.append("No remaining stipulated invoice amount was available, so the draft invoice amount defaulted to 0.00.")
    return amount_still_to_invoice, warnings


def create_customer_invoice_draft_safe(
    *,
    work_order_id: int,
    scope_of_work: str | None = None,
    billing_mode: str | None = None,
    invoice_amount: float | None = None,
    created_by: str = "automation_apply",
    source_tag: str = "customer_invoice_draft_wrapper",
):
    ensure_invoice_schema()
    header = get_invoice_header_data(int(work_order_id))
    if not header:
        raise ValueError(f"Work Order #{work_order_id} could not be loaded for safe customer invoice draft creation.")

    customer_name = str(header.get("CustomerName") or "").strip()
    site_name = str(header.get("SiteName") or "").strip()
    site_address = str(header.get("SiteAddress") or "").strip()
    if not customer_name:
        raise ValueError("Safe customer invoice draft creation requires a resolved Customer context.")
    if not (site_name or site_address):
        raise ValueError("Safe customer invoice draft creation requires a resolved Site context.")

    normalized_mode = _normalize_invoice_billing_mode(billing_mode or header.get("BillingMode") or header.get("BillingType"))
    normalized_scope = str(scope_of_work or header.get("ScopeOfWork") or header.get("Scope") or "").strip()
    if not normalized_scope:
        normalized_scope = f"Work Order #{int(work_order_id)}"

    warnings: list[str] = []
    if invoice_amount is None:
        normalized_amount, amount_warnings = _derive_safe_customer_invoice_draft_amount(int(work_order_id), normalized_mode)
        warnings.extend(amount_warnings)
    else:
        normalized_amount = round(float(invoice_amount or 0.0), 2)
        if normalized_amount < 0:
            raise ValueError("Safe customer invoice draft creation does not allow a negative invoice amount.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        draft_rows = _list_draft_invoices_for_workorder(cur, int(work_order_id))
        if len(draft_rows) > 1:
            raise ValueError(
                f"Work Order #{work_order_id} already has multiple Draft customer invoices; safe draft wrapper cannot pick one deterministically."
            )

        draft_reused = False
        if draft_rows:
            draft_row = draft_rows[0]
            if _draft_invoice_has_export_or_send_evidence(draft_row):
                raise ValueError(
                    f"Draft customer invoice #{draft_row['CustomerInvoiceId']} already has export/send evidence, so safe draft wrapper will not reuse it."
                )
            draft_reused = True
            invoice_row = draft_row
            warnings.append(f"Reused existing Draft customer invoice #{draft_row['CustomerInvoiceId']} for Work Order #{work_order_id}.")
        else:
            cur.execute(
                '''
                INSERT INTO "Invoice"
                (
                    "WorkOrderID",
                    "CustomerInvoiceAmount",
                    "InvoiceStatus",
                    "LaborPercent",
                    "MaterialPercent",
                    "LaborMilestoneNote",
                    "MaterialMilestoneNote",
                    "ScopeOfWork",
                    "BillingMode",
                    "CustomerInvoiceDate"
                )
                VALUES (%s, %s, 'Draft', %s, %s, %s, %s, %s, %s, CURRENT_DATE)
                RETURNING *
                ''',
                (
                    int(work_order_id),
                    normalized_amount,
                    0.0,
                    0.0,
                    "",
                    "",
                    normalized_scope,
                    normalized_mode,
                ),
            )
            invoice_row = cur.fetchone()

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    invoice_id = int(invoice_row["CustomerInvoiceId"])
    memory_payload = dict(get_customer_invoice_memory(invoice_id) or {})
    memory_payload.update(
        {
            "invoice_id": invoice_id,
            "wo_id": int(work_order_id),
            "mode": normalized_mode,
            "scope": normalized_scope,
            "review_summary": "Draft header-only customer invoice shell created without export, send, or line linking.",
            "draft_origin": "safe_customer_invoice_draft_wrapper",
            "created_by": str(created_by or "automation_apply").strip() or "automation_apply",
            "source_tag": str(source_tag or "customer_invoice_draft_wrapper").strip() or "customer_invoice_draft_wrapper",
            "line_linking_deferred": True,
            "document_generation_deferred": True,
            "last_saved_at": datetime.datetime.now().isoformat(),
        }
    )
    upsert_customer_invoice_memory(invoice_id, memory_payload)
    return {
        "invoice": invoice_row,
        "draft_reused": draft_reused,
        "invoice_number": str(invoice_id),
        "warnings": warnings,
        "memory": memory_payload,
    }


def lock_and_export_invoice(wo_id: int, doc_path: str = None):
    ensure_invoice_schema()
    draft = _fetch_latest_invoice(wo_id, statuses=["Draft"])
    if not draft:
        raise ValueError("No draft invoice exists for this work order.")

    memory = get_customer_invoice_memory(draft["CustomerInvoiceId"])
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            UPDATE "Invoice"
            SET "InvoiceStatus" = 'Exported',
                "CustomerInvoiceDocPath" = %s
            WHERE "CustomerInvoiceId" = %s
            ''',
            (doc_path, draft["CustomerInvoiceId"]),
        )
        for row in memory.get("labor_lines", []):
            time_id = row.get("TimeID")
            if time_id:
                cur.execute('UPDATE "Time" SET "CustomerInvoiceId" = %s WHERE "TimeID" = %s', (draft["CustomerInvoiceId"], time_id))
        for row in memory.get("material_lines", []):
            po_item_id = row.get("POItemID")
            if po_item_id:
                cur.execute('UPDATE "PurchaseOrderItem" SET "CustomerInvoiceId" = %s WHERE "POItemID" = %s', (draft["CustomerInvoiceId"], po_item_id))
        conn.commit()
    finally:
        conn.close()
    return draft["CustomerInvoiceId"]


def mark_invoice_sent(invoice_id: int, doc_path: str = None):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            UPDATE "Invoice"
            SET "InvoiceStatus" = 'Sent',
                "CustomerInvoiceSentAt" = NOW(),
                "CustomerInvoiceDocPath" = COALESCE(%s, "CustomerInvoiceDocPath")
            WHERE "CustomerInvoiceId" = %s
            ''',
            (doc_path, invoice_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_invoice_paid(invoice_id: int, manual_override: bool = False):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                "CustomerInvoiceId",
                COALESCE("InvoiceStatus", '') AS "InvoiceStatus",
                "CustomerInvoiceDocPath",
                "CustomerInvoiceSentAt"
            FROM "Invoice"
            WHERE "CustomerInvoiceId" = %s
            ''',
            (invoice_id,),
        )
        invoice = cur.fetchone()
        if not invoice:
            raise ValueError(f"Invoice #{invoice_id} could not be found.")
        if invoice["InvoiceStatus"] == "Paid":
            return invoice
        if not manual_override:
            if invoice["InvoiceStatus"] != "Sent" or not invoice.get("CustomerInvoiceSentAt") or not invoice.get("CustomerInvoiceDocPath"):
                raise ValueError("Invoice cannot be marked paid until it has been sent.")
        cur.execute(
            '''
            UPDATE "Invoice"
            SET "InvoiceStatus" = 'Paid',
                "InvDatePaid" = CURRENT_DATE::text
            WHERE "CustomerInvoiceId" = %s
            RETURNING *
            ''',
            (invoice_id,),
        )
        updated = cur.fetchone()
        conn.commit()
        return updated
    finally:
        conn.close()


def get_unbilled_workorders():
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                wo."WorkOrderID",
                c."CustomerName",
                s."SiteName",
                wo."BillingType",
                COALESCE(NULLIF(TRIM(wo."JobStatus"), ''), CASE WHEN COALESCE(wo."IsClosed", FALSE) THEN 'Closed' ELSE 'Open' END) AS "JobStatus",
                EXISTS (
                    SELECT 1 FROM "Invoice" i
                    WHERE i."WorkOrderID" = wo."WorkOrderID" AND i."InvoiceStatus" = 'Draft'
                ) AS "HasDraft"
            FROM "WorkOrder" wo
            JOIN "Site" s ON wo."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE COALESCE(wo."IsClosed", FALSE) = FALSE
              AND UPPER(REPLACE(REPLACE(COALESCE(TRIM(wo."JobStatus"), ''), '_', ''), ' ', '')) NOT IN ('DRAFT', 'PENDINGSTART')
            ORDER BY wo."WorkOrderID" DESC
            ''',
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        mode = str(row.get("BillingType") or "")
        has_draft = bool(row.get("HasDraft"))
        has_unbilled_tm = bool(get_unbilled_labor(row["WorkOrderID"]) or get_unbilled_materials(row["WorkOrderID"]))
        has_remaining_stipulated = get_total_stipulated_percent(row["WorkOrderID"]) < 100.0
        include = has_draft or has_unbilled_tm or ("fixed" in mode.lower() and has_remaining_stipulated)
        if include:
            results.append(row)
    return results


def get_invoice_pipeline_candidates():
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            WITH latest_invoice AS (
                SELECT DISTINCT ON (i."WorkOrderID")
                    i."WorkOrderID",
                    i."CustomerInvoiceId",
                    COALESCE(i."InvoiceStatus", '') AS "InvoiceStatus",
                    i."CustomerInvoiceAmount",
                    i."CustomerInvoiceDate",
                    i."CustomerInvoiceDocPath"
                FROM "Invoice" i
                WHERE COALESCE(i."InvoiceStatus", '') != 'Retired'
                ORDER BY i."WorkOrderID", i."CustomerInvoiceId" DESC
            ),
            latest_document_draft AS (
                SELECT DISTINCT ON (d."CustomerInvoiceId")
                    d."CustomerInvoiceId",
                    d."CustomerInvoiceDocumentDraftID",
                    COALESCE(d."DraftStatus", '') AS "DraftStatus",
                    d."FinalFilePath",
                    d."IsActive"
                FROM public."CustomerInvoiceDocumentDraft" d
                WHERE d."IsActive" = 1
                  AND COALESCE(d."DraftStatus", '') != 'Retired'
                ORDER BY d."CustomerInvoiceId", d."VersionNumber" DESC, d."CustomerInvoiceDocumentDraftID" DESC
            )
            SELECT
                wo."WorkOrderID",
                COALESCE(wo."IsClosed", FALSE) AS "IsClosed",
                COALESCE(NULLIF(TRIM(wo."JobStatus"), ''), CASE WHEN COALESCE(wo."IsClosed", FALSE) THEN 'Closed' ELSE 'Open' END) AS "JobStatus",
                wo."BillingType",
                c."CustomerName",
                s."SiteName",
                li."CustomerInvoiceId",
                li."InvoiceStatus",
                li."CustomerInvoiceAmount",
                li."CustomerInvoiceDate",
                li."CustomerInvoiceDocPath",
                ld."CustomerInvoiceDocumentDraftID",
                ld."DraftStatus" AS "DocumentDraftStatus",
                ld."FinalFilePath"
            FROM "WorkOrder" wo
            JOIN "Site" s ON wo."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            LEFT JOIN latest_invoice li
                ON li."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN latest_document_draft ld
                ON ld."CustomerInvoiceId" = li."CustomerInvoiceId"
            ORDER BY wo."WorkOrderID" DESC
            '''
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    included: list[dict] = []
    excluded: list[dict[str, object]] = []

    for row in rows:
        work_order_id = int(row["WorkOrderID"])
        is_closed = bool(row.get("IsClosed"))
        job_status = str(row.get("JobStatus") or "").strip()
        customer_invoice_id = row.get("CustomerInvoiceId")
        invoice_status = str(row.get("InvoiceStatus") or "").strip()
        draft_id = row.get("CustomerInvoiceDocumentDraftID")
        draft_status = str(row.get("DocumentDraftStatus") or "").strip()
        has_invoice = customer_invoice_id is not None
        has_document_draft = draft_id is not None
        has_exported_draft_file = bool(str(row.get("FinalFilePath") or "").strip())
        has_legacy_doc_path = bool(str(row.get("CustomerInvoiceDocPath") or "").strip())

        include = ((not is_closed) and not _is_non_operational_work_order_status(job_status)) or has_invoice or has_document_draft
        if not include:
            excluded.append(
                {
                    "WorkOrderID": work_order_id,
                    "reason": "non-operational work order or closed with no invoice/document activity",
                }
            )
            continue

        if not has_invoice:
            invoice_state = "No invoice record"
        elif invoice_status:
            invoice_state = invoice_status
        else:
            invoice_state = "Needs attention"

        row["WorkOrderStatus"] = job_status or ("CLOSED" if is_closed else "OPEN")

        if not has_invoice:
            document_state = "Create/save invoice data first"
        elif has_document_draft:
            if draft_status == "Locked" and has_exported_draft_file:
                document_state = "Document locked / exported"
            elif draft_status == "Locked":
                document_state = "Document locked"
            elif draft_status == "Sent":
                document_state = "Document sent"
            elif draft_status == "Draft":
                document_state = "Invoice document draft exists"
            else:
                document_state = draft_status or "Invoice document draft exists"
        elif has_legacy_doc_path:
            document_state = "Legacy exported only"
        else:
            document_state = "No document draft"

        row["InvoiceState"] = invoice_state
        row["DocumentState"] = document_state
        included.append(row)

    print(
        "[InvoicePipeline] "
        f"total_work_orders_considered={len(rows)} | "
        f"rows_included={len(included)} | "
        f"rows_excluded={len(excluded)}"
    )
    if excluded:
        excluded_preview = ", ".join(
            f"WO #{item['WorkOrderID']}: {item['reason']}" for item in excluded[:10]
        )
        print(f"[InvoicePipeline] excluded_rows_preview={excluded_preview}")

    return included


def list_invoices_and_drafts_for_work_order(wo_id: int):
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            WITH active_document_drafts AS (
                SELECT DISTINCT ON (d."CustomerInvoiceId")
                    d."CustomerInvoiceId",
                    d."CustomerInvoiceDocumentDraftID",
                    COALESCE(d."DraftStatus", '') AS "DocumentDraftStatus",
                    d."FinalFilePath",
                    d."SentAt"
                FROM public."CustomerInvoiceDocumentDraft" d
                WHERE d."IsActive" = 1
                  AND COALESCE(d."DraftStatus", '') != 'Retired'
                ORDER BY d."CustomerInvoiceId", d."VersionNumber" DESC, d."CustomerInvoiceDocumentDraftID" DESC
            )
            SELECT
                i."CustomerInvoiceId",
                i."WorkOrderID",
                COALESCE(i."InvoiceStatus", '') AS "InvoiceStatus",
                i."CustomerInvoiceAmount",
                i."CustomerInvoiceDate",
                i."CustomerInvoiceDocPath",
                i."CustomerInvoiceSentAt",
                d."CustomerInvoiceDocumentDraftID",
                COALESCE(d."DocumentDraftStatus", '') AS "DocumentDraftStatus",
                d."FinalFilePath",
                d."SentAt" AS "DocumentDraftSentAt"
            FROM "Invoice" i
            LEFT JOIN active_document_drafts d
                ON d."CustomerInvoiceId" = i."CustomerInvoiceId"
            WHERE i."WorkOrderID" = %s
              AND COALESCE(i."InvoiceStatus", '') != 'Retired'
            ORDER BY i."CustomerInvoiceId" DESC
            ''',
            (wo_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        invoice_status = str(row.get("InvoiceStatus") or "").strip()
        document_draft_status = str(row.get("DocumentDraftStatus") or "").strip()
        legacy_doc_path = str(row.get("CustomerInvoiceDocPath") or "").strip()
        final_file_path = str(row.get("FinalFilePath") or "").strip()
        has_export = bool(final_file_path or legacy_doc_path)
        sent_marker = row.get("DocumentDraftSentAt") or row.get("CustomerInvoiceSentAt")

        if document_draft_status:
            document_state = document_draft_status
        elif legacy_doc_path:
            document_state = "Legacy exported only"
        else:
            document_state = "No document draft"

        row["DocumentState"] = document_state
        row["HasExport"] = "Yes" if has_export else "No"
        row["SentStatus"] = "Sent" if sent_marker or invoice_status in {"Sent", "Paid"} else ""
        results.append(row)

    print(
        "[InvoicePipelineDetail] "
        f"WorkOrderID={wo_id} | invoice_rows_loaded={len(results)}"
    )
    return results


def get_work_order_invoice_financial_summary(work_order_id: int) -> dict:
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                wo."WorkOrderID",
                wo."SourceEstimateID",
                wo."BillingType",
                e."TotalAmount" AS "EstimateTotal"
            FROM "WorkOrder" wo
            LEFT JOIN "Estimate" e ON e."EstimateID" = wo."SourceEstimateID"
            WHERE wo."WorkOrderID" = %s
            ''',
            (work_order_id,),
        )
        work_order_row = cur.fetchone()
        if not work_order_row:
            raise ValueError(f"Work Order #{work_order_id} could not be found.")

        cur.execute(
            '''
            SELECT
                COALESCE(SUM("CustomerInvoiceAmount"::numeric), 0) AS "PreviouslyInvoicedAmount",
                COUNT(*) FILTER (WHERE COALESCE("InvoiceStatus", '') = 'Draft') AS "DraftInvoiceCount"
            FROM "Invoice"
            WHERE "WorkOrderID" = %s
              AND COALESCE("InvoiceStatus", '') IN ('Exported', 'Sent', 'Paid', 'Draft')
            ''',
            (work_order_id,),
        )
        invoice_totals = cur.fetchone() or {}

        cur.execute(
            '''
            SELECT
                COALESCE(
                    SUM(
                        COALESCE(t."HoursWorked", 0) *
                        (
                            COALESCE(NULLIF(t."HourlyRate", 0), NULLIF(e."EmployeeRate", 0), 0) *
                            (1 + (COALESCE(e."EmployeeBurden", 0) / 100.0))
                        )
                    ),
                    0
                ) AS "LabourCostToDate"
            FROM "Time" t
            LEFT JOIN "Employee" e ON t."WorkerID" = e."EmployeeID"
            WHERE t."WorkOrderID" = %s
              AND COALESCE(t."Status", '') != 'Retired'
            ''',
            (work_order_id,),
        )
        labour_totals = cur.fetchone() or {}

        cur.execute(
            '''
            SELECT
                COALESCE(SUM(COALESCE(po."PurchaseOrderTotal", 0)::numeric), 0) AS "ApprovedPurchaseOrderCost"
            FROM "PurchaseOrder" po
            WHERE po."WorkOrderID" = %s
              AND COALESCE(po."Status", '') NOT IN ('Draft', 'Retired')
            ''',
            (work_order_id,),
        )
        po_totals = cur.fetchone() or {}
    finally:
        conn.close()

    total_estimate_amount = float(work_order_row.get("EstimateTotal") or 0)
    previously_invoiced_amount = float(invoice_totals.get("PreviouslyInvoicedAmount") or 0)
    labour_cost_to_date = float(labour_totals.get("LabourCostToDate") or 0)
    approved_purchase_order_cost = float(po_totals.get("ApprovedPurchaseOrderCost") or 0)
    committed_cost = labour_cost_to_date + approved_purchase_order_cost
    amount_still_to_invoice = total_estimate_amount - previously_invoiced_amount
    billing_position_amount = previously_invoiced_amount - committed_cost
    estimate_position_amount = total_estimate_amount - committed_cost

    notes: list[str] = []
    if not work_order_row.get("SourceEstimateID"):
        notes.append("No source estimate is linked to this work order.")
    if total_estimate_amount <= 0:
        notes.append("Total Estimate is unavailable, so estimate-based positions may be incomplete.")

    draft_invoice_count = int(invoice_totals.get("DraftInvoiceCount") or 0)
    if draft_invoice_count:
        notes.append(
            f"Draft invoices are excluded from Previously Invoiced ({draft_invoice_count} draft invoice(s) found)."
        )

    notes.append(
        "Committed Cost includes labour cost to date plus purchase orders whose status is not Draft or Retired."
    )

    return {
        "WorkOrderID": int(work_order_row["WorkOrderID"]),
        "SourceEstimateID": work_order_row.get("SourceEstimateID"),
        "TotalEstimateAmount": total_estimate_amount,
        "PreviouslyInvoicedAmount": previously_invoiced_amount,
        "AmountStillToInvoice": amount_still_to_invoice,
        "LabourCostToDate": labour_cost_to_date,
        "ApprovedPurchaseOrderCost": approved_purchase_order_cost,
        "CommittedCost": committed_cost,
        "BillingPositionAmount": billing_position_amount,
        "BillingPositionLabel": "In the Black" if billing_position_amount >= 0 else "In the Red",
        "EstimatePositionAmount": estimate_position_amount,
        "EstimatePositionLabel": "Within Estimate" if estimate_position_amount >= 0 else "Over Estimate",
        "Notes": notes,
    }


def get_accounts_receivable():
    ensure_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                i."CustomerInvoiceId",
                i."CustomerInvoiceDate",
                i."WorkOrderID",
                i."CustomerInvoiceAmount",
                i."InvoiceStatus",
                c."CustomerName"
            FROM "Invoice" i
            JOIN "WorkOrder" wo ON i."WorkOrderID" = wo."WorkOrderID"
            JOIN "Site" s ON wo."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE i."InvoiceStatus" != 'Retired'
            ORDER BY i."CustomerInvoiceId" DESC
            '''
        )
        return cur.fetchall()
    finally:
        conn.close()
