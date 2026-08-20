"""
Database abstraction layer
Supports both SQLite (local) and PostgreSQL (Supabase production)
"""

import os
import sqlite3
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    url = os.environ.get("DATABASE_URL", "")
    # Providers hand out both "postgresql://" and "postgres://" — psycopg2 accepts
    # either, so accept both here. Rejecting one silently falls back to SQLite on
    # ephemeral disk, which loses every scan on the next deploy.
    if url and url.startswith(("postgresql", "postgres://")):
        return get_postgres(url)
    return get_sqlite()

def get_sqlite():
    conn = sqlite3.connect("stocks.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_postgres(url=None):
    conn = psycopg2.connect(url or os.environ.get("DATABASE_URL"), options="-c search_path=public")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET search_path TO public")
    cur.close()
    conn.autocommit = False
    return PostgresWrapper(conn)

# SQLite's "INSERT OR REPLACE" has no direct Postgres equivalent; each table
# needs its own ON CONFLICT clause, appended AFTER the VALUES list.
_UPSERT_CLAUSES = {
    "watchlist": ("ON CONFLICT (ticker) DO UPDATE SET "
                  "target_price=EXCLUDED.target_price, notes=EXCLUDED.notes"),
    "portfolio": ("ON CONFLICT (ticker) DO UPDATE SET "
                  "shares=EXCLUDED.shares, buy_price=EXCLUDED.buy_price, "
                  "notes=EXCLUDED.notes"),
    "push_subscriptions": ("ON CONFLICT (endpoint) DO UPDATE SET "
                           "p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth, "
                           "updated_at=CURRENT_TIMESTAMP"),
}


class PostgresWrapper:
    def __init__(self, conn):
        self._conn   = conn
        self._cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        if "INSERT OR REPLACE INTO" in sql:
            for table, clause in _UPSERT_CLAUSES.items():
                # Match on the table name only, so the caller's column spacing
                # does not matter, and append the clause after VALUES.
                if f"INSERT OR REPLACE INTO {table}" in sql:
                    sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO", 1)
                    sql = sql.rstrip().rstrip(";") + " " + clause
                    break
        try:
            self._cursor.execute(sql, params if params else None)
        except Exception:
            # Postgres aborts the whole transaction on any error: without this
            # rollback every later statement on this connection fails too, so a
            # single swallowed error (e.g. an idempotent migration) would
            # silently break everything after it.
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return DictRow(dict(row)) if row else None

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [DictRow(dict(r)) for r in rows]

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._cursor.close()
            self._conn.close()
        except Exception:
            pass

class DictRow(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)
