import datetime
import json
import os
import re
import time
from decimal import Decimal

from docx import Document
from psycopg2.extras import Json, RealDictCursor

from neon_ai.database.connection import get_connection
from neon_ai.database.folders import get_target_folder
from neon_ai.database.purchases import (
    AUTOMATION_MIN_DATE,
    _extract_numeric_values,
    _match_po_item_to_line,
    ensure_purchase_schema,
    get_po_export_data,
    get_purchase_order_receipt_choices,
    get_purchase_order_receipt_quantities,
    update_purchase_order_receiving_status,
)
from neon_ai.database.rfq import extract_text_from_attachment, ocr_pdf_attachment

VENDOR_INVOICE_MEMORY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor_invoice_memory.json")
_VENDOR_INVOICE_SCHEMA_READY = False


def _perf_log(area: str, name: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}")


def ensure_vendor_invoice_schema():
    global _VENDOR_INVOICE_SCHEMA_READY
    if _VENDOR_INVOICE_SCHEMA_READY:
        return
    ensure_purchase_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('ALTER TABLE "VendorInvoice" ADD COLUMN IF NOT EXISTS "VendorInvoicePaidDate" date')
        cur.execute('ALTER TABLE "VendorInvoice" ADD COLUMN IF NOT EXISTS "VendorInvoiceMemory" jsonb')
        conn.commit()
        _VENDOR_INVOICE_SCHEMA_READY = True
    finally:
        conn.close()


def _load_legacy_vendor_invoice_memory():
    if not os.path.exists(VENDOR_INVOICE_MEMORY_PATH):
        return []
    try:
        with open(VENDOR_INVOICE_MEMORY_PATH, "r", encoding="utf-8") as handle:
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


def _decode_vendor_invoice_memory(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _read_vendor_invoice_memory_from_db(invoice_id: int):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            'SELECT "VendorInvoiceMemory" FROM "VendorInvoice" WHERE "VendorInvoiceID" = %s',
            (invoice_id,),
        )
        row = cur.fetchone() or {}
        return _decode_vendor_invoice_memory(row.get("VendorInvoiceMemory"))
    finally:
        conn.close()


def _write_vendor_invoice_memory_to_db(invoice_id: int, payload: dict):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            UPDATE "VendorInvoice"
            SET "VendorInvoiceMemory" = %s
            WHERE "VendorInvoiceID" = %s
            ''',
            (Json(_json_db_ready(payload or {})), invoice_id),
        )
        conn.commit()
    finally:
        conn.close()


def load_vendor_invoice_memory():
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT "VendorInvoiceID", "VendorInvoiceMemory"
            FROM "VendorInvoice"
            WHERE "VendorInvoiceMemory" IS NOT NULL
            ORDER BY "VendorInvoiceID" ASC
            '''
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    entries = []
    for row in rows:
        payload = _decode_vendor_invoice_memory(row.get("VendorInvoiceMemory"))
        if payload:
            payload.setdefault("vendor_invoice_id", row.get("VendorInvoiceID"))
            entries.append(payload)
    return entries or _load_legacy_vendor_invoice_memory()


def save_vendor_invoice_memory(entries):
    for entry in entries or []:
        invoice_id = int(entry.get("vendor_invoice_id") or 0)
        if invoice_id:
            _write_vendor_invoice_memory_to_db(invoice_id, entry)


def upsert_vendor_invoice_memory(invoice_id: int, updates: dict):
    entry = dict(get_vendor_invoice_memory(invoice_id) or {})
    entry["vendor_invoice_id"] = int(invoice_id)
    entry.update(_json_db_ready(updates or {}))
    _write_vendor_invoice_memory_to_db(invoice_id, entry)
    return entry


def get_vendor_invoice_memory(invoice_id: int):
    payload = _read_vendor_invoice_memory_from_db(invoice_id)
    if payload:
        payload.setdefault("vendor_invoice_id", int(invoice_id))
        return payload

    legacy = next((row for row in _load_legacy_vendor_invoice_memory() if int(row.get("vendor_invoice_id") or 0) == int(invoice_id)), {})
    if legacy:
        legacy = _json_db_ready(dict(legacy))
        legacy["vendor_invoice_id"] = int(invoice_id)
        _write_vendor_invoice_memory_to_db(invoice_id, legacy)
    return legacy


def _parse_invoice_date(text: str, reference_date=None):
    reference_date = reference_date or datetime.date.today()
    if not text:
        return None

    iso_match = re.search(r'\b(20\d{2})-(\d{1,2})-(\d{1,2})\b', text)
    if iso_match:
        y, m, d = map(int, iso_match.groups())
        return datetime.date(y, m, d)

    slash_match = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2}|\d{2})\b', text)
    if slash_match:
        m, d, y = slash_match.groups()
        year = int(y)
        year = year + 2000 if year < 100 else year
        return datetime.date(year, int(m), int(d))

    month_match = re.search(r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(20\d{2}))?', text, re.IGNORECASE)
    if month_match:
        month_name, day_value, year_value = month_match.groups()
        month = datetime.datetime.strptime(month_name.title(), "%B").month
        year = int(year_value) if year_value else reference_date.year
        return datetime.date(year, month, int(day_value))

    return None


