"""
Stock Convergence Web App v3.3
================================
Speed fixes:
  - Batch price fetching (all tickers at once via yf.download)
  - Signal alignment removed from stock detail route (uses DB value only)
  - Alert check cached for 10 min (not every dashboard load)
  - get_signal_alignment only called from analyzer.py during daily scan
  - price_cache uses upsert pattern for postgres safety
"""

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from functools import wraps
import os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "stockconvergence2026")
APP_PASSWORD   = os.environ.get("APP_PASSWORD", "convergence2026")

# ── database ──────────────────────────────────────────────────────────────────

def get_db():
    from database import get_db as _get_db
    return _get_db()

def init_db():
    conn = get_db()
    tables = [
        """CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT NOT NULL,
            ticker TEXT NOT NULL, score REAL, sources TEXT, yahoo_sb TEXT,
            zacks TEXT, morningstar TEXT, insider TEXT, eps_rev TEXT,
            beats_sp TEXT, price REAL, upside_pct REAL, beta REAL,
            div_yield REAL, week52_pos REAL, short_pct REAL, vol_spike TEXT,
            streak INTEGER, is_new TEXT, sector TEXT, alignment TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS market_conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT NOT NULL,
            sp500 REAL, sp500_chg REAL, vix REAL, tny REAL,
            regime TEXT, regime_label TEXT, regime_confidence INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS penny_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT NOT NULL,
            ticker TEXT NOT NULL, name TEXT, tier TEXT, score REAL, price REAL,
            mkt_cap TEXT, vol_spike TEXT, vol_ratio TEXT, breakout TEXT,
            week52_range TEXT, beats_mkt TEXT, mo_return TEXT, insider_buy TEXT,
            short_squeeze TEXT, short_float TEXT, signals TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL UNIQUE,
            shares REAL NOT NULL, buy_price REAL NOT NULL, notes TEXT,
            added_date TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL UNIQUE,
            target_price REAL, notes TEXT,
            added_date TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
            type TEXT NOT NULL, message TEXT NOT NULL, seen INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL, auth TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS price_cache (
            ticker TEXT PRIMARY KEY, price REAL NOT NULL,
            fetched_at TEXT NOT NULL)""",
    ]
    for sql in tables:
        conn.execute(sql)
    conn.commit()
    conn.close()

def save_scan_to_db(df, market: dict):
    conn  = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute("DELETE FROM scans WHERE scan_date = ?", (today,))
    conn.execute("DELETE FROM market_conditions WHERE scan_date = ?", (today,))
    sp  = market.get("sp500", {})
    vix = market.get("vix", {})
    tny = market.get("tny", {})
    conn.execute("""
        INSERT INTO market_conditions
        (scan_date, sp500, sp500_chg, vix, tny, ai_brief, regime, regime_label, regime_confidence)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (today, sp.get("price"), sp.get("chg"), vix.get("price"), tny.get("price"),
          market.get("ai_brief",""), market.get("regime","unknown"),
          market.get("regime_label",""), market.get("regime_confidence",0)))
    for _, row in df.head(20).iterrows():
        def safe(key, default=None):
            v = row.get(key, default)
            if v in ("n/a","–","",None): return default
            try: return float(str(v).replace("%","").replace("$","").strip())
            except: return str(v) if isinstance(v,str) else default
        streak_raw = str(row.get("Streak","0")).replace("🔥","").replace("d","").strip()
        try: streak_int = int(streak_raw)
        except: streak_int = 0
        conn.execute("""
            INSERT INTO scans (scan_date,ticker,score,sources,yahoo_sb,zacks,morningstar,
            insider,eps_rev,beats_sp,price,upside_pct,beta,div_yield,week52_pos,
            short_pct,vol_spike,streak,is_new,sector,alignment)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (today, row.get("Ticker"), safe("Consensus Score"), row.get("Sources Agree"),
              row.get("Yahoo SB"), row.get("Zacks #1"), row.get("Morningstar ★★★"),
              row.get("Insider Buy"), row.get("EPS Rev ↑"), row.get("Beats S&P"),
              safe("Price"), safe("Upside %"), safe("Beta"), safe("Div Yield"),
              safe("52w Position"), safe("Short %"),
              "1" if row.get("Vol Spike") else "0",
              streak_int, row.get("New?",""), row.get("Sector","Unknown"),
              row.get("Alignment","")))
    conn.commit()
    conn.close()

# ── batch price fetching (fast) ───────────────────────────────────────────────

