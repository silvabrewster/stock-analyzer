"""
Stock Convergence Scheduler v3.0 (Railway Clean Version)
No Google Drive — Email only
"""

import os
import requests as req
from datetime import datetime

EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")
EMAIL_TO   = os.getenv("EMAIL_TO")
RESEND_KEY = os.getenv("RESEND_KEY")
TOP_N      = 10


# ── analyzer ─────────────────────────────────────────────

def run_analyzer():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running analyzer...")

    from analyzer import (
        build_universe,
        get_market_conditions,
        get_vanguard_top_holdings,
        get_yahoo_strong_buys,
        get_zacks_strong_buys,
        get_morningstar_ratings,
        get_insider_buyers,
        get_relative_strength,
        compute_consensus,
        load_streaks,
        load_yesterday_top,
        save_streaks,
    )

    universe = build_universe()
    old_streaks = load_streaks()
    yesterday_top = load_yesterday_top()

    market = get_market_conditions()
    vanguard_w = get_vanguard_top_holdings()
    yahoo_data = get_yahoo_strong_buys(universe)
    zacks_buys = get_zacks_strong_buys()
    ms_data = get_morningstar_ratings(universe)
    insiders = get_insider_buyers()
    rs = get_relative_strength(universe)

    df = compute_consensus(
        universe,
        yahoo_data,
        zacks_buys,
        ms_data,
        vanguard_w,
        insiders,
        rs,
        old_streaks,
        yesterday_top
    )

    top10 = df.head(10)["Ticker"].tolist()
    save_streaks(top10, old_streaks)

    return df, market


# ── email ────────────────────────────────────────────────

def send_email(df):
    today = datetime.now().strftime("%B %d, %Y")
    top = df.head(TOP_N)

    rows = ""
    for _, row in top.iterrows():
        rows += f"<tr><td>{row['Ticker']}</td><td>{int(row['Consensus Score'])}</td></tr>"

    html = f"""
    <h2>📈 Stock Report — {today}</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr>
            <th>Ticker</th>
            <th>Score</th>
        </tr>
        {rows}
    </table>
    """

    response = req.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "from": EMAIL_FROM,
            "to": [EMAIL_TO],
            "subject": f"Stock Report — {today}",
            "html": html
        }
    )

    if response.status_code in (200, 201):
        print("  ✓ Email sent")
    else:
        print(f"  ✗ Email failed: {response.text}")


# ── job ──────────────────────────────────────────────────

def daily_job():
    try:
        df, market = run_analyzer()
        send_email(df)
        print("  ✓ Done")
    except Exception as e:
        print(f"  ✗ Error: {e}")


# ── entry point ──────────────────────────────────────────

if __name__ == "__main__":
    print("Running job (Railway)...")
    daily_job()
