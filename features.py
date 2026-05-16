"""
Advanced Features Module
=========================
AI market brief, news sentiment, options flow,
institutional ownership, backtest, price alerts,
earnings calendar

Speed improvements:
- Earnings fetched in parallel threads (was sequential with sleep)
- fast_info used for prices (10x faster than .info)
- Price cache table used for watchlist/portfolio
"""

import time
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── PRICE CACHE ───────────────────────────────────────────────────────────────

def get_cached_price(conn, ticker: str, max_age_minutes: int = 15):
    """
    Returns cached price if fresh enough, else fetches live and caches it.
    Uses fast_info for speed — 10x faster than .info
    """
    import yfinance as yf

    # Try cache first
    try:
        row = conn.execute(
            "SELECT price, fetched_at FROM price_cache WHERE ticker = ?",
            (ticker,)
        ).fetchone()
        if row:
            fetched = datetime.fromisoformat(row["fetched_at"])
            age     = (datetime.now() - fetched).total_seconds() / 60
            if age < max_age_minutes:
                return round(float(row["price"]), 2)
    except Exception:
        pass

    # Fetch fresh price using fast_info (much faster than .info)
    price = None
    try:
        fi    = yf.Ticker(ticker).fast_info
        price = fi.last_price or fi.regular_market_price
        if price:
            price = round(float(price), 2)
            # Save to cache
            try:
                conn.execute("""
                    INSERT INTO price_cache (ticker, price, fetched_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                    price=excluded.price, fetched_at=excluded.fetched_at
                """, (ticker, price, datetime.now().isoformat()))
            except Exception:
                pass
    except Exception:
        pass

    return price


