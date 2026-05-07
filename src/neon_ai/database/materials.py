import datetime
import time

from neon_ai.database.connection import get_connection
from psycopg2.extras import RealDictCursor


def _perf_log(area: str, name: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}")


_MATERIAL_SCHEMA_READY = False


WHOLESALER_COLUMN_MAP = {
    "Internal": {
        "price": '"InternalPrice"',
        "date": None,
        "rfq": None,
        "part": None,
    },
    "Nedco": {
        "price": '"NedcoPrice"',
        "date": '"NedcoLastPriceDate"',
        "rfq": '"NedcoLastRFQ"',
        "part": '"NedcoPartNumber"',
    },
    "Gescan": {
        "price": '"GescanPrice"',
        "date": '"GescanLastPriceDate"',
        "rfq": '"GescanLastRFQ"',
        "part": '"GescanPartNumber"',
    },
    "Eecol": {
        "price": '"EecolPrice"',
        "date": '"EecolLastPriceDate"',
        "rfq": '"EecolLastRFQ"',
        "part": '"EecolPartNumber"',
    },
    "Guillevin": {
        "price": '"GuillevinPrice"',
        "date": '"GuillevinLastPriceDate"',
        "rfq": '"GuillevinLastRFQ"',
        "part": '"GuillevinPartNumber"',
    },
}

WHOLESALER_NAME_MATCHERS = {
    "nedco": "Nedco",
    "gescan": "Gescan",
    "eecol": "Eecol",
    "guillevin": "Guillevin",
}


