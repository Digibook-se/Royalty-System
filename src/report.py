import io
import unicodedata
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_report(author_name: str, df: pd.DataFrame) -> bytes:
    """
    Genererar en PDF-rapport (bytes) för författarens royalty.
    Denna version normaliserar text till ASCII för att undvika encoding-fel.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    # Rubrik
    c.setFont("Helvetica-Bold", 16)
    title_text = f"Royalty Report - {author_name}"
    # Normalisera bort diakritiska tecken
    title_ascii = unicodedata.normalize('NFKD', title_text).encode('ascii', 'ignore').decode('ascii')
    c.drawString(50, 800, title_ascii)

    # Tabellen
    c.setFont("Helvetica", 12)
    y = 760
    for _, row in df.iterrows():
        line = (
            f"{row['Titel']} ({row['ISBN']}): "
            f"Nettobelopp {row['Nettobelopp']:.2f} SEK, "
            f"Andel {row['AuthorShare']:.2f} SEK"
        )
        # Normalisera text
        line_ascii = unicodedata.normalize('NFKD', line).encode('ascii', 'ignore').decode('ascii')
        c.drawString(50, y, line_ascii)
        y -= 20

        # Ny sida om vi når botten
        if y < 50:
            c.showPage()
            c.setFont("Helvetica", 12)
            y = 800

    c.showPage()
    c.save()
    return buffer.getvalue()
