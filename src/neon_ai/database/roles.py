from neon_ai.database.connection import get_connection
from psycopg2.extras import RealDictCursor

def get_standard_roles():
    """Fetches all roles for the estimating dropdowns."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('SELECT "RoleName", "BaseRate", "BurdenPercent" FROM "StandardRole" ORDER BY "RoleName"')
        return cur.fetchall()
    except Exception as e:
        print(f"Role Fetch Error: {e}")
        return []
    finally:
        conn.close()

def add_standard_role(name, rate, burden):
    """Allows you to create a new estimating role from the UI."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO "StandardRole" ("RoleName", "BaseRate", "BurdenPercent") 
            VALUES (%s, %s, %s) 
            ON CONFLICT ("RoleName") DO UPDATE 
            SET "BaseRate" = EXCLUDED."BaseRate", "BurdenPercent" = EXCLUDED."BurdenPercent"
        ''', (name, rate, burden))
        conn.commit()
        return True
    except Exception as e:
        print(f"Role Insert Error: {e}")
        return False
    finally:
        conn.close()
