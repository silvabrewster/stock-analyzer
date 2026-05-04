"""
Stock Convergence Web App
==========================
Flask web dashboard for the Stock Convergence Analyzer.
Shows daily results, history, and market conditions.

Run locally:  python app.py
Production:   gunicorn app:app
"""

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from functools import wraps
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "stockconvergence2026")

# ── config ────────────────────────────────────────────────────────────────────

APP_PASSWORD = os.environ.get("APP_PASSWORD", "convergence2026")

# ── database ──────────────────────────────────────────────────────────────────

def get_db():
    from database import get_db as _get_db
    return _get_db()

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date   TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            score       REAL,
            sources     TEXT,
            yahoo_sb    TEXT,
            zacks       TEXT,
            morningstar TEXT,
            insider     TEXT,
            eps_rev     TEXT,
            beats_sp    TEXT,
            price       REAL,
            upside_pct  REAL,
            beta        REAL,
            div_yield   REAL,
            week52_pos  REAL,
            short_pct   REAL,
            vol_spike   TEXT,
            streak      INTEGER,
            is_new      TEXT,
            sector      TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_conditions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            sp500     REAL,
            sp500_chg REAL,
            vix       REAL,
            tny       REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS penny_scans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date    TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            name         TEXT,
            tier         TEXT,
            score        REAL,
            price        REAL,
            mkt_cap      TEXT,
            vol_spike    TEXT,
            vol_ratio    TEXT,
            breakout     TEXT,
            week52_range TEXT,
            beats_mkt    TEXT,
            mo_return    TEXT,
            insider_buy  TEXT,
            short_squeeze TEXT,
            short_float  TEXT,
            signals      TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_scan_to_db(df, market: dict):
    """Called by scheduler to persist results."""
    conn    = get_db()
    today   = datetime.now().strftime("%Y-%m-%d")

    # Clear today's data (avoid dupes on re-run)
    conn.execute("DELETE FROM scans WHERE scan_date = ?", (today,))
    conn.execute("DELETE FROM market_conditions WHERE scan_date = ?", (today,))

    # Save market conditions
    sp  = market.get("sp500", {})
    vix = market.get("vix", {})
    tny = market.get("tny", {})
    conn.execute("""
        INSERT INTO market_conditions (scan_date, sp500, sp500_chg, vix, tny)
        VALUES (?,?,?,?,?)
    """, (today,
          sp.get("price"), sp.get("chg"),
          vix.get("price"), tny.get("price")))

    # Save top 20 stocks
    for _, row in df.head(20).iterrows():
        def safe(key, default=None):
            v = row.get(key, default)
            if v in ("n/a", "–", "", None):
                return default
            try:
                return float(str(v).replace("%","").replace("$","").strip())
            except Exception:
                return str(v) if isinstance(v, str) else default

        streak_raw = str(row.get("Streak", "0")).replace("🔥","").replace("d","").strip()
        try:
            streak_int = int(streak_raw)
        except Exception:
            streak_int = 0

        conn.execute("""
            INSERT INTO scans
            (scan_date, ticker, score, sources, yahoo_sb, zacks, morningstar,
             insider, eps_rev, beats_sp, price, upside_pct, beta, div_yield,
             week52_pos, short_pct, vol_spike, streak, is_new, sector)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            today,
            row.get("Ticker"),
            safe("Consensus Score"),
            row.get("Sources Agree"),
            row.get("Yahoo SB"),
            row.get("Zacks #1"),
            row.get("Morningstar ★★★"),
            row.get("Insider Buy"),
            row.get("EPS Rev ↑"),
            row.get("Beats S&P"),
            safe("Price"),
            safe("Upside %"),
            safe("Beta"),
            safe("Div Yield"),
            safe("52w Position"),
            safe("Short %"),
            "1" if row.get("Vol Spike") else "0",
            streak_int,
            row.get("New?", ""),
            row.get("Sector", "Unknown"),
        ))

    conn.commit()
    conn.close()

# ── auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Wrong password. Try again."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    conn      = get_db()
    today     = datetime.now().strftime("%Y-%m-%d")

    # Get latest scan date
    row = conn.execute(
        "SELECT MAX(scan_date) as latest FROM scans"
    ).fetchone()
    latest_date = row["latest"] if row and row["latest"] else today

    # Top stocks for latest date
    stocks = conn.execute("""
        SELECT * FROM scans
        WHERE scan_date = ?
        ORDER BY score DESC
        LIMIT 20
    """, (latest_date,)).fetchall()

    # Market conditions
    market = conn.execute("""
        SELECT * FROM market_conditions
        WHERE scan_date = ?
    """, (latest_date,)).fetchone()

    # Scan dates for history dropdown
    dates = conn.execute("""
        SELECT DISTINCT scan_date FROM scans
        ORDER BY scan_date DESC LIMIT 30
    """).fetchall()

    # Streak leaders (stocks with highest streak)
    streaks = conn.execute("""
        SELECT ticker, MAX(streak) as max_streak, MAX(score) as score
        FROM scans WHERE streak > 1
        GROUP BY ticker ORDER BY max_streak DESC LIMIT 5
    """).fetchall()

    conn.close()
    return render_template("dashboard.html",
        stocks=stocks, market=market,
        latest_date=latest_date, dates=dates,
        streaks=streaks)

@app.route("/history")
@login_required
def history():
    conn = get_db()
    # Get top ticker across all time by avg score
    top_all_time = conn.execute("""
        SELECT ticker, COUNT(*) as appearances,
               AVG(score) as avg_score, MAX(score) as max_score,
               MAX(streak) as max_streak
        FROM scans
        GROUP BY ticker
        HAVING appearances >= 2
        ORDER BY avg_score DESC LIMIT 20
    """).fetchall()

    # Daily top ticker history
    daily = conn.execute("""
        SELECT scan_date, ticker, score, sources, upside_pct, sector
        FROM scans
        WHERE score = (SELECT MAX(score) FROM scans s2 WHERE s2.scan_date = scans.scan_date)
        ORDER BY scan_date DESC LIMIT 30
    """).fetchall()

    conn.close()
    return render_template("history.html", top_all_time=top_all_time, daily=daily)

@app.route("/api/scores/<date>")
@login_required
def api_scores(date):
    conn    = get_db()
    stocks  = conn.execute(
        "SELECT ticker, score FROM scans WHERE scan_date = ? ORDER BY score DESC",
        (date,)
    ).fetchall()
    conn.close()
    return jsonify([dict(s) for s in stocks])

@app.route("/api/ticker/<ticker>")
@login_required
def api_ticker(ticker):
    conn = get_db()
    rows = conn.execute("""
        SELECT scan_date, score, upside_pct, price, streak
        FROM scans WHERE ticker = ?
        ORDER BY scan_date DESC LIMIT 30
    """, (ticker.upper(),)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── dev seed (optional) ───────────────────────────────────────────────────────

@app.route("/checklist")
@login_required
def checklist():
    return render_template("checklist.html")

@app.route("/momentum")
@login_required
def momentum():
    conn = get_db()
    row  = conn.execute("SELECT MAX(scan_date) as latest FROM penny_scans").fetchone()
    last = row["latest"] if row and row["latest"] else None
    stocks = []
    if last:
        rows = conn.execute(
            "SELECT * FROM penny_scans WHERE scan_date = ? ORDER BY score DESC",
            (last,)
        ).fetchall()
        stocks = [dict(r) for r in rows]
    conn.close()
    return render_template("momentum.html", stocks=stocks, last_scan=last)

@app.route("/momentum/scan", methods=["POST"])
@login_required
def momentum_scan():
    """Runs penny stock scan directly and saves results."""
    try:
        from penny_scanner import run_penny_scanner
        df    = run_penny_scanner()
        today = datetime.now().strftime("%Y-%m-%d")
        conn  = get_db()
        conn.execute("DELETE FROM penny_scans WHERE scan_date = ?", (today,))
        for _, row in df.iterrows():
            conn.execute("""
                INSERT INTO penny_scans
                (scan_date, ticker, name, tier, score, price, mkt_cap,
                 vol_spike, vol_ratio, breakout, week52_range, beats_mkt,
                 mo_return, insider_buy, short_squeeze, short_float, signals)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                today,
                row.get("Ticker"), row.get("Name"), row.get("Tier"),
                row.get("Score"), row.get("Price"), row.get("Mkt Cap"),
                row.get("Vol Spike"), row.get("Vol Ratio"),
                row.get("Breakout"), row.get("52w Range%"),
                row.get("Beats Mkt"), row.get("1mo Return"),
                row.get("Insider Buy"), row.get("Short Squeeze"),
                row.get("Short Float%"), row.get("Signals"),
            ))
        conn.commit()
        conn.close()
        print("✓ Penny scan complete")
    except Exception as e:
        print(f"Penny scan error: {e}")
    return redirect(url_for("momentum"))