def extract_invoice_header_fields(subject: str, body: str, attachment_text: str = "", reference_date=None):
    combined = "\n".join(part for part in [subject, body, attachment_text] if part)
    invoice_number = None
    amount = None
    invoice_date = _parse_invoice_date(combined, reference_date=reference_date)

    inv_match = re.search(r'\b(?:invoice|inv)\s*#?\s*([A-Za-z0-9._/-]+)\b', combined, re.IGNORECASE)
    if inv_match:
        invoice_number = inv_match.group(1)[:80]

    total_patterns = [
        r'\btotal(?: due)?[:\s$]*([\d,]+\.\d{2})',
        r'\bbalance(?: due)?[:\s$]*([\d,]+\.\d{2})',
        r'\binvoice amount[:\s$]*([\d,]+\.\d{2})',
    ]
    for pattern in total_patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            amount = float(match.group(1).replace(",", ""))
            break

    if amount is None:
        numeric_values = _extract_numeric_values(combined)
        money_like = [val for val in numeric_values if val >= 1]
        if money_like:
            amount = max(money_like)

    return {
        "invoice_number": invoice_number or "",
        "invoice_date": invoice_date.isoformat() if invoice_date else (reference_date.isoformat() if reference_date else datetime.date.today().isoformat()),
        "amount": amount or 0.0,
    }


def _get_po_item_rows(po_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                poi."POItemID",
                poi."PurchaseOrderID",
                poi."MaterialID",
                poi."CatalogItemID",
                COALESCE(poi."Description", em."Description", m."Description") AS "Description",
                poi."QuantityOrdered",
                COALESCE(poi."QuantityReceived", 0) AS "QuantityReceived",
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
                ) AS "POUnitPrice",
                COALESCE(
                    NULLIF(poi."LineTotal", 0),
                    COALESCE(poi."QuantityOrdered", 0) * COALESCE(
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
                    ),
                    0
                ) AS "CurrentLineTotal",
                COALESCE(
                    em."UnitCost",
                    CASE COALESCE(m."CarryPriceSource", 'Internal')
                        WHEN 'Nedco' THEN m."NedcoPrice"
                        WHEN 'Gescan' THEN m."GescanPrice"
                        WHEN 'Eecol' THEN m."EecolPrice"
                        WHEN 'Guillevin' THEN m."GuillevinPrice"
                        ELSE m."InternalPrice"
                    END,
                    0
                ) AS "EstimateUnitPrice",
                (COALESCE(poi."QuantityOrdered", 0) - COALESCE(poi."QuantityReceived", 0)) AS "BackorderedQty"
            FROM "PurchaseOrderItem" poi
            LEFT JOIN "EstimateMaterial" em ON poi."MaterialID" = em."EstimateMaterialID"
            LEFT JOIN "Material" m ON poi."CatalogItemID" = m."ItemID"
            WHERE poi."PurchaseOrderID" = %s
            ORDER BY poi."POItemID" ASC
            ''',
            (po_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def parse_vendor_invoice_lines(po_id: int, text: str):
    po_items = _get_po_item_rows(po_id)
    if not po_items:
        return []

    parsed = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        item = _match_po_item_to_line(line, po_items)
        if not item:
            continue

        numbers = _extract_numeric_values(line)
        qty = numbers[0] if numbers else float(item.get("QuantityReceived") or item.get("QuantityOrdered") or 0)
        unit_price = numbers[-1] if len(numbers) >= 2 else float(item.get("POUnitPrice") or 0)
        parsed.append(
            {
                "po_item_id": item["POItemID"],
                "description": item.get("Description"),
                "invoice_qty": qty,
                "invoice_unit_price": unit_price,
                "invoice_line_total": qty * unit_price,
                "source_line": line[:255],
            }
        )

    deduped = {}
    for row in parsed:
        deduped[row["po_item_id"]] = row
    return list(deduped.values())


def find_matching_po_for_vendor_invoice(sender_email: str, subject: str = "", body: str = "", attachment_text: str = ""):
    combined = "\n".join(part for part in [subject, body, attachment_text] if part)
    if "invoice" not in combined.lower() and "statement" not in combined.lower() and "bill" not in combined.lower():
        return None

    po_match = re.search(r'\bpo\s*#?\s*(\d+)\b', combined, re.IGNORECASE)
    if po_match:
        return int(po_match.group(1))

    sender = (sender_email or "").strip().lower()
    if not sender:
        return None

    from neon_ai.database.purchases import load_po_dispatch_log

    matches = [row for row in load_po_dispatch_log() if (row.get("recipient_email") or "").strip().lower() == sender]
    if not matches:
        return None
    matches.sort(key=lambda row: row.get("sent_at", ""), reverse=True)
    return matches[0].get("po_id")


def _get_latest_vendor_invoice_for_po(po_id: int, statuses=None):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = '''
            SELECT *
            FROM "VendorInvoice"
            WHERE "PurchaseOrderID" = %s
        '''
        params = [po_id]
        if statuses:
            sql += ' AND "VendorInvoiceStatus" = ANY(%s)'
            params.append(statuses)
        sql += ' ORDER BY "VendorInvoiceID" DESC LIMIT 1'
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        conn.close()


def _find_vendor_invoice_by_number(po_id: int, invoice_number: str):
    if not invoice_number:
        return None
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT *
            FROM "VendorInvoice"
            WHERE "PurchaseOrderID" = %s
              AND LOWER(TRIM(COALESCE("VendorInvoiceNumber", ''))) = LOWER(TRIM(COALESCE(%s, '')))
            ORDER BY "VendorInvoiceID" DESC
            LIMIT 1
            ''',
            (po_id, invoice_number),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _next_vendor_invoice_number(po_id: int):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT COUNT(*) AS "InvoiceCount"
            FROM "VendorInvoice"
            WHERE "PurchaseOrderID" = %s
            ''',
            (po_id,),
        )
        row = cur.fetchone() or {}
    finally:
        conn.close()
    return f"DRAFT-PO-{po_id}-{int(row.get('InvoiceCount') or 0) + 1}"


def _upsert_vendor_invoice_row(invoice_id: int = None, po_id: int = None, invoice_number: str = "", invoice_date: str = "", due_date: str = "", amount: float = 0.0, description: str = "", status: str = "Draft"):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if invoice_id:
            cur.execute(
                '''
                UPDATE "VendorInvoice"
                SET "VendorInvoiceNumber" = %s,
                    "VendorInvoiceDate" = %s,
                    "VendorInvoiceDueDate" = %s,
                    "VendorInvoiceAmount" = %s,
                    "VendorInvoiceStatus" = %s,
                    "Description" = %s
                WHERE "VendorInvoiceID" = %s
                RETURNING *
                ''',
                (invoice_number, invoice_date, due_date or None, amount, status, description, invoice_id),
            )
            row = cur.fetchone()
        else:
            cur.execute(
                '''
                INSERT INTO "VendorInvoice"
                ("VendorInvoiceNumber", "PurchaseOrderID", "VendorInvoiceDate", "VendorInvoiceDueDate", "VendorInvoiceAmount", "VendorInvoiceStatus", "Description")
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                ''',
                (invoice_number or f"DRAFT-PO-{po_id}", po_id, invoice_date or datetime.date.today().isoformat(), due_date or None, amount, status, description),
            )
            row = cur.fetchone()
        conn.commit()
        return row
    finally:
        conn.close()


def get_purchase_order_invoice_rollup(po_id: int, exclude_invoice_id: int = None):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT COALESCE("PurchaseOrderTotal", 0) AS "PurchaseOrderTotal"
            FROM "PurchaseOrder"
            WHERE "PurchaseOrderID" = %s
            ''',
            (po_id,),
        )
        po_row = cur.fetchone() or {}

        sql = '''
            SELECT COALESCE(SUM(COALESCE("VendorInvoiceAmount", 0)), 0) AS "InvoicedTotal"
            FROM "VendorInvoice"
            WHERE "PurchaseOrderID" = %s
        '''
        params = [po_id]
        if exclude_invoice_id:
            sql += ' AND "VendorInvoiceID" != %s'
            params.append(exclude_invoice_id)
        cur.execute(sql, params)
        invoice_row = cur.fetchone() or {}
    finally:
        conn.close()

    po_total = float(po_row.get("PurchaseOrderTotal") or 0)
    invoiced_total = float(invoice_row.get("InvoicedTotal") or 0)
    return {
        "po_total": po_total,
        "invoiced_total": invoiced_total,
        "remaining_total": po_total - invoiced_total,
    }


