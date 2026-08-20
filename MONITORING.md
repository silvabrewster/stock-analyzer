# Monitoring

Two layers of checks, because they catch different failures.

## `/health` — runs inside the deployment

Hit it on the live site:

```
https://<your-app>.onrender.com/health          fast (~2s)
https://<your-app>.onrender.com/health?full=1   also probes upstreams
```

Because it runs on the server, it can see things an outside caller cannot:
the database connection, whether the scan actually wrote usable rows, whether
prices resolve for every holding, and which external data sources answer from
Render's IP. It renders every page server-side and reports which ones raise.

Returns JSON: `ok`, `summary`, `failures[]`, and a `checks[]` array with a
severity on each. HTTP 200 when healthy, 503 when a critical check fails, so
uptime monitors work without parsing the body.

No secrets are returned — environment variables report only whether they are
set. To require a token anyway, set `HEALTH_TOKEN` and call
`/health?token=...`.

## `monitor.py` — runs from outside

```bash
pip install requests
export APP_URL=https://<your-app>.onrender.com
export APP_PASSWORD=<your login password>

python monitor.py              # full report
python monitor.py --quiet      # silent unless something is wrong
python monitor.py --explain    # ask Claude to diagnose the failures
python monitor.py --json       # machine-readable
```

It logs in like a real user and loads every page, so it also catches problems
`/health` cannot see: Render asleep, TLS errors, redirect loops, and pages that
return HTTP 200 while rendering an error. It then pulls `/health` and reports
the internal diagnosis alongside its own findings.

`--explain` needs `ANTHROPIC_API_KEY`. It sends only the failure lines (never
credentials) and asks for a diagnosis with concrete fixes.

Exit codes: `0` healthy, `1` degraded, `2` down — so it works in cron or CI.

### Run it on a schedule

Any cron host works. On cron-job.org, point a job at `/health` every 15 minutes
and alert on non-200. To get the richer report by email, run `monitor.py` from
a machine that is already on:

```
*/30 * * * * cd /path/to/stock-analyzer && APP_URL=... APP_PASSWORD=... \
  python monitor.py --quiet --explain >> monitor.log 2>&1
```

## Reading the results

Some failures are expected on the free tier and are not defects:

| Finding | Meaning |
| --- | --- |
| `upstream.yahoo` blocked | Normal. Yahoo blocks Render IPs; prices fall back to Stooq. |
| `scan.score_quality` ~12–30 | Normal. Analyst sources are blocked, so scores top out near 30. |
| `database.connect` fails | Supabase project is paused, or `DATABASE_URL` is wrong. |
| `config.APP_PASSWORD` missing | Nobody can log in until it is set in Render. |
| `scan.freshness` several days old | Scans stopped running. Check the cron job. |
| `page.*` non-200 | A real bug — that page is broken for users. |
| `feature.backtest` 0 trades | The pick filter is rejecting everything. |