@app.route("/momentum/status")
@login_required
def momentum_status():
    return jsonify({"done": True})

@app.route("/analyze")
@login_required
def analyze():
    return render_template("analyze.html")

@app.route("/api/analyze/<ticker>")
@login_required
def api_analyze(ticker):
    """Full stock analysis with risk rating and AI summary."""
    import yfinance as yf
    import requests as req

    try:
        t    = yf.Ticker(ticker.upper())
        info = t.info

        if not info or not info.get("regularMarketPrice") and not info.get("currentPrice"):
            return jsonify({"error": "Ticker not found"})

        # Basic info
        price       = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        prev_close  = info.get("previousClose") or price
        price_chg   = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
        name        = info.get("longName") or info.get("shortName", ticker)
        sector      = info.get("sector", "Unknown")
        industry    = info.get("industry", "")
        mkt_cap     = info.get("marketCap", 0) or 0
        price_target = info.get("targetMeanPrice")
        upside      = round((price_target - price) / price * 100, 1) if price_target and price else None

        # Financials
        pe            = info.get("trailingPE")
        forward_pe    = info.get("forwardPE")
        revenue_growth = round((info.get("revenueGrowth") or 0) * 100, 1)
        profit_margin  = round((info.get("profitMargins") or 0) * 100, 1)
        roe            = round((info.get("returnOnEquity") or 0) * 100, 1)
        debt_equity    = info.get("debtToEquity")
        if debt_equity: debt_equity = round(debt_equity / 100, 2)
        beta           = info.get("beta")
        div_yield      = round((info.get("dividendYield") or 0) * 100, 2)
        short_pct      = round((info.get("shortPercentOfFloat") or 0) * 100, 1)
        num_analysts   = info.get("numberOfAnalystOpinions", 0)
        rec_mean       = info.get("recommendationMean")
        avg_vol        = info.get("averageVolume", 0) or 0
        cur_vol        = info.get("volume", 0) or 0
        vol_ratio      = round(cur_vol / avg_vol, 1) if avg_vol else 0
        vol_spike      = vol_ratio >= 2.0
        high52         = info.get("fiftyTwoWeekHigh")
        low52          = info.get("fiftyTwoWeekLow")
        week52_pos     = round((price - low52) / (high52 - low52) * 100, 1) if high52 and low52 and (high52-low52) > 0 else None

        # Mkt cap label
        if mkt_cap >= 1e12:   cap_label, cap_tier = f"${mkt_cap/1e12:.1f}T", "Mega Cap"
        elif mkt_cap >= 1e9:  cap_label, cap_tier = f"${mkt_cap/1e9:.1f}B", "Large Cap"
        elif mkt_cap >= 1e8:  cap_label, cap_tier = f"${mkt_cap/1e8:.0f}M (x100)", "Mid Cap"
        elif mkt_cap >= 1e6:  cap_label, cap_tier = f"${mkt_cap/1e6:.0f}M", "Small Cap"
        else:                 cap_label, cap_tier = "< $1M", "Micro Cap"

        # ── Signal scores ──────────────────────────────────────────────────

        # Analyst score (0-100)
        analyst_score = 0
        analyst_label = "No data"
        if rec_mean and num_analysts >= 3:
            analyst_score = max(0, round((5 - rec_mean) / 4 * 100))
            analyst_label = ["", "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"][min(5, round(rec_mean))]

        # Valuation score (0-100)
        valuation_score = 50
        valuation_label = "Fair"
        if pe and pe > 0:
            if pe < 15:   valuation_score, valuation_label = 85, "Undervalued"
            elif pe < 25: valuation_score, valuation_label = 65, "Reasonable"
            elif pe < 40: valuation_score, valuation_label = 40, "Elevated"
            else:         valuation_score, valuation_label = 20, "Expensive"
        if upside:
            if upside > 20:   valuation_score = min(100, valuation_score + 20)
            elif upside < 0:  valuation_score = max(0,   valuation_score - 20)

        # Profitability score (0-100)
        profit_score = 0
        if profit_margin > 20: profit_score += 40
        elif profit_margin > 10: profit_score += 25
        elif profit_margin > 0:  profit_score += 10
        if roe > 20: profit_score += 35
        elif roe > 10: profit_score += 20
        elif roe > 0:  profit_score += 10
        if revenue_growth > 15: profit_score += 25
        elif revenue_growth > 5: profit_score += 15
        elif revenue_growth > 0: profit_score += 5
        profit_label = "Strong" if profit_score >= 65 else ("Moderate" if profit_score >= 40 else "Weak")

        # Financial health score (0-100)
        health_score = 50
        if debt_equity is not None:
            if debt_equity < 0.3:   health_score += 30
            elif debt_equity < 1.0: health_score += 15
            elif debt_equity > 2.0: health_score -= 20
        if div_yield > 0: health_score += 10
        health_score = max(0, min(100, health_score))
        health_label = "Strong" if health_score >= 65 else ("Moderate" if health_score >= 40 else "Weak")

        # Momentum score (0-100)
        momentum_score = 0
        if week52_pos:
            momentum_score += min(50, round(week52_pos / 2))
        if vol_spike: momentum_score += 25
        if revenue_growth > 10: momentum_score += 25
        momentum_score = min(100, momentum_score)
        momentum_label = "Strong" if momentum_score >= 65 else ("Moderate" if momentum_score >= 40 else "Weak")

        # ── Risk score (0-100, higher = riskier) ──────────────────────────
        risk_score = 30  # base

        # Beta risk
        if beta:
            if beta > 2.0:   risk_score += 25
            elif beta > 1.5: risk_score += 15
            elif beta > 1.2: risk_score += 8
            elif beta < 0.5: risk_score += 5  # very low beta also unusual

        # Valuation risk
        if pe and pe > 50: risk_score += 15
        elif pe and pe > 30: risk_score += 8

        # Debt risk
        if debt_equity and debt_equity > 2.0: risk_score += 15
        elif debt_equity and debt_equity > 1.0: risk_score += 8

        # Short interest risk
        if short_pct > 25: risk_score += 15
        elif short_pct > 15: risk_score += 8

        # Size risk
        if mkt_cap < 1e9:  risk_score += 15
        elif mkt_cap < 5e9: risk_score += 8

        # Negative margin risk
        if profit_margin < 0: risk_score += 10

        # Offset for quality
        if profit_margin > 20: risk_score -= 8
        if roe > 20:           risk_score -= 5
        if num_analysts > 10:  risk_score -= 5

        risk_score = max(5, min(95, risk_score))

        risk_summary = (
            f"{name} is {'low' if risk_score<=25 else ('moderately' if risk_score<=50 else ('highly' if risk_score<=75 else 'very highly'))} risky. "
            f"{'Strong fundamentals and large market cap provide stability. ' if mkt_cap > 1e11 and profit_margin > 10 else ''}"
            f"{'High beta ({:.1f}x) means bigger swings than the market. '.format(beta) if beta and beta > 1.5 else ''}"
            f"{'Elevated short interest ({:.0f}%) adds volatility risk. '.format(short_pct) if short_pct > 15 else ''}"
            f"{'High valuation (P/E {:.0f}x) leaves little margin for error. '.format(pe) if pe and pe > 40 else ''}"
            f"{'Small market cap increases volatility. ' if mkt_cap < 2e9 else ''}"
        )

        # ── AI analysis via Claude API ─────────────────────────────────────
        ai_analysis = ""
        try:
            prompt = f"""You are a concise stock analyst. Analyze {ticker} ({name}) based on these metrics:
- Price: ${price:.2f}, P/E: {pe}, Forward P/E: {forward_pe}
- Revenue Growth: {revenue_growth}%, Profit Margin: {profit_margin}%, ROE: {roe}%
- Debt/Equity: {debt_equity}, Beta: {beta}, Short Interest: {short_pct}%
- Analyst Rating: {analyst_label} ({num_analysts} analysts), Price Target: ${price_target} ({upside}% upside)
- Market Cap: {cap_label} ({cap_tier}), Sector: {sector}
- Risk Score: {risk_score}/100
- Dividend Yield: {div_yield}%

Write 3 short paragraphs (no headers, no bullet points):
1. What this company does and its competitive position
2. Key strengths and what makes it attractive (or not) right now
3. Main risks and who this stock is appropriate for

Be direct, specific, and honest. Max 180 words total."""

            resp = req.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                ai_analysis = data["content"][0]["text"].strip()
        except Exception as e:
            ai_analysis = f"{name} operates in the {sector} sector. Based on the available metrics, the stock shows a risk score of {risk_score}/100 with {analyst_label} analyst consensus. Key metrics include a {profit_margin}% profit margin and {revenue_growth}% revenue growth. Always conduct thorough research before making investment decisions."

        return jsonify({
            "ticker": ticker, "name": name, "sector": sector, "industry": industry,
            "price": round(price, 2), "price_change_pct": price_chg,
            "price_target": price_target, "upside": upside,
            "pe": pe, "forward_pe": forward_pe,
            "revenue_growth": revenue_growth, "profit_margin": profit_margin,
            "roe": roe, "debt_equity": debt_equity, "beta": beta,
            "div_yield": div_yield, "short_pct": short_pct,
            "num_analysts": num_analysts, "analyst_label": analyst_label,
            "vol_ratio": vol_ratio, "vol_spike": vol_spike,
            "week52_pos": week52_pos,
            "mkt_cap_label": cap_label, "mkt_cap_tier": cap_tier,
            "analyst_score": analyst_score, "valuation_score": valuation_score,
            "valuation_label": valuation_label, "profit_score": profit_score,
            "profit_label": profit_label, "health_score": health_score,
            "health_label": health_label, "momentum_score": momentum_score,
            "momentum_label": momentum_label,
            "risk_score": risk_score, "risk_summary": risk_summary,
            "ai_analysis": ai_analysis,
        })

    except Exception as e:
        return jsonify({"error": str(e)})


