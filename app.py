"""
Stock Convergence Web App v3.4
================================
Phase 2 final additions:
  - Weekly trend mode on dashboard (/api/weekly_trend)
  - Strategy price alerts (checked in alerts.py, triggered by watchlist targets)
  - AI insights on history page (/history calls ai_insights.py)
"""

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
import os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "stockconvergence2026")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
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
            ai_brief TEXT,
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
        """CREATE TABLE IF NOT EXISTS insights_cache (
            key TEXT PRIMARY KEY, value TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            added_date TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, user_id))""",
    ]
    for sql in tables:
        conn.execute(sql)
    conn.commit()

    # ── schema migrations (idempotent) ────────────────────────────────────────
    migrations = [
        "ALTER TABLE market_conditions ADD COLUMN ai_brief TEXT",
        "ALTER TABLE market_conditions ADD COLUMN regime TEXT",
        "ALTER TABLE market_conditions ADD COLUMN regime_label TEXT",
        "ALTER TABLE market_conditions ADD COLUMN regime_confidence INTEGER",
        "ALTER TABLE portfolio ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'",
        "ALTER TABLE watchlist ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'",
        """CREATE TABLE IF NOT EXISTS ai_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            predicted_direction TEXT,
            confidence INTEGER,
            reasoning TEXT,
            price_at_prediction REAL,
            price_1w_later REAL,
            actual_direction TEXT,
            was_correct INTEGER,
            error_analysis TEXT,
            lessons TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # column already exists / table already exists

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
        (scan_date, sp500, sp500_chg, vix, tny, ai_brief)
        VALUES (?,?,?,?,?,?)
    """, (today, sp.get("price"), sp.get("chg"), vix.get("price"), tny.get("price"),
          market.get("ai_brief","")))
    for _, row in df.head(20).iterrows():
        def safe(key, default=None):
            v = row.get(key, default)
            if v in ("n/a","–","",None): return default
            try:
                import re
                cleaned = re.sub(r'[^\d.\-]', '', str(v))
                return float(cleaned) if cleaned else default
            except: return default
        streak_raw = str(row.get("Streak","0")).replace("🔥","").replace("d","").strip()
        try: streak_int = int(streak_raw)
        except: streak_int = 0
        conn.execute("""
            INSERT INTO scans (scan_date,ticker,score,sources,yahoo_sb,zacks,morningstar,
            insider,eps_rev,beats_sp,price,upside_pct,beta,div_yield,week52_pos,
            short_pct,vol_spike,streak,is_new,sector)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (today, row.get("Ticker"), safe("Consensus Score"), row.get("Sources Agree"),
              row.get("Yahoo SB"), row.get("Zacks #1"), row.get("Morningstar ★★★"),
              row.get("Insider Buy"), row.get("EPS Rev ↑"), row.get("Beats S&P"),
              safe("Price"), safe("Upside %"), safe("Beta"), safe("Div Yield"),
              safe("52w Position"), safe("Short %"),
              "1" if row.get("Vol Spike") else "0",
              streak_int, row.get("New?",""), row.get("Sector","Unknown")))
    conn.commit()
    conn.close()

# ── batch price fetching ──────────────────────────────────────────────────────

def batch_fetch_prices(tickers: list, conn, max_age_minutes: int = 20) -> dict:
    import yfinance as yf
    now        = datetime.now()
    prices     = {}
    need_fetch = []

    for ticker in tickers:
        try:
            row = conn.execute(
                "SELECT price, fetched_at FROM price_cache WHERE ticker = ?", (ticker,)
            ).fetchone()
            if row:
                age = (now - datetime.fromisoformat(row["fetched_at"].replace(" ","T").replace("Z","+00:00").split("+")[0])).total_seconds() / 60
                if age < max_age_minutes:
                    prices[ticker] = round(float(row["price"]), 2)
                    continue
        except Exception:
            pass
        need_fetch.append(ticker)

    if need_fetch:
        try:
            raw        = yf.download(need_fetch, period="1d", progress=False, auto_adjust=True, group_by="ticker")
            fetched_at = now.isoformat()
            for ticker in need_fetch:
                try:
                    price = float(raw["Close"].iloc[-1]) if len(need_fetch)==1 else float(raw[ticker]["Close"].iloc[-1])
                    price = round(price, 2)
                    prices[ticker] = price
                    try:
                        conn.execute("INSERT INTO price_cache (ticker,price,fetched_at) VALUES (?,?,?) ON CONFLICT(ticker) DO UPDATE SET price=excluded.price,fetched_at=excluded.fetched_at", (ticker,price,fetched_at))
                    except Exception: pass
                except Exception:
                    try:
                        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FT
                        with ThreadPoolExecutor(max_workers=1) as _ex:
                            _f = _ex.submit(lambda t=ticker: yf.Ticker(t).fast_info)
                            fi = _f.result(timeout=8)
                        p  = fi.last_price or fi.regular_market_price
                        if p:
                            prices[ticker] = round(float(p),2)
                            try: conn.execute("INSERT INTO price_cache (ticker,price,fetched_at) VALUES (?,?,?) ON CONFLICT(ticker) DO UPDATE SET price=excluded.price,fetched_at=excluded.fetched_at",(ticker,round(float(p),2),fetched_at))
                            except: pass
                    except: pass
            try: conn.commit()
            except: pass
        except Exception as e:
            print(f"Batch fetch error: {e}")
            for ticker in need_fetch:
                try:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=1) as _ex:
                        _f = _ex.submit(lambda t=ticker: yf.Ticker(t).fast_info)
                        fi = _f.result(timeout=8)
                    p  = fi.last_price or fi.regular_market_price
                    if p: prices[ticker] = round(float(p),2)
                except: pass
    return prices

# ── alert throttle ────────────────────────────────────────────────────────────

_last_alert_check = None

def maybe_check_alerts(conn):
    global _last_alert_check
    now = datetime.now()
    if _last_alert_check and (now - _last_alert_check).total_seconds() < 600:
        return
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
            if request.form.get("remember_me"):
                session.permanent = True
            session["logged_in"] = True
            session["user"] = request.form.get("username","").strip().lower() or "default"
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

    regime = None
    try:
        from market_regime import get_regime_from_db
        regime = get_regime_from_db(conn)
    except Exception as e:
        print(f"Regime error: {e}")

    try:
        maybe_check_alerts(conn)
    except Exception as e:
        print(f"Alert check error: {e}")
    finally:
        conn.close()
    return render_template("dashboard.html",
        stocks=stocks, market=market,
        latest_date=latest_date, streaks=streaks,
        ai_brief=ai_brief, regime=regime)

# ── weekly trend API (for short/long term toggle) ─────────────────────────────

@app.route("/api/weekly_trend")
@login_required
def api_weekly_trend():
    """
    Returns stocks ranked by their AVERAGE score over the last 7 days.
    This is the 'long term' / 'weekly trend' view — more stable than daily.
    """
    conn     = get_db()
    cutoff   = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    rows     = conn.execute("""
        SELECT ticker,
               COUNT(DISTINCT scan_date)    as days_seen,
               AVG(score)                   as avg_score,
               MAX(score)                   as max_score,
               MIN(score)                   as min_score,
               MAX(streak)                  as streak,
               MAX(sector)                  as sector,
               MAX(upside_pct)              as upside_pct,
               MAX(price)                   as price,
               SUM(CASE WHEN yahoo_sb='✓'   THEN 1 ELSE 0 END) as yahoo_days,
               SUM(CASE WHEN zacks='✓'      THEN 1 ELSE 0 END) as zacks_days,
               SUM(CASE WHEN insider='✓'    THEN 1 ELSE 0 END) as insider_days,
               SUM(CASE WHEN vol_spike='1'  THEN 1 ELSE 0 END) as vol_days,
               MAX(alignment)               as alignment
        FROM scans
        WHERE scan_date >= ?
        GROUP BY ticker
        HAVING COUNT(DISTINCT scan_date) >= 2
        ORDER BY AVG(score) DESC
        LIMIT 20
    """, (cutoff,)).fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "ticker":       r["ticker"],
            "avg_score":    round(r["avg_score"] or 0),
            "max_score":    round(r["max_score"] or 0),
            "min_score":    round(r["min_score"] or 0),
            "days_seen":    r["days_seen"],
            "streak":       r["streak"] or 0,
            "sector":       r["sector"] or "–",
            "upside_pct":   r["upside_pct"],
            "price":        r["price"],
            "yahoo_days":   r["yahoo_days"] or 0,
            "zacks_days":   r["zacks_days"] or 0,
            "insider_days": r["insider_days"] or 0,
            "vol_days":     r["vol_days"] or 0,
            "alignment":    r["alignment"] or "–",
            "consistency":  round((r["days_seen"] / 7) * 100),
        })
    return jsonify(result)

# ── history (with AI insights) ────────────────────────────────────────────────

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

    # AI insights from scan history
    ai_insights = None
    try:
        from ai_insights import get_ai_insights
        ai_insights = get_ai_insights(conn)
    except Exception as e:
        print(f"AI insights error: {e}")

    conn.close()
    return render_template("history.html",
        top_all_time=top_all_time, daily=daily,
        ai_insights=ai_insights)

@app.route("/api/scores/<date>")
@login_required
def api_scores(date):
    conn   = get_db()
    stocks = conn.execute("SELECT ticker, score FROM scans WHERE scan_date=? ORDER BY score DESC",(date,)).fetchall()
    conn.close()
    return jsonify([dict(s) for s in stocks])

@app.route("/api/ticker/<ticker>")
@login_required
def api_ticker(ticker):
    conn = get_db()
    rows = conn.execute("SELECT scan_date, score, upside_pct, price, streak FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 30",(ticker.upper(),)).fetchall()
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
        rows   = conn.execute("SELECT * FROM penny_scans WHERE scan_date=? ORDER BY score DESC",(last,)).fetchall()
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
        conn.execute("DELETE FROM penny_scans WHERE scan_date=?",(today,))
        for _, row in df.iterrows():
            conn.execute("""
                INSERT INTO penny_scans
                (scan_date,ticker,name,tier,score,price,mkt_cap,vol_spike,vol_ratio,
                 breakout,week52_range,beats_mkt,mo_return,insider_buy,short_squeeze,short_float,signals)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (today,row.get("Ticker"),row.get("Name"),row.get("Tier"),
                  row.get("Score"),row.get("Price"),row.get("Mkt Cap"),
                  row.get("Vol Spike"),row.get("Vol Ratio"),row.get("Breakout"),
                  row.get("52w Range%"),row.get("Beats Mkt"),row.get("1mo Return"),
                  row.get("Insider Buy"),row.get("Short Squeeze"),
                  row.get("Short Float%"),row.get("Signals")))
        conn.commit(); conn.close()
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
    import yfinance as yf
    import requests as req
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    try:
        t = yf.Ticker(ticker.upper())
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fi = ex.submit(lambda: t.fast_info).result(timeout=8)
            price = float(fi.last_price or fi.regular_market_price or 0)
        except Exception:
            price = 0
        info = {}
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(lambda: t.info)
                info   = future.result(timeout=15) or {}
        except Exception:
            info = {}
        if not info and not price:
            return jsonify({"error": "Ticker not found"})
        if not price:
            price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        prev_close   = info.get("previousClose") or price
        price_chg    = round((price-prev_close)/prev_close*100,2) if prev_close else 0
        name         = info.get("longName") or info.get("shortName",ticker)
        sector       = info.get("sector","Unknown")
        industry     = info.get("industry","")
        mkt_cap      = info.get("marketCap",0) or 0
        price_target = info.get("targetMeanPrice")
        upside       = round((price_target-price)/price*100,1) if price_target and price else None
        pe             = info.get("trailingPE")
        forward_pe     = info.get("forwardPE")
        revenue_growth = round((info.get("revenueGrowth") or 0)*100,1)
        profit_margin  = round((info.get("profitMargins") or 0)*100,1)
        roe            = round((info.get("returnOnEquity") or 0)*100,1)
        debt_equity    = info.get("debtToEquity")
        if debt_equity: debt_equity = round(debt_equity/100,2)
        beta           = info.get("beta")
        div_yield      = round((info.get("dividendYield") or 0)*100,2)
        short_pct      = round((info.get("shortPercentOfFloat") or 0)*100,1)
        num_analysts   = info.get("numberOfAnalystOpinions",0)
        rec_mean       = info.get("recommendationMean")
        avg_vol        = info.get("averageVolume",0) or 0
        cur_vol        = info.get("volume",0) or 0
        vol_ratio      = round(cur_vol/avg_vol,1) if avg_vol else 0
        vol_spike      = vol_ratio >= 2.0
        high52         = info.get("fiftyTwoWeekHigh")
        low52          = info.get("fiftyTwoWeekLow")
        week52_pos     = round((price-low52)/(high52-low52)*100,1) if high52 and low52 and (high52-low52)>0 else None
        if mkt_cap>=1e12:   cap_label,cap_tier=f"${mkt_cap/1e12:.1f}T","Mega Cap"
        elif mkt_cap>=1e9:  cap_label,cap_tier=f"${mkt_cap/1e9:.1f}B","Large Cap"
        elif mkt_cap>=1e8:  cap_label,cap_tier=f"${mkt_cap/1e8:.0f}M","Mid Cap"
        elif mkt_cap>=1e6:  cap_label,cap_tier=f"${mkt_cap/1e6:.0f}M","Small Cap"
        else:               cap_label,cap_tier="< $1M","Micro Cap"
        analyst_score=0; analyst_label="No data"
        if rec_mean and num_analysts>=3:
            analyst_score=max(0,round((5-rec_mean)/4*100))
            analyst_label=["","Strong Buy","Buy","Hold","Sell","Strong Sell"][min(5,round(rec_mean))]
        valuation_score=50; valuation_label="Fair"
        if pe and pe>0:
            if pe<15:   valuation_score,valuation_label=85,"Undervalued"
            elif pe<25: valuation_score,valuation_label=65,"Reasonable"
            elif pe<40: valuation_score,valuation_label=40,"Elevated"
            else:       valuation_score,valuation_label=20,"Expensive"
        if upside:
            if upside>20:  valuation_score=min(100,valuation_score+20)
            elif upside<0: valuation_score=max(0,valuation_score-20)
        profit_score=0
        if profit_margin>20: profit_score+=40
        elif profit_margin>10: profit_score+=25
        elif profit_margin>0:  profit_score+=10
        if roe>20: profit_score+=35
        elif roe>10: profit_score+=20
        elif roe>0:  profit_score+=10
        if revenue_growth>15: profit_score+=25
        elif revenue_growth>5: profit_score+=15
        elif revenue_growth>0: profit_score+=5
        profit_label="Strong" if profit_score>=65 else ("Moderate" if profit_score>=40 else "Weak")
        health_score=50
        if debt_equity is not None:
            if debt_equity<0.3:   health_score+=30
            elif debt_equity<1.0: health_score+=15
            elif debt_equity>2.0: health_score-=20
        if div_yield>0: health_score+=10
        health_score=max(0,min(100,health_score))
        health_label="Strong" if health_score>=65 else ("Moderate" if health_score>=40 else "Weak")
        momentum_score=0
        if week52_pos: momentum_score+=min(50,round(week52_pos/2))
        if vol_spike: momentum_score+=25
        if revenue_growth>10: momentum_score+=25
        momentum_score=min(100,momentum_score)
        momentum_label="Strong" if momentum_score>=65 else ("Moderate" if momentum_score>=40 else "Weak")
        risk_score=30
        if beta:
            if beta>2.0:   risk_score+=25
            elif beta>1.5: risk_score+=15
            elif beta>1.2: risk_score+=8
            elif beta<0.5: risk_score+=5
        if pe and pe>50: risk_score+=15
        elif pe and pe>30: risk_score+=8
        if debt_equity and debt_equity>2.0: risk_score+=15
        elif debt_equity and debt_equity>1.0: risk_score+=8
        if short_pct>25: risk_score+=15
        elif short_pct>15: risk_score+=8
        if mkt_cap<1e9:  risk_score+=15
        elif mkt_cap<5e9: risk_score+=8
        if profit_margin<0: risk_score+=10
        if profit_margin>20: risk_score-=8
        if roe>20: risk_score-=5
        if num_analysts>10: risk_score-=5
        risk_score=max(5,min(95,risk_score))
        risk_summary=(
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
        alignment = {}
        try:
            conn = get_db()
            try:
                row = conn.execute("SELECT alignment FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 1",(ticker.upper(),)).fetchone()
                if row and row["alignment"]:
                    alignment = {"label":row["alignment"]}
            finally:
                conn.close()
        except Exception:
            pass
        # ── Bull / Bear signal ────────────────────────────────────────────────
        bb_score = 0
        w52 = week52_pos or 50
        if w52 > 65:    bb_score += 2
        elif w52 > 50:  bb_score += 1
        elif w52 < 35:  bb_score -= 2
        elif w52 < 50:  bb_score -= 1
        if price_chg >  1:  bb_score += 2
        elif price_chg > 0: bb_score += 1
        elif price_chg < -1: bb_score -= 2
        elif price_chg < 0:  bb_score -= 1
        if momentum_score >= 65:  bb_score += 2
        elif momentum_score >= 40: bb_score += 1
        else: bb_score -= 1
        if analyst_score >= 65:  bb_score += 2
        elif analyst_score >= 40: bb_score += 1
        if short_pct and short_pct > 20: bb_score -= 1
        if upside and upside > 15: bb_score += 1
        elif upside and upside < 0: bb_score -= 1

        if   bb_score >= 5:  stock_signal = {"label": "Strongly Bullish", "emoji": "🐂", "color": "var(--green)"}
        elif bb_score >= 2:  stock_signal = {"label": "Bullish",          "emoji": "📈", "color": "var(--green)"}
        elif bb_score <= -5: stock_signal = {"label": "Strongly Bearish", "emoji": "🐻", "color": "var(--red)"}
        elif bb_score <= -2: stock_signal = {"label": "Bearish",          "emoji": "📉", "color": "var(--red)"}
        else:                stock_signal = {"label": "Neutral",           "emoji": "⚖️", "color": "var(--yellow)"}

        ai_analysis = ""
        try:
            anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not anthropic_key:
                raise ValueError("No API key")
            prompt = f"""Analyze {ticker} ({name}): Price ${price:.2f}, P/E {pe}, Revenue Growth {revenue_growth}%, Margin {profit_margin}%, ROE {roe}%, D/E {debt_equity}, Beta {beta}, Short {short_pct}%, Analyst: {analyst_label} ({num_analysts}), Target ${price_target} ({upside}% upside), Cap {cap_label}, Sector {sector}, Risk {risk_score}/100, Signal: {stock_signal['label']}, Smart Buy: {smart_buy['label']}.
Write 3 short paragraphs: 1) business & competitive position 2) key strengths now 3) risks & who it suits. Max 180 words. No headers."""
            resp = req.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type":    "application/json",
                    "x-api-key":       anthropic_key,
                    "anthropic-version": "2023-06-01",
                },
                json={"model": "claude-sonnet-4-6", "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30
            )
            if resp.status_code == 200:
                ai_analysis = resp.json()["content"][0]["text"].strip()
            else:
                ai_analysis = f"{name} operates in {sector}. Risk: {risk_score}/100. Research before investing."
        except Exception:
            ai_analysis = f"{name} operates in {sector}. Risk: {risk_score}/100. Research before investing."
        return jsonify({
            "ticker":ticker,"name":name,"sector":sector,"industry":industry,
            "price":round(price,2),"price_change_pct":price_chg,
            "price_target":price_target,"upside":upside,"pe":pe,"forward_pe":forward_pe,
            "revenue_growth":revenue_growth,"profit_margin":profit_margin,"roe":roe,
            "debt_equity":debt_equity,"beta":beta,"div_yield":div_yield,"short_pct":short_pct,
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
            "stock_signal":stock_signal,
        })
    except Exception as e:
        return jsonify({"error":str(e)})

