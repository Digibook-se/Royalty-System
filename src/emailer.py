import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from dotenv import load_dotenv

# Ladda SMTP-inställningar
load_dotenv()
SMTP_HOST  = os.getenv("SMTP_HOST")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER")
SMTP_PW    = os.getenv("SMTP_PW")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)

def send_report(to_email: str, pdf_bytes: bytes, quarter: str) -> None:
    if not (SMTP_HOST and SMTP_USER and SMTP_PW):
        raise RuntimeError("Behöver SMTP_HOST, SMTP_USER och SMTP_PW i .env")

    # Bygg meddelandet
    msg = EmailMessage()
    msg["Subject"] = f"Royaltyutbetalning {quarter}"
    msg["From"]    = FROM_EMAIL
    msg["To"]      = to_email
    msg["Date"]    = formatdate(localtime=True)
    msg.set_content(
        f"Hej!\n\nSe bifogad royaltyrapport för {quarter}.\n\nVänliga hälsningar,\nDitt Förlag"
    )
    filename = f"Royalty_{quarter.replace(' ', '_')}.pdf"
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    # Anslut och skicka
    if SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    else:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

    # För felsökning, sätt debugnivå:
    smtp.set_debuglevel(1)

    smtp.login(SMTP_USER, SMTP_PW)
    smtp.send_message(msg)
    smtp.quit()
