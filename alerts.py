"""
alerts.py
=========
Alert detection + Web Push notification sender.
Called from app.py on dashboard load.

Fixed for PostgreSQL compatibility — uses its own fresh connection
so a failure never poisons the main dashboard connection.
"""

import os
import json

VAPID_PRIVATE = os.environ.get(
    "VAPID_PRIVATE",
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgDwTlg--6JuFjR8pjnPpoewvvOHMdVnrHkRC0JBzPC-6hRANCAAT-nOf47BLsqE4QzlqWJ5gQgqO90EEgQwLyzyxo1NlogYe7lZ60xUmZGWwA5T0PPupu_97-Y4rL2p1b8QysJzQi"
)
VAPID_PUBLIC = os.environ.get(
    "VAPID_PUBLIC",
    "BP6c5_jsEuyoThDOWpYnmBCCo73QQSBDAvLPLGjU2WiBh7uVnrTFSZkZbADlPQ8-6m7_3v5jisvanVvxDKwnNCI"
)
VAPID_CLAIMS = {"sub": "mailto:silvabrayden0@gmail.com"}


def _send_push(subscription_info: dict, payload: dict):
    """Send a single push notification."""
    try:
        from pywebpush import webpush
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE,
            vapid_claims=VAPID_CLAIMS,
        )
    except Exception as e:
        print(f"Push send error: {e}")


def send_push_to_all(conn, payload: dict):
    """Send push notification to all stored subscriptions."""
    try:
        subs = conn.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions").fetchall()
        for sub in subs:
            _send_push({
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
            }, payload)
    except Exception as e:
        print(f"Push broadcast error: {e}")


def save_alert(conn, ticker: str, alert_type: str, message: str):
    """Save alert to DB and send push notification."""
    conn.execute(
        "INSERT INTO alerts (ticker, type, message) VALUES (?, ?, ?)",
        (ticker, alert_type, message)
    )
    icon_map = {"breakout": "📈", "volume": "⚡", "portfolio": "💼", "top5": "🏆"}
    icon = icon_map.get(alert_type, "🔔")
    send_push_to_all(conn, {
        "title": f"{icon} Convergence — {ticker}",
        "body":  message,
        "url":   f"/stock/{ticker}",
        "tag":   f"{alert_type}-{ticker}",
    })


def check_alerts(conn):
    """
    Run all alert checks on dashboard load.
    Uses its own fresh DB connection so any failure never
    affects the main dashboard page connection.
    """
    # Always use a fresh independent connection for alerts
    # so a failure can't poison the main page's postgres transaction
    from database import get_db
    from datetime import datetime
    aconn = None
    try:
        aconn = get_db()
        today = datetime.now().strftime("%Y-%m-%d")

        # Ensure tables exist
        aconn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id         SERIAL PRIMARY KEY,
                ticker     TEXT NOT NULL,
                type       TEXT NOT NULL,
                message    TEXT NOT NULL,
                seen       INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        aconn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id         SERIAL PRIMARY KEY,
                endpoint   TEXT NOT NULL UNIQUE,
                p256dh     TEXT NOT NULL,
                auth       TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        aconn.commit()

        # Get today's top 20 stocks
        stocks = aconn.execute(
            "SELECT * FROM scans WHERE scan_date = ? ORDER BY score DESC LIMIT 20",
            (today,)
        ).fetchall()

        if not stocks:
            return

        # Get already-fired alerts today to avoid duplicates
        fired_today = set()
        existing = aconn.execute(
            "SELECT ticker, type FROM alerts WHERE created_at LIKE ?",
            (f"{today}%",)
        ).fetchall()
        for a in existing:
            fired_today.add(f"{a['ticker']}_{a['type']}")

        new_alerts = []

        # Check 1: Breakout — score >= 70 AND in top 3
        for i, s in enumerate(stocks[:3]):
            key = f"{s['ticker']}_breakout"
            if key not in fired_today and (s["score"] or 0) >= 70:
                new_alerts.append((s["ticker"], "breakout",
                    f"Score {int(s['score'])}/100 — ranked #{i+1} today"))

        # Check 2: Volume spike on high-score stock
        for s in stocks[:10]:
            key = f"{s['ticker']}_volume"
            if key not in fired_today and s.get("vol_spike") == "1" and (s["score"] or 0) >= 55:
                new_alerts.append((s["ticker"], "volume",
                    f"Volume spike detected — score {int(s['score'])}/100"))

        # Check 3: Portfolio holdings ±5%
        try:
            portfolio = aconn.execute("SELECT ticker, buy_price FROM portfolio").fetchall()
            import yfinance as yf
            for holding in portfolio:
                ticker    = holding["ticker"]
                buy_price = holding["buy_price"]
                key       = f"{ticker}_portfolio"
                if key in fired_today:
                    continue
                try:
                    fi    = yf.Ticker(ticker).fast_info
                    price = fi.last_price or fi.regular_market_price
                    if price and buy_price:
                        chg = (float(price) - float(buy_price)) / float(buy_price) * 100
                        if abs(chg) >= 5:
                            direction = "up" if chg > 0 else "down"
                            new_alerts.append((ticker, "portfolio",
                                f"Your holding is {direction} {abs(chg):.1f}% from your buy price"))
                except Exception:
                    pass
        except Exception:
            pass

        # Check 4: New stock entered top 5
        yesterday_top5 = set()
        try:
            prev = aconn.execute("""
                SELECT ticker FROM scans
                WHERE scan_date = (
                    SELECT MAX(scan_date) FROM scans WHERE scan_date < ?
                )
                ORDER BY score DESC LIMIT 5
            """, (today,)).fetchall()
            yesterday_top5 = {r["ticker"] for r in prev}
        except Exception:
            pass

        for s in stocks[:5]:
            key = f"{s['ticker']}_top5"
            if key not in fired_today and s["ticker"] not in yesterday_top5:
                new_alerts.append((s["ticker"], "top5",
                    f"New entry into today's top 5 — score {int(s['score'] or 0)}/100"))

        # Save all new alerts
        for ticker, atype, message in new_alerts:
            save_alert(aconn, ticker, atype, message)

        if new_alerts:
            aconn.commit()

    except Exception as e:
        print(f"Alert check internal error: {e}")
        # Rollback so nothing is left in a broken state
        try:
            if aconn:
                aconn._conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if aconn:
                aconn.close()
        except Exception:
            pass
