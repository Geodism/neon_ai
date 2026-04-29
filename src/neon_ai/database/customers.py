from neon_ai.database.connection import get_connection


def _is_blank(value):
    text = str(value or "").strip()
    return text == "" or text.lower() in {"pending", "null", "none", "unknown", "n/a"}


def _clean_value(value):
    text = str(value or "").strip()
    return "" if _is_blank(text) else text


def sync_table_sequence(cur, table_name: str, id_column: str):
    """Realign a SERIAL sequence with the current max ID before inserting."""
    cur.execute(
        '''
        SELECT setval(
            pg_get_serial_sequence('"{table_name}"', %s),
            COALESCE((SELECT MAX("{id_column}") FROM "{table_name}"), 0) + 1,
            false
        )
        '''.format(table_name=table_name, id_column=id_column),
        (id_column,),
    )


def ensure_customer_contact_table():
    """Create the lightweight contact table the first time the customer UI needs it."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."CustomerContact" (
                "CustomerContactID" SERIAL PRIMARY KEY,
                "CustomerID" integer NOT NULL REFERENCES public."Customer"("CustomerID") ON DELETE CASCADE,
                "ContactName" text,
                "Phone" text,
                "Email" text
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_customers():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM "Customer" ORDER BY "CustomerName"')
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_site(customer_id, site_name, street_number, street_name, city):
    conn = get_connection()
    cur = conn.cursor()
    try:
        sync_table_sequence(cur, "Site", "SiteID")
        cur.execute(
            """
            INSERT INTO "Site" ("CustomerID", "SiteName", "StreetNumber", "StreetName", "City")
            VALUES (%s, %s, %s, %s, %s)
            RETURNING "SiteID"
            """,
            (customer_id, site_name, street_number, street_name, city),
        )
        new_site_id = cur.fetchone()["SiteID"]
        conn.commit()
        return new_site_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_sites():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                s.*,
                COALESCE(c."CustomerName", '') AS "CustomerName"
            FROM "Site" s
            LEFT JOIN "Customer" c ON c."CustomerID" = s."CustomerID"
            ORDER BY c."CustomerName", s."SiteName", s."SiteID"
            '''
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_all_customers():
    """Fetches all customers for dropdowns."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT "CustomerID", "CustomerName" FROM "Customer" ORDER BY "CustomerName"')
    rows = cur.fetchall()
    conn.close()
    return rows


