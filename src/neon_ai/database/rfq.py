import psycopg2
import datetime
import os
import re
import textwrap
import json
import shutil
import time
from psycopg2.extras import RealDictCursor
from neon_ai.database.connection import get_connection
from neon_ai.database.folders import get_target_folder
from pypdf import PdfReader
import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR

RFQ_DISPATCH_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rfq_dispatch_log.json")
# Automation is now fully ungated; keep a very early cutoff so the existing
# sweeper queries still work without excluding historical RFQs.
AUTOMATION_MIN_DATE = datetime.date(1900, 1, 1)
BACKGROUND_VENDOR_RFQ_FOLLOWUPS_ENV = "NEON_ENABLE_BACKGROUND_VENDOR_RFQ_FOLLOWUPS"
_OCR_ENGINE = None
_RFQ_BID_COMPARE_SCHEMA_READY = False
_MATERIAL_REQUEST_SOURCE_SCHEMA_READY = False


def _perf_log(area: str, name: str, started_at: float, **fields) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    extras = []
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


def _background_vendor_rfq_followups_enabled() -> bool:
    return str(os.getenv(BACKGROUND_VENDOR_RFQ_FOLLOWUPS_ENV, "0")).strip().lower() in {"1", "true", "yes", "on"}

def load_rfq_dispatch_log():
    if not os.path.exists(RFQ_DISPATCH_LOG_PATH):
        return []
    try:
        with open(RFQ_DISPATCH_LOG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_rfq_dispatch_log(entries):
    with open(RFQ_DISPATCH_LOG_PATH, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2)

def append_rfq_dispatch_record(record):
    entries = load_rfq_dispatch_log()
    entries.append(record)
    save_rfq_dispatch_log(entries)

def get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE

def _pdf_escape(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )

def _write_simple_pdf(pdf_path, title, lines):
    """Creates a lightweight text PDF without extra dependencies."""
    page_width = 612
    page_height = 792
    left_margin = 54
    top_start = 738
    line_height = 14
    bottom_margin = 54

    wrapped_lines = []
    for raw_line in lines:
        wrapped_lines.extend(textwrap.wrap(str(raw_line or ""), width=92) or [""])

    pages = []
    current_page = []
    y = top_start

    for title_line in textwrap.wrap(title or "RFQ", width=80) or ["RFQ"]:
        current_page.append((left_margin, y, title_line))
        y -= line_height
    y -= 8

    for line in wrapped_lines:
        if y <= bottom_margin:
            pages.append(current_page)
            current_page = []
            y = top_start
        current_page.append((left_margin, y, line))
        y -= line_height

    if not current_page:
        current_page = [(left_margin, top_start, "")]
    pages.append(current_page)

    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>")

    for i, page_lines in enumerate(pages):
        page_obj_num = 3 + i * 2
        content_obj_num = page_obj_num + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> /Contents {content_obj_num} 0 R >>"
        )
        content_lines = ["BT", "/F1 11 Tf"]
        for x, y_pos, text in page_lines:
            content_lines.append(f"1 0 0 1 {x} {y_pos} Tm ({_pdf_escape(text)}) Tj")
        content_lines.append("ET")
        content_stream = "\n".join(content_lines)
        objects.append(
            f"<< /Length {len(content_stream.encode('latin-1', errors='replace'))} >>\n"
            f"stream\n{content_stream}\nendstream"
        )

    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf_parts = ["%PDF-1.4\n"]
    offsets = [0]
    running_length = len(pdf_parts[0].encode("latin-1"))
    for index, obj in enumerate(objects, start=1):
        offsets.append(running_length)
        serialized = f"{index} 0 obj\n{obj}\nendobj\n"
        pdf_parts.append(serialized)
        running_length += len(serialized.encode("latin-1", errors="replace"))

    xref_offset = running_length
    xref_lines = [f"xref\n0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref_lines.append(f"{offset:010d} 00000 n \n")

    pdf_parts.append(
        "".join(xref_lines) +
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    )

    with open(pdf_path, "wb") as handle:
        handle.write("".join(pdf_parts).encode("latin-1", errors="replace"))

    return pdf_path

def get_or_create_vendor(vendor_name: str):
    """Checks if a vendor exists. If not, auto-creates them and returns the ID."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Check if they exist
        cur.execute('SELECT "VendorID" FROM "Vendor" WHERE "VendorName" ILIKE %s', (vendor_name,))
        result = cur.fetchone()
        
        if result:
            return result['VendorID']
            
        # If not, auto-create them
        cur.execute('INSERT INTO "Vendor" ("VendorName") VALUES (%s) RETURNING "VendorID"', (vendor_name,))
        new_vendor = cur.fetchone()
        conn.commit()
        return new_vendor['VendorID']
    finally:
        conn.close()

def save_rfq_package(estimate_id: int, vendor_name: str, due_date: str, material_ids: list):
    # LEGACY_COMPAT_ONLY:
    # Supports old loose RFQ / PriceRequest compatibility records.
    # Do not use for new RFQ creation. New RFQ workflow must use:
    # MaterialCall -> Create RFQ -> child PriceRequest.
    # Automation must not call this helper for new RFQs; use the MaterialCall-aware
    # service path plus approval/proposal flow instead.
    """Creates a legacy loose RFQ header and links the selected items."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Ensure Vendor Exists
        vendor_id = get_or_create_vendor(vendor_name)
        
        # 2. Create the Price Request (The Header)
        cur.execute("""
            INSERT INTO "PriceRequest" ("EstimateID", "VendorID", "DueDate", "DateSent", "Status")
            VALUES (%s, %s, %s, NULL, 'Draft')
            RETURNING "PriceRequestID"
        """, (estimate_id, vendor_id, due_date or None))
        
        rfq_id = cur.fetchone()['PriceRequestID']
        
        # 3. Batch Insert the Items (The List)
        for mat_id in material_ids:
            cur.execute("""
                INSERT INTO "PriceRequestItem" ("PriceRequestID", "MaterialID")
                VALUES (%s, %s)
            """, (rfq_id, mat_id))
            
        conn.commit()
        return rfq_id
    finally:
        conn.close()

def save_rfq_package_for_vendor_id(estimate_id: int, vendor_id: int, due_date: str, material_ids: list):
    # LEGACY_COMPAT_ONLY:
    # Supports old loose RFQ / PriceRequest compatibility records.
    # Do not use for new RFQ creation. New RFQ workflow must use:
    # MaterialCall -> Create RFQ -> child PriceRequest.
    # Automation must not call this helper for new RFQs; use the MaterialCall-aware
    # service path plus approval/proposal flow instead.
    """Creates a legacy loose RFQ header and items for a specific vendor id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO "PriceRequest" ("EstimateID", "VendorID", "DueDate", "DateSent", "Status")
            VALUES (%s, %s, %s, NULL, 'Draft')
            RETURNING "PriceRequestID"
        """, (estimate_id, vendor_id, due_date or None))
        rfq_id = cur.fetchone()['PriceRequestID']

        for mat_id in material_ids:
            cur.execute("""
                INSERT INTO "PriceRequestItem" ("PriceRequestID", "MaterialID")
                VALUES (%s, %s)
            """, (rfq_id, mat_id))

        conn.commit()
        return rfq_id
    finally:
        conn.close()


