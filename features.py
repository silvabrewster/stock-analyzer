"""
Advanced Features Module
=========================
AI market brief, news sentiment, options flow,
institutional ownership, backtest, price alerts,
earnings calendar
"""

import time
import requests
from datetime import datetime, timedelta
import pandas as pd

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── AI DAILY MARKET BRIEF ────────────────────────────────────────────────────

def generate_market_brief(top_stocks: list, market: dict) -> str:
    """Calls Claude API to write a daily market brief."""
    try:
        sp    = market.get("sp500", {})
        vix   = market.get("vix", {})
        tny   = market.get("tny", {})
        tickers = ", ".join([s.get("Ticker", "") for s in top_stocks[:5]])

        sp_chg_val = sp.get('chg', 0) or 0
        mood = "bullish" if sp_chg_val > 0.5 else ("bearish" if sp_chg_val < -0.5 else "mixed")
        prompt = f"""Write exactly 3 sentences as a morning market brief. No headers, no bullets, no labels.

Data: S&P 500 at {sp.get('price','n/a')} ({sp_chg_val:+.2f}%), VIX at {vix.get('price','n/a')}, 10yr yield {tny.get('price','n/a')}%. Top picks: {tickers}.

Sentence 1: Overall market mood right now.
Sentence 2: What these top picks have in common today.
Sentence 3: One specific thing to watch.

Max 75 words. Sound like a Bloomberg terminal, not a textbook."""

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=20
        )
        if resp.status_code == 200:
            return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"AI brief error: {e}")

    sp_chg = sp.get('chg', 0) or 0
    mood   = "bullish" if sp_chg > 0.5 else ("bearish" if sp_chg < -0.5 else "neutral")
    return (f"Markets opened {mood} today with the S&P 500 at {sp.get('price','n/a')}. "
            f"Top convergence picks include {tickers}, showing broad multi-source agreement. "
            f"Watch VIX at {vix.get('price','n/a')} for volatility signals.")


# ── NEWS SENTIMENT ────────────────────────────────────────────────────────────

def get_news_sentiment(ticker: str) -> dict:
    """Gets recent news headlines and scores sentiment."""
    try:
        import yfinance as yf
        t     = yf.Ticker(ticker)
        news  = t.news
        if not news:
            return {"score": 0, "label": "Neutral", "count": 0, "headlines": []}

        positive_words = ["beats", "surge", "record", "strong", "upgrade", "buy",
                         "growth", "profit", "exceeds", "raises", "bullish", "gain"]
        negative_words = ["miss", "fall", "weak", "downgrade", "sell", "loss",
                         "decline", "cut", "bearish", "drop", "concern", "risk"]

        total_score = 0
        headlines   = []
        count       = min(len(news), 5)

        for article in news[:count]:
            title = article.get("title", "").lower()
            score = 0
            for w in positive_words:
                if w in title: score += 1
            for w in negative_words:
                if w in title: score -= 1
            total_score += score
            headlines.append({
                "title": article.get("title", "")[:80],
                "score": score,
                "url":   article.get("link", "")
            })

        avg = total_score / count if count > 0 else 0
        if avg > 0.3:   label = "Positive"
        elif avg < -0.3: label = "Negative"
        else:            label = "Neutral"

        return {
            "score":     round(avg, 2),
            "label":     label,
            "count":     count,
            "headlines": headlines
        }
    except Exception as e:
        return {"score": 0, "label": "Neutral", "count": 0, "headlines": []}


# ── OPTIONS FLOW ──────────────────────────────────────────────────────────────