def _get_vendor_invoice_item_rollup(po_id: int, exclude_invoice_id: int = None):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = '''
            SELECT "VendorInvoiceID"
            FROM "VendorInvoice"
            WHERE "PurchaseOrderID" = %s
        '''
        params = [po_id]
        if exclude_invoice_id:
            sql += ' AND "VendorInvoiceID" != %s'
            params.append(exclude_invoice_id)
        cur.execute(sql, params)
        invoice_rows = cur.fetchall()
    finally:
        conn.close()

    totals = {}
    for invoice_row in invoice_rows:
        memory = get_vendor_invoice_memory(int(invoice_row["VendorInvoiceID"]))
        for row in memory.get("rows", []):
            po_item_id = int(row.get("po_item_id") or 0)
            if not po_item_id:
                continue
            totals[po_item_id] = totals.get(po_item_id, 0.0) + float(row.get("invoiced_qty") or 0)
    return totals


def _resolve_receipt_choice(po_id: int, receipt_key: str = None):
    choices = get_purchase_order_receipt_choices(po_id)
    choice_map = {row["Key"]: row for row in choices}
    if receipt_key and receipt_key in choice_map:
        return choice_map[receipt_key]
    for row in choices:
        if row["Key"] != "all_received":
            return row
    return choice_map.get("all_received")


def create_vendor_invoice_draft(po_id: int, receipt_key: str = None):
    choice = _resolve_receipt_choice(po_id, receipt_key)
    invoice = _upsert_vendor_invoice_row(
        po_id=po_id,
        invoice_number=_next_vendor_invoice_number(po_id),
        invoice_date=datetime.date.today().isoformat(),
        amount=0.0,
        description=f"Draft vendor invoice created from {choice.get('Label') if choice else 'Payables workspace'}.",
        status="Draft",
    )
    rows = _build_default_invoice_rows(po_id, invoice["VendorInvoiceID"], choice["Key"] if choice else "all_received")
    upsert_vendor_invoice_memory(
        invoice["VendorInvoiceID"],
        {
            "po_id": po_id,
            "receipt_key": choice["Key"] if choice else "all_received",
            "receipt_label": choice.get("Label") if choice else "All received material on this PO",
            "receipt_ref": choice.get("DocumentRef") if choice else "All received material",
            "receipt_date": choice.get("ReceiveDate") if choice else "",
            "rows": rows,
            "last_updated_at": datetime.datetime.now().isoformat(),
        },
    )
    return invoice


