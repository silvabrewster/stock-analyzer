"""
Stock Convergence Scheduler v3.0 (Railway Fixed)
"""

import os
import io
import json
import requests as req
from datetime import datetime
import pandas as pd

EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")
EMAIL_TO   = os.getenv("EMAIL_TO")
RESEND_KEY = os.getenv("RESEND_KEY")
TOP_N      = 10
EXCEL_LOG  = "stock_results_log.xlsx"
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

# ── google drive ─────────────────────────────────────────

def get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"  ✗ Drive auth failed: {e}")
        return None

def find_file_in_drive(service, filename):
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
    from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

    service = get_drive_service()
    if not service:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    top = df.head(TOP_N).copy()
    top.insert(0, "Date", today)

    file_id = find_file_in_drive(service, EXCEL_LOG)

    if file_id:
        try:
            request = service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            buffer.seek(0)
            existing = pd.read_excel(buffer)
            updated = pd.concat([existing, top], ignore_index=True)
        except Exception:
            updated = top
    else:
        updated = top

    buffer = io.BytesIO()
    updated.to_excel(buffer, index=False)
    buffer.seek(0)

    media = MediaIoBaseUpload(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=True
    )

    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
        print("  ✓ Excel updated in Drive")
    else:
        service.files().create(
            body={"name": EXCEL_LOG, "parents": [DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id"
        ).execute()
        print("  ✓ Excel created in Drive")

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

def send_email(df: pd.DataFrame):
    today = datetime.now().strftime("%B %d, %Y")
    top = df.head(TOP_N)

    rows = ""
    for _, row in top.iterrows():
        rows += f"<tr><td>{row['Ticker']}</td><td>{int(row['Consensus Score'])}</td></tr>"

    html = f"""
    <h2>Stock Report — {today}</h2>
    <table border="1" cellpadding="6">
    <tr><th>Ticker</th><th>Score</th></tr>
    {rows}
    </table>
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
                "subject": f"Stock Report — {today}",
                "html": html
            }
        )

        if response.status_code in (200, 201):
            print("  ✓ Email sent")
        else:
            print(f"  ✗ Email failed: {response.text}")

    except Exception as e:
        print(f"  ✗ Email error: {e}")

# ── job ──────────────────────────────────────────────────

def daily_job():
    try:
        df, market = run_analyzer()
        save_to_drive(df)
        send_email(df)
        print("  ✓ Done")
    except Exception as e:
        print(f"  ✗ Error: {e}")

# ── entry ────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running job (Railway)...")
    daily_job()
