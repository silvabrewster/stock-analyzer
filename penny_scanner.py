"""
Penny Stock Momentum Scanner - Ultra Fast Version
Completes in under 60 seconds using bulk yfinance download.
"""

import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

SMALL_MAX = 20.0
MICRO_MAX = 2.0
PENNY_MAX = 5.0

WEIGHTS = {
    "volume_spike":      30,
    "price_breakout":    25,
    "relative_strength": 20,
    "insider_buying":    15,
    "short_squeeze":     10,
}

UNIVERSE = [
    "SOFI", "HOOD", "AFRM", "UPST", "DKNG", "HIMS", "JOBY",
    "IONQ", "BLNK", "CHPT", "NIO", "LCID", "SPCE", "MVIS",
    "SNDL", "WKHS", "NKLA", "MULN", "GPRO", "LMND", "ROOT",
    "CELH", "CRSP", "BEAM", "EDIT", "FATE", "BLUE", "IOVA",
    "SKLZ", "BARK", "TRUP", "FLNC", "PTRA", "DNUT", "HEAR",
    "ACHR", "OPEN", "STEM", "MAPS", "GTLB", "DOMO", "NCNO",
]

def get_insider_buyers() -> set:
    try:
        url  = "http://openinsider.com/screener?s=&o=&pl=&ph=20&fd=14&xp=1&xs=1&vl=10&cnt=50&action=1"
        resp = requests.get(url, headers=HEADERS, timeout=8)
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

def run_penny_scanner() -> pd.DataFrame:
    print(f"[Penny Scanner] Starting fast scan of {len(UNIVERSE)} tickers...")

    # Step 1 - Get insider buyers (fast, single request)
    insider_buys = get_insider_buyers()
    print(f"  -> {len(insider_buys)} insider buys found")

    # Step 2 - Bulk download ALL price history in ONE call
    print("  -> Bulk downloading price history...")
    end   = datetime.today()
    start = end - timedelta(days=40)
    try:
        all_tickers = ["^GSPC"] + UNIVERSE
        bulk = yf.download(
            all_tickers, start=start, end=end,
            progress=False, auto_adjust=True, group_by="ticker"
        )
        # Get S&P return
        try:
            sp_close = bulk["^GSPC"]["Close"].dropna()
            sp_ret   = (float(sp_close.iloc[-1]) - float(sp_close.iloc[0])) / float(sp_close.iloc[0])
        except Exception:
            sp_ret = 0.08
    except Exception as e:
        print(f"  -> Bulk download failed: {e}")
        bulk   = None
        sp_ret = 0.08

    # Step 3 - Get info for all tickers in bulk using fast_info
    print("  -> Fetching ticker info...")
    rows = []
    for ticker in UNIVERSE:
        try:
            t         = yf.Ticker(ticker)
            fast      = t.fast_info  # much faster than .info

            price     = fast.last_price
            if not price or price <= 0 or price > SMALL_MAX:
                continue

            avg_vol   = fast.three_month_average_volume or 0
            cur_vol   = fast.last_volume or 0
            high52    = fast.year_high
            low52     = fast.year_low
            mkt_cap   = fast.market_cap or 0

            # Volume spike
            vol_ratio = round(cur_vol / avg_vol, 1) if avg_vol and avg_vol > 0 else 0
            vol_hit   = vol_ratio >= 2.0

            # 52w breakout
            range_pct = 0
            if high52 and low52 and (high52 - low52) > 0:
                range_pct = round((price - low52) / (high52 - low52) * 100, 1)
            break_hit = range_pct >= 85

            # Relative strength from bulk data
            rs_hit  = False
            mo_ret  = None
            if bulk is not None:
                try:
                    closes = bulk[ticker]["Close"].dropna()
                    if len(closes) >= 5:
                        t_ret  = (float(closes.iloc[-1]) - float(closes.iloc[0])) / float(closes.iloc[0])
                        rs_hit = t_ret > sp_ret
                        mo_ret = round(t_ret * 100, 1)
                except Exception:
                    pass

            # Short info (from slow info only if needed - skip for speed)
            short_pct   = 0
            squeeze_hit = False

            insider_hit = ticker in insider_buys

            score = (
                vol_hit     * WEIGHTS["volume_spike"]
                + break_hit   * WEIGHTS["price_breakout"]
                + rs_hit      * WEIGHTS["relative_strength"]
                + insider_hit * WEIGHTS["insider_buying"]
                + squeeze_hit * WEIGHTS["short_squeeze"]
            )

            if price <= MICRO_MAX:   tier = "Micro (<$2)"
            elif price <= PENNY_MAX: tier = "Penny ($2-5)"
            else:                    tier = "Small ($5-20)"

            if mkt_cap >= 1e9:       cap_label = f"${mkt_cap/1e9:.1f}B"
            elif mkt_cap >= 1e6:     cap_label = f"${mkt_cap/1e6:.0f}M"
            else:                    cap_label = "Micro"

            sig_count = sum([vol_hit, break_hit, rs_hit, insider_hit, squeeze_hit])

            rows.append({
                "Ticker":        ticker,
                "Name":          ticker,
                "Tier":          tier,
                "Score":         score,
                "Price":         round(price, 3),
                "Mkt Cap":       cap_label,
                "Vol Spike":     "✓" if vol_hit else "–",
                "Vol Ratio":     f"{vol_ratio}x" if vol_ratio > 0 else "–",
                "Breakout":      "✓" if break_hit else "–",
                "52w Range%":    f"{range_pct}%",
                "Beats Mkt":     "✓" if rs_hit else "–",
                "1mo Return":    f"{mo_ret}%" if mo_ret is not None else "–",
                "Insider Buy":   "✓" if insider_hit else "–",
                "Short Squeeze": "–",
                "Short Float%":  "–",
                "Signals":       f"{sig_count}/5",
            })
        except Exception as e:
            print(f"  -> {ticker} error: {e}")
            continue

    print(f"  -> Scan complete: {len(rows)} stocks processed")
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Score", ascending=False).reset_index(drop=True)

if __name__ == "__main__":
    df = run_penny_scanner()
    print(df.to_string(index=False))
