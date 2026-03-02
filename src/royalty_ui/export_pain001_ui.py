#!/usr/bin/env python3
import os
import sys
from datetime import datetime, timezone, date
from decimal import Decimal
import xml.etree.ElementTree as ET

from dotenv import load_dotenv
load_dotenv()

from models import SessionLocal, PayoutBatch, PayoutTransaction

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

        doc = ET.Element(f"{{{ns}}}Document")
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
        text(pmtinf, f"{{{ns}}}NbOfTxs", str(num))
        cs = ET.SubElement(pmtinf, f"{{{ns}}}CtrlSum")
        cs.text = f"{total_amount:.2f}"


        text(pmtinf, f"{{{ns}}}ReqdExctnDt", req_exec_date)

        # Debtor
        dbtr = ET.SubElement(pmtinf, f"{{{ns}}}Dbtr")
        text(dbtr, f"{{{ns}}}Nm", batch.debtor_name or DEBTOR_NAME)

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

            iban_clean = (iban_to_use or "").replace(" ", "").upper()

            # I TEST_IBAN_MODE: alla test-IBAN du använder är Swedbank → sätt BIC så Validex blir nöjd.
            if test_mode:
                text(fin, f"{{{ns}}}BIC", "SWEDSESS")
            else:
                # PROD: försök använda svensk IBAN-bankkod (bbbb) som medlems-id i clearing-systemet
                # Svenska IBAN har format: SEkk bbbb ....
                if iban_clean.startswith("SE") and len(iban_clean) >= 8:
                    bank_code = iban_clean[4:8]  # bbbb
                    clrm = ET.SubElement(fin, f"{{{ns}}}ClrSysMmbId")
                    clrsys = ET.SubElement(clrm, f"{{{ns}}}ClrSysId")
                    text(clrsys, f"{{{ns}}}Cd", "SESBA")
                    text(clrm, f"{{{ns}}}MmbId", bank_code)
                else:
                    default_cdtr_bic = (os.getenv("DEFAULT_CDTR_BIC", "") or "").strip()
                    if not default_cdtr_bic:
                        raise RuntimeError(
                            "Saknar mottagarbank-id: kan inte härleda ClrSysMmbId från IBAN och DEFAULT_CDTR_BIC är inte satt."
                        )
                    text(fin, f"{{{ns}}}BIC", default_cdtr_bic)

            cdtr = ET.SubElement(cdt, f"{{{ns}}}Cdtr")
            text(cdtr, f"{{{ns}}}Nm", t.creditor_name or "OKÄND")

            pstl = ET.SubElement(cdtr, f"{{{ns}}}PstlAdr")
            text(pstl, f"{{{ns}}}Ctry", "SE")


            cdtracct = ET.SubElement(cdt, f"{{{ns}}}CdtrAcct")
            cdtracct_id = ET.SubElement(cdtracct, f"{{{ns}}}Id")

            text(cdtracct_id, f"{{{ns}}}IBAN", iban_to_use)


            rmt = ET.SubElement(cdt, f"{{{ns}}}RmtInf")
            text(rmt, f"{{{ns}}}Ustrd", f"Royalty {batch.period}")

            t.status = "exported"

        # Write file
        filename = f"pain001_{batch.period}_batch{batch.id}.xml"
        out_path = os.path.join(OUT_DIR, filename)

        tree = ET.ElementTree(doc)
        tree.write(out_path, encoding="utf-8", xml_declaration=True)

        batch.pain001_filename = filename
        batch.status = "exported"
        db.commit()

        print("✅ Export klar")
        print(f"- Fil: {out_path}")
        print(f"- Antal transaktioner: {num}")
        print(f"- Total: {total_amount:.2f} SEK")

    finally:
        db.close()