# ── stock detail ──────────────────────────────────────────────────────────────

@app.route("/stock/<ticker>")
@login_required
def stock_detail(ticker):
    ticker = ticker.upper()
    conn   = get_db()
    history = conn.execute("SELECT * FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 60",(ticker,)).fetchall()
    stats   = conn.execute("SELECT COUNT(*) as appearances, AVG(score) as avg_score, MAX(score) as max_score, MAX(streak) as max_streak FROM scans WHERE ticker=?",(ticker,)).fetchone()
    latest         = history[0] if history else None
    latest_score   = int(latest["score"]) if latest else "–"
    current_streak = latest["streak"] if latest else 0
    appearances    = stats["appearances"] if stats else 0
    avg_score      = round(stats["avg_score"]) if stats and stats["avg_score"] else None
    max_streak     = stats["max_streak"] if stats else 0
    chart_dates    = [row["scan_date"] for row in reversed(list(history))]
    chart_scores   = [row["score"] for row in reversed(list(history))]
    smart_buy = None
    if latest:
        from features import get_smart_buy_rating
        smart_buy = get_smart_buy_rating(
            score=latest.get("score"),upside_pct=latest.get("upside_pct"),
            short_pct=latest.get("short_pct"),week52_pos=latest.get("week52_pos"),
            beta=latest.get("beta"),price=latest.get("price"),price_target=None
        )
    live_price = None
    try:
        row = conn.execute("SELECT price, fetched_at FROM price_cache WHERE ticker=?",(ticker,)).fetchone()
        if row:
            age = (datetime.now()-datetime.fromisoformat(row["fetched_at"].replace(" ","T").replace("Z","+00:00").split("+")[0])).total_seconds()/60
            if age < 60:
                live_price = round(float(row["price"]),2)
    except Exception: pass
    alignment = None
    if latest and latest.get("alignment"):
        alignment = {"label": latest["alignment"]}
    conn.close()
    return render_template("stock_detail.html",
        ticker=ticker,history=history,latest=latest,
        latest_score=latest_score,current_streak=current_streak,
        appearances=appearances,avg_score=avg_score,max_streak=max_streak,
        chart_dates=chart_dates,chart_scores=chart_scores,
        earnings_warning=None,smart_buy=smart_buy,
        live_price=live_price,alignment=alignment)

