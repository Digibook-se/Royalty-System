from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

import pandas as pd
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _dec(x: Any) -> Decimal:
    """Coerce input (Decimal/float/int/str/None) to Decimal safely."""
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    try:
        s = str(x).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
        return Decimal(s)
    except Exception:
        return Decimal("0")


def _sv_money(x: Any) -> str:
    d = _dec(x).quantize(Decimal("0.01"))
    # Swedish-like formatting: space thousands, comma decimal
    s = f"{d:.2f}"
    whole, frac = s.split(".")
    # add space thousand sep
    parts = []
    while len(whole) > 3:
        parts.insert(0, whole[-3:])
        whole = whole[:-3]
    parts.insert(0, whole)
    return " ".join(parts) + "," + frac


def _sv_num(x: Any) -> str:
    d = _dec(x).quantize(Decimal("0.01"))
    s = f"{d:.2f}"
    return s.replace(".", ",")


def _resolve_logo_path() -> Optional[Path]:
    candidates = [
        Path("Logo_new_darker.jpg"),
        Path("Logo_new_darker.png"),
        Path("logo.jpg"),
        Path("logo.png"),
        Path("assets/logo.jpg"),
        Path("assets/logo.png"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

def _strip_isbn_prefix_from_title(title: str) -> str:
    """
    Tar bort prefix som ser ut som ISBN + separator från början av en titel.
    Ex:
      '9781234567890 - Min bok' -> 'Min bok'
      '978-91-1-23456-7 – Min bok' -> 'Min bok'
    """
    if title is None:
        return ""

    s = str(title).strip()

    # Normalisera olika dash-tecken till vanligt '-'
    s_norm = s.replace("–", "-").replace("—", "-").replace("−", "-")

    # Matcha ISBN-13 (med eller utan bindestreck/spaces) i början + separator
    # Tillåter: 978... eller 979...
    m = re.match(r"^\s*((97[89][\d\-\s]{10,20}))\s*-\s*(.+)$", s_norm)
    if not m:
        return s.strip()

    rest = m.group(3).strip()
    return rest if rest else s.strip()

def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Try to locate/standardize expected columns."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["Titel", "ISBN", "Antal", "Nettobelopp", "AuthorShare"])

    d = df.copy()

    # If already normalized by main.py, keep it
    # Otherwise attempt fallbacks
    colmap = {}
    for c in d.columns:
        lc = str(c).strip().lower()
        if lc in ("titel", "title"):
            colmap[c] = "Titel"
        elif lc in ("isbn",):
            colmap[c] = "ISBN"
        elif lc in ("antal", "qty", "quantity", "count"):
            colmap[c] = "Antal"
        elif lc in ("nettobelopp", "netto", "net", "net_amount"):
            colmap[c] = "Nettobelopp"
        elif lc in ("forfattarandel", "författarandel", "authorshare", "author_share", "royalty"):
            colmap[c] = "AuthorShare"

    if colmap:
        d = d.rename(columns=colmap)

    # Ensure columns exist
    for need in ["Titel", "ISBN", "Antal", "Nettobelopp", "AuthorShare"]:
        if need not in d.columns:
            d[need] = ""

    # Clean
    d["Titel"] = d["Titel"].astype(str).fillna("").str.strip()
    d["ISBN"] = d["ISBN"].astype(str).fillna("").str.strip()
    # numbers
    for col in ["Antal", "Nettobelopp", "AuthorShare"]:
        d[col] = d[col].apply(_dec)

    return d


# ---------------------------------------------------------------------
# Public API used by main.py
# ---------------------------------------------------------------------

def make_report(
    author_name: str,
    df: pd.DataFrame,
    period: str,
    prev_balance: Any,
    *args: Any,
    vat_rate: Decimal = Decimal("0.06"),
) -> bytes:
    """
    Build a royalty PDF (bytes).

    Compatible calling patterns:
      - make_report(name, df, period, prev_balance, new_balance)
      - make_report(name, df, period, prev_balance, period_royalty, payout_excl_vat, new_balance)
      - make_report(name, df, period, prev_balance, period_royalty, payout_excl_vat, new_balance, vat_rate=Decimal('0.06'))
    """
    # Parse flexible args
    period_royalty = None
    payout_excl_vat = None
    new_balance = None

    if len(args) == 1:
        new_balance = args[0]
    elif len(args) >= 3:
        period_royalty = args[0]
        payout_excl_vat = args[1]
        new_balance = args[2]
    elif len(args) == 2:
        # ambiguous: assume period_royalty, new_balance
        period_royalty = args[0]
        new_balance = args[1]

    prev_balance_d = _dec(prev_balance)
    new_balance_d = _dec(new_balance)

    dfn = _normalize_df(df)
        # Slå ihop rader så att samma ISBN blir en rad (summerar Nettobelopp & Författarandel)
    dfn = dfn.copy()
    dfn["ISBN"] = dfn["ISBN"].astype(str).fillna("").str.strip()
    dfn["Titel"] = dfn["Titel"].astype(str).fillna("").str.strip()

    # Nyckel: ISBN om det finns, annars Titel (för rader utan ISBN)
    dfn["_key"] = dfn["ISBN"].where(dfn["ISBN"] != "", dfn["Titel"])

    dfn = (
        dfn.groupby("_key", as_index=False)
        .agg({
            "Titel": "first",
            "ISBN": "first",
            "Nettobelopp": "sum",
            "AuthorShare": "sum",
        })
        .drop(columns=["_key"])
    )


    # Totals
    period_total_d = _dec(period_royalty) if period_royalty is not None else _dec(dfn["AuthorShare"].sum())
    payout_excl_d = _dec(payout_excl_vat) if payout_excl_vat is not None else period_total_d
    vat_d = (payout_excl_d * _dec(vat_rate)).quantize(Decimal("0.01"))
    payout_incl_d = (payout_excl_d + vat_d).quantize(Decimal("0.01"))

    # Document setup
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Royaltyrapport – {author_name} – {period}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=18, leading=22, spaceAfter=6)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, leading=14, spaceBefore=10, spaceAfter=6)
    base = ParagraphStyle("Base", parent=styles["BodyText"], fontSize=10, leading=12)
    small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=8.5, leading=10)

    story = []

    # Logo + Title
    logo = _resolve_logo_path()
    if logo:
        try:
            img = RLImage(str(logo), width=55 * mm, height=18 * mm)
            story.append(img)
            story.append(Spacer(1, 2 * mm))
        except Exception:
            pass

    story.append(Paragraph("Royaltyrapport", h1))
    story.append(Paragraph(f"<b>Författare:</b> {author_name}", base))
    story.append(Paragraph(f"<b>Period:</b> {period}", base))
    story.append(Spacer(1, 6 * mm))

    # Summary table (2 columns)
    story.append(Paragraph("Sammanfattning", h2))

    summary_rows = [
        ["Ingående saldo (från föregående period)", f"{_sv_money(prev_balance_d)} kr"],
        ["Royalty denna period (exkl moms)", f"{_sv_money(period_total_d)} kr"],
        ["Utbetalt denna period (exkl moms)", f"{_sv_money(payout_excl_d)} kr"],
        ["Moms 6% på utbetalt belopp", f"{_sv_money(vat_d)} kr"],
        ["Utbetalt denna period (inkl ev. moms)", f"{_sv_money(payout_incl_d)} kr"],
        ["Utgående saldo (överförs till nästa period)", f"{_sv_money(new_balance_d)} kr"],
    ]

    sum_tbl = Table(
        summary_rows,
        colWidths=[95 * mm, 55 * mm],
        hAlign="LEFT",
    )
    sum_tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(sum_tbl)

    # Details table
    story.append(Paragraph("Detaljer per titel", h2))

    # Build rows
    header = ["Titel", "ISBN", "Nettobelopp", "Författarandel"]

    rows = [header]

    for _, r in dfn.iterrows():
        title = _strip_isbn_prefix_from_title(str(r.get("Titel", "")).strip())
        isbn = str(r.get("ISBN", "") or "")
        net = _sv_money(r.get("Nettobelopp", 0))
        share = _sv_money(r.get("AuthorShare", 0))

        rows.append(
            [
                Paragraph(title.replace("&", "&amp;"), small),
                Paragraph(isbn.replace("&", "&amp;"), small),
                net,
                share,
            ]
        )


    # Column widths sum to ~170mm (page width 210 - 40mm margins)
    col_widths = [95 * mm, 30 * mm, 22 * mm, 22 * mm]

    det_tbl = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    det_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(det_tbl)

    # Footer note
    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            "Beloppen ovan avser ersättning till dig som författare. "
            "Om moms inte ska tillämpas för just din ersättning kommer moms-raden vara 0 kr. Belopp under 100 kr betalas ej ut, beloppet sparas till nästa period tills ackumulerat belopp överstiger 100 kr.",
            ParagraphStyle("Foot", parent=small, textColor=colors.grey),
        )
    )

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
