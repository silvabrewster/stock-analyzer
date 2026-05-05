"""
Keep-Alive Script
==================
Pings the Render app every 10 minutes to prevent
the free tier from spinning down (cold starts).

Run this on your LOCAL PC alongside the scheduler,
OR add it to Railway as a separate worker.

Usage: python keepalive.py
"""

import time
import requests
from datetime import datetime

# Replace with your actual Render URL
APP_URL = "https://stock-analyzer-xxxx.onrender.com/ping"

def ping():
    try:
        resp = requests.get(APP_URL, timeout=10)
        print(f"[{datetime.now().strftime('%H:%M')}] Ping: {resp.status_code}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M')}] Ping failed: {e}")

if __name__ == "__main__":
    print(f"Keep-alive started for: {APP_URL}")
    print("Pinging every 10 minutes. Press Ctrl+C to stop.\n")
    while True:
        ping()
        time.sleep(600)  # 10 minutes
