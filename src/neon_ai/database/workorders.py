import time

from psycopg2.extras import RealDictCursor

from neon_ai.database.connection import get_connection


NON_OPERATIONAL_WORK_ORDER_STATUSES = {"DRAFT", "PENDINGSTART"}


def normalize_work_order_status(status: str | None) -> str:
    text = str(status or "").strip()
    if not text:
        return ""
    compact = text.replace("_", "").replace(" ", "").upper()
    if compact == "PENDINGSTART":
        return "PendingStart"
    if compact == "DRAFT":
        return "Draft"
    if compact == "OPEN":
        return "Open"
    if compact == "CLOSED":
        return "Closed"
    return text


def is_non_operational_work_order_status(status: str | None) -> bool:
    normalized = normalize_work_order_status(status)
    return normalized.replace(" ", "").replace("_", "").upper() in NON_OPERATIONAL_WORK_ORDER_STATUSES


def _perf_log(area: str, name: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}")


def check_closure_requirements(wo_id: int):
    """
    Returns a dictionary indicating if the WO meets the two hard requirements for closure.
    """
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Check Customer Payments
        # We check if (Sum of Invoice Amounts) <= (Sum of Payments)
        # Note: This assumes you have an 'InvDatePaid' or a separate 'Payment' table.
        cur.execute("""
            SELECT 
                COALESCE(SUM("CustomerInvoiceAmount"::numeric), 0) AS "TotalBilled",
                COALESCE(SUM(CASE WHEN "InvDatePaid" IS NOT NULL THEN "CustomerInvoiceAmount"::numeric ELSE 0 END), 0) AS "TotalPaid"
            FROM "Invoice"
            WHERE "WorkOrderID" = %s
        """, (wo_id,))
        billing = cur.fetchone()
        
        # 2. Check Vendor Invoices
        # We check if there are any Purchase Orders for this WO that do NOT have a matching Vendor Invoice.
        # This assumes your 'VendorInvoice' table has a 'PurchaseOrderID' link.
        cur.execute("""
            SELECT COUNT(*) AS "UnlinkedPOs"
            FROM "PurchaseOrder" po
            LEFT JOIN "VendorInvoice" vi ON po."PurchaseOrderID" = vi."PurchaseOrderID"
            WHERE po."WorkOrderID" = %s AND vi."VendorInvoiceID" IS NULL
        """, (wo_id,))
        vendor_check = cur.fetchone()

        # The Verdict
        customer_clear = (billing['TotalBilled'] > 0 and billing['TotalBilled'] <= billing['TotalPaid'])
        vendor_clear = (vendor_check['UnlinkedPOs'] == 0)

        return {
            "is_closable": customer_clear and vendor_clear,
            "billing_status": f"Paid: ${billing['TotalPaid']:,.2f} / Billed: ${billing['TotalBilled']:,.2f}",
            "vendor_status": f"Pending Vendor Bills: {vendor_check['UnlinkedPOs']}",
            "customer_clear": customer_clear,
            "vendor_clear": vendor_clear
        }
    finally:
        conn.close()

def get_wo_export_data(wo_id: int):
    """
    Pulls comprehensive details including the new Site address columns.
    Updated to pull StreetNumber and StreetName from the Site table (s).
    """
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor() 
    try:
        cur.execute("""
            SELECT 
                w."WorkOrderID", 
                COALESCE(w."CreatedDate", 'Unknown') AS "Date", 
                w."Description" AS "WorkOrderScope", 
                COALESCE(w."CustomerPO", 'N/A') AS "CustomerPO",
                c."CustomerName", 
                
                -- THE SHAKESPEARE FIX: 
                -- We now pull the specific address parts from the Site table
                s."StreetNumber", 
                s."StreetName", 
                s."SiteName",
                s."City",
                
                COALESCE(c."Phone", 'No Phone') AS "Phone",
                e."EstimateID",
                
                (SELECT string_agg("RoleDescription" || ' (' || "Hours" || ' hrs)', ', ') 
                 FROM "EstimateLabor" WHERE "EstimateID" = e."EstimateID") AS "EstLaborSummary",
                 
                (SELECT string_agg("Description" || ' (Qty: ' || "Quantity" || ')', ', ') 
                 FROM "EstimateMaterial" WHERE "EstimateID" = e."EstimateID") AS "EstMaterialSummary"

            FROM "WorkOrder" w
            JOIN "Site" s ON w."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            LEFT JOIN "Estimate" e ON e."SiteID" = w."SiteID" AND e."IsConverted" = TRUE
            WHERE w."WorkOrderID" = %s
            LIMIT 1
        """, (wo_id,))
        return cur.fetchone()
    finally:
        conn.close()