def batch_fetch_prices(tickers: list, conn, max_age_minutes: int = 20) -> dict:
    """
    Fetch prices for multiple tickers efficiently.
    1. Check cache for fresh prices first
    2. Batch-download stale/missing ones via yf.download (much faster than per-ticker)
    Returns dict of {ticker: price}
    """
    import yfinance as yf
    now         = datetime.now()
    prices      = {}
    need_fetch  = []

    # Step 1: check cache
    for ticker in tickers:
        try:
            row = conn.execute(
                "SELECT price, fetched_at FROM price_cache WHERE ticker = ?", (ticker,)
            ).fetchone()
            if row:
                age = (now - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 60
                if age < max_age_minutes:
                    prices[ticker] = round(float(row["price"]), 2)
                    continue
        except Exception:
            pass
        need_fetch.append(ticker)

    # Step 2: batch download stale/missing tickers
    if need_fetch:
        try:
            # yf.download with multiple tickers is much faster than one by one
            raw = yf.download(
                need_fetch, period="1d", progress=False,
                auto_adjust=True, group_by="ticker"
            )
            fetched_at = now.isoformat()
            for ticker in need_fetch:
                try:
                    if len(need_fetch) == 1:
                        price = float(raw["Close"].iloc[-1])
                    else:
                        price = float(raw[ticker]["Close"].iloc[-1])
                    price = round(price, 2)
                    prices[ticker] = price
                    # Update cache
                    try:
                        conn.execute("""
                            INSERT INTO price_cache (ticker, price, fetched_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(ticker) DO UPDATE SET
                            price=excluded.price, fetched_at=excluded.fetched_at
                        """, (ticker, price, fetched_at))
                    except Exception:
                        pass
                except Exception:
                    # fallback: try fast_info for this one
                    try:
                        fi    = yf.Ticker(ticker).fast_info
                        price = fi.last_price or fi.regular_market_price
                        if price:
                            price = round(float(price), 2)
                            prices[ticker] = price
                            try:
                                conn.execute("""
                                    INSERT INTO price_cache (ticker, price, fetched_at)
                                    VALUES (?, ?, ?)
                                    ON CONFLICT(ticker) DO UPDATE SET
                                    price=excluded.price, fetched_at=excluded.fetched_at
                                """, (ticker, price, fetched_at))
                            except Exception:
                                pass
                    except Exception:
                        pass
            try:
                conn.commit()
            except Exception:
                pass
        except Exception as e:
            print(f"Batch price fetch error: {e}")
            # Fallback: per-ticker fast_info
            for ticker in need_fetch:
                try:
                    fi    = yf.Ticker(ticker).fast_info
                    price = fi.last_price or fi.regular_market_price
                    if price:
                        prices[ticker] = round(float(price), 2)
                except Exception:
                    pass

    return prices

# ── alert check cache (10 min) ────────────────────────────────────────────────

_last_alert_check = None

def maybe_check_alerts(conn):
    """Only run alert check once every 10 minutes to avoid slowing every dashboard load."""
    global _last_alert_check
    now = datetime.now()
    if _last_alert_check and (now - _last_alert_check).total_seconds() < 600:
        return  # skip — checked recently
    _last_alert_check = now
    try:
        from alerts import check_alerts
        check_alerts(conn)
    except Exception as e:
        print(f"Alert check error: {e}")

# ── auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/sw.js")
def service_worker():
    return app.send_static_file("sw.js"), 200, {"Content-Type": "application/javascript"}

@app.route("/login", methods=["GET","POST"])
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

# ── dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    conn        = get_db()
    today       = datetime.now().strftime("%Y-%m-%d")
    row         = conn.execute("SELECT MAX(scan_date) as latest FROM scans").fetchone()
    latest_date = row["latest"] if row and row["latest"] else today

    stocks = conn.execute(
        "SELECT * FROM scans WHERE scan_date = ? ORDER BY score DESC LIMIT 20",
        (latest_date,)
    ).fetchall()

    market = conn.execute(
        "SELECT * FROM market_conditions WHERE scan_date = ?", (latest_date,)
    ).fetchone()

    streaks = conn.execute("""
        SELECT ticker, MAX(streak) as max_streak, MAX(score) as score
        FROM scans WHERE streak > 1
        GROUP BY ticker ORDER BY max_streak DESC LIMIT 5
    """).fetchall()

    ai_brief = None
    try:
        brief_row = conn.execute(
            "SELECT ai_brief FROM market_conditions WHERE scan_date = ?", (latest_date,)
        ).fetchone()
        if brief_row and brief_row.get("ai_brief"):
            ai_brief = brief_row["ai_brief"]
    except Exception:
        pass

    # Market regime from DB (no API calls)
    regime = None
    try:
        from market_regime import get_regime_from_db
        regime = get_regime_from_db(conn)
    except Exception as e:
        print(f"Regime error: {e}")

    # Alert check — throttled to once per 10 min
    maybe_check_alerts(conn)

    conn.close()
    return render_template("dashboard.html",
        stocks=stocks, market=market,
        latest_date=latest_date, streaks=streaks,
        ai_brief=ai_brief, regime=regime)

# ── history ───────────────────────────────────────────────────────────────────

@app.route("/history")
@login_required
def history():
    conn = get_db()
    top_all_time = conn.execute("""
        SELECT ticker, COUNT(*) as appearances,
               AVG(score) as avg_score, MAX(score) as max_score, MAX(streak) as max_streak
        FROM scans GROUP BY ticker HAVING COUNT(*) >= 2
        ORDER BY AVG(score) DESC LIMIT 20
    """).fetchall()
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
    conn   = get_db()
    stocks = conn.execute("SELECT ticker, score FROM scans WHERE scan_date=? ORDER BY score DESC", (date,)).fetchall()
    conn.close()
    return jsonify([dict(s) for s in stocks])

@app.route("/api/ticker/<ticker>")
@login_required
def api_ticker(ticker):
    conn = get_db()
    rows = conn.execute("""
        SELECT scan_date, score, upside_pct, price, streak
        FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 30
    """, (ticker.upper(),)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── pages ─────────────────────────────────────────────────────────────────────

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
        rows   = conn.execute("SELECT * FROM penny_scans WHERE scan_date=? ORDER BY score DESC", (last,)).fetchall()
        stocks = [dict(r) for r in rows]
    conn.close()
    return render_template("momentum.html", stocks=stocks, last_scan=last)

@app.route("/momentum/scan", methods=["POST"])
@login_required
def momentum_scan():
    try:
        from penny_scanner import run_penny_scanner
        df    = run_penny_scanner()
        today = datetime.now().strftime("%Y-%m-%d")
        conn  = get_db()
        conn.execute("DELETE FROM penny_scans WHERE scan_date=?", (today,))
        for _, row in df.iterrows():
            conn.execute("""
                INSERT INTO penny_scans
                (scan_date,ticker,name,tier,score,price,mkt_cap,vol_spike,vol_ratio,
                 breakout,week52_range,beats_mkt,mo_return,insider_buy,short_squeeze,short_float,signals)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (today, row.get("Ticker"), row.get("Name"), row.get("Tier"),
                  row.get("Score"), row.get("Price"), row.get("Mkt Cap"),
                  row.get("Vol Spike"), row.get("Vol Ratio"), row.get("Breakout"),
                  row.get("52w Range%"), row.get("Beats Mkt"), row.get("1mo Return"),
                  row.get("Insider Buy"), row.get("Short Squeeze"),
                  row.get("Short Float%"), row.get("Signals")))
        conn.commit()
        conn.close()
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
    """Full analysis — uses fast_info for price, info for fundamentals."""
    import yfinance as yf
    import requests as req
    try:
        t = yf.Ticker(ticker.upper())

        # Fast price first
        try:
            fi    = t.fast_info
            price = float(fi.last_price or fi.regular_market_price or 0)
        except Exception:
            price = 0

        # Fundamentals from info (one call)
        info = t.info
        if not info or (not price and not info.get("regularMarketPrice") and not info.get("currentPrice")):
            return jsonify({"error": "Ticker not found"})

        if not price:
            price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)

        prev_close   = info.get("previousClose") or price
        price_chg    = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
        name         = info.get("longName") or info.get("shortName", ticker)
        sector       = info.get("sector", "Unknown")
        industry     = info.get("industry", "")
        mkt_cap      = info.get("marketCap", 0) or 0
        price_target = info.get("targetMeanPrice")
        upside       = round((price_target - price) / price * 100, 1) if price_target and price else None
        pe             = info.get("trailingPE")
        forward_pe     = info.get("forwardPE")
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
        week52_pos     = round((price-low52)/(high52-low52)*100,1) if high52 and low52 and (high52-low52)>0 else None

        if mkt_cap >= 1e12:   cap_label, cap_tier = f"${mkt_cap/1e12:.1f}T", "Mega Cap"
        elif mkt_cap >= 1e9:  cap_label, cap_tier = f"${mkt_cap/1e9:.1f}B", "Large Cap"
        elif mkt_cap >= 1e8:  cap_label, cap_tier = f"${mkt_cap/1e8:.0f}M (x100)", "Mid Cap"
        elif mkt_cap >= 1e6:  cap_label, cap_tier = f"${mkt_cap/1e6:.0f}M", "Small Cap"
        else:                 cap_label, cap_tier = "< $1M", "Micro Cap"

        analyst_score = 0; analyst_label = "No data"
        if rec_mean and num_analysts >= 3:
            analyst_score = max(0, round((5-rec_mean)/4*100))
            analyst_label = ["","Strong Buy","Buy","Hold","Sell","Strong Sell"][min(5,round(rec_mean))]

        valuation_score = 50; valuation_label = "Fair"
        if pe and pe > 0:
            if pe < 15:   valuation_score, valuation_label = 85, "Undervalued"
            elif pe < 25: valuation_score, valuation_label = 65, "Reasonable"
            elif pe < 40: valuation_score, valuation_label = 40, "Elevated"
            else:         valuation_score, valuation_label = 20, "Expensive"
        if upside:
            if upside > 20:  valuation_score = min(100, valuation_score+20)
            elif upside < 0: valuation_score = max(0, valuation_score-20)

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
        profit_label = "Strong" if profit_score>=65 else ("Moderate" if profit_score>=40 else "Weak")

        health_score = 50
        if debt_equity is not None:
            if debt_equity < 0.3:   health_score += 30
            elif debt_equity < 1.0: health_score += 15
            elif debt_equity > 2.0: health_score -= 20
        if div_yield > 0: health_score += 10
        health_score = max(0, min(100, health_score))
        health_label = "Strong" if health_score>=65 else ("Moderate" if health_score>=40 else "Weak")

        momentum_score = 0
        if week52_pos: momentum_score += min(50, round(week52_pos/2))
        if vol_spike: momentum_score += 25
        if revenue_growth > 10: momentum_score += 25
        momentum_score = min(100, momentum_score)
        momentum_label = "Strong" if momentum_score>=65 else ("Moderate" if momentum_score>=40 else "Weak")

        risk_score = 30
        if beta:
            if beta > 2.0:   risk_score += 25
            elif beta > 1.5: risk_score += 15
            elif beta > 1.2: risk_score += 8
            elif beta < 0.5: risk_score += 5
        if pe and pe > 50: risk_score += 15
        elif pe and pe > 30: risk_score += 8
        if debt_equity and debt_equity > 2.0: risk_score += 15
        elif debt_equity and debt_equity > 1.0: risk_score += 8
        if short_pct > 25: risk_score += 15
        elif short_pct > 15: risk_score += 8
        if mkt_cap < 1e9:  risk_score += 15
        elif mkt_cap < 5e9: risk_score += 8
        if profit_margin < 0: risk_score += 10
        if profit_margin > 20: risk_score -= 8
        if roe > 20:           risk_score -= 5
        if num_analysts > 10:  risk_score -= 5
        risk_score = max(5, min(95, risk_score))
        risk_summary = (
            f"{name} is {'low' if risk_score<=25 else ('moderately' if risk_score<=50 else ('highly' if risk_score<=75 else 'very highly'))} risky. "
            f"{'Strong fundamentals and large market cap provide stability. ' if mkt_cap>1e11 and profit_margin>10 else ''}"
            f"{'High beta ({:.1f}x) means bigger swings than the market. '.format(beta) if beta and beta>1.5 else ''}"
            f"{'Elevated short interest ({:.0f}%) adds volatility risk. '.format(short_pct) if short_pct>15 else ''}"
            f"{'High valuation (P/E {:.0f}x) leaves little margin for error. '.format(pe) if pe and pe>40 else ''}"
            f"{'Small market cap increases volatility. ' if mkt_cap<2e9 else ''}"
        )

        from features import get_smart_buy_rating
        smart_buy = get_smart_buy_rating(
            score=analyst_score, upside_pct=upside, short_pct=short_pct,
            week52_pos=week52_pos, beta=beta, price=price, price_target=price_target
        )

        # Alignment from DB only — no yf.download call here
        alignment = {}
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT alignment FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 1",
                (ticker.upper(),)
            ).fetchone()
            conn.close()
            if row and row["alignment"]:
                alignment = {"label": row["alignment"], "emoji": "🎯" if "Full" in row["alignment"] else ""}
        except Exception:
            pass

        ai_analysis = ""
        try:
            prompt = f"""You are a concise stock analyst. Analyze {ticker} ({name}):
- Price: ${price:.2f}, P/E: {pe}, Forward P/E: {forward_pe}
- Revenue Growth: {revenue_growth}%, Profit Margin: {profit_margin}%, ROE: {roe}%
- Debt/Equity: {debt_equity}, Beta: {beta}, Short Interest: {short_pct}%
- Analyst Rating: {analyst_label} ({num_analysts} analysts), Price Target: ${price_target} ({upside}% upside)
- Market Cap: {cap_label} ({cap_tier}), Sector: {sector}
- Risk Score: {risk_score}/100, Smart Buy: {smart_buy['label']}

Write 3 short paragraphs (no headers, no bullets):
1. What this company does and competitive position
2. Key strengths and what makes it attractive right now
3. Main risks and who this stock is appropriate for

Be direct and honest. Max 180 words."""
            resp = req.post("https://api.anthropic.com/v1/messages",
                headers={"Content-Type":"application/json"},
                json={"model":"claude-sonnet-4-20250514","max_tokens":300,
                      "messages":[{"role":"user","content":prompt}]},
                timeout=30)
            if resp.status_code == 200:
                ai_analysis = resp.json()["content"][0]["text"].strip()
        except Exception:
            ai_analysis = f"{name} operates in {sector}. Risk: {risk_score}/100. Research before investing."

        return jsonify({
            "ticker":ticker,"name":name,"sector":sector,"industry":industry,
            "price":round(price,2),"price_change_pct":price_chg,
            "price_target":price_target,"upside":upside,
            "pe":pe,"forward_pe":forward_pe,"revenue_growth":revenue_growth,
            "profit_margin":profit_margin,"roe":roe,"debt_equity":debt_equity,
            "beta":beta,"div_yield":div_yield,"short_pct":short_pct,
            "num_analysts":num_analysts,"analyst_label":analyst_label,
            "vol_ratio":vol_ratio,"vol_spike":vol_spike,"week52_pos":week52_pos,
            "mkt_cap_label":cap_label,"mkt_cap_tier":cap_tier,
            "analyst_score":analyst_score,"valuation_score":valuation_score,
            "valuation_label":valuation_label,"profit_score":profit_score,
            "profit_label":profit_label,"health_score":health_score,
            "health_label":health_label,"momentum_score":momentum_score,
            "momentum_label":momentum_label,"risk_score":risk_score,
            "risk_summary":risk_summary,"ai_analysis":ai_analysis,
            "smart_buy":smart_buy,"alignment":alignment,
        })
    except Exception as e:
        return jsonify({"error": str(e)})

# ── stock detail (fast — no yfinance on load) ─────────────────────────────────

@app.route("/stock/<ticker>")
@login_required
def stock_detail(ticker):
    ticker = ticker.upper()
    conn   = get_db()

    history = conn.execute(
        "SELECT * FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 60", (ticker,)
    ).fetchall()
    stats = conn.execute("""
        SELECT COUNT(*) as appearances, AVG(score) as avg_score,
               MAX(score) as max_score, MAX(streak) as max_streak
        FROM scans WHERE ticker=?
    """, (ticker,)).fetchone()

    latest         = history[0] if history else None
    latest_score   = int(latest["score"]) if latest else "–"
    current_streak = latest["streak"] if latest else 0
    appearances    = stats["appearances"] if stats else 0
    avg_score      = round(stats["avg_score"]) if stats and stats["avg_score"] else None
    max_streak     = stats["max_streak"] if stats else 0
    chart_dates    = [row["scan_date"] for row in reversed(list(history))]
    chart_scores   = [row["score"] for row in reversed(list(history))]

    # Smart buy from DB data — zero API calls
    smart_buy = None
    if latest:
        from features import get_smart_buy_rating
        smart_buy = get_smart_buy_rating(
            score=latest.get("score"), upside_pct=latest.get("upside_pct"),
            short_pct=latest.get("short_pct"), week52_pos=latest.get("week52_pos"),
            beta=latest.get("beta"), price=latest.get("price"), price_target=None
        )

    # Live price from cache only — no blocking fetch on page load
    # TradingView chart shows live price anyway
    live_price = None
    try:
        row = conn.execute(
            "SELECT price, fetched_at FROM price_cache WHERE ticker=?", (ticker,)
        ).fetchone()
        if row:
            age = (datetime.now() - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 60
            if age < 60:  # use cache up to 60 min old on stock detail
                live_price = round(float(row["price"]), 2)
    except Exception:
        pass

    # Alignment from DB — no yf.download
    alignment = None
    if latest and latest.get("alignment"):
        alignment = {"label": latest["alignment"]}

    conn.close()
    return render_template("stock_detail.html",
        ticker=ticker, history=history, latest=latest,
        latest_score=latest_score, current_streak=current_streak,
        appearances=appearances, avg_score=avg_score, max_streak=max_streak,
        chart_dates=chart_dates, chart_scores=chart_scores,
        earnings_warning=None, smart_buy=smart_buy,
        live_price=live_price, alignment=alignment)

# ── sectors ───────────────────────────────────────────────────────────────────

@app.route("/sectors")
@login_required
def sectors():
    conn   = get_db()
    row    = conn.execute("SELECT MAX(scan_date) as latest FROM scans").fetchone()
    latest = row["latest"] if row and row["latest"] else None
    sector_data = []
    if latest:
        rows = conn.execute("""
            SELECT sector, COUNT(*) as count, AVG(score) as avg_score, MAX(score) as max_score
            FROM scans WHERE scan_date=? AND sector IS NOT NULL AND sector!='Unknown'
            GROUP BY sector ORDER BY avg_score DESC
        """, (latest,)).fetchall()
        for r in rows:
            top     = conn.execute("SELECT ticker FROM scans WHERE scan_date=? AND sector=? ORDER BY score DESC LIMIT 1",(latest,r["sector"])).fetchone()
            tickers = conn.execute("SELECT ticker FROM scans WHERE scan_date=? AND sector=? ORDER BY score DESC LIMIT 4",(latest,r["sector"])).fetchall()
            sector_data.append({"sector":r["sector"],"count":r["count"],"avg_score":round(r["avg_score"]),"max_score":round(r["max_score"]),"top_ticker":top["ticker"] if top else "–","tickers":" · ".join([t["ticker"] for t in tickers])})
    conn.close()
    return render_template("sectors.html", sectors=sector_data)

# ── watchlist (batch prices) ──────────────────────────────────────────────────

@app.route("/watchlist")
@login_required
def watchlist():
    conn   = get_db()
    items  = conn.execute("SELECT * FROM watchlist ORDER BY added_date DESC").fetchall()
    today  = datetime.now().strftime("%Y-%m-%d")
    tickers = [w["ticker"] for w in items]

    # Batch fetch all prices at once
    prices = batch_fetch_prices(tickers, conn) if tickers else {}

    result = []
    for w in items:
        ticker  = w["ticker"]
        scan    = conn.execute("SELECT score FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 1",(ticker,)).fetchone()
        in_scan = conn.execute("SELECT 1 FROM scans WHERE ticker=? AND scan_date=?",(ticker,today)).fetchone()
        result.append({
            "ticker":        ticker,
            "target_price":  w["target_price"],
            "notes":         w["notes"],
            "added_date":    w["added_date"][:10] if w["added_date"] else "",
            "latest_score":  scan["score"] if scan else None,
            "in_scan":       bool(in_scan),
            "current_price": prices.get(ticker),
        })
    conn.close()
    return render_template("watchlist.html", watchlist=result)

@app.route("/watchlist/add", methods=["POST"])
@login_required
def watchlist_add():
    ticker = request.form.get("ticker","").upper().strip()
    target_price = request.form.get("target_price") or None
    notes = request.form.get("notes","").strip()
    if ticker:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO watchlist (ticker,target_price,notes) VALUES (?,?,?)",(ticker,target_price,notes))
        conn.commit(); conn.close()
    return redirect(url_for("watchlist"))

@app.route("/watchlist/remove/<ticker>", methods=["POST"])
@login_required
def watchlist_remove(ticker):
    conn = get_db()
    conn.execute("DELETE FROM watchlist WHERE ticker=?",(ticker.upper(),))
    conn.commit(); conn.close()
    return redirect(url_for("watchlist"))

# ── portfolio (batch prices + optimization) ───────────────────────────────────

@app.route("/portfolio")
@login_required
def portfolio():
    conn  = get_db()
    rows  = conn.execute("SELECT * FROM portfolio ORDER BY added_date DESC").fetchall()
    tickers = [row["ticker"] for row in rows]

    # Batch fetch all prices at once — much faster than one by one
    prices = batch_fetch_prices(tickers, conn) if tickers else {}

    holdings    = []
    total_value = 0
    total_cost  = 0

    for row in rows:
        ticker     = row["ticker"]
        shares     = row["shares"]
        buy_price  = row["buy_price"]
        cost_basis = shares * buy_price
        current_price = prices.get(ticker)
        current_value = shares * (current_price or buy_price)
        scan = conn.execute("SELECT score FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 1",(ticker,)).fetchone()
        holdings.append({
            "ticker":ticker,"shares":shares,"buy_price":buy_price,
            "cost_basis":cost_basis,"current_price":current_price,
            "current_value":current_value,"notes":row["notes"],
            "scan_score":scan["score"] if scan else None,"alloc_pct":0,
        })
        total_value += current_value
        total_cost  += cost_basis

    for h in holdings:
        h["alloc_pct"] = round(h["current_value"]/total_value*100,1) if total_value else 0

    total_gain     = total_value - total_cost
    total_gain_pct = (total_gain/total_cost*100) if total_cost else 0
    scored         = [h["scan_score"] for h in holdings if h["scan_score"]]
    avg_score      = round(sum(scored)/len(scored)) if scored else None

    optimization = None
    try:
        from portfolio_optimizer import analyze_portfolio
        optimization = analyze_portfolio(holdings, conn)
    except Exception as e:
        print(f"Optimization error: {e}")

    conn.close()
    return render_template("portfolio.html",
        holdings=holdings, total_value=total_value, total_cost=total_cost,
        total_gain=total_gain, total_gain_pct=total_gain_pct,
        avg_score=avg_score, alerts=None, optimization=optimization)

@app.route("/portfolio/add", methods=["POST"])
@login_required
def portfolio_add():
    ticker = request.form.get("ticker","").upper().strip()
    shares = request.form.get("shares"); buy_price = request.form.get("buy_price")
    notes  = request.form.get("notes","").strip()
    if ticker and shares and buy_price:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO portfolio (ticker,shares,buy_price,notes) VALUES (?,?,?,?)",(ticker,float(shares),float(buy_price),notes))
        conn.commit(); conn.close()
    return redirect(url_for("portfolio"))

@app.route("/portfolio/remove/<ticker>", methods=["POST"])
@login_required
def portfolio_remove(ticker):
    conn = get_db()
    conn.execute("DELETE FROM portfolio WHERE ticker=?",(ticker.upper(),))
    conn.commit(); conn.close()
    return redirect(url_for("portfolio"))

@app.route("/api/portfolio/prices")
@login_required
def api_portfolio_prices():
    """Manual refresh — clears cache and batch fetches fresh."""
    import yfinance as yf
    conn  = get_db()
    rows  = conn.execute("SELECT ticker, shares FROM portfolio").fetchall()
    tickers = [r["ticker"] for r in rows]
    shares_map = {r["ticker"]: r["shares"] for r in rows}

    # Clear cache for these tickers so batch_fetch gets fresh data
    for ticker in tickers:
        try:
            conn.execute("DELETE FROM price_cache WHERE ticker=?", (ticker,))
        except Exception:
            pass
    conn.commit()

    prices = batch_fetch_prices(tickers, conn)
    conn.close()

    return jsonify([{
        "ticker": t,
        "current_price": prices.get(t),
        "current_value": round(shares_map[t] * prices[t], 2) if prices.get(t) else None
    } for t in tickers if prices.get(t)])

# ── compare ───────────────────────────────────────────────────────────────────

@app.route("/compare")
@login_required
def compare():
    t1=request.args.get("t1","").upper().strip(); t2=request.args.get("t2","").upper().strip(); t3=request.args.get("t3","").upper().strip()
    stocks=[]; best_score=0; best_upside=0
    if t1 and t2:
        conn=get_db()
        for ticker in [t for t in [t1,t2,t3] if t]:
            row=conn.execute("SELECT * FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 1",(ticker,)).fetchone()
            if row:
                stocks.append(dict(row))
                if row["score"] and row["score"]>best_score: best_score=row["score"]
                if row["upside_pct"] and row["upside_pct"]>best_upside: best_upside=row["upside_pct"]
        conn.close()
    return render_template("compare.html",t1=t1,t2=t2,t3=t3,stocks=stocks,best_score=best_score,best_upside=best_upside)

# ── earnings ──────────────────────────────────────────────────────────────────

@app.route("/earnings")
@login_required
def earnings_calendar():
    conn=get_db(); scores={}; tickers_in_scan=[]
    try:
        rows=conn.execute("SELECT ticker,score FROM scans WHERE scan_date=(SELECT MAX(scan_date) FROM scans) ORDER BY score DESC LIMIT 20").fetchall()
        for r in rows: scores[r["ticker"]]=r["score"]; tickers_in_scan.append(r["ticker"])
    except Exception: pass
    conn.close()
    if not tickers_in_scan: return render_template("earnings.html",urgent=[],upcoming=[])
    from features import get_earnings_calendar
    earnings=get_earnings_calendar(tickers_in_scan[:15])
    for e in earnings: e["score"]=scores.get(e["ticker"])
    return render_template("earnings.html",
        urgent=[e for e in earnings if e["days_away"]<=7],
        upcoming=[e for e in earnings if e["days_away"]>7])

# ── backtest ──────────────────────────────────────────────────────────────────

@app.route("/backtest",methods=["GET","POST"])
@login_required
def backtest():
    result=None
    if request.method=="POST":
        try:
            from features import run_backtest
            conn=get_db(); result=run_backtest(conn); conn.close()
        except Exception as e: result={"error":str(e)}
    return render_template("backtest.html",result=result)

# ── alerts API ────────────────────────────────────────────────────────────────

@app.route("/api/alerts")
@login_required
def api_alerts():
    conn=get_db()
    rows=conn.execute("SELECT id,ticker,type,message,created_at FROM alerts WHERE seen=0 ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    return jsonify({"alerts":[dict(r) for r in rows]})

@app.route("/api/alerts/clear",methods=["POST"])
@login_required
def api_alerts_clear():
    conn=get_db(); conn.execute("UPDATE alerts SET seen=1 WHERE seen=0"); conn.commit(); conn.close()
    return jsonify({"ok":True})

@app.route("/api/push/subscribe",methods=["POST"])
@login_required
def api_push_subscribe():
    try:
        data=request.get_json(); endpoint=data.get("endpoint","")
        p256dh=data.get("keys",{}).get("p256dh",""); auth=data.get("keys",{}).get("auth","")
        if endpoint and p256dh and auth:
            conn=get_db()
            conn.execute("INSERT OR REPLACE INTO push_subscriptions (endpoint,p256dh,auth) VALUES (?,?,?)",(endpoint,p256dh,auth))
            conn.commit(); conn.close()
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

# ── misc ──────────────────────────────────────────────────────────────────────

@app.route("/api/sentiment/<ticker>")
@login_required
def api_sentiment(ticker):
    from features import get_news_sentiment
    return jsonify(get_news_sentiment(ticker.upper()))

@app.route("/api/options/<ticker>")
@login_required
def api_options(ticker):
    from features import get_unusual_options
    return jsonify(get_unusual_options([ticker.upper()]).get(ticker.upper(),{}))

@app.route("/api/institutional/<ticker>")
@login_required
def api_institutional(ticker):
    from features import get_institutional_changes
    return jsonify(get_institutional_changes([ticker.upper()]).get(ticker.upper(),{}))

@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/seed")
@login_required
def seed():
    conn=get_db(); today=datetime.now().strftime("%Y-%m-%d")
    conn.execute("DELETE FROM scans WHERE scan_date=?",(today,))
    conn.execute("DELETE FROM market_conditions WHERE scan_date=?",(today,))
    conn.execute("INSERT INTO market_conditions (scan_date,sp500,sp500_chg,vix,tny,regime,regime_label,regime_confidence) VALUES (?,?,?,?,?,?,?,?)",(today,5234.18,0.43,18.2,4.31,"neutral_bull","Cautious Bull",62))
    sample=[("MA",85,"✓","✓","✓","✓","✓","✓",510.20,18.5,1.1,0.0,72,3.2,5,"Financial Services"),("NVDA",80,"✓","–","✓","✓","✓","✓",875.40,22.1,1.8,0.0,61,2.1,3,"Technology"),("MSFT",74,"✓","✓","✓","–","✓","–",415.30,12.4,0.9,0.7,58,1.8,7,"Technology"),("AVGO",70,"✓","–","✓","✓","–","✓",1340.0,15.3,1.2,1.5,45,2.4,2,"Technology"),("JPM",65,"✓","✓","–","–","✓","✓",198.50,9.8,1.1,2.2,67,0.9,1,"Financial Services"),("LLY",60,"✓","–","✓","✓","–","–",820.00,19.2,0.6,0.8,38,1.1,4,"Healthcare"),("V",55,"✓","✓","–","–","✓","–",276.40,11.1,0.9,0.7,71,0.8,2,"Financial Services"),("AAPL",52,"✓","–","–","–","✓","✓",189.30,8.4,1.2,0.5,44,1.4,0,"Technology"),("COST",48,"–","✓","✓","–","–","✓",785.20,7.2,0.8,0.6,82,0.5,1,"Consumer Cyclical"),("UNH",44,"✓","–","–","✓","–","–",512.00,14.8,0.7,1.4,29,0.7,0,"Healthcare")]
    for t,sc,y,z,ms,ins,eps,rs,pr,up,bt,dv,w52,sp,stk,sec in sample:
        sig_count=sum(1 for x in [y,z,ms,ins,eps,rs] if x=="✓")
        conn.execute("INSERT INTO scans (scan_date,ticker,score,sources,yahoo_sb,zacks,morningstar,insider,eps_rev,beats_sp,price,upside_pct,beta,div_yield,week52_pos,short_pct,vol_spike,streak,is_new,sector) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(today,t,sc,f"{sig_count}/7",y,z,ms,ins,eps,rs,pr,up,bt,dv,w52,sp,"0",stk,"🆕" if stk==0 else "",sec))
    conn.commit(); conn.close()
    return redirect(url_for("dashboard"))

# ── run ───────────────────────────────────────────────────────────────────────

init_db()
if __name__ == "__main__":
    app.run(debug=True, port=5000)