# ── stock detail ──────────────────────────────────────────────────────────────

@app.route("/stock/<ticker>")
@login_required
def stock_detail(ticker):
    ticker = ticker.upper()
    conn   = get_db()

    # Full history
    history = conn.execute(
        "SELECT * FROM scans WHERE ticker = ? ORDER BY scan_date DESC LIMIT 60",
        (ticker,)
    ).fetchall()

    # Stats
    stats = conn.execute("""
        SELECT COUNT(*) as appearances, AVG(score) as avg_score,
               MAX(score) as max_score, MAX(streak) as max_streak
        FROM scans WHERE ticker = ?
    """, (ticker,)).fetchone()

    latest = history[0] if history else None
    latest_score  = int(latest["score"]) if latest else "–"
    current_streak = latest["streak"] if latest else 0
    appearances    = stats["appearances"] if stats else 0
    avg_score      = round(stats["avg_score"]) if stats and stats["avg_score"] else None
    max_streak     = stats["max_streak"] if stats else 0

    # Chart data
    chart_dates  = [row["scan_date"] for row in reversed(list(history))]
    chart_scores = [row["score"] for row in reversed(list(history))]

    # Earnings warning via yfinance
    earnings_warning = None
    try:
        import yfinance as yf
        from datetime import timedelta
        t    = yf.Ticker(ticker)
        cal  = t.calendar
        if cal is not None and "Earnings Date" in cal:
            ed = cal["Earnings Date"]
            if hasattr(ed, '__iter__'):
                ed = list(ed)[0] if ed else None
            if ed:
                days_away = (pd.Timestamp(ed) - pd.Timestamp.now()).days
                if 0 <= days_away <= 14:
                    earnings_warning = f"in {days_away} day{'s' if days_away != 1 else ''} ({pd.Timestamp(ed).strftime('%b %d')})"
    except Exception:
        pass

    conn.close()
    return render_template("stock_detail.html",
        ticker=ticker, history=history, latest=latest,
        latest_score=latest_score, current_streak=current_streak,
        appearances=appearances, avg_score=avg_score, max_streak=max_streak,
        chart_dates=chart_dates, chart_scores=chart_scores,
        earnings_warning=earnings_warning)

