"""
Stock Convergence Analyzer  v3.1
==================================
7-source consensus engine with full accuracy boosters.

New in v3.1:
  - Signal alignment score (short/medium/long term convergence)
  - Alignment stored in DB for display on stock detail + dashboard
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
    MISSING.append("requests / beautifulsoup4")
try:
    import pandas as pd
except ImportError:
    MISSING.append("pandas")

if MISSING:
    print("\n[!] Missing packages. Run:\n")
    print(f"    pip install {' '.join(MISSING).replace(' / ', ' ')}\n")
    sys.exit(1)

# ── config ────────────────────────────────────────────────────────────────────

VANGUARD_ETFS       = ["VOO", "VGT", "VUG", "VTV", "VIG"]
VANGUARD_MIN_WEIGHT = 1.5
SP500_TICKER        = "^GSPC"
VIX_TICKER          = "^VIX"
TNX_TICKER          = "^TNX"
STREAK_FILE         = "streak_tracker.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SOURCE_WEIGHTS = {
    "yahoo_analyst":     22,
    "zacks_strong_buy":  22,
    "morningstar":       18,
    "insider_buying":    12,
    "vanguard":          10,
    "earnings_revision":  8,
    "relative_strength":  8,
}

# ── market conditions ─────────────────────────────────────────────────────────

def get_market_conditions() -> dict:
    print("\n[Market] Fetching market conditions...")
    result = {}
    try:
        for label, ticker in [("sp500", SP500_TICKER), ("vix", VIX_TICKER), ("tny", TNX_TICKER)]:
            t     = yf.Ticker(ticker)
            info  = t.info
            price = info.get("regularMarketPrice") or info.get("previousClose")
            prev  = info.get("previousClose")
            chg   = round((price - prev) / prev * 100, 2) if price and prev and prev > 0 else None
            result[label] = {"price": round(price, 2) if price else "n/a", "chg": chg}
        print(f"  → S&P: {result['sp500']['price']} ({result['sp500']['chg']}%)  "
              f"VIX: {result['vix']['price']}  10yr: {result['tny']['price']}%")
    except Exception as e:
        print(f"  → Market data error: {e}")
    return result

# ── streak tracker ────────────────────────────────────────────────────────────

def load_streaks() -> dict:
    if not os.path.exists(STREAK_FILE):
        return {}
    try:
        df = pd.read_csv(STREAK_FILE)
        return dict(zip(df["Ticker"], df["Streak"]))
    except Exception:
        return {}

def load_yesterday_top(streak_df_path: str = STREAK_FILE) -> set:
    if not os.path.exists(streak_df_path):
        return set()
    try:
        df = pd.read_csv(streak_df_path)
        return set(df[df["Streak"] > 0]["Ticker"].tolist())
    except Exception:
        return set()

def save_streaks(top_tickers: list, old_streaks: dict):
    top_set     = set(top_tickers)
    new_streaks = {}
    for t in top_tickers:
        new_streaks[t] = old_streaks.get(t, 0) + 1
    for t, s in old_streaks.items():
        if t not in top_set:
            new_streaks[t] = 0
    df = pd.DataFrame([{"Ticker": k, "Streak": v} for k, v in new_streaks.items()])
    df.to_csv(STREAK_FILE, index=False)

# ── signal alignment (new in v3.1) ────────────────────────────────────────────

def get_signal_alignment(ticker: str) -> dict:
    """
    Detects whether short-term, medium-term, and long-term signals
    all point in the same direction — a powerful accuracy booster.

    Short-term:  5-day return vs 20-day avg
    Medium-term: 20-day return vs 50-day avg
    Long-term:   50-day return vs 200-day avg (or 52w position)

    Returns:
        dict with aligned (bool), direction (bullish/bearish/mixed),
        short_term, medium_term, long_term, alignment_score (0-3)
    """
    try:
        hist = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 50:
            return _no_alignment()

        close = hist["Close"]
        last  = float(close.iloc[-1])

        # Short term: price vs 20d avg
        avg20 = float(close.tail(20).mean())
        short_bull = last > avg20

        # Medium term: 20d avg vs 50d avg
        avg50  = float(close.tail(50).mean())
        med_bull = avg20 > avg50

        # Long term: 50d avg vs 200d avg (use what we have)
        if len(close) >= 200:
            avg200   = float(close.tail(200).mean())
            long_bull = avg50 > avg200
        else:
            # fallback: 52w position > 50%
            high = float(close.max())
            low  = float(close.min())
            long_bull = (last - low) / (high - low) > 0.5 if (high - low) > 0 else False

        alignment_score = sum([short_bull, med_bull, long_bull])

        if alignment_score == 3:
            direction = "bullish"
            label     = "Full Alignment ↑"
            emoji     = "🟢"
        elif alignment_score == 0:
            direction = "bearish"
            label     = "Full Alignment ↓"
            emoji     = "🔴"
        elif alignment_score == 2:
            direction = "mostly_bullish"
            label     = "Mostly Bullish"
            emoji     = "🟡"
        elif alignment_score == 1:
            direction = "mostly_bearish"
            label     = "Mostly Bearish"
            emoji     = "🟠"
        else:
            direction = "mixed"
            label     = "Mixed Signals"
            emoji     = "⚪"

        return {
            "aligned":         alignment_score == 3 or alignment_score == 0,
            "direction":       direction,
            "label":           label,
            "emoji":           emoji,
            "alignment_score": alignment_score,
            "short_term":      "bullish" if short_bull  else "bearish",
            "medium_term":     "bullish" if med_bull    else "bearish",
            "long_term":       "bullish" if long_bull   else "bearish",
            "short_vs_avg20":  round((last - avg20) / avg20 * 100, 1),
            "avg20_vs_avg50":  round((avg20 - avg50) / avg50 * 100, 1),
        }
    except Exception as e:
        return _no_alignment()


def _no_alignment():
    return {
        "aligned": False, "direction": "unknown", "label": "No Data",
        "emoji": "⚪", "alignment_score": 0,
        "short_term": "unknown", "medium_term": "unknown", "long_term": "unknown",
        "short_vs_avg20": 0, "avg20_vs_avg50": 0,
    }


def get_signal_alignments(tickers: list) -> dict:
    """Fetch alignment for all tickers. Called during scanner run."""
    print(f"\n[Signal Alignment] Checking {len(tickers)} tickers...")
    results = {}
    bullish = 0
    for ticker in tickers:
        results[ticker] = get_signal_alignment(ticker)
        if results[ticker]["direction"] in ("bullish", "mostly_bullish"):
            bullish += 1
        time.sleep(0.15)
    print(f"  → {bullish} tickers showing bullish alignment")
    return results

# ── source 1: yahoo finance ───────────────────────────────────────────────────

def get_yahoo_strong_buys(tickers: list) -> dict:
    results = {}
    print(f"\n[Yahoo Finance] Checking {len(tickers)} tickers...")
    for ticker in tickers:
        try:
            t    = yf.Ticker(ticker)
            info = t.info
            rec       = info.get("recommendationMean")
            num       = info.get("numberOfAnalystOpinions", 0)
            key       = info.get("recommendationKey", "n/a")
            target    = info.get("targetMeanPrice")
            current   = info.get("currentPrice") or info.get("regularMarketPrice")
            high52    = info.get("fiftyTwoWeekHigh")
            low52     = info.get("fiftyTwoWeekLow")
            beta      = info.get("beta")
            div_yield = info.get("dividendYield", 0) or 0
            avg_vol   = info.get("averageVolume", 0) or 0
            cur_vol   = info.get("volume", 0) or 0
            sector    = info.get("sector", "Unknown")
            fwd_eps   = info.get("forwardEps")
            trail_eps = info.get("trailingEps")
            short_pct = info.get("shortPercentOfFloat", 0) or 0

            upside = None
            if target and current and current > 0:
                upside = round((target - current) / current * 100, 1)

            week52_pos = None
            if high52 and low52 and current and (high52 - low52) > 0:
                week52_pos = round((current - low52) / (high52 - low52) * 100, 1)

            vol_spike    = bool(avg_vol and cur_vol and cur_vol > avg_vol * 1.5)
            revision_up  = bool(fwd_eps and trail_eps and fwd_eps > trail_eps)
            strong_buy   = (rec is not None and rec <= 1.8 and num >= 5)

            results[ticker] = {
                "yahoo_strong_buy":   strong_buy,
                "yahoo_rec_mean":     round(rec, 2) if rec else None,
                "yahoo_num_analysts": num,
                "yahoo_rec_key":      key,
                "yahoo_upside_pct":   upside,
                "current_price":      round(current, 2) if current else None,
                "price_target":       round(target, 2) if target else None,
                "week52_pos":         week52_pos,
                "beta":               round(beta, 2) if beta else None,
                "div_yield":          round(div_yield * 100, 2),
                "vol_spike":          vol_spike,
                "revision_up":        revision_up,
                "short_pct":          round(short_pct * 100, 1),
                "sector":             sector,
            }
            time.sleep(0.35)
        except Exception as e:
            results[ticker] = {"yahoo_strong_buy": False, "error": str(e)}

    count = sum(1 for v in results.values() if v.get("yahoo_strong_buy"))
    print(f"  → {count} Yahoo Strong Buys found")
    return results

# ── source 2: zacks ───────────────────────────────────────────────────────────

def get_zacks_strong_buys() -> set:
    print("\n[Zacks] Fetching Strong Buy list...")
    url = "https://www.zacks.com/stocks/buy-list/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            print(f"  → Blocked (HTTP {resp.status_code}). Skipping.")
            return set()
        soup    = BeautifulSoup(resp.text, "html.parser")
        matches = re.findall(r'"symbol"\s*:\s*"([A-Z]{1,5})"', resp.text)
        tickers = set(matches)
        if not tickers:
            for row in soup.select("table tbody tr"):
                cells = row.find_all("td")
                if cells:
                    t = cells[0].get_text(strip=True).upper()
                    if re.match(r'^[A-Z]{1,5}$', t):
                        tickers.add(t)
        print(f"  → {len(tickers)} Zacks #1 Strong Buys found")
        return tickers
    except Exception as e:
        print(f"  → Error: {e}. Skipping.")
        return set()

# ── source 3: morningstar ─────────────────────────────────────────────────────

def get_morningstar_ratings(tickers: list) -> dict:
    print(f"\n[Morningstar] Estimating ratings for {len(tickers)} tickers...")
    results = {}
    for ticker in tickers:
        try:
            info          = yf.Ticker(ticker).info
            roe           = info.get("returnOnEquity", 0) or 0
            profit_margin = info.get("profitMargins", 0) or 0
            forward_pe    = info.get("forwardPE")
            target        = info.get("targetMeanPrice")
            current       = info.get("currentPrice") or info.get("regularMarketPrice")
            discount = None
            if target and current and current > 0:
                discount = (target - current) / current
            ms_score = 0
            if roe > 0.15:                     ms_score += 1
            if profit_margin > 0.10:           ms_score += 1
            if discount and discount > 0.10:   ms_score += 2
            if forward_pe and forward_pe < 25: ms_score += 1
            results[ticker] = {"ms_strong": ms_score >= 3, "ms_star_equiv": min(5, ms_score + 1)}
            time.sleep(0.3)
        except Exception:
            results[ticker] = {"ms_strong": False}
    count = sum(1 for v in results.values() if v.get("ms_strong"))
    print(f"  → {count} Morningstar 4-5★ equivalents found")
    return results

# ── source 4: vanguard ────────────────────────────────────────────────────────

def get_vanguard_top_holdings() -> dict:
    print(f"\n[Vanguard] Mining top holdings from {VANGUARD_ETFS}...")
    holdings = {}
    for etf_ticker in VANGUARD_ETFS:
        try:
            etf     = yf.Ticker(etf_ticker)
            fetched = False
            try:
                fd = etf.funds_data
                if fd is not None and hasattr(fd, "top_holdings"):
                    df = fd.top_holdings
                    if df is not None and not df.empty:
                        for _, row in df.iterrows():
                            sym    = str(row.get("Symbol", "")).upper().strip()
                            weight = float(row.get("Holding Percent", 0))
                            if sym and weight > 0:
                                holdings.setdefault(sym, []).append(weight)
                        fetched = True
            except Exception:
                pass
            if not fetched:
                try:
                    h = etf.get_holdings()
                    if h is not None and not h.empty:
                        for sym, row in h.iterrows():
                            weight = float(row.get("% Assets", 0))
                            if sym and weight > 0:
                                holdings.setdefault(str(sym).upper(), []).append(weight)
                except Exception:
                    pass
        except Exception as e:
            print(f"  → {etf_ticker}: {e}")
    result = {k: round(max(v), 2) for k, v in holdings.items()}
    if not result:
        print("  → Live data unavailable. Using Q1 2026 snapshot.")
        result = {
            "AAPL":5.2,"MSFT":6.8,"NVDA":5.9,"AMZN":4.1,"META":2.8,"GOOGL":2.5,
            "GOOG":1.8,"BRK-B":1.7,"LLY":1.6,"AVGO":1.6,"JPM":1.5,"TSLA":1.4,
            "UNH":1.3,"V":1.2,"XOM":1.1,"MA":1.0,"COST":1.0,"HD":0.9,"PG":0.9,"JNJ":0.8,
        }
    strong = {k: v for k, v in result.items() if v >= VANGUARD_MIN_WEIGHT}
    print(f"  → {len(strong)} tickers with ≥{VANGUARD_MIN_WEIGHT}% Vanguard weight")
    return result

# ── source 5: insider buying ──────────────────────────────────────────────────

def get_insider_buyers() -> set:
    print("\n[Insider Buying] Fetching SEC filings via OpenInsider...")
    url = "http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=30&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl=100&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=100&action=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  → Blocked. Skipping.")
            return set()
        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"class": "tinytable"})
        if not table:
            print("  → Table not found. Skipping.")
            return set()
        ticker_counts = {}
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) > 3:
                ticker = cells[3].get_text(strip=True).upper()
                if re.match(r'^[A-Z]{1,5}$', ticker):
                    ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
        buyers = {t for t, c in ticker_counts.items() if c >= 2}
        print(f"  → {len(buyers)} stocks with cluster insider buying")
        return buyers
    except Exception as e:
        print(f"  → Error: {e}. Skipping.")
        return set()

# ── source 6/7: relative strength ────────────────────────────────────────────

def get_relative_strength(tickers: list) -> dict:
    print(f"\n[Relative Strength] Calculating 3-month performance vs S&P 500...")
    results = {}
    end   = datetime.today()
    start = end - timedelta(days=95)
    try:
        sp500  = yf.download(SP500_TICKER, start=start, end=end, progress=False, auto_adjust=True)
        sp_ret = (float(sp500["Close"].iloc[-1]) - float(sp500["Close"].iloc[0])) / float(sp500["Close"].iloc[0])
    except Exception as e:
        print(f"  → S&P error: {e}. Skipping.")
        return {}
    beating = 0
    for ticker in tickers:
        try:
            hist = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
            if hist.empty or len(hist) < 10:
                results[ticker] = False
                continue
            t_ret = (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0])
            results[ticker] = t_ret > sp_ret
            if results[ticker]:
                beating += 1
            time.sleep(0.2)
        except Exception:
            results[ticker] = False
    print(f"  → {beating} stocks outperforming S&P 500 over 3 months")
    return results

# ── sector concentration ──────────────────────────────────────────────────────

def check_sector_concentration(top_df: pd.DataFrame, yahoo_data: dict):
    sectors = []
    for ticker in top_df["Ticker"].head(10).tolist():
        sector = yahoo_data.get(ticker, {}).get("sector", "Unknown")
        if sector and sector != "Unknown":
            sectors.append(sector)
    if not sectors:
        return None
    sector_counts = pd.Series(sectors).value_counts()
    top_sector    = sector_counts.index[0]
    top_count     = sector_counts.iloc[0]
    if top_count >= 4:
        return f"⚠️ Sector concentration warning: {top_count}/10 top picks are {top_sector}"
    return None

# ── consensus engine (v3.1 — adds alignment) ─────────────────────────────────

def compute_consensus(
    tickers:          list,
    yahoo_data:       dict,
    zacks_buys:       set,
    morningstar_data: dict,
    vanguard_weights: dict,
    insider_buyers:   set,
    rel_strength:     dict,
    old_streaks:      dict,
    yesterday_top:    set,
    alignments:       dict = None,   # NEW in v3.1
) -> pd.DataFrame:
    rows = []

    for ticker in tickers:
        y        = yahoo_data.get(ticker, {})
        m        = morningstar_data.get(ticker, {})
        v_weight = vanguard_weights.get(ticker, 0)
        align    = (alignments or {}).get(ticker, {})

        yahoo_hit    = 1 if y.get("yahoo_strong_buy") else 0
        zacks_hit    = 1 if ticker in zacks_buys else 0
        ms_hit       = 1 if m.get("ms_strong") else 0
        vgd_hit      = 1 if v_weight >= VANGUARD_MIN_WEIGHT else 0
        insider_hit  = 1 if ticker in insider_buyers else 0
        revision_hit = 1 if y.get("revision_up") else 0
        rs_hit       = 1 if rel_strength.get(ticker) else 0

        sources_agree = yahoo_hit + zacks_hit + ms_hit + vgd_hit + insider_hit + revision_hit + rs_hit

        score = (
            yahoo_hit    * SOURCE_WEIGHTS["yahoo_analyst"]
            + zacks_hit  * SOURCE_WEIGHTS["zacks_strong_buy"]
            + ms_hit     * SOURCE_WEIGHTS["morningstar"]
            + vgd_hit    * SOURCE_WEIGHTS["vanguard"]
            + insider_hit  * SOURCE_WEIGHTS["insider_buying"]
            + revision_hit * SOURCE_WEIGHTS["earnings_revision"]
            + rs_hit       * SOURCE_WEIGHTS["relative_strength"]
        )

        # ── Signal alignment bonus (up to +8 points) ──────────────────────
        # Only boosts when all 3 timeframes agree AND fundamental score is decent
        alignment_bonus = 0
        alignment_label = ""
        if align:
            ascore = align.get("alignment_score", 0)
            adir   = align.get("direction", "")
            if ascore == 3 and adir == "bullish" and score >= 40:
                alignment_bonus = 8   # full bullish alignment
                alignment_label = "🎯"
            elif ascore == 2 and adir == "mostly_bullish" and score >= 40:
                alignment_bonus = 4
                alignment_label = "↑"
            elif ascore == 0 and adir == "bearish":
                alignment_bonus = -4  # all timeframes bearish = penalty
                alignment_label = "↓"
        score += alignment_bonus

        short_pct  = y.get("short_pct", 0) or 0
        short_flag = "⚠️" if short_pct > 20 else ("🎯" if short_pct > 10 and yahoo_hit else "")
        upside     = y.get("yahoo_upside_pct")
        week52     = y.get("week52_pos")
        beta       = y.get("beta")
        div        = y.get("div_yield", 0)
        is_new     = "🆕" if ticker not in yesterday_top else ""
        streak     = old_streaks.get(ticker, 0)
        streak_str = f"🔥{streak}d" if streak >= 3 else (f"{streak}d" if streak > 0 else "–")

        rows.append({
            "Ticker":           ticker,
            "Consensus Score":  score,
            "Sources Agree":    f"{sources_agree}/7",
            "New?":             is_new,
            "Streak":           streak_str,
            "Yahoo SB":         "✓" if yahoo_hit else "–",
            "Zacks #1":         "✓" if zacks_hit else "–",
            "Morningstar ★★★":  "✓" if ms_hit    else "–",
            "Insider Buy":      "✓" if insider_hit else "–",
            "EPS Rev ↑":        "✓" if revision_hit else "–",
            "Beats S&P":        "✓" if rs_hit else "–",
            "Alignment":        align.get("label", "–") if align else "–",
            "Alignment Emoji":  alignment_label,
            "Short %":          f"{short_pct}% {short_flag}",
            "Vol Spike":        "🔥" if y.get("vol_spike") else "",
            "52w Position":     f"{week52}%" if week52 is not None else "n/a",
            "Beta":             beta if beta else "n/a",
            "Div Yield":        f"{div}%" if div and div > 0 else "–",
            "Price":            y.get("current_price", "n/a"),
            "Upside %":         f"{upside}%" if upside is not None else "n/a",
            "# Analysts":       y.get("yahoo_num_analysts", "n/a"),
            "Sector":           y.get("sector", "Unknown"),
        })

    df = pd.DataFrame(rows).sort_values("Consensus Score", ascending=False)
    return df

# ── universe ──────────────────────────────────────────────────────────────────

def build_universe(extra_tickers=None) -> list:
    base = [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG",
        "BRK-B","LLY","AVGO","JPM","TSLA","UNH","V","XOM",
        "MA","COST","HD","PG","JNJ","ABBV","MRK","CVX",
        "CRM","BAC","AMD","NFLX","ORCL","ACN","TMO",
        "WMT","DIS","ADBE","QCOM","TXN","GS","CAT","INTU",
        "IBM","GE","RTX","SPGI","NOW","ISRG","AMAT","MU",
        "PANW","KLAC","LRCX","ADI","SCHW","BLK","AXP","SYK",
        "GILD","REGN","VRTX","ZTS","MCO","TT","ETN",
        "PH","ROK","DHR","A","IQV","EW","BSX","MDT",
        "AMGN","BIIB","ILMN","IDXX","ALGN","RMD","DXCM","PODD",
        "SNPS","CDNS","PTC","FTNT","CRWD","ZS","OKTA",
        "DDOG","NET","MDB","SNOW","PLTR","APP","HOOD","COIN",
    ]
    if extra_tickers:
        base.extend([t.upper() for t in extra_tickers if t.upper() not in base])
    return list(dict.fromkeys(base))

# ── display ───────────────────────────────────────────────────────────────────

def print_results(df: pd.DataFrame, top_n: int = 15, market: dict = {}):
    sp  = market.get("sp500", {})
    vix = market.get("vix", {})
    tny = market.get("tny", {})
    print("\n" + "═" * 100)
    print("  STOCK CONVERGENCE ANALYZER v3.1  —  7-Source | Signal Alignment")
    print(f"  Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  Universe: {len(df)} tickers")
    sp_arrow = "▲" if (sp.get("chg") or 0) > 0 else "▼"
    print(f"  S&P 500: {sp.get('price','n/a')} {sp_arrow}{abs(sp.get('chg',0))}%  |  "
          f"VIX: {vix.get('price','n/a')}  |  10yr Yield: {tny.get('price','n/a')}%")
    print("═" * 100)
    high   = df[df["Consensus Score"] >= 60]
    medium = df[(df["Consensus Score"] >= 35) & (df["Consensus Score"] < 60)]
    print(f"\n🟢  HIGH CONVICTION  (score ≥ 60)  —  {len(high)} stocks\n")
    _print_table(high.head(top_n)) if not high.empty else print("  None found.")
    print(f"\n🟡  MODERATE CONVICTION  (score 35–59)  —  {len(medium)} stocks\n")
    _print_table(medium.head(top_n)) if not medium.empty else print("  None found.")
    print("\n" + "─" * 100)
    print("Score: Yahoo(22)+Zacks(22)+MS(18)+Insider(12)+Vanguard(10)+EPS(8)+RS(8)+Alignment(+8 bonus)")
    print("🎯 = Full signal alignment  ↑ = Mostly bullish  ↓ = Bearish alignment penalty")
    print("─" * 100 + "\n")


def _print_table(df: pd.DataFrame):
    cols = ["Ticker","Consensus Score","Sources Agree","New?","Streak",
            "Yahoo SB","Zacks #1","Insider Buy","EPS Rev ↑","Beats S&P","Alignment",
            "52w Position","Beta","Div Yield","Vol Spike","Short %","Price","Upside %","Sector"]
    available = [c for c in cols if c in df.columns]
    print(df[available].to_string(index=False))

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stock Convergence Analyzer v3.1")
    parser.add_argument("--tickers", nargs="+")
    parser.add_argument("--top",     type=int, default=15)
    parser.add_argument("--export",  type=str)
    parser.add_argument("--no-alignment", action="store_true", help="Skip alignment (faster)")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════╗")
    print("║   Stock Convergence Analyzer  v3.1       ║")
    print("║   7-Source | Signal Alignment             ║")
    print("╚══════════════════════════════════════════╝")

    universe      = [t.upper() for t in args.tickers] if args.tickers else build_universe()
    old_streaks   = load_streaks()
    yesterday_top = load_yesterday_top()

    print(f"\nAnalyzing {len(universe)} tickers across 7 sources + alignment...")

    market           = get_market_conditions()
    vanguard_weights = get_vanguard_top_holdings()
    yahoo_data       = get_yahoo_strong_buys(universe)
    zacks_buys       = get_zacks_strong_buys()
    morningstar_data = get_morningstar_ratings(universe)
    insider_buyers   = get_insider_buyers()
    rel_strength     = get_relative_strength(universe)

    # Signal alignment (can skip with --no-alignment for speed)
    alignments = {}
    if not args.no_alignment:
        alignments = get_signal_alignments(universe)

    df = compute_consensus(
        universe, yahoo_data, zacks_buys, morningstar_data,
        vanguard_weights, insider_buyers, rel_strength,
        old_streaks, yesterday_top, alignments
    )

    top10 = df.head(10)["Ticker"].tolist()
    save_streaks(top10, old_streaks)

    warning = check_sector_concentration(df, yahoo_data)
    if warning:
        print(f"\n{warning}")

    print_results(df, top_n=args.top, market=market)

    fname = args.export or f"convergence_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df.to_csv(fname, index=False)
    print(f"✓ Results saved to {fname}\n")


if __name__ == "__main__":
    main()
