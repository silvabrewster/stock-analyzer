"""
alerts.py v2
============
Alert detection + Web Push notification sender.
New in v2: Strategy price alerts — fires when a watchlist stock
hits its target price.
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
    conn.execute(
        "INSERT INTO alerts (ticker, type, message) VALUES (?, ?, ?)",
        (ticker, alert_type, message)
    )
    icon_map = {
        "breakout":    "📈",
        "volume":      "⚡",
        "portfolio":   "💼",
        "top5":        "🏆",
        "price_target":"🎯",
        "insider_buy":   "📈",
        "insider_sell":  "📉",
    }
    icon = icon_map.get(alert_type, "🔔")
    send_push_to_all(conn, {
        "title": f"{icon} Convergence — {ticker}",
        "body":  message,
        "url":   f"/stock/{ticker}",
        "tag":   f"{alert_type}-{ticker}",
    })


def check_insider_for_ticker(ticker, min_value=50000):
    """Fetch recent insider trades for a ticker from openinsider.com."""
    try:
        import requests
        from bs4 import BeautifulSoup
        url = f"http://openinsider.com/screener?s={ticker}&xp=1&xs=1&fd=14&cnt=10&action=1"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"class": "tinytable"})
        if not table:
            return []
        trades = []
        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 11:
                continue
            title = cells[4].get_text(strip=True)
            trade_type_raw = cells[5].get_text(strip=True)
            value_raw = cells[10].get_text(strip=True)
            try:
                value = float(value_raw.replace("$", "").replace(",", "").replace("+", "").replace("-", ""))
            except Exception:
                continue
            if value < min_value:
                continue
            if "P" in trade_type_raw:
                trade_type = "buy"
            elif "S" in trade_type_raw and "P" not in trade_type_raw:
                trade_type = "sell"
            else:
                continue
            trades.append({"type": trade_type, "title": title, "value": value})
        return trades
    except Exception:
        return []


def check_alerts(conn):
    """
    Run all alert checks. Uses its own fresh DB connection
    so failures can't affect the main dashboard connection.
    """
    from database import get_db
    from datetime import datetime
    aconn = None
    try:
        aconn = get_db()
        today = datetime.now().strftime("%Y-%m-%d")

        # Ensure tables exist (SQLite-compatible syntax)
        for sql in [
            """CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
                type TEXT NOT NULL, message TEXT NOT NULL, seen INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        ]:
            try:
                aconn.execute(sql)
            except Exception:
                pass
        aconn.commit()

        # Get already-fired alerts today
        fired_today = set()
        existing = aconn.execute(
            "SELECT ticker, type FROM alerts WHERE created_at LIKE ?",
            (f"{today}%",)
        ).fetchall()
        for a in existing:
            fired_today.add(f"{a['ticker']}_{a['type']}")

        new_alerts = []

        # ── Get today's top 20 stocks ──────────────────────────────────────
        stocks = aconn.execute(
            "SELECT * FROM scans WHERE scan_date = ? ORDER BY score DESC LIMIT 20",
            (today,)
        ).fetchall()

        if stocks:
            # Check 1: Breakout — score >= 70 AND in top 3
            for i, s in enumerate(stocks[:3]):
                key = f"{s['ticker']}_breakout"
                if key not in fired_today and (s["score"] or 0) >= 70:
                    new_alerts.append((s["ticker"], "breakout",
                        f"Score {int(s['score'])}/100 — ranked #{i+1} today"))

            # Check 2: Volume spike on high-score stock
            for s in stocks[:10]:
                key = f"{s['ticker']}_volume"
                if key not in fired_today and s.get("vol_spike")=="1" and (s["score"] or 0)>=55:
                    new_alerts.append((s["ticker"], "volume",
                        f"Volume spike detected — score {int(s['score'])}/100"))

            # Check 3: New stock entered top 5
            yesterday_top5 = set()
            try:
                prev = aconn.execute("""
                    SELECT ticker FROM scans
                    WHERE scan_date = (SELECT MAX(scan_date) FROM scans WHERE scan_date < ?)
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

        # ── Check 4: Portfolio holdings ±5% ───────────────────────────────
        try:
            portfolio = aconn.execute("SELECT DISTINCT ticker, buy_price FROM portfolio").fetchall()
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor as _TPE, TimeoutError as _FT
            def _get_price(t):
                fi = yf.Ticker(t).fast_info
                return fi.last_price or fi.regular_market_price
            for holding in portfolio:
                ticker    = holding["ticker"]
                buy_price = holding["buy_price"]
                key       = f"{ticker}_portfolio"
                if key in fired_today: continue
                try:
                    with _TPE(max_workers=1) as _ex:
                        price = _ex.submit(_get_price, ticker).result(timeout=8)
                    if price and buy_price:
                        chg = (float(price)-float(buy_price))/float(buy_price)*100
                        if abs(chg) >= 5:
                            direction = "up" if chg>0 else "down"
                            new_alerts.append((ticker, "portfolio",
                                f"Your holding is {direction} {abs(chg):.1f}% from your buy price"))
                except Exception:
                    pass
        except Exception:
            pass

        # ── Check 5: Watchlist price targets (NEW) ─────────────────────────
        try:
            watchlist = aconn.execute(
                "SELECT ticker, target_price FROM watchlist WHERE target_price IS NOT NULL"
            ).fetchall()
            import yfinance as yf
            for w in watchlist:
                ticker       = w["ticker"]
                target_price = float(w["target_price"])
                key          = f"{ticker}_price_target"
                if key in fired_today: continue
                try:
                    # Use cached price first
                    cached = aconn.execute(
                        "SELECT price, fetched_at FROM price_cache WHERE ticker=?", (ticker,)
                    ).fetchone()
                    current_price = None
                    if cached:
                        from datetime import datetime as dt
                        age = (dt.now()-dt.fromisoformat(cached["fetched_at"].replace(" ","T").replace("Z","+00:00").split("+")[0])).total_seconds()/60
                        if age < 30:
                            current_price = float(cached["price"])
                    if not current_price:
                        from concurrent.futures import ThreadPoolExecutor as _TPE2
                        with _TPE2(max_workers=1) as _ex2:
                            fi = _ex2.submit(lambda t=ticker: yf.Ticker(t).fast_info).result(timeout=8)
                        current_price = fi.last_price or fi.regular_market_price
                        if current_price: current_price = float(current_price)

                    if current_price and target_price:
                        diff_pct = (current_price - target_price) / target_price * 100
                        # Alert when price drops TO or BELOW target (buying opportunity)
                        if diff_pct <= 0:
                            new_alerts.append((ticker, "price_target",
                                f"Hit your target! Now at ${current_price:.2f} (target: ${target_price:.2f}) — potential buy zone"))
                        # Also alert when within 2% of target
                        elif diff_pct <= 2:
                            new_alerts.append((ticker, "price_target",
                                f"Near your target — ${current_price:.2f} is {diff_pct:.1f}% above your ${target_price:.2f} target"))
                except Exception:
                    pass
        except Exception:
            pass

        # ── Check 6: Insider activity on portfolio + watchlist stocks ────────
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            port_rows = aconn.execute("SELECT DISTINCT ticker FROM portfolio").fetchall()
            watch_rows = aconn.execute("SELECT DISTINCT ticker FROM watchlist").fetchall()
            all_tickers = list({r["ticker"] for r in port_rows + watch_rows})[:12]
            if all_tickers:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futures = {ex.submit(check_insider_for_ticker, t): t for t in all_tickers}
                    for future in as_completed(futures, timeout=20):
                        ticker = futures[future]
                        try:
                            trades = future.result()
                            for trade in trades[:1]:  # one alert per ticker
                                atype = "insider_buy" if trade["type"] == "buy" else "insider_sell"
                                key = f"{ticker}_{atype}"
                                if key in fired_today:
                                    continue
                                action = "bought" if trade["type"] == "buy" else "sold"
                                msg = f"{trade['title']} {action} ~${trade['value']:,.0f} in shares (last 14 days)"
                                new_alerts.append((ticker, atype, msg))
                        except Exception:
                            pass
        except Exception as e:
            print(f"Insider alert check error: {e}")

        # Save all new alerts
        for ticker, atype, message in new_alerts:
            save_alert(aconn, ticker, atype, message)
        if new_alerts:
            aconn.commit()

    except Exception as e:
        print(f"Alert check internal error: {e}")
        try:
            if aconn: aconn._conn.rollback()
        except Exception: pass
    finally:
        try:
            if aconn: aconn.close()
        except Exception: pass
