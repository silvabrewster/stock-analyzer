#!/usr/bin/env python3
"""
monitor.py
==========
Hits the LIVE site the way a real user does — logs in, loads every page,
checks the data is fresh and correct — then reports exactly what is broken.

Unlike /health (which runs inside the deployment), this runs from outside, so
it also catches Render being asleep, TLS problems, redirect loops, and pages
that return 200 but render an error.

Usage:
    python monitor.py                          # uses env vars
    python monitor.py --url https://... --password secret
    python monitor.py --explain                # ask Claude to diagnose failures
    python monitor.py --quiet                  # only print if something is wrong

Env vars:
    APP_URL, APP_PASSWORD, HEALTH_TOKEN, ANTHROPIC_API_KEY

Exit codes:  0 = healthy   1 = degraded   2 = down
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    print("monitor.py needs the requests package:  pip install requests")
    sys.exit(2)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# path, friendly name, substring that must appear when the page renders properly
PAGES = [
    ("/",           "Dashboard",     "Convergence"),
    ("/history",    "History",       "Convergence"),
    ("/sectors",    "Sectors",       "Convergence"),
    ("/watchlist",  "Watchlist",     "Convergence"),
    ("/portfolio",  "Portfolio",     "Convergence"),
    ("/compare",    "Compare",       "Convergence"),
    ("/backtest",   "Backtest",      "Convergence"),
    ("/momentum",   "Momentum",      "Convergence"),
    ("/analyze",    "Analyze",       "Convergence"),
    ("/earnings",   "Earnings",      "Convergence"),
    ("/checklist",  "Checklist",     "Convergence"),
    ("/ai-picks",   "Claude's Picks","Convergence"),
]

# Text that means the page rendered an error even though HTTP said 200.
ERROR_MARKERS = ("Traceback (most recent", "Internal Server Error",
                 "jinja2.exceptions", "werkzeug.exceptions")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class Report:
    def __init__(self):
        self.results = []      # (ok, severity, name, detail)
        self.started = datetime.now()

    def add(self, ok, name, detail, severity="high"):
        self.results.append((ok, severity, name, detail))
        return ok

    @property
    def failures(self):
        return [r for r in self.results if not r[0]]

    @property
    def critical(self):
        return [r for r in self.failures if r[1] == "critical"]

    def render(self, quiet=False):
        if quiet and not self.failures:
            return
        print(f"\n  Convergence health — {self.started:%Y-%m-%d %H:%M:%S}")
        print("  " + "─" * 62)
        for ok, sev, name, detail in self.results:
            mark  = f"{GREEN}✓{RESET}" if ok else (
                    f"{RED}✗{RESET}" if sev == "critical" else f"{YELLOW}!{RESET}")
            print(f"  {mark} {name:<26} {DIM}{detail}{RESET}")
        print("  " + "─" * 62)
        passed = len(self.results) - len(self.failures)
        if not self.failures:
            print(f"  {GREEN}All {len(self.results)} checks passed.{RESET}\n")
        elif self.critical:
            print(f"  {RED}{len(self.critical)} critical failure(s){RESET} "
                  f"— {passed}/{len(self.results)} passed\n")
        else:
            print(f"  {YELLOW}Degraded{RESET} — {passed}/{len(self.results)} passed\n")


def wake(session, url, report, attempts=3):
    """Render free tier sleeps; the first request can take ~60s."""
    for i in range(attempts):
        started = time.time()
        try:
            r = session.get(f"{url}/ping", timeout=90)
            elapsed = time.time() - started
            if r.status_code == 200:
                note = f"{elapsed:.1f}s" + (" (cold start)" if elapsed > 8 else "")
                return report.add(True, "Site reachable", note, "critical")
            report.add(False, "Site reachable",
                       f"HTTP {r.status_code} from /ping", "critical")
            return False
        except requests.exceptions.RequestException as e:
            if i == attempts - 1:
                return report.add(False, "Site reachable",
                                  f"{type(e).__name__}: {e}", "critical")
            time.sleep(5)
    return False


def login(session, url, password, report):
    if not password:
        report.add(False, "Login", "No password given (set APP_PASSWORD)", "critical")
        return False
    try:
        session.get(f"{url}/login", timeout=30)
        r = session.post(f"{url}/login", data={"password": password,
                                               "username": "monitor"},
                         timeout=30, allow_redirects=False)
        if r.status_code in (301, 302) and "/login" not in r.headers.get("Location", ""):
            return report.add(True, "Login", "Accepted", "critical")
        if r.status_code == 200 and "Wrong password" in r.text:
            return report.add(False, "Login", "Password rejected", "critical")
        if r.status_code == 200 and "not configured" in r.text:
            return report.add(False, "Login",
                              "APP_PASSWORD is not set on the server", "critical")
        return report.add(False, "Login", f"Unexpected HTTP {r.status_code}", "critical")
    except requests.exceptions.RequestException as e:
        return report.add(False, "Login", f"{type(e).__name__}: {e}", "critical")


def check_pages(session, url, report):
    for path, name, marker in PAGES:
        try:
            r = session.get(f"{url}{path}", timeout=90)
            if r.status_code != 200:
                report.add(False, name, f"HTTP {r.status_code}", "critical")
                continue
            hit = next((m for m in ERROR_MARKERS if m in r.text), None)
            if hit:
                report.add(False, name, f"Rendered an error ({hit})", "critical")
            elif marker and marker not in r.text:
                report.add(False, name,
                           "200 but page content looks wrong", "high")
            else:
                report.add(True, name, f"{len(r.content) // 1024}kb", "low")
        except requests.exceptions.RequestException as e:
            report.add(False, name, f"{type(e).__name__}: {e}", "critical")


def check_health_endpoint(session, url, token, report):
    """Pull the app's internal self-diagnosis."""
    try:
        params = {"full": "1"}
        if token:
            params["token"] = token
        r = session.get(f"{url}/health", params=params, timeout=120)
        if r.status_code == 401:
            return report.add(False, "Health endpoint",
                              "Unauthorized — set HEALTH_TOKEN", "high")
        if r.status_code == 404:
            return report.add(False, "Health endpoint",
                              "Not deployed yet (old build still live)", "high")
        data = r.json()
        if data.get("error"):
            return report.add(False, "Health endpoint", data["error"], "critical")

        report.add(bool(data.get("ok")), "Internal diagnosis",
                   data.get("summary", "?"),
                   "critical" if not data.get("ok") else "low")
        # Surface each internal failure as its own line so the cause is obvious.
        for check in data.get("checks", []):
            if not check.get("ok"):
                report.add(False, f"  ↳ {check['name']}", check.get("detail", ""),
                           check.get("severity", "high"))
        return bool(data.get("ok"))
    except ValueError:
        return report.add(False, "Health endpoint", "Response was not JSON", "high")
    except requests.exceptions.RequestException as e:
        return report.add(False, "Health endpoint", f"{type(e).__name__}: {e}", "high")