def init_price_cache(conn):
    """Create price cache table if it doesn't exist."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_cache (
                ticker     TEXT PRIMARY KEY,
                price      REAL NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        conn.commit()
    except Exception:
        pass


# ── AI DAILY MARKET BRIEF ────────────────────────────────────────────────────

def generate_market_brief(top_stocks: list, market: dict) -> str:
    """Calls Claude API to write a daily market brief."""
    try:
        sp    = market.get("sp500", {})
        vix   = market.get("vix", {})
        tny   = market.get("tny", {})
        tickers = ", ".join([s.get("Ticker", "") for s in top_stocks[:5]])

        sp_chg_val = sp.get('chg', 0) or 0
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
    tickers = ", ".join([s.get("Ticker", "") for s in top_stocks[:5]])
    return (f"Markets opened {mood} today with the S&P 500 at {sp.get('price','n/a')}. "
            f"Top convergence picks include {tickers}, showing broad multi-source agreement. "
            f"Watch VIX at {vix.get('price','n/a')} for volatility signals.")


# ── NEWS SENTIMENT ────────────────────────────────────────────────────────────

def get_news_sentiment(ticker: str) -> dict:
    """Gets recent news headlines and scores sentiment."""
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        news = t.news
        if not news:
            return {"score": 0, "label": "Neutral", "count": 0, "headlines": []}

        positive_words = ["beats","surge","record","strong","upgrade","buy",
                          "growth","profit","exceeds","raises","bullish","gain"]
        negative_words = ["miss","fall","weak","downgrade","sell","loss",
                          "decline","cut","bearish","drop","concern","risk"]

        total_score = 0
        headlines   = []
        count       = min(len(news), 5)

        for article in news[:count]:
            title = article.get("title", "").lower()
            score = sum(1 for w in positive_words if w in title) - \
                    sum(1 for w in negative_words if w in title)
            total_score += score
            headlines.append({
                "title": article.get("title", "")[:80],
                "score": score,
                "url":   article.get("link", "")
            })

        avg   = total_score / count if count > 0 else 0
        label = "Positive" if avg > 0.3 else ("Negative" if avg < -0.3 else "Neutral")
        return {"score": round(avg,2), "label": label, "count": count, "headlines": headlines}
    except Exception:
        return {"score": 0, "label": "Neutral", "count": 0, "headlines": []}


# ── OPTIONS FLOW ──────────────────────────────────────────────────────────────

def get_unusual_options(tickers: list) -> dict:
    """Checks options data for unusual activity."""
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
                        "unusual":   True,
                        "type":      trade.get("put_call", ""),
                        "premium":   trade.get("premium", 0),
                        "sentiment": "Bullish" if trade.get("put_call") == "CALL" else "Bearish"
                    }
    except Exception:
        pass

    for ticker in tickers:
        if ticker not in results:
            try:
                import yfinance as yf
                t    = yf.Ticker(ticker)
                opts = t.options
                if opts:
                    chain     = t.option_chain(opts[0])
                    calls_vol = chain.calls["volume"].sum() if not chain.calls.empty else 0
                    puts_vol  = chain.puts["volume"].sum()  if not chain.puts.empty  else 0
                    total_vol = calls_vol + puts_vol
                    if total_vol > 0:
                        pc_ratio = puts_vol / calls_vol if calls_vol > 0 else 1
                        results[ticker] = {
                            "unusual":   total_vol > 10000,
                            "type":      "CALL" if pc_ratio < 0.7 else "PUT",
                            "pc_ratio":  round(pc_ratio, 2),
                            "sentiment": "Bullish" if pc_ratio < 0.7 else ("Bearish" if pc_ratio > 1.3 else "Neutral"),
                            "volume":    int(total_vol)
                        }
                time.sleep(0.2)
            except Exception:
                results[ticker] = {"unusual": False, "sentiment": "Neutral"}
    return results


# ── INSTITUTIONAL OWNERSHIP ───────────────────────────────────────────────────

def get_institutional_changes(tickers: list) -> dict:
    """Gets institutional ownership % via yfinance."""
    results = {}
    for ticker in tickers:
        try:
            import yfinance as yf
            t        = yf.Ticker(ticker)
            info     = t.info
            inst_pct = info.get("institutionalOwnershipPercentage") or \
                       info.get("heldPercentInstitutions", 0) or 0
            inst_pct = round(inst_pct * 100, 1) if inst_pct <= 1 else round(inst_pct, 1)
            holders  = t.institutional_holders
            net_change = 0
            if holders is not None and not holders.empty and "% Out" in holders.columns:
                net_change = float(holders["% Out"].iloc[0]) if len(holders) > 0 else 0
            results[ticker] = {"inst_pct": inst_pct, "net_change": round(net_change,2), "increasing": net_change > 0, "label": f"{inst_pct}% institutional"}
            time.sleep(0.2)
        except Exception:
            results[ticker] = {"inst_pct": 0, "net_change": 0, "increasing": False, "label": "n/a"}
    return results


# ── BACKTEST ──────────────────────────────────────────────────────────────────

def run_backtest(conn) -> dict:
    """Simulates buying the #1 pick each day and calculates returns."""
    import yfinance as yf
    try:
        picks = conn.execute("""
            SELECT scan_date, ticker, score
            FROM scans
            WHERE score = (SELECT MAX(score) FROM scans s2 WHERE s2.scan_date = scans.scan_date)
            ORDER BY scan_date ASC
        """).fetchall()

        print(f"Backtest: found {len(picks) if picks else 0} picks from DB")
        if not picks or len(picks) < 2:
            return {"error": "Need at least 2 days of scan history to backtest"}

        results = []; portfolio = 10000; sp_start = None; sp_end = None

        for i, pick in enumerate(picks[:-1]):
            try:
                ticker    = pick["ticker"]
                buy_date  = pick["scan_date"]
                sell_date = picks[i+1]["scan_date"]
            except (KeyError, TypeError, IndexError):
                continue
            try:
                # extend sell_date by 5 days so yfinance returns at least 1 bar
                import datetime as _dt
                sell_dt_ext = (
                    _dt.datetime.strptime(sell_date, "%Y-%m-%d") + _dt.timedelta(days=5)
                ).strftime("%Y-%m-%d")
                hist = yf.download(ticker, start=buy_date, end=sell_dt_ext, progress=False, auto_adjust=True)
                if hist.empty: continue
                close = hist["Close"]
                if hasattr(close, "columns"):
                    close = close.iloc[:, 0]
                buy_price  = float(close.iloc[0])
                sell_price = float(close.iloc[-1])
                ret        = (sell_price - buy_price) / buy_price
                portfolio *= (1 + ret)
                sp_hist    = yf.download("^GSPC", start=buy_date, end=sell_dt_ext, progress=False, auto_adjust=True)
                if not sp_hist.empty:
                    sp_close = sp_hist["Close"]
                    if hasattr(sp_close, "columns"):
                        sp_close = sp_close.iloc[:, 0]
                    if sp_start is None: sp_start = float(sp_close.iloc[0])
                    sp_end = float(sp_close.iloc[-1])
                results.append({"date": buy_date, "ticker": ticker, "return_pct": round(ret*100,2), "portfolio": round(portfolio,2)})
                time.sleep(0.1)
            except Exception as e:
                print(f"Backtest trade error {ticker} {buy_date}: {e}")
                continue

        if not results:
            return {"error": "Could not calculate returns"}

        total_return = round((portfolio - 10000) / 10000 * 100, 1)
        sp_return    = round((sp_end - sp_start) / sp_start * 100, 1) if sp_start and sp_end else 0
        wins         = sum(1 for r in results if r["return_pct"] > 0)

        return {
            "total_return":  total_return, "sp_return": sp_return,
            "alpha":         round(total_return - sp_return, 1),
            "final_value":   round(portfolio, 2), "starting": 10000,
            "num_trades":    len(results), "win_rate": round(wins/len(results)*100,1),
            "best_trade":    max(results, key=lambda x: x["return_pct"]),
            "worst_trade":   min(results, key=lambda x: x["return_pct"]),
            "daily_results": results[-30:],
        }
    except Exception as e:
        return {"error": str(e)}


