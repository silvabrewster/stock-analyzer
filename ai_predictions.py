"""
ai_predictions.py
=================
Claude picks the top stocks of the day, predicts direction over the next week,
and tracks its own accuracy — learning from mistakes each cycle.

Flow:
  1. On load, check if today already has predictions → return cached.
  2. Before generating new predictions, evaluate yesterday's batch:
     - Fetch actual current prices vs. price_at_prediction
     - Compare to predicted_direction
     - If wrong, ask Claude to analyse why and extract lessons
  3. Pass those lessons as context when generating today's predictions.
  4. Store everything in ai_predictions table.
"""

import json
import os
import requests
from datetime import datetime, timedelta


ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL         = "claude-sonnet-4-6"


# ── helpers ───────────────────────────────────────────────────────────────────

def _claude(prompt: str, max_tokens: int = 600) -> str:
    if not ANTHROPIC_KEY:
        return ""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model":     MODEL,
                "max_tokens": max_tokens,
                "messages":  [{"role": "user", "content": prompt}],
            },
            timeout=45,
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"[ai_predictions] Claude error: {e}")
    return ""


def _fetch_price(ticker: str) -> float | None:
    try:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as ex:
            fi = ex.submit(lambda t=ticker: yf.Ticker(t).fast_info).result(timeout=8)
        p = fi.last_price or fi.regular_market_price
        return round(float(p), 2) if p else None
    except Exception:
        return None


# ── evaluate old predictions ──────────────────────────────────────────────────

def evaluate_pending_predictions(conn) -> str:
    """
    Checks predictions that are old enough (≥5 days) but not yet evaluated.
    Returns a 'lessons' string that will be injected into the next prediction prompt.
    """
    cutoff     = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    pending    = conn.execute("""
        SELECT id, ticker, predicted_direction, price_at_prediction, prediction_date
        FROM ai_predictions
        WHERE was_correct IS NULL AND prediction_date <= ?
        ORDER BY prediction_date DESC LIMIT 10
    """, (cutoff,)).fetchall()

    if not pending:
        return ""

    wrong_cases = []
    for row in pending:
        ticker     = row["ticker"]
        predicted  = row["predicted_direction"]
        old_price  = row["price_at_prediction"]
        current    = _fetch_price(ticker)
        if not current or not old_price:
            continue

        pct_chg = (current - old_price) / old_price * 100
        if pct_chg > 2:
            actual = "bullish"
        elif pct_chg < -2:
            actual = "bearish"
        else:
            actual = "neutral"

        was_correct = 1 if predicted == actual else 0

        error_text = ""
        if not was_correct:
            wrong_cases.append({"ticker": ticker, "predicted": predicted,
                                 "actual": actual, "pct_chg": round(pct_chg, 1)})

        conn.execute("""
            UPDATE ai_predictions
            SET price_1w_later=?, actual_direction=?, was_correct=?
            WHERE id=?
        """, (current, actual, was_correct, row["id"]))

    conn.commit()

    if not wrong_cases:
        return ""

    # Ask Claude to analyse the mistakes
    cases_text = "\n".join(
        f"- {c['ticker']}: predicted {c['predicted']}, actual {c['actual']} ({c['pct_chg']:+.1f}%)"
        for c in wrong_cases
    )
    analysis = _claude(
        f"""You previously made incorrect stock direction predictions. Analyse each mistake briefly and extract 2-3 actionable lessons that would improve future predictions.

Wrong predictions:
{cases_text}

Reply with a concise JSON object:
{{
  "lessons": ["lesson1", "lesson2", "lesson3"]
}}
Only output the JSON, nothing else.""",
        max_tokens=400,
    )

    lessons = []
    try:
        parsed  = json.loads(analysis)
        lessons = parsed.get("lessons", [])
        conn.execute(
            "UPDATE ai_predictions SET lessons=? WHERE id IN ({})".format(
                ",".join("?" * len(pending))
            ),
            [json.dumps(lessons)] + [r["id"] for r in pending],
        )
        conn.commit()
    except Exception:
        pass

    return "\n".join(f"• {l}" for l in lessons) if lessons else ""


# ── generate today's picks ────────────────────────────────────────────────────

