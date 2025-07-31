import os
import io
import csv
import requests
import pandas as pd

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from urllib.parse import urljoin
from email.utils import parsedate_to_datetime

load_dotenv()

ELIB_USER = os.getenv("ELIB_USER")
ELIB_PW   = os.getenv("ELIB_PW")

LOGIN_URL        = "https://admin.elib.se/login.aspx"
HISTORY_LIST_URL = (
    "https://admin.elib.se/Publisher/"
    "publisher_invoice_historyNS.aspx?pub=3471"
)

def fetch_invoice_csv() -> pd.DataFrame:
    sess = requests.Session()

    # --- 1) Hämta login-sidan och dolda fält ---
    resp0 = sess.get(LOGIN_URL, timeout=10)
    resp0.raise_for_status()
    soup0 = BeautifulSoup(resp0.text, "lxml")

    login_data = {
        inp["name"]: inp.get("value", "")
        for inp in soup0.find_all("input", {"name": True})
    }
    # Anpassa mot dina faktiska name-attribut:
    login_data["ctl00$masterContentCenter$txtLogin"]    = ELIB_USER
    login_data["ctl00$masterContentCenter$txtPassword"] = ELIB_PW

    # --- 2) Posta login ---
    resp1 = sess.post(LOGIN_URL, data=login_data, timeout=10)
    resp1.raise_for_status()

    # --- 3) Hämta lista med CSV-länkar ---
    resp_list = sess.get(HISTORY_LIST_URL, timeout=10)
    resp_list.raise_for_status()
    soup_list = BeautifulSoup(resp_list.text, "lxml")

    csv_links = []
    for a in soup_list.find_all("a", href=True):
        if a["href"].lower().endswith(".csv"):
            csv_links.append(urljoin(HISTORY_LIST_URL, a["href"]))

    if not csv_links:
        raise RuntimeError("Inga CSV-länkar hittades på eLib-sidan")

    # --- 4) Välj senast ändrade fil via HEAD/Last-Modified ---
    latest_url = None
    latest_dt  = None
    for url in csv_links:
        head = sess.head(url, timeout=10, allow_redirects=True)
        if head.status_code != 200:
            continue
        lm = head.headers.get("Last-Modified")
        try:
            dt = parsedate_to_datetime(lm) if lm else None
        except Exception:
            dt = None
        if latest_dt is None or (dt and dt > latest_dt):
            latest_dt, latest_url = dt, url

    if latest_url is None:
        latest_url = csv_links[0]

    # --- 5) Hämta den utvalda CSV-filen ---
    resp_csv = sess.get(latest_url, timeout=10)
    resp_csv.raise_for_status()
    raw = resp_csv.text

    # --- 6) Auto-detect separator ---
    try:
        first = raw.splitlines()[0]
        dialect = csv.Sniffer().sniff(first, delimiters=[",", ";", "\t"])
        sep = dialect.delimiter
    except Exception:
        sep = ","

    df = pd.read_csv(io.StringIO(raw), sep=sep)
    return df

def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Renam:ar kolumner och konsoliderar nettobelopp per författare/titel/ISBN.
    Beräknar 70% till författaren, 30% till förlaget.
    """
    # Mappa egna kolumnnamn till standard
    df = df.rename(columns={
        "Author": "Författarnamn",
        "TitleAndCode": "Titel",
        "IdentifyerCode": "ISBN",
        "TotalAmt": "Nettobelopp",
    })

    # Kontroll
    missing = [c for c in ["Författarnamn","Titel","ISBN","Nettobelopp"] if c not in df.columns]
    if missing:
        raise KeyError(f"Saknade kolumner i DataFrame: {missing}")

    grouped = (
        df
        .groupby(["Författarnamn", "Titel", "ISBN"], as_index=False)["Nettobelopp"]
        .sum()
    )
    grouped["AuthorShare"]    = grouped["Nettobelopp"] * 0.70
    grouped["PublisherShare"] = grouped["Nettobelopp"] * 0.30
    return grouped
