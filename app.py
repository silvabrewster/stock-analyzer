"""
Stock Convergence Web App
==========================
Flask web dashboard for the Stock Convergence Analyzer.
Shows daily results, history, and market conditions.

Run locally:  python app.py
Production:   gunicorn app:app
"""

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from functools import wraps
import sqlite3
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "stockconvergence2026")

# ── config ────────────────────────────────────────────────────────────────────

APP_PASSWORD = os.environ.get("APP_PASSWORD", "convergence2026")
DB_PATH      = os.environ.get("DB_PATH", "stocks.db")

# ── database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_conditions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            sp500     REAL,
            sp500_chg REAL,
            vix       REAL,
            tny       REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_scan_to_db(df, market: dict):
    """Called by scheduler to persist results."""
    conn    = get_db()
    today   = datetime.now().strftime("%Y-%m-%d")

    # Clear today's data (avoid dupes on re-run)
    conn.execute("DELETE FROM scans WHERE scan_date = ?", (today,))
    conn.execute("DELETE FROM market_conditions WHERE scan_date = ?", (today,))

    # Save market conditions
    sp  = market.get("sp500", {})
    vix = market.get("vix", {})
    tny = market.get("tny", {})
    conn.execute("""
        INSERT INTO market_conditions (scan_date, sp500, sp500_chg, vix, tny)
        VALUES (?,?,?,?,?)
    """, (today,
          sp.get("price"), sp.get("chg"),
          vix.get("price"), tny.get("price")))

    # Save top 20 stocks
    for _, row in df.head(20).iterrows():
        def safe(key, default=None):
            v = row.get(key, default)
            if v in ("n/a", "–", "", None):
                return default
            try:
                return float(str(v).replace("%","").replace("$","").strip())
            except Exception:
                return str(v) if isinstance(v, str) else default

        streak_raw = str(row.get("Streak", "0")).replace("🔥","").replace("d","").strip()
        try:
            streak_int = int(streak_raw)
        except Exception:
            streak_int = 0

        conn.execute("""
            INSERT INTO scans
            (scan_date, ticker, score, sources, yahoo_sb, zacks, morningstar,
             insider, eps_rev, beats_sp, price, upside_pct, beta, div_yield,
             week52_pos, short_pct, vol_spike, streak, is_new, sector)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            today,
            row.get("Ticker"),
            safe("Consensus Score"),
            row.get("Sources Agree"),
            row.get("Yahoo SB"),
            row.get("Zacks #1"),
            row.get("Morningstar ★★★"),
            row.get("Insider Buy"),
            row.get("EPS Rev ↑"),
            row.get("Beats S&P"),
            safe("Price"),
            safe("Upside %"),
            safe("Beta"),
            safe("Div Yield"),
            safe("52w Position"),
            safe("Short %"),
            "1" if row.get("Vol Spike") else "0",
            streak_int,
            row.get("New?", ""),
            row.get("Sector", "Unknown"),
        ))

    conn.commit()
    conn.close()

# ── auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Wrong password. Try again."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    conn      = get_db()
    today     = datetime.now().strftime("%Y-%m-%d")

    # Get latest scan date
    row = conn.execute(
        "SELECT MAX(scan_date) as latest FROM scans"
    ).fetchone()
    latest_date = row["latest"] if row and row["latest"] else today

    # Top stocks for latest date
    stocks = conn.execute("""
        SELECT * FROM scans
        WHERE scan_date = ?
        ORDER BY score DESC
        LIMIT 20
    """, (latest_date,)).fetchall()

    # Market conditions
    market = conn.execute("""
        SELECT * FROM market_conditions
        WHERE scan_date = ?
    """, (latest_date,)).fetchone()

    # Scan dates for history dropdown
    dates = conn.execute("""
        SELECT DISTINCT scan_date FROM scans
        ORDER BY scan_date DESC LIMIT 30
    """).fetchall()

    # Streak leaders (stocks with highest streak)
    streaks = conn.execute("""
        SELECT ticker, MAX(streak) as max_streak, MAX(score) as score
        FROM scans WHERE streak > 1
        GROUP BY ticker ORDER BY max_streak DESC LIMIT 5
    """).fetchall()

    conn.close()
    return render_template("dashboard.html",
        stocks=stocks, market=market,
        latest_date=latest_date, dates=dates,
        streaks=streaks)

@app.route("/history")
@login_required
def history():
    conn = get_db()
    # Get top ticker across all time by avg score
    top_all_time = conn.execute("""
        SELECT ticker, COUNT(*) as appearances,
               AVG(score) as avg_score, MAX(score) as max_score,
               MAX(streak) as max_streak
        FROM scans
        GROUP BY ticker
        HAVING appearances >= 2
        ORDER BY avg_score DESC LIMIT 20
    """).fetchall()

    # Daily top ticker history
    daily = conn.execute("""
        SELECT scan_date, ticker, score, sources, upside_pct, sector
        FROM scans
        WHERE score = (SELECT MAX(score) FROM scans s2 WHERE s2.scan_date = scans.scan_date)
        ORDER BY scan_date DESC LIMIT 30
    """).fetchall()

    conn.close()
    return render_template("history.html", top_all_time=top_all_time, daily=daily)

@app.route("/api/scores/<date>")
@login_required
def api_scores(date):
    conn    = get_db()
    stocks  = conn.execute(
        "SELECT ticker, score FROM scans WHERE scan_date = ? ORDER BY score DESC",
        (date,)
    ).fetchall()
    conn.close()
    return jsonify([dict(s) for s in stocks])

@app.route("/api/ticker/<ticker>")
@login_required
def api_ticker(ticker):
    conn = get_db()
    rows = conn.execute("""
        SELECT scan_date, score, upside_pct, price, streak
        FROM scans WHERE ticker = ?
        ORDER BY scan_date DESC LIMIT 30
    """, (ticker.upper(),)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── dev seed (optional) ───────────────────────────────────────────────────────

@app.route("/seed")
@login_required
def seed():
    """Seeds DB with sample data so you can preview the UI."""
    import random
    conn  = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute("DELETE FROM scans WHERE scan_date = ?", (today,))
    conn.execute("DELETE FROM market_conditions WHERE scan_date = ?", (today,))
    conn.execute("""
        INSERT INTO market_conditions (scan_date, sp500, sp500_chg, vix, tny)
        VALUES (?,?,?,?,?)
    """, (today, 5234.18, 0.43, 18.2, 4.31))
    sample = [
        ("MA", 85, "✓","✓","✓","✓","✓","✓", 510.20, 18.5, 1.1, 0.0, 72, 3.2, 5, "Financial Services"),
        ("NVDA", 80, "✓","–","✓","✓","✓","✓", 875.40, 22.1, 1.8, 0.0, 61, 2.1, 3, "Technology"),
        ("MSFT", 74, "✓","✓","✓","–","✓","–", 415.30, 12.4, 0.9, 0.7, 58, 1.8, 7, "Technology"),
        ("AVGO", 70, "✓","–","✓","✓","–","✓", 1340.00, 15.3, 1.2, 1.5, 45, 2.4, 2, "Technology"),
        ("JPM",  65, "✓","✓","–","–","✓","✓", 198.50, 9.8,  1.1, 2.2, 67, 0.9, 1, "Financial Services"),
        ("LLY",  60, "✓","–","✓","✓","–","–", 820.00, 19.2, 0.6, 0.8, 38, 1.1, 4, "Healthcare"),
        ("V",    55, "✓","✓","–","–","✓","–", 276.40, 11.1, 0.9, 0.7, 71, 0.8, 2, "Financial Services"),
        ("AAPL", 52, "✓","–","–","–","✓","✓", 189.30, 8.4,  1.2, 0.5, 44, 1.4, 0, "Technology"),
        ("COST", 48, "–","✓","✓","–","–","✓", 785.20, 7.2,  0.8, 0.6, 82, 0.5, 1, "Consumer Cyclical"),
        ("UNH",  44, "✓","–","–","✓","–","–", 512.00, 14.8, 0.7, 1.4, 29, 0.7, 0, "Healthcare"),
    ]
    for i, (t, sc, y, z, ms, ins, eps, rs, pr, up, bt, dv, w52, sp, stk, sec) in enumerate(sample):
        conn.execute("""
            INSERT INTO scans (scan_date,ticker,score,sources,yahoo_sb,zacks,morningstar,
            insider,eps_rev,beats_sp,price,upside_pct,beta,div_yield,week52_pos,
            short_pct,vol_spike,streak,is_new,sector)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (today,t,sc,f"{i+1}/7",y,z,ms,ins,eps,rs,pr,up,bt,dv,w52,sp,"0",stk,"🆕" if stk==0 else "",sec))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))

# ── run ───────────────────────────────────────────────────────────────────────

# Initialize DB on startup (runs in Railway too)
init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
