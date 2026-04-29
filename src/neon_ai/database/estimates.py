from tkinter import messagebox

from neon_ai.database.connection import get_connection
from neon_ai.database.materials import get_or_create_material_from_estimate
from psycopg2.extras import RealDictCursor

def _log_estimate_sheet_save(cur, estimate_id, action_label, labor_lines, material_lines):
    """Writes an audit note whenever the estimate sheet is saved."""
    note_text = (
        f"{action_label} estimate sheet saved. "
        f"Labor lines: {len(labor_lines)}. Material lines: {len(material_lines)}."
    )
    cur.execute(
        'INSERT INTO "Note" ("EstimateID", "NoteText", "Category") VALUES (%s, %s, %s)',
        (estimate_id, note_text, 'Estimate Sheet')
    )


def ensure_estimate_sequence(cur):
    """Realign the estimate ID sequence before inserts to prevent duplicate IDs."""
    cur.execute(
        '''
        SELECT setval(
            pg_get_serial_sequence('"Estimate"', %s),
            COALESCE((SELECT MAX("EstimateID") FROM "Estimate"), 0) + 1,
            false
        )
        ''',
        ("EstimateID",),
    )


def normalize_estimate_status(status_value):
    text = str(status_value or "").strip().lower()
    if text in {"", "draft"}:
        return "Draft"
    if text in {"locked", "submitted"}:
        return "Locked"
    if text == "sent":
        return "Sent"
    if text == "accepted":
        return "Accepted"
    return str(status_value or "Draft").strip() or "Draft"


def is_estimate_editable_status(status_value):
    return normalize_estimate_status(status_value) == "Draft"


def _resolve_price_source(item_id, current_value=None):
    if current_value and str(current_value).strip():
        return str(current_value).strip()
    return "Catalog" if item_id else "Manual"


def _hydrate_material_catalog_links(material_lines):
    hydrated_lines = []
    for line in material_lines:
        m_desc, qty, cost, total, item_id, part_no, *rest = list(line)
        if not item_id:
            material = get_or_create_material_from_estimate(m_desc, part_no)
            item_id = material.get("ItemID")
            part_no = material.get("PartNumber") or part_no
        hydrated_lines.append([m_desc, qty, cost, total, item_id, part_no, *rest])
    return hydrated_lines


