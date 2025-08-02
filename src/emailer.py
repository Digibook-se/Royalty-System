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

# SMTP settings (fallback)
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PW   = os.getenv("SMTP_PW")

# From address
FROM_EMAIL = os.getenv("FROM_EMAIL") or SMTP_USER

# Debug loaded variables
print(f"emailer.py – MAILGUN_DOMAIN = {MAILGUN_DOMAIN}")
print(f"emailer.py – MAILGUN_API_KEY  = {MAILGUN_API_KEY[:10] + '...' if MAILGUN_API_KEY else None}")


def send_report(to_email: str, pdf_bytes: bytes, quarter: str) -> None:
    """
    Sends a PDF royalty report via Mailgun HTTP API (if configured),
    otherwise via SMTP STARTTLS/SSL.
    """
    subject = f"Royaltyutbetalning {quarter}"

    # If Mailgun configured, use HTTP API
    if MAILGUN_DOMAIN and MAILGUN_API_KEY:
        url = f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages"
        data = {
            "from":    FROM_EMAIL,
            "to":      to_email,
            "subject": subject,
            "text":    f"Hej!\n\nSe bifogad royaltyrapport för {quarter}.\n\nVänliga hälsningar,\nDitt Förlag"
        }
        files = [("attachment", (f"Royalty_{quarter.replace(' ', '_')}.pdf", pdf_bytes, "application/pdf"))]
        resp = requests.post(
            url,
            auth=("api", MAILGUN_API_KEY),
            data=data,
            files=files,
            timeout=30
        )
        resp.raise_for_status()
        return

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