def get_dashboard_work_orders():
    """Pulls the active Work Orders with live financial telemetry."""
    started_at = time.perf_counter()
    try:
        from neon_ai.database.connection import get_connection
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT 
                    wo."WorkOrderID", 
                    COALESCE(wo."CreatedDate", 'N/A') AS "CreatedDate",
                    s."SiteName",
                    COALESCE(
                        NULLIF(TRIM(wo."JobStatus"), ''),
                        CASE
                            WHEN COALESCE(wo."IsClosed", FALSE) THEN 'Closed'
                            ELSE 'Open'
                        END
                    ) as "Status",
                    
                    -- The Baseline (Estimated Cost)
                    (
                        COALESCE((SELECT SUM("LineTotal") FROM "EstimateLabor" el JOIN "Estimate" e ON el."EstimateID" = e."EstimateID" WHERE e."SiteID" = wo."SiteID" AND e."IsConverted" = TRUE), 0) +
                        COALESCE((SELECT SUM("LineTotal") FROM "EstimateMaterial" em JOIN "Estimate" e ON em."EstimateID" = e."EstimateID" WHERE e."SiteID" = wo."SiteID" AND e."IsConverted" = TRUE), 0)
                    ) AS "EstCost",
                    
                    -- The Actual Burn (Labor + Materials)
                    -- FIX: Added ::numeric to EmployeeRate just in case Supabase thinks it is text!
                    (
                        COALESCE((SELECT SUM(t."HoursWorked" * emp."EmployeeRate"::numeric) FROM "Time" t JOIN "Employee" emp ON t."WorkerID" = emp."EmployeeID" WHERE t."WorkOrderID" = wo."WorkOrderID"), 0) +
                        COALESCE((SELECT SUM("PurchaseOrderTotal") FROM "PurchaseOrder" WHERE "WorkOrderID" = wo."WorkOrderID"), 0)
                    ) AS "ActCost"
                    
                FROM "WorkOrder" wo
                JOIN "Site" s ON wo."SiteID" = s."SiteID"
                ORDER BY wo."WorkOrderID" DESC
            """)
            return cur.fetchall()
        finally:
            conn.close()
    finally:
        _perf_log("db", "workorders.get_dashboard_work_orders", started_at)


def get_work_order_telemetry(wo_id: int):
    """Fetches telemetry, now pulling notes from the parent Estimate."""
    conn = get_connection()
    cur = conn.cursor()
    data = {}
    try:
        # 1. Get Base Info, Compliance, AND the OB1 Link
        cur.execute("""
            SELECT 
                "WorkOrderID", 
                "CreatedDate", 
                "Description", 
                "IsClosed", 
                "CustomerPO", 
                "AcceptanceDocPath",
                "SourceEstimateID" 
            FROM "WorkOrder" 
            WHERE "WorkOrderID" = %s
        """, (wo_id,))
        data['parent'] = cur.fetchone()

        # 2. Get Labor Burn Ledger
        # FIX: Using your exact EmployeeName and EmployeeRate columns
        cur.execute("""
            SELECT 
                t."DateWorked", 
                emp."EmployeeName" AS "Role", 
                t."TaskId" AS "Task", 
                t."HoursWorked", 
                (t."HoursWorked" * emp."EmployeeRate"::numeric) AS "Cost"
            FROM "Time" t
            JOIN "Employee" emp ON t."WorkerID" = emp."EmployeeID"
            WHERE t."WorkOrderID" = %s
            ORDER BY t."DateWorked" DESC
        """, (wo_id,))
        data['labor'] = cur.fetchall()

        # 3. Get Material Burn Ledger
        # Pulled from your first message this morning!
        cur.execute("""
            SELECT 
                po."PurchaseOrderID" AS "PONum", 
                po."Date", 
                v."VendorName", 
                po."PurchaseOrderTotal" AS "Cost"
            FROM "PurchaseOrder" po
            JOIN "Vendor" v ON po."VendorID" = v."VendorID"
            WHERE po."WorkOrderID" = %s
            ORDER BY po."Date" DESC
        """, (wo_id,))
        data['materials'] = cur.fetchall()

        # 4. Get Operations Notes Ledger
        # 4. Get Unified Notes via the SourceEstimateID
        # This pulls every note from the day the estimate was created to right now.
        cur.execute("""
            SELECT TO_CHAR("Timestamp", 'Mon DD - HH12:MI PM') AS "Date", "NoteText", "Category"
            FROM "Note"
            WHERE "EstimateID" = %s
            ORDER BY "Timestamp" DESC
        """, (data['parent']['SourceEstimateID'],))
        data['notes'] = cur.fetchall()

        # 5. Get Billing Ledger (Invoices)
        cur.execute("""
            SELECT 
                "CustomerInvoiceId" AS "InvNum", 
                "CustomerInvoiceDate" AS "Date", 
                "CustomerInvoiceAmount"::numeric AS "Amount"
            FROM "Invoice"
            WHERE "WorkOrderID" = %s
            ORDER BY "CustomerInvoiceDate" DESC
        """, (wo_id,))
        data['invoices'] = cur.fetchall()

        return data
    finally:
        conn.close()

def get_next_workorder_id():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT COALESCE(MAX("WorkOrderID"), 0) + 1 AS "NextID" FROM "WorkOrder"')
    row = cur.fetchone()
    conn.close()
    return row["NextID"]

def insert_workorder(site_id, created_date, job_status, description, billing_type): 
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO "WorkOrder" ("SiteID", "CreatedDate", "JobStatus", "Description", "BillingType")
        VALUES (%s, %s, %s, %s, %s)
    """, (site_id, created_date, job_status, description, billing_type))
    conn.commit()
    conn.close()


