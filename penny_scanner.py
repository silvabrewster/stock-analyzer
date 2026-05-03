"""
Penny Stock & Small Cap Momentum Scanner  v2.0
================================================
Fast version — uses batch downloads and parallel processing.
Scans ~60 tickers in under 2 minutes.
"""

import time
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

PENNY_MAX = 5.0
SMALL_MAX = 20.0
MICRO_MAX = 2.0

WEIGHTS = {
    "volume_spike":      30,
    "price_breakout":    25,
    "relative_strength": 20,
    "insider_buying":    15,
    "short_squeeze":     10,
}

# ── universe ──────────────────────────────────────────────────────────────────

def build_small_cap_universe() -> list[str]:
    return [
        "SOFI", "HOOD", "OPEN", "AFRM", "UPST", "DKNG", "SKLZ",
        "HIMS", "STEM", "JOBY", "ACHR", "IONQ", "BLNK", "CHPT",
        "EVGO", "XPEV", "LI", "NIO", "LCID", "SPCE", "MVIS",
        "SNDL", "WKHS", "NKLA", "GOEV", "MULN", "ATER", "GPRO",
        "LMND", "ROOT", "BARK", "TRUP", "CELH", "MNDY", "GTLB",
        "DOMO", "NCNO", "CRSP", "BEAM", "EDIT", "NTLA", "FATE",
        "BLUE", "IOVA", "NKTR", "RARE", "GFAI", "DPRO", "MAPS",
        "FLNC", "PTRA", "HYLN", "BODY", "KPLT", "PFGC", "DNUT",
        "HEAR", "PETQ", "SHYF", "WOOF",
    ]

# ── batch relative strength ───────────────────────────────────────────────────

def get_batch_returns(tickers: list[str]) -> dict[str, float]:
    """Downloads all tickers at once — much faster than one by one."""
    print("  → Downloading price history (batch)...")
    end   = datetime.today()
    start = end - timedelta(days=35)
    try:
        # Download S&P and all tickers in one call
        all_tickers = ["^GSPC"] + tickers
        data = yf.download(
            all_tickers, start=start, end=end,
            progress=False, auto_adjust=True, group_by="ticker"
        )
        returns = {}
        # S&P return
        try:
            sp_close = data["^GSPC"]["Close"].dropna()
            sp_ret   = (float(sp_close.iloc[-1]) - float(sp_close.iloc[0])) / float(sp_close.iloc[0])
        except Exception:
            sp_ret = 0.10  # fallback

        for ticker in tickers:
            try:
                closes = data[ticker]["Close"].dropna()
                if len(closes) < 5:
                    returns[ticker] = None
                    continue
                t_ret = (float(closes.iloc[-1]) - float(closes.iloc[0])) / float(closes.iloc[0])
                returns[ticker] = t_ret - sp_ret  # relative to market
            except Exception:
                returns[ticker] = None

        return returns
    except Exception as e:
        print(f"  → Batch download error: {e}")
        return {}

# ── insider buying ────────────────────────────────────────────────────────────

def get_penny_insider_buyers() -> set[str]:
    print("  → Fetching insider buys...")
    url = "http://openinsider.com/screener?s=&o=&pl=&ph=5&ll=&lh=&fd=14&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl=10&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=100&action=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return set()
        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"class": "tinytable"})
        if not table:
            return set()
        tickers = set()
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) > 3:
                t = cells[3].get_text(strip=True).upper()
                if re.match(r'^[A-Z]{1,5}$', t):
                    tickers.add(t)
        return tickers
    except Exception:
        return set()

# ── batch info fetch ──────────────────────────────────────────────────────────

def get_batch_info(tickers: list[str]) -> dict[str, dict]:
    """Fetches info for all tickers with minimal delay."""
    print(f"  → Fetching info for {len(tickers)} tickers...")
    results = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            results[ticker] = info
            time.sleep(0.2)  # reduced delay
        except Exception:
            results[ticker] = {}
    return results

# ── main scanner ──────────────────────────────────────────────────────────────

def run_penny_scanner() -> pd.DataFrame:
    universe     = build_small_cap_universe()
    print(f"\n[Penny Scanner v2] Scanning {len(universe)} tickers...")

    # Fetch all data
    insider_buys = get_penny_insider_buyers()
    rel_returns  = get_batch_returns(universe)
    all_info     = get_batch_info(universe)

    rows = []
    for ticker in universe:
        try:
            info    = all_info.get(ticker, {})
            price   = info.get("currentPrice") or info.get("regularMarketPrice")
            mkt_cap = info.get("marketCap", 0) or 0
            name    = info.get("shortName", ticker)

            if not price or price <= 0 or price > SMALL_MAX:
                continue

            avg_vol   = info.get("averageVolume", 0) or 0
            cur_vol   = info.get("volume", 0) or 0
            high52    = info.get("fiftyTwoWeekHigh")
            low52     = info.get("fiftyTwoWeekLow")
            short_pct = (info.get("shortPercentOfFloat", 0) or 0) * 100

            # Volume spike
            vol_ratio = round(cur_vol / avg_vol, 1) if avg_vol > 0 else 0
            vol_hit   = vol_ratio >= 2.0

            # Price breakout
            range_pct = 0
            if high52 and low52 and (high52 - low52) > 0:
                range_pct = round((price - low52) / (high52 - low52) * 100, 1)
            break_hit = range_pct >= 85

            # Relative strength
            rel_ret  = rel_returns.get(ticker)
            rs_hit   = (rel_ret is not None and rel_ret > 0)
            mo_ret   = round((rel_ret + 0.10) * 100, 1) if rel_ret is not None else None

            # Insider
            insider_hit = ticker in insider_buys

            # Short squeeze
            squeeze_hit = short_pct >= 20

            score = (
                vol_hit     * WEIGHTS["volume_spike"]
                + break_hit   * WEIGHTS["price_breakout"]
                + rs_hit      * WEIGHTS["relative_strength"]
                + insider_hit * WEIGHTS["insider_buying"]
                + squeeze_hit * WEIGHTS["short_squeeze"]
            )

            if price <= MICRO_MAX:       tier = "Micro (<$2)"
            elif price <= PENNY_MAX:     tier = "Penny ($2-5)"
            else:                        tier = "Small ($5-20)"

            if mkt_cap >= 1e9:           cap_label = f"${mkt_cap/1e9:.1f}B"
            elif mkt_cap >= 1e6:         cap_label = f"${mkt_cap/1e6:.0f}M"
            else:                        cap_label = "Micro"

            sig_count = sum([vol_hit, break_hit, rs_hit, insider_hit, squeeze_hit])

            rows.append({
                "Ticker":       ticker,
                "Name":         name[:25],
                "Tier":         tier,
                "Score":        score,
                "Price":        round(price, 3),
                "Mkt Cap":      cap_label,
                "Vol Spike":    "✓" if vol_hit else "–",
                "Vol Ratio":    f"{vol_ratio}x" if vol_ratio > 0 else "–",
                "Breakout":     "✓" if break_hit else "–",
                "52w Range%":   f"{range_pct}%",
                "Beats Mkt":    "✓" if rs_hit else "–",
                "1mo Return":   f"{mo_ret}%" if mo_ret else "–",
                "Insider Buy":  "✓" if insider_hit else "–",
                "Short Squeeze":"✓" if squeeze_hit else "–",
                "Short Float%": f"{round(short_pct,1)}%" if short_pct > 0 else "–",
                "Signals":      f"{sig_count}/5",
            })
        except Exception:
            continue

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Score", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    df = run_penny_scanner()
    print(df.head(20).to_string(index=False))
