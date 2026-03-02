import sys
from pathlib import Path

# Lägg till royalty-system/src på Python path
SRC_DIR = Path(__file__).resolve().parents[1]  # .../royalty-system/src
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import streamlit as st

st.set_page_config(
    page_title="Royalty UI",
    page_icon="📚",
    layout="wide",
)

st.title("📚 Royalty UI")
st.write(
    "Välj sida i vänstermenyn.\n\n"
    "Tips: börja med **Ny körning**."
)

with st.expander("Första gången här? (kort guide)", expanded=True):
    st.markdown(
        """
**Så funkar appen:**

1. **Ny körning**: Ladda upp eLib CSV (och ev. Biblio Excel) → klicka *Analysera*.
2. Du får en **preview** (sammanfattning + tabell).
3. Klicka **Godkänn** → körningen sparas i databasen (ingen bankfil ännu).
4. **Kör & Export**: Skapa payout batch → ladda ner `pain.001`, bokföring och `Blocked.xlsx`.

⚠️ Streamlit kör om koden ofta. Därför är de viktiga knapparna tydligt separerade: *Analysera* → *Godkänn* → *Skapa batch* → *Export*.
"""
    )