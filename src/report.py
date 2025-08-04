import io
import unicodedata
from decimal import Decimal
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib import colors

# PDF layout
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 20 * mm
LOGO_PATH = "logo_new_darker.jpg"
FOOTER_TEXT = (
    "Media 24 / Digibook, Org.nr: 969796-0293, info@digibook.se, www.digibook.se"
)


def _header_footer(canvas, doc):
    # Header: logotyp
    try:
        logo = Image(LOGO_PATH, width=40*mm, height=12*mm)
        logo.drawOn(canvas, MARGIN, PAGE_HEIGHT - MARGIN - 12*mm)
    except Exception:
        pass
    # Footer
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    footer_y = MARGIN / 2
    canvas.drawCentredString(PAGE_WIDTH / 2, footer_y, FOOTER_TEXT)
    canvas.restoreState()


def to_ascii(s: str) -> str:
    # Behåll å, ä, ö oförändrat
    return str(s)


def make_report(author_name: str, df, period: str, prev_balance: Decimal, new_balance: Decimal) -> bytes:
    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN+15*mm, bottomMargin=MARGIN+10*mm)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
    doc.addPageTemplates([PageTemplate(id='pf', frames=[frame], onPage=_header_footer)])

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=14, spaceAfter=6)
    period_style = ParagraphStyle('Period', parent=styles['Normal'], fontSize=10, textColor=colors.black)
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8, leading=10)
    bold_cell = ParagraphStyle('BoldCell', parent=cell_style, fontName='Helvetica-Bold')
    notice_style = ParagraphStyle('Notice', parent=cell_style, fontSize=8, textColor=colors.black)

        # Beräkningar
    today = Decimal(str(df['AuthorShare'].sum()))
    vat_amount = (today * Decimal('0.06')).quantize(Decimal('0.01'))
    # Nytt saldo före utbetalning
    total_balance = prev_balance + today
    # Utbetalningslogik: bara betala ut om ackumulerat belopp >=100
    if total_balance >= Decimal('100'):
        paid_excl_vat = total_balance
        carry_over = Decimal('0.00')
    else:
        paid_excl_vat = Decimal('0.00')
        carry_over = total_balance
    paid_incl_vat = (paid_excl_vat + vat_amount).quantize(Decimal('0.01'))

    story = []
    # Titel och period
    story.append(Paragraph(f"Försäljningsrapport för {to_ascii(author_name)}", title_style))
    story.append(Paragraph(f"Period: {to_ascii(period)}", period_style))
    story.append(Spacer(1, 4))

    # Detaljerad tabell över titlar direkt efter period
    data = [
        [Paragraph('<b>ISBN - Titel</b>', cell_style), 
         Paragraph('<b>Intäkt</b>', cell_style), Paragraph('<b>Författarandel (70%)</b>', cell_style)]
    ]
    widths = [doc.width*0.7, doc.width*0.10, doc.width*0.2]
    for _, r in df.iterrows():
        data.append([
            Paragraph(to_ascii(r['Titel']), cell_style),
            #Paragraph(to_ascii(r['ISBN']), cell_style),
            Paragraph(f"{r['Nettobelopp']:.2f}", cell_style),
            Paragraph(f"{r['AuthorShare']:.2f}", cell_style)
        ])
    tbl = Table(data, colWidths=widths)
    tbl.setStyle(TableStyle([
        ('GRID',(0,0),(-1,-1),0.5,colors.grey),
        ('VALIGN',(0,0),(-1,-1),'TOP')
    ]))
    story.append(tbl)
    story.append(Spacer(1, 8))
    # Rubrik för sammanställning
    story.append(Paragraph("Sammanställning", period_style))
    story.append(Spacer(1, 4))

    # Tabell: Saldo och utbetalning
    summary_data = [
        [Paragraph('<b>Saldo föregående period(er)</b>', cell_style), Paragraph(f"{prev_balance:.2f} kr", cell_style)],
        [Paragraph('<b>Författarandel denna period</b>', cell_style), Paragraph(f"{today:.2f} kr", cell_style)],
        [Paragraph('<b>Att spara</b>', cell_style), Paragraph(f"{carry_over:.2f} kr", cell_style)],
        [Paragraph('<b>Att betala ut</b>', cell_style), Paragraph(f"{paid_excl_vat:.2f} kr", cell_style)],
    ]
    # Lägg endast till moms-avsnitt om det finns ett belopp att betala ut
    if paid_excl_vat > 0:
        summary_data.append([Paragraph('<b>Moms 6%</b>', cell_style), Paragraph(f"{vat_amount:.2f} kr", cell_style)])
        summary_data.append([Paragraph('<b>Att betala ut (inkl. moms)</b>', bold_cell), Paragraph(f"{paid_incl_vat:.2f} kr", bold_cell)])

    summary_tbl = Table(summary_data, colWidths=[doc.width*0.7, doc.width*0.3])
    summary_tbl.setStyle(TableStyle([
        ('GRID',(0,0),(-1,-1),0.5,colors.grey),
        ('ALIGN',(1,0),(-1,-1),'RIGHT')
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 6))

    # Notis längst ned
    notice = Paragraph(
        "Belopp under 100 kr betalas ej ut, beloppet sparas till nästa redovisning och utbetalning sker när det ackumulerade beloppet överstiger 100 kr",
        notice_style
    )
    story.append(notice)

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