# ── sectors ───────────────────────────────────────────────────────────────────

@app.route("/sectors")
@login_required
def sectors():
    conn   = get_db()
    row    = conn.execute("SELECT MAX(scan_date) as latest FROM scans").fetchone()
    latest = row["latest"] if row and row["latest"] else None
    sector_data = []
    if latest:
        rows = conn.execute("SELECT sector, COUNT(*) as count, AVG(score) as avg_score, MAX(score) as max_score FROM scans WHERE scan_date=? AND sector IS NOT NULL AND sector!='Unknown' GROUP BY sector ORDER BY avg_score DESC",(latest,)).fetchall()
        for r in rows:
            top     = conn.execute("SELECT ticker FROM scans WHERE scan_date=? AND sector=? ORDER BY score DESC LIMIT 1",(latest,r["sector"])).fetchone()
            tickers = conn.execute("SELECT ticker FROM scans WHERE scan_date=? AND sector=? ORDER BY score DESC LIMIT 4",(latest,r["sector"])).fetchall()
            sector_data.append({"sector":r["sector"],"count":r["count"],"avg_score":round(r["avg_score"]),"max_score":round(r["max_score"]),"top_ticker":top["ticker"] if top else "–","tickers":" · ".join([t["ticker"] for t in tickers])})
    conn.close()
    return render_template("sectors.html",sectors=sector_data)