def get_or_create_vendor_invoice_draft(po_id: int):
    existing = _get_latest_vendor_invoice_for_po(po_id, statuses=["Draft"])
    if existing:
        return existing
    return create_vendor_invoice_draft(po_id, receipt_key="all_received")


def _build_default_invoice_rows(po_id: int, invoice_id: int = None, receipt_key: str = None):
    memory = get_vendor_invoice_memory(invoice_id) if invoice_id else {}
    active_receipt_key = receipt_key or memory.get("receipt_key") or "all_received"
    receipt_qty_map = get_purchase_order_receipt_quantities(po_id, active_receipt_key)
    prior_invoiced_qty_map = _get_vendor_invoice_item_rollup(po_id, exclude_invoice_id=invoice_id)
    line_map = {row.get("po_item_id"): row for row in memory.get("rows", memory.get("parsed_lines", []))}
    rows = []
    for po_item in _get_po_item_rows(po_id):
        saved = line_map.get(po_item["POItemID"], {})
        delivered_qty = float(receipt_qty_map.get(int(po_item["POItemID"]), 0.0))
        if active_receipt_key == "all_received" and not receipt_qty_map:
            delivered_qty = float(po_item.get("QuantityReceived") or 0)
        previously_invoiced_qty = float(prior_invoiced_qty_map.get(int(po_item["POItemID"]), 0.0))
        default_invoice_qty = max(0.0, delivered_qty - previously_invoiced_qty)
        invoiced_qty = float(saved.get("invoice_qty", default_invoice_qty))
        invoice_price = float(saved.get("invoice_unit_price", po_item.get("POUnitPrice") or 0))
        subtotal = float(saved.get("invoice_line_total", invoiced_qty * invoice_price))
        match_status = "OK"
        if invoiced_qty > delivered_qty + 0.001:
            match_status = "Review"
        if abs(invoice_price - float(po_item.get("POUnitPrice") or 0)) > 0.009:
            match_status = "Review"
        rows.append(
            {
                "po_item_id": po_item["POItemID"],
                "description": po_item.get("Description") or "",
                "estimate_unit_price": float(po_item.get("EstimateUnitPrice") or 0),
                "delivered_qty": delivered_qty,
                "invoiced_qty": invoiced_qty,
                "invoice_unit_price": invoice_price,
                "subtotal": subtotal,
                "backordered_qty": float(po_item.get("BackorderedQty") or 0),
                "match_status": match_status,
            }
        )
    return rows