def export_batch_to_file(batch_id: int, exec_date=None) -> str:
    """Exporterar batch till pain.001 och returnerar sökvägen till XML-filen.

    Den här funktionen gör att Streamlit-UI:t kan anropa exporten utan att behöva köra scriptet via CLI.
    """
    if exec_date is None:
        exec_date = date.today()
    os.makedirs(OUT_DIR, exist_ok=True)
    db = SessionLocal()
    try:
        batch = db.query(PayoutBatch).filter(PayoutBatch.id == batch_id).first()
        if not batch:
            raise RuntimeError(f"Hittar ingen payout_batch med id={batch_id}")
        txs = db.query(PayoutTransaction).filter(
            PayoutTransaction.batch_id == batch.id,
            PayoutTransaction.status != "blocked"
        ).all()
        if not txs:
            raise RuntimeError("Inga transaktioner att exportera (alla blocked eller inga will_payout).")
        # Återanvänd samma logik som i main() genom att låna dess kodväg:
        # Vi bygger helt enkelt XML och skriver filen på samma sätt som main.
        total_amount = sum([dec(t.amount) for t in txs], Decimal("0"))
        num = len(txs)
        # ---- (Koden nedan är kopierad från main() med små justeringar) ----
        ns = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
        ET.register_namespace("", ns)
        doc = ET.Element(f"{{{ns}}}Document")
        cstmr = ET.SubElement(doc, f"{{{ns}}}CstmrCdtTrfInitn")
        grp = ET.SubElement(cstmr, f"{{{ns}}}GrpHdr")
        msg_id = f"BATCH{batch.id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        text(grp, f"{{{ns}}}MsgId", msg_id)
        text(grp, f"{{{ns}}}CreDtTm", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        text(grp, f"{{{ns}}}NbOfTxs", str(num))
        text(grp, f"{{{ns}}}CtrlSum", f"{total_amount:.2f}")
        initg = ET.SubElement(grp, f"{{{ns}}}InitgPty")
        text(initg, f"{{{ns}}}Nm", DEBTOR_NAME)
        pmt = ET.SubElement(cstmr, f"{{{ns}}}PmtInf")
        text(pmt, f"{{{ns}}}PmtInfId", f"PMT{batch.id}")
        text(pmt, f"{{{ns}}}PmtMtd", "TRF")
        text(pmt, f"{{{ns}}}BtchBookg", "true")
        text(pmt, f"{{{ns}}}NbOfTxs", str(num))
        text(pmt, f"{{{ns}}}CtrlSum", f"{total_amount:.2f}")
        pmt_tp = ET.SubElement(pmt, f"{{{ns}}}PmtTpInf")
        svc_lvl = ET.SubElement(pmt_tp, f"{{{ns}}}SvcLvl")
        text(svc_lvl, f"{{{ns}}}Cd", "SEPA")
        text(pmt, f"{{{ns}}}ReqdExctnDt", exec_date.isoformat())
        dbtr = ET.SubElement(pmt, f"{{{ns}}}Dbtr")
        text(dbtr, f"{{{ns}}}Nm", DEBTOR_NAME)
        dbtracct = ET.SubElement(pmt, f"{{{ns}}}DbtrAcct")
        dbtracct_id = ET.SubElement(dbtracct, f"{{{ns}}}Id")
        text(dbtracct_id, f"{{{ns}}}IBAN", DEBTOR_IBAN)
        dbtragt = ET.SubElement(pmt, f"{{{ns}}}DbtrAgt")
        fin = ET.SubElement(dbtragt, f"{{{ns}}}FinInstnId")
        if DEBTOR_BIC:
            text(fin, f"{{{ns}}}BIC", DEBTOR_BIC)
        text(pmt, f"{{{ns}}}ChrgBr", "SLEV")
        test_mode = (os.getenv("TEST_IBAN_MODE", "0") == "1")
        validex_ibans = (os.getenv("VALIDEX_TEST_IBANS", "") or "").split(",")
        validex_ibans = [x.strip().replace(" ", "") for x in validex_ibans if x.strip()]
        rotate = int(os.getenv("VALIDEX_ROTATE", "1") or "1")
        for idx, t in enumerate(txs, start=1):
            iban_clean = (t.creditor_iban or "").replace(" ", "")
            iban_to_use = iban_clean
            if test_mode and validex_ibans:
                if rotate:
                    iban_to_use = validex_ibans[(idx - 1) % len(validex_ibans)]
                else:
                    iban_to_use = validex_ibans[0]
            cdt = ET.SubElement(pmt, f"{{{ns}}}CdtTrfTxInf")
            pmtid = ET.SubElement(cdt, f"{{{ns}}}PmtId")
            text(pmtid, f"{{{ns}}}EndToEndId", t.end_to_end_id)
            amt = ET.SubElement(cdt, f"{{{ns}}}Amt")
            inst = ET.SubElement(amt, f"{{{ns}}}InstdAmt", Ccy=batch.currency or "SEK")
            inst.text = f"{dec(t.amount):.2f}"
            cdtragt = ET.SubElement(cdt, f"{{{ns}}}CdtrAgt")
            fin = ET.SubElement(cdtragt, f"{{{ns}}}FinInstnId")
            if test_mode:
                text(fin, f"{{{ns}}}BIC", "SWEDSESS")
            else:
                if iban_clean.startswith("SE") and len(iban_clean) >= 8:
                    bank_code = iban_clean[4:8]
                    clrm = ET.SubElement(fin, f"{{{ns}}}ClrSysMmbId")
                    clrsys = ET.SubElement(clrm, f"{{{ns}}}ClrSysId")
                    text(clrsys, f"{{{ns}}}Cd", "SESBA")
                    text(clrm, f"{{{ns}}}MmbId", bank_code)
                else:
                    default_cdtr_bic = (os.getenv("DEFAULT_CDTR_BIC", "") or "").strip()
                    if not default_cdtr_bic:
                        raise RuntimeError(
                            "Saknar mottagarbank-id: kan inte härleda ClrSysMmbId från IBAN och DEFAULT_CDTR_BIC är inte satt."
                        )
                    text(fin, f"{{{ns}}}BIC", default_cdtr_bic)
            cdtr = ET.SubElement(cdt, f"{{{ns}}}Cdtr")
            text(cdtr, f"{{{ns}}}Nm", t.creditor_name or "OKÄND")
            pstl = ET.SubElement(cdtr, f"{{{ns}}}PstlAdr")
            text(pstl, f"{{{ns}}}Ctry", "SE")
            cdtracct = ET.SubElement(cdt, f"{{{ns}}}CdtrAcct")
            cdtracct_id = ET.SubElement(cdtracct, f"{{{ns}}}Id")
            text(cdtracct_id, f"{{{ns}}}IBAN", iban_to_use)
            rmt = ET.SubElement(cdt, f"{{{ns}}}RmtInf")
            text(rmt, f"{{{ns}}}Ustrd", f"Royalty {batch.period}")
            t.status = "exported"
        filename = f"pain001_{batch.period}_batch{batch.id}.xml"
        out_path = os.path.join(OUT_DIR, filename)
        tree = ET.ElementTree(doc)
        tree.write(out_path, encoding="utf-8", xml_declaration=True)
        batch.pain001_filename = filename
        batch.status = "exported"
        db.commit()
        return out_path
    finally:
        db.close()
if __name__ == "__main__":
    main()