# ── watchlist (with price alert checking) ────────────────────────────────────

@app.route("/watchlist")
@login_required
def watchlist():
    conn    = get_db()
    user_id = session.get("user", "default")
    items   = conn.execute("SELECT * FROM watchlist WHERE user_id=? ORDER BY added_date DESC", (user_id,)).fetchall()
    today   = datetime.now().strftime("%Y-%m-%d")
    tickers = [w["ticker"] for w in items]
    prices  = batch_fetch_prices(tickers,conn) if tickers else {}
    result  = []
    for w in items:
        ticker  = w["ticker"]
        scan    = conn.execute("SELECT score FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 1",(ticker,)).fetchone()
        in_scan = conn.execute("SELECT 1 FROM scans WHERE ticker=? AND scan_date=?",(ticker,today)).fetchone()
        current_price = prices.get(ticker)

        # Check if price alert should fire
        price_alert = None
        if current_price and w["target_price"]:
            diff_pct = (current_price - w["target_price"]) / w["target_price"] * 100
            if abs(diff_pct) <= 2:  # within 2% of target
                price_alert = "near_target"
            elif current_price <= w["target_price"]:
                price_alert = "at_or_below"

        result.append({
            "ticker":        ticker,
            "target_price":  w["target_price"],
            "notes":         w["notes"],
            "added_date":    w["added_date"][:10] if w["added_date"] else "",
            "latest_score":  scan["score"] if scan else None,
            "in_scan":       bool(in_scan),
            "current_price": current_price,
            "price_alert":   price_alert,
        })
    conn.close()
    return render_template("watchlist.html", watchlist=result)

