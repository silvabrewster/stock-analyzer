"""
Database abstraction layer
Supports both SQLite (local) and PostgreSQL (Supabase production)
"""

import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    """Returns appropriate DB connection based on environment."""
    if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
        return get_postgres()
    return get_sqlite()

def get_sqlite():
    conn = sqlite3.connect("stocks.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_postgres():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return PostgresWrapper(conn)

class PostgresWrapper:
    """Wraps psycopg2 to behave like sqlite3 for our app."""
    def __init__(self, conn):
        self._conn = conn
        self._cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        # Convert SQLite ? placeholders to PostgreSQL %s
        sql = sql.replace("?", "%s")
        # Convert SQLite INTEGER PRIMARY KEY AUTOINCREMENT to PostgreSQL SERIAL
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        # Convert INSERT OR REPLACE to INSERT ... ON CONFLICT
        if "INSERT OR REPLACE INTO watchlist" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO watchlist (ticker, target_price, notes)",
                "INSERT INTO watchlist (ticker, target_price, notes) "
                "ON CONFLICT (ticker) DO UPDATE SET target_price=EXCLUDED.target_price, notes=EXCLUDED.notes, added_date=watchlist.added_date --"
            )
        self._cursor.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [DictRow(dict(r)) for r in rows]

    def commit(self):
        self._conn.commit()

    def close(self):
        self._cursor.close()
        self._conn.close()

class DictRow(dict):
    """Allows both dict['key'] and row.key access."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

def init_db():
    """Creates all tables if they don't exist."""
    conn = get_db()
    tables = [
        """CREATE TABLE IF NOT EXISTS scans (
            id          SERIAL PRIMARY KEY,
            scan_date   TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            score       REAL,
            sources     TEXT,
            yahoo_sb    TEXT,
            zacks       TEXT,
            morningstar TEXT,
            insider     TEXT,
            eps_rev     TEXT,
            beats_sp    TEXT,
            price       REAL,
            upside_pct  REAL,
            beta        REAL,
            div_yield   REAL,
            week52_pos  REAL,
            short_pct   REAL,
            vol_spike   TEXT,
            streak      INTEGER,
            is_new      TEXT,
            sector      TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS market_conditions (
            id        SERIAL PRIMARY KEY,
            scan_date TEXT NOT NULL,
            sp500     REAL,
            sp500_chg REAL,
            vix       REAL,
            tny       REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS penny_scans (
            id           SERIAL PRIMARY KEY,
            scan_date    TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            name         TEXT,
            tier         TEXT,
            score        REAL,
            price        REAL,
            mkt_cap      TEXT,
            vol_spike    TEXT,
            vol_ratio    TEXT,
            breakout     TEXT,
            week52_range TEXT,
            beats_mkt    TEXT,
            mo_return    TEXT,
            insider_buy  TEXT,
            short_squeeze TEXT,
            short_float  TEXT,
            signals      TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS watchlist (
            id           SERIAL PRIMARY KEY,
            ticker       TEXT NOT NULL UNIQUE,
            target_price REAL,
            notes        TEXT,
            added_date   TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    for sql in tables:
        conn.execute(sql)
    conn.commit()
    conn.close()