def create_work_order_draft_safe(
    *,
    estimate_id: int,
    desired_status: str = "Draft",
    created_by: str = "automation_apply",
    source_tag: str = "work_order_draft_wrapper",
):
    normalized_status = normalize_work_order_status(desired_status)
    if normalized_status not in {"Draft", "PendingStart"}:
        raise ValueError("Safe WorkOrder draft creation requires JobStatus = Draft or PendingStart.")

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                e."EstimateID",
                e."SiteID",
                COALESCE(e."Description", '') AS "EstimateDescription",
                COALESCE(e."BillingType", '') AS "BillingType",
                COALESCE(e."Status", '') AS "EstimateStatus",
                COALESCE(e."IsConverted", FALSE) AS "IsConverted",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName"
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = %s
            ''',
            (int(estimate_id),),
        )
        estimate_row = cur.fetchone()
        if not estimate_row:
            raise ValueError(f"Estimate #{estimate_id} could not be loaded for safe WorkOrder draft creation.")
        if bool(estimate_row.get("IsConverted")):
            raise ValueError(f"Estimate #{estimate_id} is already converted; safe WorkOrder draft wrapper will not create another WorkOrder.")
        if not estimate_row.get("SiteID"):
            raise ValueError("Safe WorkOrder draft creation requires a resolved Site.")
        if not str(estimate_row.get("CustomerName") or "").strip():
            raise ValueError("Safe WorkOrder draft creation requires a resolved Customer context.")

        normalized_description = str(estimate_row.get("EstimateDescription") or "").strip() or f"Estimate #{int(estimate_id)}"
        normalized_billing_type = str(estimate_row.get("BillingType") or "").strip() or "Project"
        warnings: list[str] = []

        cur.execute(
            '''
            SELECT
                "WorkOrderID",
                "SiteID",
                COALESCE("Description", '') AS "Description",
                COALESCE("BillingType", '') AS "BillingType",
                COALESCE("JobStatus", '') AS "JobStatus",
                COALESCE("IsApproved", FALSE) AS "IsApproved",
                COALESCE("IsClosed", FALSE) AS "IsClosed",
                "SourceEstimateID"
            FROM "WorkOrder"
            WHERE "SourceEstimateID" = %s
            ORDER BY "WorkOrderID" DESC
            ''',
            (int(estimate_id),),
        )
        existing_rows = cur.fetchall() or []

        reusable_rows = [
            row
            for row in existing_rows
            if not bool(row.get("IsClosed"))
            and not bool(row.get("IsApproved"))
            and is_non_operational_work_order_status(row.get("JobStatus"))
        ]
        blocking_rows = [row for row in existing_rows if row not in reusable_rows]

        if blocking_rows:
            blocking_ids = ", ".join(str(int(row["WorkOrderID"])) for row in blocking_rows if row.get("WorkOrderID"))
            raise ValueError(
                f"Estimate #{estimate_id} already has operational or non-reusable WorkOrder context ({blocking_ids}); "
                "safe WorkOrder draft wrapper will not create another WorkOrder."
            )
        if len(reusable_rows) > 1:
            raise ValueError(
                f"Estimate #{estimate_id} already has multiple draft/pending WorkOrders; safe WorkOrder draft wrapper cannot pick one deterministically."
            )

        draft_reused = False
        if reusable_rows:
            work_order_row = reusable_rows[0]
            draft_reused = True
            warnings.append(
                f"Reused existing {normalize_work_order_status(work_order_row.get('JobStatus')) or 'Draft'} WorkOrder "
                f"#{int(work_order_row['WorkOrderID'])} for Estimate #{estimate_id}."
            )
        else:
            cur.execute(
                '''
                INSERT INTO "WorkOrder"
                (
                    "SiteID",
                    "CreatedDate",
                    "JobStatus",
                    "Description",
                    "BillingType",
                    "IsApproved",
                    "IsClosed",
                    "SourceEstimateID"
                )
                VALUES (%s, CURRENT_DATE, %s, %s, %s, FALSE, FALSE, %s)
                RETURNING *
                ''',
                (
                    int(estimate_row["SiteID"]),
                    normalized_status,
                    normalized_description,
                    normalized_billing_type,
                    int(estimate_id),
                ),
            )
            work_order_row = cur.fetchone()

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "work_order": work_order_row,
        "draft_reused": draft_reused,
        "warnings": warnings,
        "source_estimate_id": int(estimate_id),
        "created_by": str(created_by or "automation_apply").strip() or "automation_apply",
        "source_tag": str(source_tag or "work_order_draft_wrapper").strip() or "work_order_draft_wrapper",
    }

def get_workorders():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM "WorkOrder" ORDER BY "WorkOrderID" DESC')
    rows = cur.fetchall()
    conn.close()
    return rows

def get_workorder_summary():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            w."WorkOrderID",
            c."CustomerName",
            s."SiteName",
            w."CreatedDate",
            w."JobStatus",
            w."Description"
        FROM "WorkOrder" w
        JOIN "Site" s ON w."SiteID" = s."SiteID"
        JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
        ORDER BY w."WorkOrderID" DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def get_all_work_orders():
    """Fetches all work orders with their current approval and closed statuses."""
    conn = get_connection()
    cur = conn.cursor()
    # Notice the AS "IsClosed" added to the very end of line 8!
    cur.execute(
        """
        SELECT w."WorkOrderID", c."CustomerName", s."SiteName", w."Description", w."IsApproved", COALESCE(w."IsClosed", FALSE) AS "IsClosed"
        FROM "WorkOrder" w
        JOIN "Site" s ON w."SiteID" = s."SiteID"
        JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
        ORDER BY w."WorkOrderID" DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def close_work_order(work_order_id: int):
    """Flips the IsClosed switch to True. This is a one-way street."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('UPDATE "WorkOrder" SET "IsClosed" = TRUE WHERE "WorkOrderID" = %s', (work_order_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_open_workorders(billing_mode: str):
    """
    Returns a list of all currently open work orders. USE THIS FIRST to see a list of available jobs. 
    Returns IDs and Site Names for all OPEN work orders.
    Args:
        billing_mode (str): Use 'Project' for job sites or 'Ops' for office tasks.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT w."WorkOrderID", COALESCE(s."SiteName", 'Unknown Site') AS "SiteName" 
        FROM "WorkOrder" w
        LEFT JOIN "Site" s ON w."SiteID" = s."SiteID"
        WHERE UPPER(TRIM(w."JobStatus")) = 'OPEN' 
        AND COALESCE(w."BillingType", 'Project') = %s
        ORDER BY w."WorkOrderID" DESC
    """, (billing_mode,))
    rows = cur.fetchall()
    conn.close()

    # THE CLEANER: Convert EVERYTHING to a standard string or float
    # This guarantees the AI can read it without a JSON error.
    output = []
    for row in rows:
        # We turn the database object into a standard Python string for the AI
        # This is the 'safest' way to pass lists to a LLM.
        output.append(f"ID: {row['WorkOrderID']} | Site: {row['SiteName']}")
    
    return "\n".join(output) # Return one clean string instead of a complex list

def get_open_workorders_dict(billing_mode: str):
    """
    Returns a list of dictionaries for the UI timer.
    The 'billing_mode' argument is required to filter between Project and Ops.
    """
    conn = get_connection()
    cur = conn.cursor()
    # We use the %s placeholder to safely inject the billing_mode into the SQL
    cur.execute("""
        SELECT w."WorkOrderID", COALESCE(s."SiteName", 'Unknown Site') AS "SiteName" 
        FROM "WorkOrder" w
        LEFT JOIN "Site" s ON w."SiteID" = s."SiteID"
        WHERE UPPER(TRIM(w."JobStatus")) = 'OPEN' 
        AND COALESCE(w."BillingType", 'Project') = %s
        ORDER BY w."WorkOrderID" DESC
    """, (billing_mode,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_workorder_financials(workorder_id: int): 
    """
    Retrieves a financial summary (Materials, Labor, Invoiced) for a specific job ID.
    
    Args:
        workorder_id (int): The numeric ID of the Work Order (e.g., 1, 42, 105).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            (SELECT COALESCE(SUM("PurchaseOrderTotal"), 0) FROM "PurchaseOrder" WHERE "WorkOrderID" = %s) AS "MaterialCost",
            (SELECT COALESCE(SUM(t."HoursWorked" * e."EmployeeRate"), 0) 
             FROM "Time" t 
             JOIN "Employee" e ON t."WorkerID" = e."EmployeeID" 
             WHERE t."WorkOrderID" = %s) AS "LaborCost",
            (SELECT COALESCE(SUM("CustomerInvoiceAmount"), 0) FROM "Invoice" WHERE "WorkOrderID" = %s) AS "TotalInvoiced"
    """, (workorder_id, workorder_id, workorder_id))
    
    row = cur.fetchone()
    conn.close()
    
    if row:
        material_cost = float(row["MaterialCost"])
        labor_cost = float(row["LaborCost"])
        total_invoiced = float(row["TotalInvoiced"])
        total_cost = material_cost + labor_cost
        
        return {
            "MaterialCost": material_cost,
            "LaborCost": labor_cost,
            "TotalCost": total_cost,
            "TotalInvoiced": total_invoiced,
            "Difference": total_invoiced - total_cost
        }
    return None

def approve_work_order(work_order_id: int):
    """
    Flips the approval switch to True.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE "WorkOrder"
            SET "IsApproved" = TRUE
            WHERE "WorkOrderID" = %s
            """,
            (work_order_id,)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