def generate_picks(conn) -> list:
    """
    Generates Claude's stock picks + predictions for today.
    Skips if predictions already exist for today.
    Returns list of prediction dicts.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # Return cached predictions for today
    existing = conn.execute(
        "SELECT * FROM ai_predictions WHERE prediction_date=? ORDER BY confidence DESC",
        (today,)
    ).fetchall()
    if existing:
        return [dict(r) for r in existing]

    # Evaluate old predictions first and collect lessons
    lessons_text = evaluate_pending_predictions(conn)

    # Fetch today's top stocks from the DB
    row = conn.execute("SELECT MAX(scan_date) as latest FROM scans").fetchone()
    if not row or not row["latest"]:
        return []
    latest_date = row["latest"]

    stocks = conn.execute("""
        SELECT ticker, score, sector, upside_pct, price, short_pct,
               week52_pos, vol_spike, streak, alignment
        FROM scans WHERE scan_date=? ORDER BY score DESC LIMIT 15
    """, (latest_date,)).fetchall()

    if not stocks:
        return []

    # Build stock context for prompt
    stock_lines = []
    for s in stocks:
        stock_lines.append(
            f"{s['ticker']}: score={s['score']}, sector={s['sector']}, "
            f"upside={s['upside_pct']}%, 52w_pos={s['week52_pos']}%, "
            f"streak={s['streak']}, alignment={s['alignment']}, short%={s['short_pct']}"
        )
    stocks_text = "\n".join(stock_lines)

    lessons_section = f"\n\nLessons from your recent incorrect predictions — apply these:\n{lessons_text}" if lessons_text else ""

    prompt = f"""You are a stock analyst reviewing today's top-scoring stocks from a multi-source convergence scanner.{lessons_section}

Today's candidates (score = 0-100 consensus strength):
{stocks_text}

Pick your TOP 5 stocks for the next 7 days and predict direction. For each, give:
- predicted_direction: "bullish", "bearish", or "neutral"
- confidence: 1-100
- reasoning: 1-2 sentences max

Reply with ONLY a valid JSON array, no markdown, no extra text:
[
  {{"ticker": "XXX", "predicted_direction": "bullish", "confidence": 75, "reasoning": "..."}}
]"""

    raw = _claude(prompt, max_tokens=700)
    if not raw:
        return []

    try:
        # Strip any accidental markdown fences
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        picks = json.loads(raw)
    except Exception as e:
        print(f"[ai_predictions] JSON parse error: {e}\nRaw: {raw[:200]}")
        return []

    results = []
    for pick in picks:
        ticker    = pick.get("ticker", "").upper()
        direction = pick.get("predicted_direction", "neutral")
        conf      = pick.get("confidence", 50)
        reasoning = pick.get("reasoning", "")

        # Current price
        price_row = conn.execute(
            "SELECT price FROM scans WHERE ticker=? AND scan_date=?", (ticker, latest_date)
        ).fetchone()
        price = float(price_row["price"]) if price_row and price_row["price"] else None

        conn.execute("""
            INSERT INTO ai_predictions
            (prediction_date, ticker, predicted_direction, confidence, reasoning, price_at_prediction)
            VALUES (?,?,?,?,?,?)
        """, (today, ticker, direction, conf, reasoning, price))

        results.append({
            "prediction_date":  today,
            "ticker":           ticker,
            "predicted_direction": direction,
            "confidence":       conf,
            "reasoning":        reasoning,
            "price_at_prediction": price,
            "was_correct":      None,
        })

    conn.commit()
    return results


# ── public entry point ────────────────────────────────────────────────────────

def get_picks_with_history(conn) -> dict:
    """Returns today's picks + recent accuracy stats."""
    picks   = generate_picks(conn)

    # Historical accuracy
    history = conn.execute("""
        SELECT was_correct, COUNT(*) as cnt
        FROM ai_predictions
        WHERE was_correct IS NOT NULL
        GROUP BY was_correct
    """).fetchall()

    correct = sum(r["cnt"] for r in history if r["was_correct"] == 1)
    total   = sum(r["cnt"] for r in history)
    accuracy = round(correct / total * 100) if total else None

    # Most recent lessons
    last_lessons_row = conn.execute("""
        SELECT lessons FROM ai_predictions
        WHERE lessons IS NOT NULL AND lessons != ''
        ORDER BY prediction_date DESC LIMIT 1
    """).fetchone()
    lessons = []
    if last_lessons_row:
        try:
            lessons = json.loads(last_lessons_row["lessons"])
        except Exception:
            pass

    return {
        "picks":         picks,
        "accuracy":      accuracy,
        "total_checked": total,
        "correct":       correct,
        "lessons":       lessons,
    }
