"""
health.py
=========
Self-diagnostic for the live app. Runs INSIDE the deployment, so it sees the
real database, the real network egress, and the real environment — the things
an outside caller cannot inspect.

GET /health          fast checks only (safe for uptime pings, ~2s)
GET /health?full=1   also renders every page server-side and probes upstreams

Returns JSON: {"ok": bool, "checks": [{name, ok, detail, severity}], ...}
Nothing sensitive is returned — env vars report only whether they are set.
"""

import os
from datetime import datetime, timedelta

# Pages rendered during a full check. Anything hitting yfinance hard is marked
# slow=True so a fast check stays quick.
_PAGES = [
    ("/",             "dashboard", False),
    ("/history",      "history",   False),
    ("/sectors",      "sectors",   False),
    ("/watchlist",    "watchlist", False),
    ("/portfolio",    "portfolio", False),
    ("/compare",      "compare",   False),
    ("/backtest",     "backtest",  False),
    ("/checklist",    "checklist", False),
    ("/ai-picks",     "ai_picks",  False),
    ("/momentum",     "momentum",  True),
    ("/analyze",      "analyze",   True),
    ("/earnings",     "earnings",  True),
]

# Reference tickers used for the "is our price data actually correct" check.
_PRICE_REFS = ["AAPL", "MSFT"]


def _check(name, ok, detail, severity="high"):
    return {"name": name, "ok": bool(ok), "detail": detail, "severity": severity}


def _fmt_age(dt):
    delta = datetime.now() - dt
    days, secs = delta.days, delta.seconds
    if days > 0:
        return f"{days}d ago"
    if secs >= 3600:
        return f"{secs // 3600}h ago"
    return f"{secs // 60}m ago"