def get_open_purchase_order_invoice_pipeline():
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                po."PurchaseOrderID",
                po."Date",
                po."Status",
                po."PurchaseOrderTotal",
                v."VendorName",
                s."SiteName",
                (
                    SELECT vi."VendorInvoiceID"
                    FROM "VendorInvoice" vi
                    WHERE vi."PurchaseOrderID" = po."PurchaseOrderID"
                    ORDER BY vi."VendorInvoiceID" DESC
                    LIMIT 1
                ) AS "VendorInvoiceID",
                (
                    SELECT vi."VendorInvoiceStatus"
                    FROM "VendorInvoice" vi
                    WHERE vi."PurchaseOrderID" = po."PurchaseOrderID"
                    ORDER BY vi."VendorInvoiceID" DESC
                    LIMIT 1
                ) AS "VendorInvoiceStatus",
                (
                    SELECT vi."VendorInvoiceAmount"
                    FROM "VendorInvoice" vi
                    WHERE vi."PurchaseOrderID" = po."PurchaseOrderID"
                    ORDER BY vi."VendorInvoiceID" DESC
                    LIMIT 1
                ) AS "VendorInvoiceAmount"
            FROM "PurchaseOrder" po
            JOIN "Vendor" v ON po."VendorID" = v."VendorID"
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            WHERE po."Date" >= %s
              AND po."Status" != 'Retired'
              AND (
                    EXISTS (
                        SELECT 1
                        FROM "PurchaseOrderItem" poi
                        WHERE poi."PurchaseOrderID" = po."PurchaseOrderID"
                          AND COALESCE(poi."QuantityReceived", 0) > 0
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM "VendorInvoice" vi
                        WHERE vi."PurchaseOrderID" = po."PurchaseOrderID"
                    )
              )
            ORDER BY po."Date" DESC, po."PurchaseOrderID" DESC
            ''',
            (AUTOMATION_MIN_DATE,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_vendor_invoice_pipeline():
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                vi."VendorInvoiceID",
                vi."PurchaseOrderID",
                vi."VendorInvoiceNumber",
                vi."VendorInvoiceDate",
                vi."VendorInvoiceDueDate",
                vi."VendorInvoiceAmount",
                vi."VendorInvoiceStatus",
                po."Status" AS "POStatus",
                v."VendorName",
                s."SiteName"
            FROM "VendorInvoice" vi
            JOIN "PurchaseOrder" po ON vi."PurchaseOrderID" = po."PurchaseOrderID"
            JOIN "Vendor" v ON po."VendorID" = v."VendorID"
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            WHERE po."Status" != 'Retired'
            ORDER BY COALESCE(vi."VendorInvoiceDate"::text, '') DESC, vi."VendorInvoiceID" DESC
            '''
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_received_purchase_order_choices():
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT DISTINCT
                po."PurchaseOrderID",
                v."VendorName",
                COALESCE(s."SiteName", 'Unknown Site') AS "SiteName"
            FROM "PurchaseOrder" po
            JOIN "Vendor" v ON po."VendorID" = v."VendorID"
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            LEFT JOIN "PurchaseOrderItem" poi ON poi."PurchaseOrderID" = po."PurchaseOrderID"
            WHERE po."Status" != 'Retired'
              AND (
                    COALESCE(poi."QuantityReceived", 0) > 0
                    OR EXISTS (
                        SELECT 1
                        FROM "VendorInvoice" vi
                        WHERE vi."PurchaseOrderID" = po."PurchaseOrderID"
                    )
              )
            ORDER BY po."PurchaseOrderID" DESC
            '''
        )
        rows = cur.fetchall()
        return [
            {
                "PurchaseOrderID": int(row["PurchaseOrderID"]),
                "Label": f"PO #{row['PurchaseOrderID']} - {row.get('VendorName') or 'Vendor'} - {row.get('SiteName') or 'Site'}",
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_vendor_invoice_creation_context(po_id: int):
    po_data = get_po_export_data(po_id)
    if not po_data:
        return None
    return {
        "po": po_data,
        "receipt_choices": get_purchase_order_receipt_choices(po_id),
        "rollup": get_purchase_order_invoice_rollup(po_id),
    }


def get_vendor_invoice_workspace(po_id: int):
    po_data = get_po_export_data(po_id)
    if not po_data:
        return None
    invoice = get_or_create_vendor_invoice_draft(po_id)
    memory = get_vendor_invoice_memory(invoice["VendorInvoiceID"])
    rows = _build_default_invoice_rows(po_id, invoice["VendorInvoiceID"], memory.get("receipt_key"))
    total = sum(float(row.get("subtotal") or 0) for row in rows)
    rollup_excluding_current = get_purchase_order_invoice_rollup(po_id, exclude_invoice_id=invoice["VendorInvoiceID"])
    return {
        "po": po_data,
        "invoice": invoice,
        "rows": rows,
        "total": total,
        "report_path": memory.get("report_path"),
        "receipt_key": memory.get("receipt_key") or "all_received",
        "receipt_label": memory.get("receipt_label") or "All received material on this PO",
        "rollup_excluding_current": rollup_excluding_current,
        "receipt_choices": get_purchase_order_receipt_choices(po_id),
    }


def get_vendor_invoice_workspace_by_invoice_id(invoice_id: int):
    started_at = time.perf_counter()
    try:
        payload = get_vendor_invoice_detail(invoice_id)
        if not payload:
            return None
        po_id = int(payload["header"]["PurchaseOrderID"])
        po_data = get_po_export_data(po_id)
        memory = payload["memory"]
        rows = _build_default_invoice_rows(po_id, invoice_id, memory.get("receipt_key"))
        total = sum(float(row.get("subtotal") or 0) for row in rows)
        return {
            "po": po_data,
            "invoice": payload["header"],
            "rows": rows,
            "total": total,
            "report_path": memory.get("report_path"),
            "receipt_key": memory.get("receipt_key") or "all_received",
            "receipt_label": memory.get("receipt_label") or "All received material on this PO",
            "rollup_excluding_current": get_purchase_order_invoice_rollup(po_id, exclude_invoice_id=invoice_id),
            "receipt_choices": get_purchase_order_receipt_choices(po_id),
        }
    finally:
        _perf_log("db", "vendor_invoices.get_vendor_invoice_workspace_by_invoice_id", started_at)


def _recalculate_purchase_order_total(po_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            UPDATE "PurchaseOrder"
            SET "PurchaseOrderTotal" = COALESCE((
                SELECT SUM(COALESCE("LineTotal", 0))
                FROM "PurchaseOrderItem"
                WHERE "PurchaseOrderID" = %s
            ), 0)
            WHERE "PurchaseOrderID" = %s
            ''',
            (po_id, po_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_po_item(po_item_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT poi.*, po."PurchaseOrderID", po."WorkOrderID", wo."SourceEstimateID" AS "EstimateID"
            FROM "PurchaseOrderItem" poi
            JOIN "PurchaseOrder" po ON poi."PurchaseOrderID" = po."PurchaseOrderID"
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            WHERE poi."POItemID" = %s
            ''',
            (po_item_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def apply_vendor_invoice_adjustment(invoice_id: int, po_item_id: int, field: str, new_value: float, note_text: str):
    from neon_ai.database.automation import log_estimate_action

    po_item = _get_po_item(po_item_id)
    if not po_item:
        raise ValueError("Purchase order item not found.")
    if not note_text or not note_text.strip():
        raise ValueError("Adjustment note is required.")

    new_value = float(new_value)
    conn = get_connection()
    cur = conn.cursor()
    try:
        if field == "qty":
            new_qty = max(0.0, new_value)
            cur.execute(
                '''
                UPDATE "PurchaseOrderItem"
                SET "QuantityReceived" = %s,
                    "LastReceivedDate" = %s,
                    "LineTotal" = %s * COALESCE("UnitPriceAtOrder", 0),
                    "LastPackingSlip" = CASE
                        WHEN COALESCE("LastPackingSlip", '') = '' THEN %s
                        ELSE CONCAT("LastPackingSlip", ' | ', %s)
                    END
                WHERE "POItemID" = %s
                ''',
                (
                    new_qty,
                    datetime.date.today().isoformat(),
                    new_qty,
                    "Adjusted via vendor invoice",
                    "Adjusted via vendor invoice",
                    po_item_id,
                ),
            )
        elif field == "price":
            new_price = max(0.0, new_value)
            cur.execute(
                '''
                UPDATE "PurchaseOrderItem"
                SET "UnitPriceAtOrder" = %s,
                    "LineTotal" = COALESCE("QuantityReceived", 0) * %s
                WHERE "POItemID" = %s
                ''',
                (new_price, new_price, po_item_id),
            )
        else:
            raise ValueError("Unsupported adjustment field.")
        conn.commit()
    finally:
        conn.close()

    _recalculate_purchase_order_total(po_item["PurchaseOrderID"])
    update_purchase_order_receiving_status(po_item["PurchaseOrderID"])

    memory = get_vendor_invoice_memory(invoice_id)
    rows = memory.get("rows", [])
    for row in rows:
        if row.get("po_item_id") != po_item_id:
            continue
        if field == "qty":
            row["delivered_qty"] = max(0.0, new_value)
            row["invoiced_qty"] = max(0.0, new_value)
        else:
            row["invoice_unit_price"] = max(0.0, new_value)
        row["subtotal"] = float(row.get("invoiced_qty") or 0) * float(row.get("invoice_unit_price") or 0)
    if rows:
        upsert_vendor_invoice_memory(invoice_id, {"rows": rows, "last_updated_at": datetime.datetime.now().isoformat()})

    estimate_id = po_item.get("EstimateID")
    if estimate_id:
        label = "quantity" if field == "qty" else "price"
        log_estimate_action(
            estimate_id,
            f"Vendor invoice adjustment on PO #{po_item['PurchaseOrderID']} for item '{po_item.get('Description')}'. Updated {label} to {new_value}. Note: {note_text}",
            "Vendor Invoice",
        )


def save_vendor_invoice_draft(
    po_id: int,
    invoice_id: int,
    invoice_number: str,
    invoice_date: str,
    due_date: str,
    description: str,
    rows: list,
    status: str = "Draft",
    receipt_key: str = None,
    receipt_label: str = None,
):
    total = sum(float(row.get("subtotal") or 0) for row in rows)
    invoice = _upsert_vendor_invoice_row(
        invoice_id=invoice_id,
        invoice_number=invoice_number or _next_vendor_invoice_number(po_id),
        invoice_date=invoice_date or datetime.date.today().isoformat(),
        due_date=due_date,
        amount=total,
        description=description or "Vendor invoice draft saved from Payables workspace.",
        status=status,
    )
    existing_memory = get_vendor_invoice_memory(invoice["VendorInvoiceID"])
    choice = _resolve_receipt_choice(po_id, receipt_key or existing_memory.get("receipt_key"))
    resolved_receipt_ref = choice.get("DocumentRef") if choice else ""
    resolved_receipt_date = choice.get("ReceiveDate") if choice else ""
    if not receipt_key:
        resolved_receipt_ref = existing_memory.get("receipt_ref") or resolved_receipt_ref
        resolved_receipt_date = existing_memory.get("receipt_date") or resolved_receipt_date
    upsert_vendor_invoice_memory(
        invoice["VendorInvoiceID"],
        {
            "po_id": po_id,
            "rows": rows,
            "receipt_key": receipt_key or existing_memory.get("receipt_key") or "all_received",
            "receipt_label": receipt_label or existing_memory.get("receipt_label") or (choice.get("Label") if choice else "All received material on this PO"),
            "receipt_ref": resolved_receipt_ref,
            "receipt_date": resolved_receipt_date,
            "last_updated_at": datetime.datetime.now().isoformat(),
        },
    )
    return invoice


def generate_vendor_invoice_report(invoice_id: int, force: bool = False):
    payload = get_vendor_invoice_detail(invoice_id)
    if not payload:
        raise ValueError("Vendor invoice could not be loaded.")

    memory = get_vendor_invoice_memory(invoice_id)
    existing_path = memory.get("report_path")
    if existing_path and os.path.exists(existing_path) and not force:
        return existing_path

    header = payload["header"]
    po_data = get_po_export_data(header["PurchaseOrderID"])
    target_dir = get_target_folder(
        po_data.get("StreetNumber"),
        po_data.get("StreetName"),
        po_data.get("SiteName"),
        po_data.get("EstimateID"),
        "Vendor Invoices",
    )
    filename = f"Vendor_Invoice_Reconciliation_{invoice_id}.docx"
    report_path = os.path.join(target_dir, filename).replace("\\", "/")

    doc = Document()
    doc.add_heading(f"Vendor Invoice Reconciliation #{invoice_id}", 0)
    doc.add_paragraph(f"Vendor: {header.get('VendorName')}")
    doc.add_paragraph(f"PO #: {header.get('PurchaseOrderID')}")
    doc.add_paragraph(f"Invoice #: {header.get('VendorInvoiceNumber')}")
    doc.add_paragraph(f"Invoice Date: {header.get('VendorInvoiceDate')}")
    doc.add_paragraph(f"Status: {header.get('VendorInvoiceStatus')}")
    if memory.get("receipt_label"):
        doc.add_paragraph(f"Receiving paperwork: {memory.get('receipt_label')}")

    table = doc.add_table(rows=1, cols=7)
    table.style = "Table Grid"
    headers = ["Description", "Estimate Price", "Delivered Qty", "Invoiced Qty", "Invoice Price", "Subtotal", "Match"]
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text

    total = 0.0
    for row in payload["rows"]:
        cells = table.add_row().cells
        cells[0].text = str(row.get("Description") or "")
        cells[1].text = f"${float(row.get('EstimateUnitPrice') or 0):,.2f}"
        cells[2].text = f"{float(row.get('DeliveredQty') or 0):.2f}"
        cells[3].text = f"{float(row.get('InvoicedQty') or 0):.2f}"
        cells[4].text = f"${float(row.get('InvoiceUnitPrice') or 0):,.2f}"
        cells[5].text = f"${float(row.get('InvoiceLineTotal') or 0):,.2f}"
        cells[6].text = str(row.get("MatchStatus") or "OK")
        total += float(row.get("InvoiceLineTotal") or 0)

    doc.add_paragraph(f"Total Invoiced: ${total:,.2f}")
    doc.save(report_path)
    upsert_vendor_invoice_memory(invoice_id, {"report_path": report_path, "last_report_generated_at": datetime.datetime.now().isoformat()})
    return report_path


def get_vendor_invoice_detail(invoice_id: int):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                vi.*,
                po."PurchaseOrderID",
                po."Status" AS "POStatus",
                v."VendorName",
                s."SiteName"
            FROM "VendorInvoice" vi
            JOIN "PurchaseOrder" po ON vi."PurchaseOrderID" = po."PurchaseOrderID"
            JOIN "Vendor" v ON po."VendorID" = v."VendorID"
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            WHERE vi."VendorInvoiceID" = %s
            ''',
            (invoice_id,),
        )
        header = cur.fetchone()
    finally:
        conn.close()

    if not header:
        return None

    memory = get_vendor_invoice_memory(invoice_id)
    rows = _build_default_invoice_rows(header["PurchaseOrderID"], invoice_id, memory.get("receipt_key"))
    return {
        "header": header,
        "rows": [
            {
                "POItemID": row["po_item_id"],
                "Description": row["description"],
                "EstimateUnitPrice": row["estimate_unit_price"],
                "DeliveredQty": row["delivered_qty"],
                "InvoicedQty": row["invoiced_qty"],
                "InvoiceUnitPrice": row["invoice_unit_price"],
                "InvoiceLineTotal": row["subtotal"],
                "MatchStatus": row["match_status"],
                "BackorderedQty": row["backordered_qty"],
            }
            for row in rows
        ],
        "memory": memory,
    }


def process_inbound_vendor_invoice_email(po_id: int, sender_email: str, subject: str, body: str, attachment_path: str = None, received_at=None):
    from neon_ai.gateway import send_to_user

    reference_date = received_at.date() if hasattr(received_at, "date") else datetime.date.today()
    attachment_text = ""
    if attachment_path and os.path.exists(attachment_path):
        attachment_text = extract_text_from_attachment(attachment_path)
        if not attachment_text and attachment_path.lower().endswith(".pdf"):
            attachment_text = ocr_pdf_attachment(attachment_path)

    combined_text = "\n".join(part for part in [subject, body, attachment_text] if part)
    header = extract_invoice_header_fields(subject, body, attachment_text, reference_date=reference_date)
    parsed_lines = parse_vendor_invoice_lines(po_id, combined_text)

    existing_invoice = _find_vendor_invoice_by_number(po_id, header["invoice_number"])
    invoice = existing_invoice or create_vendor_invoice_draft(po_id, receipt_key="all_received")
    rows = _build_default_invoice_rows(po_id, invoice["VendorInvoiceID"], get_vendor_invoice_memory(invoice["VendorInvoiceID"]).get("receipt_key"))
    if parsed_lines:
        line_map = {row["po_item_id"]: row for row in parsed_lines}
        for row in rows:
            matched = line_map.get(row["po_item_id"])
            if matched:
                row["invoiced_qty"] = float(matched.get("invoice_qty") or row["delivered_qty"])
                row["invoice_unit_price"] = float(matched.get("invoice_unit_price") or row["invoice_unit_price"])
                row["subtotal"] = row["invoiced_qty"] * row["invoice_unit_price"]
                row["match_status"] = "Review" if row["invoiced_qty"] > row["delivered_qty"] + 0.001 else "OK"

    saved_invoice = save_vendor_invoice_draft(
        po_id,
        invoice["VendorInvoiceID"],
        header["invoice_number"] or invoice.get("VendorInvoiceNumber"),
        header["invoice_date"],
        "",
        f"Draft created from vendor email from {sender_email}.",
        rows,
        status="Draft",
    )
    upsert_vendor_invoice_memory(
        saved_invoice["VendorInvoiceID"],
        {
            "attachment_path": attachment_path,
            "attachment_text_excerpt": (attachment_text or "")[:2000],
            "parsed_lines": parsed_lines,
            "received_at": received_at.isoformat() if hasattr(received_at, "isoformat") else datetime.datetime.now().isoformat(),
            "last_updated_at": datetime.datetime.now().isoformat(),
        },
    )

    po_data = get_po_export_data(po_id)
    send_to_user(
        subject=f"Vendor Invoice Ready For Review: PO #{po_id}",
        content=(
            f"Vendor invoice draft #{saved_invoice['VendorInvoiceID']} is ready for review.\n\n"
            f"Vendor: {po_data.get('VendorName') if po_data else 'Unknown'}\n"
            f"PO: #{po_id}\n"
            f"Invoice number: {header['invoice_number'] or saved_invoice['VendorInvoiceNumber']}\n"
            f"Invoice date: {header['invoice_date']}\n"
            f"Parsed line matches: {len(parsed_lines)}\n\n"
            "Next action: review the draft vendor invoice in the Payables workspace and lock it once verified."
        )
    )

    return {
        "vendor_invoice_id": saved_invoice["VendorInvoiceID"],
        "po_id": po_id,
        "invoice_number": saved_invoice["VendorInvoiceNumber"],
        "invoice_amount": float(saved_invoice.get("VendorInvoiceAmount") or 0),
        "matched_lines": len(parsed_lines),
    }


def lock_vendor_invoice_ready_to_pay(invoice_id: int):
    from neon_ai.gateway import MY_EMAIL, send_to_user

    payload = get_vendor_invoice_detail(invoice_id)
    if not payload:
        raise ValueError("Vendor invoice could not be loaded.")

    header = payload["header"]
    report_path = generate_vendor_invoice_report(invoice_id, force=True)
    _upsert_vendor_invoice_row(
        invoice_id=invoice_id,
        invoice_number=header["VendorInvoiceNumber"],
        invoice_date=header.get("VendorInvoiceDate") or datetime.date.today().isoformat(),
        due_date=header.get("VendorInvoiceDueDate") or "",
        amount=float(header.get("VendorInvoiceAmount") or 0),
        description=header.get("Description") or "",
        status="ReadyToPay",
    )
    upsert_vendor_invoice_memory(invoice_id, {"report_path": report_path, "locked_at": datetime.datetime.now().isoformat()})

    send_to_user(
        subject=f"Pay Vendor Invoice: {header['VendorInvoiceNumber']}",
        content=(
            f"Vendor invoice {header['VendorInvoiceNumber']} for PO #{header['PurchaseOrderID']} is locked and ready to pay.\n\n"
            f"Vendor: {header.get('VendorName')}\n"
            f"Amount: ${float(header.get('VendorInvoiceAmount') or 0):,.2f}\n"
            f"Site: {header.get('SiteName') or 'N/A'}\n\n"
            "The reconciliation report is attached."
        ),
        recipient=MY_EMAIL,
        attachment_path=report_path,
    )
    return report_path


def mark_vendor_invoice_paid(invoice_id: int):
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            UPDATE "VendorInvoice"
            SET "VendorInvoiceStatus" = 'Paid',
                "VendorInvoicePaidDate" = CURRENT_DATE
            WHERE "VendorInvoiceID" = %s
            ''',
            (invoice_id,),
        )
        conn.commit()
    finally:
        conn.close()


def get_vendor_payables_summary():
    ensure_vendor_invoice_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                COALESCE(SUM(vi."VendorInvoiceAmount"::numeric), 0) AS "TotalOwed",
                COUNT(*) AS "InvoiceCount"
            FROM "VendorInvoice" vi
            JOIN "PurchaseOrder" po ON vi."PurchaseOrderID" = po."PurchaseOrderID"
            WHERE COALESCE(vi."VendorInvoiceStatus", '') IN ('Draft', 'ReadyToPay')
              AND EXISTS (
                    SELECT 1
                    FROM "PurchaseOrderItem" poi
                    WHERE poi."PurchaseOrderID" = po."PurchaseOrderID"
                      AND COALESCE(poi."QuantityReceived", 0) > 0
              )
            '''
        )
        row = cur.fetchone() or {}
        return {
            "total_owed": float(row.get("TotalOwed") or 0),
            "invoice_count": int(row.get("InvoiceCount") or 0),
        }
    finally:
        conn.close()


