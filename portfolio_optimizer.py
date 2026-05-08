"""
portfolio_optimizer.py
======================
Analyzes portfolio holdings for:
  - Sector concentration risk
  - Diversification score (0-100)
  - Correlation warnings (too many tech stocks etc)
  - Rebalancing suggestions
  - Overall portfolio health score

Called from the /portfolio route in app.py.
Uses only data already in the DB — no extra API calls.
"""


# Sector target weights (loosely based on S&P 500 composition)
SECTOR_TARGETS = {
    "Technology":             0.28,
    "Financial Services":     0.13,
    "Healthcare":             0.12,
    "Consumer Cyclical":      0.10,
    "Industrials":            0.09,
    "Communication Services": 0.08,
    "Consumer Defensive":     0.07,
    "Energy":                 0.05,
    "Utilities":              0.03,
    "Real Estate":            0.03,
    "Basic Materials":        0.02,
}

MAX_SINGLE_STOCK_PCT  = 25   # flag if one stock > 25% of portfolio
MAX_SINGLE_SECTOR_PCT = 40   # flag if one sector > 40% of portfolio
MIN_POSITIONS         = 4    # flag if fewer than 4 holdings
IDEAL_POSITIONS       = 8    # ideal number of positions


def get_holding_signal(scan_score, gain_pct, streak=0) -> dict:
    """Returns a buy/sell/hold signal for a single holding."""
    if scan_score is None:
        return {"emoji": "⚪", "label": "No Data", "color": "var(--muted)",
                "detail": "Not yet in any scan"}
    score = int(scan_score)
    gain  = gain_pct or 0
    if score >= 70 and gain > -5:
        return {"emoji": "🔥", "label": "Strong Hold", "color": "var(--green)",
                "detail": f"Score {score} — high conviction, consider adding"}
    elif score >= 55 and gain > -10:
        return {"emoji": "✅", "label": "Hold", "color": "var(--green)",
                "detail": f"Score {score} — positive signals"}
    elif gain > 25 and score < 50:
        return {"emoji": "💰", "label": "Take Profit", "color": "var(--yellow)",
                "detail": f"Up {gain:.0f}% but score dropped to {score} — trim?"}
    elif gain < -15 and score < 45:
        return {"emoji": "🔴", "label": "Consider Exit", "color": "var(--red)",
                "detail": f"Down {abs(gain):.0f}% with weak score {score} — review thesis"}
    elif score < 45:
        return {"emoji": "👀", "label": "Watch", "color": "var(--yellow)",
                "detail": f"Score {score} — signals weakening, monitor closely"}
    else:
        return {"emoji": "🟡", "label": "Neutral", "color": "var(--muted)",
                "detail": f"Score {score} — mixed signals, hold"}