def ensure_material_schema():
    global _MATERIAL_SCHEMA_READY
    if _MATERIAL_SCHEMA_READY:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('ALTER TABLE public."Material" ADD COLUMN IF NOT EXISTS "CarryPriceSource" text')
        cur.execute('ALTER TABLE public."Material" ADD COLUMN IF NOT EXISTS "IsActive" boolean DEFAULT TRUE')
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."MaterialVendorPriceHistory" (
                "MaterialVendorPriceHistoryID" SERIAL PRIMARY KEY,
                "ItemID" integer NOT NULL REFERENCES public."Material"("ItemID") ON DELETE CASCADE,
                "VendorID" integer REFERENCES public."Vendor"("VendorID") ON DELETE SET NULL,
                "VendorName" text,
                "WholesalerName" text,
                "PriceRequestID" integer REFERENCES public."PriceRequest"("PriceRequestID") ON DELETE SET NULL,
                "PRItemID" integer REFERENCES public."PriceRequestItem"("PRItemID") ON DELETE SET NULL,
                "VendorPartNumber" text,
                "VendorQuoteNumber" text,
                "QuoteDate" date,
                "UnitPrice" numeric NOT NULL,
                "SourceType" text,
                "SourceFilePath" text,
                "CapturedAt" timestamptz NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.commit()
        _MATERIAL_SCHEMA_READY = True
    finally:
        conn.close()


def normalize_carry_price_source(source):
    text = str(source or "").strip()
    if text in WHOLESALER_COLUMN_MAP:
        return text
    lowered = text.lower()
    if lowered in {"", "catalog"}:
        return "Internal"
    for key, canonical in WHOLESALER_NAME_MATCHERS.items():
        if key in lowered:
            return canonical
    return "Internal"


def infer_wholesaler_key(vendor_name):
    lowered = str(vendor_name or "").strip().lower()
    for token, canonical in WHOLESALER_NAME_MATCHERS.items():
        if token in lowered:
            return canonical
    return None


def get_material_price_for_vendor(material_row, vendor_name=None):
    """Returns the most relevant catalog price for the selected vendor."""
    wholesaler = infer_wholesaler_key(vendor_name)
    if wholesaler == "Nedco":
        return float(material_row.get("NedcoPrice") or get_carried_price(material_row) or 0.0)
    if wholesaler == "Gescan":
        return float(material_row.get("GescanPrice") or get_carried_price(material_row) or 0.0)
    if wholesaler == "Eecol":
        return float(material_row.get("EecolPrice") or get_carried_price(material_row) or 0.0)
    if wholesaler == "Guillevin":
        return float(material_row.get("GuillevinPrice") or get_carried_price(material_row) or 0.0)
    return float(get_carried_price(material_row) or 0.0)


def get_carry_source_choices():
    return list(WHOLESALER_COLUMN_MAP.keys())


def _coerce_optional_decimal(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return float(text.replace(",", ""))


def _coerce_optional_text(value):
    text = str(value or "").strip()
    return text or None


def _coerce_part_number(value):
    text = str(value or "").strip()
    return text


def _coerce_optional_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _prices_differ(left_value, right_value):
    if left_value is None and right_value is None:
        return False
    if left_value is None or right_value is None:
        return True
    return round(float(left_value), 4) != round(float(right_value), 4)


def _decorate_material_rows(rows):
    for row in rows:
        row["CarryPriceSource"] = normalize_carry_price_source(row.get("CarryPriceSource"))
        row["CurrentPrice"] = get_carried_price(row)
        row["CurrentPriceDate"] = get_carried_price_date(row)
    return rows


def search_materials(search_text, include_inactive=False):
    """Searches the material catalog by Part Number or Description."""
    ensure_material_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        filters = ['("PartNumber" ILIKE %s OR "Description" ILIKE %s)']
        params = [f"%{search_text}%", f"%{search_text}%"]
        if not include_inactive:
            filters.append('COALESCE("IsActive", TRUE) = TRUE')
        cur.execute(
            f'''
            SELECT
                "ItemID",
                "PartNumber",
                "Description",
                "Unit",
                "CarryPriceSource",
                "IsActive",
                "InternalPrice",
                "NedcoPrice",
                "NedcoLastPriceDate",
                "NedcoPartNumber",
                "GescanPrice",
                "GescanLastPriceDate",
                "GescanPartNumber",
                "EecolPrice",
                "EecolLastPriceDate",
                "EecolPartNumber",
                "GuillevinPrice",
                "GuillevinLastPriceDate",
                "GuillevinPartNumber"
            FROM "Material"
            WHERE {' AND '.join(filters)}
            ORDER BY "Description" ASC
            LIMIT 30
            ''',
            params,
        )
        return _decorate_material_rows(cur.fetchall())
    except Exception as e:
        print(f"Material Search Error: {e}")
        return []
    finally:
        conn.close()


def search_materials_by_part_number(search_text, include_inactive=False, limit=30):
    """Searches the material catalog primarily by part number for UI suggestion lists."""
    ensure_material_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        normalized = str(search_text or "").strip()
        if not normalized:
            return []

        filters = ['COALESCE("PartNumber", \'\') <> \'\'', '"PartNumber" ILIKE %s']
        params = [f"%{normalized}%"]
        if not include_inactive:
            filters.append('COALESCE("IsActive", TRUE) = TRUE')
        cur.execute(
            f'''
            SELECT
                "ItemID",
                "PartNumber",
                "Description",
                "Unit",
                "CarryPriceSource",
                "IsActive",
                "InternalPrice",
                "NedcoPrice",
                "NedcoLastPriceDate",
                "NedcoPartNumber",
                "GescanPrice",
                "GescanLastPriceDate",
                "GescanPartNumber",
                "EecolPrice",
                "EecolLastPriceDate",
                "EecolPartNumber",
                "GuillevinPrice",
                "GuillevinLastPriceDate",
                "GuillevinPartNumber"
            FROM "Material"
            WHERE {' AND '.join(filters)}
            ORDER BY
                CASE
                    WHEN LOWER("PartNumber") = LOWER(%s) THEN 0
                    WHEN LOWER("PartNumber") LIKE LOWER(%s) THEN 1
                    ELSE 2
                END,
                COALESCE("PartNumber", '') ASC,
                "ItemID" ASC
            LIMIT %s
            ''',
            params + [normalized, f"{normalized}%", int(limit)],
        )
        return _decorate_material_rows(cur.fetchall())
    except Exception as e:
        print(f"Material Part Search Error: {e}")
        return []
    finally:
        conn.close()


def get_material_pipeline(search_text="", include_inactive=True):
    started_at = time.perf_counter()
    try:
        ensure_material_schema()
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            filters = []
            params = []
            if search_text.strip():
                filters.append('("PartNumber" ILIKE %s OR "Description" ILIKE %s)')
                params.extend([f"%{search_text.strip()}%", f"%{search_text.strip()}%"])
            if not include_inactive:
                filters.append('COALESCE("IsActive", TRUE) = TRUE')

            sql = """
                SELECT
                    "ItemID",
                    "PartNumber",
                    "Description",
                    "Unit",
                    "CarryPriceSource",
                    "IsActive",
                    "InternalPrice",
                    "NedcoPrice",
                    "NedcoLastPriceDate",
                    "NedcoLastRFQ",
                    "NedcoPartNumber",
                    "GescanPrice",
                    "GescanLastPriceDate",
                    "GescanLastRFQ",
                    "GescanPartNumber",
                    "EecolPrice",
                    "EecolLastPriceDate",
                    "EecolLastRFQ",
                    "EecolPartNumber",
                    "GuillevinPrice",
                    "GuillevinLastPriceDate",
                    "GuillevinLastRFQ",
                    "GuillevinPartNumber"
                FROM "Material"
            """
            if filters:
                sql += " WHERE " + " AND ".join(filters)
            sql += ' ORDER BY COALESCE("Description", \'\') ASC, "ItemID" ASC'

            cur.execute(sql, params)
            return _decorate_material_rows(cur.fetchall())
        finally:
            conn.close()
    finally:
        _perf_log("db", "materials.get_material_pipeline", started_at)


def get_material_by_id(item_id):
    ensure_material_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('SELECT * FROM "Material" WHERE "ItemID" = %s', (item_id,))
        row = cur.fetchone()
        if not row:
            return None
        row["CarryPriceSource"] = normalize_carry_price_source(row.get("CarryPriceSource"))
        row["CurrentPrice"] = get_carried_price(row)
        row["CurrentPriceDate"] = get_carried_price_date(row)
        return row
    finally:
        conn.close()


def get_or_create_material_from_estimate(description, part_number=None, unit="EA"):
    """Ensures ad-hoc estimate materials become reusable catalog materials."""
    ensure_material_schema()
    clean_description = _coerce_optional_text(description)
    clean_part_number = _coerce_part_number(part_number)
    clean_unit = _coerce_optional_text(unit) or "EA"
    if not clean_description:
        raise ValueError("Material description is required.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if clean_part_number:
            cur.execute(
                '''
                SELECT "ItemID", "PartNumber", "Description", "Unit"
                FROM "Material"
                WHERE LOWER(TRIM(COALESCE("PartNumber", ''))) = LOWER(TRIM(%s))
                ORDER BY "ItemID" ASC
                LIMIT 1
                ''',
                (clean_part_number,),
            )
            match = cur.fetchone()
            if match:
                return dict(match)

        cur.execute(
            '''
            SELECT "ItemID", "PartNumber", "Description", "Unit"
            FROM "Material"
            WHERE LOWER(TRIM(COALESCE("Description", ''))) = LOWER(TRIM(%s))
            ORDER BY
                CASE
                    WHEN LOWER(TRIM(COALESCE("PartNumber", ''))) = LOWER(TRIM(%s)) THEN 0
                    ELSE 1
                END,
                "ItemID" ASC
            LIMIT 1
            ''',
            (clean_description, clean_part_number),
        )
        match = cur.fetchone()
        if match:
            if clean_part_number and not match.get("PartNumber"):
                cur.execute(
                    'UPDATE "Material" SET "PartNumber" = %s WHERE "ItemID" = %s',
                    (clean_part_number, match["ItemID"]),
                )
                match["PartNumber"] = clean_part_number
            if clean_unit and not match.get("Unit"):
                cur.execute(
                    'UPDATE "Material" SET "Unit" = %s WHERE "ItemID" = %s',
                    (clean_unit, match["ItemID"]),
                )
                match["Unit"] = clean_unit
            conn.commit()
            return dict(match)

        cur.execute(
            '''
            INSERT INTO "Material"
            ("PartNumber", "Description", "Unit", "CarryPriceSource", "IsActive", "InternalPrice")
            VALUES (%s, %s, %s, 'Internal', TRUE, 0)
            RETURNING "ItemID", "PartNumber", "Description", "Unit"
            ''',
            (clean_part_number or "", clean_description, clean_unit),
        )
        created = cur.fetchone()
        conn.commit()
        return dict(created)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_material_price_history(item_id, limit=25):
    ensure_material_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT
                "MaterialVendorPriceHistoryID",
                "VendorID",
                "VendorName",
                "WholesalerName",
                "PriceRequestID",
                "PRItemID",
                "VendorPartNumber",
                "VendorQuoteNumber",
                "QuoteDate",
                "UnitPrice",
                "SourceType",
                "SourceFilePath",
                "CapturedAt"
            FROM public."MaterialVendorPriceHistory"
            WHERE "ItemID" = %s
            ORDER BY "CapturedAt" DESC, "MaterialVendorPriceHistoryID" DESC
            LIMIT %s
            """,
            (item_id, limit),
        )
        return cur.fetchall()
    finally:
        conn.close()


def save_material(material_id, payload):
    ensure_material_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        existing = None
        if material_id:
            cur.execute('SELECT * FROM "Material" WHERE "ItemID" = %s', (material_id,))
            existing = cur.fetchone()

        carry_source = normalize_carry_price_source(payload.get("carry_source"))
        part_number = _coerce_part_number(payload.get("part_number"))
        description = _coerce_optional_text(payload.get("description"))
        unit = _coerce_optional_text(payload.get("unit"))
        is_active = _coerce_optional_bool(payload.get("is_active"), default=True)
        internal_price = _coerce_optional_decimal(payload.get("internal_price"))

        if not description:
            raise ValueError("Description is required.")

        if material_id:
            cur.execute(
                """
                UPDATE "Material"
                SET "PartNumber" = %s,
                    "Description" = %s,
                    "Unit" = %s,
                    "CarryPriceSource" = %s,
                    "IsActive" = %s,
                    "InternalPrice" = %s
                WHERE "ItemID" = %s
                """,
                (part_number or "", description, unit, carry_source, is_active, internal_price, material_id),
            )
            target_item_id = material_id
        else:
            cur.execute(
                """
                INSERT INTO "Material"
                ("PartNumber", "Description", "Unit", "CarryPriceSource", "IsActive", "InternalPrice")
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING "ItemID"
                """,
                (part_number or "", description, unit, carry_source, is_active, internal_price),
            )
            target_item_id = cur.fetchone()["ItemID"]

        if existing is None:
            cur.execute('SELECT * FROM "Material" WHERE "ItemID" = %s', (target_item_id,))
            existing = cur.fetchone() or {}

        for wholesaler_name in ("Nedco", "Gescan", "Eecol", "Guillevin"):
            columns = WHOLESALER_COLUMN_MAP[wholesaler_name]
            price_value = _coerce_optional_decimal(payload.get(f"{wholesaler_name.lower()}_price"))
            part_value = _coerce_optional_text(payload.get(f"{wholesaler_name.lower()}_part_number"))
            old_price = existing.get(columns["price"].replace('"', ""))
            old_date = existing.get(columns["date"].replace('"', "")) if columns["date"] else None
            new_date = old_date
            if _prices_differ(old_price, price_value):
                new_date = datetime.datetime.now(datetime.timezone.utc) if price_value is not None else None
            cur.execute(
                f'''
                UPDATE "Material"
                SET {columns["price"]} = %s,
                    {columns["date"]} = %s,
                    {columns["part"]} = %s
                WHERE "ItemID" = %s
                ''',
                (price_value, new_date, part_value, target_item_id),
            )
            if _prices_differ(old_price, price_value) and price_value is not None:
                insert_material_price_history(
                    cur,
                    target_item_id,
                    wholesaler_name=wholesaler_name,
                    unit_price=price_value,
                    vendor_name=wholesaler_name,
                    vendor_part_number=part_value,
                    quote_date=(new_date.date().isoformat() if isinstance(new_date, datetime.datetime) else None),
                    source_type="Manual Catalog Edit",
                )

        conn.commit()
        return target_item_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_material_price_history(
    cur,
    item_id,
    wholesaler_name,
    unit_price,
    vendor_name=None,
    vendor_id=None,
    rfq_id=None,
    pr_item_id=None,
    vendor_part_number=None,
    vendor_quote_number=None,
    quote_date=None,
    source_type="Vendor Quote",
    source_file_path=None,
):
    cur.execute(
        """
        INSERT INTO public."MaterialVendorPriceHistory"
        (
            "ItemID",
            "VendorID",
            "VendorName",
            "WholesalerName",
            "PriceRequestID",
            "PRItemID",
            "VendorPartNumber",
            "VendorQuoteNumber",
            "QuoteDate",
            "UnitPrice",
            "SourceType",
            "SourceFilePath"
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            item_id,
            vendor_id,
            vendor_name,
            wholesaler_name,
            rfq_id,
            pr_item_id,
            vendor_part_number,
            vendor_quote_number,
            quote_date,
            unit_price,
            source_type,
            source_file_path,
        ),
    )


def record_vendor_quote_price(
    cur,
    item_id,
    unit_price,
    vendor_name,
    vendor_id=None,
    rfq_id=None,
    pr_item_id=None,
    quote_number=None,
    quote_date=None,
    vendor_part_number=None,
    source_file_path=None,
):
    wholesaler_name = infer_wholesaler_key(vendor_name)
    if wholesaler_name:
        columns = WHOLESALER_COLUMN_MAP[wholesaler_name]
        cur.execute(
            f'''
            UPDATE "Material"
            SET {columns["price"]} = %s,
                {columns["date"]} = %s,
                {columns["rfq"]} = %s,
                {columns["part"]} = COALESCE(%s, {columns["part"]}),
                "CarryPriceSource" = %s
            WHERE "ItemID" = %s
            ''',
            (
                unit_price,
                quote_date or datetime.date.today().isoformat(),
                quote_number,
                vendor_part_number,
                wholesaler_name,
                item_id,
            ),
        )

    insert_material_price_history(
        cur,
        item_id,
        wholesaler_name=wholesaler_name or vendor_name or "Unknown",
        unit_price=unit_price,
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        rfq_id=rfq_id,
        pr_item_id=pr_item_id,
        vendor_part_number=vendor_part_number,
        vendor_quote_number=quote_number,
        quote_date=quote_date,
        source_type="Vendor Quote",
        source_file_path=source_file_path,
    )
    return wholesaler_name


def record_vendor_quote_history_only(
    cur,
    item_id,
    unit_price,
    vendor_name,
    vendor_id=None,
    rfq_id=None,
    pr_item_id=None,
    quote_number=None,
    quote_date=None,
    vendor_part_number=None,
    source_file_path=None,
):
    """Persist quote-history evidence without mutating Material pricing columns.

    This helper exists so Receive Quotes and approval-gated quote apply can store
    passive vendor history while protecting Material.InternalPrice and the wider
    catalogue baseline from silent mutation.
    """
    wholesaler_name = infer_wholesaler_key(vendor_name)
    insert_material_price_history(
        cur,
        item_id,
        wholesaler_name=wholesaler_name or vendor_name or "Unknown",
        unit_price=unit_price,
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        rfq_id=rfq_id,
        pr_item_id=pr_item_id,
        vendor_part_number=vendor_part_number,
        vendor_quote_number=quote_number,
        quote_date=quote_date,
        source_type="Vendor Quote",
        source_file_path=source_file_path,
    )
    return wholesaler_name


def record_purchase_order_price(item_id, unit_price, vendor_name=None, vendor_id=None, vendor_part_number=None):
    """Stores the latest direct-purchase price back into the material catalog."""
    ensure_material_schema()
    target_item_id = int(item_id or 0)
    price_value = _coerce_optional_decimal(unit_price)
    if target_item_id <= 0 or price_value is None:
        return None

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('SELECT * FROM "Material" WHERE "ItemID" = %s', (target_item_id,))
        material = cur.fetchone()
        if not material:
            raise ValueError(f"Material #{target_item_id} was not found.")

        wholesaler_name = infer_wholesaler_key(vendor_name)
        today = datetime.date.today().isoformat()
        if wholesaler_name:
            columns = WHOLESALER_COLUMN_MAP[wholesaler_name]
            cur.execute(
                f'''
                UPDATE "Material"
                SET {columns["price"]} = %s,
                    {columns["date"]} = %s,
                    {columns["part"]} = COALESCE(%s, {columns["part"]}),
                    "CarryPriceSource" = %s
                WHERE "ItemID" = %s
                ''',
                (
                    price_value,
                    today,
                    _coerce_optional_text(vendor_part_number),
                    wholesaler_name,
                    target_item_id,
                ),
            )
        else:
            cur.execute(
                '''
                UPDATE "Material"
                SET "InternalPrice" = %s,
                    "CarryPriceSource" = 'Internal'
                WHERE "ItemID" = %s
                ''',
                (price_value, target_item_id),
            )

        insert_material_price_history(
            cur,
            target_item_id,
            wholesaler_name=wholesaler_name or vendor_name or "Internal",
            unit_price=price_value,
            vendor_name=vendor_name,
            vendor_id=vendor_id,
            vendor_part_number=_coerce_optional_text(vendor_part_number),
            quote_date=today,
            source_type="Purchase Order",
        )
        conn.commit()
        return wholesaler_name
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_carried_price(material_row):
    """Calculates the active price based on the CarryPriceSource."""
    source = normalize_carry_price_source(material_row.get("CarryPriceSource"))
    if source == "Internal":
        return float(material_row.get("InternalPrice") or 0.0)
    if source == "Nedco":
        return float(material_row.get("NedcoPrice") or material_row.get("InternalPrice") or 0.0)
    if source == "Gescan":
        return float(material_row.get("GescanPrice") or material_row.get("InternalPrice") or 0.0)
    if source == "Eecol":
        return float(material_row.get("EecolPrice") or material_row.get("InternalPrice") or 0.0)
    if source == "Guillevin":
        return float(material_row.get("GuillevinPrice") or material_row.get("InternalPrice") or 0.0)
    return float(material_row.get("InternalPrice") or 0.0)


def get_carried_price_date(material_row):
    source = normalize_carry_price_source(material_row.get("CarryPriceSource"))
    if source == "Nedco":
        return material_row.get("NedcoLastPriceDate")
    if source == "Gescan":
        return material_row.get("GescanLastPriceDate")
    if source == "Eecol":
        return material_row.get("EecolLastPriceDate")
    if source == "Guillevin":
        return material_row.get("GuillevinLastPriceDate")
    return None
