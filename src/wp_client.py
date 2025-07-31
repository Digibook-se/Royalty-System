import os
import requests
from dotenv import load_dotenv

load_dotenv()

WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PW = os.getenv("WP_APP_PW")  # format: user:application_password

def fetch_authors(form_id: int) -> list[dict]:
    """
    Hämtar alla inskick från WPForms-formulär med ID form_id,
    returnerar en lista dict med författarinfo.
    """
    url = f"{WP_URL}/wp-json/wpforms/v1/forms/{form_id}/entries"
    resp = requests.get(url, auth=(WP_USER, WP_APP_PW), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    entries = data.get("entries", [])
    authors = []
    for e in entries:
        fields = {f["name"]: f["value"] for f in e.get("fields", [])}
        authors.append({
            "email": fields.get("email"),
            "name": fields.get("name"),
            "bank_account": fields.get("bank_account"),
            # Lägg till fler fält här om du behöver
        })
    return authors
