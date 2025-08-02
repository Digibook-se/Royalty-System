import io
import unicodedata
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib import colors

# PDF layout configuration
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 20 * mm
LOGO_PATH = "logo.png"  # Placera er logotyp i projektroten eller ange korrekt sökväg
FOOTER_TEXT = (
    "Media 24 / Digibook, Org.nr: 969796-0293, "
    "info@digibook.se, www.digibook.se"
)


def _header_footer(canvas, doc):
    # Header: logotyp
    try:
        logo = Image(LOGO_PATH, width=40*mm, height=12*mm)
        logo.drawOn(canvas, MARGIN, PAGE_HEIGHT - MARGIN - 12*mm)
    except Exception:
        pass

    # Footer text
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    footer_y = MARGIN / 2
    canvas.drawString(MARGIN, footer_y, FOOTER_TEXT)
    canvas.restoreState()


def to_ascii(s: str) -> str:
    """Normalize Unicode strings to ASCII by stripping diacritics."""
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')


def make_report(author_name: str, df) -> bytes:
    """Generate a PDF royalty report for a single author."""
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + 15*mm,
        bottomMargin=MARGIN + 10*mm,
    )

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id='normal'
    )
    template = PageTemplate(id='withHeaderFooter', frames=[frame], onPage=_header_footer)
    doc.addPageTemplates([template])

    styles = getSampleStyleSheet()
    # Title style
    title_style = ParagraphStyle(
        'Title', parent=styles['Heading1'], alignment=0, spaceAfter=12
    )
    normal = styles['Normal']

    # Build story
    story = []
    story.append(Paragraph(f"Royaltyrapport för {to_ascii(author_name)}", title_style))
    story.append(Spacer(1, 5*mm))

    # Table header
    data = [[
        Paragraph('<b>Titel</b>', normal),
        Paragraph('<b>ISBN</b>', normal),
        Paragraph('<b>Nettobelopp</b>', normal),
        Paragraph('<b>Författarandel</b>', normal),
        Paragraph('<b>Förlagsandel</b>', normal)
    ]]

    # Table rows
    for _, row in df.iterrows():
        data.append([
            Paragraph(to_ascii(row['Titel']), normal),
            Paragraph(row['ISBN'], normal),
            Paragraph(f"{row['Nettobelopp']:.2f}", normal),
            Paragraph(f"{row['AuthorShare']:.2f}", normal),
            Paragraph(f"{row['PublisherShare']:.2f}", normal),
        ])

    col_widths = [doc.width*0.4, doc.width*0.2, doc.width*0.13, doc.width*0.13, doc.width*0.14]
    table = Table(data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.gray),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 10)
    ]))
    story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