def _verify_material_request_source_schema(cur) -> None:
    global _MATERIAL_REQUEST_SOURCE_SCHEMA_READY
    if _MATERIAL_REQUEST_SOURCE_SCHEMA_READY:
        return
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'PriceRequest'
          AND column_name IN ('MaterialRequestSourceType', 'MaterialRequestSourceID')
        """
    )
    found = {
        str(row.get("column_name") if isinstance(row, dict) else row[0])
        for row in cur.fetchall()
    }
    missing = [
        column
        for column in ("MaterialRequestSourceType", "MaterialRequestSourceID")
        if column not in found
    ]
    if missing:
        raise RuntimeError(
            "PriceRequest source-context schema is missing required columns: "
            + ", ".join(missing)
            + ". Run the RFQ/source-context migration before using this workflow."
        )
    _MATERIAL_REQUEST_SOURCE_SCHEMA_READY = True


def ensure_material_request_source_schema(cur=None, *, force: bool = False, verify_only: bool = False):
    global _MATERIAL_REQUEST_SOURCE_SCHEMA_READY
    if _MATERIAL_REQUEST_SOURCE_SCHEMA_READY and not force:
        return
    owns_connection = cur is None
    conn = None
    if owns_connection:
        conn = get_connection()
        cur = conn.cursor()
    try:
        if verify_only:
            _verify_material_request_source_schema(cur)
        else:
            cur.execute('ALTER TABLE public."PriceRequest" ADD COLUMN IF NOT EXISTS "MaterialRequestSourceType" text')
            cur.execute('ALTER TABLE public."PriceRequest" ADD COLUMN IF NOT EXISTS "MaterialRequestSourceID" integer')
            _MATERIAL_REQUEST_SOURCE_SCHEMA_READY = True
        if owns_connection and conn is not None:
            conn.commit()
    except Exception:
        if owns_connection and conn is not None:
            conn.rollback()
        raise
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _normalize_material_request_source_type(source_type: str | None) -> str:
    normalized = str(source_type or "").strip().lower()
    if normalized in {"workorder", "work order", "wo"}:
        return "WorkOrder"
    return "Estimate"


def resolve_material_request_source_context(
    source_type: str,
    source_id: int,
    *,
    cur=None,
    require_eligible: bool = False,
):
    from neon_ai.database.estimates import normalize_estimate_status

    normalized_source_type = _normalize_material_request_source_type(source_type)
    owns_connection = cur is None
    conn = None
    if owns_connection:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if normalized_source_type == "Estimate":
            cur.execute(
                """
                SELECT
                    e."EstimateID",
                    COALESCE(e."Status", 'Draft') AS "Status",
                    COALESCE(e."IsConverted", FALSE) AS "IsConverted",
                    COALESCE(s."SiteName", 'Unknown Site') AS "SiteName",
                    COALESCE(e."Description", '') AS "Description"
                FROM "Estimate" e
                LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
                WHERE e."EstimateID" = %s
                """,
                (int(source_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Estimate #{source_id} was not found.")

            estimate_id = int(row["EstimateID"])
            estimate_status = normalize_estimate_status(row.get("Status"))
            is_converted = bool(row.get("IsConverted"))
            is_available = estimate_status == "Draft" and not is_converted
            if require_eligible and not is_available:
                if is_converted:
                    raise ValueError(
                        f"Estimate #{estimate_id} has already been converted to a Work Order and cannot be used for a new material-request RFQ."
                    )
                raise ValueError(
                    f"Estimate #{estimate_id} is not an available draft estimate for material requests (current status: {estimate_status or 'Unknown'})."
                )

            description = str(row.get("Description") or "").strip()
            site_name = str(row.get("SiteName") or "Unknown Site").strip()
            return {
                "source_type": "Estimate",
                "source_id": estimate_id,
                "selector_label": "Estimate #",
                "source_document_label": f"Estimate #{estimate_id}",
                "selection_label": f"{estimate_id} - {description or site_name or 'Draft Estimate'}",
                "estimate_id": estimate_id,
                "work_order_id": None,
                "site_name": site_name,
                "description": description,
                "estimate_status": estimate_status,
                "is_valid_for_material_request": is_available,
            }

        cur.execute(
            """
            SELECT
                wo."WorkOrderID",
                COALESCE(NULLIF(TRIM(wo."JobStatus"), ''), CASE WHEN COALESCE(wo."IsClosed", FALSE) THEN 'Closed' ELSE 'Unknown' END) AS "JobStatus",
                COALESCE(wo."IsClosed", FALSE) AS "IsClosed",
                COALESCE(s."SiteName", 'Unknown Site') AS "SiteName",
                COALESCE(wo."Description", '') AS "Description",
                COALESCE(wo."SourceEstimateID", est."EstimateID") AS "EstimateID"
            FROM "WorkOrder" wo
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            LEFT JOIN LATERAL (
                SELECT e."EstimateID"
                FROM "Estimate" e
                WHERE e."SiteID" = wo."SiteID"
                ORDER BY e."EstimateID" DESC
                LIMIT 1
            ) est ON TRUE
            WHERE wo."WorkOrderID" = %s
            """,
            (int(source_id),),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Work Order #{source_id} was not found.")

        work_order_id = int(row["WorkOrderID"])
        work_order_status = str(row.get("JobStatus") or "").strip() or "Unknown"
        is_open = work_order_status.upper() == "OPEN" and not bool(row.get("IsClosed"))
        if require_eligible and not is_open:
            raise ValueError(
                f"Work Order #{work_order_id} is not open and cannot be used for a new material request (current status: {work_order_status})."
            )

        estimate_id = int(row["EstimateID"]) if row.get("EstimateID") is not None else None
        if not estimate_id:
            raise ValueError(
                f"Work Order #{work_order_id} could not be mapped to an EstimateID for RFQ drafting."
            )

        description = str(row.get("Description") or "").strip()
        site_name = str(row.get("SiteName") or "Unknown Site").strip()
        return {
            "source_type": "WorkOrder",
            "source_id": work_order_id,
            "selector_label": "Work Order #",
            "source_document_label": f"Work Order #{work_order_id}",
            "selection_label": f"{work_order_id} - {site_name or description or 'Open Work Order'}",
            "estimate_id": estimate_id,
            "work_order_id": work_order_id,
            "site_name": site_name,
            "description": description,
            "work_order_status": work_order_status,
            "is_valid_for_material_request": is_open,
        }
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _resolve_manual_rfq_estimate_id(cur, source_type: str, source_id: int) -> int:
    context = resolve_material_request_source_context(
        source_type,
        int(source_id),
        cur=cur,
        require_eligible=True,
    )
    return int(context["estimate_id"])


def _insert_manual_estimate_material(cur, estimate_id: int, row: dict) -> int:
    from neon_ai.database.estimates import (
        DEFAULT_MATERIAL_PST_RATE,
        _calculate_estimate_material_line_total,
        _normalize_material_pst_rate,
        _normalize_material_pst_taxable,
        ensure_estimate_material_tax_schema,
    )

    description = str(row.get("description") or "").strip()
    if not description:
        raise ValueError("Material description is required.")

    quantity = float(row.get("qty") or 0)
    if quantity <= 0:
        raise ValueError("Material quantity must be greater than zero.")

    part_number = str(row.get("part_number") or "").strip() or None
    catalog_item_id = row.get("catalog_item_id")
    if catalog_item_id in ("", None):
        catalog_item_id = None
    else:
        catalog_item_id = int(catalog_item_id)
    unit_cost = float(row.get("unit_cost") or 0)
    pst_taxable = _normalize_material_pst_taxable(row.get("pst_taxable"))
    pst_rate = _normalize_material_pst_rate(row.get("pst_rate") or DEFAULT_MATERIAL_PST_RATE)
    line_total = _calculate_estimate_material_line_total(quantity, unit_cost, pst_taxable, pst_rate)
    price_source = "Catalog" if catalog_item_id else "Manual"
    ensure_estimate_material_tax_schema(cur=cur)

    cur.execute(
        """
        INSERT INTO "EstimateMaterial" (
            "EstimateID",
            "Description",
            "Quantity",
            "UnitCost",
            "LineTotal",
            "ItemID",
            "PartNumber",
            "PriceSource",
            "PSTTaxable",
            "PSTRate"
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING "EstimateMaterialID"
        """,
        (
            estimate_id,
            description,
            quantity,
            unit_cost,
            line_total,
            catalog_item_id,
            part_number,
            price_source,
            pst_taxable,
            pst_rate,
        ),
    )
    return int(cur.fetchone()["EstimateMaterialID"])


def _update_manual_estimate_material(cur, estimate_material_id: int, row: dict) -> None:
    from neon_ai.database.estimates import (
        DEFAULT_MATERIAL_PST_RATE,
        _calculate_estimate_material_line_total,
        _normalize_material_pst_rate,
        _normalize_material_pst_taxable,
        ensure_estimate_material_tax_schema,
    )

    description = str(row.get("description") or "").strip()
    if not description:
        raise ValueError("Material description is required.")

    quantity = float(row.get("qty") or 0)
    if quantity <= 0:
        raise ValueError("Material quantity must be greater than zero.")

    part_number = str(row.get("part_number") or "").strip() or None
    catalog_item_id = row.get("catalog_item_id")
    if catalog_item_id in ("", None):
        catalog_item_id = None
    else:
        catalog_item_id = int(catalog_item_id)
    unit_cost = float(row.get("unit_cost") or 0)
    pst_taxable = _normalize_material_pst_taxable(row.get("pst_taxable"))
    pst_rate = _normalize_material_pst_rate(row.get("pst_rate") or DEFAULT_MATERIAL_PST_RATE)
    line_total = _calculate_estimate_material_line_total(quantity, unit_cost, pst_taxable, pst_rate)
    price_source = "Catalog" if catalog_item_id else "Manual"
    ensure_estimate_material_tax_schema(cur=cur)

    cur.execute(
        """
        UPDATE "EstimateMaterial"
        SET "Description" = %s,
            "Quantity" = %s,
            "UnitCost" = %s,
            "LineTotal" = %s,
            "ItemID" = %s,
            "PartNumber" = %s,
            "PriceSource" = %s,
            "PSTTaxable" = %s,
            "PSTRate" = %s
        WHERE "EstimateMaterialID" = %s
        """,
        (
            description,
            quantity,
            unit_cost,
            line_total,
            catalog_item_id,
            part_number,
            price_source,
            pst_taxable,
            pst_rate,
            estimate_material_id,
        ),
    )


def _normalize_material_request_due_date(due_date):
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


def _build_material_request_notes(
    *,
    source_type: str,
    source_id: int,
    material_rows: list[dict],
    rfq_notes: str | None = None,
    rfq_id: int | None = None,
    is_update: bool = False,
) -> str:
    header_note_lines = []
    if is_update and rfq_id:
        header_note_lines.append(f"Updated from Material Request Workspace. RFQID={rfq_id}.")
    else:
        header_note_lines.append("Created from Material Request Workspace.")
    source_label = "Work Order" if _normalize_material_request_source_type(source_type) == "WorkOrder" else "Estimate"
    header_note_lines.append(f"Source Document: {source_label} #{int(source_id)}")
    clean_rfq_notes = str(rfq_notes or "").strip()
    if clean_rfq_notes:
        header_note_lines.append(f"Request Notes: {clean_rfq_notes}")
        header_note_lines.append(f"RFQ Notes: {clean_rfq_notes}")
    for index, row in enumerate(material_rows, start=1):
        note_text = str(row.get("notes") or "").strip()
        if note_text:
            header_note_lines.append(f"Row {index} note: {note_text}")
    return "\n".join(header_note_lines)


def create_manual_rfq_draft(
    source_type: str,
    source_id: int,
    vendor_id: int,
    material_rows: list[dict],
    *,
    due_date=None,
    rfq_notes: str | None = None,
) -> int:
    # LEGACY_COMPAT_ONLY:
    # Supports old loose RFQ / PriceRequest compatibility records.
    # Do not use for new RFQ creation. New RFQ workflow must use:
    # MaterialCall -> Create RFQ -> child PriceRequest.
    # Automation must not call this helper for new RFQs; use the MaterialCall-aware
    # service path plus approval/proposal flow instead.
    if not vendor_id:
        raise ValueError("Vendor selection is required.")
    if not material_rows:
        raise ValueError("Add at least one material row before saving the RFQ draft.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        source_context = resolve_material_request_source_context(
            source_type,
            int(source_id),
            cur=cur,
            require_eligible=True,
        )
        estimate_id = int(source_context["estimate_id"])
        normalized_due_date = _normalize_material_request_due_date(due_date)
        notes_payload = _build_material_request_notes(
            source_type=str(source_context["source_type"]),
            source_id=int(source_context["source_id"]),
            material_rows=material_rows,
            rfq_notes=rfq_notes,
            is_update=False,
        )

        cur.execute(
            """
            INSERT INTO "PriceRequest" (
                "EstimateID",
                "VendorID",
                "DueDate",
                "DateSent",
                "Status",
                "Notes",
                "MaterialRequestSourceType",
                "MaterialRequestSourceID"
            )
            VALUES (%s, %s, %s, NULL, 'Draft', %s, %s, %s)
            RETURNING "PriceRequestID"
            """,
            (
                estimate_id,
                int(vendor_id),
                normalized_due_date,
                notes_payload,
                str(source_context["source_type"]),
                int(source_context["source_id"]),
            ),
        )
        rfq_id = int(cur.fetchone()["PriceRequestID"])

        for row in material_rows:
            estimate_material_id = _insert_manual_estimate_material(cur, estimate_id, row)
            cur.execute(
                """
                INSERT INTO "PriceRequestItem" (
                    "PriceRequestID",
                    "MaterialID",
                    "QuantityOverride",
                    "QuotedUnitPrice",
                    "IsSubstitute",
                    "SubstituteNotes",
                    "IsCarried"
                )
                VALUES (%s, %s, %s, NULL, FALSE, NULL, FALSE)
                """,
                (
                    rfq_id,
                    estimate_material_id,
                    float(row.get("qty") or 0),
                ),
            )

        conn.commit()
        return rfq_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_manual_rfq_draft(
    rfq_id: int,
    material_rows: list[dict],
    *,
    due_date=None,
    rfq_notes: str | None = None,
) -> None:
    # LEGACY_COMPAT_ONLY:
    # Supports old loose RFQ / PriceRequest compatibility records.
    # Do not use for new RFQ creation or normal MaterialCall child RFQ editing.
    # New RFQ workflow must use:
    # MaterialCall -> Create RFQ -> child PriceRequest.
    # Automation must not call this helper for new RFQs; use the MaterialCall-aware
    # service path plus approval/proposal flow instead.
    if not material_rows:
        raise ValueError("Add at least one material row before saving the RFQ draft.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        cur.execute(
            """
            SELECT
                "PriceRequestID",
                "EstimateID",
                "Status",
                "Notes",
                "MaterialRequestSourceType",
                "MaterialRequestSourceID"
            FROM "PriceRequest"
            WHERE "PriceRequestID" = %s
            """,
            (rfq_id,),
        )
        rfq_row = cur.fetchone()
        if not rfq_row:
            raise ValueError(f"RFQ #{rfq_id} was not found.")
        if str(rfq_row.get("Status") or "").strip().lower() == "sent":
            raise ValueError("Sent RFQs cannot be updated through Material Request Workspace.")

        estimate_id = int(rfq_row["EstimateID"])
        cur.execute(
            """
            SELECT "PRItemID", "MaterialID"
            FROM "PriceRequestItem"
            WHERE "PriceRequestID" = %s
            ORDER BY "PRItemID"
            """,
            (rfq_id,),
        )
        existing_rows = cur.fetchall()

        metadata = _parse_material_request_metadata(rfq_row.get("Notes"))
        persisted_source_type = rfq_row.get("MaterialRequestSourceType") or metadata.get("source_type") or "Estimate"
        persisted_source_id = (
            rfq_row.get("MaterialRequestSourceID")
            or metadata.get("source_id")
            or estimate_id
        )
        notes_payload = _build_material_request_notes(
            source_type=str(persisted_source_type),
            source_id=int(persisted_source_id),
            material_rows=material_rows,
            rfq_notes=rfq_notes,
            rfq_id=int(rfq_id),
            is_update=True,
        )
        normalized_due_date = _normalize_material_request_due_date(due_date)

        reusable_count = min(len(existing_rows), len(material_rows))
        for index in range(reusable_count):
            existing = existing_rows[index]
            row = material_rows[index]
            estimate_material_id = int(existing["MaterialID"])
            _update_manual_estimate_material(cur, estimate_material_id, row)
            cur.execute(
                """
                UPDATE "PriceRequestItem"
                SET "QuantityOverride" = %s
                WHERE "PRItemID" = %s
                """,
                (float(row.get("qty") or 0), int(existing["PRItemID"])),
            )

        for row in material_rows[reusable_count:]:
            estimate_material_id = _insert_manual_estimate_material(cur, estimate_id, row)
            cur.execute(
                """
                INSERT INTO "PriceRequestItem" (
                    "PriceRequestID",
                    "MaterialID",
                    "QuantityOverride",
                    "QuotedUnitPrice",
                    "IsSubstitute",
                    "SubstituteNotes",
                    "IsCarried"
                )
                VALUES (%s, %s, %s, NULL, FALSE, NULL, FALSE)
                """,
                (
                    int(rfq_id),
                    estimate_material_id,
                    float(row.get("qty") or 0),
                ),
            )

        for existing in existing_rows[reusable_count:]:
            cur.execute(
                'DELETE FROM "PriceRequestItem" WHERE "PRItemID" = %s',
                (int(existing["PRItemID"]),),
            )

        cur.execute(
            """
            UPDATE "PriceRequest"
            SET "DateSent" = NULL,
                "Status" = 'Draft',
                "DueDate" = %s,
                "Notes" = %s,
                "MaterialRequestSourceType" = %s,
                "MaterialRequestSourceID" = %s
            WHERE "PriceRequestID" = %s
            """,
            (
                normalized_due_date,
                notes_payload,
                str(_normalize_material_request_source_type(str(persisted_source_type))),
                int(persisted_source_id),
                int(rfq_id),
            ),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_material_call_backed_rfq_draft_header(
    rfq_id: int,
    *,
    due_date=None,
    rfq_notes: str | None = None,
) -> None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        cur.execute(
            """
            SELECT
                "PriceRequestID",
                "MaterialCallID",
                "Status",
                "Notes"
            FROM "PriceRequest"
            WHERE "PriceRequestID" = %s
            """,
            (int(rfq_id),),
        )
        rfq_row = cur.fetchone()
        if not rfq_row:
            raise ValueError(f"RFQ #{rfq_id} was not found.")
        if rfq_row.get("MaterialCallID") in (None, "", 0, "0"):
            raise ValueError(
                f"RFQ #{rfq_id} is not MaterialCall-backed. Use the legacy compatibility updater for loose RFQs."
            )
        normalized_status = str(rfq_row.get("Status") or "").strip().lower()
        if normalized_status == "sent":
            raise ValueError("Sent RFQs cannot be updated through Create RFQ.")
        if normalized_status == "locked":
            raise ValueError("Locked RFQs cannot be updated through Create RFQ.")

        normalized_due_date = _normalize_material_request_due_date(due_date)
        existing_lines = [str(line or "") for line in str(rfq_row.get("Notes") or "").splitlines()]
        filtered_lines = [
            line
            for line in existing_lines
            if not line.startswith("RFQ Notes:")
            and not line.startswith("Request Notes:")
        ]
        clean_notes = str(rfq_notes or "").strip()
        if clean_notes:
            filtered_lines.append(f"RFQ Notes: {clean_notes}")
        notes_payload = "\n".join(line for line in filtered_lines if str(line).strip())

        cur.execute(
            """
            UPDATE "PriceRequest"
            SET "DueDate" = %s,
                "Notes" = %s,
                "Status" = 'Draft'
            WHERE "PriceRequestID" = %s
            """,
            (
                normalized_due_date,
                notes_payload,
                int(rfq_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def lock_rfq_draft(rfq_id: int) -> dict[str, object]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        cur.execute(
            """
            SELECT
                pr."PriceRequestID",
                pr."MaterialCallID",
                pr."VendorID",
                pr."Status",
                pr."DueDate",
                pr."DateSent",
                v."VendorName"
            FROM "PriceRequest" pr
            LEFT JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."PriceRequestID" = %s
            """,
            (int(rfq_id),),
        )
        rfq_row = cur.fetchone()
        if not rfq_row:
            raise ValueError(f"RFQ #{rfq_id} was not found.")
        if rfq_row.get("MaterialCallID") in (None, "", 0, "0"):
            raise ValueError("Select a MaterialCall-backed RFQ first.")
        if rfq_row.get("VendorID") in (None, "", 0, "0"):
            raise ValueError("Select an RFQ with a saved vendor before locking it.")

        normalized_status = str(rfq_row.get("Status") or "").strip().lower()
        if normalized_status == "sent":
            raise ValueError("Sent RFQs cannot be locked again.")
        if normalized_status not in {"", "draft", "open", "locked"}:
            raise ValueError(
                f"Only draft RFQs can be locked. Current status is {rfq_row.get('Status') or 'Unknown'}."
            )
        if normalized_status != "locked":
            cur.execute(
                'UPDATE "PriceRequest" SET "Status" = %s WHERE "PriceRequestID" = %s',
                ("Locked", int(rfq_id)),
            )
            conn.commit()

        return {
            "rfq_id": int(rfq_row["PriceRequestID"]),
            "material_call_id": int(rfq_row["MaterialCallID"]),
            "vendor_id": int(rfq_row["VendorID"]),
            "vendor_name": str(rfq_row.get("VendorName") or ""),
            "due_date": rfq_row.get("DueDate"),
            "date_sent": rfq_row.get("DateSent"),
            "status": "Locked",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _parse_material_request_metadata(notes_text: str | None) -> dict[str, object]:
    raw_notes = str(notes_text or "")
    source_type: str | None = None
    source_id: int | None = None
    rfq_notes = ""
    row_notes: dict[int, str] = {}
    for line in raw_notes.splitlines():
        line = line.strip()
        if "SourceType=" in line and "SourceID=" in line:
            try:
                source_type = line.split("SourceType=", 1)[1].split(".", 1)[0].split(" ", 1)[0].strip()
            except Exception:
                source_type = None
            try:
                source_id_text = line.split("SourceID=", 1)[1].split(".", 1)[0].split(" ", 1)[0].strip()
                source_id = int(source_id_text)
            except Exception:
                source_id = None
            continue
        if line.startswith("SourceType="):
            source_type = line.split("=", 1)[1].strip() or source_type
            continue
        if line.startswith("SourceID="):
            try:
                source_id = int(line.split("=", 1)[1].strip())
            except Exception:
                pass
            continue
        if line.startswith("RFQ Notes:"):
            rfq_notes = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Request Notes:") and not rfq_notes:
            rfq_notes = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Row ") and " note:" in line:
            try:
                index_text, note_text = line.split(" note:", 1)
                row_number = int(index_text.replace("Row", "").strip())
                row_notes[row_number] = note_text.strip()
            except Exception:
                continue
    return {
        "source_type": source_type,
        "source_id": source_id,
        "rfq_notes": rfq_notes,
        "row_notes": row_notes,
    }


def _extract_material_request_source_fields(row: dict | None) -> tuple[str | None, int | None]:
    if not row:
        return None, None
    source_type = row.get("MaterialRequestSourceType")
    source_id = row.get("MaterialRequestSourceID")
    if source_type and source_id not in (None, ""):
        try:
            return str(source_type), int(source_id)
        except (TypeError, ValueError):
            pass
    metadata = _parse_material_request_metadata(row.get("Notes"))
    source_type = metadata.get("source_type")
    source_id = metadata.get("source_id")
    if source_type and source_id not in (None, ""):
        try:
            return str(source_type), int(source_id)
        except (TypeError, ValueError):
            return None, None
    estimate_id = row.get("EstimateID")
    if estimate_id not in (None, ""):
        try:
            return "Estimate", int(estimate_id)
        except (TypeError, ValueError):
            return None, None
    return None, None


def _apply_material_request_source_context(row: dict | None, *, cur=None) -> dict | None:
    if not row:
        return row
    metadata = _parse_material_request_metadata(row.get("Notes"))
    source_type, source_id = _extract_material_request_source_fields(row)
    row["MaterialRequestSourceType"] = source_type
    row["MaterialRequestSourceID"] = source_id
    row["MaterialRequestNotes"] = metadata.get("rfq_notes") or ""
    row["MaterialRequestSourceLabel"] = ""
    row["MaterialRequestSelectorLabel"] = "Source Document #"
    row["MaterialRequestWorkOrderID"] = None
    if source_type and source_id:
        try:
            context = resolve_material_request_source_context(source_type, int(source_id), cur=cur, require_eligible=False)
            row["MaterialRequestSourceType"] = context.get("source_type")
            row["MaterialRequestSourceID"] = context.get("source_id")
            row["MaterialRequestSourceLabel"] = context.get("source_document_label") or ""
            row["MaterialRequestSelectorLabel"] = context.get("selector_label") or "Source Document #"
            row["MaterialRequestWorkOrderID"] = context.get("work_order_id")
        except Exception:
            normalized_source_type = _normalize_material_request_source_type(source_type)
            row["MaterialRequestSourceType"] = normalized_source_type
            row["MaterialRequestSourceID"] = int(source_id)
            row["MaterialRequestSelectorLabel"] = "Work Order #" if normalized_source_type == "WorkOrder" else "Estimate #"
            row["MaterialRequestSourceLabel"] = (
                f"Work Order #{int(source_id)}" if normalized_source_type == "WorkOrder" else f"Estimate #{int(source_id)}"
            )
            row["MaterialRequestWorkOrderID"] = int(source_id) if normalized_source_type == "WorkOrder" else None
    return row


def get_material_request_source_context_for_rfq(rfq_id: int, *, cur=None):
    owns_connection = cur is None
    conn = None
    if owns_connection:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        cur.execute(
            """
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."Notes",
                pr."MaterialRequestSourceType",
                pr."MaterialRequestSourceID"
            FROM "PriceRequest" pr
            WHERE pr."PriceRequestID" = %s
            """,
            (rfq_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return _apply_material_request_source_context(dict(row), cur=cur)
    finally:
        if owns_connection and conn is not None:
            conn.close()


def get_rfq_for_material_request(rfq_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        cur.execute(
            """
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."VendorID",
                pr."DueDate",
                pr."DateSent",
                pr."Status",
                pr."Notes",
                pr."MaterialRequestSourceType",
                pr."MaterialRequestSourceID",
                pr."MaterialCallID",
                mc."MaterialCallNumber",
                v."VendorName"
            FROM "PriceRequest" pr
            LEFT JOIN "MaterialCall" mc ON pr."MaterialCallID" = mc."MaterialCallID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."PriceRequestID" = %s
            """,
            (rfq_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return _apply_material_request_source_context(row, cur=cur)
    finally:
        conn.close()


def get_rfq_items_for_material_request(rfq_id: int):
    rows = list(get_rfq_requested_material_rows(rfq_id) or [])
    return rows


def _catalog_price_source_sql(material_alias: str = "m") -> str:
    alias = material_alias
    return f"""
        CASE COALESCE({alias}."CarryPriceSource", 'Internal')
            WHEN 'Nedco' THEN COALESCE({alias}."NedcoPrice", {alias}."InternalPrice", 0)
            WHEN 'Gescan' THEN COALESCE({alias}."GescanPrice", {alias}."InternalPrice", 0)
            WHEN 'Eecol' THEN COALESCE({alias}."EecolPrice", {alias}."InternalPrice", 0)
            WHEN 'Guillevin' THEN COALESCE({alias}."GuillevinPrice", {alias}."InternalPrice", 0)
            ELSE COALESCE({alias}."InternalPrice", 0)
        END
    """


def get_rfq_requested_material_rows(rfq_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        catalog_price_sql = _catalog_price_source_sql("m")
        cur.execute(
            f"""
            SELECT
                pri."PRItemID",
                pri."MaterialID" AS "EstimateMaterialID",
                pri."MaterialCallItemID",
                pr."MaterialCallID",
                mc."MaterialCallNumber",
                COALESCE(pri."QuantityOverride", mci."Quantity", em."Quantity", 0) AS "Quantity",
                COALESCE(NULLIF(TRIM(mci."Unit"), ''), NULLIF(TRIM(m."Unit"), ''), '') AS "Unit",
                COALESCE(NULLIF(TRIM(mci."PartNumber"), ''), NULLIF(TRIM(em."PartNumber"), ''), NULLIF(TRIM(m."PartNumber"), ''), '') AS "PartNumber",
                COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') AS "Description",
                COALESCE(
                    NULLIF(({catalog_price_sql}), 0),
                    NULLIF(em."UnitCost", 0),
                    0
                ) AS "UnitCost",
                COALESCE(mci."MaterialID", em."ItemID") AS "CatalogItemID",
                COALESCE(NULLIF(TRIM(mci."Notes"), ''), '') AS "MaterialCallNotes"
            FROM "PriceRequestItem" pri
            JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
            LEFT JOIN "MaterialCall" mc ON pr."MaterialCallID" = mc."MaterialCallID"
            LEFT JOIN "MaterialCallItem" mci ON pri."MaterialCallItemID" = mci."MaterialCallItemID"
            LEFT JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            LEFT JOIN "Material" m ON COALESCE(mci."MaterialID", em."ItemID") = m."ItemID"
            WHERE pri."PriceRequestID" = %s
            ORDER BY COALESCE(pri."MaterialCallItemID", pri."PRItemID") ASC, pri."PRItemID" ASC
            """,
            (rfq_id,),
        )
        rows = cur.fetchall()

        cur.execute('SELECT COALESCE("Notes", \'\') AS "Notes" FROM "PriceRequest" WHERE "PriceRequestID" = %s', (rfq_id,))
        notes_row = cur.fetchone() or {}
        metadata = _parse_material_request_metadata(notes_row.get("Notes"))
        note_lines = metadata.get("row_notes") or {}
        for index, row in enumerate(rows, start=1):
            row["Notes"] = str(row.get("MaterialCallNotes") or "").strip() or note_lines.get(index, "")
            row.pop("MaterialCallNotes", None)
        return rows
    finally:
        conn.close()


def duplicate_rfq_for_vendor(source_rfq_id: int, target_vendor_id: int) -> int:
    # LEGACY_COMPAT_ONLY:
    # Supports old loose RFQ / PriceRequest compatibility records.
    # Do not use for new RFQ creation. New RFQ workflow must use:
    # MaterialCall -> Create RFQ -> child PriceRequest.
    # Automation must not call this helper for new RFQs; use the MaterialCall-aware
    # service path plus approval/proposal flow instead.
    if not target_vendor_id:
        raise ValueError("Select a target vendor first.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        cur.execute(
            """
            SELECT
                "PriceRequestID",
                "EstimateID",
                "VendorID",
                "DueDate",
                "Notes",
                "MaterialRequestSourceType",
                "MaterialRequestSourceID"
            FROM "PriceRequest"
            WHERE "PriceRequestID" = %s
            """,
            (source_rfq_id,),
        )
        source_header = cur.fetchone()
        if not source_header:
            raise ValueError(f"RFQ #{source_rfq_id} was not found.")

        metadata = _parse_material_request_metadata(source_header.get("Notes"))
        source_context = _apply_material_request_source_context(source_header, cur=cur) or source_header
        persisted_source_type = source_context.get("MaterialRequestSourceType") or "Estimate"
        persisted_source_id = int(
            source_context.get("MaterialRequestSourceID")
            or source_header.get("EstimateID")
            or 0
        )
        cur.execute(
            """
            INSERT INTO "PriceRequest" (
                "EstimateID",
                "VendorID",
                "DueDate",
                "DateSent",
                "Status",
                "Notes",
                "MaterialRequestSourceType",
                "MaterialRequestSourceID"
            )
            VALUES (%s, %s, %s, NULL, 'Draft', %s, %s, %s)
            RETURNING "PriceRequestID"
            """,
            (
                int(source_header["EstimateID"]),
                int(target_vendor_id),
                source_header.get("DueDate"),
                _build_material_request_notes(
                    source_type=str(persisted_source_type),
                    source_id=int(persisted_source_id),
                    material_rows=[],
                    rfq_notes=str(metadata.get("rfq_notes") or ""),
                    is_update=False,
                ),
                str(_normalize_material_request_source_type(str(persisted_source_type))),
                int(persisted_source_id),
            ),
        )
        new_rfq_id = int(cur.fetchone()["PriceRequestID"])

        cur.execute(
            """
            SELECT "PRItemID", "MaterialID", "QuantityOverride"
            FROM "PriceRequestItem"
            WHERE "PriceRequestID" = %s
            ORDER BY "PRItemID" ASC
            """,
            (source_rfq_id,),
        )
        source_items = cur.fetchall()
        if not source_items:
            raise ValueError("The selected RFQ has no material rows to duplicate.")

        row_notes = metadata.get("row_notes") or {}
        material_rows_for_notes = []
        for index, item in enumerate(source_items, start=1):
            material_rows_for_notes.append({"notes": row_notes.get(index, "")})
            cur.execute(
                """
                INSERT INTO "PriceRequestItem" (
                    "PriceRequestID",
                    "MaterialID",
                    "QuantityOverride",
                    "QuotedUnitPrice",
                    "IsSubstitute",
                    "SubstituteNotes",
                    "IsCarried"
                )
                VALUES (%s, %s, %s, NULL, FALSE, NULL, FALSE)
                """,
                (
                    new_rfq_id,
                    int(item["MaterialID"]),
                    float(item.get("QuantityOverride") or 0),
                ),
            )

        cur.execute(
            'UPDATE "PriceRequest" SET "Notes" = %s WHERE "PriceRequestID" = %s',
            (
                _build_material_request_notes(
                    source_type=str(metadata.get("source_type") or "Estimate"),
                    source_id=int(metadata.get("source_id") or source_header["EstimateID"]),
                    material_rows=material_rows_for_notes,
                    rfq_notes=str(metadata.get("rfq_notes") or ""),
                    is_update=False,
                ),
                new_rfq_id,
            ),
        )

        conn.commit()
        return new_rfq_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_vendor_choices():
    """Returns vendor rows for RFQ selection dialogs."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT "VendorID", "VendorName", "VendorContactName", "VendorContactNumber"
            FROM "Vendor"
            ORDER BY "VendorName"
        """)
        return cur.fetchall()
    finally:
        conn.close()

def get_estimate_rfq_status(estimate_id: int):
    """Returns RFQ coverage and quote-return status for an estimate."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COUNT(*) AS "MaterialCount"
            FROM "EstimateMaterial"
            WHERE "EstimateID" = %s
        """, (estimate_id,))
        material_count = int(cur.fetchone()["MaterialCount"] or 0)

        cur.execute("""
            SELECT COUNT(*) AS "RFQCount"
            FROM "PriceRequest"
            WHERE "EstimateID" = %s
        """, (estimate_id,))
        rfq_count = int(cur.fetchone()["RFQCount"] or 0)

        cur.execute("""
            SELECT COUNT(*) AS "ReturnedQuoteCount"
            FROM "PriceRequest" pr
            WHERE pr."EstimateID" = %s
              AND (
                    pr."VendorQuoteNumber" IS NOT NULL
                 OR pr."QuoteFilePath" IS NOT NULL
                 OR EXISTS (
                        SELECT 1
                        FROM "PriceRequestItem" pri
                        WHERE pri."PriceRequestID" = pr."PriceRequestID"
                          AND pri."QuotedUnitPrice" IS NOT NULL
                    )
              )
        """, (estimate_id,))
        returned_quote_count = int(cur.fetchone()["ReturnedQuoteCount"] or 0)

        return {
            "material_count": material_count,
            "rfq_count": rfq_count,
            "returned_quote_count": returned_quote_count,
            "has_materials": material_count > 0,
            "has_rfq": rfq_count > 0,
            "has_returned_quotes": returned_quote_count > 0,
        }
    finally:
        conn.close()

def _build_rfq_pdf(estimate_row, material_rows, vendor_name, rfq_id):
    """Builds a simple RFQ PDF in the project RFQ folder."""
    target_dir = get_target_folder(
        estimate_row.get("StreetNumber"),
        estimate_row.get("StreetName"),
        estimate_row.get("SiteName"),
        estimate_row["EstimateID"],
        "RFQs"
    )
    safe_vendor = re.sub(r"[^A-Za-z0-9_-]+", "_", vendor_name or "Vendor").strip("_") or "Vendor"
    pdf_path = os.path.join(target_dir, f"RFQ_{estimate_row['EstimateID']}_{safe_vendor}_{rfq_id}.pdf")

    lines = [
        f"Estimate ID: {estimate_row['EstimateID']}",
        f"Customer: {estimate_row.get('CustomerName') or 'Unknown'}",
        f"Project Site: {estimate_row.get('SiteName') or 'Unknown'}",
        f"Project Address: {estimate_row.get('ProjectAddress') or 'Unknown'}",
        f"Requested Vendor: {vendor_name}",
        f"Date: {datetime.date.today()}",
        "",
        "Requested Materials:"
    ]
    for item in material_rows:
        qty = item.get("Quantity") or 0
        desc = item.get("Description") or "Material"
        lines.append(f"- Qty {qty}: {desc}")

    return _write_simple_pdf(
        pdf_path,
        f"Request For Quote - Estimate #{estimate_row['EstimateID']}",
        lines
    )


def _load_estimate_rfq_estimate_row(cur, estimate_id: int):
    cur.execute(
        """
        SELECT
            e."EstimateID",
            e."Description",
            c."CustomerName",
            s."SiteName",
            s."StreetNumber",
            s."StreetName",
            CONCAT_WS(', ', CONCAT_WS(' ', s."StreetNumber", s."StreetName"), s."City") AS "ProjectAddress"
        FROM "Estimate" e
        JOIN "Site" s ON e."SiteID" = s."SiteID"
        JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
        WHERE e."EstimateID" = %s
        """,
        (estimate_id,),
    )
    return cur.fetchone()


def _load_estimate_rfq_material_rows(estimate_id: int, material_ids=None):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        params = [estimate_id]
        material_sql = ""
        if material_ids:
            placeholders = ",".join(["%s"] * len(material_ids))
            material_sql = f' AND em."EstimateMaterialID" IN ({placeholders})'
            params.extend(material_ids)

        estimate_row = _load_estimate_rfq_estimate_row(cur, estimate_id)
        if not estimate_row:
            return None, []

        cur.execute(
            f"""
            SELECT
                em."EstimateMaterialID",
                em."Quantity",
                COALESCE(NULLIF(TRIM(m."Unit"), ''), '') AS "Unit",
                COALESCE(NULLIF(TRIM(em."PartNumber"), ''), NULLIF(TRIM(m."PartNumber"), ''), '') AS "PartNumber",
                COALESCE(NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') AS "Description",
                '' AS "Notes"
            FROM "EstimateMaterial" em
            LEFT JOIN "Material" m ON em."ItemID" = m."ItemID"
            WHERE em."EstimateID" = %s
            {material_sql}
            ORDER BY em."EstimateMaterialID"
            """,
            params,
        )
        return estimate_row, cur.fetchall()
    finally:
        conn.close()


def build_rfq_preview_text(estimate_id: int, vendor_name: str, due_date: str = None, material_ids=None, rfq_id: int = None):
    estimate_row, material_rows = _load_estimate_rfq_material_rows(estimate_id, material_ids=material_ids)
    if not estimate_row:
        raise ValueError(f"Estimate #{estimate_id} could not be loaded.")
    if not material_rows:
        raise ValueError("This estimate has no material lines to include in the RFQ.")

    preview_lines = [
        f"RFQ #{rfq_id or 'Draft'}",
        f"Estimate: #{estimate_row['EstimateID']}",
        f"Vendor: {vendor_name or 'Not selected'}",
        f"Customer: {estimate_row.get('CustomerName') or 'Unknown'}",
        f"Site: {estimate_row.get('SiteName') or 'Unknown'}",
        f"Project Address: {estimate_row.get('ProjectAddress') or 'Unknown'}",
        f"Requested Due Date: {due_date or 'Not specified'}",
        "",
        "Requested Materials:",
    ]
    for item in material_rows:
        preview_lines.append(f"- Qty {float(item.get('Quantity') or 0):.2f}: {item.get('Description') or 'Material'}")
    return "\n".join(preview_lines)


def get_vendor_email_for_rfq(vendor_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT COALESCE(vc."Email", '') AS "Email"
            FROM "Vendor" v
            LEFT JOIN public."VendorContact" vc ON vc."VendorID" = v."VendorID"
            WHERE v."VendorID" = %s
            ORDER BY vc."VendorContactID" ASC
            LIMIT 1
            ''',
            (vendor_id,),
        )
        row = cur.fetchone()
        return ((row.get("Email") or "").strip().lower() or None) if row else None
    finally:
        conn.close()


def send_rfq_by_id(rfq_id: int, recipient_email: str = None):
    from neon_ai.gateway import send_to_user

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."VendorID",
                pr."DueDate",
                v."VendorName"
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."PriceRequestID" = %s
            """,
            (rfq_id,),
        )
        rfq_row = cur.fetchone()
        if not rfq_row:
            raise ValueError(f"RFQ #{rfq_id} could not be loaded.")

        cur.execute(
            '''
            SELECT pri."MaterialID"
            FROM "PriceRequestItem" pri
            WHERE pri."PriceRequestID" = %s
            ORDER BY pri."PRItemID"
            ''',
            (rfq_id,),
        )
        material_ids = [int(row["MaterialID"]) for row in cur.fetchall()]
    finally:
        conn.close()

    estimate_row, material_rows = _load_estimate_rfq_material_rows(rfq_row["EstimateID"], material_ids=material_ids)
    if not material_rows:
        raise ValueError("This RFQ has no packaged material lines to send.")

    email_to = (recipient_email or get_vendor_email_for_rfq(rfq_row["VendorID"]) or "").strip().lower()
    if not email_to:
        raise ValueError(
            f"RFQ #{rfq_id} does not have a vendor email on file yet. "
            "Add a vendor contact email first, then send the RFQ."
        )

    pdf_path = _build_rfq_pdf(estimate_row, material_rows, rfq_row["VendorName"], rfq_id)
    due_date_text = rfq_row.get("DueDate") or "Not specified"
    sent = send_to_user(
        subject=f"RFQ #{rfq_id} - Estimate #{rfq_row['EstimateID']} - {estimate_row['SiteName']}",
        content=(
            f"Hello {rfq_row['VendorName']},\n\n"
            f"Please find attached RFQ #{rfq_id} for Estimate #{rfq_row['EstimateID']} at {estimate_row['SiteName']}.\n\n"
            "Please review the attached material list and send your quote back at your earliest convenience.\n\n"
            f"Requested due date: {due_date_text}\n\n"
            "Best regards,\nArgon Electrical"
        ),
        recipient=email_to,
        attachment_path=pdf_path,
    )
    if not sent:
        raise RuntimeError(f"RFQ #{rfq_id} could not be sent through the email gateway.")

    mark_rfq_sent(
        rfq_id,
        estimate_id=rfq_row["EstimateID"],
        vendor_id=rfq_row["VendorID"],
        vendor_name=rfq_row["VendorName"],
        recipient_email=email_to,
        attachment_path=pdf_path,
    )
    return {
        "rfq_id": rfq_id,
        "recipient_email": email_to,
        "pdf_path": pdf_path,
        "vendor_name": rfq_row["VendorName"],
    }

def create_and_send_rfq_batch(estimate_id: int, vendor_requests: list, due_date: str = None):
    # LEGACY_COMPAT_ONLY:
    # This helper bypasses the current operator-reviewed Create RFQ drafting flow even
    # though it now creates MaterialCall-backed child RFQs.
    # Do not use for normal new RFQ creation. New RFQ workflow must use:
    # MaterialCall -> Create RFQ -> child PriceRequest.
    # Automation must not call this helper unless a MaterialCall-aware approval/proposal
    # flow explicitly owns it.
    """Creates one Material Call batch, then creates/sends sibling vendor RFQs under it."""
    batch = _create_material_call_backed_rfq_batch(
        estimate_id,
        vendor_requests,
        due_date=due_date,
        send=False,
    )

    sent_results = []
    send_failures = []
    for row in batch["results"]:
        try:
            send_result = send_rfq_by_id(int(row["rfq_id"]), recipient_email=row["recipient_email"])
            sent_results.append(
                {
                    **row,
                    "recipient_email": send_result["recipient_email"],
                    "pdf_path": send_result["pdf_path"],
                    "sent": True,
                }
            )
        except Exception as exc:
            send_failures.append(
                {
                    "vendor_name": row["vendor_name"],
                    "rfq_id": int(row["rfq_id"]),
                    "error": str(exc),
                }
            )
            break

    if send_failures:
        created_count = len(batch["results"])
        sent_count = len(sent_results)
        failure = send_failures[0]
        raise RuntimeError(
            f"Material Call {batch['material_call_number']} created {created_count} RFQ(s). "
            f"Send stopped after {sent_count} successful email(s). "
            f"RFQ #{failure['rfq_id']} for {failure['vendor_name']} failed to send: {failure['error']}. "
            "The created draft RFQs remain available in the RFQ workspace for manual review."
        )

    return sent_results


def create_rfq_batch_without_sending(estimate_id: int, vendor_requests: list, due_date: str = None):
    # LEGACY_COMPAT_ONLY:
    # This helper bypasses the current operator-reviewed Create RFQ drafting flow even
    # though it now creates MaterialCall-backed child RFQs.
    # Do not use for normal new RFQ creation. New RFQ workflow must use:
    # MaterialCall -> Create RFQ -> child PriceRequest.
    # Automation must not call this helper unless a MaterialCall-aware approval/proposal
    # flow explicitly owns it.
    """Creates one Material Call batch and sibling draft RFQs without sending email."""
    batch = _create_material_call_backed_rfq_batch(
        estimate_id,
        vendor_requests,
        due_date=due_date,
        send=False,
    )
    return batch["results"]


def _normalize_batch_vendor_requests(vendor_requests: list) -> list[dict]:
    normalized: list[dict] = []
    seen_vendor_ids: set[int] = set()
    for request in vendor_requests or []:
        vendor_id = int(request["vendor_id"])
        vendor_name = str(request.get("vendor_name") or "").strip() or f"Vendor #{vendor_id}"
        recipient_email = str(request.get("recipient_email") or "").strip()
        if not recipient_email:
            raise ValueError(f"Recipient email is required for {vendor_name}.")
        if vendor_id in seen_vendor_ids:
            raise ValueError(f"{vendor_name} was selected more than once in this RFQ batch.")
        seen_vendor_ids.add(vendor_id)
        normalized.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "recipient_email": recipient_email,
            }
        )
    if not normalized:
        raise ValueError("Select at least one wholesaler to create RFQs.")
    return normalized


