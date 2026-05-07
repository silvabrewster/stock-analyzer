"""
Keep-Alive Script
==================
Two modes:

  1. Continuous (default) — runs forever, pings every 10 minutes.
     Run locally or as a Render Background Worker:
       python keepalive.py

  2. One-shot (--once) — ping once and exit.
     Used by the Render Cron job in render.yaml (fires every 10 min):
       python keepalive.py --once

Free alternative to Render cron (no credit card needed):
  Sign up at https://cron-job.org and add a job pointing to:
  https://your-app.onrender.com/ping  (every 10 minutes)
"""

import sys
import time
import os
import requests
from datetime import datetime

APP_URL = os.environ.get("APP_URL", "").rstrip("/")
if not APP_URL:
    APP_URL = "https://stock-analyzer-xxxx.onrender.com"

PING_URL      = f"{APP_URL}/ping"
INTERVAL_SECS = 600   # 10 minutes


def ping() -> bool:
    try:
        resp = requests.get(PING_URL, timeout=15)
        ok   = resp.status_code == 200
        tag  = "✓" if ok else "✗"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {tag} {resp.status_code} — {PING_URL}")
        return ok
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✗ {e}")
        return False


if __name__ == "__main__":
    once = "--once" in sys.argv

    if once:
        ping()
    else:
        print(f"Keep-alive started → {PING_URL}")
        print("Pinging every 10 minutes. Ctrl+C to stop.\n")
        while True:
            ping()
            time.sleep(INTERVAL_SECS)
