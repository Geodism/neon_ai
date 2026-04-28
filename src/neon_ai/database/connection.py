from __future__ import annotations

import psycopg2
from psycopg2.extras import RealDictCursor

from neon_ai.config import get_required_env, load_neon_env


def get_connection():
    load_neon_env()
    db_url = get_required_env("DB_URL")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)
