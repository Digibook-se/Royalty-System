#!/usr/bin/env python3
import os
import sys
import re
import unicodedata
from datetime import datetime, timezone, date
from decimal import Decimal
import xml.etree.ElementTree as ET

_BIC_RE = re.compile(r"^[A-Z0-9]{8}([A-Z0-9]{3})?$")  # 8 eller 11 tecken

def clean_bic(bic: str | None) -> str:
    if not bic:
        return ""
    return bic.strip().upper().replace(" ", "").replace("/", "")

def is_valid_bic(bic: str) -> bool:
    return bool(_BIC_RE.match(bic))

from dotenv import load_dotenv
load_dotenv()

from models import SessionLocal, PayoutBatch, PayoutTransaction, Author

OUT_DIR = "out"

DEBTOR_NAME = os.getenv("DEBTOR_NAME", "Ditt Bolag AB")
DEBTOR_IBAN = os.getenv("DEBTOR_IBAN", "SE4180000818363030547990").replace(" ", "")
# BIC är ofta valfri, men vissa banker vill ha den. Sätt i .env om ni har.
DEBTOR_BIC = os.getenv("DEBTOR_BIC", "").strip()

# Swedbank Validex test-IBAN (roterande)
SWEDBANK_TEST_IBANS = [
    "SE4280000890119146168423",
    "SE2080000890119146168431",
    "SE1980000890119146168449",
    "SE2480000890119146168456",
    "SE0280000890119146168464",
]

_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 /-?:().,'+")

def sepa_sanitize(text: str | None, *, replacement: str = " ") -> str:
    """
    Gör text bank-godkänd:
    - ersätter & med OCH
    - tar bort å/ä/ö/é/ł (gör om till a/a/o/e/l)
    - ersätter allt annat konstigt med mellanslag
    """
    if not text:
        return ""

    text = text.replace("&", " OCH ")

    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))

    out = []
    for ch in text:
        out.append(ch if ch in _ALLOWED else replacement)

    text = "".join(out)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def is_test_iban_mode() -> bool:
    return (os.getenv("TEST_IBAN_MODE", "0") or "0").strip() == "1"


def dec(x) -> Decimal:
    return Decimal(str(x or "0"))

def parse_args(argv):
    if "--batch-id" not in argv:
        raise SystemExit("❌ Ange --batch-id, t.ex. --batch-id 1")
    batch_id = int(argv[argv.index("--batch-id") + 1])

    exec_date = None
    if "--exec-date" in argv:
        exec_date = argv[argv.index("--exec-date") + 1]  # YYYY-MM-DD
    return batch_id, exec_date

def text(parent, tag, value):
    el = ET.SubElement(parent, tag)
    el.text = str(value)
    return el