def _create_material_call_backed_rfq_batch(
    estimate_id: int,
    vendor_requests: list,
    *,
    due_date: str | None = None,
    send: bool = False,
):
    from neon_ai.database.material_calls import (
        create_material_call_from_estimate_items,
        create_vendor_rfq_for_material_call,
        get_material_call,
    )

    estimate_row, material_rows = _load_estimate_rfq_material_rows(estimate_id)
    if not estimate_row:
        raise ValueError(f"Estimate #{estimate_id} could not be loaded.")
    if not material_rows:
        raise ValueError("This estimate has no material lines to package into an RFQ.")
    normalized_requests = _normalize_batch_vendor_requests(vendor_requests)
    material_ids = [int(row["EstimateMaterialID"]) for row in material_rows]
    material_call_id = create_material_call_from_estimate_items(
        int(estimate_id),
        material_ids,
        notes=f"Estimate RFQ batch for Estimate #{int(estimate_id)}",
    )
    material_call = get_material_call(int(material_call_id)) or {}
    material_call_number = str(
        material_call.get("MaterialCallNumber") or f"MC#{int(material_call_id)}"
    ).strip()

    results = []
    for request in normalized_requests:
        try:
            created = create_vendor_rfq_for_material_call(
                int(material_call_id),
                int(request["vendor_id"]),
                due_date=due_date,
                notes=f"Estimate RFQ batch for {request['vendor_name']}",
            )
        except Exception as exc:
            raise RuntimeError(
                f"Material Call {material_call_number} was created for Estimate #{int(estimate_id)}, "
                f"but vendor RFQ creation stopped after {len(results)} successful vendor RFQ(s). "
                f"Vendor {request['vendor_name']} failed: {exc}"
            ) from exc

        results.append(
            {
                "rfq_id": int(created["rfq_id"]),
                "estimate_id": int(estimate_id),
                "vendor_id": int(request["vendor_id"]),
                "vendor_name": request["vendor_name"],
                "recipient_email": request["recipient_email"],
                "due_date": due_date,
                "created_without_send": not send,
                "material_call_id": int(material_call_id),
                "material_call_number": material_call_number,
                "item_count": int(created.get("item_count") or len(material_ids)),
            }
        )

    return {
        "material_call_id": int(material_call_id),
        "material_call_number": material_call_number,
        "estimate_id": int(estimate_id),
        "results": results,
    }


