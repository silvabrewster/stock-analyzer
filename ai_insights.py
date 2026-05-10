"""
ai_insights.py
==============
Analyzes historical scan data to find which signal combinations
actually led to gains. Called from the /history route.

The AI looks at:
  - Which signals (Yahoo, Zacks, Insider, etc.) appeared most in top performers
  - Which combinations had the best follow-through
  - Day-of-week and streak patterns
  - Sector rotation patterns

Returns actionable insights shown on the History page.
"""

import os
import requests
import json
from datetime import datetime, timedelta


def get_ai_insights(conn) -> dict:
    """
    Analyzes scan history and returns AI-generated insights about
    which patterns are working best. Caches result for 24 hours.
    """
    try:
        # Check cache — insights don't need to refresh more than once a day
        try:
            cached = conn.execute("""
                SELECT value, updated_at FROM insights_cache
                WHERE key = 'ai_insights' LIMIT 1
            """).fetchone()
            if cached:
                age = (datetime.now() - datetime.fromisoformat(cached["updated_at"].replace(" ", "T"))).total_seconds()
                if age < 86400:  # 24 hours
                    return json.loads(cached["value"])
        except Exception:
            pass  # cache table might not exist yet

        # Build analysis data from DB
        analysis = _build_analysis(conn)
        if not analysis or analysis.get("total_scans", 0) < 5:
            return {"ready": False, "reason": "Need more scan history — run at least 5 daily scans first."}

        # Call Claude API
        insights = _call_claude(analysis)

        # Cache it
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS insights_cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO insights_cache (key, value, updated_at)
                VALUES ('ai_insights', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """, (json.dumps(insights), datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            print(f"Cache save error: {e}")

        return insights

    except Exception as e:
        print(f"AI insights error: {e}")
        return {"ready": False, "reason": "Analysis unavailable right now."}


def _build_analysis(conn) -> dict:
    """Pull stats from DB to feed the AI."""
    try:
        # Total scans and date range
        meta = conn.execute("""
            SELECT COUNT(DISTINCT scan_date) as scan_days,
                   MIN(scan_date) as first_scan,
                   MAX(scan_date) as last_scan,
                   COUNT(*) as total_rows
            FROM scans
        """).fetchone()

        if not meta or meta["scan_days"] < 3:
            return {"total_scans": 0}

        # Top 5 all-time performers
        top_tickers = conn.execute("""
            SELECT ticker, COUNT(*) as appearances,
                   AVG(score) as avg_score, MAX(score) as max_score,
                   MAX(streak) as max_streak,
                   SUM(CASE WHEN yahoo_sb='✓' THEN 1 ELSE 0 END) as yahoo_hits,
                   SUM(CASE WHEN zacks='✓' THEN 1 ELSE 0 END) as zacks_hits,
                   SUM(CASE WHEN insider='✓' THEN 1 ELSE 0 END) as insider_hits,
                   SUM(CASE WHEN eps_rev='✓' THEN 1 ELSE 0 END) as eps_hits,
                   SUM(CASE WHEN beats_sp='✓' THEN 1 ELSE 0 END) as rs_hits,
                   MAX(sector) as sector
            FROM scans
            GROUP BY ticker HAVING COUNT(*) >= 2
            ORDER BY AVG(score) DESC LIMIT 10
        """).fetchall()

        # Signal win rates — which signals appear most in high-scoring stocks
        signal_stats = conn.execute("""
            SELECT
                AVG(CASE WHEN yahoo_sb='✓' THEN 1.0 ELSE 0.0 END) as yahoo_rate,
                AVG(CASE WHEN zacks='✓' THEN 1.0 ELSE 0.0 END) as zacks_rate,
                AVG(CASE WHEN morningstar='✓' THEN 1.0 ELSE 0.0 END) as ms_rate,
                AVG(CASE WHEN insider='✓' THEN 1.0 ELSE 0.0 END) as insider_rate,
                AVG(CASE WHEN eps_rev='✓' THEN 1.0 ELSE 0.0 END) as eps_rate,
                AVG(CASE WHEN beats_sp='✓' THEN 1.0 ELSE 0.0 END) as rs_rate,
                AVG(score) as avg_score
            FROM scans WHERE score >= 60
        """).fetchone()

        # Sector breakdown of high scorers
        sectors = conn.execute("""
            SELECT sector, COUNT(*) as count, AVG(score) as avg_score
            FROM scans WHERE score >= 55 AND sector IS NOT NULL AND sector != 'Unknown'
            GROUP BY sector ORDER BY avg_score DESC LIMIT 5
        """).fetchall()

        # Streak analysis — do streaks predict continued performance?
        streak_data = conn.execute("""
            SELECT
                COUNT(*) as total,
                AVG(score) as avg_score,
                AVG(CASE WHEN streak >= 3 THEN score ELSE NULL END) as streak_avg_score
            FROM scans WHERE streak > 0
        """).fetchone()

        # Most common signal combos in top performers
        combo_data = conn.execute("""
            SELECT
                yahoo_sb, zacks, insider, eps_rev, beats_sp,
                COUNT(*) as count, AVG(score) as avg_score
            FROM scans WHERE score >= 65
            GROUP BY yahoo_sb, zacks, insider, eps_rev, beats_sp
            ORDER BY count DESC LIMIT 5
        """).fetchall()

        return {
            "total_scans":  meta["scan_days"],
            "date_range":   f"{meta['first_scan']} to {meta['last_scan']}",
            "top_tickers":  [dict(t) for t in top_tickers],
            "signal_stats": dict(signal_stats) if signal_stats else {},
            "sectors":      [dict(s) for s in sectors],
            "streak_data":  dict(streak_data) if streak_data else {},
            "combo_data":   [dict(c) for c in combo_data],
        }
    except Exception as e:
        print(f"Analysis build error: {e}")
        return {"total_scans": 0}


def _call_claude(analysis: dict) -> dict:
    """Call Claude API to generate insights from the analysis data."""
    top_5 = analysis.get("top_tickers", [])[:5]
    top_names = ", ".join([f"{t['ticker']} (avg {t['avg_score']:.0f}, {t['appearances']}x)" for t in top_5])

    sig = analysis.get("signal_stats", {})
    signal_summary = ""
    if sig:
        rates = [
            ("Yahoo Strong Buy", sig.get("yahoo_rate", 0)),
            ("Zacks #1", sig.get("zacks_rate", 0)),
            ("Morningstar", sig.get("ms_rate", 0)),
            ("Insider Buying", sig.get("insider_rate", 0)),
            ("EPS Revision", sig.get("eps_rate", 0)),
            ("Beats S&P", sig.get("rs_rate", 0)),
        ]
        rates.sort(key=lambda x: x[1], reverse=True)
        signal_summary = ", ".join([f"{name}: {rate*100:.0f}%" for name, rate in rates[:4]])

    sectors = analysis.get("sectors", [])
    sector_summary = ", ".join([f"{s['sector']} (avg {s['avg_score']:.0f})" for s in sectors[:3]])

    streak = analysis.get("streak_data", {})
    streak_note = f"Stocks with 3+ day streaks average {streak.get('streak_avg_score', 0):.0f} vs {streak.get('avg_score', 0):.0f} overall." if streak else ""

    prompt = f"""You are analyzing {analysis['total_scans']} days of stock scan data ({analysis['date_range']}) from a 7-source consensus system.

DATA:
- Top performers: {top_names}
- Signal rates in high-scoring stocks (score≥60): {signal_summary}
- Top sectors: {sector_summary}
- Streak insight: {streak_note}

Generate exactly 3 actionable insights for this investor. Each insight should be 1-2 sentences and start with a specific emoji. Focus on:
1. Which signal combination is working best (most predictive)
2. A sector or stock pattern worth watching
3. One specific tactical recommendation based on the streak/consistency data

Be specific and data-driven. Reference actual tickers or percentages from the data. No generic advice.
Format as JSON with key "insights" containing a list of 3 strings."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         os.environ.get("ANTHROPIC_API_KEY", ""),
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=25
        )
        if resp.status_code == 200:
            text = resp.json()["content"][0]["text"].strip()
            # Parse JSON from response
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            return {
                "ready":    True,
                "insights": data.get("insights", []),
                "updated":  datetime.now().strftime("%b %d at %I:%M %p"),
                "days_analyzed": analysis["total_scans"],
            }
    except Exception as e:
        print(f"Claude insights error: {e}")

    # Fallback: generate rule-based insights
    return _fallback_insights(analysis)


