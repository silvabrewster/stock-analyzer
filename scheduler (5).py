"""
Stock Convergence Scheduler
============================
Runs the analyzer every morning at 7:00 AM,
emails top results via Resend API,
and saves running Excel log to Google Drive.
"""

import schedule
import time
import os
import io
import requests as req
from datetime import datetime
import pandas as pd

# ── config ───────────────────────────────────────────────────────────────────

EMAIL_FROM      = "onboarding@resend.dev"
EMAIL_TO        = "silvabrayden0@gmail.com"
RESEND_KEY      = "re_cWJizFpm_1zrGKUJ2djd7S5mbPQHKJorY"
RUN_TIME        = "07:00"
EXCEL_LOG       = "stock_results_log.xlsx"
TOP_N           = 10
DRIVE_FOLDER_ID = "1NK1gDvWvszHPC6GS3Jmi5O9C4sr9Tn-n"
CREDENTIALS     = "credentials.json"

# ── google drive ──────────────────────────────────────────────────────────────

def get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        scopes = ["https://www.googleapis.com/auth/drive"]
        creds  = service_account.Credentials.from_service_account_file(
            CREDENTIALS, scopes=scopes
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"  ✗ Drive auth failed: {e}")
        return None


def find_file_in_drive(service, filename):
    """Returns file ID if filename exists in the Drive folder."""
    try:
        results = service.files().list(
            q=f"name='{filename}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false",
            fields="files(id, name)"
        ).execute()
        files = results.get("files", [])
        return files[0]["id"] if files else None
    except Exception:
        return None


def save_to_drive(df: pd.DataFrame):
    """Saves Excel log to Google Drive, updating if it already exists."""
    from googleapiclient.http import MediaIoBaseUpload

    service = get_drive_service()
    if not service:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    top   = df.head(TOP_N).copy()
    top.insert(0, "Date", today)

    # Download existing file from Drive if it exists
    file_id = find_file_in_drive(service, EXCEL_LOG)
    if file_id:
        try:
            from googleapiclient.http import MediaIoBaseDownload
            request  = service.files().get_media(fileId=file_id)
            buffer   = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            existing = pd.read_excel(buffer)
            updated  = pd.concat([existing, top], ignore_index=True)
        except Exception:
            updated = top
    else:
        updated = top

    # Write updated Excel to buffer
    buffer = io.BytesIO()
    updated.to_excel(buffer, index=False)
    buffer.seek(0)

    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    media = MediaIoBaseUpload(buffer, mimetype=mime, resumable=True)

    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"  ✓ Excel log updated in Google Drive")
    else:
        service.files().create(
            body={"name": EXCEL_LOG, "parents": [DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id"
        ).execute()
        print(f"  ✓ Excel log created in Google Drive")

# ── run analyzer ─────────────────────────────────────────────────────────────

def run_analyzer() -> pd.DataFrame:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running analyzer...")

    from analyzer import (
        build_universe,
        get_vanguard_top_holdings,
        get_yahoo_strong_buys,
        get_zacks_strong_buys,
        get_morningstar_ratings,
        get_insider_buyers,
        get_relative_strength,
        compute_consensus,
    )

    universe         = build_universe()
    vanguard_weights = get_vanguard_top_holdings()
    yahoo_data       = get_yahoo_strong_buys(universe)
    zacks_buys       = get_zacks_strong_buys()
    morningstar_data = get_morningstar_ratings(universe)
    insider_buyers   = get_insider_buyers()
    rel_strength     = get_relative_strength(universe)

    df = compute_consensus(
        universe, yahoo_data, zacks_buys, morningstar_data,
        vanguard_weights, insider_buyers, rel_strength
    )
    return df

# ── send email ────────────────────────────────────────────────────────────────

def send_email(df: pd.DataFrame):
    today = datetime.now().strftime("%B %d, %Y")
    top   = df.head(TOP_N)

    rows = ""
    for i, (_, row) in enumerate(top.iterrows()):
        score    = row["Consensus Score"]
        color    = "#2d6a2d" if score >= 70 else "#7a6a00"
        bg_score = "#e6f4e6" if score >= 70 else "#fff8dc"
        bg_row   = "#f9f9f9" if i % 2 == 0 else "white"
        rows += f"""
        <tr style="background:{bg_row};">
          <td style="padding:8px 12px;font-weight:600;">{row['Ticker']}</td>
          <td style="padding:8px 12px;text-align:center;">
            <span style="background:{bg_score};color:{color};padding:3px 10px;
                         border-radius:12px;font-weight:600;">{int(score)}</span>
          </td>
          <td style="padding:8px 12px;text-align:center;">{row['Sources Agree']}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Yahoo SB','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Zacks #1','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Morningstar ★★★★','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Insider Buy','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('EPS Revision ↑','–')}</td>
          <td style="padding:8px 12px;text-align:center;">{row.get('Beats S&P','–')}</td>
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
            <th style="padding:10px 12px;">Insider</th>
            <th style="padding:10px 12px;">EPS Rev</th>
            <th style="padding:10px 12px;">Beats S&P</th>
            <th style="padding:10px 12px;">Price</th>
            <th style="padding:10px 12px;">Upside</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <br>
      <p style="font-size:12px;color:#888;">
        Score legend: Yahoo(30pts) + Zacks(30pts) + Morningstar(25pts) + Vanguard(15pts)<br>
        🟢 ≥70 = High conviction &nbsp;|&nbsp; 🟡 40–69 = Moderate conviction<br>
        📊 Full history saved to Google Drive: Stock Reports/stock_results_log.xlsx<br><br>
        <em>This is not financial advice. Always do your own research before investing.</em>
      </p>
    </body></html>
    """

    try:
        response = req.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": EMAIL_FROM,
                "to": [EMAIL_TO],
                "subject": f"📈 Stock Convergence Report — {today}",
                "html": html,
            }
        )
        if response.status_code in (200, 201):
            print(f"  ✓ Email sent to {EMAIL_TO}")
        else:
            print(f"  ✗ Email failed: {response.status_code} {response.text}")
    except Exception as e:
        print(f"  ✗ Email failed: {e}")

# ── daily job ─────────────────────────────────────────────────────────────────

def daily_job():
    print(f"\n{'='*50}")
    print(f"  Daily Stock Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    try:
        df = run_analyzer()
        save_to_drive(df)
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

    print("  Running first scan now...")
    daily_job()

    schedule.every().day.at(RUN_TIME).do(daily_job)

    print(f"\n  ✓ Scheduled for {RUN_TIME} AM every day.")
    print("  Waiting for next run...\n")

    while True:
        schedule.run_pending()
        time.sleep(60)