def get_sites_for_customer(customer_id: int):
    """Fetches only the sites belonging to a specific customer."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT "SiteID", "SiteName", "StreetNumber", "StreetName", "City"
            FROM "Site"
            WHERE "CustomerID" = %s
            ORDER BY "SiteName", "SiteID"
            ''',
            (customer_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_site_by_id(site_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                s.*,
                COALESCE(c."CustomerName", '') AS "CustomerName"
            FROM "Site" s
            LEFT JOIN "Customer" c ON c."CustomerID" = s."CustomerID"
            WHERE s."SiteID" = %s
            ''',
            (site_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def save_site(site_id, customer_id, site_name, street_number, street_name, city):
    conn = get_connection()
    cur = conn.cursor()
    try:
        payload = (customer_id, site_name, street_number, street_name, city)
        if site_id:
            cur.execute(
                '''
                UPDATE "Site"
                SET "CustomerID" = %s,
                    "SiteName" = %s,
                    "StreetNumber" = %s,
                    "StreetName" = %s,
                    "City" = %s
                WHERE "SiteID" = %s
                ''',
                payload + (site_id,),
            )
            target_site_id = site_id
        else:
            sync_table_sequence(cur, "Site", "SiteID")
            cur.execute(
                '''
                INSERT INTO "Site" ("CustomerID", "SiteName", "StreetNumber", "StreetName", "City")
                VALUES (%s, %s, %s, %s, %s)
                RETURNING "SiteID"
                ''',
                payload,
            )
            target_site_id = cur.fetchone()["SiteID"]

        conn.commit()
        return target_site_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_site(site_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT COUNT(*) AS "EstimateCount" FROM "Estimate" WHERE "SiteID" = %s', (site_id,))
        estimate_count = int(cur.fetchone()["EstimateCount"] or 0)
        cur.execute('SELECT COUNT(*) AS "WorkOrderCount" FROM "WorkOrder" WHERE "SiteID" = %s', (site_id,))
        work_order_count = int(cur.fetchone()["WorkOrderCount"] or 0)

        if estimate_count or work_order_count:
            raise ValueError(
                "This site cannot be deleted because it is already linked to "
                f"{estimate_count} estimate(s) and {work_order_count} work order(s)."
            )

        cur.execute('DELETE FROM "Site" WHERE "SiteID" = %s', (site_id,))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_customer(customer_name, address, street_name, city_name, postal_code, email=None, phone=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO "Customer" ("CustomerName", "Address", "StreetName", "CityName", "PostalCode", "Email", "Phone")
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (customer_name, address, street_name, city_name, postal_code, email, phone),
    )
    conn.commit()
    conn.close()


def get_customer_pipeline():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                c."CustomerID",
                c."CustomerName",
                COALESCE(c."CityName", '') AS "CityName",
                COALESCE(c."Phone", '') AS "Phone",
                COALESCE(c."Email", '') AS "Email",
                COUNT(DISTINCT s."SiteID") AS "SiteCount",
                COUNT(DISTINCT wo."WorkOrderID") AS "WorkOrderCount"
            FROM "Customer" c
            LEFT JOIN "Site" s ON s."CustomerID" = c."CustomerID"
            LEFT JOIN "WorkOrder" wo ON wo."SiteID" = s."SiteID"
            GROUP BY c."CustomerID", c."CustomerName", c."CityName", c."Phone", c."Email"
            ORDER BY c."CustomerName"
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_customer_by_id(customer_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM "Customer" WHERE "CustomerID" = %s', (customer_id,))
        return cur.fetchone()
    finally:
        conn.close()


def get_customer_by_email(email: str):
    if not email:
        return None
    conn = get_connection()
    cur = conn.cursor()
    try:
        clean_email = email.split("<")[-1].strip("> ").lower()
        cur.execute(
            'SELECT * FROM "Customer" WHERE LOWER(COALESCE("Email", \'\')) = %s LIMIT 1',
            (clean_email,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_customer_primary_site(customer_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT "SiteID", "SiteName", "StreetNumber", "StreetName", "City"
            FROM "Site"
            WHERE "CustomerID" = %s
            ORDER BY "SiteID" ASC
            LIMIT 1
            ''',
            (customer_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_customer_latest_estimate_id(customer_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT e."EstimateID"
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            WHERE s."CustomerID" = %s
            ORDER BY e."EstimateID" DESC
            LIMIT 1
            ''',
            (customer_id,),
        )
        row = cur.fetchone()
        return row["EstimateID"] if row else None
    finally:
        conn.close()


def get_customer_missing_fields(customer_id: int):
    customer = get_customer_by_id(customer_id)
    if not customer:
        return []

    missing = []
    field_map = {
        "CustomerName": customer.get("CustomerName"),
        "CustomerEmail": customer.get("Email"),
        "CustomerPhone": customer.get("Phone"),
        "AddressNumber": customer.get("Address"),
        "StreetName": customer.get("StreetName"),
        "CityName": customer.get("CityName"),
        "PostalCode": customer.get("PostalCode"),
    }
    for label, value in field_map.items():
        if _is_blank(value):
            missing.append(label)
    return missing


def update_customer_missing_fields(customer_id: int, profile: dict):
    """Fill in missing customer fields from parsed inbox history without overwriting known values."""
    customer = get_customer_by_id(customer_id)
    if not customer:
        return {"updated_fields": [], "customer": None}

    updates = {}
    mapping = {
        "CustomerName": "CustomerName",
        "CustomerEmail": "Email",
        "CustomerPhone": "Phone",
        "AddressNumber": "Address",
        "StreetName": "StreetName",
        "CityName": "CityName",
        "PostalCode": "PostalCode",
    }

    for profile_key, column_name in mapping.items():
        incoming_value = _clean_value((profile or {}).get(profile_key))
        if incoming_value and _is_blank(customer.get(column_name)):
            updates[column_name] = incoming_value

    if not updates:
        return {"updated_fields": [], "customer": customer}

    conn = get_connection()
    cur = conn.cursor()
    try:
        set_clause = ", ".join(f'"{column}" = %s' for column in updates.keys())
        params = list(updates.values()) + [customer_id]
        cur.execute(f'UPDATE "Customer" SET {set_clause} WHERE "CustomerID" = %s', params)
        conn.commit()
    finally:
        conn.close()

    return {"updated_fields": list(updates.keys()), "customer": get_customer_by_id(customer_id)}


def update_customer_fields(customer_id: int, profile: dict):
    """Apply explicitly provided customer profile fields, even if they correct existing values."""
    customer = get_customer_by_id(customer_id)
    if not customer:
        return {"updated_fields": [], "customer": None}

    updates = {}
    mapping = {
        "CustomerName": "CustomerName",
        "CustomerEmail": "Email",
        "CustomerPhone": "Phone",
        "AddressNumber": "Address",
        "StreetName": "StreetName",
        "CityName": "CityName",
        "PostalCode": "PostalCode",
    }

    for profile_key, column_name in mapping.items():
        incoming_value = _clean_value((profile or {}).get(profile_key))
        if incoming_value and str(customer.get(column_name) or "").strip() != incoming_value:
            updates[column_name] = incoming_value

    if not updates:
        return {"updated_fields": [], "customer": customer}

    conn = get_connection()
    cur = conn.cursor()
    try:
        set_clause = ", ".join(f'"{column}" = %s' for column in updates.keys())
        params = list(updates.values()) + [customer_id]
        cur.execute(f'UPDATE "Customer" SET {set_clause} WHERE "CustomerID" = %s', params)
        conn.commit()
    finally:
        conn.close()

    return {"updated_fields": list(updates.keys()), "customer": get_customer_by_id(customer_id)}


def update_site_fields(site_id: int, profile: dict):
    """Apply customer-confirmed site address details to the linked site record."""
    if not site_id:
        return {"updated_fields": [], "site": None}

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM "Site" WHERE "SiteID" = %s', (site_id,))
        site = cur.fetchone()
        if not site:
            return {"updated_fields": [], "site": None}

        same_as_customer = str((profile or {}).get("SiteSameAsCustomer") or "").strip().lower() in {"yes", "true", "same", "same as customer", "1"}
        if same_as_customer:
            street_number = _clean_value(profile.get("AddressNumber"))
            street_name = _clean_value(profile.get("StreetName"))
            city_name = _clean_value(profile.get("CityName"))
            site_name = _clean_value(profile.get("SiteName")) or " ".join(part for part in [street_number, street_name] if part)
        else:
            street_number = _clean_value(profile.get("SiteStreetNumber"))
            street_name = _clean_value(profile.get("SiteStreetName"))
            city_name = _clean_value(profile.get("SiteCity"))
            site_name = _clean_value(profile.get("SiteName"))

        updates = {}
        if street_number and str(site.get("StreetNumber") or "").strip() != street_number:
            updates["StreetNumber"] = street_number
        if street_name and str(site.get("StreetName") or "").strip() != street_name:
            updates["StreetName"] = street_name
        if city_name and str(site.get("City") or "").strip() != city_name:
            updates["City"] = city_name
        if site_name and (
            _is_blank(site.get("SiteName")) or
            "pending site" in str(site.get("SiteName") or "").lower() or
            str(site.get("SiteName") or "").strip() != site_name
        ):
            updates["SiteName"] = site_name

        if not updates:
            return {"updated_fields": [], "site": site}

        set_clause = ", ".join(f'"{column}" = %s' for column in updates.keys())
        params = list(updates.values()) + [site_id]
        cur.execute(f'UPDATE "Site" SET {set_clause} WHERE "SiteID" = %s', params)
        conn.commit()
        cur.execute('SELECT * FROM "Site" WHERE "SiteID" = %s', (site_id,))
        updated_site = cur.fetchone()
        return {"updated_fields": list(updates.keys()), "site": updated_site}
    finally:
        conn.close()


def get_customer_contacts(customer_id: int):
    ensure_customer_contact_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT "CustomerContactID", "ContactName", "Phone", "Email"
            FROM public."CustomerContact"
            WHERE "CustomerID" = %s
            ORDER BY "ContactName" NULLS LAST, "CustomerContactID"
            """,
            (customer_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def save_customer(customer_id, customer_data, contacts):
    """Creates or updates a customer and replaces the related contact list."""
    ensure_customer_contact_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        payload = (
            customer_data.get("customer_name"),
            customer_data.get("address"),
            customer_data.get("street_name"),
            customer_data.get("city_name"),
            customer_data.get("postal_code"),
            customer_data.get("email"),
            customer_data.get("phone"),
        )

        if customer_id:
            cur.execute(
                """
                UPDATE "Customer"
                SET "CustomerName" = %s,
                    "Address" = %s,
                    "StreetName" = %s,
                    "CityName" = %s,
                    "PostalCode" = %s,
                    "Email" = %s,
                    "Phone" = %s
                WHERE "CustomerID" = %s
                """,
                payload + (customer_id,),
            )
            target_customer_id = customer_id
        else:
            cur.execute(
                """
                INSERT INTO "Customer" ("CustomerName", "Address", "StreetName", "CityName", "PostalCode", "Email", "Phone")
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING "CustomerID"
                """,
                payload,
            )
            target_customer_id = cur.fetchone()["CustomerID"]

        cur.execute('DELETE FROM public."CustomerContact" WHERE "CustomerID" = %s', (target_customer_id,))

        for contact in contacts:
            name = (contact.get("name") or "").strip()
            phone = (contact.get("phone") or "").strip()
            email = (contact.get("email") or "").strip()
            if not any((name, phone, email)):
                continue
            cur.execute(
                """
                INSERT INTO public."CustomerContact" ("CustomerID", "ContactName", "Phone", "Email")
                VALUES (%s, %s, %s, %s)
                """,
                (target_customer_id, name or None, phone or None, email or None),
            )

        conn.commit()
        return target_customer_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_customer_portfolio(customer_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT "SiteID", COALESCE("SiteName", '') AS "SiteName",
                   COALESCE("StreetNumber", '') AS "StreetNumber",
                   COALESCE("StreetName", '') AS "StreetName",
                   COALESCE("City", '') AS "City"
            FROM "Site"
            WHERE "CustomerID" = %s
            ORDER BY "SiteName", "SiteID"
            """,
            (customer_id,),
        )
        sites = cur.fetchall()

        cur.execute(
            """
            SELECT
                e."EstimateID",
                COALESCE(e."CreatedDate"::text, '') AS "CreatedDate",
                COALESCE(e."Status", '') AS "Status",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(e."TotalAmount", 0) AS "TotalAmount"
            FROM "Estimate" e
            JOIN "Site" s ON s."SiteID" = e."SiteID"
            WHERE s."CustomerID" = %s
            ORDER BY e."EstimateID" DESC
            """,
            (customer_id,),
        )
        estimates = cur.fetchall()

        cur.execute(
            """
            WITH invoice_rollup AS (
                SELECT
                    i."WorkOrderID",
                    COALESCE(SUM(i."CustomerInvoiceAmount"::numeric), 0) AS "TotalBilled",
                    COALESCE(SUM(CASE WHEN i."InvDatePaid" IS NOT NULL THEN i."CustomerInvoiceAmount"::numeric ELSE 0 END), 0) AS "TotalPaid"
                FROM "Invoice" i
                GROUP BY i."WorkOrderID"
            )
            SELECT
                wo."WorkOrderID",
                COALESCE(s."SiteName", '') AS "SiteName",
                CASE
                    WHEN COALESCE(wo."IsClosed", FALSE) THEN 'CLOSED'
                    WHEN COALESCE(NULLIF(wo."JobStatus", ''), '') != '' THEN wo."JobStatus"
                    ELSE 'OPEN'
                END AS "WOStatus",
                COALESCE(src."TotalAmount", est_guess."TotalAmount", 0) AS "WOValue",
                (COALESCE(ir."TotalBilled", 0) - COALESCE(ir."TotalPaid", 0)) AS "Owing"
            FROM "WorkOrder" wo
            JOIN "Site" s ON s."SiteID" = wo."SiteID"
            LEFT JOIN "Estimate" src ON src."EstimateID" = wo."SourceEstimateID"
            LEFT JOIN LATERAL (
                SELECT e."TotalAmount"
                FROM "Estimate" e
                WHERE e."SiteID" = wo."SiteID"
                ORDER BY e."IsConverted" DESC, e."EstimateID" DESC
                LIMIT 1
            ) est_guess ON TRUE
            LEFT JOIN invoice_rollup ir ON ir."WorkOrderID" = wo."WorkOrderID"
            WHERE s."CustomerID" = %s
            ORDER BY wo."WorkOrderID" DESC
            """,
            (customer_id,),
        )
        work_orders = cur.fetchall()

        return {
            "sites": sites,
            "estimates": estimates,
            "work_orders": work_orders,
        }
    finally:
        conn.close()
