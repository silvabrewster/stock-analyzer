"""
Stock Convergence Scheduler v3.1
===================================
Runs analyzer at 14:00 UTC (7AM Pacific),
saves to SQLite (for web app), Google Drive,
and emails via Resend.
"""

import schedule
import time
import os
import io
import requests as req
from datetime import datetime
import pandas as pd

EMAIL_FROM      = "onboarding@resend.dev"
EMAIL_TO        = "silvabrayden0@gmail.com"
RESEND_KEY      = "re_cWJizFpm_1zrGKUJ2djd7S5mbPQHKJorY"
RUN_TIME        = "14:00"
EXCEL_LOG       = "stock_results_log.xlsx"
TOP_N           = 10
DRIVE_FOLDER_ID = "1NK1gDvWvszHPC6GS3Jmi5O9C4sr9Tn-n"
CREDENTIALS     = "credentials.json"

# ── google drive ──────────────────────────────────────────────────────────────

def get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS, scopes=["https://www.googleapis.com/auth/drive"]
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
    try:
        from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
        service = get_drive_service()
        if not service:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        top   = df.head(TOP_N).copy()
        top.insert(0, "Date", today)
        file_id = find_file_in_drive(service, EXCEL_LOG)
        if file_id:
            try:
                request    = service.files().get_media(fileId=file_id)
                buffer     = io.BytesIO()
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
        buffer = io.BytesIO()
        updated.to_excel(buffer, index=False)
        buffer.seek(0)
        mime  = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        media = MediaIoBaseUpload(buffer, mimetype=mime, resumable=True)
        if file_id:
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            service.files().create(
                body={"name": EXCEL_LOG, "parents": [DRIVE_FOLDER_ID]},
                media_body=media, fields="id"
            ).execute()
        print(f"  ✓ Excel log updated in Google Drive")
    except Exception as e:
        print(f"  ✗ Drive save failed: {e}")

# ── run analyzer ─────────────────────────────────────────────────────────────

def run_analyzer():
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

def send_email(df: pd.DataFrame, market: dict, warning):
    today  = datetime.now().strftime("%B %d, %Y")
    top    = df.head(TOP_N)
    sp     = market.get("sp500", {})
    vix    = market.get("vix", {})
    tny    = market.get("tny", {})
    sp_chg = sp.get("chg", 0) or 0
    sp_color = "#2d6a2d" if sp_chg >= 0 else "#8b1a1a"
    sp_arrow = "▲" if sp_chg >= 0 else "▼"
    rows = ""
    for i, (_, row) in enumerate(top.iterrows()):
        score  = row["Consensus Score"]
        upside = row.get("Upside %", "n/a")
        bg_row = "#f9f9f9" if i % 2 == 0 else "white"
        sc, sb = ("#2d6a2d","#e6f4e6") if score >= 70 else (("#7a6a00","#fff8dc") if score >= 50 else ("#5f5e5a","#f1efe8"))
        try:
            uv = float(str(upside).replace("%",""))
            uc = "#2d6a2d" if uv >= 15 else ("#7a6a00" if uv >= 5 else "#8b1a1a")
        except Exception:
            uc = "#222"
        rows += f"""
        <tr style="background:{bg_row};">
          <td style="padding:8px 12px;font-weight:600;">{row['Ticker']} {row.get('New?','')} {row.get('Vol Spike','')}</td>
          <td style="padding:8px 12px;text-align:center;"><span style="background:{sb};color:{sc};padding:3px 10px;border-radius:12px;font-weight:600;">{int(score)}</span></td>
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
    warning_html = f'<p style="background:#fff3cd;padding:10px 14px;border-radius:4px;margin-bottom:12px;">{warning}</p>' if warning else ""
    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:900px;margin:auto;">
      <h2 style="color:#1a1a2e;margin-bottom:4px;">📈 Stock Convergence Report — {today}</h2>
      <div style="background:#1a1a2e;color:white;border-radius:8px;padding:12px 18px;margin-bottom:16px;font-size:13px;">
        S&P 500: <strong>{sp.get('price','n/a')}</strong> <span style="color:{sp_color};">{sp_arrow}{abs(sp_chg)}%</span>
        &nbsp;&nbsp; VIX: <strong>{vix.get('price','n/a')}</strong>
        &nbsp;&nbsp; 10yr: <strong>{tny.get('price','n/a')}%</strong>
      </div>
      {warning_html}
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="background:#1a1a2e;color:white;">
          <th style="padding:10px 12px;text-align:left;">Ticker</th>
          <th style="padding:10px 12px;">Score</th><th style="padding:10px 12px;">Sources</th>
          <th style="padding:10px 12px;">Streak</th><th style="padding:10px 12px;">Yahoo</th>
          <th style="padding:10px 12px;">Zacks</th><th style="padding:10px 12px;">MS</th>
          <th style="padding:10px 12px;">Insider</th><th style="padding:10px 12px;">EPS Rev</th>
          <th style="padding:10px 12px;">Beats S&P</th><th style="padding:10px 12px;">Price</th>
          <th style="padding:10px 12px;">Upside</th><th style="padding:10px 12px;">Beta</th>
          <th style="padding:10px 12px;">Div</th><th style="padding:10px 12px;">52w Pos</th>
          <th style="padding:10px 12px;">Sector</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="font-size:11px;color:#888;margin-top:16px;">Not financial advice. Always do your own research.</p>
    </body></html>"""
    try:
        r = req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
            json={"from": EMAIL_FROM, "to": [EMAIL_TO],
                  "subject": f"📈 Stock Convergence Report — {today}", "html": html}
        )
        print(f"  ✓ Email sent" if r.status_code in (200,201) else f"  ✗ Email failed: {r.text}")
    except Exception as e:
        print(f"  ✗ Email error: {e}")

# ── daily job ─────────────────────────────────────────────────────────────────

def daily_job():
    print(f"\n{'='*55}\n  Daily Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*55}")
    try:
        df, market, warning = run_analyzer()

        # Save to web app database
        try:
            from app import save_scan_to_db, init_db
            init_db()
            save_scan_to_db(df, market)
            print("  ✓ Results saved to web app database")
        except Exception as e:
            print(f"  ✗ DB save failed: {e}")

        save_to_drive(df)
        send_email(df, market, warning)
        print(f"\n  ✓ Done! Next run at {RUN_TIME} UTC tomorrow.")
    except Exception as e:
        print(f"\n  ✗ Error: {e}")

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════╗
║   Stock Convergence Scheduler  v3.1      ║
║   Runs daily at {RUN_TIME} UTC (7AM Pacific)  ║
╚══════════════════════════════════════════╝
  Press Ctrl+C to stop.
    """)
    print("  Running first scan now...")
    daily_job()
    schedule.every().day.at(RUN_TIME).do(daily_job)
    print(f"\n  ✓ Scheduled for {RUN_TIME} UTC every day.")
    while True:
        schedule.run_pending()
        time.sleep(60)
