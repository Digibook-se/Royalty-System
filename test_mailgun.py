#!/usr/bin/env python3
import os
import requests
from dotenv import load_dotenv

# Ladda .env från projektroten
load_dotenv()

# Hämta miljövariabler
DOMAIN  = os.getenv("MAILGUN_DOMAIN")
API_KEY = os.getenv("MAILGUN_API_KEY")
RECIP   = os.getenv("TEST_EMAIL") or "Marino Assarsson <info@digibook.se>"

# Debug: visa laddade variabler
print(f"MAILGUN_DOMAIN= {DOMAIN}")
print(f"MAILGUN_API_KEY= {API_KEY[:10] + '...' if API_KEY else None}")
print(f"TEST_EMAIL      = {RECIP}")

if not DOMAIN or not API_KEY:
    raise RuntimeError(
        "Sätt MAILGUN_DOMAIN och MAILGUN_API_KEY i .env eller som miljövariabler!"
    )

# Skicka en enkel text-mail utan bilaga
response = requests.post(
    f"https://api.mailgun.net/v3/{DOMAIN}/messages",
    auth=("api", API_KEY),
    data={
        "from":    f"Mailgun Sandbox <postmaster@{DOMAIN}>",
        "to":      [RECIP],
        "subject": "Test-Mailgun via Python",
        "text":    "Hej! Detta är ett test via Mailgun API."
    },
    timeout=10
)

# Kontrollera svarskod
try:
    response.raise_for_status()
    print("Mailgun svarade:", response.status_code, response.json())
except requests.exceptions.HTTPError as e:
    print(f"HTTPError: {e} - {response.text}")
    raise
