"""
Stock Convergence Analyzer  v3.0 (Railway Fixed)
"""

import argparse
import time
import re
import sys
import os
from datetime import datetime, timedelta

MISSING = []
try:
    import yfinance as yf
except ImportError:
    MISSING.append("yfinance")
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    MISSING.append("requests beautifulsoup4")
try:
    import pandas as pd
except ImportError:
    MISSING.append("pandas")

if MISSING:
    print("\n[!] Missing packages. Run:\n")
    print(f"pip install {' '.join(MISSING)}\n")
    sys.exit(1)

# ── config ─────────────────────────

VANGUARD_ETFS = ["VOO", "VGT", "VUG", "VTV", "VIG"]
VANGUARD_MIN_WEIGHT = 1.5
SP500_TICKER = "^GSPC"
VIX_TICKER = "^VIX"
TNX_TICKER = "^TNX"
STREAK_FILE = "streak_tracker.csv"

HEADERS = {"User-Agent": "Mozilla/5.0"}

SOURCE_WEIGHTS = {
    "yahoo_analyst": 22,
    "zacks_strong_buy": 22,
    "morningstar": 18,
    "insider_buying": 12,
    "vanguard": 10,
    "earnings_revision": 8,
    "relative_strength": 8,
}

# ── safe yf helper ─────────────────

def safe_info(ticker):
    try:
        return yf.Ticker(ticker).get_info()
    except Exception:
        return {}

# ── market ─────────────────────────

def get_market_conditions():
    result = {}
    for label, ticker in [("sp500", SP500_TICKER), ("vix", VIX_TICKER), ("tny", TNX_TICKER)]:
        try:
            info = safe_info(ticker)
            price = info.get("regularMarketPrice") or info.get("previousClose")
            prev = info.get("previousClose")
            chg = round((price - prev) / prev * 100, 2) if price and prev and prev != 0 else None
            result[label] = {"price": price, "chg": chg}
        except Exception:
            result[label] = {"price": "n/a", "chg": None}
    return result

# ── streaks ────────────────────────

def load_streaks():
    if not os.path.exists(STREAK_FILE):
        return {}
    try:
        df = pd.read_csv(STREAK_FILE)
        return dict(zip(df["Ticker"], df["Streak"]))
    except:
        return {}

def load_yesterday_top():
    if not os.path.exists(STREAK_FILE):
        return set()
    try:
        df = pd.read_csv(STREAK_FILE)
        return set(df[df["Streak"] > 0]["Ticker"])
    except:
        return set()

def save_streaks(top, old):
    new = {}
    for t in top:
        new[t] = old.get(t, 0) + 1
    for t in old:
        if t not in top:
            new[t] = 0
    pd.DataFrame([{"Ticker": k, "Streak": v} for k, v in new.items()]).to_csv(STREAK_FILE, index=False)

# ── yahoo ──────────────────────────

def get_yahoo_strong_buys(tickers):
    results = {}
    for ticker in tickers:
        try:
            info = safe_info(ticker)

            rec = info.get("recommendationMean")
            num = info.get("numberOfAnalystOpinions", 0)
            target = info.get("targetMeanPrice")
            current = info.get("currentPrice") or info.get("regularMarketPrice")

            upside = round((target - current)/current*100,1) if target and current else None

            results[ticker] = {
                "yahoo_strong_buy": rec and rec <= 1.8 and num >= 5,
                "yahoo_upside_pct": upside,
                "current_price": current,
                "beta": info.get("beta"),
                "div_yield": (info.get("dividendYield") or 0)*100,
                "sector": info.get("sector", "Unknown"),
                "revision_up": info.get("forwardEps") and info.get("trailingEps") and info.get("forwardEps") > info.get("trailingEps"),
            }

            time.sleep(0.1)
        except:
            results[ticker] = {}

    return results

# ── zacks ──────────────────────────

def get_zacks_strong_buys():
    try:
        resp = requests.get("https://www.zacks.com/stocks/buy-list/", headers=HEADERS, timeout=10)
        matches = re.findall(r'"symbol"\s*:\s*"([A-Z]{1,5})"', resp.text)
        return set(matches)
    except:
        return set()

# ── morningstar (light) ────────────

def get_morningstar_ratings(tickers):
    results = {}
    for t in tickers:
        info = safe_info(t)
        score = 0
        if (info.get("returnOnEquity") or 0) > 0.15: score += 1
        if (info.get("profitMargins") or 0) > 0.1: score += 1
        if (info.get("forwardPE") or 999) < 25: score += 1
        results[t] = {"ms_strong": score >= 2}
        time.sleep(0.1)
    return results

# ── vanguard fallback ──────────────

def get_vanguard_top_holdings():
    return {
        "AAPL":7.2,"MSFT":6.8,"NVDA":5.9,"AMZN":4.1,
        "META":2.8,"GOOGL":2.5,"JPM":1.5
    }

# ── insider ────────────────────────

def get_insider_buyers():
    try:
        resp = requests.get("http://openinsider.com/screener", headers=HEADERS, timeout=10)
        tickers = re.findall(r'>\s*([A-Z]{1,5})\s*<', resp.text)
        return set(tickers[:50])
    except:
        return set()

# ── RS ─────────────────────────────

def get_relative_strength(tickers):
    results = {}
    try:
        end = datetime.today()
        start = end - timedelta(days=90)

        sp = yf.download(SP500_TICKER, start=start, end=end, progress=False)
        if sp.empty or len(sp) < 2:
            return {}

        sp_ret = (sp["Close"].iloc[-1] - sp["Close"].iloc[0]) / sp["Close"].iloc[0]

        for t in tickers:
            try:
                d = yf.download(t, start=start, end=end, progress=False)
                if d.empty or len(d) < 2:
                    results[t] = False
                    continue
                ret = (d["Close"].iloc[-1] - d["Close"].iloc[0]) / d["Close"].iloc[0]
                results[t] = ret > sp_ret
                time.sleep(0.1)
            except:
                results[t] = False

    except:
        return {}

    return results

# ── consensus ──────────────────────

def compute_consensus(tickers, yahoo, zacks, ms, vanguard, insiders, rs, old, yesterday):
    rows = []
    for t in tickers:
        y = yahoo.get(t, {})
        score = 0

        if y.get("yahoo_strong_buy"): score += 22
        if t in zacks: score += 22
        if ms.get(t, {}).get("ms_strong"): score += 18
        if t in insiders: score += 12
        if vanguard.get(t,0) >= 1.5: score += 10
        if y.get("revision_up"): score += 8
        if rs.get(t): score += 8

        rows.append({
            "Ticker": t,
            "Consensus Score": score,
            "Price": y.get("current_price"),
            "Upside %": y.get("yahoo_upside_pct"),
            "Sector": y.get("sector")
        })

    return pd.DataFrame(rows).sort_values("Consensus Score", ascending=False)

# ── universe ───────────────────────

def build_universe():
    return ["AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","JPM","XOM","AVGO","LLY","COST"]

# ── main ───────────────────────────

def main():
    universe = build_universe()

    old = load_streaks()
    yesterday = load_yesterday_top()

    market = get_market_conditions()
    yahoo = get_yahoo_strong_buys(universe)
    zacks = get_zacks_strong_buys()
    ms = get_morningstar_ratings(universe)
    vanguard = get_vanguard_top_holdings()
    insiders = get_insider_buyers()
    rs = get_relative_strength(universe)

    df = compute_consensus(universe, yahoo, zacks, ms, vanguard, insiders, rs, old, yesterday)

    save_streaks(df.head(10)["Ticker"].tolist(), old)

    print(df.head(10))

if __name__ == "__main__":
    main()