def sweep_for_ready_to_pay_vendor_invoices():
    from neon_ai.gateway import MY_EMAIL, send_to_user

    today = datetime.date.today()
    if today < AUTOMATION_MIN_DATE:
        return

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT vi."VendorInvoiceID", vi."VendorInvoiceNumber", vi."VendorInvoiceDate", vi."VendorInvoiceAmount", vi."VendorInvoiceStatus", po."PurchaseOrderID"
            FROM "VendorInvoice" vi
            JOIN "PurchaseOrder" po ON vi."PurchaseOrderID" = po."PurchaseOrderID"
            WHERE vi."VendorInvoiceStatus" = 'ReadyToPay'
              AND po."Date" >= %s
            ''',
            (AUTOMATION_MIN_DATE,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    for row in rows:
        invoice_date = _parse_invoice_date(str(row.get("VendorInvoiceDate") or ""), reference_date=today)
        if not invoice_date or today < invoice_date + datetime.timedelta(days=25):
            continue

        memory = get_vendor_invoice_memory(row["VendorInvoiceID"])
        last_sent = memory.get("last_payment_reminder_at")
        if last_sent:
            try:
                last_date = datetime.datetime.fromisoformat(last_sent).date()
                if today <= last_date + datetime.timedelta(days=7):
                    continue
            except ValueError:
                pass

        report_path = generate_vendor_invoice_report(row["VendorInvoiceID"], force=False)
        send_to_user(
            subject=f"Payment Reminder: Vendor Invoice {row['VendorInvoiceNumber']}",
            content=(
                f"Vendor invoice {row['VendorInvoiceNumber']} for PO #{row['PurchaseOrderID']} has been ready to pay for 25 days.\n\n"
                f"Amount: ${float(row.get('VendorInvoiceAmount') or 0):,.2f}\n"
                f"Invoice date: {row.get('VendorInvoiceDate')}\n\n"
                "Please schedule payment."
            ),
            recipient=MY_EMAIL,
            attachment_path=report_path,
        )
        upsert_vendor_invoice_memory(row["VendorInvoiceID"], {"last_payment_reminder_at": datetime.datetime.now().isoformat()})
