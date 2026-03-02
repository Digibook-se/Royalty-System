import os
import re
import logging
import unicodedata
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()


def read_biblio_xlsx(path: str, fromdate: str, todate: str) -> pd.DataFrame:
    df = pd.read_excel(path)

    out = pd.DataFrame()

    # Sätt datumspann så main.py kan läsa period (valfritt men bra)
    out["FromDate"] = fromdate
    out["ToDate"] = todate

    # Mappa Biblio -> samma råformat som eLib (så aggregate() funkar för båda)
    out["Author"] = df["Author"].astype(str).str.strip()
    out["TitleAndCode"] = df["Title"].astype(str).str.strip()
    out["IdentifyerCode"] = df["ISBN"].astype(str).str.strip()
    out["TotalAmt"] = pd.to_numeric(df["Amount (SEK)"], errors="coerce").fillna(0)

    # Spårbarhet (frivilligt)
    out["Source"] = "BIBLIO"
    out["MaterialType"] = df.get("Material Type", "")

    return out


def maybe_append_biblio(df: pd.DataFrame) -> pd.DataFrame:
    """
    Om BIBLIO_FILE är satt i .env: läs biblio-xlsx och slå ihop med df.
    Returnerar alltid en DataFrame.
    """
    biblio_file = (os.getenv("BIBLIO_FILE") or "").strip().strip('"').strip("'")
    if not biblio_file:
        return df

    fromdate = (os.getenv("BIBLIO_FROMDATE") or "").strip()
    todate = (os.getenv("BIBLIO_TODATE") or "").strip()
    if not fromdate or not todate:
        raise RuntimeError("BIBLIO_FILE är satt men BIBLIO_FROMDATE/BIBLIO_TODATE saknas i .env")

    if not os.path.exists(biblio_file):
        raise RuntimeError(f"BIBLIO_FILE pekar på en fil som inte finns: {biblio_file}")

    logging.info("BIBLIO_FILE satt – läser biblio-xlsx: %s", biblio_file)
    bdf = read_biblio_xlsx(biblio_file, fromdate, todate)

    # Slå ihop (ignore_index så vi får en fin radindex)
    out = pd.concat([df, bdf], ignore_index=True)

    logging.info(
        "Biblio tillagt: +%d rader, total df=%d rader",
        len(bdf),
        len(out),
    )
    return out


def _norm_col(s: str) -> str:
    """
    Normaliserar kolumnnamn:
    - å/ä/ö -> a/a/o
    - tar bort mellanslag/underscore/bindestreck
    - lowercase
    """
    s = str(s).strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[\s_\-]+", "", s)
    return s


def _to_float_series(s: pd.Series) -> pd.Series:
    """
    Robust konvertering till float (klarar komma/punkt, mellanslag osv).
    """
    x = s.astype(str).str.replace("\u00a0", " ", regex=False).str.replace(" ", "", regex=False)
    x = x.str.replace(",", ".", regex=False)
    x = x.str.replace(r"[^0-9\.\-]", "", regex=True)
    return pd.to_numeric(x, errors="coerce").fillna(0.0)


def _read_csv_auto_bytes(content: bytes) -> pd.DataFrame:
    text = None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = content.decode("utf-8", errors="replace")

    lines = text.splitlines()
    first_line = lines[0] if lines else ""
    sep = ";" if first_line.count(";") >= first_line.count(",") else ","

    from io import StringIO
    return pd.read_csv(StringIO(text), sep=sep)


def _read_csv_auto(path: str) -> pd.DataFrame:
    with open(path, "rb") as f:
        return _read_csv_auto_bytes(f.read())


def fetch_invoice_csv() -> pd.DataFrame:
    """
    Hämtar senaste eLib invoice-CSV och returnerar DataFrame.

    Om LOCAL_CSV är satt läser vi lokalt (perfekt för test).
    """
    local_csv = os.getenv("LOCAL_CSV", "").strip()
    if local_csv:
        local_csv = local_csv.strip('"').strip("'")
        logging.info("LOCAL_CSV satt – läser lokal CSV: %s", local_csv)
        df = _read_csv_auto(local_csv)
        df = maybe_append_biblio(df)
        return df


    elib_user = os.getenv("ELIB_USER")
    elib_pw = os.getenv("ELIB_PW")
    if not elib_user or not elib_pw:
        raise RuntimeError("ELIB_USER/ELIB_PW saknas i .env (eller använd LOCAL_CSV för test).")

    base_url = "https://admin.elib.se"
    history_url = f"{base_url}/Publisher/publisher_invoice_historyNS.aspx"

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "royalty-system/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    r = session.get(history_url, timeout=30)
    r.raise_for_status()
    html = r.text

    # Best-effort hitta CSV-länkar i HTML
    matches = re.findall(
        r"(\/Publisher\/publisher_invoice_historyNS\.aspx\?invoiceID=\d+&fileName=[^\"\'\s>]+\.csv)",
        html,
    )
    if not matches:
        matches = re.findall(
            r"(https:\/\/admin\.elib\.se\/Publisher\/publisher_invoice_historyNS\.aspx\?invoiceID=\d+&fileName=[^\"\'\s>]+\.csv)",
            html,
        )

    if not matches:
        raise RuntimeError("Kunde inte hitta någon CSV-länk i eLib-sidan. Prova LOCAL_CSV för test.")

    def extract_dt(url: str) -> datetime:
        m = re.search(r"_(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.csv", url)
        if not m:
            return datetime.min
        return datetime.strptime(m.group(1), "%Y-%m-%d-%H-%M-%S")

    best = sorted(matches, key=extract_dt, reverse=True)[0]
    csv_url = base_url + best if best.startswith("/") else best

    logging.info("Använder eLib-CSV: %s", csv_url)

    csv_resp = session.get(csv_url, timeout=60)
    csv_resp.raise_for_status()

    df = _read_csv_auto_bytes(csv_resp.content)
    df = maybe_append_biblio(df)
    return df



