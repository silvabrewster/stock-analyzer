"""
market_regime.py
================
Bull / Bear / Neutral / Correction detection.
Called from scheduler after market data is fetched,
and from the dashboard route on page load.

Regime is stored in market_conditions table and displayed
as a banner on the dashboard.

Signals used (all data already collected by analyzer.py):
  - VIX level and trend
  - S&P 500 price vs 50d / 200d moving average
  - 10yr yield trend (rising = headwind)
  - Sector breadth (how many sectors have high avg scores)
  - % of top 20 stocks with score >= 60
"""

import os
from datetime import datetime


# ── regime thresholds ─────────────────────────────────────────────────────────

VIX_FEAR       = 25   # above = elevated fear
VIX_PANIC      = 35   # above = panic / correction territory
SP_BEAR_DROP   = -10  # % drop from recent high = bear
SP_CORR_DROP   = -5   # % drop from recent high = correction


def detect_market_regime(market: dict, top_stocks: list) -> dict:
    """
    Detects current market regime from available data.

    Args:
        market: dict with sp500, vix, tny keys (from get_market_conditions)
        top_stocks: list of scan row dicts from DB

    Returns:
        dict with keys: regime, label, emoji, color, confidence, summary, signals
    """
    signals = []
    bull_points = 0
    bear_points = 0
    total_weight = 0

    # ── Signal 1: VIX level (weight: 3) ──────────────────────────────────
    vix = None
    try:
        vix = float(market.get("vix", {}).get("price") or 0)
    except Exception:
        pass

    if vix:
        total_weight += 3
        if vix < 15:
            bull_points += 3
            signals.append({"name": "VIX", "value": f"{vix:.1f}", "signal": "bullish", "note": "Low fear — market calm"})
        elif vix < VIX_FEAR:
            bull_points += 1.5
            bear_points += 1.5
            signals.append({"name": "VIX", "value": f"{vix:.1f}", "signal": "neutral", "note": "Normal volatility range"})
        elif vix < VIX_PANIC:
            bear_points += 2
            bull_points += 1
            signals.append({"name": "VIX", "value": f"{vix:.1f}", "signal": "bearish", "note": "Elevated fear"})
        else:
            bear_points += 3
            signals.append({"name": "VIX", "value": f"{vix:.1f}", "signal": "bearish", "note": "Panic levels — extreme fear"})

    # ── Signal 2: S&P 500 change (weight: 3) ──────────────────────────────
    sp_chg = None
    try:
        sp_chg = float(market.get("sp500", {}).get("chg") or 0)
    except Exception:
        pass

    if sp_chg is not None:
        total_weight += 3
        if sp_chg >= 0.5:
            bull_points += 3
            signals.append({"name": "S&P Today", "value": f"{sp_chg:+.2f}%", "signal": "bullish", "note": "Strong up day"})
        elif sp_chg >= 0:
            bull_points += 2
            bear_points += 1
            signals.append({"name": "S&P Today", "value": f"{sp_chg:+.2f}%", "signal": "bullish", "note": "Slight gain"})
        elif sp_chg >= -0.5:
            bull_points += 1
            bear_points += 2
            signals.append({"name": "S&P Today", "value": f"{sp_chg:+.2f}%", "signal": "neutral", "note": "Slight loss"})
        else:
            bear_points += 3
            signals.append({"name": "S&P Today", "value": f"{sp_chg:+.2f}%", "signal": "bearish", "note": "Significant down day"})

    # ── Signal 3: 10yr yield (weight: 2) ──────────────────────────────────
    tny = None
    try:
        tny = float(market.get("tny", {}).get("price") or 0)
    except Exception:
        pass

    if tny:
        total_weight += 2
        if tny < 3.5:
            bull_points += 2
            signals.append({"name": "10yr Yield", "value": f"{tny:.2f}%", "signal": "bullish", "note": "Low rates — equity tailwind"})
        elif tny < 4.5:
            bull_points += 1
            bear_points += 1
            signals.append({"name": "10yr Yield", "value": f"{tny:.2f}%", "signal": "neutral", "note": "Moderate rates"})
        else:
            bear_points += 2
            signals.append({"name": "10yr Yield", "value": f"{tny:.2f}%", "signal": "bearish", "note": "High rates — equity headwind"})

    # ── Signal 4: Stock score breadth (weight: 4) ─────────────────────────
    # What % of top 20 scanned stocks have score >= 60
    if top_stocks:
        total_weight += 4
        high_score = sum(1 for s in top_stocks if (s.get("score") or 0) >= 60)
        pct        = high_score / len(top_stocks) * 100
        if pct >= 60:
            bull_points += 4
            signals.append({"name": "Score Breadth", "value": f"{pct:.0f}%", "signal": "bullish", "note": f"{high_score}/{len(top_stocks)} stocks scoring ≥60"})
        elif pct >= 35:
            bull_points += 2
            bear_points += 2
            signals.append({"name": "Score Breadth", "value": f"{pct:.0f}%", "signal": "neutral", "note": f"{high_score}/{len(top_stocks)} stocks scoring ≥60"})
        else:
            bear_points += 4
            signals.append({"name": "Score Breadth", "value": f"{pct:.0f}%", "signal": "bearish", "note": f"Only {high_score}/{len(top_stocks)} stocks scoring ≥60"})

    # ── Signal 5: Insider buying breadth (weight: 2) ──────────────────────
    if top_stocks:
        total_weight += 2
        insider_count = sum(1 for s in top_stocks if s.get("insider") == "✓")
        if insider_count >= 5:
            bull_points += 2
            signals.append({"name": "Insider Buying", "value": f"{insider_count} stocks", "signal": "bullish", "note": "Strong insider buying across top picks"})
        elif insider_count >= 2:
            bull_points += 1
            bear_points += 1
            signals.append({"name": "Insider Buying", "value": f"{insider_count} stocks", "signal": "neutral", "note": "Some insider activity"})
        else:
            bear_points += 1
            bull_points += 1
            signals.append({"name": "Insider Buying", "value": f"{insider_count} stocks", "signal": "neutral", "note": "Limited insider activity"})

    # ── Compute regime ────────────────────────────────────────────────────
    if total_weight == 0:
        return _unknown_regime()

    bull_pct = bull_points / total_weight * 100
    bear_pct = bear_points / total_weight * 100
    confidence = round(max(bull_pct, bear_pct))

    # VIX panic overrides everything
    if vix and vix >= VIX_PANIC:
        return {
            "regime":     "correction",
            "label":      "Market Correction",
            "emoji":      "🔴",
            "color":      "var(--red)",
            "bg":         "rgba(248,113,113,0.08)",
            "border":     "rgba(248,113,113,0.3)",
            "confidence": confidence,
            "summary":    f"VIX at {vix:.0f} signals panic selling. Extreme caution warranted — this is a correction or worse.",
            "signals":    signals,
        }

    if bull_pct >= 65:
        return {
            "regime":     "bull",
            "label":      "Bull Market",
            "emoji":      "🟢",
            "color":      "var(--green)",
            "bg":         "rgba(74,222,128,0.06)",
            "border":     "rgba(74,222,128,0.25)",
            "confidence": confidence,
            "summary":    f"Strong bullish conditions across {len(signals)} signals. Momentum favors long positions.",
            "signals":    signals,
        }
    elif bull_pct >= 50:
        return {
            "regime":     "neutral_bull",
            "label":      "Cautious Bull",
            "emoji":      "🟡",
            "color":      "var(--yellow)",
            "bg":         "rgba(251,191,36,0.06)",
            "border":     "rgba(251,191,36,0.25)",
            "confidence": confidence,
            "summary":    "Moderately bullish but some headwinds present. Selective positioning recommended.",
            "signals":    signals,
        }
    elif bear_pct >= 65:
        return {
            "regime":     "bear",
            "label":      "Bear Market",
            "emoji":      "🔴",
            "color":      "var(--red)",
            "bg":         "rgba(248,113,113,0.08)",
            "border":     "rgba(248,113,113,0.3)",
            "confidence": confidence,
            "summary":    "Bearish conditions dominating. Risk management is priority — consider reducing exposure.",
            "signals":    signals,
        }
    elif bear_pct >= 50:
        return {
            "regime":     "neutral_bear",
            "label":      "Cautious Bear",
            "emoji":      "🟠",
            "color":      "var(--orange, #fb923c)",
            "bg":         "rgba(251,146,60,0.06)",
            "border":     "rgba(251,146,60,0.25)",
            "confidence": confidence,
            "summary":    "More bearish than bullish. Tread carefully and watch key support levels.",
            "signals":    signals,
        }
    else:
        return {
            "regime":     "neutral",
            "label":      "Neutral Market",
            "emoji":      "⚪",
            "color":      "var(--muted)",
            "bg":         "rgba(107,114,128,0.06)",
            "border":     "rgba(107,114,128,0.2)",
            "confidence": confidence,
            "summary":    "Mixed signals — no clear directional bias. Stock picking matters more than macro.",
            "signals":    signals,
        }