def _parse_dt(value):
    """Parse the mixed timestamp formats stored across SQLite and Postgres."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).replace(" ", "T").split("+")[0].split(".")[0]
    try:
        return datetime.fromisoformat(text)
    except Exception:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except Exception:
            return None


# ── individual checks ─────────────────────────────────────────────────────────

def _check_database(get_db):
    try:
        conn = get_db()
    except Exception as e:
        return [_check("database.connect", False,
                       f"Cannot connect: {e}. Check DATABASE_URL and whether the "
                       f"Supabase project is paused.", "critical")], None
    checks = [_check("database.connect", True, "Connected", "critical")]
    try:
        for table in ("scans", "market_conditions", "portfolio", "watchlist",
                      "price_cache", "favorites", "alerts"):
            try:
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
                count = row["c"] if row else 0
                checks.append(_check(f"database.table.{table}", True,
                                     f"{count} rows", "medium"))
            except Exception as e:
                checks.append(_check(f"database.table.{table}", False,
                                     f"Query failed: {e}", "critical"))
    except Exception as e:
        checks.append(_check("database.tables", False, str(e), "critical"))
    return checks, conn


def _check_scan_data(conn):
    """The scan pipeline is the heart of the app — verify it produced usable rows."""
    checks = []
    try:
        row = conn.execute(
            "SELECT scan_date, COUNT(*) AS c FROM scans "
            "GROUP BY scan_date ORDER BY scan_date DESC LIMIT 1"
        ).fetchone()
    except Exception as e:
        return [_check("scan.latest", False, f"Query failed: {e}", "critical")]

    if not row:
        return [_check("scan.latest", False,
                       "No scans in the database at all. Run a scan.", "critical")]

    scan_date = row["scan_date"]
    count     = row["c"]
    parsed    = _parse_dt(scan_date)
    age_days  = (datetime.now() - parsed).days if parsed else None

    stale = age_days is not None and age_days > 4
    checks.append(_check(
        "scan.freshness", not stale,
        f"Latest scan {scan_date} ({count} stocks)"
        + (f", {age_days}d old — scans may have stopped running" if stale else ""),
        "high"))

    # A scan that "succeeded" but wrote empty columns is the silent failure mode
    # that makes the dashboard show blanks and "Unknown".
    try:
        q = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN score IS NULL OR score = 0 THEN 1 ELSE 0 END) AS no_score,"
            " SUM(CASE WHEN price IS NULL OR price = 0 THEN 1 ELSE 0 END) AS no_price,"
            " SUM(CASE WHEN sector IS NULL OR sector = 'Unknown' THEN 1 ELSE 0 END) AS no_sector,"
            " MAX(score) AS max_score"
            " FROM scans WHERE scan_date = ?", (scan_date,)
        ).fetchone()
        total     = q["total"] or 0
        no_price  = q["no_price"] or 0
        no_sector = q["no_sector"] or 0
        max_score = q["max_score"] or 0

        checks.append(_check(
            "scan.prices", total > 0 and no_price < total,
            f"{total - no_price}/{total} stocks have a price"
            + (" — price source failed during the scan" if no_price == total else ""),
            "high"))
        checks.append(_check(
            "scan.sectors", total > 0 and no_sector < total,
            f"{total - no_sector}/{total} stocks have a sector"
            + (" — all Unknown, sector lookup failed" if no_sector == total else ""),
            "medium"))
        # Max score tells us how many data sources actually answered.
        if max_score >= 25:
            detail, ok = f"Top score {max_score:.0f} — most sources responded", True
        elif max_score >= 12:
            detail, ok = (f"Top score {max_score:.0f} — analyst sources blocked, "
                          f"running on insider/Vanguard/momentum only"), True
        else:
            detail, ok = (f"Top score {max_score:.0f} — nearly every data source "
                          f"failed; scores are not meaningful"), False
        checks.append(_check("scan.score_quality", ok, detail, "high"))
    except Exception as e:
        checks.append(_check("scan.columns", False, f"Query failed: {e}", "high"))

    return checks


def _check_prices(conn, batch_fetch_prices, scrape_price):
    """Can we resolve a current price, and is it actually correct?"""
    checks = []

    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c, MAX(fetched_at) AS newest FROM price_cache"
        ).fetchone()
        newest = _parse_dt(row["newest"]) if row else None
        checks.append(_check(
            "price.cache", bool(row and row["c"]),
            f"{row['c'] if row else 0} cached"
            + (f", newest {_fmt_age(newest)}" if newest else ""),
            "medium"))
    except Exception as e:
        checks.append(_check("price.cache", False, str(e), "medium"))

    # Every portfolio holding must resolve to a price or the P/L is wrong —
    # this is exactly the "portfolio shows 0% gain" failure.
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM portfolio").fetchall()
        tickers = [r["ticker"] for r in rows]
        if tickers:
            prices  = batch_fetch_prices(tickers, conn)
            missing = [t for t in tickers if not prices.get(t)]
            checks.append(_check(
                "price.portfolio_coverage", not missing,
                f"{len(tickers) - len(missing)}/{len(tickers)} holdings priced"
                + (f"; missing {', '.join(missing)}" if missing else ""),
                "high"))
        else:
            checks.append(_check("price.portfolio_coverage", True,
                                 "No holdings to price", "low"))
    except Exception as e:
        checks.append(_check("price.portfolio_coverage", False, str(e), "high"))

    return checks


def _check_price_accuracy(conn, scrape_price):
    """Compare our stored price against an independent source (slow: network)."""
    checks = []
    for ticker in _PRICE_REFS:
        try:
            ref = scrape_price(ticker)
            if not ref:
                checks.append(_check(f"accuracy.{ticker}", False,
                                     "Reference source unreachable — cannot verify",
                                     "medium"))
                continue
            row = conn.execute(
                "SELECT price FROM price_cache WHERE ticker = ?", (ticker,)
            ).fetchone()
            if not row or not row["price"]:
                checks.append(_check(f"accuracy.{ticker}", True,
                                     f"Reference ${ref} (nothing cached to compare)",
                                     "low"))
                continue
            ours = float(row["price"])
            drift = abs(ours - ref) / ref * 100 if ref else 0
            checks.append(_check(
                f"accuracy.{ticker}", drift <= 10,
                f"ours ${ours:.2f} vs reference ${ref:.2f} ({drift:.1f}% off)"
                + (" — stored price is stale or wrong" if drift > 10 else ""),
                "high"))
        except Exception as e:
            checks.append(_check(f"accuracy.{ticker}", False, str(e), "medium"))
    return checks


def _check_upstreams(scrape_price):
    """Which external data sources actually answer from this host?"""
    import requests
    checks = []

    price = None
    try:
        price = scrape_price("AAPL")
    except Exception:
        pass
    checks.append(_check(
        "upstream.price_scrape", bool(price),
        f"Stooq/Yahoo HTML returned ${price}" if price
        else "No price source reachable — portfolio and prices will be stale",
        "high"))

    for name, url in (("yahoo", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"),
                      ("openinsider", "http://openinsider.com/latest-cluster-buys")):
        try:
            r = requests.get(url, timeout=8, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            ok = r.status_code == 200
            checks.append(_check(f"upstream.{name}", ok,
                                 f"HTTP {r.status_code}"
                                 + (" — blocked from this host" if not ok else ""),
                                 "medium"))
        except Exception as e:
            checks.append(_check(f"upstream.{name}", False,
                                 f"{type(e).__name__}: {e}", "medium"))
    return checks


def _check_config():
    """Report only whether each variable is set — never its value."""
    required = {
        "DATABASE_URL":  "critical",
        "APP_PASSWORD":  "critical",
    }
    optional = {
        "ANTHROPIC_API_KEY": "medium",
        "RESEND_KEY":        "low",
        "VAPID_PRIVATE":     "low",
        "VAPID_PUBLIC":      "low",
        "SCAN_TOKEN":        "medium",
        "SECRET_KEY":        "medium",
    }
    checks = []
    for var, sev in required.items():
        checks.append(_check(f"config.{var}", bool(os.environ.get(var)),
                             "set" if os.environ.get(var) else
                             "MISSING — the app cannot work without this", sev))
    for var, sev in optional.items():
        is_set = bool(os.environ.get(var))
        checks.append(_check(f"config.{var}", True,
                             "set" if is_set else "not set (feature disabled)", sev))
    return checks


def _check_pages(app, conn, include_slow):
    """Render each page server-side and report which ones raise."""
    checks = []
    try:
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "healthcheck"
        for path, name, slow in _PAGES:
            if slow and not include_slow:
                continue
            try:
                resp = client.get(path)
                ok = resp.status_code == 200
                checks.append(_check(
                    f"page.{name}", ok,
                    f"HTTP {resp.status_code}"
                    + ("" if ok else " — this page is broken for users"),
                    "critical" if not ok else "low"))
            except Exception as e:
                checks.append(_check(f"page.{name}", False,
                                     f"{type(e).__name__}: {e}", "critical"))

        # Detail pages take a ticker, so drive one with a ticker that exists.
        ticker = None
        if conn is not None:
            try:
                row = conn.execute(
                    "SELECT ticker FROM scans ORDER BY scan_date DESC LIMIT 1"
                ).fetchone()
                ticker = row["ticker"] if row else None
            except Exception:
                pass
        if ticker:
            try:
                resp = client.get(f"/stock/{ticker}")
                ok = resp.status_code == 200
                checks.append(_check(
                    "page.stock_detail", ok,
                    f"/stock/{ticker} → HTTP {resp.status_code}"
                    + ("" if ok else " — stock detail pages are broken"),
                    "critical" if not ok else "low"))
            except Exception as e:
                checks.append(_check("page.stock_detail", False,
                                     f"{type(e).__name__}: {e}", "critical"))
    except Exception as e:
        checks.append(_check("page.harness", False, str(e), "high"))
    return checks


def _check_backtest(conn):
    """The backtest loading is not enough — it must actually produce trades."""
    try:
        from features import run_backtest
        result = run_backtest(conn)
    except Exception as e:
        return [_check("feature.backtest", False,
                       f"{type(e).__name__}: {e}", "high")]
    if result.get("error"):
        # Too little history is a normal state, not a defect.
        expected = "at least 2 days" in result["error"]
        return [_check("feature.backtest", expected, result["error"],
                       "low" if expected else "high")]
    trades = result.get("num_trades", 0)
    return [_check("feature.backtest", trades > 0,
                   f"{trades} trades, {result.get('total_return')}% return"
                   if trades else
                   "Ran but produced 0 trades — the pick filter rejects everything",
                   "low" if trades else "high")]


# ── entry point ───────────────────────────────────────────────────────────────

def run_health_check(app, get_db, batch_fetch_prices, scrape_price, full=False):
    started = datetime.now()
    checks  = []

    checks += _check_config()

    db_checks, conn = _check_database(get_db)
    checks += db_checks

    try:
        if conn is not None:
            checks += _check_scan_data(conn)
            checks += _check_prices(conn, batch_fetch_prices, scrape_price)
            checks += _check_backtest(conn)
            if full:
                checks += _check_price_accuracy(conn, scrape_price)

        if full:
            checks += _check_upstreams(scrape_price)

        checks += _check_pages(app, conn, include_slow=full)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    failed   = [c for c in checks if not c["ok"]]
    critical = [c for c in failed if c["severity"] == "critical"]

    return {
        "ok":       not critical,
        "degraded": bool(failed) and not critical,
        "summary":  (f"{len(checks) - len(failed)}/{len(checks)} checks passed"
                     + (f", {len(critical)} critical" if critical else "")),
        "failures": [f"{c['name']}: {c['detail']}" for c in failed],
        "checks":   checks,
        "mode":     "full" if full else "fast",
        "elapsed":  round((datetime.now() - started).total_seconds(), 2),
        "time":     started.isoformat(timespec="seconds"),
    }
