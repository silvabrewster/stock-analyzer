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

        # Score badge color
        if score >= 70:
            sc, sb = "#2d6a2d", "#e6f4e6"
        elif score >= 50:
            sc, sb = "#7a6a00", "#fff8dc"
        else:
            sc, sb = "#5f5e5a", "#f1efe8"

        # Upside color
        try:
            upside_val = float(str(upside).replace("%",""))
            uc = "#2d6a2d" if upside_val >= 15 else ("#7a6a00" if upside_val >= 5 else "#8b1a1a")
        except Exception:
            uc = "#222"

        rows += f"""
        <tr style="background:{bg_row};">
          <td style="padding:8px 12px;font-weight:600;">{row['Ticker']} {row.get('New?','')} {row.get('Vol Spike','')}</td>
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
          <td style="padding:8px 12px;text-align:center;">{row.get('Beta','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Div Yield','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('52w Position','–')}</td>
          <td style="padding:8px 12px;text-align:center;font-size:11px;">{row.get('Sector','–')}</td>
        </tr>"""

    warning_html = f'<p style="background:#fff3cd;border-left:4px solid #ffc107;padding:10px 14px;border-radius:4px;">{warning}</p>' if warning else ""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:900px;margin:auto;">

      <h2 style="color:#1a1a2e;margin-bottom:4px;">📈 Stock Convergence Report — {today}</h2>
      <p style="color:#888;font-size:13px;margin-top:0;">7-Source Consensus Engine | v3.0</p>

      <!-- Market conditions bar -->
      <div style="background:#1a1a2e;color:white;border-radius:8px;padding:12px 18px;margin-bottom:16px;display:flex;gap:24px;font-size:13px;">
        <span>S&P 500: <strong>{sp.get('price','n/a')}</strong> <span style="color:{sp_color};">{sp_arrow}{abs(sp_chg)}%</span></span>
        <span>VIX: <strong>{vix.get('price','n/a')}</strong></span>
        <span>10yr Yield: <strong>{tny.get('price','n/a')}%</strong></span>
      </div>

      {warning_html}

      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead>
          <tr style="background:#1a1a2e;color:white;">
            <th style="padding:10px 12px;text-align:left;">Ticker</th>
            <th style="padding:10px 12px;">Score</th>
            <th style="padding:10px 12px;">Sources</th>
            <th style="padding:10px 12px;">Streak</th>
            <th style="padding:10px 12px;">Yahoo</th>
            <th style="padding:10px 12px;">Zacks</th>
            <th style="padding:10px 12px;">MS</th>
            <th style="padding:10px 12px;">Insider</th>
            <th style="padding:10px 12px;">EPS Rev</th>
            <th style="padding:10px 12px;">Beats S&P</th>
            <th style="padding:10px 12px;">Price</th>
            <th style="padding:10px 12px;">Upside</th>
            <th style="padding:10px 12px;">Beta</th>
            <th style="padding:10px 12px;">Div</th>
            <th style="padding:10px 12px;">52w Pos</th>
            <th style="padding:10px 12px;">Sector</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <br>
      <p style="font-size:11px;color:#888;line-height:1.8;">
        Score: Yahoo(22) + Zacks(22) + Morningstar(18) + Insider(12) + Vanguard(10) + EPS Rev(8) + RS(8)<br>
        🆕 = New to top 10 today &nbsp;|&nbsp; 🔥Xd = X-day streak &nbsp;|&nbsp; 🔥 = Volume spike<br>
        Upside color: <span style="color:#2d6a2d;">■</span> ≥15% &nbsp; <span style="color:#7a6a00;">■</span> 5–14% &nbsp; <span style="color:#8b1a1a;">■</span> &lt;5%<br>
        52w Position: 0% = at yearly low, 100% = at yearly high<br><br>
        <em>Not financial advice. Always do your own research before investing.</em>
      </p>
    </body></html>
    """

    try:
        response = req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
            json={"from": EMAIL_FROM, "to": [EMAIL_TO],
                  "subject": f"📈 Stock Convergence Report — {today}", "html": html}
        )
        if response.status_code in (200, 201):
            print(f"  ✓ Email sent to {EMAIL_TO}")
        else:
            print(f"  ✗ Email failed: {response.status_code} {response.text}")
    except Exception as e:
        print(f"  ✗ Email failed: {e}")

# ── daily job ─────────────────────────────────────────────────────────────────

def daily_job():
    print(f"\n{'='*55}")
    print(f"  Daily Stock Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")
    try:
        df, market, warning = run_analyzer()
df, market, warning = run_analyzer()
send_email(df, market, warning)
        send_email(df, market, warning)
        print(f"\n  ✓ Done! Next run at {RUN_TIME} UTC tomorrow.")
    except Exception as e:
        print(f"\n  ✗ Error: {e}")

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════╗
║   Stock Convergence Scheduler  v3.0      ║
║   Runs daily at {RUN_TIME} UTC (7AM Pacific)  ║
║   Sends to: {EMAIL_TO[:28]}  ║
╚══════════════════════════════════════════╝
  Press Ctrl+C to stop.
    """)
    print("  Running first scan now...")
    daily_job()
    schedule.every().day.at(RUN_TIME).do(daily_job)
    print(f"\n  ✓ Scheduled for {RUN_TIME} UTC every day.")
    print("  Waiting for next run...\n")
    while True:
        schedule.run_pending()
        time.sleep(60)