@app.route("/watchlist/add", methods=["POST"])
@login_required
def watchlist_add():
    ticker       = request.form.get("ticker","").upper().strip()
    target_price = request.form.get("target_price") or None
    notes        = request.form.get("notes","").strip()
    user_id      = session.get("user", "default")
    if ticker:
        conn = get_db()
        try:
            conn.execute("DELETE FROM watchlist WHERE ticker=? AND user_id=?", (ticker, user_id))
            conn.execute(
                "INSERT INTO watchlist (ticker, user_id, target_price, notes) VALUES (?,?,?,?)",
                (ticker, user_id, target_price, notes)
            )
            conn.commit()
        finally:
            conn.close()
    return redirect(url_for("watchlist"))

@app.route("/watchlist/remove/<ticker>", methods=["POST"])
@login_required
def watchlist_remove(ticker):
    user_id = session.get("user", "default")
    conn = get_db()
    try:
        conn.execute("DELETE FROM watchlist WHERE ticker=? AND user_id=?", (ticker.upper(), user_id))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("watchlist"))

# ── portfolio ─────────────────────────────────────────────────────────────────

@app.route("/portfolio")
@login_required
def portfolio():
    conn    = get_db()
    user_id = session.get("user", "default")
    rows    = conn.execute("SELECT * FROM portfolio WHERE user_id=? ORDER BY added_date DESC", (user_id,)).fetchall()
    tickers = [row["ticker"] for row in rows]
    prices  = batch_fetch_prices(tickers,conn) if tickers else {}
    holdings=[]; total_value=0; total_cost=0
    for row in rows:
        ticker=row["ticker"]; shares=row["shares"]; buy_price=row["buy_price"]
        cost_basis=shares*buy_price; current_price=prices.get(ticker)
        current_value=shares*(current_price or buy_price)
        gain_pct=(current_value-cost_basis)/cost_basis*100 if cost_basis else 0
        scan=conn.execute("SELECT score,streak FROM scans WHERE ticker=? ORDER BY scan_date DESC LIMIT 1",(ticker,)).fetchone()
        scan_score=scan["score"] if scan else None
        streak=scan["streak"] if scan else 0
        holdings.append({"ticker":ticker,"shares":shares,"buy_price":buy_price,"cost_basis":cost_basis,"current_price":current_price,"current_value":current_value,"gain_pct":gain_pct,"notes":row["notes"],"scan_score":scan_score,"streak":streak or 0,"alloc_pct":0})
        total_value+=current_value; total_cost+=cost_basis
    for h in holdings:
        h["alloc_pct"]=round(h["current_value"]/total_value*100,1) if total_value else 0
    total_gain=total_value-total_cost
    total_gain_pct=(total_gain/total_cost*100) if total_cost else 0
    scored=[h["scan_score"] for h in holdings if h["scan_score"]]
    avg_score=round(sum(scored)/len(scored)) if scored else None
    try:
        from portfolio_optimizer import get_holding_signal
        for h in holdings:
            h["signal"] = get_holding_signal(h["scan_score"], h.get("gain_pct", 0), h.get("streak", 0))
    except Exception as e:
        print(f"Signal error: {e}")
        for h in holdings:
            h.setdefault("signal", None)
    optimization=None
    try:
        from portfolio_optimizer import analyze_portfolio
        optimization=analyze_portfolio(holdings,conn)
    except Exception as e:
        print(f"Optimization error: {e}")
    finally:
        conn.close()
    return render_template("portfolio.html",holdings=holdings,total_value=total_value,total_cost=total_cost,total_gain=total_gain,total_gain_pct=total_gain_pct,avg_score=avg_score,alerts=None,optimization=optimization)

