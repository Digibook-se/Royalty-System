#!/usr/bin/env python3
import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# Ladda .env
load_dotenv()

api_key = os.getenv("MAILGUN_API_KEY")
if not api_key:
    raise RuntimeError("Ingen MAILGUN_API_KEY satt!")

# Hämta lista av domäner
resp = requests.get(
    "https://api.mailgun.net/v3/domains",
    auth=HTTPBasicAuth("api", api_key),
    timeout=10
)

print("Statuskod:", resp.status_code)
print(resp.json())