def get_unusual_options(tickers: list[str]) -> dict[str, dict]:
    """
    Checks Unusual Whales for unusual options activity.
    Returns dict of ticker -> options signal.
    """
    results = {}
    try:
        url  = "https://unusualwhales.com/api/option_trades/recent"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for trade in data.get("data", []):
                sym = trade.get("ticker", "").upper()
                if sym in tickers:
                    results[sym] = {
                        "unusual":    True,
                        "type":       trade.get("put_call", ""),
                        "premium":    trade.get("premium", 0),
                        "sentiment":  "Bullish" if trade.get("put_call") == "CALL" else "Bearish"
                    }
    except Exception:
        pass

    # Fallback: use yfinance options data
    for ticker in tickers:
        if ticker not in results:
            try:
                import yfinance as yf
                t = yf.Ticker(ticker)
                opts = t.options
                if opts:
                    # Check put/call ratio from nearest expiry
                    chain = t.option_chain(opts[0])
                    calls_vol = chain.calls["volume"].sum() if not chain.calls.empty else 0
                    puts_vol  = chain.puts["volume"].sum()  if not chain.puts.empty  else 0
                    total_vol = calls_vol + puts_vol
                    if total_vol > 0:
                        pc_ratio = puts_vol / calls_vol if calls_vol > 0 else 1
                        unusual  = total_vol > 10000
                        results[ticker] = {
                            "unusual":   unusual,
                            "type":      "CALL" if pc_ratio < 0.7 else "PUT",
                            "pc_ratio":  round(pc_ratio, 2),
                            "sentiment": "Bullish" if pc_ratio < 0.7 else ("Bearish" if pc_ratio > 1.3 else "Neutral"),
                            "volume":    int(total_vol)
                        }
                time.sleep(0.3)
            except Exception:
                results[ticker] = {"unusual": False, "sentiment": "Neutral"}

    return results


# ── INSTITUTIONAL OWNERSHIP ───────────────────────────────────────────────────

def get_institutional_changes(tickers: list[str]) -> dict[str, dict]:
    """Gets institutional ownership % and recent changes via yfinance."""
    results = {}
    for ticker in tickers:
        try:
            import yfinance as yf
            t    = yf.Ticker(ticker)
            info = t.info
            inst_pct = info.get("institutionalOwnershipPercentage") or \
                       info.get("heldPercentInstitutions", 0) or 0
            inst_pct = round(inst_pct * 100, 1) if inst_pct <= 1 else round(inst_pct, 1)

            # Get institutional holders
            holders = t.institutional_holders
            net_change = 0
            if holders is not None and not holders.empty:
                if "% Out" in holders.columns:
                    net_change = float(holders["% Out"].iloc[0]) if len(holders) > 0 else 0

            results[ticker] = {
                "inst_pct":   inst_pct,
                "net_change": round(net_change, 2),
                "increasing": net_change > 0,
                "label":      f"{inst_pct}% institutional"
            }
            time.sleep(0.3)
        except Exception:
            results[ticker] = {"inst_pct": 0, "net_change": 0, "increasing": False, "label": "n/a"}
    return results


# ── BACKTEST ──────────────────────────────────────────────────────────────────

def run_backtest(conn) -> dict:
    """
    Simulates buying the #1 pick each day and calculates returns.
    Compares against S&P 500 buy-and-hold.
    """
    import yfinance as yf

    try:
        # Get all daily #1 picks from DB
        picks = conn.execute("""
            SELECT scan_date, ticker, score
            FROM scans
            WHERE score = (SELECT MAX(score) FROM scans s2 WHERE s2.scan_date = scans.scan_date)
            ORDER BY scan_date ASC
        """).fetchall()

        if not picks or len(picks) < 5:
            return {"error": "Need at least 5 days of scan history to backtest"}

        results   = []
        portfolio = 10000  # starting $10k
        sp_start  = None
        sp_end    = None

        for i, pick in enumerate(picks[:-1]):
            try:
                ticker   = pick["ticker"]
                buy_date = pick["scan_date"]
                sell_date = picks[i+1]["scan_date"]
            except (KeyError, TypeError, IndexError):
                continue

            try:
                hist = yf.download(ticker,
                    start=buy_date, end=sell_date,
                    progress=False, auto_adjust=True)
                if hist.empty or len(hist) < 2:
                    continue
                buy_price  = float(hist["Close"].iloc[0])
                sell_price = float(hist["Close"].iloc[-1])
                ret        = (sell_price - buy_price) / buy_price
                portfolio *= (1 + ret)

                if sp_start is None:
                    sp_hist  = yf.download("^GSPC", start=buy_date,
                        end=sell_date, progress=False, auto_adjust=True)
                    if not sp_hist.empty:
                        sp_start = float(sp_hist["Close"].iloc[0])
                        sp_end   = float(sp_hist["Close"].iloc[-1])
                else:
                    sp_hist = yf.download("^GSPC", start=buy_date,
                        end=sell_date, progress=False, auto_adjust=True)
                    if not sp_hist.empty:
                        sp_end = float(sp_hist["Close"].iloc[-1])

                results.append({
                    "date":       buy_date,
                    "ticker":     ticker,
                    "return_pct": round(ret * 100, 2),
                    "portfolio":  round(portfolio, 2)
                })
                time.sleep(0.2)
            except Exception:
                continue

        if not results:
            return {"error": "Could not calculate returns"}

        total_return  = round((portfolio - 10000) / 10000 * 100, 1)
        sp_return     = round((sp_end - sp_start) / sp_start * 100, 1) if sp_start and sp_end else 0
        wins          = sum(1 for r in results if r["return_pct"] > 0)
        win_rate      = round(wins / len(results) * 100, 1)
        best          = max(results, key=lambda x: x["return_pct"])
        worst         = min(results, key=lambda x: x["return_pct"])

        return {
            "total_return":  total_return,
            "sp_return":     sp_return,
            "alpha":         round(total_return - sp_return, 1),
            "final_value":   round(portfolio, 2),
            "starting":      10000,
            "num_trades":    len(results),
            "win_rate":      win_rate,
            "best_trade":    best,
            "worst_trade":   worst,
            "daily_results": results[-30:],  # last 30 for chart
        }
    except Exception as e:
        return {"error": str(e)}


