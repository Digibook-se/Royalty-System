import os
import io
import re
import csv
import requests
import pandas as pd
from decimal import Decimal
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from urllib.parse import urljoin
from email.utils import parsedate_to_datetime
import unicodedata
from datetime import datetime

load_dotenv()

# Om du vill testa mot en lokal CSV istället för eLib, sätt denna env-var
LOCAL_CSV = os.getenv("/Users/Marino/royalty-system/Elib_files")

ELIB_USER = os.getenv("ELIB_USER")
ELIB_PW   = os.getenv("ELIB_PW")
LOGIN_URL        = "https://admin.elib.se/login.aspx"
HISTORY_LIST_URL = (
    "https://admin.elib.se/Publisher/"
    "publisher_invoice_historyNS.aspx?pub=3471"
)
DATE_REGEX = re.compile(r"fileName=.*?_(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.csv")


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_str = nfkd.encode('ascii', 'ignore').decode('ascii')
    return ascii_str.lower()


def fetch_invoice_csv() -> pd.DataFrame:
    # Om man vill testa med lokal CSV
    if LOCAL_CSV:
        print(f"Laddar lokal CSV: {LOCAL_CSV}")
        return pd.read_csv(LOCAL_CSV, sep=None, engine='python')

    sess = requests.Session()
    # 1) Inloggning
    resp0 = sess.get(LOGIN_URL, timeout=15)
    resp0.raise_for_status()
    soup0 = BeautifulSoup(resp0.text, "lxml")
    login_data = {inp['name']: inp.get('value', '') for inp in soup0.find_all('input', {'name': True})}
    login_data['ctl00$masterContentCenter$txtLogin'] = ELIB_USER
    login_data['ctl00$masterContentCenter$txtPassword'] = ELIB_PW
    resp1 = sess.post(LOGIN_URL, data=login_data, timeout=15)
    resp1.raise_for_status()

    # 2) Hämta historiksida
    resp_list = sess.get(HISTORY_LIST_URL, timeout=15)
    resp_list.raise_for_status()
    html = resp_list.text
    # Debug: skriv ut omenvägen kan inte hitta avsnitt
    print("DEBUG: laddad HTML, använd LOCAL_CSV för lokal testning.")

    soup_list = BeautifulSoup(html, 'lxml')

    # --- samla alla csv-länkar direkt ---
    csv_links = [
        urljoin(HISTORY_LIST_URL, a['href'])
        for a in soup_list.find_all('a', href=True)
        if a['href'].lower().endswith('.csv')
    ]
    if not csv_links:
        raise RuntimeError("Inga CSV-länkar hittades på eLib-sidan")

    # --- 3) välj senaste fil via HEAD/Last-Modified eller filnamnsdatum ---
    latest_url = None
    latest_dt = None
    for url in csv_links:
        dt = None
        try:
            head = sess.head(url, timeout=10, allow_redirects=True)
            head.raise_for_status()
            lm = head.headers.get('Last-Modified')
            dt = parsedate_to_datetime(lm) if lm else None
        except Exception:
            dt = None
        if dt is None:
            m = DATE_REGEX.search(url)
            if m:
                try:
                    dt = datetime.strptime(m.group(1), '%Y-%m-%d-%H-%M-%S')
                except Exception:
                    pass
        if latest_dt is None or (dt and dt > latest_dt):
            latest_dt = dt
            latest_url = url
    if not latest_url:
        latest_url = csv_links[0]

    print(f"Använder eLib-CSV: {latest_url}")
    print(f"Senaste datum: {latest_dt}")

    # 4) hämta CSV och läs
    resp_csv = sess.get(latest_url, timeout=20)
    resp_csv.raise_for_status()
    raw = resp_csv.text
    # auto-detect delimiter
    try:
        first = raw.splitlines()[0]
        dialect = csv.Sniffer().sniff(first, delimiters=[',',';','\t'])
        sep = dialect.delimiter
    except Exception:
        sep = ','
    df = pd.read_csv(io.StringIO(raw), sep=sep)
    return df


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={
        'Author': 'Författarnamn',
        'TitleAndCode': 'Titel',
        'IdentifyerCode': 'ISBN',
        'TotalAmt': 'Nettobelopp',
    })
    missing = [c for c in ['Författarnamn','Titel','ISBN','Nettobelopp'] if c not in df.columns]
    if missing:
        raise KeyError(f"Saknade kolumner: {missing}")
    grouped = df.groupby(['Författarnamn','Titel','ISBN'], as_index=False)['Nettobelopp'].sum()
    grouped['AuthorShare'] = grouped['Nettobelopp'] * Decimal('0.70')
    grouped['PublisherShare'] = grouped['Nettobelopp'] * Decimal('0.30')
    return grouped