def main():
    batch_id, exec_date = parse_args(sys.argv[1:])
    os.makedirs(OUT_DIR, exist_ok=True)

    db = SessionLocal()
    try:
        batch = db.query(PayoutBatch).filter(PayoutBatch.id == batch_id).first()
        if not batch:
            raise SystemExit(f"❌ Hittar ingen payout_batch med id={batch_id}")

        txs = db.query(PayoutTransaction).filter(
            PayoutTransaction.batch_id == batch.id,
            PayoutTransaction.status != "blocked"
        ).all()

        author_ids = sorted({t.author_id for t in txs if t.author_id is not None})
        authors = db.query(Author.id, Author.bic).filter(Author.id.in_(author_ids)).all()
        bic_by_author_id = {a_id: (bic or "").strip().upper() for a_id, bic in authors}

        if not txs:
            raise SystemExit("❌ Inga transaktioner att exportera (alla blocked eller inga will_payout).")

        total_amount = sum([dec(t.amount) for t in txs], Decimal("0"))
        num = len(txs)

        # Execution date
        if exec_date:
            req_exec_date = exec_date
        else:
            req_exec_date = date.today().isoformat()

        # Minimal pain.001 (generisk)
        # OBS: Bankens MIG kan kräva specifik version/namespace.
        # Detta är en bra bas som brukar fungera, men om banken klagar så anpassar vi versionen.
        ns = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
        ET.register_namespace("", ns)
        ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")

        doc = ET.Element(
            f"{{{ns}}}Document",
            {
                "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation": f"{ns} pain.001.001.03.xsd"
            }
        )
        ccti = ET.SubElement(doc, f"{{{ns}}}CstmrCdtTrfInitn")

        # Group Header
        grphdr = ET.SubElement(ccti, f"{{{ns}}}GrpHdr")
        msg_id = f"ROY-{batch.period}-B{batch.id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        text(grphdr, f"{{{ns}}}MsgId", msg_id)
        text(grphdr, f"{{{ns}}}CreDtTm", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
        text(grphdr, f"{{{ns}}}NbOfTxs", str(num))
        ctrl_sum = ET.SubElement(grphdr, f"{{{ns}}}CtrlSum")
        ctrl_sum.text = f"{total_amount:.2f}"

        initg = ET.SubElement(grphdr, f"{{{ns}}}InitgPty")

        signer_id = (os.getenv("SWEDBANK_SIGNER_ID", "") or "").strip()

        if signer_id:
            initg_id = ET.SubElement(initg, f"{{{ns}}}Id")
            orgid = ET.SubElement(initg_id, f"{{{ns}}}OrgId")
            othr = ET.SubElement(orgid, f"{{{ns}}}Othr")
            ET.SubElement(othr, f"{{{ns}}}Id").text = signer_id

            schme = ET.SubElement(othr, f"{{{ns}}}SchmeNm")
            ET.SubElement(schme, f"{{{ns}}}Cd").text = "BANK"



        # Payment Info
        pmtinf = ET.SubElement(ccti, f"{{{ns}}}PmtInf")
        text(pmtinf, f"{{{ns}}}PmtInfId", f"P{batch.id}-{batch.period}")
        text(pmtinf, f"{{{ns}}}PmtMtd", "TRF")
        # Payment Type Info (många banker kräver SEPA här)
        text(pmtinf, f"{{{ns}}}NbOfTxs", str(num))
        cs = ET.SubElement(pmtinf, f"{{{ns}}}CtrlSum")
        cs.text = f"{total_amount:.2f}"


        text(pmtinf, f"{{{ns}}}ReqdExctnDt", req_exec_date)

        # Debtor
        dbtr = ET.SubElement(pmtinf, f"{{{ns}}}Dbtr")
        text(dbtr, f"{{{ns}}}Nm", sepa_sanitize(batch.debtor_name or DEBTOR_NAME))

        dbtracct = ET.SubElement(pmtinf, f"{{{ns}}}DbtrAcct")
        dbtracct_id = ET.SubElement(dbtracct, f"{{{ns}}}Id")
        text(dbtracct_id, f"{{{ns}}}IBAN", (batch.debtor_iban or DEBTOR_IBAN).replace(" ", ""))

        if DEBTOR_BIC:
            dbtragt = ET.SubElement(pmtinf, f"{{{ns}}}DbtrAgt")
            fin = ET.SubElement(dbtragt, f"{{{ns}}}FinInstnId")
            text(fin, f"{{{ns}}}BIC", DEBTOR_BIC)

        text(pmtinf, f"{{{ns}}}ChrgBr", "SLEV")

        # Testläge: byt mottagar-IBAN till Swedbanks testkonton (roterande)
        test_mode = is_test_iban_mode()
        test_idx = 0

        if test_mode:
            # Gör det extra tydligt att filen är en testfil
            msg_id = "TEST-" + msg_id
            # Uppdatera MsgId-elementet som redan skapats
            grphdr.find(f"{{{ns}}}MsgId").text = msg_id


        # Transactions
        for t in txs:
            cdt = ET.SubElement(pmtinf, f"{{{ns}}}CdtTrfTxInf")

            # --- Bestäm vilket IBAN som ska användas (test-rotationsläge eller riktigt) ---
            real_iban = (t.creditor_iban or "").replace(" ", "")

            if test_mode:
                iban_to_use = SWEDBANK_TEST_IBANS[test_idx % len(SWEDBANK_TEST_IBANS)]
                test_idx += 1
            else:
                iban_to_use = real_iban

            # Säkerhetsspärr – i testläge får vi aldrig använda real_iban
            if test_mode and iban_to_use == real_iban:
                raise RuntimeError(
                    "TEST_IBAN_MODE=1 men mottagar-IBAN blev inte utbytt – avbryter för säkerhets skull."
                )

            pmtid = ET.SubElement(cdt, f"{{{ns}}}PmtId")
            text(pmtid, f"{{{ns}}}EndToEndId", t.end_to_end_id)

            amt = ET.SubElement(cdt, f"{{{ns}}}Amt")
            instd = ET.SubElement(amt, f"{{{ns}}}InstdAmt", Ccy="SEK")
            instd.text = f"{dec(t.amount):.2f}"

            # --- Creditor Agent (Swedbank/Validex kräver CdtrAgt när PmtMtd=TRF) ---
            cdtragt = ET.SubElement(cdt, f"{{{ns}}}CdtrAgt")
            fin = ET.SubElement(cdtragt, f"{{{ns}}}FinInstnId")

            if test_mode:
                # Test-IBAN är Swedbank-testkonton -> Swedbank BIC
                text(fin, f"{{{ns}}}BIC", "SWEDSESS")
            else:
                bic_raw = bic_by_author_id.get(t.author_id, "")
                bic = clean_bic(bic_raw)

                if not bic:
                    raise RuntimeError(
                        f"Saknar BIC för author_id={t.author_id} ({t.creditor_name}). "
                        "Importera BIC till authors-tabellen innan export."
                    )

                if not is_valid_bic(bic):
                    raise RuntimeError(
                        f"OGILTIG BIC '{bic_raw}' (rensad='{bic}') för author_id={t.author_id} ({t.creditor_name}). "
                        "BIC måste vara 8 eller 11 tecken (A–Z/0–9)."
                    )

                text(fin, f"{{{ns}}}BIC", bic)

            cdtr = ET.SubElement(cdt, f"{{{ns}}}Cdtr")
            text(cdtr, f"{{{ns}}}Nm", sepa_sanitize(t.creditor_name or "OKAND"))

            pstl = ET.SubElement(cdtr, f"{{{ns}}}PstlAdr")
            text(pstl, f"{{{ns}}}Ctry", "SE")


            cdtracct = ET.SubElement(cdt, f"{{{ns}}}CdtrAcct")
            cdtracct_id = ET.SubElement(cdtracct, f"{{{ns}}}Id")

            text(cdtracct_id, f"{{{ns}}}IBAN", iban_to_use)


            rmt = ET.SubElement(cdt, f"{{{ns}}}RmtInf")
            text(rmt, f"{{{ns}}}Ustrd", sepa_sanitize(f"Royalty {batch.period}"))

            t.status = "exported"

        # Write file
        filename = f"pain001_{batch.period}_batch{batch.id}.xml"
        out_path = os.path.join(OUT_DIR, filename)

        tree = ET.ElementTree(doc)
        try:
            ET.indent(tree, space="  ", level=0)
        except Exception:
            pass

        xml_bytes = ET.tostring(doc, encoding="utf-8", xml_declaration=False)

        with open(out_path, "wb") as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n')
            f.write(xml_bytes.replace(b"\n", b"\r\n"))

        batch.pain001_filename = filename
        batch.status = "exported"
        db.commit()

        print("✅ Export klar")
        print(f"- Fil: {out_path}")
        print(f"- Antal transaktioner: {num}")
        print(f"- Total: {total_amount:.2f} SEK")

    finally:
        db.close()

if __name__ == "__main__":
    main()