def _fallback_insights(analysis: dict) -> dict:
    """Generate insights without AI if Claude call fails."""
    insights = []
    sig = analysis.get("signal_stats", {})
    top = analysis.get("top_tickers", [])

    if sig:
        best_signal = max([
            ("Yahoo Strong Buy", sig.get("yahoo_rate", 0)),
            ("Insider Buying",   sig.get("insider_rate", 0)),
            ("EPS Revision",     sig.get("eps_rate", 0)),
            ("Beats S&P",        sig.get("rs_rate", 0)),
        ], key=lambda x: x[1])
        insights.append(f"📊 {best_signal[0]} appears in {best_signal[1]*100:.0f}% of high-scoring stocks — it's your most reliable signal so far.")

    if top:
        t = top[0]
        insights.append(f"🏆 {t['ticker']} is your strongest all-time pick with an average score of {t['avg_score']:.0f} across {t['appearances']} appearances.")

    sectors = analysis.get("sectors", [])
    if sectors:
        insights.append(f"🗺️ {sectors[0]['sector']} is producing the highest average scores ({sectors[0]['avg_score']:.0f}) — consider weighting picks from this sector.")

    return {
        "ready":         True,
        "insights":      insights,
        "updated":       datetime.now().strftime("%b %d at %I:%M %p"),
        "days_analyzed": analysis.get("total_scans", 0),
    }
