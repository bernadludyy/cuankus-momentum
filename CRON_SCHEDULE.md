# Bot 4 — cron-job.org schedule

This file is the canonical external schedule for `Cuankus Momentum Market Scanner`.
The GitHub workflow itself intentionally has no native `schedule:` trigger.

The old `scanner.yml` endpoint has been retired. Only Session A-F should point to `market_scanner.yml`.

All cron-job.org jobs use:

- Time zone: `Asia/Jakarta`
- Method: `POST`
- URL: `https://api.github.com/repos/bernadludyy/cuankus-momentum/actions/workflows/market_scanner.yml/dispatches`
- Body: `{"ref":"main"}`
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <organization PAT>`
  - `Content-Type: application/json`
  - `X-GitHub-Api-Version: 2022-11-28`

## Exact sessions

These expressions match the scanner's current WIB market-hours guard exactly:
Mon–Thu 09:00–12:00 and 13:30–15:50; Friday 09:00–11:30 and 14:00–15:50.

| Job | Cron expression | Purpose |
| --- | --- | --- |
| Session A | `*/5 9-10,14 * * 1-5` | Common full hours: 09–10 and 14 |
| Session B | `*/5 11 * * 1-4` | Mon–Thu 11:00–11:55 |
| Session C | `0,5,10,15,20,25,30 11 * * 5` | Friday 11:00–11:30 |
| Session D | `0 12 * * 1-4` | Mon–Thu exactly 12:00 |
| Session E | `30,35,40,45,50,55 13 * * 1-4` | Mon–Thu 13:30–13:55 |
| Session F | `0,5,10,15,20,25,30,35,40,45,50 15 * * 1-5` | Mon–Fri 15:00–15:50 |

Expected dispatch count:
- Monday–Thursday: 66 runs/day
- Friday: 54 runs/day
- Saturday–Sunday: 0 runs/day

Do not enable an older broad schedule such as `*/5 9-15 * * 1-5` at the same time,
otherwise duplicate/out-of-session dispatches will occur.

The Python scanner retains its own market-hours guard as a second safety layer.