def mark_rfq_sent(
    rfq_id: int,
    *,
    estimate_id: int,
    vendor_id: int,
    vendor_name: str,
    recipient_email: str,
    attachment_path: str,
) -> None:
    from neon_ai.database.automation import log_estimate_action

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            UPDATE "PriceRequest"
            SET "DateSent" = CURRENT_DATE,
                "Status" = 'Sent'
            WHERE "PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        conn.commit()
    finally:
        conn.close()

    append_rfq_dispatch_record({
        "rfq_id": rfq_id,
        "estimate_id": estimate_id,
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "recipient_email": str(recipient_email or "").strip().lower(),
        "sent_at": datetime.datetime.now().isoformat(),
    })
    log_estimate_action(
        estimate_id,
        f"RFQ #{rfq_id} emailed to {vendor_name} at {recipient_email}. File: {attachment_path}",
        "RFQ"
    )

def classify_vendor_rfq_response(subject, body_text):
    text = f"{subject or ''}\n{body_text or ''}".lower()
    decline_patterns = [
        r"\bno bid\b", r"\bnot bidding\b", r"\bwon't be quoting\b", r"\bwill not be quoting\b",
        r"\bcan't quote\b", r"\bcannot quote\b", r"\bunfortunately.*cannot\b", r"\bunable to quote\b",
        r"\bpass on this\b", r"\bnot able to price\b"
    ]
    eta_markers = [
        r"\bby tomorrow\b", r"\bby end of day\b", r"\beod\b", r"\bnext week\b", r"\bthis afternoon\b",
        r"\btomorrow\b", r"\bfriday\b", r"\bmonday\b", r"\btuesday\b", r"\bwednesday\b", r"\bthursday\b",
        r"\bsend.*later\b", r"\bhave.*to you\b", r"\bshould have\b", r"\bexpect\b", r"\bturn this around\b",
        r"\bwithin \d+ (hour|hours|day|days)\b", r"\bonce .* gets back\b"
    ]
    ack_markers = [
        r"\bwe are quoting\b", r"\bworking on it\b", r"\bpricing it now\b", r"\bwe will quote\b",
        r"\bwe'll quote\b", r"\bputting this together\b", r"\blooking at this\b", r"\bon it\b"
    ]

    if any(re.search(pattern, text) for pattern in decline_patterns):
        return "Declined"
    if any(re.search(pattern, text) for pattern in eta_markers):
        return "ETA Provided"
    if any(re.search(pattern, text) for pattern in ack_markers):
        return "Acknowledged"
    return None

def extract_eta_text(subject, body_text):
    combined = f"{subject or ''}\n{body_text or ''}"
    sentences = re.split(r'(?<=[.!?])\s+|\n+', combined)
    eta_patterns = [
        r"\bby tomorrow\b", r"\bby end of day\b", r"\beod\b", r"\bnext week\b", r"\bthis afternoon\b",
        r"\btomorrow\b", r"\bfriday\b", r"\bmonday\b", r"\btuesday\b", r"\bwednesday\b", r"\bthursday\b",
        r"\bwithin \d+ (hour|hours|day|days)\b", r"\bexpect\b", r"\bshould have\b", r"\bhave .* to you\b"
    ]
    for sentence in sentences:
        lowered = sentence.lower()
        if any(re.search(pattern, lowered) for pattern in eta_patterns):
            return sentence.strip()[:255]
    return None

def update_rfq_status_and_notes(rfq_id, status, note_text=None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if note_text:
            cur.execute("""
                UPDATE "PriceRequest"
                SET "Status" = %s,
                    "Notes" = CASE
                        WHEN COALESCE("Notes", '') = '' THEN %s
                        ELSE CONCAT("Notes", E'\n', %s)
                    END
                WHERE "PriceRequestID" = %s
            """, (status, note_text, note_text, rfq_id))
        else:
            cur.execute('UPDATE "PriceRequest" SET "Status" = %s WHERE "PriceRequestID" = %s', (status, rfq_id))
        conn.commit()
    finally:
        conn.close()

def process_vendor_rfq_response_without_quote(rfq_id, sender_email, subject, body, received_at=None):
    """Handles RFQ vendor replies that do not yet contain usable pricing."""
    from neon_ai.database.automation import log_estimate_action

    rfq_context = get_rfq_context(rfq_id)
    if not rfq_context:
        return None

    response_state = classify_vendor_rfq_response(subject, body) or "Pending"
    eta_text = extract_eta_text(subject, body)
    if response_state == "Pending" and eta_text:
        response_state = "ETA Provided"

    note_bits = [f"Vendor RFQ response received from {sender_email}."]
    if response_state == "Declined":
        note_bits.append("Vendor indicated they will not be providing pricing.")
    elif response_state == "ETA Provided":
        note_bits.append(f"Vendor provided a timing indication: {eta_text or 'Timing mentioned in email.'}")
    elif response_state == "Acknowledged":
        note_bits.append("Vendor acknowledged the RFQ but did not provide pricing yet.")
    else:
        note_bits.append("Reply received but no pricing or clear timing commitment was detected.")

    note_bits.append(f"Subject: {subject}")
    update_rfq_status_and_notes(rfq_id, response_state, " ".join(note_bits))
    log_estimate_action(
        rfq_context["EstimateID"],
        f"RFQ #{rfq_id} vendor response logged. Status -> {response_state}. Sender: {sender_email}. ETA: {eta_text or 'None provided'}.",
        "RFQ"
    )
    return {
        "rfq_id": rfq_id,
        "estimate_id": rfq_context["EstimateID"],
        "vendor_name": rfq_context["VendorName"],
        "site_name": rfq_context["SiteName"],
        "status": response_state,
        "eta_text": eta_text,
    }

def get_rfq_recipient_email(rfq_id):
    entries = load_rfq_dispatch_log()
    matches = [row for row in entries if row.get("rfq_id") == rfq_id]
    if not matches:
        return None
    matches.sort(key=lambda row: row.get("sent_at", ""), reverse=True)
    return matches[0].get("recipient_email")

def sweep_for_outstanding_rfq_followups():
    """Sends 48-hour vendor reminders and 72-hour owner follow-up alerts for outstanding RFQs."""
    from neon_ai.gateway import send_to_user
    from neon_ai.database.automation import log_estimate_action

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."DateSent",
                pr."Status",
                pr."Notes",
                c."CustomerName",
                s."SiteName",
                v."VendorName"
            FROM "PriceRequest" pr
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."Status" NOT IN ('Quote Received', 'Declined', 'Owner Follow-Up Needed')
              AND pr."DateSent" >= %s
            ORDER BY pr."DateSent" ASC, pr."PriceRequestID" ASC
        """, (AUTOMATION_MIN_DATE,))
        rfqs = cur.fetchall()
    finally:
        conn.close()

    today = datetime.date.today()
    for rfq in rfqs:
        date_sent = rfq.get("DateSent")
        if not date_sent:
            continue
        age_days = (today - date_sent).days
        recipient_email = get_rfq_recipient_email(rfq["PriceRequestID"])
        status = (rfq.get("Status") or "Pending").strip()
        notes = rfq.get("Notes") or ""

        if age_days >= 3:
            if status != "Owner Follow-Up Needed":
                send_to_user(
                    subject=f"Owner Follow-Up Needed: RFQ #{rfq['PriceRequestID']} - {rfq['VendorName']}",
                    content=(
                        f"RFQ #{rfq['PriceRequestID']} for {rfq['VendorName']} is still outstanding after 72 hours.\n\n"
                        f"Estimate: #{rfq['EstimateID']}\n"
                        f"Site: {rfq['SiteName']}\n"
                        f"Date sent: {date_sent}\n"
                        f"Current RFQ status: {status}\n"
                        f"Notes: {notes or 'None'}\n\n"
                        "No RFQ should remain open longer than 72 hours. Please follow up with the wholesaler directly."
                    )
                )
                update_rfq_status_and_notes(
                    rfq["PriceRequestID"],
                    "Owner Follow-Up Needed",
                    "72-hour owner follow-up alert sent because the RFQ remains outstanding."
                )
                log_estimate_action(
                    rfq["EstimateID"],
                    f"RFQ #{rfq['PriceRequestID']} exceeded 72 hours without pricing. Owner follow-up alert sent.",
                    "RFQ"
                )
            continue

        if age_days >= 2 and status not in ("ETA Provided", "Reminder Sent") and recipient_email:
            if not _background_vendor_rfq_followups_enabled():
                print(
                    "RFQ Sweeper: Background vendor follow-up is disabled. "
                    "Use manual RFQ follow-up/send workflow. "
                    f"RFQ #{rfq['PriceRequestID']} for {rfq['VendorName']} needs manual vendor follow-up."
                )
                continue

            send_to_user(
                subject=f"Friendly Follow Up: RFQ #{rfq['PriceRequestID']}",
                content=(
                    f"Hello {rfq['VendorName']},\n\n"
                    f"We're following up on RFQ #{rfq['PriceRequestID']} for {rfq['SiteName']}.\n\n"
                    "If you are planning to provide pricing, please reply to let us know when we should expect it. "
                    "If you are not quoting this one, a quick reply to let us know is appreciated as well.\n\n"
                    "Thank you,\nArgon Electrical"
                ),
                recipient=recipient_email
            )
            update_rfq_status_and_notes(
                rfq["PriceRequestID"],
                "Reminder Sent",
                "48-hour friendly reminder sent because no pricing or quote timing had been provided."
            )
            log_estimate_action(
                rfq["EstimateID"],
                f"48-hour reminder sent for RFQ #{rfq['PriceRequestID']} to {rfq['VendorName']}.",
                "RFQ"
            )

def extract_rfq_id_from_text(subject, body):
    text = f"{subject or ''}\n{body or ''}"
    match = re.search(r'\brfq\s*#?\s*(\d+)\b', text, re.IGNORECASE)
    return int(match.group(1)) if match else None

def find_matching_rfq_for_email(sender_email, subject="", body=""):
    """Finds the RFQ most likely associated with an inbound vendor quote email."""
    print(f"[RFQ] Looking for RFQ match for sender={sender_email} subject={subject[:80]!r}")
    rfq_id = extract_rfq_id_from_text(subject, body)
    if rfq_id:
        print(f"[RFQ] Matched RFQ directly from email text: RFQ #{rfq_id}")
        return rfq_id

    sender = (sender_email or "").strip().lower()
    if not sender:
        print("[RFQ] No sender email available for RFQ matching.")
        return None

    entries = load_rfq_dispatch_log()
    matches = [row for row in entries if row.get("recipient_email", "").strip().lower() == sender]
    if not matches:
        print("[RFQ] No RFQ dispatch-log match found for sender email.")
        return None

    matches.sort(key=lambda row: row.get("sent_at", ""), reverse=True)
    matched_rfq = matches[0].get("rfq_id")
    print(f"[RFQ] Matched RFQ from dispatch log: RFQ #{matched_rfq}")
    return matched_rfq

def get_rfq_context(rfq_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."VendorID",
                pr."VendorQuoteNumber",
                pr."MaterialCallID",
                pr."Status" AS "RFQStatus",
                e."Status" AS "EstimateStatus",
                c."CustomerName",
                s."SiteName",
                s."StreetNumber",
                s."StreetName",
                v."VendorName",
                mc."MaterialCallNumber"
            FROM "PriceRequest" pr
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            LEFT JOIN "MaterialCall" mc ON pr."MaterialCallID" = mc."MaterialCallID"
            WHERE pr."PriceRequestID" = %s
        """, (rfq_id,))
        return cur.fetchone()
    finally:
        conn.close()

