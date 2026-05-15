#!/usr/bin/env python3
"""
EHC Helpdesk Service Monitor

Checks health of ehc-helpdesk and ehc-vllm services every 60 seconds.
If a service fails 5 consecutive checks, sends an email alert and attempts restart.

Environment variables:
    ALERT_EMAIL_FROM     — sender email address
    ALERT_EMAIL_TO       — recipient email address
    ALERT_SMTP_HOST      — SMTP server (default: smtp.gmail.com)
    ALERT_SMTP_PORT      — SMTP port (default: 587)
    ALERT_SMTP_PASSWORD  — SMTP app password

Usage:
    python3 scripts/monitor.py
"""

import json
import os
import smtplib
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.mime.text import MIMEText


# --- Configuration ---

SERVICES = [
    {
        "name": "ehc-helpdesk",
        "url": "http://localhost:8080/health",
        "systemd_unit": "ehc-helpdesk",
    },
    {
        "name": "ehc-vllm",
        "url": "http://localhost:8000/health",
        "systemd_unit": "ehc-vllm",
    },
]

CHECK_INTERVAL = 60  # seconds
FAIL_THRESHOLD = 5   # consecutive failures before alert + restart (~5 min grace for vLLM startup)
HTTP_TIMEOUT = 10    # seconds

# Email config from environment
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
ALERT_SMTP_HOST = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
ALERT_SMTP_PORT = int(os.getenv("ALERT_SMTP_PORT", "587"))
ALERT_SMTP_PASSWORD = os.getenv("ALERT_SMTP_PASSWORD", "")


# --- State ---

# Track consecutive failures per service
_fail_counts: dict[str, int] = {s["name"]: 0 for s in SERVICES}


def check_health(url: str) -> tuple[bool, str]:
    """
    Check a service health endpoint.
    Returns (is_healthy, detail_message).
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode())
                if body.get("status") == "ok":
                    return True, "OK"
                return False, f"Unexpected response: {body}"
            return False, f"HTTP {resp.status}"
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_alert_email(service_name: str, failure_detail: str) -> bool:
    """
    Send an alert email. Returns True if sent successfully.
    """
    if not all([ALERT_EMAIL_FROM, ALERT_EMAIL_TO, ALERT_SMTP_PASSWORD]):
        print(f"[MONITOR] Email not configured, skipping alert for {service_name}")
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"[EHC Helpdesk] Service DOWN: {service_name}"
    body = (
        f"Service Monitor Alert\n"
        f"{'=' * 40}\n\n"
        f"Timestamp   : {now}\n"
        f"Service     : {service_name}\n"
        f"Failure     : {failure_detail}\n"
        f"Consecutive : {FAIL_THRESHOLD} failed checks\n"
        f"Action      : Auto-restart attempted via systemctl\n\n"
        f"Please check the service status:\n"
        f"  sudo systemctl status {service_name}\n"
        f"  sudo journalctl -u {service_name} --since '5 min ago'\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = ALERT_EMAIL_FROM
    msg["To"] = ALERT_EMAIL_TO

    try:
        with smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(ALERT_EMAIL_FROM, ALERT_SMTP_PASSWORD)
            server.sendmail(ALERT_EMAIL_FROM, [ALERT_EMAIL_TO], msg.as_string())
        print(f"[MONITOR] Alert email sent to {ALERT_EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[MONITOR] Failed to send email: {type(e).__name__}: {e}")
        return False


def restart_service(systemd_unit: str) -> bool:
    """
    Attempt to restart a systemd service.
    Returns True if the command succeeded.
    """
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", systemd_unit],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"[MONITOR] Successfully restarted {systemd_unit}")
            return True
        else:
            print(f"[MONITOR] Restart failed: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        print(f"[MONITOR] Restart timed out for {systemd_unit}")
        return False
    except Exception as e:
        print(f"[MONITOR] Restart error: {type(e).__name__}: {e}")
        return False


def run_check_cycle():
    """Run one check cycle for all services."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")

    for service in SERVICES:
        name = service["name"]
        url = service["url"]
        unit = service["systemd_unit"]

        healthy, detail = check_health(url)

        if healthy:
            if _fail_counts[name] > 0:
                print(f"[{now}] {name}: RECOVERED (was {_fail_counts[name]} failures)")
            _fail_counts[name] = 0
        else:
            _fail_counts[name] += 1
            print(f"[{now}] {name}: FAIL ({_fail_counts[name]}/{FAIL_THRESHOLD}) — {detail}")

            if _fail_counts[name] >= FAIL_THRESHOLD:
                print(f"[{now}] {name}: THRESHOLD REACHED — sending alert and restarting")
                send_alert_email(name, detail)
                restart_service(unit)
                _fail_counts[name] = 0  # reset after restart attempt


def main():
    """Main monitoring loop."""
    print("[MONITOR] EHC Helpdesk Service Monitor starting")
    print(f"[MONITOR] Checking {len(SERVICES)} services every {CHECK_INTERVAL}s")
    print(f"[MONITOR] Alert threshold: {FAIL_THRESHOLD} consecutive failures")
    print(f"[MONITOR] Email alerts: {'configured' if ALERT_EMAIL_FROM else 'NOT configured'}")
    print()

    while True:
        try:
            run_check_cycle()
        except Exception as e:
            print(f"[MONITOR] Unexpected error in check cycle: {type(e).__name__}: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
