from neon_ai.database.connection import get_connection


def _is_blank(value):
    text = str(value or "").strip()
    return text == "" or text.lower() in {"pending", "null", "none", "unknown", "n/a"}


def _normalize_account_number(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    raise ValueError("Account Number must contain digits only.")


def ensure_vendor_schema():
    """Create the lightweight vendor extensions needed by the upgraded vendor workspace."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('ALTER TABLE public."Vendor" ADD COLUMN IF NOT EXISTS "BillingAddress" text')
        cur.execute('ALTER TABLE public."Vendor" ADD COLUMN IF NOT EXISTS "BillingCity" text')
        cur.execute('ALTER TABLE public."Vendor" ADD COLUMN IF NOT EXISTS "VendorMainPhone" text')
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."VendorContact" (
                "VendorContactID" SERIAL PRIMARY KEY,
                "VendorID" integer NOT NULL REFERENCES public."Vendor"("VendorID") ON DELETE CASCADE,
                "ContactName" text,
                "Phone" text,
                "MobilePhone" text,
                "Email" text
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_vendor_pipeline():
    ensure_vendor_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                v."VendorID",
                COALESCE(v."VendorName", '') AS "VendorName",
                COALESCE(v."AccountNumber"::text, '') AS "AccountNumber",
                COALESCE(v."VendorCity", '') AS "VendorCity",
                COALESCE(v."VendorMainPhone", v."VendorContactNumber", '') AS "MainPhone",
                COUNT(DISTINCT po."PurchaseOrderID") AS "POCount",
                COUNT(DISTINCT pr."PriceRequestID") AS "RFQCount"
            FROM "Vendor" v
            LEFT JOIN "PurchaseOrder" po ON po."VendorID" = v."VendorID"
            LEFT JOIN "PriceRequest" pr ON pr."VendorID" = v."VendorID"
            GROUP BY v."VendorID", v."VendorName", v."AccountNumber", v."VendorCity", v."VendorMainPhone", v."VendorContactNumber"
            ORDER BY v."VendorName"
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_vendor_by_id(vendor_id: int):
    ensure_vendor_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM "Vendor" WHERE "VendorID" = %s', (vendor_id,))
        return cur.fetchone()
    finally:
        conn.close()


def get_vendor_contacts(vendor_id: int):
    ensure_vendor_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT "VendorContactID", "ContactName", "Phone", "MobilePhone", "Email"
            FROM public."VendorContact"
            WHERE "VendorID" = %s
            ORDER BY "ContactName" NULLS LAST, "VendorContactID"
            """,
            (vendor_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def save_vendor(vendor_id, vendor_data, contacts):
    ensure_vendor_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        payload = (
            _normalize_account_number(vendor_data.get("account_number")),
            vendor_data.get("vendor_name"),
            vendor_data.get("street_address"),
            vendor_data.get("city"),
            vendor_data.get("billing_address"),
            vendor_data.get("billing_city"),
            vendor_data.get("main_phone"),
        )

        if vendor_id:
            cur.execute(
                """
                UPDATE "Vendor"
                SET "AccountNumber" = %s,
                    "VendorName" = %s,
                    "VendorAddressNumber" = %s,
                    "VendorCity" = %s,
                    "BillingAddress" = %s,
                    "BillingCity" = %s,
                    "VendorMainPhone" = %s,
                    "VendorContactName" = NULL,
                    "VendorContactNumber" = NULL
                WHERE "VendorID" = %s
                """,
                payload + (vendor_id,),
            )
            target_vendor_id = vendor_id
        else:
            cur.execute(
                """
                INSERT INTO "Vendor"
                ("AccountNumber", "VendorName", "VendorAddressNumber", "VendorCity", "BillingAddress", "BillingCity", "VendorMainPhone")
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING "VendorID"
                """,
                payload,
            )
            target_vendor_id = cur.fetchone()["VendorID"]

        cur.execute('DELETE FROM public."VendorContact" WHERE "VendorID" = %s', (target_vendor_id,))

        first_contact_name = None
        first_contact_phone = None
        for contact in contacts:
            name = str(contact.get("name") or "").strip()
            phone = str(contact.get("phone") or "").strip()
            mobile = str(contact.get("mobile") or "").strip()
            email = str(contact.get("email") or "").strip()
            if not any((name, phone, mobile, email)):
                continue
            if first_contact_name is None:
                first_contact_name = name or None
                first_contact_phone = phone or mobile or None
            cur.execute(
                """
                INSERT INTO public."VendorContact" ("VendorID", "ContactName", "Phone", "MobilePhone", "Email")
                VALUES (%s, %s, %s, %s, %s)
                """,
                (target_vendor_id, name or None, phone or None, mobile or None, email or None),
            )

        if first_contact_name or first_contact_phone:
            cur.execute(
                """
                UPDATE "Vendor"
                SET "VendorContactName" = %s,
                    "VendorContactNumber" = %s
                WHERE "VendorID" = %s
                """,
                (first_contact_name, first_contact_phone, target_vendor_id),
            )

        conn.commit()
        return target_vendor_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_vendor_overview(vendor_id: int):
    ensure_vendor_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(pr."Status", '') AS "Status",
                COALESCE(pr."DueDate"::text, '') AS "DueDate"
            FROM "PriceRequest" pr
            JOIN "Estimate" e ON pr."EstimateID" = e."EstimateID"
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            WHERE pr."VendorID" = %s
            ORDER BY pr."PriceRequestID" DESC
            """,
            (vendor_id,),
        )
        rfqs = cur.fetchall()

        cur.execute(
            """
            SELECT
                po."PurchaseOrderID",
                COALESCE(po."Date"::text, '') AS "Date",
                COALESCE(po."Status", '') AS "Status",
                COALESCE(po."PurchaseOrderTotal", 0) AS "PurchaseOrderTotal",
                COALESCE(s."SiteName", '') AS "SiteName"
            FROM "PurchaseOrder" po
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            WHERE po."VendorID" = %s
            ORDER BY po."PurchaseOrderID" DESC
            """,
            (vendor_id,),
        )
        purchase_orders = cur.fetchall()

        cur.execute(
            """
            SELECT
                vi."VendorInvoiceID",
                COALESCE(vi."VendorInvoiceNumber", '') AS "VendorInvoiceNumber",
                COALESCE(vi."VendorInvoiceDate", '') AS "VendorInvoiceDate",
                COALESCE(vi."VendorInvoiceStatus", '') AS "VendorInvoiceStatus",
                COALESCE(vi."VendorInvoiceAmount", 0) AS "VendorInvoiceAmount"
            FROM "VendorInvoice" vi
            JOIN "PurchaseOrder" po ON vi."PurchaseOrderID" = po."PurchaseOrderID"
            WHERE po."VendorID" = %s
            ORDER BY vi."VendorInvoiceID" DESC
            """,
            (vendor_id,),
        )
        invoices = cur.fetchall()

        return {
            "rfqs": rfqs,
            "purchase_orders": purchase_orders,
            "invoices": invoices,
        }
    finally:
        conn.close()