def _resolve_columns(df: pd.DataFrame) -> dict:
    """
    Försöker hitta kolumner som motsvarar:
      author, title, isbn, net

    Din CSV verkar vara eLib-varianten med:
      Author, TitleAndCode, IdentifyerCode, TotalAmt
    """
    norm_map = {_norm_col(c): c for c in df.columns}

    # AUTHOR
    author_aliases = [
        "forfattarnamn", "author", "authorname", "forfattare", "namn"
    ]

    # TITLE
    # I din fil finns TitleAndCode
    title_aliases = [
        "titel", "title", "titleandcode", "booktitle", "verk", "boktitel", "product"
    ]

    # ISBN / identifierare
    # I din fil finns IdentifyerCode (stavningen är lite udda men vanligt i export)
    isbn_aliases = [
        "isbn", "isbn13", "isbn10", "identifyercode", "identifiercode", "identifyer", "identifyerid", "id", "product"
    ]

    # NET / belopp
    # I din fil finns TotalAmt
    net_aliases = [
        "nettobelopp", "netamount", "net", "netto", "totalamt", "total", "amount", "sum"
    ]

    def pick(aliases):
        for a in aliases:
            if a in norm_map:
                return norm_map[a]
        return None

    return {
        "author": pick(author_aliases),
        "title": pick(title_aliases),
        "isbn": pick(isbn_aliases),
        "net": pick(net_aliases),
    }


def _extract_title(title_and_code: str) -> str:
    """
    TitleAndCode kan ibland innehålla extra info.
    Vi gör en försiktig städning men behåller texten om vi är osäkra.
    """
    if title_and_code is None:
        return ""
    s = str(title_and_code).strip()

    # Vanliga mönster: "Titel (123...)" eller "Titel - 123..."
    # Ta bort en trailing kod i parentes om den ser ut som siffror/ISBN
    s2 = re.sub(r"\s*\((?:97[89]\d{10}|\d{9}[\dXx]|\d{6,})\)\s*$", "", s).strip()
    return s2 if s2 else s


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returnerar aggregerade royaltyrader per:
      Författarnamn, Titel, ISBN
    Skapar:
      Nettobelopp, AuthorShare, PublisherShare
    """
    cols = _resolve_columns(df)

    if not all(cols.values()):
        existing = list(df.columns)
        logging.error("CSV-kolumner hittades inte som väntat. Befintliga kolumner: %s", existing)
        raise KeyError(
            f"Saknade kolumner i eLib-data. "
            f"Jag hittade: author={cols['author']}, title={cols['title']}, isbn={cols['isbn']}, net={cols['net']}. "
            f"CSV har kolumner: {existing}"
        )

    work = df.copy()

    # Byt till standardnamn internt
    work = work.rename(
        columns={
            cols["author"]: "Författarnamn",
            cols["title"]: "Titel_raw",
            cols["isbn"]: "ISBN",
            cols["net"]: "Nettobelopp",
        }
    )

    # Titel-städning
    work["Titel"] = work["Titel_raw"].apply(_extract_title)

    # Belopp till float
    work["Nettobelopp"] = _to_float_series(work["Nettobelopp"])

    # Se till att ISBN är sträng
    work["ISBN"] = work["ISBN"].astype(str).fillna("")

    grouped = (
        work.groupby(["Författarnamn", "Titel", "ISBN"], dropna=False)["Nettobelopp"]
        .sum()
        .reset_index()
    )

    # Räkna i float (pandas)
    grouped["AuthorShare"] = (grouped["Nettobelopp"] * 0.70).round(2)
    grouped["PublisherShare"] = (grouped["Nettobelopp"] * 0.30).round(2)

    return grouped