@app.route("/portfolio/add", methods=["POST"])
@login_required
def portfolio_add():
    ticker    = request.form.get("ticker","").upper().strip()
    shares    = request.form.get("shares")
    buy_price = request.form.get("buy_price")
    notes     = request.form.get("notes","").strip()
    user_id   = session.get("user", "default")
    if ticker and shares and buy_price:
        try:
            conn = get_db()
            conn.execute("DELETE FROM portfolio WHERE ticker=?", (ticker,))
            conn.execute(
                "INSERT INTO portfolio (ticker, user_id, shares, buy_price, notes) VALUES (?,?,?,?,?)",
                (ticker, user_id, float(shares), float(buy_price), notes)
            )
            conn.commit(); conn.close()
        except Exception as e:
            print(f"Portfolio add error: {e}")
            try: conn.close()
            except: pass
    return redirect(url_for("portfolio"))

@app.route("/portfolio/remove/<ticker>", methods=["POST"])
@login_required
def portfolio_remove(ticker):
    user_id = session.get("user", "default")
    conn = get_db()
    try:
        conn.execute("DELETE FROM portfolio WHERE ticker=? AND user_id=?", (ticker.upper(), user_id))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("portfolio"))

@app.route("/api/portfolio/prices")
@login_required
def api_portfolio_prices():
    import yfinance as yf
    user_id = session.get("user", "default")
    conn = get_db()
    rows = conn.execute("SELECT ticker, shares FROM portfolio WHERE user_id=?", (user_id,)).fetchall()
    tickers=[r["ticker"] for r in rows]; shares_map={r["ticker"]:r["shares"] for r in rows}
    try:
        for t in tickers:
            try: conn.execute("DELETE FROM price_cache WHERE ticker=?",(t,))
            except: pass
        conn.commit()
        prices=batch_fetch_prices(tickers,conn)
    finally:
        conn.close()
    return jsonify([{"ticker":t,"current_price":prices.get(t),"current_value":round(shares_map[t]*prices[t],2) if prices.get(t) else None} for t in tickers if prices.get(t)])

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
    except: pass
    conn.close()
    if not tickers_in_scan: return render_template("earnings.html",urgent=[],upcoming=[])
    from features import get_earnings_calendar
    earnings=get_earnings_calendar(tickers_in_scan[:15])
    for e in earnings: e["score"]=scores.get(e["ticker"])
    return render_template("earnings.html",urgent=[e for e in earnings if e["days_away"]<=7],upcoming=[e for e in earnings if e["days_away"]>7])