# ── sector heatmap ────────────────────────────────────────────────────────────

@app.route("/sectors")
@login_required
def sectors():
    conn = get_db()
    row  = conn.execute("SELECT MAX(scan_date) as latest FROM scans").fetchone()
    latest = row["latest"] if row and row["latest"] else None

    sector_data = []
    if latest:
        rows = conn.execute("""
            SELECT sector, COUNT(*) as count, AVG(score) as avg_score,
                   MAX(score) as max_score
            FROM scans WHERE scan_date = ? AND sector IS NOT NULL AND sector != 'Unknown'
            GROUP BY sector ORDER BY avg_score DESC
        """, (latest,)).fetchall()

        for r in rows:
            top = conn.execute("""
                SELECT ticker FROM scans WHERE scan_date = ? AND sector = ?
                ORDER BY score DESC LIMIT 1
            """, (latest, r["sector"])).fetchone()

            tickers = conn.execute("""
                SELECT ticker FROM scans WHERE scan_date = ? AND sector = ?
                ORDER BY score DESC LIMIT 4
            """, (latest, r["sector"])).fetchall()

            sector_data.append({
                "sector":    r["sector"],
                "count":     r["count"],
                "avg_score": round(r["avg_score"]),
                "max_score": round(r["max_score"]),
                "top_ticker": top["ticker"] if top else "–",
                "tickers":   " · ".join([t["ticker"] for t in tickers]),
            })

    conn.close()
    return render_template("sectors.html", sectors=sector_data)