def explain(report, url):
    """Ask Claude what the failures mean and what to change."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print(f"  {DIM}--explain needs ANTHROPIC_API_KEY{RESET}\n")
        return
    if not report.failures:
        return
    findings = "\n".join(f"- [{sev}] {name}: {detail}"
                         for _, sev, name, detail in report.failures)
    prompt = (
        "You are diagnosing a Flask stock-analysis app deployed on Render free "
        "tier with a Supabase Postgres database. Known constraints: Yahoo "
        "Finance, Zacks and Finviz block Render IPs, so scores max out near 30 "
        "on a nominal 0-100 scale; Supabase free tier pauses after ~1 week idle; "
        "Render free tier sleeps after 15 minutes idle.\n\n"
        f"Automated checks against {url} failed:\n{findings}\n\n"
        "For each distinct root cause give: what is broken, the most likely "
        "cause, and the specific fix (env var to set, service to restore, or "
        "code change). Be concrete and brief. Group related failures — do not "
        "repeat yourself. If a failure is an expected consequence of the known "
        "constraints above, say so plainly instead of proposing a fix."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 900,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60)
        if r.status_code == 200:
            print("  Diagnosis")
            print("  " + "─" * 62)
            for line in r.json()["content"][0]["text"].strip().splitlines():
                print(f"  {line}")
            print()
        else:
            print(f"  {DIM}Diagnosis unavailable: HTTP {r.status_code}{RESET}\n")
    except Exception as e:
        print(f"  {DIM}Diagnosis unavailable: {e}{RESET}\n")


def main():
    ap = argparse.ArgumentParser(description="Check the live Convergence app.")
    ap.add_argument("--url",      default=os.environ.get("APP_URL", ""))
    ap.add_argument("--password", default=os.environ.get("APP_PASSWORD", ""))
    ap.add_argument("--token",    default=os.environ.get("HEALTH_TOKEN", ""))
    ap.add_argument("--explain",  action="store_true",
                    help="ask Claude to diagnose any failures")
    ap.add_argument("--quiet",    action="store_true",
                    help="print nothing when everything passes")
    ap.add_argument("--json",     action="store_true", help="emit JSON instead")
    args = ap.parse_args()

    if not args.url:
        print("No URL. Pass --url or set APP_URL.")
        return 2
    url = args.url.rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url

    report  = Report()
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    if wake(session, url, report):
        if login(session, url, args.password, report):
            check_pages(session, url, report)
            check_health_endpoint(session, url, args.token, report)

    if args.json:
        print(json.dumps({
            "ok": not report.failures,
            "critical": len(report.critical),
            "checks": [{"ok": o, "severity": s, "name": n, "detail": d}
                       for o, s, n, d in report.results],
        }, indent=2))
    else:
        report.render(quiet=args.quiet)
        if args.explain:
            explain(report, url)

    return 2 if report.critical else (1 if report.failures else 0)


if __name__ == "__main__":
    sys.exit(main())
