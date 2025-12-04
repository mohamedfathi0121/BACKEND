# ===== FILE: db.py =====
import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

DATABASE_URL = os.getenv("DATABASE_URL")

# ---------------------------------------------------------
# Create a new DB connection
# ---------------------------------------------------------
def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in environment.")
    
    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )

# ---------------------------------------------------------
# fetchall(query, params)  → returns list of dictionaries
# ---------------------------------------------------------
def fetchall(query, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params or ())
        rows = cur.fetchall()
        return rows
    finally:
        cur.close()
        conn.close()

# ---------------------------------------------------------
# execute(query, params)  → executes INSERT / UPDATE / DELETE
# ---------------------------------------------------------
def execute(query, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params or ())
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ---------------------------------------------------------
# get_cursor(commit_mode)
# lets you run many queries inside one transaction:
#   with db.get_cursor(True) as (conn, cur):
#       cur.execute(...)
# ---------------------------------------------------------
@contextmanager
def get_cursor(commit_mode=True):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn, cur
        if commit_mode:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