def analyze_portfolio(holdings: list, conn=None) -> dict:
    """
    Analyzes a list of portfolio holdings and returns optimization data.

    Args:
        holdings: list of dicts with keys:
            ticker, shares, buy_price, current_value, cost_basis, scan_score
        conn: DB connection (optional, for sector lookup)

    Returns:
        dict with health_score, issues, suggestions, sector_breakdown, alerts
    """
    if not holdings:
        return _empty_result()

    total_value = sum(h.get("current_value", 0) for h in holdings)
    if total_value <= 0:
        return _empty_result()

    issues      = []
    suggestions = []
    positives   = []

    # ── Get sectors for each holding ──────────────────────────────────────
    for h in holdings:
        h["sector"] = _get_sector(h["ticker"], conn)

    # ── Position concentration ────────────────────────────────────────────
    for h in holdings:
        pct = h["current_value"] / total_value * 100
        h["portfolio_pct"] = round(pct, 1)
        if pct > MAX_SINGLE_STOCK_PCT:
            issues.append({
                "type":    "concentration",
                "level":   "high",
                "message": f"{h['ticker']} is {pct:.0f}% of your portfolio — overconcentrated",
                "action":  f"Consider trimming {h['ticker']} to under {MAX_SINGLE_STOCK_PCT}%"
            })

    # ── Sector breakdown ──────────────────────────────────────────────────
    sector_values = {}
    for h in holdings:
        sec = h.get("sector", "Unknown")
        sector_values[sec] = sector_values.get(sec, 0) + h["current_value"]

    sector_breakdown = []
    for sec, val in sorted(sector_values.items(), key=lambda x: x[1], reverse=True):
        pct    = val / total_value * 100
        target = SECTOR_TARGETS.get(sec, 0.05) * 100
        diff   = pct - target
        status = "over" if diff > 10 else ("under" if diff < -10 else "ok")
        sector_breakdown.append({
            "sector": sec,
            "value":  round(val, 2),
            "pct":    round(pct, 1),
            "target": round(target, 1),
            "diff":   round(diff, 1),
            "status": status,
        })
        if pct > MAX_SINGLE_SECTOR_PCT:
            issues.append({
                "type":    "sector_concentration",
                "level":   "medium",
                "message": f"{sec} is {pct:.0f}% of your portfolio (target: ~{target:.0f}%)",
                "action":  f"Diversify out of {sec} — add exposure to underweight sectors"
            })

    # ── Number of positions ───────────────────────────────────────────────
    n = len(holdings)
    if n < MIN_POSITIONS:
        issues.append({
            "type":    "undiversified",
            "level":   "high",
            "message": f"Only {n} holding{'s' if n != 1 else ''} — very undiversified",
            "action":  f"Add more positions to reduce single-stock risk. Aim for {IDEAL_POSITIONS}+"
        })
    elif n < IDEAL_POSITIONS:
        issues.append({
            "type":    "undiversified",
            "level":   "low",
            "message": f"{n} positions is a bit concentrated",
            "action":  f"Consider adding {IDEAL_POSITIONS - n} more positions for better diversification"
        })

    # ── Low scan scores ───────────────────────────────────────────────────
    low_score_holds = [h for h in holdings if h.get("scan_score") and h["scan_score"] < 35]
    for h in low_score_holds:
        issues.append({
            "type":    "weak_signal",
            "level":   "low",
            "message": f"{h['ticker']} has a low Convergence score ({int(h['scan_score'])})",
            "action":  f"Review your thesis on {h['ticker']} — weak multi-source consensus"
        })

    # ── Strong holdings ───────────────────────────────────────────────────
    strong = [h for h in holdings if h.get("scan_score") and h["scan_score"] >= 65]
    if strong:
        tickers = ", ".join(h["ticker"] for h in strong)
        positives.append(f"Strong consensus on {tickers} — these are high-conviction holds")

    # ── Gain/loss analysis ────────────────────────────────────────────────
    total_cost  = sum(h.get("cost_basis", 0) for h in holdings)
    total_gain  = total_value - total_cost
    gain_pct    = (total_gain / total_cost * 100) if total_cost else 0

    if gain_pct > 20:
        positives.append(f"Portfolio up {gain_pct:.1f}% overall — consider taking some profits on biggest winners")
    elif gain_pct < -15:
        issues.append({
            "type":    "drawdown",
            "level":   "medium",
            "message": f"Portfolio down {abs(gain_pct):.1f}% — significant drawdown",
            "action":  "Review each holding's thesis. Cut losers with weak signals."
        })

    # ── Suggestions ───────────────────────────────────────────────────────
    # Find most underweight sectors (good places to add)
    underweight = [s for s in sector_breakdown if s["diff"] < -8 and s["sector"] != "Unknown"]
    if underweight:
        top_uw = underweight[:2]
        for uw in top_uw:
            suggestions.append(f"Consider adding {uw['sector']} exposure — currently {uw['pct']:.0f}% vs {uw['target']:.0f}% target")

    if not suggestions and not issues:
        suggestions.append("Portfolio looks well balanced! Keep monitoring your positions.")

    # ── Health score (0-100) ──────────────────────────────────────────────
    health_score = 100

    # Deduct for issues
    for issue in issues:
        if issue["level"] == "high":   health_score -= 20
        elif issue["level"] == "medium": health_score -= 10
        elif issue["level"] == "low":    health_score -= 5

    # Boost for positives
    health_score += len(positives) * 5

    # Boost for good diversification
    if n >= IDEAL_POSITIONS:
        health_score += 10
    if len(sector_values) >= 4:
        health_score += 5

    health_score = max(0, min(100, health_score))

    # Health label
    if health_score >= 75:
        health_label = "Healthy"
        health_color = "var(--green)"
        health_emoji = "✅"
    elif health_score >= 50:
        health_label = "Fair"
        health_color = "var(--yellow)"
        health_emoji = "⚠️"
    else:
        health_label = "Needs Attention"
        health_color = "var(--red)"
        health_emoji = "🔴"

    return {
        "health_score":     health_score,
        "health_label":     health_label,
        "health_color":     health_color,
        "health_emoji":     health_emoji,
        "issues":           issues,
        "suggestions":      suggestions,
        "positives":        positives,
        "sector_breakdown": sector_breakdown,
        "num_positions":    n,
        "total_value":      round(total_value, 2),
        "total_gain_pct":   round(gain_pct, 1),
        "holdings":         holdings,
    }


def _get_sector(ticker: str, conn) -> str:
    """Look up sector from scans DB — no API call needed."""
    if not conn:
        return "Unknown"
    try:
        row = conn.execute(
            "SELECT sector FROM scans WHERE ticker = ? ORDER BY scan_date DESC LIMIT 1",
            (ticker,)
        ).fetchone()
        if row and row["sector"] and row["sector"] != "Unknown":
            return row["sector"]
    except Exception:
        pass
    # Fallback map for common tickers
    SECTOR_MAP = {
        "AAPL":"Technology","MSFT":"Technology","NVDA":"Technology","AMZN":"Consumer Cyclical",
        "META":"Communication Services","GOOGL":"Communication Services","GOOG":"Communication Services",
        "JPM":"Financial Services","BAC":"Financial Services","GS":"Financial Services","V":"Financial Services","MA":"Financial Services",
        "LLY":"Healthcare","UNH":"Healthcare","JNJ":"Healthcare","ABBV":"Healthcare","MRK":"Healthcare",
        "XOM":"Energy","CVX":"Energy",
        "COST":"Consumer Defensive","WMT":"Consumer Defensive","PG":"Consumer Defensive",
        "AVGO":"Technology","AMD":"Technology","QCOM":"Technology","INTC":"Technology","TXN":"Technology",
        "TSLA":"Consumer Cyclical","HD":"Consumer Cyclical",
        "CAT":"Industrials","GE":"Industrials","RTX":"Industrials",
    }
    return SECTOR_MAP.get(ticker.upper(), "Unknown")


def _empty_result():
    return {
        "health_score": 0, "health_label": "No Holdings", "health_color": "var(--muted)",
        "health_emoji": "💼", "issues": [], "suggestions": ["Add your first holding to get optimization insights."],
        "positives": [], "sector_breakdown": [], "num_positions": 0,
        "total_value": 0, "total_gain_pct": 0, "holdings": [],
    }
