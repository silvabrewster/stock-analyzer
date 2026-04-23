"""
Stock Convergence Scheduler
============================
Runs the analyzer every morning at 7:00 AM,
emails top results to silvabrayden0@gmail.com,
and saves a running Excel log over time.

Run once to start:
    python scheduler.py
"""

import schedule
import time
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import pandas as pd

# ── config ───────────────────────────────────────────────────────────────────

EMAIL_FROM    = "silvabrayden0@gmail.com"
EMAIL_TO      = "silvabrayden0@gmail.com"
EMAIL_PASS    = "bzmwjvbkuuechsgq"
RUN_TIME      = "07:00"
EXCEL_LOG     = "stock_results_log.xlsx"
TOP_N         = 10

# ── run analyzer ─────────────────────────────────────────────────────────────

def run_analyzer() -> pd.DataFrame:
    """Imports and runs the analyzer, returns results DataFrame."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running analyzer...")

    from analyzer import (
        build_universe,
        get_vanguard_top_holdings,
        get_yahoo_strong_buys,
        get_zacks_strong_buys,
        get_morningstar_ratings,
        compute_consensus,
    )

    universe         = build_universe()
    vanguard_weights = get_vanguard_top_holdings()
    yahoo_data       = get_yahoo_strong_buys(universe)
    zacks_buys       = get_zacks_strong_buys()
    morningstar_data = get_morningstar_ratings(universe)

    df = compute_consensus(
        universe, yahoo_data, zacks_buys, morningstar_data, vanguard_weights
    )
    return df

# ── save to excel ─────────────────────────────────────────────────────────────

def save_to_excel(df: pd.DataFrame):
    """Appends today's top results to the running Excel log."""
    today = datetime.now().strftime("%Y-%m-%d")
    top   = df.head(TOP_N).copy()
    top.insert(0, "Date", today)

    if os.path.exists(EXCEL_LOG):
        existing = pd.read_excel(EXCEL_LOG)
        updated  = pd.concat([existing, top], ignore_index=True)
    else:
        updated = top

    updated.to_excel(EXCEL_LOG, index=False)
    print(f"  ✓ Results saved to {EXCEL_LOG}")

# ── send email ────────────────────────────────────────────────────────────────

def send_email(df: pd.DataFrame):
    """Sends top stock picks via Gmail."""
    today = datetime.now().strftime("%B %d, %Y")
    top   = df.head(TOP_N)

    # Build HTML table
    rows = ""
    for _, row in top.iterrows():
        score = row["Consensus Score"]
        color = "#2d6a2d" if score >= 70 else "#7a6a00"
        rows += f"""
        <tr>
          <td style="padding:8px 12px;font-weight:600;">{row['Ticker']}</td>
          <td style="padding:8px 12px;text-align:center;">
            <span style="background:{'#e6f4e6' if score >= 70 else '#fff8dc'};
                         color:{color};padding:3px 10px;border-radius:12px;
                         font-weight:600;">{int(score)}</span>
          </td>
          <td style="padding:8px 12px;text-align:center;">{row['Sources Agree']}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Yahoo SB','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Zacks #1','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Morningstar ★★★★','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Vanguard Wt%','–')}</td>
          <td style="padding:8px 12px;text-align:center;">${row.get('Price','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Upside %','–')}%</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:700px;margin:auto;">
      <h2 style="color:#1a1a2e;">📈 Stock Convergence Report — {today}</h2>
      <p>Top {TOP_N} stocks where multiple analysis sources agree on a <strong>Strong Buy</strong>.</p>

      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#1a1a2e;color:white;">
            <th style="padding:10px 12px;text-align:left;">Ticker</th>
            <th style="padding:10px 12px;">Score</th>
            <th style="padding:10px 12px;">Sources</th>
            <th style="padding:10px 12px;">Yahoo</th>
            <th style="padding:10px 12px;">Zacks</th>
            <th style="padding:10px 12px;">Morningstar</th>
            <th style="padding:10px 12px;">Vanguard%</th>
            <th style="padding:10px 12px;">Price</th>
            <th style="padding:10px 12px;">Upside</th>
          </tr>
        </thead>
        <tbody>
          {''.join([f'<tr style="background:{"#f9f9f9" if i%2==0 else "white"}">' + rows.split('<tr>')[i+1] for i in range(len(top))])}
        </tbody>
      </table>

      <br>
      <p style="font-size:12px;color:#888;">
        Score legend: Yahoo(30pts) + Zacks(30pts) + Morningstar(25pts) + Vanguard(15pts)<br>
        🟢 ≥70 = High conviction &nbsp;|&nbsp; 🟡 40–69 = Moderate conviction<br><br>
        <em>This is not financial advice. Always do your own research before investing.</em>
      </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Stock Convergence Report — {today}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    # Attach Excel log
    if os.path.exists(EXCEL_LOG):
        with open(EXCEL_LOG, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={EXCEL_LOG}"
            )
            msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASS)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"  ✓ Email sent to {EMAIL_TO}")
    except Exception as e:
        print(f"  ✗ Email failed: {e}")

# ── daily job ─────────────────────────────────────────────────────────────────

def daily_job():
    print(f"\n{'='*50}")
    print(f"  Daily Stock Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    try:
        df = run_analyzer()
        save_to_excel(df)
        send_email(df)
        print(f"\n  ✓ Done! Next run at {RUN_TIME} tomorrow.")
    except Exception as e:
        print(f"\n  ✗ Error during job: {e}")

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════╗
║   Stock Convergence Scheduler  v1.0      ║
║   Runs daily at {RUN_TIME}                  ║
║   Sends to: {EMAIL_TO[:28]}  ║
╚══════════════════════════════════════════╝

  Scheduler is running. Do not close this window.
  Press Ctrl+C to stop.
    """)

    # Run once immediately on startup
    print("  Running first scan now...")
    daily_job()

    # Then schedule daily at 7:00 AM
    schedule.every().day.at(RUN_TIME).do(daily_job)

    print(f"\n  ✓ Scheduled for {RUN_TIME} AM every day.")
    print("  Waiting for next run...\n")

    while True:
        schedule.run_pending()
        time.sleep(60)
