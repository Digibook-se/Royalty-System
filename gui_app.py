import os
import datetime
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from decimal import Decimal
import sys

# Lägg till src i path
BASE_DIR = os.path.dirname(__file__)
SRC_DIR = os.path.join(BASE_DIR, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Lokala imports efter sökväg
from elib_client import fetch_invoice_csv, aggregate
from report import make_report

# Ladda .env
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Beräkna tidigare kvartal för förifyllt värde
today = datetime.date.today()
current_quarter = (today.month - 1) // 3 + 1
prev_quarter = current_quarter - 1 if current_quarter > 1 else 4
prev_year = today.year if current_quarter > 1 else today.year - 1
default_period = f"Kvartal {prev_quarter}, {prev_year}"

# Streamlit-konfiguration
st.set_page_config(page_title="Royalty Pipeline GUI", layout="wide")
st.title("📊 Royalty Pipeline GUI")

# Period-input med förvalt värde
period = st.text_input("Period", value=default_period)

# Filuppladdning eller eLib-fetch
use_local = st.sidebar.checkbox("Använd lokal CSV-fil", value=False)
if use_local:
    uploaded = st.sidebar.file_uploader("Ladda upp eLib CSV", type=["csv"])
    if uploaded is None:
        st.sidebar.warning("Väntar på att du laddar upp en CSV-fil...")
        st.stop()
    df = pd.read_csv(uploaded)
else:
    try:
        df = fetch_invoice_csv()
    except Exception as e:
        st.error(f"Kunde inte hämta CSV från eLib: {e}")
        st.stop()

st.success(f"Data laddad: {len(df)} rader")

# Konvertera till Decimal vid behov
if 'TotalAmt' in df.columns:
    df['TotalAmt'] = df['TotalAmt'].apply(lambda x: Decimal(str(x)))

# Konsolidera data
aq_df = aggregate(df)

# Visa konsoliderad tabell
st.subheader("Konsoliderad data per titel för alla författare")
st.dataframe(aq_df)

# Författarval
authors = sorted(aq_df['Författarnamn'].unique())
selected = st.sidebar.selectbox("Välj författare", authors)
group = aq_df[aq_df['Författarnamn'] == selected]

# Visa detaljer
st.subheader(f"Detaljer för {selected}")
st.table(group[['Titel','ISBN','Nettobelopp','AuthorShare']]
         .rename(columns={'AuthorShare':'Författarandel'}).reset_index(drop=True))

# Saldo-input
prev_val = st.sidebar.number_input(
    "Saldo föregående period (kr)", min_value=0.0, value=0.0, step=1.0
)
prev_balance = Decimal(str(prev_val))

# Beräkna new balance och dagens share
today_share = Decimal(str(group['AuthorShare'].sum()))
new_balance = prev_balance + today_share
st.sidebar.markdown("---")
st.sidebar.write(f"**Författarandel denna period:** {today_share:.2f} kr")
st.sidebar.write(f"**Nytt sparat saldo:** {new_balance:.2f} kr")

# Generera PDF-rapport
if st.sidebar.button("Generera rapport och ladda ner PDF"):
    try:
        pdf_bytes = make_report(
            selected,
            group,
            period,
            prev_balance,
            new_balance
        )
        st.success("PDF genererad – klicka för nedladdning")
        st.download_button(
            "Ladda ner PDF",
            data=pdf_bytes,
            file_name=f"Royalty_{selected}.pdf",
            mime="application/pdf"
        )
    except Exception as e:
        st.error(f"Fel vid PDF-generering: {e}")
