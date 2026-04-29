from neon_ai.database.connection import get_connection

def get_dashboard_metrics():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            w."WorkOrderID",
            COALESCE(c."CustomerName", 'Unknown') AS "CustomerName",
            COALESCE(s."SiteName", 'Unknown Site') AS "SiteName",
            COALESCE(SUM(t."HoursWorked"), 0) AS "TotalHours",
            COALESCE(SUM(t."HoursWorked" * e."EmployeeRate"), 0) AS "LaborCost",
            COALESCE((SELECT SUM("PurchaseOrderTotal") FROM "PurchaseOrder" WHERE "WorkOrderID" = w."WorkOrderID"), 0) AS "MaterialCost",
            
            -- THE FIX: We link through the SiteID since WorkOrder doesn't explicitly store the EstimateID!
            (
                COALESCE((SELECT SUM(el."LineTotal") FROM "EstimateLabor" el JOIN "Estimate" est ON el."EstimateID" = est."EstimateID" WHERE est."SiteID" = w."SiteID"), 0) + 
                COALESCE((SELECT SUM(em."LineTotal") FROM "EstimateMaterial" em JOIN "Estimate" est ON em."EstimateID" = est."EstimateID" WHERE est."SiteID" = w."SiteID"), 0)
            ) AS "WorkOrderValue"
            
        FROM "WorkOrder" w
        LEFT JOIN "Site" s ON w."SiteID" = s."SiteID"
        LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
        LEFT JOIN "Time" t ON w."WorkOrderID" = t."WorkOrderID"
        LEFT JOIN "Employee" e ON t."WorkerID" = e."EmployeeID"
        WHERE UPPER(TRIM(w."JobStatus")) = 'OPEN'
        GROUP BY w."WorkOrderID", c."CustomerName", s."SiteName", w."SiteID"
        ORDER BY w."WorkOrderID" DESC
    """)
    rows = cur.fetchall()
    conn.close()

    # --- THE JSON FIX ---
    sanitized_rows = []
    for row in rows:
        clean_row = dict(row) 
        clean_row["TotalHours"] = float(clean_row["TotalHours"])
        clean_row["LaborCost"] = float(clean_row["LaborCost"])
        clean_row["MaterialCost"] = float(clean_row["MaterialCost"])
        # Add the new Value float conversion!
        clean_row["WorkOrderValue"] = float(clean_row["WorkOrderValue"])
        sanitized_rows.append(clean_row)

    return sanitized_rows