# ── watchlist ─────────────────────────────────────────────────────────────────

@app.route("/watchlist")
@login_required
def watchlist():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT NOT NULL UNIQUE,
            target_price REAL,
            notes        TEXT,
            added_date   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    items = conn.execute("SELECT * FROM watchlist ORDER BY added_date DESC").fetchall()
    result = []
    for w in items:
        ticker = w["ticker"]
        # Get latest scan score
        scan = conn.execute(
            "SELECT score FROM scans WHERE ticker = ? ORDER BY scan_date DESC LIMIT 1",
            (ticker,)
        ).fetchone()
        # Check if in today's scan
        today = datetime.now().strftime("%Y-%m-%d")
        in_scan = conn.execute(
            "SELECT 1 FROM scans WHERE ticker = ? AND scan_date = ?",
            (ticker, today)
        ).fetchone()
        # Get current price
        current_price = None
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info
            current_price = info.get("currentPrice") or info.get("regularMarketPrice")
            if current_price: current_price = round(current_price, 2)
        except Exception:
            pass

        result.append({
            "ticker":        ticker,
            "target_price":  w["target_price"],
            "notes":         w["notes"],
            "added_date":    w["added_date"][:10] if w["added_date"] else "",
            "latest_score":  scan["score"] if scan else None,
            "in_scan":       bool(in_scan),
            "current_price": current_price,
        })

    conn.close()
    return render_template("watchlist.html", watchlist=result)

