import datetime
import json
import os
from decimal import Decimal

from psycopg2.extras import Json, RealDictCursor

from neon_ai.database.connection import get_connection


CUSTOMER_INVOICE_MEMORY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "customer_invoice_memory.json")
_INVOICE_SCHEMA_READY = False


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
                EXISTS (
                    SELECT 1 FROM "Invoice" i
                    WHERE i."WorkOrderID" = wo."WorkOrderID" AND i."InvoiceStatus" = 'Draft'
                ) AS "HasDraft"
            FROM "WorkOrder" wo
            JOIN "Site" s ON wo."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE COALESCE(wo."IsClosed", FALSE) = FALSE
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
