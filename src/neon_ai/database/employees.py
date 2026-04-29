from neon_ai.database.connection import get_connection
from psycopg2.extras import RealDictCursor


def ensure_employee_schema():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('ALTER TABLE "Employee" ADD COLUMN IF NOT EXISTS "EmployeeClass" text')
        conn.commit()
    finally:
        conn.close()


def get_all_employees():
    """Fetches all active employees for the pipeline."""
    ensure_employee_schema()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('''
            SELECT "EmployeeID", "EmployeeName", "EmployeeRate", "EmployeeBurden",
                   "EmployeeAddress", "EmployeeCity", "EmployeePhone", "EmployeeEmail",
                   "EmployeeClass"
            FROM "Employee"
            WHERE "IsActive" = true
            ORDER BY "EmployeeName" ASC
        ''')
        return cur.fetchall()
    finally:
        conn.close()

def save_employee(data: dict):
    """Inserts a new employee or updates an existing one."""
    ensure_employee_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if data.get('emp_id'):
            # UPDATE existing
            cur.execute('''
                UPDATE "Employee" SET 
                    "EmployeeName" = %s, "EmployeeRate" = %s, "EmployeeBurden" = %s,
                    "EmployeeAddress" = %s, "EmployeeCity" = %s, 
                    "EmployeePhone" = %s, "EmployeeEmail" = %s, "EmployeeClass" = %s
                WHERE "EmployeeID" = %s
            ''', (data['name'], data['rate'], data['burden'], 
                  data['address'], data['city'], data['phone'], data['email'], data.get('employee_class'), data['emp_id']))
        else:
            # INSERT new
            cur.execute('''
                INSERT INTO "Employee" 
                ("EmployeeName", "EmployeeRate", "EmployeeBurden", "EmployeeAddress", "EmployeeCity", "EmployeePhone", "EmployeeEmail", "EmployeeClass")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (data['name'], data['rate'], data['burden'], 
                  data['address'], data['city'], data['phone'], data['email'], data.get('employee_class')))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Employee Save Error: {e}")
        return False
    finally:
        conn.close()
