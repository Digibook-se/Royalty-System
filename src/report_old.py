import io
import os
from decimal import Decimal
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Table,
    TableStyle,
    Spacer,
    Image,
)

# Generate royalty report PDF
styles = getSampleStyleSheet()

# Custom Header style
if 'Header' not in styles:
    styles.add(ParagraphStyle(
        name="Header",
        parent=styles["Heading2"],
        fontSize=14,
        leading=16,
        textColor=colors.black,
        spaceAfter=6,
    ))

# Table header
if 'TableHeader' not in styles:
    styles.add(ParagraphStyle(
        name="TableHeader",
        parent=styles["Normal"],
        fontSize=9,
        leading=11,
        alignment=1,  # center
        textColor=colors.black,
        spaceBefore=4,
        spaceAfter=4,
    ))

# Body text
if 'Body' not in styles:
    styles.add(ParagraphStyle(
        name="Body",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        textColor=colors.black,
    ))

# Notice text
if 'Notice' not in styles:
    styles.add(ParagraphStyle(
        name="Notice",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.black,
        spaceBefore=8,
        spaceAfter=8,
    ))

NOTICE_TEXT = (
    "Belopp under 100 kr betalas ej ut, beloppet sparas till nästa "
    "redovisning och utbetalning sker när det ackumulerade beloppet överstiger 100 kr."
)


def _footer(canvas, doc):
    footer_text = (
        'Media 24 / Digibook, Organisationsnummer: 969796-0293, '
        'info@digibook.se, www.digibook.se'
    )
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.black)
    canvas.drawCentredString(
        doc.leftMargin + doc.width / 2,
        doc.bottomMargin / 2,
        footer_text
    )
    canvas.restoreState()


def make_report(
    author_name,
    df,
    period,
    prev_balance: Decimal,
    period_share: Decimal,
    payout_excl_vat: Decimal,
    carry_to_next: Decimal,
):
    """
    Skapar PDF för en författare.

    Viktigt:
    - payout_excl_vat och carry_to_next ska räknas i main.py (affärslogiken hör hemma där).
    - Rapporten visar alltid moms och inkl moms (även om 0.00), för konsekvent redovisning.
    """
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=80,
        bottomMargin=60,
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id='normal'
    )
    template = PageTemplate(id='report', frames=[frame], onPage=_footer)
    doc.addPageTemplates([template])

    elements = []

    # Logo top-left, scaled 30% smaller
    logo_path = os.path.join(os.getcwd(), "logo_new_darker.jpg")
    if os.path.exists(logo_path):
        img = Image(logo_path)
        img.drawHeight = 35
        img.drawWidth = img.imageWidth * 35.0 / img.imageHeight
        img.hAlign = 'LEFT'
        elements.append(img)
        elements.append(Spacer(1, 12))

    # Period and author
    elements.append(Paragraph(f"Period: {period}", styles['Header']))
    elements.append(Paragraph(f"Författare: {author_name}", styles['Header']))
    elements.append(Spacer(1, 12))

    # Data table
    data = [[
        Paragraph("Titel", styles['TableHeader']),
        Paragraph("ISBN", styles['TableHeader']),
        Paragraph("Netto (kr)", styles['TableHeader']),
        Paragraph("Författarandel (kr)", styles['TableHeader']),
    ]]
    for _, row in df.iterrows():
        data.append([
            Paragraph(str(row['Titel']), styles['Body']),
            Paragraph(str(row['ISBN']), styles['Body']),
            Paragraph(f"{float(row['Nettobelopp']):.2f}", styles['Body']),
            Paragraph(f"{float(row['AuthorShare']):.2f}", styles['Body']),
        ])
    col_widths = [doc.width * 0.5, doc.width * 0.2, doc.width * 0.15, doc.width * 0.15]
    table = Table(data, colWidths=col_widths, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))

    # === Summary calculations (alltid 6% moms) ===
    vat = (payout_excl_vat * Decimal('0.06')).quantize(Decimal('0.01'))
    payout_incl_vat = (payout_excl_vat + vat).quantize(Decimal('0.01'))

    # Summary table
    elements.append(Paragraph('Sammanställning', styles['Header']))

    summ_data = [
        ['Saldo sparat från föregående period (kr)', f"{prev_balance:.2f}"],
        ['Författarandel denna period (kr)', f"{period_share:.2f}"],
        ['Att betala ut (exkl. moms)', f"{payout_excl_vat:.2f}"],
        ['Moms 6% på utbetalning', f"{vat:.2f}"],
        ['Att betala ut (inkl. moms)', f"{payout_incl_vat:.2f}"],
        ['Att spara till nästa period', f"{carry_to_next:.2f}"],
    ]

    summ_table = Table(
        summ_data,
        colWidths=[doc.width * 0.6, doc.width * 0.4],
        hAlign='LEFT'
    )

    style_cmds = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]

    # Markera "Att betala ut (inkl. moms)" extra tydligt
    for idx, row in enumerate(summ_data):
        if row[0] == 'Att betala ut (inkl. moms)':
            style_cmds += [
                ('FONTNAME', (0, idx), (-1, idx), 'Helvetica-Bold'),
                ('FONTSIZE', (0, idx), (-1, idx), 10),
            ]
            break

    summ_table.setStyle(TableStyle(style_cmds))
    elements.append(summ_table)

    # Notice
    elements.append(Paragraph(NOTICE_TEXT, styles['Notice']))
    elements.append(Spacer(1, 12))

    # Build PDF
    doc.build(elements)
    return buffer.getvalue()
