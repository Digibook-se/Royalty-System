#!/usr/bin/env python3
import os
import sys
import csv
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
load_dotenv()

from models import SessionLocal, Author

OUT_DIR = "out"
DEFAULT_EXPORT_DIR = "wpforms_exports"


def norm(s: str) -> str:
    return (s or "").strip()


def norm_email(s: str) -> str:
    return norm(s).lower()


def ensure_out_dir() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)


def read_wpforms_csv(path: str) -> Tuple[List[dict], List[str]]:
    """
    Läser CSV robust med vanliga encoding-varianter från WPForms-exporter.
    Returnerar (rows, headers)
    """
    encodings = ["utf-8-sig", "utf-8", "latin-1"]
    last_err = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                headers = reader.fieldnames or []
                return rows, headers
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Kunde inte läsa CSV '{path}'. Senaste fel: {last_err}")


def collect_csvs_from_dir(dir_path: str) -> List[str]:
    if not os.path.isdir(dir_path):
        return []
    out: List[str] = []
    for fn in os.listdir(dir_path):
        if fn.lower().endswith(".csv"):
            out.append(os.path.join(dir_path, fn))
    return sorted(out)


def parse_args(argv: List[str]) -> Tuple[Optional[str], List[str]]:
    """
    Stöd:
      python src/import_wpforms_authors.py --dir wpforms_exports
      python src/import_wpforms_authors.py file1.csv file2.csv
    """
    if not argv:
        return None, []
    if argv[0] in ("--dir", "-d"):
        if len(argv) < 2:
            raise SystemExit("❌ Du måste ange en mapp efter --dir. Ex: --dir wpforms_exports")
        return argv[1], []
    return None, argv


def find_header(headers: List[str], contains_any: List[str]) -> Optional[str]:
    """
    Hitta första header vars lower() innehåller någon kandidatsträng.
    """
    for h in headers:
        lh = (h or "").lower().strip()
        for c in contains_any:
            if c in lh:
                return h
    return None


def detect_columns(headers: List[str]) -> dict:
    """
    Detekterar:
      - email
      - name_first
      - name_last
      - name_full (fallback)
      - iban (prioriteras)
      - bank_local (fallback)
      - vat_registered (checkbox)
      - vat_number (om finns)
    """
    email_col = find_header(headers, ["email", "e-post", "epost", "e post", "mail"])

    name_first = find_header(headers, [
        "författare: first", "forfattare: first",
        "author: first", "name: first", "first name"
    ])
    name_last = find_header(headers, [
        "författare: last", "forfattare: last",
        "author: last", "name: last", "last name"
    ])
    name_full = find_header(headers, ["författare", "forfattare", "author name", "name"])

    # Separat IBAN (prioritet)
    iban_col = find_header(headers, ["iban"])

    bic_col = find_header(headers, ["bic", "swift"])

    # Fallback: kontonummer/clearing etc
    bank_local_col = find_header(headers, [
        "bankkontonummer",
        "kontonummer",
        "clearing",
        "bankkonto",
        "account number",
        "var vill du mottaga bokens intäkter",
        "var vill du motta bokens intäkter",
        "mottaga bokens intäkter",
        "motta bokens intäkter",
    ])

    # Checkbox: "Intäkten går till mitt företag som är momsregistrerat ..."
    vat_registered_col = find_header(headers, [
        "momsregistrerat",
        "momsregistrerad",
        "vat registered",
        "intäkten går till mitt företag",
    ])

    # VAT-/momsnummer (finns i Bankuppgifter-formuläret)
    vat_number_col = find_header(headers, [
        "momsregistreringsnummer",
        "vat number",
        "momsnummer",
    ])

    return {
        "email": email_col,
        "name_first": name_first,
        "name_last": name_last,
        "name_full": name_full,
        "iban": iban_col,
        "bank_local": bank_local_col,
        "vat_registered": vat_registered_col,
        "vat_number": vat_number_col,
        "bic": bic_col,
    }


def build_name(row: dict, cols: dict) -> str:
    first = norm(row.get(cols["name_first"], "")) if cols.get("name_first") else ""
    last = norm(row.get(cols["name_last"], "")) if cols.get("name_last") else ""

    if first or last:
        return norm(f"{first} {last}")

    full = norm(row.get(cols["name_full"], "")) if cols.get("name_full") else ""
    return full


def parse_checkbox_value(raw: str) -> int:
    """
    WPForms export brukar ge "Checked" om ikryssad, annars tomt.
    Men vi stödjer fler varianter.
    """
    v = norm(raw).lower()
    if v in ("checked", "1", "true", "yes", "ja", "y"):
        return 1
    return 0