def get_rfq_items_for_ingest(rfq_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT
                pri."PRItemID",
                pri."MaterialID",
                pri."MaterialCallItemID",
                pri."QuotedUnitPrice",
                pr."MaterialCallID",
                em."EstimateID",
                COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') AS "Description",
                COALESCE(pri."QuantityOverride", mci."Quantity", em."Quantity", 0) AS "Quantity",
                COALESCE(NULLIF(TRIM(mci."PartNumber"), ''), NULLIF(TRIM(em."PartNumber"), ''), NULLIF(TRIM(m."PartNumber"), ''), '') AS "PartNumber",
                COALESCE(mci."MaterialID", em."ItemID") AS "ItemID",
                COALESCE(NULLIF(TRIM(mci."Unit"), ''), NULLIF(TRIM(m."Unit"), ''), '') AS "Unit"
            FROM "PriceRequestItem" pri
            JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
            LEFT JOIN "MaterialCallItem" mci ON pri."MaterialCallItemID" = mci."MaterialCallItemID"
            LEFT JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            LEFT JOIN "Material" m ON COALESCE(mci."MaterialID", em."ItemID") = m."ItemID"
            WHERE pri."PriceRequestID" = %s
            ORDER BY
                COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') ASC,
                pri."PRItemID" ASC
        """, (rfq_id,))
        return cur.fetchall()
    finally:
        conn.close()

def extract_text_from_attachment(attachment_path):
    print(f"[RFQ] Extracting text from attachment: {attachment_path}")
    lower = attachment_path.lower()
    if lower.endswith(".pdf"):
        reader = PdfReader(attachment_path)
        chunks = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        text = "\n".join(chunks).strip()
        print(f"[RFQ] Native PDF extraction produced {len(text)} characters.")
        return text
    if lower.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
        text = ocr_image_file(attachment_path)
        print(f"[RFQ] Image OCR produced {len(text)} characters.")
        return text
    if lower.endswith(".txt") or lower.endswith(".csv"):
        with open(attachment_path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
        print(f"[RFQ] Text attachment produced {len(text)} characters.")
        return text
    print("[RFQ] Unsupported attachment type for direct text extraction.")
    return ""

def ocr_image_file(image_path):
    print(f"[RFQ] Running OCR on image file: {image_path}")
    engine = get_ocr_engine()
    result, _ = engine(image_path)
    if not result:
        print("[RFQ] OCR returned no text for image.")
        return ""
    text = "\n".join(item[1] for item in result if len(item) > 1)
    print(f"[RFQ] OCR image extraction produced {len(text)} characters.")
    return text

def render_pdf_pages_for_ocr(pdf_path, max_pages=4):
    doc = fitz.open(pdf_path)
    try:
        images = []
        for page_index in range(min(len(doc), max_pages)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            images.append(img)
        return images
    finally:
        doc.close()

def ocr_pdf_attachment(pdf_path, max_pages=4):
    print(f"[RFQ] Running OCR fallback on PDF: {pdf_path}")
    engine = get_ocr_engine()
    chunks = []
    for image in render_pdf_pages_for_ocr(pdf_path, max_pages=max_pages):
        result, _ = engine(image)
        if result:
            chunks.append("\n".join(item[1] for item in result if len(item) > 1))
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    print(f"[RFQ] OCR PDF extraction produced {len(text)} characters.")
    return text

def score_quote_text(text):
    normalized = normalize_text(text)
    if not normalized:
        return 0
    score = 0
    if "quote" in normalized or "quotation" in normalized or "rfq" in normalized:
        score += 3
    score += len(re.findall(r'\$?\s*[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?', text or ""))
    score += len(re.findall(r'[A-Za-z0-9-]{3,}', text or "")) // 40
    return score

def extract_best_quote_text(email_body, attachment_path=None):
    """Chooses the most useful text source for quote parsing: body, PDF text, or OCR."""
    print("[RFQ] Choosing best quote text source from email body and attachments.")
    candidates = [("email_body", email_body or "")]
    lower = (attachment_path or "").lower()

    if attachment_path and os.path.exists(attachment_path):
        if lower.endswith(".pdf"):
            pdf_text = extract_text_from_attachment(attachment_path)
            candidates.append(("pdf_text", pdf_text))
            if score_quote_text(pdf_text) < 6:
                ocr_text = ocr_pdf_attachment(attachment_path)
                candidates.append(("pdf_ocr", ocr_text))
        elif lower.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
            candidates.append(("image_ocr", ocr_image_file(attachment_path)))
        else:
            candidates.append(("attachment_text", extract_text_from_attachment(attachment_path)))

    ranked = sorted(candidates, key=lambda item: score_quote_text(item[1]), reverse=True)
    best_source, best_text = ranked[0]
    score_summary = ", ".join(f"{src}={score_quote_text(txt)}" for src, txt in ranked)
    print(f"[RFQ] Quote text scores: {score_summary}")
    print(f"[RFQ] Selected extraction source: {best_source}")
    return {
        "source": best_source,
        "text": best_text or "",
        "candidates": [{"source": src, "score": score_quote_text(txt)} for src, txt in ranked]
    }

def extract_quote_number(subject, text):
    combined = f"{subject or ''}\n{text or ''}"
    patterns = [
        r'(?:vendor\s+quote|quote|quotation)\s*(?:number|no\.?|#)\s*[:#-]?\s*([A-Za-z0-9-]+)',
        r'\bref(?:erence)?\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Za-z0-9-]+)',
        r'\bproposal\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Za-z0-9-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            candidate = match.group(1)[:80]
            if not re.fullmatch(r'\d{1,6}', candidate):
                return candidate
            if not re.search(r'\brfq\s*#?\s*' + re.escape(candidate) + r'\b', combined, re.IGNORECASE):
                return candidate
    return None

def extract_quote_date(text):
    combined = text or ""
    patterns = [
        r'\b(20\d{2}-\d{2}-\d{2})\b',
        r'\b(\d{4}/\d{2}/\d{2})\b',
        r'\b(\d{2}/\d{2}/\d{4})\b',
        r'\b(\d{2}-\d{2}-\d{4})\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            value = match.group(1)
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y"):
                try:
                    return datetime.datetime.strptime(value, fmt).date().isoformat()
                except ValueError:
                    continue
    return datetime.date.today().isoformat()

def extract_money_candidates(line):
    values = []
    for match in re.findall(r'(?<![#\w])\$?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)', line):
        try:
            values.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return values

def contains_likely_pricing(text):
    sample = text or ""
    if "$" in sample:
        return True
    if len(re.findall(r'\b[0-9]+\.[0-9]{2}\b', sample)) >= 2:
        return True
    return False

def select_unit_price_from_line(line, qty):
    values = [v for v in extract_money_candidates(line) if v > 0]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    if qty and qty > 0:
        for candidate in values:
            for other in values:
                if candidate == other:
                    continue
                if abs((candidate * qty) - other) < 0.05:
                    return candidate
    return min(values)

def normalize_text(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()

def choose_line_for_item(item, lines):
    part_number = normalize_text(item.get("PartNumber"))
    description = normalize_text(item.get("Description"))
    desc_tokens = [tok for tok in description.split() if len(tok) > 2]
    for line in lines:
        normalized_line = normalize_text(line)
        if part_number and part_number in normalized_line:
            return line
    if not desc_tokens:
        return None
    scored = []
    for line in lines:
        normalized_line = normalize_text(line)
        score = sum(1 for tok in desc_tokens if tok in normalized_line)
        if score > 0:
            scored.append((score, line))
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] >= min(2, len(desc_tokens)) else None

def parse_quote_prices_for_rfq(rfq_id: int, quote_text: str):
    print(f"[RFQ] Parsing quote prices for RFQ #{rfq_id}")
    items = get_rfq_items_for_ingest(rfq_id)
    lines = [line.strip() for line in (quote_text or "").splitlines() if line.strip()]
    matches = []
    unmatched = []

    for item in items:
        line = choose_line_for_item(item, lines)
        if not line:
            unmatched.append(item)
            continue
        unit_price = select_unit_price_from_line(line, float(item.get("Quantity") or 0))
        if unit_price is None:
            unmatched.append(item)
            continue
        matches.append({
            "pr_item_id": item["PRItemID"],
            "material_id": item["MaterialID"],
            "material_call_item_id": item.get("MaterialCallItemID"),
            "material_call_id": item.get("MaterialCallID"),
            "item_id": item.get("ItemID"),
            "part_number": item.get("PartNumber"),
            "description": item.get("Description"),
            "unit_price": unit_price,
            "line": line,
        })

    print(f"[RFQ] Parsing complete for RFQ #{rfq_id}: matched={len(matches)} unmatched={len(unmatched)}")
    return matches, unmatched

def save_incoming_quote_file(rfq_context, source_file_path, preferred_name=None):
    target_dir = get_target_folder(
        rfq_context.get("StreetNumber"),
        rfq_context.get("StreetName"),
        rfq_context.get("SiteName"),
        rfq_context["EstimateID"],
        "Quotes"
    )
    base_name = preferred_name or os.path.basename(source_file_path)
    target_path = os.path.join(target_dir, base_name.replace(" ", "_"))
    shutil.copy2(source_file_path, target_path)
    return target_path

def save_email_quote_pdf(rfq_context, sender_email, subject, body, received_at=None):
    """Creates a PDF archive when pricing came in directly by email instead of an attachment."""
    target_dir = get_target_folder(
        rfq_context.get("StreetNumber"),
        rfq_context.get("StreetName"),
        rfq_context.get("SiteName"),
        rfq_context["EstimateID"],
        "Quotes"
    )
    timestamp = (received_at or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    sender_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (sender_email or "vendor")).strip("_") or "vendor"
    pdf_path = os.path.join(target_dir, f"Email_Quote_RFQ_{rfq_context['PriceRequestID']}_{sender_slug}_{timestamp}.pdf")
    lines = [
        f"RFQ #: {rfq_context['PriceRequestID']}",
        f"Estimate #: {rfq_context['EstimateID']}",
        f"Vendor: {rfq_context['VendorName']}",
        f"Received From: {sender_email}",
        f"Received At: {received_at or datetime.datetime.now()}",
        "",
        "Subject:",
        subject or "",
        "",
        "Body:",
        body or "",
    ]
    return _write_simple_pdf(pdf_path, f"Vendor Email Quote - RFQ #{rfq_context['PriceRequestID']}", lines)

def build_email_quote_number(sender_email, received_at=None):
    stamp = (received_at or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    sender_slug = re.sub(r"[^A-Za-z0-9@._-]+", "_", sender_email or "vendor")
    return f"{stamp}_{sender_slug}"[:80]

def update_material_catalog_prices(vendor_name: str, matched_prices: list):
    print(f"[RFQ] Updating material catalog prices for vendor: {vendor_name}")
    from neon_ai.database.materials import ensure_material_schema, record_vendor_quote_price

    ensure_material_schema()

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    updated = 0
    try:
        for item in matched_prices:
            if not item.get("item_id"):
                continue
            wholesaler_name = record_vendor_quote_price(
                cur,
                item_id=item["item_id"],
                unit_price=item["unit_price"],
                vendor_name=vendor_name,
                vendor_id=item.get("vendor_id"),
                rfq_id=item.get("rfq_id"),
                pr_item_id=item.get("pr_item_id"),
                quote_number=item.get("quote_number"),
                quote_date=item.get("quote_date"),
                vendor_part_number=item.get("vendor_part_number"),
                source_file_path=item.get("source_file_path"),
            )
            if wholesaler_name:
                updated += 1
        conn.commit()
        print(f"[RFQ] Material catalog rows updated: {updated}")
        return updated
    finally:
        conn.close()

def ingest_vendor_quote_email(rfq_id: int, sender_email: str, subject: str, body: str, attachment_path: str = None, received_at=None):
    """Reconciles an inbound vendor quote email to an RFQ and updates draft estimate costs."""
    from neon_ai.database.estimates import sync_estimate_pricing_from_sources
    from neon_ai.database.automation import log_estimate_action

    print(f"[RFQ] Starting vendor quote ingest for RFQ #{rfq_id} from {sender_email}")
    rfq_context = get_rfq_context(rfq_id)
    if not rfq_context:
        print(f"[RFQ] RFQ #{rfq_id} could not be loaded.")
        return None
    if str(rfq_context.get("RFQStatus") or "").strip().lower() == "locked":
        print(f"[RFQ] RFQ #{rfq_id} is locked; inbound quote ingest skipped.")
        return {
            "rfq_id": rfq_id,
            "estimate_id": rfq_context.get("EstimateID"),
            "vendor_name": rfq_context.get("VendorName"),
            "site_name": rfq_context.get("SiteName"),
            "matched_count": 0,
            "unmatched_count": 0,
            "material_updates": 0,
            "archived_quote_path": None,
            "quote_number": str(rfq_context.get("VendorQuoteNumber") or "").strip(),
            "estimate_status": str(rfq_context.get("EstimateStatus") or ""),
            "estimate_pricing_updated": False,
            "extraction_source": "locked_skip",
            "extraction_candidates": [],
            "locked_skip": True,
        }

    extraction_result = extract_best_quote_text(body or "", attachment_path=attachment_path)
    archived_quote_path = None
    if attachment_path and os.path.exists(attachment_path):
        print(f"[RFQ] Archiving inbound quote attachment for RFQ #{rfq_id}")
        archived_quote_path = save_incoming_quote_file(
            rfq_context,
            attachment_path,
            preferred_name=os.path.basename(attachment_path)
        )
        print(f"[RFQ] Quote archived to: {archived_quote_path}")
    else:
        print(f"[RFQ] No attachment found; creating PDF archive from vendor email body for RFQ #{rfq_id}")
        archived_quote_path = save_email_quote_pdf(
            rfq_context,
            sender_email,
            subject,
            body,
            received_at=received_at
        )
        print(f"[RFQ] Email quote archived to: {archived_quote_path}")
    combined_text = extraction_result["text"]
    matched_prices, unmatched_items = parse_quote_prices_for_rfq(rfq_id, combined_text)
    quote_number = extract_quote_number(subject, combined_text)
    if not quote_number:
        quote_number = build_email_quote_number(sender_email, received_at=received_at)
    quote_date = extract_quote_date(combined_text)
    print(f"[RFQ] Quote metadata extracted: quote_number={quote_number}, quote_date={quote_date}")

    conn = get_connection()
    cur = conn.cursor()
    try:
        print(f"[RFQ] Writing quote header and line pricing back to RFQ #{rfq_id}")
        cur.execute("""
            UPDATE "PriceRequest"
            SET "VendorQuoteNumber" = %s,
                "VendorQuoteDate" = %s,
                "QuoteFilePath" = %s,
                "Status" = %s
            WHERE "PriceRequestID" = %s
        """, (quote_number, quote_date, archived_quote_path, 'Quote Received', rfq_id))

        material_call_id = rfq_context.get("MaterialCallID")
        for item in matched_prices:
            if material_call_id is not None:
                cur.execute(
                    '''
                    UPDATE "PriceRequestItem"
                    SET "QuotedUnitPrice" = %s
                    WHERE "PRItemID" = %s
                      AND "PriceRequestID" = %s
                    ''',
                    (item["unit_price"], item["pr_item_id"], rfq_id),
                )
            else:
                cur.execute(
                    'UPDATE "PriceRequestItem" SET "QuotedUnitPrice" = %s, "IsCarried" = %s WHERE "PRItemID" = %s',
                    (item["unit_price"], True, item["pr_item_id"])
                )
                cur.execute(
                    'UPDATE "PriceRequestItem" SET "IsCarried" = FALSE WHERE "MaterialID" = %s AND "PriceRequestID" != %s',
                    (item["material_id"], rfq_id)
                )

        conn.commit()
    finally:
        conn.close()

    matched_prices_for_catalog = []
    for item in matched_prices:
        matched_prices_for_catalog.append(
            {
                **item,
                "vendor_id": rfq_context["VendorID"],
                "rfq_id": rfq_id,
                "quote_number": quote_number,
                "quote_date": quote_date,
                "vendor_part_number": item.get("part_number"),
                "source_file_path": archived_quote_path,
            }
        )
    material_updates = update_material_catalog_prices(rfq_context["VendorName"], matched_prices_for_catalog)

    sync_result = sync_estimate_pricing_from_sources(rfq_context["EstimateID"])
    if sync_result["estimate_pricing_updated"]:
        print(f"[RFQ] Estimate #{rfq_context['EstimateID']} is still draft; pricing snapshot updated.")
        estimate_effect = "Draft estimate material lines were refreshed from the returned vendor pricing."
    else:
        print(f"[RFQ] Estimate #{rfq_context['EstimateID']} is locked; estimate snapshot left unchanged.")
        estimate_effect = f"Estimate status is {sync_result['estimate_status']}, so estimate material lines remained unchanged."

    log_estimate_action(
        rfq_context["EstimateID"],
        f"Vendor quote ingested from {sender_email} for RFQ #{rfq_id} using {extraction_result['source']}. Matched {len(matched_prices)} item(s); unmatched {len(unmatched_items)}. Quote file: {archived_quote_path or 'Email body only'}.",
        "RFQ"
    )
    log_estimate_action(
        rfq_context["EstimateID"],
        (
            f"Received wholesaler estimate from {rfq_context['VendorName']} for RFQ #{rfq_id}. "
            f"Source email: {sender_email}. Extraction used: {extraction_result['source']}. "
            f"Matched prices: {len(matched_prices)}. Unmatched items: {len(unmatched_items)}. "
            f"Material catalog updates: {material_updates}. "
            f"Archived quote: {archived_quote_path or 'Email body only'}. "
            f"{estimate_effect}"
        ),
        "Vendor Quote"
    )
    print(f"[RFQ] Vendor quote ingest complete for RFQ #{rfq_id}")

    return {
        "rfq_id": rfq_id,
        "estimate_id": rfq_context["EstimateID"],
        "vendor_name": rfq_context["VendorName"],
        "site_name": rfq_context["SiteName"],
        "matched_count": len(matched_prices),
        "unmatched_count": len(unmatched_items),
        "material_updates": material_updates,
        "archived_quote_path": archived_quote_path,
        "quote_number": quote_number,
        "estimate_status": sync_result["estimate_status"],
        "estimate_pricing_updated": sync_result["estimate_pricing_updated"],
        "extraction_source": extraction_result["source"],
        "extraction_candidates": extraction_result["candidates"],
    }

def process_inbound_vendor_rfq_email(rfq_id: int, sender_email: str, subject: str, body: str, attachment_path: str = None, received_at=None):
    """Routes a matched RFQ email either into quote ingestion or vendor-response tracking."""
    extraction_result = extract_best_quote_text(body or "", attachment_path=attachment_path)
    if attachment_path or contains_likely_pricing(extraction_result["text"]):
        return ingest_vendor_quote_email(
            rfq_id,
            sender_email,
            subject,
            body,
            attachment_path=attachment_path,
            received_at=received_at
        )
    return process_vendor_rfq_response_without_quote(
        rfq_id,
        sender_email,
        subject,
        body,
        received_at=received_at
    )

def get_all_vendors():
    """Pulls a list of every vendor in the database."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT "VendorName" FROM "Vendor" ORDER BY "VendorName"')
        return [row['VendorName'] for row in cur.fetchall()]
    finally:
        conn.close()

def get_quoted_vendors_for_estimate(estimate_id: int):
    """Checks which vendors have already been sent an RFQ for a specific estimate."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            SELECT v."VendorName" 
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."EstimateID" = %s
        ''', (estimate_id,))
        return [row['VendorName'] for row in cur.fetchall()]
    finally:
        conn.close()

def get_active_rfqs():
    # CLEANUP_CANDIDATE_SHADOWED:
    # This earlier definition is shadowed by a later get_active_rfqs() definition in this
    # module. Keep it only until hidden/legacy RFQ surfaces are fully audited and covered
    # by smoke tests.
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                pr."PriceRequestID", 
                pr."EstimateID", 
                pr."VendorID",
                s."SiteName",        -- The missing piece!
                v."VendorName", 
                pr."DueDate", 
                pr."Status"
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            -- WALKING THE CHAIN:
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            ORDER BY pr."DueDate" ASC
        """)
        return cur.fetchall()
    finally:
        conn.close()

