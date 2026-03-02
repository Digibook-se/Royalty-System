import os
import sys
import datetime
import io
import csv
import streamlit as st
import pandas as pd
from decimal import Decimal
from dotenv import load_dotenv

# === Lägg till src i path ===
BASE_DIR = os.path.dirname(__file__)
SRC_DIR = os.path.join(BASE_DIR, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# === Lokala imports ===
from elib_client import aggregate
from report import make_report
from emailer import send_report
from models import SessionLocal, Author

# === Ladda .env ===
load_dotenv(os.path.join(BASE_DIR, '.env'))

# === Läs exkluderade författare en gång ===
EXCLUDED = [
    name.strip()
    for name in os.getenv("EXCLUDED_AUTHORS", "").split(",")
    if name.strip()
]

# === Period default (föregående kvartal) ===
today = datetime.date.today()
current_q = (today.month - 1) // 3 + 1
prev_q = current_q - 1 if current_q > 1 else 4
prev_year = today.year if current_q > 1 else today.year - 1
default_period = f"Kvartal {prev_q}, {prev_year}"

# === Streamlit-konfig ===
st.set_page_config(page_title="Royalty Manager", layout="wide")
st.title("📚 Royalty Manager GUI")

# === Sidebar-inställningar ===
st.sidebar.header("Inställningar")

# 1) Välj lokal CSV via browser-knapp
#    (uppladdning ger oss en fil-like buffer i minnet)
df_file = st.sidebar.file_uploader("Ladda upp eLib CSV", type=['csv'])
if df_file:
    st.sidebar.success(f"Vald fil: {df_file.name}")

# 2) Alternativ: ange sökväg manuellt (default: senaste CSV i ./Elib_files)
default_csv_dir = os.path.join(BASE_DIR, 'Elib_files')
default_csv = ''
if os.path.isdir(default_csv_dir):
    csvs = [f for f in os.listdir(default_csv_dir) if f.lower().endswith('.csv')]
    if csvs:
        latest = sorted(
            csvs,
            key=lambda x: os.path.getmtime(os.path.join(default_csv_dir, x)),
            reverse=True
        )[0]
        default_csv = os.path.join(default_csv_dir, latest)

csv_path = st.sidebar.text_input("eller sökväg till eLib CSV", value=default_csv)

# 3) Validera att vi har en källa
if not df_file and not os.path.isfile(csv_path):
    st.sidebar.error("Ogiltig CSV-fil. Ladda upp eller ange giltig sökväg.")
    st.stop()

# 4) Standard-output-mapp = samma som CSV-mappen
default_dir = os.path.dirname(csv_path) if csv_path else BASE_DIR
output_dir = st.sidebar.text_input(
    "Spara rapporter till mapp",
    value=default_dir,
    help="Mapp där PDF och payout CSV sparas"
)
if not os.path.isdir(output_dir):
    st.sidebar.error("Ogiltig mapp. Kontrollera sökväg.")
    st.stop()

# 5) Testläge och gräns
test_mode = st.sidebar.checkbox("Testläge (skicka bara till TEST_EMAIL)", value=True)
test_limit = st.sidebar.number_input(
    "Max antal mejl i testläget", min_value=1,
    value=int(os.getenv('TEST_LIMIT', '3'))
)

# 6) Period
period = st.sidebar.text_input("Period", value=default_period)

# === Hjälpfunktion: Läs CSV med auto-detect av separator ===
def load_df(file_obj, path=None):
    try:
        if file_obj:
            raw = file_obj.read().decode('utf-8')
        else:
            with open(path, 'r', encoding='utf-8') as f:
                raw = f.read()
        first = raw.splitlines()[0]
        dialect = csv.Sniffer().sniff(first, delimiters=[',',';','	'])
        sep = dialect.delimiter
    except Exception:
        sep = ','
    return pd.read_csv(io.StringIO(raw), sep=sep)

# === Läs in källdata ===
try:
    df = load_df(df_file, path=csv_path)
    st.success(f"Data laddad ({len(df)}) rader")
except Exception as e:
    # För bättre felsökning, visa kända kolumner om vi läser direkt från sökväg
    cols_hint = []
    try:
        cols_hint = list(pd.read_csv(csv_path, nrows=0).columns)
    except Exception:
        pass
    st.error(f"Kunde inte läsa CSV: {e}
Kolumner: {cols_hint}")
    st.stop()

# === Decimal-konvertering av TotalAmt (om finns) ===
if 'TotalAmt' in df.columns:
    df['TotalAmt'] = df['TotalAmt'].apply(
        lambda x: Decimal(str(x)) if pd.notnull(x) else Decimal('0')
    )

# === Konsolidera data och exkludera författare ===
try:
    agg_df = aggregate(df)
    if EXCLUDED:
        agg_df = agg_df[~agg_df['Författarnamn'].isin(EXCLUDED)]
except KeyError as ke:
    st.error(f"Saknade kolumner i data: {ke}")
    st.stop()

# === DB-saldo ===
session = SessionLocal()

def get_prev(name: str) -> Decimal:
    """Hämta carry-over från Authors-tabellen."""
    auth = session.query(Author).filter_by(name=name).first()
    return auth.carried_balance if auth else Decimal('0')

# === Summera per författare ===
summary = (
    agg_df.groupby('Författarnamn')['AuthorShare']
    .sum().to_frame('share_excl')
)
summary['prev_balance'] = [get_prev(n) for n in summary.index]
summary['total_balance'] = summary['prev_balance'] + summary['share_excl']

# === Kalkyl: moms, att betala, att spara ===
def calc(row):
    total = row['total_balance']
    if total >= Decimal('100'):
        pay_excl = total
        carry = Decimal('0')
    else:
        pay_excl = Decimal('0')
        carry = total
    vat = (pay_excl * Decimal('0.06')).quantize(Decimal('0.01'))
    pay_incl = (pay_excl + vat).quantize(Decimal('0.01'))
    return pd.Series({
        'Att betala ut (exkl moms)': pay_excl,
        'Moms 6%': vat,
        'Att betala ut (inkl moms)': pay_incl,
        'Att spara': carry
    })

# Ta även bort exkluderade i summary (redundans, men säkert)
if EXCLUDED:
    summary = summary.loc[~summary.index.isin(EXCLUDED)]

summary = summary.join(summary.apply(calc, axis=1))

# === Visa översikt ===
st.subheader("Översikt av utbetalningar")
display_df = summary[[
    'Att betala ut (exkl moms)',
    'Moms 6%',
    'Att betala ut (inkl moms)',
    'Att spara'
]]
st.dataframe(display_df.style.format('{:.2f}'))

# === Välj författare att processa ===
to_process = st.multiselect(
    "Välj författare",
    summary.index.tolist(),
    default=summary.index.tolist()
)

# === Generera & skicka ===
if st.sidebar.button("Generera & skicka rapporter"):
    results = []
    sent = 0
    for name in to_process:
        row = summary.loc[name]
        group = agg_df[agg_df['Författarnamn'] == name]

        # Skapa PDF
        pdf = make_report(name, group, period, row.prev_balance, row.total_balance)

        # Spara PDF
        fname = f"Royalty_{name.replace(' ', '_')}_{period.replace(' ', '_')}.pdf"
        path_pdf = os.path.join(output_dir, fname)
        with open(path_pdf, 'wb') as f:
            f.write(pdf)

        # Mottagare
        if test_mode:
            recipient = os.getenv('TEST_EMAIL')
        else:
            rec_obj = session.query(Author).filter_by(name=name).first()
            recipient = rec_obj.email if rec_obj else None

        # Skicka mejl (respektera test_limit)
        if recipient and (not test_mode or sent < test_limit):
            try:
                send_report(recipient, pdf, period)
                sent += 1
            except Exception as e:
                st.warning(f"Kunde inte skicka mejl till {name} ({recipient}): {e}")

        # Uppdatera DB: spara carry-over
        auth = session.query(Author).filter_by(name=name).first()
        if auth:
            auth.carried_balance = summary.at[name, 'Att spara']
            session.add(auth)

        # Payout-lista (bara de som faktiskt får en utbetalning)
        if summary.at[name, 'Att betala ut (exkl moms)'] > Decimal('0'):
            results.append((
                name,
                recipient or '',
                f"{summary.at[name, 'Att betala ut (inkl moms)']:.2f}"
            ))

    # Commit och skapa payout-CSV
    session.commit()
    payout_df = pd.DataFrame(results, columns=['Författarnamn','Email','Belopp inkl moms'])
    csv_path_out = os.path.join(output_dir, f"payouts_{period.replace(' ','_')}.csv")
    payout_df.to_csv(csv_path_out, index=False)

    st.success(f"Rapporter skapade ({sent} mejl skickade). CSV sparad: {csv_path_out}")
    session.close()

# === Liten info nederst ===
if EXCLUDED:
    st.info("Exkluderade författare (från .env EXCLUDED_AUTHORS):
- " + "
- ".join(EXCLUDED))
import os
import sys
import datetime
import io
import csv
import streamlit as st
import pandas as pd
from decimal import Decimal
from dotenv import load_dotenv

# === Lägg till src i path ===
BASE_DIR = os.path.dirname(__file__)
SRC_DIR = os.path.join(BASE_DIR, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# === Lokala imports ===
from elib_client import aggregate
from report import make_report
from emailer import send_report
from models import SessionLocal, Author

# === Ladda .env ===
load_dotenv(os.path.join(BASE_DIR, '.env'))

# === Läs exkluderade författare en gång ===
EXCLUDED = [
    name.strip()
    for name in os.getenv("EXCLUDED_AUTHORS", "").split(",")
    if name.strip()
]

# === Period default (föregående kvartal) ===
today = datetime.date.today()
current_q = (today.month - 1) // 3 + 1
prev_q = current_q - 1 if current_q > 1 else 4
prev_year = today.year if current_q > 1 else today.year - 1
default_period = f"Kvartal {prev_q}, {prev_year}"

# === Streamlit-konfig ===
st.set_page_config(page_title="Royalty Manager", layout="wide")
st.title("📚 Royalty Manager GUI")

# === Sidebar-inställningar ===
st.sidebar.header("Inställningar")

# 1) Välj lokal CSV via browser-knapp
#    (uppladdning ger oss en fil-like buffer i minnet)
df_file = st.sidebar.file_uploader("Ladda upp eLib CSV", type=['csv'])
if df_file:
    st.sidebar.success(f"Vald fil: {df_file.name}")

# 2) Alternativ: ange sökväg manuellt (default: senaste CSV i ./Elib_files)
default_csv_dir = os.path.join(BASE_DIR, 'Elib_files')
default_csv = ''
if os.path.isdir(default_csv_dir):
    csvs = [f for f in os.listdir(default_csv_dir) if f.lower().endswith('.csv')]
    if csvs:
        latest = sorted(
            csvs,
            key=lambda x: os.path.getmtime(os.path.join(default_csv_dir, x)),
            reverse=True
        )[0]
        default_csv = os.path.join(default_csv_dir, latest)

csv_path = st.sidebar.text_input("eller sökväg till eLib CSV", value=default_csv)

# 3) Validera att vi har en källa
if not df_file and not os.path.isfile(csv_path):
    st.sidebar.error("Ogiltig CSV-fil. Ladda upp eller ange giltig sökväg.")
    st.stop()

# 4) Standard-output-mapp = samma som CSV-mappen
default_dir = os.path.dirname(csv_path) if csv_path else BASE_DIR
output_dir = st.sidebar.text_input(
    "Spara rapporter till mapp",
    value=default_dir,
    help="Mapp där PDF och payout CSV sparas"
)
if not os.path.isdir(output_dir):
    st.sidebar.error("Ogiltig mapp. Kontrollera sökväg.")
    st.stop()

# 5) Testläge och gräns
test_mode = st.sidebar.checkbox("Testläge (skicka bara till TEST_EMAIL)", value=True)
test_limit = st.sidebar.number_input(
    "Max antal mejl i testläget", min_value=1,
    value=int(os.getenv('TEST_LIMIT', '3'))
)

# 6) Period
period = st.sidebar.text_input("Period", value=default_period)

# === Hjälpfunktion: Läs CSV med auto-detect av separator ===
def load_df(file_obj, path=None):
    try:
        if file_obj:
            raw = file_obj.read().decode('utf-8')
        else:
            with open(path, 'r', encoding='utf-8') as f:
                raw = f.read()
        first = raw.splitlines()[0]
        dialect = csv.Sniffer().sniff(first, delimiters=[',',';','	'])
        sep = dialect.delimiter
    except Exception:
        sep = ','
    return pd.read_csv(io.StringIO(raw), sep=sep)

# === Läs in källdata ===
try:
    df = load_df(df_file, path=csv_path)
    st.success(f"Data laddad ({len(df)}) rader")
except Exception as e:
    # För bättre felsökning, visa kända kolumner om vi läser direkt från sökväg
    cols_hint = []
    try:
        cols_hint = list(pd.read_csv(csv_path, nrows=0).columns)
    except Exception:
        pass
    st.error(f"Kunde inte läsa CSV: {e}
Kolumner: {cols_hint}")
    st.stop()

# === Decimal-konvertering av TotalAmt (om finns) ===
if 'TotalAmt' in df.columns:
    df['TotalAmt'] = df['TotalAmt'].apply(
        lambda x: Decimal(str(x)) if pd.notnull(x) else Decimal('0')
    )

# === Konsolidera data och exkludera författare ===
try:
    agg_df = aggregate(df)
    if EXCLUDED:
        agg_df = agg_df[~agg_df['Författarnamn'].isin(EXCLUDED)]
except KeyError as ke:
    st.error(f"Saknade kolumner i data: {ke}")
    st.stop()

# === DB-saldo ===
session = SessionLocal()

def get_prev(name: str) -> Decimal:
    """Hämta carry-over från Authors-tabellen."""
    auth = session.query(Author).filter_by(name=name).first()
    return auth.carried_balance if auth else Decimal('0')

# === Summera per författare ===
summary = (
    agg_df.groupby('Författarnamn')['AuthorShare']
    .sum().to_frame('share_excl')
)
summary['prev_balance'] = [get_prev(n) for n in summary.index]
summary['total_balance'] = summary['prev_balance'] + summary['share_excl']

# === Kalkyl: moms, att betala, att spara ===
def calc(row):
    total = row['total_balance']
    if total >= Decimal('100'):
        pay_excl = total
        carry = Decimal('0')
    else:
        pay_excl = Decimal('0')
        carry = total
    vat = (pay_excl * Decimal('0.06')).quantize(Decimal('0.01'))
    pay_incl = (pay_excl + vat).quantize(Decimal('0.01'))
    return pd.Series({
        'Att betala ut (exkl moms)': pay_excl,
        'Moms 6%': vat,
        'Att betala ut (inkl moms)': pay_incl,
        'Att spara': carry
    })

# Ta även bort exkluderade i summary (redundans, men säkert)
if EXCLUDED:
    summary = summary.loc[~summary.index.isin(EXCLUDED)]

summary = summary.join(summary.apply(calc, axis=1))

# === Visa översikt ===
st.subheader("Översikt av utbetalningar")
display_df = summary[[
    'Att betala ut (exkl moms)',
    'Moms 6%',
    'Att betala ut (inkl moms)',
    'Att spara'
]]
st.dataframe(display_df.style.format('{:.2f}'))

# === Välj författare att processa ===
to_process = st.multiselect(
    "Välj författare",
    summary.index.tolist(),
    default=summary.index.tolist()
)

# === Generera & skicka ===
if st.sidebar.button("Generera & skicka rapporter"):
    results = []
    sent = 0
    for name in to_process:
        row = summary.loc[name]
        group = agg_df[agg_df['Författarnamn'] == name]

        # Skapa PDF
        pdf = make_report(name, group, period, row.prev_balance, row.total_balance)

        # Spara PDF
        fname = f"Royalty_{name.replace(' ', '_')}_{period.replace(' ', '_')}.pdf"
        path_pdf = os.path.join(output_dir, fname)
        with open(path_pdf, 'wb') as f:
            f.write(pdf)

        # Mottagare
        if test_mode:
            recipient = os.getenv('TEST_EMAIL')
        else:
            rec_obj = session.query(Author).filter_by(name=name).first()
            recipient = rec_obj.email if rec_obj else None

        # Skicka mejl (respektera test_limit)
        if recipient and (not test_mode or sent < test_limit):
            try:
                send_report(recipient, pdf, period)
                sent += 1
            except Exception as e:
                st.warning(f"Kunde inte skicka mejl till {name} ({recipient}): {e}")

        # Uppdatera DB: spara carry-over
        auth = session.query(Author).filter_by(name=name).first()
        if auth:
            auth.carried_balance = summary.at[name, 'Att spara']
            session.add(auth)

        # Payout-lista (bara de som faktiskt får en utbetalning)
        if summary.at[name, 'Att betala ut (exkl moms)'] > Decimal('0'):
            results.append((
                name,
                recipient or '',
                f"{summary.at[name, 'Att betala ut (inkl moms)']:.2f}"
            ))

    # Commit och skapa payout-CSV
    session.commit()
    payout_df = pd.DataFrame(results, columns=['Författarnamn','Email','Belopp inkl moms'])
    csv_path_out = os.path.join(output_dir, f"payouts_{period.replace(' ','_')}.csv")
    payout_df.to_csv(csv_path_out, index=False)

    st.success(f"Rapporter skapade ({sent} mejl skickade). CSV sparad: {csv_path_out}")
    session.close()

# === Liten info nederst ===
if EXCLUDED:
    st.info("Exkluderade författare (från .env EXCLUDED_AUTHORS):
- " + "
- ".join(EXCLUDED))