# ── backtest ──────────────────────────────────────────────────────────────────

@app.route("/backtest",methods=["GET","POST"])
@login_required
def backtest():
    result=None
    if request.method=="POST":
        conn = None
        try:
            from features import run_backtest
            conn=get_db(); result=run_backtest(conn)
        except Exception as e:
            result={"error":str(e)}
        finally:
            if conn:
                conn.close()
    return render_template("backtest.html",result=result)

# ── portfolio earnings API ───────────────────────────────────────────────────

@app.route("/api/portfolio/earnings")
@login_required
def api_portfolio_earnings():
    conn = get_db()
    user_id = session.get("user", "default")
    rows = conn.execute("SELECT ticker FROM portfolio WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    tickers = [r["ticker"] for r in rows]
    if not tickers:
        return jsonify([])
    try:
        from features import get_earnings_calendar
        earnings = get_earnings_calendar(tickers)
        return jsonify(earnings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    conn=get_db()
    try:
        conn.execute("UPDATE alerts SET seen=1 WHERE seen=0")
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok":True})

@app.route("/api/push/test", methods=["POST"])
@login_required
def api_push_test():
    conn = get_db()
    try:
        from alerts import send_push_to_all
        send_push_to_all(conn, {
            "title": "📈 Convergence — Test",
            "body":  "Push notifications are working!",
            "url":   "/",
            "tag":   "test",
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    finally:
        conn.close()

@app.route("/api/push/subscribe",methods=["POST"])
@login_required
def api_push_subscribe():
    try:
        data=request.get_json(); endpoint=data.get("endpoint","")
        p256dh=data.get("keys",{}).get("p256dh",""); auth=data.get("keys",{}).get("auth","")
        if endpoint and p256dh and auth:
            conn=get_db(); conn.execute("INSERT OR REPLACE INTO push_subscriptions (endpoint,p256dh,auth) VALUES (?,?,?)",(endpoint,p256dh,auth)); conn.commit(); conn.close()
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/push/status")
@login_required
def api_push_status():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM push_subscriptions").fetchone()["c"]
    conn.close()
    return jsonify({"subscriptions": count})

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
    return "pong",200

@app.route("/api/run-scan", methods=["POST"])
def api_run_scan():
    # Simple token check so only the cron job can trigger this
    token = request.headers.get("X-Scan-Token") or request.args.get("token","")
    if token != os.environ.get("SCAN_TOKEN", ""):
        return jsonify({"error": "unauthorized"}), 401
    import threading
    def _run():
        try:
            from scheduler import daily_job
            daily_job()
        except Exception as e:
            print(f"[run-scan] error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "msg": "scan started"})

# ── Claude's Picks ────────────────────────────────────────────────────────────

@app.route("/ai-picks")
@login_required
def ai_picks():
    conn = get_db()
    try:
        from ai_predictions import get_picks_with_history
        data = get_picks_with_history(conn)
    except Exception as e:
        print(f"AI picks error: {e}")
        data = {"picks": [], "accuracy": None, "total_checked": 0, "correct": 0, "lessons": []}
    conn.close()
    return render_template("ai_picks.html", **data)

@app.route("/api/ai-picks/regenerate", methods=["POST"])
@login_required
def api_ai_picks_regenerate():
    conn  = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute("DELETE FROM ai_predictions WHERE prediction_date=?", (today,))
    conn.commit()
    conn.close()
    return redirect(url_for("ai_picks"))

@app.route("/seed")
@login_required
def seed():
    conn=get_db(); today=datetime.now().strftime("%Y-%m-%d")
    conn.execute("DELETE FROM scans WHERE scan_date=?",(today,)); conn.execute("DELETE FROM market_conditions WHERE scan_date=?",(today,))
    conn.execute("INSERT INTO market_conditions (scan_date,sp500,sp500_chg,vix,tny,regime,regime_label,regime_confidence) VALUES (?,?,?,?,?,?,?,?)",(today,5234.18,0.43,18.2,4.31,"neutral_bull","Cautious Bull",62))
    sample=[("MA",85,"✓","✓","✓","✓","✓","✓",510.20,18.5,1.1,0.0,72,3.2,5,"Financial Services"),("NVDA",80,"✓","–","✓","✓","✓","✓",875.40,22.1,1.8,0.0,61,2.1,3,"Technology"),("MSFT",74,"✓","✓","✓","–","✓","–",415.30,12.4,0.9,0.7,58,1.8,7,"Technology"),("AVGO",70,"✓","–","✓","✓","–","✓",1340.0,15.3,1.2,1.5,45,2.4,2,"Technology"),("JPM",65,"✓","✓","–","–","✓","✓",198.50,9.8,1.1,2.2,67,0.9,1,"Financial Services"),("LLY",60,"✓","–","✓","✓","–","–",820.00,19.2,0.6,0.8,38,1.1,4,"Healthcare"),("V",55,"✓","✓","–","–","✓","–",276.40,11.1,0.9,0.7,71,0.8,2,"Financial Services"),("AAPL",52,"✓","–","–","–","✓","✓",189.30,8.4,1.2,0.5,44,1.4,0,"Technology"),("COST",48,"–","✓","✓","–","–","✓",785.20,7.2,0.8,0.6,82,0.5,1,"Consumer Cyclical"),("UNH",44,"✓","–","–","✓","–","–",512.00,14.8,0.7,1.4,29,0.7,0,"Healthcare")]
    for t,sc,y,z,ms,ins,eps,rs,pr,up,bt,dv,w52,sp,stk,sec in sample:
        sig_count=sum(1 for x in [y,z,ms,ins,eps,rs] if x=="✓")
        conn.execute("INSERT INTO scans (scan_date,ticker,score,sources,yahoo_sb,zacks,morningstar,insider,eps_rev,beats_sp,price,upside_pct,beta,div_yield,week52_pos,short_pct,vol_spike,streak,is_new,sector) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(today,t,sc,f"{sig_count}/7",y,z,ms,ins,eps,rs,pr,up,bt,dv,w52,sp,"0",stk,"🆕" if stk==0 else "",sec))
    conn.commit(); conn.close()
    return redirect(url_for("dashboard"))

# ── run ───────────────────────────────────────────────────────────────────────

def _keepalive_thread():
    """
    Background thread that pings /ping every 8 minutes.
    On Render free tier the dyno sleeps after ~15 min of inactivity —
    this thread keeps it warm while the process is already running.
    For a cold-start fix, use an external pinger (cron-job.org or render.yaml cron).
    """
    import threading, time as _time, requests as _req
    def _loop():
        _time.sleep(60)  # wait for app to start
        app_url = os.environ.get("APP_URL", "").rstrip("/")
        if not app_url:
            return
        ping_url = f"{app_url}/ping"
        while True:
            try:
                _req.get(ping_url, timeout=10)
            except Exception:
                pass
            _time.sleep(480)  # 8 minutes
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


_scan_running = False

def _do_scan():
    global _scan_running
    if _scan_running:
        return
    _scan_running = True
    try:
        from scheduler import run_analyzer
        print(f"[scan] Starting daily scan...")
        df, market, warning, alignments = run_analyzer()
        from market_regime import detect_market_regime
        try:
            top_stocks = df.head(20).to_dict("records")
            mapped = [{"score": r.get("Consensus Score", 0), "insider": r.get("Insider Buy", "–")} for r in top_stocks]
            regime = detect_market_regime(market, mapped)
            market["regime"]            = regime.get("regime", "unknown")
            market["regime_label"]      = regime.get("label", "")
            market["regime_confidence"] = regime.get("confidence", 0)
        except Exception as e:
            print(f"[scan] Regime error: {e}")
        try:
            from features import generate_market_brief
            market["ai_brief"] = generate_market_brief(df.head(5).to_dict("records"), market)
        except Exception as e:
            print(f"[scan] AI brief error: {e}")
        save_scan_to_db(df, market)
        print(f"[scan] Done — {len(df)} stocks saved.")
    except Exception as e:
        print(f"[scan] Error: {e}")
    finally:
        _scan_running = False


def _daily_scan_thread():
    import threading, time as _time
    def _loop():
        _time.sleep(90)  # let app fully start
        while True:
            now = datetime.utcnow()
            today = now.strftime("%Y-%m-%d")
            # Run at 14:00 UTC (7 AM Pacific) if not already done today
            if now.hour == 14 and now.minute < 5:
                try:
                    conn = get_db()
                    try:
                        row = conn.execute("SELECT COUNT(*) as c FROM scans WHERE scan_date=?", (today,)).fetchone()
                        already_done = row and row["c"] > 0
                    finally:
                        conn.close()
                    if not already_done:
                        _do_scan()
                except Exception as e:
                    print(f"[scan-thread] {e}")
                _time.sleep(360)  # sleep 6 min after attempting
            else:
                _time.sleep(30)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


@app.route("/run-scan", methods=["POST"])
@login_required
def run_scan():
    import threading
    t = threading.Thread(target=_do_scan, daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "Scan started — check back in ~2 minutes."})


init_db()
_keepalive_thread()
_daily_scan_thread()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
