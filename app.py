import os
import time
import random
import smtplib
import logging
import threading
from datetime import datetime
from email.mime.text import MIMEText

from flask import Flask, request, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("naukri-refresher")

app = Flask(__name__)

NAUKRI_EMAIL = os.environ.get("NAUKRI_EMAIL")
NAUKRI_PASSWORD = os.environ.get("NAUKRI_PASSWORD")
RESUME_PATH = os.environ.get("RESUME_PATH", "/app/resume.pdf")

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
NOTIFY_TO = os.environ.get("NOTIFY_TO", GMAIL_ADDRESS)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

PROFILE_URL = "https://www.naukri.com/mnjuser/profile?id=&altresid"

WINDOW_START_MIN = 8 * 60 + 50
WINDOW_END_MIN = 9 * 60 + 20

scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
automation_enabled = True

job_status_lock = threading.Lock()
job_status = {
    "state": "idle",
    "logged_in": False,
    "resume_uploaded": False,
    "completed": False,
    "message": "No run yet.",
    "last_run": None,
    "triggered_by": None,
}


def update_status(**kwargs):
    with job_status_lock:
        job_status.update(kwargs)


def get_status():
    with job_status_lock:
        return dict(job_status)


def send_email(subject: str, body: str):
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD and NOTIFY_TO):
        log.warning("Email not configured, skipping notification. Subject: %s", subject)
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = NOTIFY_TO
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        log.info("Notification email sent: %s", subject)
    except Exception as e:
        log.error("Failed to send email: %s", e)


