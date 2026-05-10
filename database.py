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
    if url and url.startswith("postgresql"):
        return get_postgres(url)
    return get_sqlite()

def get_sqlite():
    conn = sqlite3.connect("stocks.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_postgres(url=None):
    conn = psycopg2.connect(url or os.environ.get("DATABASE_URL"))
    conn.autocommit = False
    return PostgresWrapper(conn)

class PostgresWrapper:
    def __init__(self, conn):
        self._conn   = conn
        self._cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        if "INSERT OR REPLACE INTO watchlist" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO watchlist (ticker, target_price, notes)",
                "INSERT INTO watchlist (ticker, target_price, notes) "
                "ON CONFLICT (ticker) DO UPDATE SET "
                "target_price=EXCLUDED.target_price, notes=EXCLUDED.notes"
            )
        if "INSERT OR REPLACE INTO portfolio" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO portfolio (ticker, shares, buy_price, notes)",
                "INSERT INTO portfolio (ticker, shares, buy_price, notes) "
                "ON CONFLICT (ticker) DO UPDATE SET "
                "shares=EXCLUDED.shares, buy_price=EXCLUDED.buy_price, notes=EXCLUDED.notes"
            )
        if "INSERT OR REPLACE INTO push_subscriptions" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO push_subscriptions (endpoint, p256dh, auth)",
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth) "
                "ON CONFLICT (endpoint) DO UPDATE SET "
                "p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth, updated_at=CURRENT_TIMESTAMP"
            )
        self._cursor.execute(sql, params if params else None)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return DictRow(dict(row)) if row else None

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [DictRow(dict(r)) for r in rows]

    def commit(self):
        self._conn.commit()

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