# ── PRICE ALERTS ──────────────────────────────────────────────────────────────

def check_price_alerts(conn, top_tickers: list, resend_key: str, email_to: str):
    """Emails user when a watchlisted stock enters the top 10."""
    try:
        watchlist = conn.execute("SELECT ticker, target_price, notes FROM watchlist").fetchall()
        if not watchlist: return
        alerts = [w["ticker"] for w in watchlist if w["ticker"] in top_tickers]
        if not alerts: return
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
        requests.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
            json={"from": "onboarding@resend.dev", "to": [email_to],
                  "subject": f"🔔 Watchlist Alert: {tickers} entered top 10", "html": html},
            timeout=10)
    except Exception as e:
        print(f"Price alert error: {e}")


# ── EARNINGS CALENDAR (parallel, fast) ───────────────────────────────────────

def _fetch_earnings_single(ticker: str) -> dict | None:
    """Fetch earnings for one ticker. Called in parallel."""
    try:
        import yfinance as yf
        t   = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None or "Earnings Date" not in cal:
            return None
        ed = cal["Earnings Date"]
        if hasattr(ed, '__iter__'):
            ed = list(ed)
            ed = ed[0] if ed else None
        if not ed:
            return None
        ts        = pd.Timestamp(ed)
        days_away = (ts - pd.Timestamp.now()).days
        if -3 <= days_away <= 30:
            return {
                "ticker":    ticker,
                "date":      ts.strftime("%Y-%m-%d"),
                "days_away": days_away,
                "label":     ts.strftime("%b %d"),
                "urgent":    days_away <= 7
            }
    except Exception:
        pass
    return None


def get_earnings_calendar(tickers: list) -> list:
    """
    Gets upcoming earnings dates in PARALLEL — much faster than sequential.
    Was: 15 stocks × 0.3s sleep = 4.5s minimum
    Now: all 15 fetched at once = ~1-2s total
    """
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_earnings_single, t): t for t in tickers}
        for future in as_completed(futures, timeout=20):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception:
                pass
    return sorted(results, key=lambda x: x["days_away"])


# ── SMART BUY RATING ──────────────────────────────────────────────────────────

def get_smart_buy_rating(score, upside_pct, short_pct, week52_pos, beta, price, price_target):
    """
    Returns a smart buy rating based on convergence score + valuation signals.
    Only returns Smart Buy when multiple factors align — designed to boost accuracy.

    Returns:
        dict with keys: rating, label, color, reason
    """
    # Can't be smart buy if price is above analyst target
    above_target = price_target and price and price > price_target

    score      = score or 0
    upside     = upside_pct or 0
    short      = short_pct or 0
    w52        = week52_pos or 50
    b          = beta or 1.0

    # ── STRONG BUY ────────────────────────────────────────────────────────
    if (score >= 70
        and upside >= 10
        and not above_target
        and short < 20
        and b < 2.5):
        return {
            "rating": "strong_buy",
            "label":  "Strong Buy",
            "emoji":  "🟢",
            "color":  "var(--green)",
            "reason": f"High consensus score, {upside:.0f}% upside, low risk"
        }

    # ── BUY ───────────────────────────────────────────────────────────────
    if (score >= 55
        and upside >= 5
        and not above_target
        and short < 25):
        return {
            "rating": "buy",
            "label":  "Buy",
            "emoji":  "🟩",
            "color":  "var(--green)",
            "reason": f"Good signals with {upside:.0f}% analyst upside"
        }

    # ── AVOID — above target ──────────────────────────────────────────────
    if above_target:
        overshoot = round((price - price_target) / price_target * 100, 1)
        return {
            "rating": "avoid",
            "label":  "Avoid",
            "emoji":  "🔴",
            "color":  "var(--red)",
            "reason": f"Price is {overshoot}% above analyst target — not a smart entry"
        }

    # ── AVOID — high short interest + weak score ──────────────────────────
    if short > 25 and score < 55:
        return {
            "rating": "avoid",
            "label":  "Avoid",
            "emoji":  "🔴",
            "color":  "var(--red)",
            "reason": f"High short interest ({short:.0f}%) + weak consensus"
        }

    # ── AVOID — low score ─────────────────────────────────────────────────
    if score < 40:
        return {
            "rating": "avoid",
            "label":  "Avoid",
            "emoji":  "🔴",
            "color":  "var(--red)",
            "reason": "Low consensus score across sources"
        }

    # ── NEUTRAL (default) ─────────────────────────────────────────────────
    return {
        "rating": "neutral",
        "label":  "Neutral",
        "emoji":  "🟡",
        "color":  "var(--yellow)",
        "reason": "Mixed signals — wait for clearer setup"
    }