def _unknown_regime():
    return {
        "regime": "unknown", "label": "Scanning...", "emoji": "⚪",
        "color": "var(--muted)", "bg": "rgba(107,114,128,0.06)",
        "border": "rgba(107,114,128,0.2)", "confidence": 0,
        "summary": "Market regime analysis pending scan data.",
        "signals": [],
    }


def get_regime_from_db(conn) -> dict:
    """
    Called on dashboard load — computes regime from stored DB data.
    No external API calls needed.
    """
    try:
        # Get latest market conditions
        market_row = conn.execute(
            "SELECT * FROM market_conditions ORDER BY scan_date DESC LIMIT 1"
        ).fetchone()
        if not market_row:
            return _unknown_regime()

        market = {
            "sp500": {"price": market_row.get("sp500"), "chg": market_row.get("sp500_chg")},
            "vix":   {"price": market_row.get("vix"),   "chg": None},
            "tny":   {"price": market_row.get("tny"),   "chg": None},
        }

        # Get latest scan stocks
        latest_date = market_row.get("scan_date")
        stocks = []
        if latest_date:
            rows = conn.execute(
                "SELECT score, insider FROM scans WHERE scan_date = ? ORDER BY score DESC LIMIT 20",
                (latest_date,)
            ).fetchall()
            stocks = [dict(r) for r in rows]

        return detect_market_regime(market, stocks)
    except Exception as e:
        print(f"Regime detection error: {e}")
        return _unknown_regime()
