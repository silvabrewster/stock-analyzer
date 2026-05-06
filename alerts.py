"""
alerts.py
=========
Alert detection + Web Push notification sender.
Called from app.py on dashboard load.

Alert types:
  breakout  — stock jumped into top 3 today with score >= 70
  volume    — vol_spike fired on a high-score stock
  portfolio — a portfolio holding moved +/- 5% today
  top5      — a new stock entered top 5 that wasn't there yesterday
"""

import os
import json
import requests

# ── VAPID config ──────────────────────────────────────────────────────────────
# These are your app's push keys. They are already set — do not change them
# unless you regenerate keys and update the frontend VAPID_PUBLIC too.
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
    """Send a single push notification. Silently fails if pywebpush not installed."""
    try:
        from pywebpush import webpush, WebPushException
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
            sub_info = {
                "endpoint": sub["endpoint"],
                "keys": {
                    "p256dh": sub["p256dh"],
                    "auth":   sub["auth"],
                }
            }
            _send_push(sub_info, payload)
    except Exception as e:
        print(f"Push broadcast error: {e}")


def save_alert(conn, ticker: str, alert_type: str, message: str):
    """Save alert to DB and send push notification."""
    conn.execute(
        "INSERT INTO alerts (ticker, type, message) VALUES (?, ?, ?)",
        (ticker, alert_type, message)
    )
    # Send push
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
    Only fires alerts that haven't been seen today to avoid spam.
    """
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    # Get today's top 20 stocks
    stocks = conn.execute(
        "SELECT * FROM scans WHERE scan_date = ? ORDER BY score DESC LIMIT 20",
        (today,)
    ).fetchall()

    if not stocks:
        return  # No scan yet today

    # Get already-fired alerts today to avoid duplicates
    fired_today = set()
    existing = conn.execute(
        "SELECT ticker, type FROM alerts WHERE created_at LIKE ?",
        (f"{today}%",)
    ).fetchall()
    for a in existing:
        fired_today.add(f"{a['ticker']}_{a['type']}")

    new_alerts = []

    # ── Check 1: Breakout — score >= 70 AND in top 3 ──────────────────────
    for i, s in enumerate(stocks[:3]):
        key = f"{s['ticker']}_breakout"
        if key not in fired_today and (s["score"] or 0) >= 70:
            msg = f"Score {int(s['score'])}/100 — ranked #{i+1} today"
            new_alerts.append((s["ticker"], "breakout", msg))

    # ── Check 2: Volume spike on high-score stock ─────────────────────────
    for s in stocks[:10]:
        key = f"{s['ticker']}_volume"
        if key not in fired_today and s.get("vol_spike") == "1" and (s["score"] or 0) >= 55:
            msg = f"Volume spike detected — score {int(s['score'])}/100"
            new_alerts.append((s["ticker"], "volume", msg))

    # ── Check 3: Portfolio holdings ±5% ───────────────────────────────────
    try:
        portfolio = conn.execute("SELECT ticker, buy_price FROM portfolio").fetchall()
        import yfinance as yf
        for holding in portfolio:
            ticker    = holding["ticker"]
            buy_price = holding["buy_price"]
            key       = f"{ticker}_portfolio"
            if key in fired_today:
                continue
            try:
                info  = yf.Ticker(ticker).fast_info
                price = info.last_price
                if price and buy_price:
                    chg = (price - buy_price) / buy_price * 100
                    if abs(chg) >= 5:
                        direction = "up" if chg > 0 else "down"
                        msg = f"Your holding is {direction} {abs(chg):.1f}% from your buy price"
                        new_alerts.append((ticker, "portfolio", msg))
            except Exception:
                pass
    except Exception:
        pass

    # ── Check 4: New stock entered top 5 ─────────────────────────────────
    yesterday_top5 = set()
    try:
        prev = conn.execute("""
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
            msg = f"New entry into today's top 5 — score {int(s['score'] or 0)}/100"
            new_alerts.append((s["ticker"], "top5", msg))

    # Save and push all new alerts
    for ticker, atype, message in new_alerts:
        save_alert(conn, ticker, atype, message)

    if new_alerts:
        conn.commit()