def main() -> None:
    ensure_out_dir()

    args = sys.argv[1:]
    dir_arg, file_args = parse_args(args)

    if dir_arg:
        paths = collect_csvs_from_dir(dir_arg)
        if not paths:
            print(f"❌ Hittade inga .csv i mappen: {dir_arg}")
            sys.exit(1)
    elif file_args:
        paths = file_args
    else:
        paths = collect_csvs_from_dir(DEFAULT_EXPORT_DIR)
        if not paths:
            print("❌ Hittade inga .csv i wpforms_exports/")
            print("Kör: python src/import_wpforms_authors.py --dir wpforms_exports")
            sys.exit(1)

    missing_files = [p for p in paths if not os.path.exists(p)]
    if missing_files:
        print("❌ Hittar inte följande filer:")
        for p in missing_files:
            print(f"- {p}")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_txt = os.path.join(OUT_DIR, f"wpforms_import_{timestamp}.txt")
    report_csv = os.path.join(OUT_DIR, f"wpforms_import_{timestamp}.csv")

    report_lines: List[str] = []
    report_lines.append("WPForms import – Authors")
    report_lines.append("=" * 60)
    report_lines.append(f"Tid: {datetime.now().isoformat(timespec='seconds')}")
    report_lines.append("")
    report_lines.append("Filer:")
    for p in paths:
        report_lines.append(f"- {p}")
    report_lines.append("")

    total_rows = 0
    skipped_missing_email = 0
    created = 0
    updated = 0
    unchanged = 0

    db = SessionLocal()
    try:
        # Cache: email -> Author
        existing = db.query(Author).all()
        cache: Dict[str, Author] = {norm_email(a.email): a for a in existing if a.email}

        with open(report_csv, "w", encoding="utf-8", newline="") as f_out:
            writer = csv.writer(f_out)
            writer.writerow([
                "source_file", "action", "email", "name", "bank_account",
                "vat_registered", "vat_number",
                "email_col", "name_first_col", "name_last_col", "name_full_col",
                "iban_col", "bank_local_col", "vat_registered_col", "vat_number_col"
            ])

            for path in paths:
                rows, headers = read_wpforms_csv(path)
                total_rows += len(rows)

                cols = detect_columns(headers)

                report_lines.append(f"FIL: {path}")
                report_lines.append(f"- Rader: {len(rows)}")
                report_lines.append(
                    f"- Upptäckta kolumner: "
                    f"email={cols.get('email')}, "
                    f"name_first={cols.get('name_first')}, "
                    f"name_last={cols.get('name_last')}, "
                    f"name_full={cols.get('name_full')}, "
                    f"iban={cols.get('iban')}, "
                    f"bank_local={cols.get('bank_local')}, "
                    f"vat_registered={cols.get('vat_registered')}, "
                    f"vat_number={cols.get('vat_number')}"
                )
                report_lines.append("")

                for r in rows:
                    email = norm_email(r.get(cols["email"], "")) if cols.get("email") else ""
                    if not email:
                        skipped_missing_email += 1
                        continue

                    name = build_name(r, cols)

                    iban = norm(r.get(cols["iban"], "")) if cols.get("iban") else ""
                    bank_local = norm(r.get(cols["bank_local"], "")) if cols.get("bank_local") else ""
                    bic = norm(r.get(cols["bic"], "")) if cols.get("bic") else ""

                    # Prioritet: IBAN först, annars lokalt kontonummer
                    bank_account = iban or bank_local

                    vat_raw = r.get(cols["vat_registered"], "") if cols.get("vat_registered") else ""
                    vat_registered = parse_checkbox_value(str(vat_raw))

                    vat_number = norm(r.get(cols["vat_number"], "")) if cols.get("vat_number") else ""

                    author = cache.get(email)

                    if author is None:
                        author = Author(
                            email=email,
                            name=name or None,
                            bank_account=bank_account or None,
                            vat_registered=bool(vat_registered),
                            vat_number=vat_number or None,
                            bic=bic or None,
                        )
                        db.add(author)
                        cache[email] = author
                        action = "created"
                        created += 1
                    else:
                        changed = False

                        if name and (not author.name or norm(author.name) != name):
                            author.name = name
                            changed = True

                        if bank_account and (not author.bank_account or norm(author.bank_account) != bank_account):
                            author.bank_account = bank_account
                            changed = True

                        # Uppdatera momsstatus om kolumnen finns i filen
                        if cols.get("vat_registered") and bool(author.vat_registered) != bool(vat_registered):
                            author.vat_registered = bool(vat_registered)
                            changed = True

                        # Uppdatera momsnummer om det finns i raden
                        if vat_number and (not author.vat_number or norm(author.vat_number) != vat_number):
                            author.vat_number = vat_number
                            changed = True

                        if bic and (not author.bic or norm(author.bic) != bic):
                            author.bic = bic
                            changed = True

                        action = "updated" if changed else "unchanged"
                        if action == "updated":
                            updated += 1
                        else:
                            unchanged += 1

                    writer.writerow([
                        path, action, email, author.name or "", author.bank_account or "",
                        int(bool(author.vat_registered)), author.vat_number or "",
                        cols.get("email") or "",
                        cols.get("name_first") or "",
                        cols.get("name_last") or "",
                        cols.get("name_full") or "",
                        cols.get("iban") or "",
                        cols.get("bank_local") or "",
                        cols.get("vat_registered") or "",
                        cols.get("vat_number") or "",
                    ])

        db.commit()

        report_lines.append("SAMMANFATTNING")
        report_lines.append("-" * 60)
        report_lines.append(f"Totalt lästa rader:                 {total_rows}")
        report_lines.append(f"Unika emails (cache):               {len(cache)}")
        report_lines.append(f"Skapade Authors:                    {created}")
        report_lines.append(f"Uppdaterade Authors:                {updated}")
        report_lines.append(f"Oförändrade Authors:                {unchanged}")
        report_lines.append(f"Skippade rader (saknar email):      {skipped_missing_email}")
        report_lines.append("")
        report_lines.append("RAPPORTER SPARADE")
        report_lines.append("-" * 60)
        report_lines.append(f"- {report_txt}")
        report_lines.append(f"- {report_csv}")

        with open(report_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines) + "\n")

        print("\n".join(report_lines))

    except Exception:
        db.rollback()
        print("\n❌ Importen avbröts och inga ändringar sparades (rollback).")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
