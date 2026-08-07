import os
import time
import random
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText

from flask import Flask
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

# ---------- CONFIG (from environment variables, set these in Render dashboard) ----------
NAUKRI_EMAIL = os.environ.get("NAUKRI_EMAIL")
NAUKRI_PASSWORD = os.environ.get("NAUKRI_PASSWORD")
RESUME_PATH = os.environ.get("RESUME_PATH", "/opt/render/project/src/resume.pdf")

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")       # your gmail id
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")  # 16-char app password
NOTIFY_TO = os.environ.get("NOTIFY_TO", GMAIL_ADDRESS)      # where alert email goes

# Window inside which the daily job time is randomized (24h format, minutes)
WINDOW_START_MIN = 8 * 60 + 50   # 8:50 AM
WINDOW_END_MIN = 9 * 60 + 20     # 9:20 AM

scheduler = BackgroundScheduler(timezone="Asia/Kolkata")


# ---------- EMAIL ----------
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


# ---------- CORE REFRESH JOB ----------
def refresh_naukri_profile():
    """
    Logs into Naukri and re-uploads the same resume file to bump the
    'last updated' timestamp, which pushes the profile up in recruiter search.

    NOTE: The element selectors below (By.ID / By.XPATH values) are best-effort
    guesses based on Naukri's typical DOM structure. Naukri changes its frontend
    periodically, so you MUST verify these selectors yourself via browser
    DevTools (right-click element -> Inspect) before first real run, and update
    them if they don't match. Search for 'VERIFY THIS' comments below.
    """
    driver = None
    try:
        options = uc.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1366,768")
        driver = uc.Chrome(options=options)
        wait = WebDriverWait(driver, 20)

        log.info("Opening Naukri login page")
        driver.get("https://www.naukri.com/nlogin/login")
        time.sleep(random.uniform(2, 4))

        # VERIFY THIS: login field IDs
        email_field = wait.until(EC.presence_of_element_located((By.ID, "usernameField")))
        email_field.send_keys(NAUKRI_EMAIL)
        time.sleep(random.uniform(0.5, 1.5))

        password_field = driver.find_element(By.ID, "passwordField")
        password_field.send_keys(NAUKRI_PASSWORD)
        time.sleep(random.uniform(0.5, 1.5))

        # VERIFY THIS: login submit button
        login_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        login_btn.click()

        # Wait for dashboard to load
        wait.until(EC.url_contains("naukri.com/mnjuser"))
        time.sleep(random.uniform(3, 5))

        # Check for CAPTCHA / unexpected redirect
        if "captcha" in driver.current_url.lower():
            raise RuntimeError("CAPTCHA encountered during login — cannot proceed automatically")

        log.info("Login successful, navigating to profile")
        driver.get("https://www.naukri.com/mnjuser/profile")
        time.sleep(random.uniform(2, 4))

        driver.execute_script("window.scrollBy(0, 600);")
        time.sleep(random.uniform(1, 2))

        # VERIFY THIS: resume upload input — usually a hidden <input type='file'>
        upload_input = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )
        if not os.path.isfile(RESUME_PATH):
            raise FileNotFoundError(f"Resume file not found at {RESUME_PATH}")

        upload_input.send_keys(RESUME_PATH)
        time.sleep(random.uniform(3, 6))

        log.info("Resume re-uploaded successfully — profile refreshed")
        send_email(
            "Naukri profile refresh: SUCCESS",
            f"Profile refreshed successfully at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST.",
        )

    except TimeoutException as e:
        log.error("Timeout waiting for element: %s", e)
        send_email(
            "Naukri profile refresh: FAILED (timeout)",
            f"Timed out waiting for a page element. This usually means Naukri's page "
            f"layout changed, or a CAPTCHA appeared. Manual check needed.\n\nError: {e}",
        )
    except Exception as e:
        log.error("Refresh job failed: %s", e)
        send_email(
            "Naukri profile refresh: FAILED",
            f"Automation failed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST.\n\n"
            f"Error: {e}\n\nPlease update your profile manually today.",
        )
    finally:
        if driver:
            driver.quit()


# ---------- DAILY RE-SCHEDULING WITH RANDOM TIME ----------
def schedule_next_run():
    """
    Picks a random time within the configured window for TODAY's run,
    removes any previous job, and schedules a fresh one-off job.
    Then schedules itself again for the next day at midnight to pick a new
    random time — this way the exact minute changes every day.
    """
    minute_offset = random.randint(WINDOW_START_MIN, WINDOW_END_MIN)
    hour = minute_offset // 60
    minute = minute_offset % 60

    scheduler.add_job(
        refresh_naukri_profile,
        trigger=CronTrigger(hour=hour, minute=minute, timezone="Asia/Kolkata"),
        id="daily_refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    log.info("Next refresh scheduled for %02d:%02d IST today", hour, minute)


def init_scheduler():
    # Pick today's random time immediately on startup
    schedule_next_run()
    # Every midnight, pick a new random time for the coming day
    scheduler.add_job(
        schedule_next_run,
        trigger=CronTrigger(hour=0, minute=1, timezone="Asia/Kolkata"),
        id="reshuffle_time",
        replace_existing=True,
    )
    scheduler.start()


init_scheduler()


# ---------- ROUTES ----------
@app.route("/")
def health():
    jobs = scheduler.get_jobs()
    next_runs = {job.id: str(job.next_run_time) for job in jobs}
    return {"status": "running", "scheduled_jobs": next_runs}


@app.route("/trigger-now")
def trigger_now():
    """Manual test endpoint — hit this URL to run the refresh immediately."""
    scheduler.add_job(refresh_naukri_profile, id="manual_trigger", replace_existing=True)
    return {"status": "triggered manually, check email for result"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