def _get_estimate_status(cur, estimate_id):
    cur.execute('SELECT "Status" FROM "Estimate" WHERE "EstimateID" = %s', (estimate_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Estimate #{estimate_id} was not found.")
    return normalize_estimate_status(row["Status"])


def _update_estimate_total_from_lines(cur, estimate_id):
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


def _get_carried_quote_material_ids(cur, estimate_id):
    cur.execute(
        '''
        SELECT DISTINCT pri."MaterialID"
        FROM "PriceRequestItem" pri
        JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
        WHERE pr."EstimateID" = %s
          AND pri."IsCarried" = TRUE
          AND pri."QuotedUnitPrice" IS NOT NULL
        ORDER BY pri."MaterialID"
        ''',
        (estimate_id,),
    )
    return [int(row["MaterialID"]) for row in cur.fetchall()]


def _apply_carried_quote_pricing(cur, estimate_id):
    cur.execute(
        '''
        WITH carried AS (
            SELECT DISTINCT ON (pri."MaterialID")
                pri."MaterialID",
                pri."QuotedUnitPrice",
                v."VendorName"
            FROM "PriceRequestItem" pri
            JOIN "PriceRequest" pr ON pri."PriceRequestID" = pr."PriceRequestID"
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."EstimateID" = %s
              AND pri."IsCarried" = TRUE
              AND pri."QuotedUnitPrice" IS NOT NULL
            ORDER BY
                pri."MaterialID",
                pr."VendorQuoteDate" DESC NULLS LAST,
                pr."PriceRequestID" DESC,
                pri."PRItemID" DESC
        )
        UPDATE "EstimateMaterial" em
        SET "UnitCost" = carried."QuotedUnitPrice",
            "LineTotal" = em."Quantity" * carried."QuotedUnitPrice",
            "AwardedUnitCost" = carried."QuotedUnitPrice",
            "AwardedVendor" = carried."VendorName",
            "PriceSource" = 'Vendor Quote'
        FROM carried
        WHERE em."EstimateID" = %s
          AND em."EstimateMaterialID" = carried."MaterialID"
          AND COALESCE(em."IsCommitted", FALSE) = FALSE
        ''',
        (estimate_id, estimate_id),
    )
    return _get_carried_quote_material_ids(cur, estimate_id)


def _restore_noncarried_quote_lines(cur, estimate_id, carried_ids):
    params = [estimate_id]
    exclusion_sql = ""
    if carried_ids:
        placeholders = ",".join(["%s"] * len(carried_ids))
        exclusion_sql = f' AND em."EstimateMaterialID" NOT IN ({placeholders})'
        params.extend(carried_ids)

    cur.execute(
        f'''
        UPDATE "EstimateMaterial" em
        SET "AwardedUnitCost" = NULL,
            "AwardedVendor" = NULL,
            "PriceSource" = CASE
                WHEN em."ItemID" IS NOT NULL THEN 'Catalog'
                ELSE 'Manual'
            END
        WHERE em."EstimateID" = %s
          AND COALESCE(em."IsCommitted", FALSE) = FALSE
          AND LOWER(TRIM(COALESCE(em."PriceSource", ''))) = 'vendor quote'
          {exclusion_sql}
        ''',
        params,
    )


def _sync_catalog_pricing(cur, estimate_id, excluded_material_ids=None):
    params = [estimate_id]
    exclusion_sql = ""
    if excluded_material_ids:
        placeholders = ",".join(["%s"] * len(excluded_material_ids))
        exclusion_sql = f' AND em."EstimateMaterialID" NOT IN ({placeholders})'
        params.extend(excluded_material_ids)

    cur.execute(
        f'''
        UPDATE "EstimateMaterial" em
        SET "UnitCost" = CASE COALESCE(m."CarryPriceSource", 'Internal')
                WHEN 'Nedco' THEN COALESCE(m."NedcoPrice", m."InternalPrice", 0)
                WHEN 'Gescan' THEN COALESCE(m."GescanPrice", m."InternalPrice", 0)
                WHEN 'Eecol' THEN COALESCE(m."EecolPrice", m."InternalPrice", 0)
                WHEN 'Guillevin' THEN COALESCE(m."GuillevinPrice", m."InternalPrice", 0)
                ELSE COALESCE(m."InternalPrice", 0)
            END,
            "LineTotal" = em."Quantity" * CASE COALESCE(m."CarryPriceSource", 'Internal')
                WHEN 'Nedco' THEN COALESCE(m."NedcoPrice", m."InternalPrice", 0)
                WHEN 'Gescan' THEN COALESCE(m."GescanPrice", m."InternalPrice", 0)
                WHEN 'Eecol' THEN COALESCE(m."EecolPrice", m."InternalPrice", 0)
                WHEN 'Guillevin' THEN COALESCE(m."GuillevinPrice", m."InternalPrice", 0)
                ELSE COALESCE(m."InternalPrice", 0)
            END,
            "AwardedUnitCost" = NULL,
            "AwardedVendor" = NULL,
            "PriceSource" = 'Catalog'
        FROM "Material" m
        WHERE em."EstimateID" = %s
          AND em."ItemID" = m."ItemID"
          AND COALESCE(em."IsCommitted", FALSE) = FALSE
          AND LOWER(TRIM(COALESCE(em."PriceSource", 'catalog'))) IN ('catalog', 'internal')
          {exclusion_sql}
        ''',
        params,
    )


def _sync_labor_rates_from_roles(cur, estimate_id):
    cur.execute(
        '''
        UPDATE "EstimateLabor" el
        SET "Rate" = sr."BaseRate" * (1 + (COALESCE(sr."BurdenPercent", 0) / 100.0)),
            "LineTotal" = el."Hours" * (sr."BaseRate" * (1 + (COALESCE(sr."BurdenPercent", 0) / 100.0)))
        FROM "StandardRole" sr
        WHERE el."EstimateID" = %s
          AND LOWER(TRIM(COALESCE(el."RoleDescription", ''))) = LOWER(TRIM(COALESCE(sr."RoleName", '')))
        ''',
        (estimate_id,),
    )


def sync_estimate_pricing_from_sources(estimate_id, cur=None):
    """Refreshes a draft estimate's labor and material pricing from current draft sources."""
    conn = None
    own_connection = cur is None
    if own_connection:
        conn = get_connection()
        cur = conn.cursor()
    try:
        status = _get_estimate_status(cur, estimate_id)
        if not is_estimate_editable_status(status):
            return {
                "estimate_status": status,
                "estimate_pricing_updated": False,
                "carried_material_ids": [],
            }

        carried_ids = _apply_carried_quote_pricing(cur, estimate_id)
        _restore_noncarried_quote_lines(cur, estimate_id, carried_ids)
        _sync_catalog_pricing(cur, estimate_id, excluded_material_ids=carried_ids)
        _sync_labor_rates_from_roles(cur, estimate_id)
        _update_estimate_total_from_lines(cur, estimate_id)

        if own_connection:
            conn.commit()

        return {
            "estimate_status": status,
            "estimate_pricing_updated": True,
            "carried_material_ids": carried_ids,
        }
    except Exception:
        if own_connection and conn:
            conn.rollback()
        raise
    finally:
        if own_connection and conn:
            conn.close()


def insert_estimate(site_id: int, description: str, estimated_hours: float, material_cost: float, markup_percentage: float):
    """
    Creates a new Estimate. Notice we removed 'total_amount'.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO "Estimate" ("SiteID", "Description", "EstimatedHours", "MaterialCost", "MarkupPercentage")
        VALUES (%s, %s, %s, %s, %s)
        RETURNING "EstimateId"
        """,
        (site_id, description, estimated_hours, material_cost, markup_percentage)
    )
    new_id = cur.fetchone()["EstimateId"]
    conn.commit()
    conn.close()
    
    return new_id

def insert_full_estimate(site_id, description, billing_type, lab_markup, mat_markup, labor_lines, material_lines):
    conn = get_connection()
    cur = conn.cursor()
    try:
        material_lines = _hydrate_material_catalog_links(material_lines)
        ensure_estimate_sequence(cur)
        cur.execute("""
            INSERT INTO "Estimate" 
            ("SiteID", "Description", "BillingType", "LaborMarkUp", "MaterialMarkUp", "Status", "CreatedDate")
            VALUES (%s, %s, %s, %s, %s, 'Draft', CURRENT_DATE)
            RETURNING "EstimateID"
        """, (site_id, description, billing_type, lab_markup, mat_markup))

        new_estimate_id = cur.fetchone()['EstimateID']

        for line in labor_lines:
            cur.execute(
                'INSERT INTO "EstimateLabor" ("EstimateID", "RoleDescription", "Hours", "Rate", "LineTotal") VALUES (%s, %s, %s, %s, %s)',
                (new_estimate_id, line[0], line[1], line[2], line[3])
            )

        for m in material_lines:
            cur.execute('''
                INSERT INTO "EstimateMaterial" 
                ("EstimateID", "Description", "Quantity", "UnitCost", "LineTotal", "ItemID", "PartNumber", "PriceSource") 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (new_estimate_id, m[0], m[1], m[2], m[3], m[4], m[5], _resolve_price_source(m[4])))

        sync_estimate_pricing_from_sources(new_estimate_id, cur=cur)

        _log_estimate_sheet_save(
            cur,
            new_estimate_id,
            "New",
            labor_lines,
            material_lines
        )
        conn.commit()
        return new_estimate_id
    except Exception as e:
        conn.rollback() 
        raise e
    finally:
        conn.close()

def get_recent_estimates():
    """
    Returns a list of the 10 most recent estimates.
    """
    conn = get_connection()
    cur = conn.cursor()
    # We JOIN on Site to get the friendly name instead of just the ID number
    cur.execute("""
        SELECT e."EstimateID", s."SiteName", e."Description", e."TotalAmount" 
        FROM "Estimate" e
        JOIN "Site" s ON e."SiteID" = s."SiteID"
        ORDER BY e."EstimateID" DESC LIMIT 10
    """)
    rows = cur.fetchall()
    conn.close()

    # THE CLEANER: Fix the Decimals for the AI!
    output = []
    for row in rows:
        amount = float(row["TotalAmount"]) if "Decimal" in str(type(row["TotalAmount"])) else row["TotalAmount"]
        # Truncate description if it's too long
        desc = (row['Description'][:30] + '...') if row['Description'] and len(row['Description']) > 30 else row['Description']
        output.append(f"ID: {row['EstimateId']} | Site: {row['SiteName']} | Desc: {desc} | Total: ${amount:.2f}")
    
    return "\n".join(output) if output else "No recent estimates found."



def get_all_estimates():
    """Fetches a list of all estimates for the dropdown menu."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT "EstimateID", "Description" FROM "Estimate" ORDER BY "EstimateID" DESC')
    rows = cur.fetchall()
    conn.close()
    return rows

def get_all_estimate_summaries():
    """Fetches every estimate so we can view them, regardless of status."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT e."EstimateID", s."SiteName", e."Status" 
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            ORDER BY e."EstimateID" DESC
        """)
        return cur.fetchall()
    finally:
        conn.close()

def get_estimate_materials(estimate_id):
    from neon_ai.database.connection import get_connection
    from psycopg2.extras import RealDictCursor

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if your table is "EstimateMaterial" or "estimatematerial"
        cur.execute('''
            SELECT "EstimateMaterialID", "Quantity", "Description"
            FROM "EstimateMaterial"
            WHERE "EstimateID" = %s
        ''', (estimate_id,))
        
        rows = cur.fetchall()
        print(f"DEBUG DB: Found {len(rows)} items for Est ID {estimate_id}") # Check your terminal for this!
        return rows
    except Exception as e:
        print(f"DATABASE ERROR: {e}")
        return []
    finally:
        conn.close()

def add_single_material(estimate_id: int, description: str, qty: float):
    """Adds a newly discovered material to an existing estimate."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO "EstimateMaterial" ("EstimateID", "Description", "Quantity", "UnitCost", "LineTotal", "PriceSource")
            VALUES (%s, %s, %s, 0.0, 0.0, 'Manual')
            """,
            (estimate_id, description, qty)
        )
        sync_estimate_pricing_from_sources(estimate_id, cur=cur)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def check_po_clearance(estimate_id: int) -> bool:
    """
    The Gatekeeper: Checks if the Estimate is tied to an Approved Work Order.
    Returns True if it is safe to order materials.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # We join through SiteID since WorkOrder doesn't explicitly store the EstimateID
    cur.execute(
        """
        SELECT w."IsApproved"
        FROM "WorkOrder" w
        JOIN "Estimate" e ON w."SiteID" = e."SiteID"
        WHERE e."EstimateID" = %s
        """,
        (estimate_id,)
    )
    
    row = cur.fetchone()
    conn.close()
    
    # If the work order exists AND is approved, return True.
    if row and row['IsApproved'] == True:
        return True
        
    return False

def get_dashboard_estimates():
    """Pulls pending estimates with Description and bulletproof math."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                e."EstimateID", 
                COALESCE(e."CreatedDate"::text, 'N/A'), 
                c."CustomerName", 
                s."SiteName", 
                COALESCE(e."Status", 'Draft') AS "Status",
                e."Description", -- <--- THE RESTORED WIRE!
                (
                    (COALESCE((SELECT SUM("LineTotal") FROM "EstimateLabor" WHERE "EstimateID" = e."EstimateID"), 0) * (1 + (COALESCE(e."LaborMarkUp", 0)/100.0))) +
                    (COALESCE((SELECT SUM("LineTotal") FROM "EstimateMaterial" WHERE "EstimateID" = e."EstimateID"), 0) * (1 + (COALESCE(e."MaterialMarkUp", 0)/100.0)))
                ) AS "TotalValue"
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE COALESCE(e."IsConverted", FALSE) = FALSE
            ORDER BY e."EstimateID" DESC
        """)
        return cur.fetchall()
    finally:
        conn.close()

def convert_estimate_to_workorder(estimate_id: int, po_number: str, doc_path: str):
    """
    Converts a winning estimate into an active Work Order, carrying over the PO and Auth Doc.
    FIXED: Now explicitly sets the JobStatus to 'Open' so the PO Command Center can see it!
    """
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Get the parent Estimate data
        cur.execute('SELECT "SiteID", "Description", "BillingType" FROM "Estimate" WHERE "EstimateID" = %s', (estimate_id,))
        est = cur.fetchone()
        if not est: raise Exception("Estimate not found.")

        # 2. Create the NEW Work Order with the PO, Document, AND JobStatus attached!
        cur.execute("""
            INSERT INTO "WorkOrder" 
            ("SiteID", "Description", "BillingType", "CustomerPO", "AcceptanceDocPath", "IsApproved", "IsClosed", "CreatedDate", "JobStatus", "SourceEstimateID")
            VALUES (%s, %s, %s, %s, %s, TRUE, FALSE, CURRENT_DATE::text, 'Open', %s)
            RETURNING "WorkOrderID"
        """, (est['SiteID'], est['Description'], est['BillingType'], po_number, doc_path, estimate_id))
        
        new_wo_id = cur.fetchone()['WorkOrderID']

        # 3. Mark the original Estimate as converted
        cur.execute('UPDATE "Estimate" SET "IsConverted" = TRUE, "Status" = \'Accepted\' WHERE "EstimateID" = %s', (estimate_id,))
        
       # 4. Optional: Add an automatic note to the unified Note table
        # We anchor this to the estimate_id so it stays in the project's permanent record.
        cur.execute("""
            INSERT INTO "Note" ("EstimateID", "NoteText", "Category") 
            VALUES (%s, %s, %s)
        """, (
            estimate_id, 
            f"Auto-Generated Work Order #{new_wo_id} from this estimate. PO: {po_number}", 
            'System'
        ))
        conn.commit()
        return new_wo_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_full_estimate(estimate_id: int):
    """Pulls all the details of an estimate including labor and materials."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Grab Parent Details + Customer/Site Names
        cur.execute("""
            SELECT e.*, c."CustomerName", s."SiteName"
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = %s
        """, (estimate_id,))
        parent = cur.fetchone()

        # 2. Grab Labor Lines
        cur.execute('SELECT "RoleDescription", "Hours", "Rate", "LineTotal" FROM "EstimateLabor" WHERE "EstimateID" = %s', (estimate_id,))
        labor = cur.fetchall()

        # 3. Grab Material Lines
        cur.execute('SELECT "Description", "Quantity", "UnitCost", "LineTotal" FROM "EstimateMaterial" WHERE "EstimateID" = %s', (estimate_id,))
        materials = cur.fetchall()

        return {"parent": parent, "labor": labor, "materials": materials}
    finally:
        conn.close()

def get_detailed_estimate_data(estimate_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                e.*, 
                s."SiteName", s."StreetNumber" AS "SiteNum", s."StreetName" AS "SiteStreet", s."City" AS "SiteCity",
                c."CustomerName", c."Address" AS "CustAddr", c."StreetName" AS "CustStreet", 
                c."CityName" AS "CustCity", c."PostalCode" AS "CustZip",
                c."Phone", c."Email" -- <--- ADDED THESE TWO!
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = %s
        """, (estimate_id,))
        parent = cur.fetchone()

        # 2. Grab Labor
        cur.execute('SELECT * FROM "EstimateLabor" WHERE "EstimateID" = %s', (estimate_id,))
        labor = cur.fetchall()

        # 3. Grab Materials
        cur.execute('SELECT * FROM "EstimateMaterial" WHERE "EstimateID" = %s', (estimate_id,))
        materials = cur.fetchall()

        return {"parent": parent, "labor": labor, "materials": materials}
    finally:
        conn.close()

def update_estimate_status(estimate_id: int, new_status: str):
    """Updates the status of an estimate to whatever we specify (e.g., 'Locked' or 'Submitted')."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE "Estimate" 
            SET "Status" = %s 
            WHERE "EstimateID" = %s
        """, (normalize_estimate_status(new_status), estimate_id))
        conn.commit()
        return True # Report back that it worked!
    except Exception as e:
        print(f"DB Error: {e}")
        return False
    finally:
        conn.close()

def get_draft_estimates():
    """Fetches a list of open drafts for the dropdown."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT e."EstimateID", s."SiteName" 
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            WHERE UPPER(TRIM(COALESCE(e."Status", 'DRAFT'))) = 'DRAFT'
            ORDER BY e."EstimateID" DESC
        """)
        return cur.fetchall()
    finally:
        conn.close()

def update_draft_estimate(est_id, site_id, desc, b_type, lab_markup, mat_markup, labor_lines, material_lines):
    """Updates the parent, replaces labor, and UPSERTS materials safely."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        material_lines = _hydrate_material_catalog_links(material_lines)
        # 1. Update the parent record
        cur.execute("""
            UPDATE "Estimate"
            SET "SiteID" = %s, "Description" = %s, "BillingType" = %s, 
                "LaborMarkUp" = %s, "MaterialMarkUp" = %s
            WHERE "EstimateID" = %s
              AND UPPER(TRIM(COALESCE("Status", 'DRAFT'))) = 'DRAFT'
        """, (site_id, desc, b_type, lab_markup, mat_markup, est_id))
        if cur.rowcount == 0:
            raise ValueError(f"Estimate #{est_id} is not in Draft status and cannot be updated.")

        # 2. Labor is safe to Wipe & Replace (There are no RFQs attached to labor!)
        cur.execute('DELETE FROM "EstimateLabor" WHERE "EstimateID" = %s', (est_id,))
        for l in labor_lines:
            cur.execute('INSERT INTO "EstimateLabor" ("EstimateID", "RoleDescription", "Hours", "Rate", "LineTotal") VALUES (%s, %s, %s, %s, %s)', 
                        (est_id, l[0], l[1], l[2], l[3]))

        # 3. THE SURGEON: Smart Material Sync
        
        # Collect all the "Golden Keys" the UI sent us
        # (Remember, est_mat_id is at index [6] from our save_data function)
        incoming_ids = [m[6] for m in material_lines if m[6] is not None]

        # A. Delete materials the user removed from the Tkinter tree
        if incoming_ids:
            # Delete anything that belongs to this estimate but IS NOT in the incoming list
            format_strings = ','.join(['%s'] * len(incoming_ids))
            cur.execute(f'''
                DELETE FROM "EstimateMaterial" 
                WHERE "EstimateID" = %s AND "EstimateMaterialID" NOT IN ({format_strings})
            ''', [est_id] + incoming_ids)
        else:
            # If the tree was completely empty, try to delete everything
            cur.execute('DELETE FROM "EstimateMaterial" WHERE "EstimateID" = %s', (est_id,))

        # B. Update existing and Insert new
        for m in material_lines:
            # m = [m_desc, qty, cost, total, item_id, part_no, est_mat_id]
            m_desc, qty, cost, total, item_id, part_no, est_mat_id = m
            
            if est_mat_id is not None:
                # It has a Golden Key! UPDATE IT!
                cur.execute('''
                    UPDATE "EstimateMaterial"
                    SET "Description" = %s, "Quantity" = %s, "UnitCost" = %s, "LineTotal" = %s, "ItemID" = %s, "PartNumber" = %s
                    WHERE "EstimateMaterialID" = %s
                ''', (m_desc, qty, cost, total, item_id, part_no, est_mat_id))
            else:
                # It's brand new! INSERT IT!
                cur.execute('''
                    INSERT INTO "EstimateMaterial" 
                    ("EstimateID", "Description", "Quantity", "UnitCost", "LineTotal", "ItemID", "PartNumber", "PriceSource") 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (est_id, m_desc, qty, cost, total, item_id, part_no, _resolve_price_source(item_id)))

        sync_estimate_pricing_from_sources(est_id, cur=cur)

        _log_estimate_sheet_save(
            cur,
            est_id,
            "Updated",
            labor_lines,
            material_lines
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
def insert_estimate_note(estimate_id: int, note_text: str):
    """Saves a new note to the ledger with an automatic timestamp."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        # We slice the text to 140 chars just in case the UI let it slip
        cur.execute(
            'INSERT INTO "Note" ("EstimateID", "NoteText", "Category") VALUES (%s, %s, %s)',
            (estimate_id, note_text[:140], 'Estimate Note')
        )
        conn.commit()
    finally:
        conn.close()

def get_estimate_notes(estimate_id: int):
    """Pulls the historical ledger of notes, formatted beautifully."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        # SQL trick: TO_CHAR formats the ugly database timestamp into a pretty string!
        cur.execute("""
            SELECT TO_CHAR("Timestamp", 'Mon DD - HH12:MI PM') AS "Date", "NoteText", "Category"
            FROM "Note"
            WHERE "EstimateID" = %s
            ORDER BY "Timestamp" DESC
        """, (estimate_id,))
        return cur.fetchall()
    finally:
        conn.close()

def recalculate_estimate_totals(estimate_id: int):
    """Rebuilds the estimate total from the stored estimate labor and material lines."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        _update_estimate_total_from_lines(cur, estimate_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Recalculation error: {e}")
    finally:
        conn.close()

def get_estimate_id_from_wo(wo_id: int):
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            SELECT e."EstimateID" FROM "WorkOrder" wo
            JOIN "Site" s ON wo."SiteID" = s."SiteID"
            JOIN "Estimate" e ON s."SiteID" = e."SiteID"
            WHERE wo."WorkOrderID" = %s
        ''', (wo_id,))
        res = cur.fetchone()
        return res[0] if res else None
    finally:
        conn.close()

def add_estimate_note(estimate_id: int, note_text: str):
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            'INSERT INTO "Note" ("EstimateID", "NoteText", "Category") VALUES (%s, %s, %s)',
            (estimate_id, note_text, 'Estimate Note')
        )
        conn.commit()
    finally:
        conn.close()

def on_estimate_select(self, event):
        selection = self.estimate_var.get()
        if not selection: return
        
        # Extract the ID: "2 - Install Car Charger" -> 2
        try:
            self.current_estimate_id = int(selection.split(" - ")[0])
        except (ValueError, IndexError):
            return

        # Clear the old rows first!
        for item in self.existing_tree.get_children():
            self.existing_tree.delete(item)
            
        try:
            from database.estimates import get_estimate_materials
            materials = get_estimate_materials(self.current_estimate_id)
            
            for mat in materials:
                # The .get() check handles both "Quantity" and "quantity"
                qty = mat.get('Quantity') or mat.get('quantity') or 0
                desc = mat.get('Description') or mat.get('description') or "Unknown Material"
                
                self.existing_tree.insert("", "end", values=(qty, desc))
                
        except Exception as e:
            messagebox.showerror("UI Error", f"Failed to display materials: {e}")