# ── PRICE ALERTS ──────────────────────────────────────────────────────────────

def check_price_alerts(conn, top_tickers: list[str], resend_key: str, email_to: str):
    """Emails user when a watchlisted stock enters the top 10."""
    try:
        watchlist = conn.execute("SELECT ticker, target_price, notes FROM watchlist").fetchall()
        if not watchlist:
            return

        alerts = []
        for w in watchlist:
            ticker = w["ticker"] if isinstance(w, dict) else w[0]
            if ticker in top_tickers:
                alerts.append(ticker)

        if not alerts:
            return

        today   = datetime.now().strftime("%B %d, %Y")
        tickers = ", ".join(alerts)
        html    = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;">
          <h2 style="color:#c9a84c;">🔔 Watchlist Alert — {today}</h2>
          <p>The following stocks from your watchlist just entered the <strong>Top 10</strong>:</p>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin:16px 0;">
            {"".join([f'<span style="background:#1a1a2e;color:#c9a84c;padding:8px 16px;border-radius:8px;font-family:monospace;font-size:18px;">{t}</span>' for t in alerts])}
          </div>
          <p style="color:#888;font-size:13px;">Log in to view full analysis and signals.</p>
        </body></html>"""

        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
            json={
                "from": "onboarding@resend.dev",
                "to":   [email_to],
                "subject": f"🔔 Watchlist Alert: {tickers} entered top 10",
                "html": html
            },
            timeout=10
        )
        print(f"  ✓ Price alert sent for: {tickers}")
    except Exception as e:
        print(f"  ✗ Price alert error: {e}")


# ── EARNINGS CALENDAR ─────────────────────────────────────────────────────────

def get_earnings_calendar(tickers: list[str]) -> list[dict]:
    """Gets upcoming earnings dates for a list of tickers."""
    import yfinance as yf
    earnings = []
    for ticker in tickers:
        try:
            t   = yf.Ticker(ticker)
            cal = t.calendar
            if cal is not None and "Earnings Date" in cal:
                ed = cal["Earnings Date"]
                if hasattr(ed, '__iter__'):
                    ed = list(ed)
                    ed = ed[0] if ed else None
                if ed:
                    ts        = pd.Timestamp(ed)
                    days_away = (ts - pd.Timestamp.now()).days
                    if -3 <= days_away <= 30:
                        earnings.append({
                            "ticker":    ticker,
                            "date":      ts.strftime("%Y-%m-%d"),
                            "days_away": days_away,
                            "label":     ts.strftime("%b %d"),
                            "urgent":    days_away <= 7
                        })
            time.sleep(0.3)
        except Exception:
            continue
    return sorted(earnings, key=lambda x: x["days_away"])