@app.route("/watchlist/add", methods=["POST"])
@login_required
def watchlist_add():
    ticker       = request.form.get("ticker", "").upper().strip()
    target_price = request.form.get("target_price") or None
    notes        = request.form.get("notes", "").strip()
    if ticker:
        conn = get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                target_price REAL, notes TEXT,
                added_date TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO watchlist (ticker, target_price, notes)
            VALUES (?,?,?)
        """, (ticker, target_price, notes))
        conn.commit()
        conn.close()
    return redirect(url_for("watchlist"))

@app.route("/watchlist/remove/<ticker>", methods=["POST"])
@login_required
def watchlist_remove(ticker):
    conn = get_db()
    conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
    conn.commit()
    conn.close()
    return redirect(url_for("watchlist"))

# ── compare ───────────────────────────────────────────────────────────────────

@app.route("/compare")
@login_required
def compare():
    t1 = request.args.get("t1", "").upper().strip()
    t2 = request.args.get("t2", "").upper().strip()
    t3 = request.args.get("t3", "").upper().strip()

    stocks     = []
    best_score = 0
    best_upside = 0

    if t1 and t2:
        conn    = get_db()
        tickers = [t for t in [t1, t2, t3] if t]
        for ticker in tickers:
            row = conn.execute(
                "SELECT * FROM scans WHERE ticker = ? ORDER BY scan_date DESC LIMIT 1",
                (ticker,)
            ).fetchone()
            if row:
                stocks.append(dict(row))
                if row["score"] and row["score"] > best_score:
                    best_score = row["score"]
                if row["upside_pct"] and row["upside_pct"] > best_upside:
                    best_upside = row["upside_pct"]
        conn.close()

    return render_template("compare.html",
        t1=t1, t2=t2, t3=t3,
        stocks=stocks, best_score=best_score, best_upside=best_upside)

@app.route("/seed")
@login_required
def seed():
    """Seeds DB with sample data so you can preview the UI."""
    import random
    conn  = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute("DELETE FROM scans WHERE scan_date = ?", (today,))
    conn.execute("DELETE FROM market_conditions WHERE scan_date = ?", (today,))
    conn.execute("""
        INSERT INTO market_conditions (scan_date, sp500, sp500_chg, vix, tny)
        VALUES (?,?,?,?,?)
    """, (today, 5234.18, 0.43, 18.2, 4.31))
    sample = [
        ("MA", 85, "✓","✓","✓","✓","✓","✓", 510.20, 18.5, 1.1, 0.0, 72, 3.2, 5, "Financial Services"),
        ("NVDA", 80, "✓","–","✓","✓","✓","✓", 875.40, 22.1, 1.8, 0.0, 61, 2.1, 3, "Technology"),
        ("MSFT", 74, "✓","✓","✓","–","✓","–", 415.30, 12.4, 0.9, 0.7, 58, 1.8, 7, "Technology"),
        ("AVGO", 70, "✓","–","✓","✓","–","✓", 1340.00, 15.3, 1.2, 1.5, 45, 2.4, 2, "Technology"),
        ("JPM",  65, "✓","✓","–","–","✓","✓", 198.50, 9.8,  1.1, 2.2, 67, 0.9, 1, "Financial Services"),
        ("LLY",  60, "✓","–","✓","✓","–","–", 820.00, 19.2, 0.6, 0.8, 38, 1.1, 4, "Healthcare"),
        ("V",    55, "✓","✓","–","–","✓","–", 276.40, 11.1, 0.9, 0.7, 71, 0.8, 2, "Financial Services"),
        ("AAPL", 52, "✓","–","–","–","✓","✓", 189.30, 8.4,  1.2, 0.5, 44, 1.4, 0, "Technology"),
        ("COST", 48, "–","✓","✓","–","–","✓", 785.20, 7.2,  0.8, 0.6, 82, 0.5, 1, "Consumer Cyclical"),
        ("UNH",  44, "✓","–","–","✓","–","–", 512.00, 14.8, 0.7, 1.4, 29, 0.7, 0, "Healthcare"),
    ]
    for i, (t, sc, y, z, ms, ins, eps, rs, pr, up, bt, dv, w52, sp, stk, sec) in enumerate(sample):
        # count signals
        sig_count = sum([1 for x in [y,z,ms,ins,eps,rs] if x == "✓"])
        conn.execute("""
            INSERT INTO scans (scan_date,ticker,score,sources,yahoo_sb,zacks,morningstar,
            insider,eps_rev,beats_sp,price,upside_pct,beta,div_yield,week52_pos,
            short_pct,vol_spike,streak,is_new,sector)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (today,t,sc,f"{sig_count}/7",y,z,ms,ins,eps,rs,pr,up,bt,dv,w52,sp,"0",stk,"🆕" if stk==0 else "",sec))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))

# ── run ───────────────────────────────────────────────────────────────────────

# Initialize DB on startup (runs in Railway too)
init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