def refresh_naukri_profile(triggered_by="scheduled"):
    if not automation_enabled:
        log.info("Automation is paused — skipping this run.")
        update_status(state="idle", message="Automation is paused, run skipped.")
        return

    update_status(
        state="running",
        logged_in=False,
        resume_uploaded=False,
        completed=False,
        message="Starting browser and logging in...",
        triggered_by=triggered_by,
    )

    driver = None
    try:
        options = uc.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1366,768")
        options.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
        driver = uc.Chrome(options=options)
        wait = WebDriverWait(driver, 20)

        log.info("Opening Naukri login page")
        driver.get("https://www.naukri.com/nlogin/login")
        time.sleep(random.uniform(2, 4))

        email_field = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[@aria-label='Email ID / Username']"))
        )
        email_field.send_keys(NAUKRI_EMAIL)
        time.sleep(random.uniform(0.5, 1.5))

        password_field = driver.find_element(By.XPATH, "//input[@aria-label='Password']")
        password_field.send_keys(NAUKRI_PASSWORD)
        time.sleep(random.uniform(0.5, 1.5))

        login_btn = driver.find_element(By.CSS_SELECTOR, "button.loginButton")
        login_btn.click()

        wait.until(EC.url_contains("naukri.com/mnjuser"))
        time.sleep(random.uniform(3, 5))

        if "captcha" in driver.current_url.lower():
            raise RuntimeError("CAPTCHA encountered during login — cannot proceed automatically")

        log.info("Login successful, navigating to profile")
        update_status(logged_in=True, message="Logged in. Opening profile page...")

        driver.get(PROFILE_URL)
        time.sleep(random.uniform(2, 4))
        driver.execute_script("window.scrollBy(0, 600);")
        time.sleep(random.uniform(1, 2))

        upload_input = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".resume-upload-container input[type='file']")
            )
        )

        if not os.path.isfile(RESUME_PATH):
            raise FileNotFoundError(f"Resume file not found at {RESUME_PATH}")

        update_status(message="Uploading resume...")
        upload_input.send_keys(RESUME_PATH)
        time.sleep(random.uniform(3, 6))

        log.info("Resume re-uploaded successfully — profile refreshed")
        update_status(
            resume_uploaded=True,
            state="success",
            completed=True,
            message="Profile refreshed successfully.",
            last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        send_email(
            "Naukri profile refresh: SUCCESS",
            f"Profile refreshed successfully at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST.",
        )

    except TimeoutException as e:
        log.error("Timeout waiting for element: %s", e)
        update_status(
            state="failed",
            completed=False,
            message=f"Timed out waiting for a page element: {e}",
            last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        send_email(
            "Naukri profile refresh: FAILED (timeout)",
            f"Timed out waiting for a page element. This usually means Naukri's page "
            f"layout changed, or a CAPTCHA appeared. Manual check needed.\n\nError: {e}",
        )
    except Exception as e:
        log.error("Refresh job failed: %s", e)
        update_status(
            state="failed",
            completed=False,
            message=f"Failed: {e}",
            last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        send_email(
            "Naukri profile refresh: FAILED",
            f"Automation failed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST.\n\n"
            f"Error: {e}\n\nPlease update your profile manually today.",
        )
    finally:
        if driver:
            driver.quit()


def schedule_next_run():
    minute_offset = random.randint(WINDOW_START_MIN, WINDOW_END_MIN)
    hour = minute_offset // 60
    minute = minute_offset % 60

    scheduler.add_job(
        lambda: refresh_naukri_profile(triggered_by="scheduled"),
        trigger=CronTrigger(hour=hour, minute=minute, timezone="Asia/Kolkata"),
        id="daily_refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    log.info("Next refresh scheduled for %02d:%02d IST today", hour, minute)


def init_scheduler():
    schedule_next_run()
    scheduler.add_job(
        schedule_next_run,
        trigger=CronTrigger(hour=0, minute=1, timezone="Asia/Kolkata"),
        id="reshuffle_time",
        replace_existing=True,
    )
    scheduler.start()


init_scheduler()


def check_token():
    if not ADMIN_TOKEN:
        return True
    supplied = request.args.get("token") or (request.json or {}).get("token") if request.is_json else request.args.get("token")
    return supplied == ADMIN_TOKEN


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Naukri Auto-Refresher</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }
  .card {
    background: #1e293b; border-radius: 16px; padding: 32px; width: 100%; max-width: 440px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.4);
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #94a3b8; font-size: 13px; margin-bottom: 24px; }
  .steps { display: flex; flex-direction: column; gap: 14px; margin-bottom: 24px; }
  .step {
    display: flex; align-items: center; gap: 12px; padding: 12px 14px;
    background: #0f172a; border-radius: 10px; border: 1px solid #334155;
  }
  .box {
    width: 22px; height: 22px; border-radius: 6px; border: 2px solid #475569;
    flex-shrink: 0; display: flex; align-items: center; justify-content: center;
    font-size: 14px; transition: all 0.2s;
  }
  .box.checked { background: #22c55e; border-color: #22c55e; color: #0f172a; font-weight: bold; }
  .box.failed { background: #ef4444; border-color: #ef4444; color: #fff; }
  .step-label { font-size: 14px; }
  button {
    width: 100%; padding: 14px; border-radius: 10px; border: none; font-size: 15px;
    font-weight: 600; cursor: pointer; margin-bottom: 10px; transition: opacity 0.2s;
  }
  #runBtn { background: #6366f1; color: white; }
  #runBtn:disabled { opacity: 0.5; cursor: not-allowed; }
  .row { display: flex; gap: 10px; }
  .row button { flex: 1; }
  #pauseBtn { background: #f59e0b; color: #0f172a; }
  #resumeBtn { background: #22c55e; color: #0f172a; }
  .status-msg {
    font-size: 13px; color: #cbd5e1; text-align: center; margin: 14px 0 4px;
    min-height: 18px;
  }
  .meta { font-size: 12px; color: #64748b; text-align: center; margin-top: 8px; }
  .badge {
    display: inline-block; font-size: 11px; padding: 3px 8px; border-radius: 999px;
    margin-left: 8px; vertical-align: middle;
  }
  .badge.on { background: #14532d; color: #86efac; }
  .badge.off { background: #7c2d12; color: #fdba74; }
</style>
</head>
<body>
  <div class="card">
    <h1>Naukri Auto-Refresher <span id="autoBadge" class="badge"></span></h1>
    <div class="sub">Manual run + live status</div>

    <div class="steps">
      <div class="step">
        <div id="box-login" class="box">1</div>
        <div class="step-label">Logged in to Naukri</div>
      </div>
      <div class="step">
        <div id="box-upload" class="box">2</div>
        <div class="step-label">Resume uploaded</div>
      </div>
      <div class="step">
        <div id="box-complete" class="box">3</div>
        <div class="step-label">Task complete</div>
      </div>
    </div>

    <button id="runBtn" onclick="triggerRun()">Run Now</button>
    <div class="row">
      <button id="pauseBtn" onclick="callAction('/api/pause')">Pause Daily Job</button>
      <button id="resumeBtn" onclick="callAction('/api/resume')">Resume Daily Job</button>
    </div>

    <div class="status-msg" id="statusMsg">Loading status...</div>
    <div class="meta" id="lastRun"></div>
  </div>

<script>
function getToken() {
  let t = localStorage.getItem('naukri_admin_token');
  if (t === null) {
    t = prompt("Enter admin token (leave blank if none configured):") || "";
    localStorage.setItem('naukri_admin_token', t);
  }
  return t;
}

function setBox(id, state) {
  const el = document.getElementById(id);
  el.classList.remove('checked', 'failed');
  if (state === 'ok') { el.classList.add('checked'); el.textContent = '✓'; }
  else if (state === 'fail') { el.classList.add('failed'); el.textContent = '✗'; }
  else { el.textContent = ''; }
}

async function refreshStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const s = data.job_status;

    setBox('box-login', s.logged_in ? 'ok' : (s.state === 'failed' && !s.logged_in ? 'fail' : ''));
    setBox('box-upload', s.resume_uploaded ? 'ok' : (s.state === 'failed' && s.logged_in && !s.resume_uploaded ? 'fail' : ''));
    setBox('box-complete', s.completed ? 'ok' : (s.state === 'failed' ? 'fail' : ''));

    document.getElementById('statusMsg').textContent = s.message || '';
    document.getElementById('lastRun').textContent = s.last_run ? ('Last run: ' + s.last_run + ' IST (' + (s.triggered_by || '') + ')') : '';

    const badge = document.getElementById('autoBadge');
    badge.textContent = data.automation_enabled ? 'Daily job: ON' : 'Daily job: PAUSED';
    badge.className = 'badge ' + (data.automation_enabled ? 'on' : 'off');

    document.getElementById('runBtn').disabled = (s.state === 'running');
    document.getElementById('runBtn').textContent = (s.state === 'running') ? 'Running...' : 'Run Now';
  } catch (e) {
    document.getElementById('statusMsg').textContent = 'Could not reach server.';
  }
}

async function triggerRun() {
  await callAction('/api/trigger');
}

async function callAction(path) {
  const token = getToken();
  try {
    const res = await fetch(path + '?token=' + encodeURIComponent(token));
    const data = await res.json();
    if (res.status === 401) {
      alert('Wrong token. Clearing saved token — try again.');
      localStorage.removeItem('naukri_admin_token');
    }
  } catch (e) {}
  refreshStatus();
}

refreshStatus();
setInterval(refreshStatus, 2000);
</script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/api/status")
def api_status():
    jobs = scheduler.get_jobs()
    next_runs = {job.id: str(job.next_run_time) for job in jobs}
    return jsonify({
        "automation_enabled": automation_enabled,
        "scheduled_jobs": next_runs,
        "job_status": get_status(),
    })


@app.route("/api/trigger")
def api_trigger():
    if not check_token():
        return jsonify({"error": "unauthorized"}), 401
    current = get_status()
    if current["state"] == "running":
        return jsonify({"status": "already running"})
    scheduler.add_job(
        lambda: refresh_naukri_profile(triggered_by="manual"),
        id="manual_trigger",
        replace_existing=True,
    )
    return jsonify({"status": "triggered manually, watch the dashboard for progress"})


@app.route("/api/pause")
def api_pause():
    global automation_enabled
    if not check_token():
        return jsonify({"error": "unauthorized"}), 401
    automation_enabled = False
    if scheduler.get_job("daily_refresh"):
        scheduler.remove_job("daily_refresh")
    log.info("Automation paused via /api/pause")
    return jsonify({"status": "paused"})


@app.route("/api/resume")
def api_resume():
    global automation_enabled
    if not check_token():
        return jsonify({"error": "unauthorized"}), 401
    automation_enabled = True
    schedule_next_run()
    log.info("Automation resumed via /api/resume")
    return jsonify({"status": "resumed"})


@app.route("/trigger-now")
def trigger_now_alias():
    return api_trigger()


@app.route("/pause")
def pause_alias():
    return api_pause()


@app.route("/resume")
def resume_alias():
    return api_resume()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
