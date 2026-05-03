"""
Penny Stock & Small Cap Momentum Scanner
==========================================
Separate scanner optimized for stocks under $20.
Uses momentum-based signals instead of analyst consensus
since most analysts don't cover small/micro caps.

Signal weights (sum to 100):
  Volume spike        30  — unusual volume = something happening
  Price breakout      25  — breaking 52-week high = momentum
  Relative strength   20  — beating the market recently
  Insider buying      15  — management buying own stock
  Short squeeze       10  — high short interest = squeeze potential
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

# Price tiers
PENNY_MAX    = 5.0    # true penny stocks
SMALL_MAX    = 20.0   # small caps we track
MICRO_MAX    = 2.0    # micro caps (higher risk)

# Signal weights
WEIGHTS = {
    "volume_spike":    30,
    "price_breakout":  25,
    "relative_strength": 20,
    "insider_buying":  15,
    "short_squeeze":   10,
}

# ── universe ──────────────────────────────────────────────────────────────────

def build_small_cap_universe() -> list[str]:
    """
    Returns a list of small/micro cap tickers to scan.
    Mix of known small caps + dynamic fetch from screener.
    """
    # Known small caps with decent liquidity
    base = [
        # Micro caps under $5
        "SNDL", "CLOV", "WKHS", "RIDE", "NKLA", "GOEV", "FFIE",
        "MULN", "BBIG", "PROG", "ATER", "SPRT", "IRNT", "OPAD",
        "GFAI", "DPRO", "MVIS", "NAKD", "EXPR", "BBBY",
        # Small caps $5-$20
        "SOFI", "HOOD", "OPEN", "UWMC", "RKT", "AFRM", "UPST",
        "DKNG", "PENN", "SKLZ", "BARK", "HIMS", "MAPS", "STEM",
        "JOBY", "ACHR", "LILM", "SPCE", "IONQ", "ARQQ",
        "BZFD", "FLNC", "BLNK", "CHPT", "EVGO", "PTRA",
        "HYLN", "XPEV", "LI", "NIO", "LCID", "FSR",
        "BODY", "NTRB", "KPLT", "LMND", "ROOT", "MILE",
        "BARK", "PETQ", "SHYF", "WOOF", "TRUP",
        "GPRO", "HEAR", "IOVA", "FATE", "BEAM", "EDIT",
        "CRSP", "NTLA", "VERV", "GRPH", "MDNA",
        "NKTR", "SGEN", "ALNY", "RARE", "BLUE",
        "CELH", "PFGC", "USFD", "CHEF", "DNUT",
        "MNDY", "FROG", "GTLB", "DOMO", "NCNO",
    ]
    return list(dict.fromkeys([t.upper() for t in base]))


# ── signal 1: volume spike ────────────────────────────────────────────────────

def check_volume_spike(info: dict) -> tuple[bool, float]:
    """Returns (is_spike, ratio) where ratio = current/avg volume."""
    avg_vol = info.get("averageVolume", 0) or 0
    cur_vol = info.get("volume", 0) or 0
    if avg_vol == 0:
        return False, 0
    ratio = cur_vol / avg_vol
    return ratio >= 2.0, round(ratio, 1)


# ── signal 2: price breakout ──────────────────────────────────────────────────

def check_price_breakout(info: dict) -> tuple[bool, float]:
    """Returns (is_breakout, pct_from_high) — near or above 52w high."""
    high52  = info.get("fiftyTwoWeekHigh")
    low52   = info.get("fiftyTwoWeekLow")
    current = info.get("currentPrice") or info.get("regularMarketPrice")
    if not all([high52, low52, current]):
        return False, 0
    pct_of_range = (current - low52) / (high52 - low52) * 100 if (high52 - low52) > 0 else 0
    # Breakout = in top 15% of 52w range
    return pct_of_range >= 85, round(pct_of_range, 1)


# ── signal 3: relative strength ──────────────────────────────────────────────

def check_relative_strength(ticker: str) -> tuple[bool, float]:
    """Returns (beating_market, return_pct) vs S&P over 1 month."""
    try:
        end   = datetime.today()
        start = end - timedelta(days=35)
        sp    = yf.download("^GSPC", start=start, end=end, progress=False, auto_adjust=True)
        tk    = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if sp.empty or tk.empty or len(tk) < 5:
            return False, 0
        sp_ret = (float(sp["Close"].iloc[-1]) - float(sp["Close"].iloc[0])) / float(sp["Close"].iloc[0]) * 100
        tk_ret = (float(tk["Close"].iloc[-1]) - float(tk["Close"].iloc[0])) / float(tk["Close"].iloc[0]) * 100
        return tk_ret > sp_ret, round(tk_ret, 1)
    except Exception:
        return False, 0


# ── signal 4: insider buying ──────────────────────────────────────────────────

def get_penny_insider_buyers() -> set[str]:
    """Scrapes OpenInsider for small cap insider buys."""
    url = "http://openinsider.com/screener?s=&o=&pl=&ph=5&ll=&lh=&fd=14&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl=10&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=100&action=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
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


# ── signal 5: short squeeze potential ────────────────────────────────────────

def check_short_squeeze(info: dict) -> tuple[bool, float]:
    """High short interest + volume spike = squeeze potential."""
    short_pct = info.get("shortPercentOfFloat", 0) or 0
    short_pct_val = short_pct * 100
    # Short squeeze candidate = >20% short float
    return short_pct_val >= 20, round(short_pct_val, 1)


# ── main scanner ──────────────────────────────────────────────────────────────

def run_penny_scanner() -> pd.DataFrame:
    """Scans small cap universe and returns scored DataFrame."""
    universe      = build_small_cap_universe()
    insider_buys  = get_penny_insider_buyers()
    rows          = []

    print(f"\n[Penny Scanner] Scanning {len(universe)} small/micro cap tickers...")

    for ticker in universe:
        try:
            info    = yf.Ticker(ticker).info
            price   = info.get("currentPrice") or info.get("regularMarketPrice")
            mkt_cap = info.get("marketCap", 0) or 0
            name    = info.get("shortName", ticker)

            if not price or price <= 0:
                continue

            # Only scan stocks under $20
            if price > SMALL_MAX:
                continue

            # Skip if no volume data
            if not info.get("averageVolume"):
                continue

            # Signals
            vol_hit,    vol_ratio   = check_volume_spike(info)
            break_hit,  range_pct   = check_price_breakout(info)
            squeeze_hit, short_pct  = check_short_squeeze(info)
            insider_hit = ticker in insider_buys

            # Relative strength (skip for very low volume stocks)
            avg_vol = info.get("averageVolume", 0) or 0
            if avg_vol > 100000:
                rs_hit, rs_ret = check_relative_strength(ticker)
            else:
                rs_hit, rs_ret = False, 0

            # Score
            score = (
                (vol_hit     * WEIGHTS["volume_spike"])
                + (break_hit   * WEIGHTS["price_breakout"])
                + (rs_hit      * WEIGHTS["relative_strength"])
                + (insider_hit * WEIGHTS["insider_buying"])
                + (squeeze_hit * WEIGHTS["short_squeeze"])
            )

            # Price tier
            if price <= MICRO_MAX:
                tier = "Micro (<$2)"
            elif price <= PENNY_MAX:
                tier = "Penny ($2-5)"
            else:
                tier = "Small ($5-20)"

            # Market cap label
            if mkt_cap >= 1e9:
                cap_label = f"${mkt_cap/1e9:.1f}B"
            elif mkt_cap >= 1e6:
                cap_label = f"${mkt_cap/1e6:.0f}M"
            else:
                cap_label = "Micro"

            rows.append({
                "Ticker":         ticker,
                "Name":           name[:25],
                "Tier":           tier,
                "Score":          score,
                "Price":          round(price, 3),
                "Mkt Cap":        cap_label,
                "Vol Spike":      "✓" if vol_hit else "–",
                "Vol Ratio":      f"{vol_ratio}x" if vol_ratio > 0 else "–",
                "Breakout":       "✓" if break_hit else "–",
                "52w Range%":     f"{range_pct}%" if range_pct > 0 else "–",
                "Beats Mkt":      "✓" if rs_hit else "–",
                "1mo Return":     f"{rs_ret}%" if rs_ret != 0 else "–",
                "Insider Buy":    "✓" if insider_hit else "–",
                "Short Squeeze":  "✓" if squeeze_hit else "–",
                "Short Float%":   f"{short_pct}%" if short_pct > 0 else "–",
                "Signals":        f"{sum([vol_hit, break_hit, rs_hit, insider_hit, squeeze_hit])}/5",
            })
            time.sleep(0.3)

        except Exception as e:
            continue

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Score", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    print("\n╔══════════════════════════════════════════╗")
    print("║   Penny/Small Cap Momentum Scanner       ║")
    print("╚══════════════════════════════════════════╝\n")
    df = run_penny_scanner()
    if df.empty:
        print("No results found.")
    else:
        high = df[df["Score"] >= 50]
        print(f"\n🔥 HIGH MOMENTUM (score ≥ 50) — {len(high)} stocks\n")
        print(high.to_string(index=False))
        fname = f"penny_scan_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        df.to_csv(fname, index=False)
        print(f"\n✓ Full results saved to {fname}")
