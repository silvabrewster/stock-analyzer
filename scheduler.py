"""
Stock Convergence Scheduler v3.0
==================================
Runs analyzer daily at 7:00 AM Pacific (14:00 UTC),
emails a rich report via Resend, 
"""

import schedule
import time
import requests as req
from datetime import datetime
import pandas as pd

EMAIL_FROM      = "onboarding@resend.dev"
EMAIL_TO        = "silvabrayden0@gmail.com"
RESEND_KEY      = "re_cWJizFpm_1zrGKUJ2djd7S5mbPQHKJorY"
RUN_TIME        = "14:00"   # 7:00 AM Pacific (UTC-7)
TOP_N           = 10

# ── run analyzer ─────────────────────────────────────────────────────────────

def run_analyzer() -> tuple[pd.DataFrame, dict]:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running analyzer...")
    from analyzer import (
        build_universe, get_market_conditions,
        get_vanguard_top_holdings, get_yahoo_strong_buys,
        get_zacks_strong_buys, get_morningstar_ratings,
        get_insider_buyers, get_relative_strength,
        compute_consensus, check_sector_concentration,
        load_streaks, load_yesterday_top, save_streaks,
    )
    universe      = build_universe()
    old_streaks   = load_streaks()
    yesterday_top = load_yesterday_top()
    market        = get_market_conditions()
    vanguard_w    = get_vanguard_top_holdings()
    yahoo_data    = get_yahoo_strong_buys(universe)
    zacks_buys    = get_zacks_strong_buys()
    ms_data       = get_morningstar_ratings(universe)
    insiders      = get_insider_buyers()
    rs            = get_relative_strength(universe)

    df = compute_consensus(
        universe, yahoo_data, zacks_buys, ms_data,
        vanguard_w, insiders, rs, old_streaks, yesterday_top
    )

    top10 = df.head(10)["Ticker"].tolist()
    save_streaks(top10, old_streaks)
    warning = check_sector_concentration(df, yahoo_data)

    return df, market, warning


# ── send email ────────────────────────────────────────────────────────────────

def send_email(df: pd.DataFrame, market: dict, warning: str | None):
    today = datetime.now().strftime("%B %d, %Y")
    top   = df.head(TOP_N)

    sp    = market.get("sp500", {})
    vix   = market.get("vix", {})
    tny   = market.get("tny", {})

    sp_chg = sp.get("chg", 0) or 0
    sp_color = "#2d6a2d" if sp_chg >= 0 else "#8b1a1a"
    sp_arrow = "▲" if sp_chg >= 0 else "▼"

    rows = ""

    for i, (_, row) in enumerate(top.iterrows()):
        score    = row["Consensus Score"]
        upside   = row.get("Upside %", "n/a")
        bg_row   = "#f9f9f9" if i % 2 == 0 else "white"

        if score >= 70:
            sc, sb = "#2d6a2d", "#e6f4e6"
        elif score >= 50:
            sc, sb = "#7a6a00", "#fff8dc"
        else:
            sc, sb = "#5f5e5a", "#f1efe8"

        try:
            upside_val = float(str(upside).replace("%",""))
            uc = "#2d6a2d" if upside_val >= 15 else ("#7a6a00" if upside_val >= 5 else "#8b1a1a")
        except Exception:
            uc = "#222"

        rows += f"""
        <tr style="background:{bg_row};">
          <td style="padding:8px 12px;font-weight:600;">{row['Ticker']}</td>
          <td style="padding:8px 12px;text-align:center;">
            <span style="background:{sb};color:{sc};padding:3px 10px;border-radius:12px;font-weight:600;">{int(score)}</span>
          </td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Sources Agree','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Streak','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Yahoo SB','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Zacks #1','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Morningstar ★★★','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Insider Buy','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('EPS Rev ↑','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Beats S&P','–')}</td>
          <td style="padding:8px 12px;text-align:center;">${row.get('Price','–')}</td>
          <td style="padding:8px 12px;text-align:center;color:{uc};font-weight:600;">{upside}</td>
        </tr>
        """

    warning_html = f'<p style="background:#fff3cd;padding:10px;">{warning}</p>' if warning else ""

    html = f"""
    <html><body style="font-family:Arial;max-width:900px;margin:auto;">

      <h2>📈 Stock Convergence Report — {today}</h2>

      <div style="background:#1a1a2e;color:white;padding:10px;">
        S&P: {sp.get('price','n/a')} {sp_arrow}{abs(sp_chg)}%
      </div>

      {warning_html}

      <table style="width:100%;font-size:12px;">
        <tbody>{rows}</tbody>
      </table>

    </body></html>
    """

    try:
     response = req.post(
    "https://api.resend.com/emails",
    headers={
        "Authorization": f"Bearer {RESEND_KEY}",
        "Content-Type": "application/json"
    },
    json={
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": f"📈 Stock Report — {today}",
        "html": html
    }
)

print(response.status_code)
print(response.text)
            }
        )

        if response.status_code in (200, 201):
            print(f"✓ Email sent to {EMAIL_TO}")
        else:
            print(f"✗ Email failed: {response.text}")

    except Exception as e:
        print(f"✗ Email failed: {e}")


# ── daily job ─────────────────────────────────────────────────────────────────
def daily_job():
    print(f"\n{'='*55}")
    print(f"  Daily Stock Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")
    try:
        df, market, warning = run_analyzer()
        send_email(df, market, warning)
        print(f"\n  ✓ Email sent (or attempted).")
    except Exception as e:
        print(f"\n  ✗ Error: {e}")
# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Stock Scheduler v3.0 starting...")
    daily_job()

    schedule.every().day.at(RUN_TIME).do(daily_job)

    print(f"Scheduled daily at {RUN_TIME} UTC")

    while True:
        schedule.run_pending()
        time.sleep(60)
