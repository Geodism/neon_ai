import psycopg2
import datetime
import os
import re
import textwrap
import json
import shutil
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
    """Creates the RFQ Header and links all the selected items."""
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
    """Creates the RFQ header and items for a specific vendor id."""
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
        estimate_row = cur.fetchone()
        if not estimate_row:
            return None, []

        cur.execute(
            f"""
            SELECT "EstimateMaterialID", "Quantity", "Description"
            FROM "EstimateMaterial" em
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
    """Creates RFQs for selected vendors, builds PDFs, emails them, and returns the results."""
    estimate_row, material_rows = _load_estimate_rfq_material_rows(estimate_id)
    if not estimate_row:
        raise ValueError(f"Estimate #{estimate_id} could not be loaded.")
    if not material_rows:
        raise ValueError("This estimate has no material lines to package into an RFQ.")

    results = []
    material_ids = [row["EstimateMaterialID"] for row in material_rows]

    for request in vendor_requests:
        vendor_id = request["vendor_id"]
        vendor_name = request["vendor_name"]
        recipient_email = (request.get("recipient_email") or "").strip()
        if not recipient_email:
            raise ValueError(f"Recipient email is required for {vendor_name}.")

        rfq_id = save_rfq_package_for_vendor_id(estimate_id, vendor_id, due_date, material_ids)
        send_result = send_rfq_by_id(rfq_id, recipient_email=recipient_email)
        results.append({
            "rfq_id": rfq_id,
            "vendor_name": vendor_name,
            "recipient_email": send_result["recipient_email"],
            "pdf_path": send_result["pdf_path"],
            "sent": True,
        })

    return results


def create_rfq_batch_without_sending(estimate_id: int, vendor_requests: list, due_date: str = None):
    """Creates RFQ draft records for selected vendors without sending email."""
    estimate_row, material_rows = _load_estimate_rfq_material_rows(estimate_id)
    if not estimate_row:
        raise ValueError(f"Estimate #{estimate_id} could not be loaded.")
    if not material_rows:
        raise ValueError("This estimate has no material lines to package into an RFQ.")

    material_ids = [row["EstimateMaterialID"] for row in material_rows]
    results = []
    seen_vendor_ids = set()

    for request in vendor_requests:
        vendor_id = int(request["vendor_id"])
        vendor_name = str(request.get("vendor_name") or "").strip() or f"Vendor #{vendor_id}"
        recipient_email = (request.get("recipient_email") or "").strip()
        if not recipient_email:
            raise ValueError(f"Recipient email is required for {vendor_name}.")
        if vendor_id in seen_vendor_ids:
            raise ValueError(f"{vendor_name} was selected more than once in this RFQ batch.")

        seen_vendor_ids.add(vendor_id)
        rfq_id = save_rfq_package_for_vendor_id(estimate_id, vendor_id, due_date, material_ids)
        results.append(
            {
                "rfq_id": int(rfq_id),
                "estimate_id": int(estimate_id),
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "recipient_email": recipient_email,
                "due_date": due_date,
                "created_without_send": True,
            }
        )

    return results


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
                e."Status" AS "EstimateStatus",
                c."CustomerName",
                s."SiteName",
                s."StreetNumber",
                s."StreetName",
                v."VendorName"
            FROM "PriceRequest" pr
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
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
                pri."QuotedUnitPrice",
                em."EstimateID",
                em."Description",
                em."Quantity",
                em."PartNumber",
                em."ItemID"
            FROM "PriceRequestItem" pri
            JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            WHERE pri."PriceRequestID" = %s
            ORDER BY pri."PRItemID"
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

        for item in matched_prices:
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
        cur.execute('''
            SELECT pri."PRItemID", pri."MaterialID", pri."IsCarried", 
                   em."Description", em."Quantity", em."UnitCost", pri."QuotedUnitPrice"
            FROM "PriceRequestItem" pri
            JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            WHERE pri."PriceRequestID" = %s
            ORDER BY em."Description"
        ''', (price_request_id,))
        return cur.fetchall()
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
            RETURNING "PriceRequestID", "IsCarried"
        ''', (quoted_price, pr_item_id))
        updated = cur.fetchone()
        if not updated:
            return False

        cur.execute('''
            SELECT pr."EstimateID"
            FROM "PriceRequest" pr
            WHERE pr."PriceRequestID" = %s
        ''', (updated["PriceRequestID"],))
        estimate_row = cur.fetchone()
        if not estimate_row:
            conn.commit()
            return False

        sync_result = {"estimate_pricing_updated": False, "estimate_status": "Unknown"}
        if updated.get("IsCarried"):
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
        cur.execute('''
            SELECT c."CustomerName", s."SiteName"
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = %s
        ''', (estimate_id,))
        project = cur.fetchone()

        target_file_path = None
        if source_file_path and os.path.exists(source_file_path) and project:
            quote_folder, _ = get_project_file_paths(
                project['CustomerName'],
                project['SiteName'],
                estimate_id,
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
                    (item[1], estimate_id),
                )
                cur.execute('UPDATE "PriceRequestItem" SET "IsCarried" = TRUE WHERE "PRItemID" = %s', (item[0],))

        metadata = {}
        cur.execute(
            '''
            SELECT
                pri."PRItemID",
                pri."MaterialID",
                em."ItemID",
                em."PartNumber",
                em."Description",
                pr."VendorID",
                v."VendorName"
            FROM "PriceRequestItem" pri
            JOIN "EstimateMaterial" em ON pri."MaterialID" = em."EstimateMaterialID"
            JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pri."PriceRequestID" = %s
            ''',
            (rfq_id,),
        )
        for row in cur.fetchall():
            metadata[int(row["PRItemID"])] = row

        sync_result = sync_estimate_pricing_from_sources(estimate_id, cur=cur)
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
        cur.execute('''
            SELECT
                pr."VendorQuoteNumber",
                pr."VendorQuoteDate",
                pr."QuoteFilePath",
                pr."DateSent",
                pr."DueDate",
                pr."Status",
                v."VendorName",
                pr."VendorID"
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."PriceRequestID" = %s
        ''', (rfq_id,))
        return cur.fetchone()
    finally:
        conn.close()

def get_wo_for_rfq(rfq_id: int):
    """
    Finds the active Work Order number for a given RFQ.
    Walks the chain: PriceRequest -> Estimate -> Site -> WorkOrder
    """
    resolution = get_wo_resolution_for_rfq(rfq_id)
    return resolution.get("open_work_order_id")


def get_wo_resolution_for_rfq(rfq_id: int):
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT
                w."WorkOrderID",
                COALESCE(NULLIF(TRIM(w."JobStatus"), ''), CASE WHEN COALESCE(w."IsClosed", FALSE) THEN 'Closed' ELSE 'Unknown' END) AS "JobStatus",
                COALESCE(w."IsClosed", FALSE) AS "IsClosed"
            FROM "PriceRequest" pr
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            LEFT JOIN "WorkOrder" w ON e."SiteID" = w."SiteID"
            WHERE pr."PriceRequestID" = %s 
            ORDER BY w."WorkOrderID" DESC NULLS LAST
        """, (rfq_id,))

        rows = [row for row in cur.fetchall() if row.get("WorkOrderID")]
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
                "message": f"Work Order #{open_row['WorkOrderID']} is open and ready for material ordering.",
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

