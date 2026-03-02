import time
import re
from datetime import datetime, timezone
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from dotenv import load_dotenv
import requests

# Load config from .env
load_dotenv()

# Mailgun settings
MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
MAILGUN_API_BASE = os.getenv("MAILGUN_API_BASE", "https://api.eu.mailgun.net")


if MAILGUN_DOMAIN and not MAILGUN_API_KEY:
    raise RuntimeError("MAILGUN_DOMAIN är satt men MAILGUN_API_KEY saknas i .env")

if MAILGUN_API_KEY and not MAILGUN_DOMAIN:
    raise RuntimeError("MAILGUN_API_KEY är satt men MAILGUN_DOMAIN saknas i .env")

#if not MAILGUN_API_KEY:
 #   raise RuntimeError("MAILGUN_API_KEY saknas i miljövariablerna")

# SMTP settings (fallback)
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PW   = os.getenv("SMTP_PW")

# From address
FROM_EMAIL = os.getenv("FROM_EMAIL") or SMTP_USER

# Debug loaded variables
#print(f"emailer.py – MAILGUN_DOMAIN = {MAILGUN_DOMAIN}")
#print(f"emailer.py – MAILGUN_API_KEY  = {MAILGUN_API_KEY[:10] + '...' if MAILGUN_API_KEY else None}")


def send_report(to_email: str, pdf_bytes: bytes, quarter: str) -> None:
    """
    Sends a PDF royalty report via Mailgun HTTP API (if configured),
    otherwise via SMTP STARTTLS/SSL.
    """
    subject = f"Royaltyutbetalning {quarter}"

    # If Mailgun configured, use HTTP API
    if MAILGUN_DOMAIN and MAILGUN_API_KEY:
        url = f"{MAILGUN_API_BASE}/v3/{MAILGUN_DOMAIN}/messages"
        data = {
            "from":    FROM_EMAIL,
            "to":      to_email,
            "subject": subject,
            "text":    f"Hej!\n\nSe bifogad royaltyrapport för {quarter}.\n\nVänliga hälsningar,\nDigibook.se"
        }
        files = [("attachment", (f"Royalty_{quarter.replace(' ', '_')}.pdf", pdf_bytes, "application/pdf"))]
        for attempt in range(5):
            resp = requests.post(
                url,
                auth=("api", MAILGUN_API_KEY),
                data=data,
                files=files,
                timeout=30
            )

            # 420 = Mailgun rate limit → vänta och försök igen
            if resp.status_code == 420:
                m = re.search(r"try again after (.+? UTC)", resp.text)
                if m:
                    retry_at = datetime.strptime(
                        m.group(1), "%a, %d %b %Y %H:%M:%S UTC"
                    ).replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)

                    # Vänta tills tiden + 2 minuter marginal
                    sleep_s = max(0, (retry_at - now).total_seconds()) + 120
                    time.sleep(sleep_s)
                    continue

                # Om vi inte kan läsa tiden: vänta 3 min och försök igen
                time.sleep(180)
                continue

            # Andra fel
            if resp.status_code >= 400:
                raise RuntimeError(f"Mailgun fel {resp.status_code}: {resp.text}")
            time.sleep(2)  # liten paus för att inte slå i Mailguns rate limits
            # Success
            return

        # Om vi provat flera gånger och fortfarande får 420
        raise RuntimeError(f"Mailgun fel 420 (rate limit) efter flera försök: {resp.text}")

    # Otherwise fallback to SMTP
    if not (SMTP_HOST and SMTP_USER and SMTP_PW):
        raise RuntimeError("Behöver Mailgun- eller SMTP-inställningar i .env")

    # Build email message
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = to_email
    msg["Date"]    = formatdate(localtime=True)
    msg.set_content(
        f"Hej!\n\nSe bifogad royaltyrapport för {quarter}.\n\nVänliga hälsningar,\nDitt Förlag"
    )
    filename = f"Royalty_{quarter.replace(' ', '_')}.pdf"
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    # Connect and send via SMTP
    if SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    else:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

    smtp.set_debuglevel(1)
    smtp.login(SMTP_USER, SMTP_PW)
    smtp.send_message(msg)
    smtp.quit()
