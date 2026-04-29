from neon_ai.database.connection import get_connection

def get_employees():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM "Employee" ORDER BY "EmployeeName"')
    rows = cur.fetchall()
    conn.close()
    return rows

# 2. UPGRADED FOR TIBER CHESS TIMER
def insert_time(worker_id, workorder_id, date_worked, hours_worked, task_id=None, start_time=None, end_time=None, hourly_rate=0.0):
    conn = get_connection()
    cur = conn.cursor()
    
    date_str = str(date_worked)
    pg_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}" if len(date_str) == 8 else date_worked

    # 2. Add "HourlyRate" to the INSERT statement and an extra %s to the VALUES
    cur.execute(
        """
        INSERT INTO "Time" ("WorkerID", "WorkOrderID", "DateWorked", "TaskId","StartTime", "EndTime", "HoursWorked", "HourlyRate")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        # 3. Add hourly_rate to the tuple so the plugs match the receptacles perfectly!
        (worker_id, workorder_id, pg_date, task_id, start_time, end_time, hours_worked, hourly_rate)
    )
    conn.commit()
    conn.close()

def get_tasks():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute('SELECT "TaskName" FROM "Task" WHERE "TaskName" IS NOT NULL')
        rows = cur.fetchall()
        conn.close()
        return [row["TaskName"] for row in rows]
    except Exception:
        return [
            "Rough-inBillable", "FinishingBillable", 
            "Project ManagementNonBillable", "PhoneNonBillable", 
            "EstimatingNonBillable", "LightingBillable", "DistributionBillable"
        ]

def get_todays_task_time(worker_id, task_name, date_worked):
    conn = get_connection()
    cur = conn.cursor()
    
    date_str = str(date_worked)
    pg_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}" if len(date_str) == 8 else date_worked

    cur.execute("""
        SELECT COALESCE(SUM("HoursWorked"), 0) AS "TotalHours" 
        FROM "Time" 
        WHERE "WorkerID" = %s AND "TaskId" = %s AND "DateWorked" = %s
    """, (worker_id, task_name, pg_date))
    
    row = cur.fetchone()
    conn.close()
    return (row["TotalHours"] if row else 0) * 3600
