from neon_ai.database.connection import get_connection
from psycopg2.extras import RealDictCursor

from neon_ai.database.connection import get_connection

def get_open_workorder_choices():
    """Returns all open work orders for timesheet dropdowns."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            '''
            SELECT
                w."WorkOrderID",
                COALESCE(s."SiteName", 'Unknown Site') AS "SiteName",
                COALESCE(w."BillingType", '') AS "BillingType"
            FROM "WorkOrder" w
            LEFT JOIN "Site" s ON w."SiteID" = s."SiteID"
            WHERE UPPER(TRIM(COALESCE(w."JobStatus", ''))) = 'OPEN'
              AND COALESCE(w."IsClosed", FALSE) = FALSE
            ORDER BY w."WorkOrderID" DESC
            '''
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    choices = []
    for row in rows:
        wo_id = row.get("WorkOrderID")
        site_name = row.get("SiteName") or "Unknown Site"
        choices.append({
            "WorkOrderID": int(wo_id),
            "Label": f"{wo_id} - {site_name}",
        })
    return choices


def get_standard_tasks():
    """Fetches tasks using bulletproof dictionary lookups."""
    from neon_ai.database.connection import get_connection
    from psycopg2.extras import RealDictCursor

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Force exact casing with public."Task"
        cur.execute('SELECT "TaskName" FROM public."Task" ORDER BY "TaskName" ASC')
        rows = cur.fetchall()

        # Bulletproof extraction: checks for Capital and Lowercase
        tasks = []
        for row in rows:
            name = row.get('TaskName') or row.get('taskname')
            if name:
                tasks.append(name)
                
        print(f"DEBUG: Fetched {len(tasks)} standard tasks from DB.")
        return tasks
    except Exception as e:
        print(f"Task Fetch Error: {e}")
        return []
    finally:
        conn.close()

def add_standard_task(task_name):
    """Adds a new task type to the global list."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('INSERT INTO "Task" ("TaskName") VALUES (%s) ON CONFLICT DO NOTHING', (task_name,))
        conn.commit()
    finally:
        conn.close()

def get_employee_timesheet(emp_id, start_date, end_date):
    """Fetches hours for the 7-day matrix."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('''
            SELECT "TimeID", "DateWorked", "WorkOrderID", "TaskId",
                   "HoursWorked", "Status", "EntrySource"
            FROM "Time"
            WHERE "WorkerID" = %s AND "DateWorked" >= %s AND "DateWorked" <= %s
            ORDER BY "DateWorked" ASC
        ''', (emp_id, start_date, end_date))
        rows = cur.fetchall()
        for row in rows:
            row["TaskName"] = row.get("TaskId") or ""
        return rows
    finally:
        conn.close()

def add_manual_time(data):
    """Saves a manual entry to the 'Time' table using the provided payload."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # We removed the SELECT statement! 
        # We now trust the 'data' dictionary to contain the rate.
        
        # Using data.get('rate', 0.0) is a safe way to say: 
        # "Give me the rate, but if it's missing, use 0.0"
        rate = data.get('rate', 0.0)
        
        cur.execute('''
            INSERT INTO "Time" ("WorkerID", "WorkOrderID", "DateWorked", "HoursWorked", "TaskId", "HourlyRate", "Status", "EntrySource")
            VALUES (%s, %s, %s, %s, %s, %s, 'Pending', 'Manual')
        ''', (data['emp_id'], data['wo_id'], data['date'], data['hours'], data['task'], rate))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Insert Error: {e}")
        return False
    finally:
        conn.close()

def lock_week_timesheet(emp_id, start_date, end_date):
    """Approves all time for the selected week."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            UPDATE "Time" SET "Status" = 'Approved' 
            WHERE "WorkerID" = %s AND "DateWorked" >= %s AND "DateWorked" <= %s AND "Status" = 'Pending'
        ''', (emp_id, start_date, end_date))
        conn.commit()
    finally:
        conn.close()

def delete_standard_task(task_name):
    """Removes a task from the global standard list."""
    from neon_ai.database.connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('DELETE FROM public."Task" WHERE "TaskName" = %s', (task_name,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Delete Task Error: {e}")
        return False
    finally:
        conn.close()
