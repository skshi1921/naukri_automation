# Naukri Auto-Refresher

Automates the daily "profile refresh" action on Naukri.com — re-uploads your resume
every day at a randomized time near 9 AM so your profile shows up as recently active,
which pushes it up in recruiter search rankings.

---

## What it actually does

Every day, at a random time between **8:50 AM – 9:20 AM IST**, the app:

1. Launches a headless Chromium browser in the background (no visible window, runs on the server)
2. Logs into your Naukri account
3. Opens your profile page and re-uploads the same `resume.pdf`
4. This "update" action bumps your profile's last-updated timestamp — the signal Naukri
   uses to rank profiles higher in recruiter search
5. Closes the browser
6. Sends you an email telling you whether it succeeded or failed

It does **not** edit your resume content, skills, headline, or any other profile field.
It only re-submits the file.

---

## Features

### 1. Randomized daily schedule
The exact run time changes every day (picked fresh at 12:01 AM IST) within an
8:50–9:20 AM window. This avoids the exact-same-second-every-day pattern that
looks bot-like to fraud detection systems.

### 2. Live dashboard (`/`)
Visiting your deployed URL shows a small status page with:
- 3-step progress tracker: **Logged in → Resume uploaded → Task complete**
- A live status message (updates every 2 seconds)
- Timestamp and trigger source (`manual` or `scheduled`) of the last run
- A badge showing whether the daily automation is currently ON or PAUSED

### 3. Manual "Run Now" button
Lets you trigger a refresh on demand from the dashboard, instead of waiting for
the scheduled time — useful for testing.

### 4. Pause / Resume controls
Two buttons on the dashboard let you temporarily disable the daily job (e.g. if
you're not job-hunting for a while) without deleting or redeploying the app.
Resuming immediately schedules the next randomized run.

### 5. Admin token protection
All action routes (`/api/trigger`, `/api/pause`, `/api/resume`) require a secret
token (set via the `ADMIN_TOKEN` environment variable). Without the correct
token, these actions are rejected — so if someone finds your Render URL, they
can't trigger logins or mess with your automation. The dashboard asks for the
token once and remembers it in the browser.

### 6. Email notifications
After every run (scheduled or manual), you get an email via Gmail SMTP:
- **Success** — confirms the profile was refreshed, with timestamp
- **Failure** — includes the error (timeout, missing resume file, CAPTCHA
  encountered, etc.) so you know to update manually that day

### 7. CAPTCHA detection
If Naukri throws a CAPTCHA during login, the script detects it immediately,
stops (rather than retrying blindly), and emails you a failure notice instead
of getting stuck.

### 8. API endpoints (for scripting / external monitoring)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard UI |
| `/api/status` | GET | Current job status + next scheduled run time (JSON) |
| `/api/trigger?token=...` | GET | Manually trigger a refresh |
| `/api/pause?token=...` | GET | Pause the daily automation |
| `/api/resume?token=...` | GET | Resume the daily automation |
| `/trigger-now`, `/pause`, `/resume` | GET | Legacy aliases of the above (for old uptime-pinger URLs) |

---

## Deployment (Render, Docker-based)

This app **requires** Render's Docker environment, not the native Python
runtime — Chromium needs to be installed as root during image build, which
native Python builds don't allow.

1. Push all files (`Dockerfile`, `app.py`, `requirements.txt`, `render.yaml`,
   plus your `resume.pdf`) to the repo root.
2. In Render, create a new **Blueprint** service from this repo — it will
   auto-detect `render.yaml` and use the Dockerfile.
3. Set these environment variables in the Render dashboard:

| Variable | Required | Notes |
|---|---|---|
| `NAUKRI_EMAIL` | Yes | Your Naukri login email |
| `NAUKRI_PASSWORD` | Yes | Your Naukri login password |
| `GMAIL_ADDRESS` | Yes (for emails) | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Yes (for emails) | 16-character Gmail App Password (not your normal password) |
| `NOTIFY_TO` | No | Where alerts go — defaults to `GMAIL_ADDRESS` |
| `ADMIN_TOKEN` | Strongly recommended | Random secret string protecting the action routes |

`RESUME_PATH` does **not** need to be set manually — the Dockerfile sets it to
`/app/resume.pdf` automatically, matching where `resume.pdf` lands inside the
container.

---

## Known limitations

- **Selector fragility**: Naukri can change its page structure at any time.
  If runs start failing with a `TimeoutException`, the login/upload element
  selectors in `app.py` likely need re-checking via browser DevTools.
- **CAPTCHA**: if Naukri requires a CAPTCHA on a given login, this automation
  cannot solve it — you'll get a failure email and need to log in manually
  that day.
- **Free-tier sleep**: Render's free web services can spin down after
  inactivity. If the scheduled 9 AM job is silently missed, this is the most
  likely cause — an external uptime pinger can help but isn't a guaranteed fix.
- **Terms of Service**: automated login/actions may be against Naukri's ToS.
  Use at your own risk.