def get_rfq_items_for_matrix(price_request_id: int):
    """Pulls the specific materials, quoted prices, and checkbox status for Tab 2."""
    from neon_ai.database.connection import get_connection
    from psycopg2.extras import RealDictCursor
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(f'''
            SELECT
                pri."PRItemID",
                pri."MaterialID",
                pri."MaterialCallItemID",
                pr."MaterialCallID",
                mc."MaterialCallNumber",
                COALESCE(pri."IsCarried", FALSE) AS "IsCarried",
                COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') AS "Description",
                COALESCE(pri."QuantityOverride", mci."Quantity", em."Quantity", 0) AS "Quantity",
                COALESCE(mci."MaterialID", em."ItemID") AS "CatalogItemID",
                m."InternalPrice" AS "CatalogInternalPrice",
                em."UnitCost" AS "EstimateUnitCost",
                CASE
                    WHEN COALESCE(mci."MaterialID", em."ItemID") IS NOT NULL
                         AND NULLIF(COALESCE(m."InternalPrice", 0), 0) IS NOT NULL
                        THEN m."InternalPrice"
                    WHEN NULLIF(COALESCE(em."UnitCost", 0), 0) IS NOT NULL
                        THEN em."UnitCost"
                    ELSE NULL
                END AS "DatabaseUnitPrice",
                CASE
                    WHEN COALESCE(mci."MaterialID", em."ItemID") IS NOT NULL
                         AND NULLIF(COALESCE(m."InternalPrice", 0), 0) IS NOT NULL
                        THEN m."InternalPrice"
                    WHEN NULLIF(COALESCE(em."UnitCost", 0), 0) IS NOT NULL
                        THEN em."UnitCost"
                    ELSE NULL
                END AS "UnitCost",
                CASE
                    WHEN COALESCE(mci."MaterialID", em."ItemID") IS NOT NULL
                         AND NULLIF(COALESCE(m."InternalPrice", 0), 0) IS NOT NULL
                        THEN 'Materials Catalogue InternalPrice'
                    WHEN NULLIF(COALESCE(em."UnitCost", 0), 0) IS NOT NULL
                        THEN 'EstimateMaterial.UnitCost fallback'
                    WHEN COALESCE(mci."MaterialID", em."ItemID") IS NOT NULL
                        THEN 'Catalogue link present but no InternalPrice'
                    ELSE 'No catalogue link'
                END AS "DatabasePriceSource",
                pri."QuotedUnitPrice",
                pr."EstimateID",
                pr."Status" AS "RFQStatus",
                em."PriceSource" AS "EstimatePriceSource",
                COALESCE(NULLIF(TRIM(mci."Unit"), ''), NULLIF(TRIM(m."Unit"), ''), '') AS "Unit",
                COALESCE(NULLIF(TRIM(mci."PartNumber"), ''), NULLIF(TRIM(em."PartNumber"), ''), NULLIF(TRIM(m."PartNumber"), ''), '') AS "PartNumber"
            FROM "PriceRequestItem" pri
            JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
            LEFT JOIN "MaterialCall" mc ON pr."MaterialCallID" = mc."MaterialCallID"
            LEFT JOIN "MaterialCallItem" mci ON pri."MaterialCallItemID" = mci."MaterialCallItemID"
            LEFT JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            LEFT JOIN "Material" m ON COALESCE(mci."MaterialID", em."ItemID") = m."ItemID"
            WHERE pri."PriceRequestID" = %s
            ORDER BY
                COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') ASC,
                pri."PRItemID" ASC
        ''', (price_request_id,))
        return cur.fetchall()
    finally:
        conn.close()


def _update_estimate_total_from_lines(cur, estimate_id: int) -> None:
    cur.execute(
        '''
        UPDATE "Estimate"
        SET "TotalAmount" = (
            COALESCE((SELECT SUM("LineTotal") FROM "EstimateLabor" WHERE "EstimateID" = %s), 0) * (1 + (COALESCE("LaborMarkUp", 0)/100.0)) +
            COALESCE((SELECT SUM("LineTotal") FROM "EstimateMaterial" WHERE "EstimateID" = %s), 0) * (1 + (COALESCE("MaterialMarkUp", 0)/100.0))
        )
        WHERE "EstimateID" = %s
        ''',
        (estimate_id, estimate_id, estimate_id),
    )


def save_quote_response(
    rfq_id: int,
    quote_no: str,
    quote_date: str,
    all_items_data: list,
    source_file_path: str = None,
):
    from neon_ai.database.automation import get_project_file_paths
    from neon_ai.database.estimates import _get_estimate_status

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                pr."EstimateID",
                pr."MaterialCallID",
                pr."Status",
                c."CustomerName",
                s."SiteName"
            FROM "PriceRequest" pr
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE pr."PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        rfq_scope = cur.fetchone()
        if not rfq_scope or rfq_scope.get("EstimateID") is None:
            raise ValueError(f"RFQ #{rfq_id} is missing estimate context.")

        current_status = str(rfq_scope.get("Status") or "").strip().lower()
        if current_status in {"locked", "quote locked"}:
            raise ValueError("This quote is locked and can no longer be edited.")

        scope_estimate_id = int(rfq_scope["EstimateID"])
        material_call_id = int(rfq_scope["MaterialCallID"]) if rfq_scope.get("MaterialCallID") is not None else None

        target_file_path = None
        if source_file_path and os.path.exists(source_file_path):
            quote_folder, _ = get_project_file_paths(
                rfq_scope['CustomerName'],
                rfq_scope['SiteName'],
                scope_estimate_id,
                category="Quotes"
            )
            ext = os.path.splitext(source_file_path)[1]
            quote_label = str(quote_no or f"RFQ_{rfq_id}_quote").strip().replace(" ", "_")
            filename = f"Quote_{quote_label}_RFQ_{rfq_id}{ext}"
            target_file_path = os.path.join(quote_folder, filename)
            shutil.copy2(source_file_path, target_file_path)

        cur.execute(
            '''
            UPDATE "PriceRequest"
            SET "VendorQuoteNumber" = %s,
                "VendorQuoteDate" = %s,
                "QuoteFilePath" = %s,
                "Status" = 'Quote Received'
            WHERE "PriceRequestID" = %s
            ''',
            (str(quote_no or "").strip(), quote_date or None, target_file_path, rfq_id),
        )

        for pr_item_id, _material_id, quote_unit in all_items_data:
            cur.execute(
                '''
                UPDATE "PriceRequestItem"
                SET "QuotedUnitPrice" = %s
                WHERE "PRItemID" = %s
                  AND "PriceRequestID" = %s
                ''',
                (quote_unit, pr_item_id, rfq_id),
            )

        estimate_status = _get_estimate_status(cur, scope_estimate_id)

        metadata = {}
        cur.execute(
            '''
            SELECT
                pri."PRItemID",
                pri."MaterialID",
                pri."MaterialCallItemID",
                COALESCE(mci."MaterialID", em."ItemID") AS "ItemID",
                COALESCE(NULLIF(TRIM(mci."PartNumber"), ''), NULLIF(TRIM(em."PartNumber"), ''), '') AS "PartNumber",
                COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), '') AS "Description",
                pr."VendorID",
                v."VendorName"
            FROM "PriceRequestItem" pri
            LEFT JOIN "MaterialCallItem" mci ON pri."MaterialCallItemID" = mci."MaterialCallItemID"
            LEFT JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pri."PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        for row in cur.fetchall():
            metadata[int(row["PRItemID"])] = row

        conn.commit()

        matched_prices = []
        for pr_item_id, _material_id, unit_price in all_items_data:
            item_meta = metadata.get(int(pr_item_id))
            if not item_meta:
                continue
            matched_prices.append(
                {
                    "pr_item_id": int(pr_item_id),
                    "item_id": item_meta.get("ItemID"),
                    "unit_price": unit_price,
                    "part_number": item_meta.get("PartNumber"),
                    "vendor_part_number": item_meta.get("PartNumber"),
                    "vendor_id": item_meta.get("VendorID"),
                    "rfq_id": rfq_id,
                    "quote_number": quote_no,
                    "quote_date": quote_date or None,
                    "source_file_path": target_file_path,
                }
            )
        vendor_name = next((row.get("VendorName") for row in metadata.values()), None)
        material_updates = update_material_catalog_prices(vendor_name or "", matched_prices) if matched_prices else 0

        return {
            "saved": True,
            "material_call_backed": material_call_id is not None,
            "carry_mutation_performed": False,
            "estimate_status": estimate_status,
            "estimate_pricing_updated": False,
            "estimate_update_blocked_by_lock": False,
            "estimate_updated_row_count": 0,
            "estimate_update_deferred_to_bid_compare": True,
            "material_updates": material_updates,
            "quote_file_path": target_file_path,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def lock_quote_response(rfq_id: int) -> dict[str, object]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                "PriceRequestID",
                "MaterialCallID",
                "Status"
            FROM "PriceRequest"
            WHERE "PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"RFQ #{rfq_id} was not found.")

        status = str(row.get("Status") or "").strip()
        normalized = status.lower()
        if normalized == "sent":
            raise ValueError("Sent RFQs cannot be locked from Receive Quotes.")
        if normalized == "locked":
            return {
                "rfq_id": int(row["PriceRequestID"]),
                "material_call_id": row.get("MaterialCallID"),
                "status": "Locked",
                "already_locked": True,
            }
        if normalized not in {"quote received"}:
            raise ValueError("Save Quote Response first, then Lock Quote.")

        cur.execute(
            '''
            UPDATE "PriceRequest"
            SET "Status" = 'Locked'
            WHERE "PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        conn.commit()
        return {
            "rfq_id": int(row["PriceRequestID"]),
            "material_call_id": row.get("MaterialCallID"),
            "status": "Locked",
            "already_locked": False,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_rfq_item_price(pr_item_id: int, unit_price: float):
    """Saves the quoted unit price from the matrix back to the database."""
    sync_quote_to_estimate(pr_item_id, unit_price)

def sync_quote_to_estimate(pr_item_id: int, quoted_price: float):
    """
    The Gatekeeper: Pushes the quoted price back to the master estimate,
    BUT ONLY if the estimate is still a 'Draft' (Not sent to customer yet).
    """
    from neon_ai.database.estimates import sync_estimate_pricing_from_sources
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('''
            UPDATE "PriceRequestItem"
            SET "QuotedUnitPrice" = %s
            WHERE "PRItemID" = %s
            RETURNING "PriceRequestID", "IsCarried", "MaterialCallItemID"
        ''', (quoted_price, pr_item_id))
        updated = cur.fetchone()
        if not updated:
            return False

        cur.execute('''
            SELECT pr."EstimateID", pr."MaterialCallID"
            FROM "PriceRequest" pr
            WHERE pr."PriceRequestID" = %s
        ''', (updated["PriceRequestID"],))
        estimate_row = cur.fetchone()
        if not estimate_row:
            conn.commit()
            return False

        sync_result = {"estimate_pricing_updated": False, "estimate_status": "Unknown"}
        if updated.get("IsCarried") and estimate_row.get("EstimateID") is not None:
            sync_result = sync_estimate_pricing_from_sources(estimate_row["EstimateID"], cur=cur)

        conn.commit()
        return bool(sync_result.get("estimate_pricing_updated") or True)
    except Exception as e:
        conn.rollback()
        print(f"Sync error: {e}")
        return False
    finally:
        conn.close()


def save_quote_and_carry_items(estimate_id: int, rfq_id: int, quote_no: str, quote_date: str,
                               all_items_data: list, carried_mat_ids: list, source_file_path: str = None):
    from neon_ai.database.estimates import sync_estimate_pricing_from_sources
    from neon_ai.database.automation import get_project_file_paths

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        cur.execute(
            '''
            SELECT "EstimateID", "MaterialCallID"
            FROM "PriceRequest"
            WHERE "PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        rfq_scope = cur.fetchone()
        if not rfq_scope or rfq_scope.get("EstimateID") is None:
            raise ValueError(f"RFQ #{rfq_id} is missing estimate context.")

        scope_estimate_id = int(rfq_scope["EstimateID"])
        material_call_id = int(rfq_scope["MaterialCallID"]) if rfq_scope.get("MaterialCallID") is not None else None

        cur.execute('''
            SELECT c."CustomerName", s."SiteName"
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = %s
        ''', (scope_estimate_id,))
        project = cur.fetchone()

        target_file_path = None
        if source_file_path and os.path.exists(source_file_path) and project:
            quote_folder, _ = get_project_file_paths(
                project['CustomerName'],
                project['SiteName'],
                scope_estimate_id,
                category="Quotes"
            )
            ext = os.path.splitext(source_file_path)[1]
            filename = f"Quote_{quote_no}_RFQ_{rfq_id}{ext}".replace(" ", "_")
            target_file_path = os.path.join(quote_folder, filename)
            shutil.copy2(source_file_path, target_file_path)

        cur.execute('''
            UPDATE "PriceRequest"
            SET "VendorQuoteNumber" = %s,
                "VendorQuoteDate" = %s,
                "QuoteFilePath" = %s,
                "Status" = 'Quote Received'
            WHERE "PriceRequestID" = %s
        ''', (quote_no, quote_date or None, target_file_path, rfq_id))

        carry_mutation_performed = material_call_id is None
        if material_call_id is not None:
            for item in all_items_data:
                cur.execute(
                    '''
                    UPDATE "PriceRequestItem"
                    SET "QuotedUnitPrice" = %s
                    WHERE "PRItemID" = %s
                      AND "PriceRequestID" = %s
                    ''',
                    (item[2], item[0], rfq_id),
                )
        else:
            for item in all_items_data:
                cur.execute(
                    'UPDATE "PriceRequestItem" SET "QuotedUnitPrice" = %s, "IsCarried" = FALSE WHERE "PRItemID" = %s',
                    (item[2], item[0])
                )
                if item[1] in carried_mat_ids:
                    cur.execute(
                        '''
                        UPDATE "PriceRequestItem"
                        SET "IsCarried" = FALSE
                        WHERE "MaterialID" = %s
                          AND "PriceRequestID" IN (
                              SELECT "PriceRequestID" FROM "PriceRequest" WHERE "EstimateID" = %s
                          )
                        ''',
                        (item[1], scope_estimate_id),
                    )
                    cur.execute('UPDATE "PriceRequestItem" SET "IsCarried" = TRUE WHERE "PRItemID" = %s', (item[0],))

        metadata = {}
        cur.execute(
            '''
            SELECT
                pri."PRItemID",
                pri."MaterialID",
                pri."MaterialCallItemID",
                COALESCE(mci."MaterialID", em."ItemID") AS "ItemID",
                COALESCE(NULLIF(TRIM(mci."PartNumber"), ''), NULLIF(TRIM(em."PartNumber"), ''), '') AS "PartNumber",
                COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), '') AS "Description",
                pr."VendorID",
                v."VendorName"
            FROM "PriceRequestItem" pri
            LEFT JOIN "MaterialCallItem" mci ON pri."MaterialCallItemID" = mci."MaterialCallItemID"
            LEFT JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pri."PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        for row in cur.fetchall():
            metadata[int(row["PRItemID"])] = row

        sync_result = sync_estimate_pricing_from_sources(scope_estimate_id, cur=cur)
        conn.commit()

        matched_prices = []
        for pr_item_id, _material_id, unit_price in all_items_data:
            item_meta = metadata.get(int(pr_item_id))
            if not item_meta:
                continue
            matched_prices.append(
                {
                    "pr_item_id": int(pr_item_id),
                    "item_id": item_meta.get("ItemID"),
                    "unit_price": unit_price,
                    "part_number": item_meta.get("PartNumber"),
                    "vendor_part_number": item_meta.get("PartNumber"),
                    "vendor_id": item_meta.get("VendorID"),
                    "rfq_id": rfq_id,
                    "quote_number": quote_no,
                    "quote_date": quote_date or None,
                    "source_file_path": target_file_path,
                }
            )
        vendor_name = next((row.get("VendorName") for row in metadata.values()), None)
        material_updates = update_material_catalog_prices(vendor_name or "", matched_prices) if matched_prices else 0

        return {
            "saved": True,
            "material_call_backed": material_call_id is not None,
            "carry_mutation_performed": carry_mutation_performed,
            "estimate_status": sync_result["estimate_status"],
            "estimate_pricing_updated": sync_result["estimate_pricing_updated"],
            "material_updates": material_updates,
            "quote_file_path": target_file_path,
        }
    except Exception as e:
        conn.rollback()
        print(f"Librarian/RFQ Save Error: {e}")
        return {
            "saved": False,
            "material_call_backed": False,
            "carry_mutation_performed": False,
            "estimate_status": "Unknown",
            "estimate_pricing_updated": False,
            "material_updates": 0,
            "quote_file_path": None,
        }
    finally:
        conn.close()

def get_rfq_header_data(rfq_id: int):
    """Pulls the quote and logistics metadata for a specific RFQ."""
    from neon_ai.database.connection import get_connection
    from psycopg2.extras import RealDictCursor
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur, verify_only=True)
        cur.execute('''
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."VendorQuoteNumber",
                pr."VendorQuoteDate",
                pr."QuoteFilePath",
                pr."DateSent",
                pr."DueDate",
                pr."Status",
                pr."Notes",
                pr."MaterialRequestSourceType",
                pr."MaterialRequestSourceID",
                pr."MaterialCallID",
                mc."MaterialCallNumber",
                v."VendorName",
                pr."VendorID",
                c."CustomerName",
                s."SiteName"
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            LEFT JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            LEFT JOIN "MaterialCall" mc ON pr."MaterialCallID" = mc."MaterialCallID"
            WHERE pr."PriceRequestID" = %s
        ''', (rfq_id,))
        row = cur.fetchone()
        if not row:
            return None
        return _apply_material_request_source_context(row, cur=cur)
    finally:
        conn.close()


def _verify_rfq_bid_compare_schema(cur) -> None:
    global _RFQ_BID_COMPARE_SCHEMA_READY
    if _RFQ_BID_COMPARE_SCHEMA_READY:
        return
    cur.execute('SELECT to_regclass(%s) AS "table_name"', ('public."RFQCarriedSelection"',))
    row = cur.fetchone()
    table_name = row.get("table_name") if isinstance(row, dict) else row[0]
    if not table_name:
        raise RuntimeError(
            'RFQCarriedSelection schema is missing. Run the RFQ bid-compare migration before using Bid Compare.'
        )
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'RFQCarriedSelection'
          AND column_name IN (
              'EstimateID',
              'MaterialID',
              'ContextPriceRequestID',
              'SourcePriceRequestID',
              'SourcePRItemID',
              'VendorID',
              'MaterialCallID',
              'MaterialCallItemID'
          )
        """
    )
    found = {
        str(row.get("column_name") if isinstance(row, dict) else row[0])
        for row in cur.fetchall()
    }
    required = {
        'EstimateID',
        'MaterialID',
        'ContextPriceRequestID',
        'SourcePriceRequestID',
        'SourcePRItemID',
        'VendorID',
        'MaterialCallID',
        'MaterialCallItemID',
    }
    missing = sorted(required - found)
    if missing:
        raise RuntimeError(
            "RFQCarriedSelection schema is missing required columns: "
            + ", ".join(missing)
            + ". Run the RFQ bid-compare migration before using this workflow."
        )
    _RFQ_BID_COMPARE_SCHEMA_READY = True


def ensure_rfq_bid_compare_schema(cur=None, *, force: bool = False, verify_only: bool = False):
    global _RFQ_BID_COMPARE_SCHEMA_READY
    if _RFQ_BID_COMPARE_SCHEMA_READY and not force:
        return
    owns_connection = cur is None
    conn = None
    if owns_connection:
        conn = get_connection()
        cur = conn.cursor()
    try:
        if verify_only:
            _verify_rfq_bid_compare_schema(cur)
        else:
            cur.execute('CREATE SEQUENCE IF NOT EXISTS "RFQCarriedSelection_RFQCarriedSelectionID_seq"')
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS public."RFQCarriedSelection" (
                    "RFQCarriedSelectionID" integer NOT NULL DEFAULT nextval('"RFQCarriedSelection_RFQCarriedSelectionID_seq"'::regclass),
                    "ContextPriceRequestID" integer NOT NULL,
                    "EstimateID" integer,
                    "MaterialID" integer,
                    "SourcePriceRequestID" integer NOT NULL,
                    "SourcePRItemID" integer NOT NULL,
                    "VendorID" integer NOT NULL,
                    "CarriedUnitPrice" numeric,
                    "CarriedExtendedPrice" numeric,
                    "CarriedQuantity" numeric,
                    "SelectionType" text,
                    "SelectionNotes" text,
                    "CreatedAt" timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "UpdatedAt" timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "RFQCarriedSelection_pkey" PRIMARY KEY ("RFQCarriedSelectionID"),
                    CONSTRAINT "RFQCarriedSelection_context_rfq_fkey"
                        FOREIGN KEY ("ContextPriceRequestID") REFERENCES public."PriceRequest"("PriceRequestID"),
                    CONSTRAINT "RFQCarriedSelection_estimate_fkey"
                        FOREIGN KEY ("EstimateID") REFERENCES public."Estimate"("EstimateID"),
                    CONSTRAINT "RFQCarriedSelection_material_fkey"
                        FOREIGN KEY ("MaterialID") REFERENCES public."EstimateMaterial"("EstimateMaterialID"),
                    CONSTRAINT "RFQCarriedSelection_source_rfq_fkey"
                        FOREIGN KEY ("SourcePriceRequestID") REFERENCES public."PriceRequest"("PriceRequestID"),
                    CONSTRAINT "RFQCarriedSelection_source_item_fkey"
                        FOREIGN KEY ("SourcePRItemID") REFERENCES public."PriceRequestItem"("PRItemID"),
                    CONSTRAINT "RFQCarriedSelection_vendor_fkey"
                        FOREIGN KEY ("VendorID") REFERENCES public."Vendor"("VendorID")
                )
                '''
            )
            cur.execute(
                '''
                ALTER TABLE public."RFQCarriedSelection"
                ADD COLUMN IF NOT EXISTS "MaterialCallID" integer
                '''
            )
            cur.execute(
                '''
                ALTER TABLE public."RFQCarriedSelection"
                ADD COLUMN IF NOT EXISTS "MaterialCallItemID" integer
                '''
            )
            cur.execute(
                '''
                ALTER TABLE public."RFQCarriedSelection"
                ALTER COLUMN "EstimateID" DROP NOT NULL
                '''
            )
            cur.execute(
                '''
                ALTER TABLE public."RFQCarriedSelection"
                ALTER COLUMN "MaterialID" DROP NOT NULL
                '''
            )
            cur.execute('DROP INDEX IF EXISTS public."idx_rfqcarriedselection_estimate_material"')
            cur.execute(
                '''
                CREATE UNIQUE INDEX IF NOT EXISTS "idx_rfqcarriedselection_estimate_material"
                ON public."RFQCarriedSelection" ("EstimateID", "MaterialID")
                WHERE "MaterialCallID" IS NULL AND "MaterialID" IS NOT NULL
                '''
            )
            cur.execute(
                '''
                CREATE UNIQUE INDEX IF NOT EXISTS "idx_rfqcarriedselection_materialcall_item"
                ON public."RFQCarriedSelection" ("MaterialCallID", "MaterialCallItemID")
                WHERE "MaterialCallID" IS NOT NULL AND "MaterialCallItemID" IS NOT NULL
                '''
            )
            cur.execute(
                '''
                CREATE INDEX IF NOT EXISTS "idx_rfqcarriedselection_materialcallid"
                ON public."RFQCarriedSelection" ("MaterialCallID")
                '''
            )
            _RFQ_BID_COMPARE_SCHEMA_READY = True
        if owns_connection and conn is not None:
            conn.commit()
    except Exception:
        if owns_connection and conn is not None:
            conn.rollback()
        raise
    finally:
        if owns_connection and conn is not None:
            conn.close()


def get_bid_compare_data(rfq_id: int):
    started_at = time.perf_counter()
    connection_started_at = time.perf_counter()
    conn = get_connection()
    _perf_log("db", "bid_compare.connection", connection_started_at, rfq_id=rfq_id)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        schema_started_at = time.perf_counter()
        ensure_rfq_bid_compare_schema(cur=cur, verify_only=True)
        _perf_log("db", "bid_compare.schema_ensure", schema_started_at, rfq_id=rfq_id)
        header_started_at = time.perf_counter()
        cur.execute(
            '''
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."MaterialCallID",
                pr."VendorID",
                pr."Status",
                pr."DueDate",
                pr."DateSent",
                pr."VendorQuoteNumber",
                pr."VendorQuoteDate",
                mc."MaterialCallNumber",
                v."VendorName",
                s."SiteName"
            FROM "PriceRequest" pr
            LEFT JOIN "MaterialCall" mc ON pr."MaterialCallID" = mc."MaterialCallID"
            LEFT JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            LEFT JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            WHERE pr."PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        header = cur.fetchone()
        _perf_log("db", "bid_compare.header_query", header_started_at, rfq_id=rfq_id)
        if not header:
            raise ValueError(f"RFQ #{rfq_id} was not found.")

        estimate_id = header.get("EstimateID")
        material_call_id = int(header["MaterialCallID"]) if header.get("MaterialCallID") is not None else None
        material_call_number = str(header.get("MaterialCallNumber") or "").strip()
        wo_resolution_started_at = time.perf_counter()
        wo_resolution = _get_wo_resolution_for_estimate(cur, estimate_id)
        _perf_log("db", "bid_compare.work_order_resolution", wo_resolution_started_at, rfq_id=rfq_id)
        work_order_id = (
            wo_resolution.get("open_work_order_id")
            or wo_resolution.get("latest_work_order_id")
        )
        compare_scope = "MaterialCall" if material_call_id is not None else "Estimate"
        carried_save_supported = True
        carried_save_message = ""

        if material_call_id is not None:
            vendor_rows_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    pr."PriceRequestID",
                    pr."VendorID",
                    pr."Status",
                    pr."DateSent",
                    pr."VendorQuoteNumber",
                    pr."VendorQuoteDate",
                    v."VendorName"
                FROM "PriceRequest" pr
                LEFT JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
                WHERE pr."MaterialCallID" = %s
                ORDER BY
                    CASE WHEN pr."PriceRequestID" = %s THEN 0 ELSE 1 END,
                    v."VendorName" ASC,
                    pr."PriceRequestID" ASC
                ''',
                (material_call_id, rfq_id),
            )
            vendor_rows = [dict(row) for row in cur.fetchall()]
            _perf_log("db", "bid_compare.vendor_rows_query", vendor_rows_started_at, rfq_id=rfq_id, row_count=len(vendor_rows), compare_scope=compare_scope)

            base_lines_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    pri."PRItemID",
                    pri."MaterialID",
                    pri."MaterialCallItemID",
                    COALESCE(NULLIF(TRIM(mci."PartNumber"), ''), NULLIF(TRIM(em."PartNumber"), ''), NULLIF(TRIM(m."PartNumber"), ''), '') AS "PartNumber",
                    COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') AS "Description",
                    COALESCE(pri."QuantityOverride", mci."Quantity", em."Quantity", 0) AS "RequestedQuantity",
                    COALESCE(NULLIF(TRIM(mci."Unit"), ''), NULLIF(TRIM(m."Unit"), ''), '') AS "Unit",
                    COALESCE(pri."QuotedUnitPrice", 0) AS "ContextQuotedUnitPrice",
                    COALESCE(pri."IsCarried", FALSE) AS "ContextIsCarried"
                FROM "PriceRequestItem" pri
                LEFT JOIN "MaterialCallItem" mci ON pri."MaterialCallItemID" = mci."MaterialCallItemID"
                LEFT JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
                LEFT JOIN "Material" m ON COALESCE(mci."MaterialID", em."ItemID") = m."ItemID"
                WHERE pri."PriceRequestID" = %s
                ORDER BY
                    COALESCE(NULLIF(TRIM(mci."Description"), ''), NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') ASC,
                    pri."PRItemID" ASC
                ''',
                (rfq_id,),
            )
            base_lines = [dict(row) for row in cur.fetchall()]
            _perf_log("db", "bid_compare.base_lines_query", base_lines_started_at, rfq_id=rfq_id, row_count=len(base_lines), compare_scope=compare_scope)

            quote_rows_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    pri."PRItemID",
                    pri."PriceRequestID",
                    pr."VendorID",
                    pri."MaterialID",
                    pri."MaterialCallItemID",
                    COALESCE(pri."QuantityOverride", mci."Quantity", em."Quantity", 0) AS "RequestedQuantity",
                    pri."QuotedUnitPrice",
                    COALESCE(pri."IsSubstitute", FALSE) AS "IsSubstitute",
                    COALESCE(pri."SubstituteNotes", '') AS "SubstituteNotes",
                    COALESCE(pri."IsCarried", FALSE) AS "IsCarried",
                    pr."VendorQuoteNumber",
                    pr."VendorQuoteDate"
                FROM "PriceRequestItem" pri
                JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
                LEFT JOIN "MaterialCallItem" mci ON pri."MaterialCallItemID" = mci."MaterialCallItemID"
                LEFT JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
                WHERE pr."MaterialCallID" = %s
                ORDER BY pri."PriceRequestID" ASC, pri."PRItemID" ASC
                ''',
                (material_call_id,),
            )
            quote_rows = [dict(row) for row in cur.fetchall()]
            _perf_log("db", "bid_compare.quote_rows_query", quote_rows_started_at, rfq_id=rfq_id, row_count=len(quote_rows), compare_scope=compare_scope)

            selection_rows_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    "MaterialCallItemID",
                    "SourcePriceRequestID",
                    "SourcePRItemID",
                    "VendorID",
                    "CarriedUnitPrice",
                    "CarriedExtendedPrice",
                    "CarriedQuantity",
                    COALESCE("SelectionType", '') AS "SelectionType",
                    COALESCE("SelectionNotes", '') AS "SelectionNotes"
                FROM "RFQCarriedSelection"
                WHERE "MaterialCallID" = %s
                  AND "MaterialCallItemID" IS NOT NULL
                ''',
                (material_call_id,),
            )
            selection_rows = {
                int(row["MaterialCallItemID"]): dict(row)
                for row in cur.fetchall()
                if row.get("MaterialCallItemID") is not None
            }
            _perf_log("db", "bid_compare.selection_rows_query", selection_rows_started_at, rfq_id=rfq_id, row_count=len(selection_rows), compare_scope=compare_scope)
        else:
            vendor_rows_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    pr."PriceRequestID",
                    pr."VendorID",
                    pr."Status",
                    pr."DateSent",
                    pr."VendorQuoteNumber",
                    pr."VendorQuoteDate",
                    v."VendorName"
                FROM "PriceRequest" pr
                LEFT JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
                WHERE pr."EstimateID" = %s
                ORDER BY
                    CASE WHEN pr."PriceRequestID" = %s THEN 0 ELSE 1 END,
                    v."VendorName" ASC,
                    pr."PriceRequestID" ASC
                ''',
                (estimate_id, rfq_id),
            )
            vendor_rows = [dict(row) for row in cur.fetchall()]
            _perf_log("db", "bid_compare.vendor_rows_query", vendor_rows_started_at, rfq_id=rfq_id, row_count=len(vendor_rows), compare_scope=compare_scope)

            base_lines_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    pri."PRItemID",
                    pri."MaterialID",
                    COALESCE(NULLIF(TRIM(em."PartNumber"), ''), NULLIF(TRIM(m."PartNumber"), ''), '') AS "PartNumber",
                    COALESCE(NULLIF(TRIM(em."Description"), ''), NULLIF(TRIM(m."Description"), ''), '') AS "Description",
                    COALESCE(pri."QuantityOverride", em."Quantity", 0) AS "RequestedQuantity",
                    COALESCE(NULLIF(TRIM(m."Unit"), ''), '') AS "Unit",
                    COALESCE(pri."QuotedUnitPrice", 0) AS "ContextQuotedUnitPrice",
                    COALESCE(pri."IsCarried", FALSE) AS "ContextIsCarried"
                FROM "PriceRequestItem" pri
                JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
                LEFT JOIN "Material" m ON em."ItemID" = m."ItemID"
                WHERE pri."PriceRequestID" = %s
                ORDER BY
                    em."Description" ASC, pri."PRItemID" ASC
                ''',
                (rfq_id,),
            )
            base_lines = [dict(row) for row in cur.fetchall()]
            _perf_log("db", "bid_compare.base_lines_query", base_lines_started_at, rfq_id=rfq_id, row_count=len(base_lines), compare_scope=compare_scope)

            quote_rows_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    pri."PRItemID",
                    pri."PriceRequestID",
                    pr."VendorID",
                    pri."MaterialID",
                    COALESCE(pri."QuantityOverride", em."Quantity", 0) AS "RequestedQuantity",
                    pri."QuotedUnitPrice",
                    COALESCE(pri."IsSubstitute", FALSE) AS "IsSubstitute",
                    COALESCE(pri."SubstituteNotes", '') AS "SubstituteNotes",
                    COALESCE(pri."IsCarried", FALSE) AS "IsCarried",
                    pr."VendorQuoteNumber",
                    pr."VendorQuoteDate"
                FROM "PriceRequestItem" pri
                JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
                JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
                WHERE pr."EstimateID" = %s
                ORDER BY pri."PriceRequestID" ASC, pri."PRItemID" ASC
                ''',
                (estimate_id,),
            )
            quote_rows = [dict(row) for row in cur.fetchall()]
            _perf_log("db", "bid_compare.quote_rows_query", quote_rows_started_at, rfq_id=rfq_id, row_count=len(quote_rows), compare_scope=compare_scope)

            selection_rows_started_at = time.perf_counter()
            cur.execute(
                '''
                SELECT
                    "MaterialID",
                    "SourcePriceRequestID",
                    "SourcePRItemID",
                    "VendorID",
                    "CarriedUnitPrice",
                    "CarriedExtendedPrice",
                    "CarriedQuantity",
                    COALESCE("SelectionType", '') AS "SelectionType",
                    COALESCE("SelectionNotes", '') AS "SelectionNotes"
                FROM "RFQCarriedSelection"
                WHERE "EstimateID" = %s
                  AND "MaterialCallID" IS NULL
                ''',
                (estimate_id,),
            )
            selection_rows = {
                int(row["MaterialID"]): dict(row)
                for row in cur.fetchall()
            }
            _perf_log("db", "bid_compare.selection_rows_query", selection_rows_started_at, rfq_id=rfq_id, row_count=len(selection_rows), compare_scope=compare_scope)

        if material_call_id is not None:
            carried_save_supported = all(base.get("MaterialCallItemID") is not None for base in base_lines)
            carried_save_message = (
                ""
                if carried_save_supported
                else "One or more RFQ lines are missing Material Call item linkage, so carried selections cannot be saved safely for this RFQ."
            )

        map_build_started_at = time.perf_counter()
        vendor_quote_map = {}
        for row in quote_rows:
            material_key = (
                int(row["MaterialCallItemID"])
                if material_call_id is not None and row.get("MaterialCallItemID") is not None
                else int(row["MaterialID"])
            )
            pr_id = int(row["PriceRequestID"])
            quoted_unit = row.get("QuotedUnitPrice")
            qty = float(row.get("RequestedQuantity") or 0)
            vendor_quote_map.setdefault(material_key, {})[pr_id] = {
                "source_pr_item_id": int(row["PRItemID"]),
                "source_price_request_id": pr_id,
                "vendor_id": int(row["VendorID"]) if row.get("VendorID") is not None else None,
                "quoted_unit_price": float(quoted_unit) if quoted_unit is not None else None,
                "quoted_extended_price": float(quoted_unit) * qty if quoted_unit is not None else None,
                "requested_quantity": qty,
                "is_substitute": bool(row.get("IsSubstitute")),
                "substitute_notes": str(row.get("SubstituteNotes") or "").strip(),
                "is_carried": bool(row.get("IsCarried")),
                "vendor_quote_number": str(row.get("VendorQuoteNumber") or "").strip(),
                "vendor_quote_date": row.get("VendorQuoteDate"),
            }
        _perf_log("db", "bid_compare.quote_map_build", map_build_started_at, rfq_id=rfq_id, row_count=len(quote_rows), compare_scope=compare_scope)

        lines_build_started_at = time.perf_counter()
        lines = []
        for base in base_lines:
            material_key = (
                int(base["MaterialCallItemID"])
                if material_call_id is not None and base.get("MaterialCallItemID") is not None
                else int(base["MaterialID"])
            )
            vendor_quotes = vendor_quote_map.get(material_key, {})
            saved_selection = selection_rows.get(material_key)
            if not saved_selection:
                saved_selection = next(
                    (
                        {
                            "SourcePriceRequestID": quote["source_price_request_id"],
                            "SourcePRItemID": quote["source_pr_item_id"],
                            "VendorID": quote.get("vendor_id"),
                            "CarriedUnitPrice": quote.get("quoted_unit_price"),
                            "CarriedExtendedPrice": quote.get("quoted_extended_price"),
                            "CarriedQuantity": quote.get("requested_quantity"),
                            "SelectionType": "LegacyCarry",
                            "SelectionNotes": "",
                        }
                        for quote in vendor_quotes.values()
                        if quote.get("is_carried")
                    ),
                    None,
                )

            notes_summary_parts = []
            for vendor_row in vendor_rows:
                quote = vendor_quotes.get(int(vendor_row["PriceRequestID"]))
                if not quote:
                    continue
                vendor_label = str(vendor_row.get("VendorName") or f"RFQ #{vendor_row['PriceRequestID']}")
                note_bits = []
                if quote.get("is_substitute"):
                    note_bits.append("Substitution")
                if quote.get("substitute_notes"):
                    note_bits.append(quote["substitute_notes"])
                if note_bits:
                    notes_summary_parts.append(f"{vendor_label}: {' | '.join(note_bits)}")

            lines.append(
                {
                    "material_id": material_key,
                    "estimate_material_id": int(base["MaterialID"]) if base.get("MaterialID") is not None else None,
                    "material_call_item_id": int(base["MaterialCallItemID"]) if base.get("MaterialCallItemID") is not None else None,
                    "base_pr_item_id": int(base["PRItemID"]),
                    "part_number": str(base.get("PartNumber") or "").strip(),
                    "description": str(base.get("Description") or "").strip(),
                    "requested_quantity": float(base.get("RequestedQuantity") or 0),
                    "unit": str(base.get("Unit") or "").strip(),
                    "vendor_quotes": vendor_quotes,
                    "saved_selection": saved_selection,
                    "notes_summary": "\n".join(notes_summary_parts),
                }
            )
        _perf_log("db", "bid_compare.lines_build", lines_build_started_at, rfq_id=rfq_id, row_count=len(lines), vendor_count=len(vendor_rows), compare_scope=compare_scope)

        return {
            "header": {
                "rfq_id": int(header["PriceRequestID"]),
                "estimate_id": int(header["EstimateID"]) if header.get("EstimateID") is not None else None,
                "material_call_id": material_call_id,
                "material_call_number": material_call_number or (f"MC#{material_call_id}" if material_call_id is not None else ""),
                "compare_scope": compare_scope,
                "work_order_id": work_order_id,
                "status": str(header.get("Status") or ""),
                "vendor_name": str(header.get("VendorName") or ""),
                "vendor_id": int(header["VendorID"]) if header.get("VendorID") is not None else None,
                "site_name": str(header.get("SiteName") or ""),
                "quote_number": str(header.get("VendorQuoteNumber") or "").strip(),
                "quote_date": header.get("VendorQuoteDate"),
                "carried_save_supported": carried_save_supported,
                "carried_save_message": carried_save_message,
            },
            "vendors": vendor_rows,
            "lines": lines,
        }
    finally:
        _perf_log("db", "bid_compare.data_load", started_at, rfq_id=rfq_id)
        conn.close()


def save_bid_compare_carried_selections(context_rfq_id: int, selections: list[dict]):
    from neon_ai.database.estimates import sync_estimate_pricing_from_sources

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_rfq_bid_compare_schema(cur=cur)
        cur.execute(
            '''
            SELECT "EstimateID", "MaterialCallID"
            FROM "PriceRequest"
            WHERE "PriceRequestID" = %s
            ''',
            (context_rfq_id,),
        )
        rfq_row = cur.fetchone()
        if not rfq_row or rfq_row.get("EstimateID") is None:
            raise ValueError(f"RFQ #{context_rfq_id} is missing estimate context.")

        estimate_id = int(rfq_row["EstimateID"])
        material_call_id = int(rfq_row["MaterialCallID"]) if rfq_row.get("MaterialCallID") is not None else None
        if material_call_id is not None:
            cur.execute(
                '''
                SELECT
                    pri."MaterialCallItemID",
                    pri."MaterialID"
                FROM "PriceRequestItem" pri
                WHERE pri."PriceRequestID" = %s
                ''',
                (context_rfq_id,),
            )
            scoped_rows = [dict(row) for row in cur.fetchall()]
            scoped_material_call_items = [
                {
                    "material_call_item_id": int(row["MaterialCallItemID"]),
                    "material_id": int(row["MaterialID"]) if row.get("MaterialID") is not None else None,
                }
                for row in scoped_rows
                if row.get("MaterialCallItemID") is not None
            ]
            if not scoped_material_call_items:
                raise ValueError(
                    f"RFQ #{context_rfq_id} is missing Material Call item linkage and cannot save carried selections safely."
                )

            cur.execute(
                '''
                UPDATE "PriceRequestItem"
                SET "IsCarried" = FALSE
                WHERE "MaterialCallItemID" = ANY(%s)
                  AND "PriceRequestID" IN (
                      SELECT "PriceRequestID" FROM "PriceRequest" WHERE "MaterialCallID" = %s
                  )
                ''',
                ([row["material_call_item_id"] for row in scoped_material_call_items], material_call_id),
            )

            for row in scoped_material_call_items:
                cur.execute(
                    '''
                    DELETE FROM "RFQCarriedSelection"
                    WHERE "MaterialCallID" = %s
                      AND "MaterialCallItemID" = %s
                    ''',
                    (material_call_id, row["material_call_item_id"]),
                )

            normalized = []
            for selection in selections or []:
                if not selection:
                    continue
                material_call_item_id = selection.get("material_call_item_id")
                if material_call_item_id is None:
                    raise ValueError("A carried selection is missing Material Call item context.")
                source_pr_item_id = int(selection["source_pr_item_id"])
                source_price_request_id = int(selection["source_price_request_id"])
                vendor_id = int(selection["vendor_id"])
                quantity = float(selection.get("carried_quantity") or 0)
                unit_price = float(selection.get("carried_unit_price") or 0)
                extended_price = float(selection.get("carried_extended_price") or (quantity * unit_price))
                normalized.append(
                    {
                        "material_call_item_id": int(material_call_item_id),
                        "material_id": int(selection["estimate_material_id"]) if selection.get("estimate_material_id") is not None else None,
                        "source_pr_item_id": source_pr_item_id,
                        "source_price_request_id": source_price_request_id,
                        "vendor_id": vendor_id,
                        "carried_quantity": quantity,
                        "carried_unit_price": unit_price,
                        "carried_extended_price": extended_price,
                    }
                )

            unique_vendor_ids = {row["vendor_id"] for row in normalized}
            selection_type = "FullPackage" if len(unique_vendor_ids) == 1 and normalized else "LineItem"

            for row in normalized:
                cur.execute(
                    '''
                    INSERT INTO "RFQCarriedSelection" (
                        "ContextPriceRequestID",
                        "EstimateID",
                        "MaterialID",
                        "MaterialCallID",
                        "MaterialCallItemID",
                        "SourcePriceRequestID",
                        "SourcePRItemID",
                        "VendorID",
                        "CarriedUnitPrice",
                        "CarriedExtendedPrice",
                        "CarriedQuantity",
                        "SelectionType",
                        "SelectionNotes",
                        "UpdatedAt"
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ''',
                    (
                        context_rfq_id,
                        estimate_id,
                        row["material_id"],
                        material_call_id,
                        row["material_call_item_id"],
                        row["source_price_request_id"],
                        row["source_pr_item_id"],
                        row["vendor_id"],
                        row["carried_unit_price"],
                        row["carried_extended_price"],
                        row["carried_quantity"],
                        selection_type,
                        "",
                    ),
                )
                cur.execute(
                    '''
                    UPDATE "PriceRequestItem"
                    SET "IsCarried" = TRUE
                    WHERE "PRItemID" = %s
                    ''',
                    (row["source_pr_item_id"],),
                )

            sync_result = sync_estimate_pricing_from_sources(estimate_id, cur=cur)
            conn.commit()
            return {
                "saved": True,
                "selection_count": len(normalized),
                "selection_type": selection_type,
                "estimate_pricing_updated": bool(sync_result.get("estimate_pricing_updated")),
                "estimate_status": str(sync_result.get("estimate_status") or "Unknown"),
            }

        cur.execute(
            '''
            SELECT "MaterialID"
            FROM "PriceRequestItem"
            WHERE "PriceRequestID" = %s
            ''',
            (context_rfq_id,),
        )
        scoped_material_ids = [int(row["MaterialID"]) for row in cur.fetchall()]
        if not scoped_material_ids:
            return {"saved": True, "selection_count": 0, "estimate_pricing_updated": False, "estimate_status": "Unknown"}

        for material_id in scoped_material_ids:
            cur.execute(
                '''
                UPDATE "PriceRequestItem"
                SET "IsCarried" = FALSE
                WHERE "MaterialID" = %s
                  AND "PriceRequestID" IN (
                      SELECT "PriceRequestID" FROM "PriceRequest" WHERE "EstimateID" = %s
                  )
                ''',
                (material_id, estimate_id),
            )
            cur.execute(
                '''
                DELETE FROM "RFQCarriedSelection"
                WHERE "EstimateID" = %s AND "MaterialID" = %s
                ''',
                (estimate_id, material_id),
            )

        normalized = []
        for selection in selections or []:
            if not selection:
                continue
            material_id = int(selection["material_id"])
            source_pr_item_id = int(selection["source_pr_item_id"])
            source_price_request_id = int(selection["source_price_request_id"])
            vendor_id = int(selection["vendor_id"])
            quantity = float(selection.get("carried_quantity") or 0)
            unit_price = float(selection.get("carried_unit_price") or 0)
            extended_price = float(selection.get("carried_extended_price") or (quantity * unit_price))
            normalized.append(
                {
                    "material_id": material_id,
                    "source_pr_item_id": source_pr_item_id,
                    "source_price_request_id": source_price_request_id,
                    "vendor_id": vendor_id,
                    "carried_quantity": quantity,
                    "carried_unit_price": unit_price,
                    "carried_extended_price": extended_price,
                }
            )

        unique_vendor_ids = {row["vendor_id"] for row in normalized}
        selection_type = "FullPackage" if len(unique_vendor_ids) == 1 and normalized else "LineItem"

        for row in normalized:
            cur.execute(
                '''
                INSERT INTO "RFQCarriedSelection" (
                    "ContextPriceRequestID",
                    "EstimateID",
                    "MaterialID",
                    "SourcePriceRequestID",
                    "SourcePRItemID",
                    "VendorID",
                    "CarriedUnitPrice",
                    "CarriedExtendedPrice",
                    "CarriedQuantity",
                    "SelectionType",
                    "SelectionNotes",
                    "UpdatedAt"
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT ("EstimateID", "MaterialID")
                DO UPDATE SET
                    "ContextPriceRequestID" = EXCLUDED."ContextPriceRequestID",
                    "SourcePriceRequestID" = EXCLUDED."SourcePriceRequestID",
                    "SourcePRItemID" = EXCLUDED."SourcePRItemID",
                    "VendorID" = EXCLUDED."VendorID",
                    "CarriedUnitPrice" = EXCLUDED."CarriedUnitPrice",
                    "CarriedExtendedPrice" = EXCLUDED."CarriedExtendedPrice",
                    "CarriedQuantity" = EXCLUDED."CarriedQuantity",
                    "SelectionType" = EXCLUDED."SelectionType",
                    "SelectionNotes" = EXCLUDED."SelectionNotes",
                    "UpdatedAt" = CURRENT_TIMESTAMP
                ''',
                (
                    context_rfq_id,
                    estimate_id,
                    row["material_id"],
                    row["source_price_request_id"],
                    row["source_pr_item_id"],
                    row["vendor_id"],
                    row["carried_unit_price"],
                    row["carried_extended_price"],
                    row["carried_quantity"],
                    selection_type,
                    "",
                ),
            )
            cur.execute(
                '''
                UPDATE "PriceRequestItem"
                SET "IsCarried" = TRUE
                WHERE "PRItemID" = %s
                ''',
                (row["source_pr_item_id"],),
            )

        sync_result = sync_estimate_pricing_from_sources(estimate_id, cur=cur)
        conn.commit()
        return {
            "saved": True,
            "selection_count": len(normalized),
            "selection_type": selection_type,
            "estimate_pricing_updated": bool(sync_result.get("estimate_pricing_updated")),
            "estimate_status": str(sync_result.get("estimate_status") or "Unknown"),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_wo_for_rfq(rfq_id: int):
    """
    Finds the active Work Order number for a given RFQ.
    Walks the chain: PriceRequest -> Estimate -> Site -> WorkOrder
    """
    resolution = get_wo_resolution_for_rfq(rfq_id)
    return resolution.get("open_work_order_id")


def _resolve_wo_resolution_rows(rows):
    rows = [row for row in rows if row.get("WorkOrderID")]
    open_row = next(
        (
            row for row in rows
            if str(row.get("JobStatus") or "").strip().upper() == "OPEN" and not bool(row.get("IsClosed"))
        ),
        None,
    )
    latest_row = rows[0] if rows else None

    if open_row:
        return {
            "open_work_order_id": int(open_row["WorkOrderID"]),
            "latest_work_order_id": int(open_row["WorkOrderID"]),
            "latest_status": str(open_row.get("JobStatus") or "Open"),
            "can_create_po": True,
            "message": f'Work Order #{open_row["WorkOrderID"]} is open and ready for material ordering.',
        }

    if latest_row:
        latest_status = str(latest_row.get("JobStatus") or "Unknown")
        latest_id = int(latest_row["WorkOrderID"])
        return {
            "open_work_order_id": None,
            "latest_work_order_id": latest_id,
            "latest_status": latest_status,
            "can_create_po": False,
            "message": (
                f"Problem: Work Order #{latest_id} is not open (current status: {latest_status}). "
                "Solution: reopen the work order or create a new open work order before ordering materials."
            ),
        }

    return {
        "open_work_order_id": None,
        "latest_work_order_id": None,
        "latest_status": None,
        "can_create_po": False,
        "message": (
            "Problem: there is no work order for this RFQ yet. "
            "Solution: approve the estimate and open the work order before ordering materials."
        ),
    }


def _get_wo_resolution_for_estimate(cur, estimate_id: int | None):
    if not estimate_id:
        return _resolve_wo_resolution_rows([])
    cur.execute(
        """
        SELECT
            w."WorkOrderID",
            COALESCE(NULLIF(TRIM(w."JobStatus"), ''), CASE WHEN COALESCE(w."IsClosed", FALSE) THEN 'Closed' ELSE 'Unknown' END) AS "JobStatus",
            COALESCE(w."IsClosed", FALSE) AS "IsClosed"
        FROM "Estimate" e
        LEFT JOIN "WorkOrder" w ON e."SiteID" = w."SiteID"
        WHERE e."EstimateID" = %s
        ORDER BY w."WorkOrderID" DESC NULLS LAST
        """,
        (estimate_id,),
    )
    return _resolve_wo_resolution_rows(cur.fetchall())


def get_wo_resolution_for_rfq(rfq_id: int):
    from neon_ai.database.connection import get_connection

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT pr."EstimateID"
            FROM "PriceRequest" pr
            WHERE pr."PriceRequestID" = %s
            """,
            (rfq_id,),
        )
        row = cur.fetchone() or {}
        estimate_id = int(row.get("EstimateID") or 0) or None
        return _get_wo_resolution_for_estimate(cur, estimate_id)
    finally:
        conn.close()


def get_awarded_quotes():
    from neon_ai.database.connection import get_connection
    from psycopg2.extras import RealDictCursor
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor) 
    try:
        cur.execute("""
            SELECT DISTINCT
                pr."PriceRequestID", 
                pr."EstimateID", 
                s."SiteName",
                v."VendorName",
                pr."VendorID" -- ADD THIS LINE: We need this for the PO!
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "PriceRequestItem" pri ON pr."PriceRequestID" = pri."PriceRequestID"
            WHERE pri."IsCarried" = TRUE
            ORDER BY pr."PriceRequestID" DESC
        """)
        return cur.fetchall()
    finally:
        conn.close()

def get_active_rfqs():
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                pr."PriceRequestID", 
                pr."EstimateID", 
                s."SiteName",        
                v."VendorName", 
                pr."DateSent",
                pr."DueDate", 
                pr."Status",
                pr."VendorID" -- ADDED THIS FOR THE PO TAB!
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            ORDER BY pr."DueDate" ASC
        """)
        return cur.fetchall()
    finally:
        conn.close()

