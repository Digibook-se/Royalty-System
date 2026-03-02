import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import os
from pathlib import Path
from datetime import datetime
import streamlit as st

from services.db import init_db, get_db
from services.royalty_service import load_inputs_to_env, analyze_run, approve_run_plan

init_db()

st.title("🆕 Ny körning")

st.write("Ladda upp filer och klicka **Analysera**. Inget sparas i databasen förrän du klickar **Godkänn körning**.")

# --- Uploads ---
uploads_dir = Path("uploads")
uploads_dir.mkdir(exist_ok=True)

col1, col2 = st.columns(2)

with col1:
    elib_file = st.file_uploader("eLib CSV (obligatorisk)", type=["csv"], accept_multiple_files=False)

with col2:
    biblio_file = st.file_uploader("Biblio Excel (valfri)", type=["xlsx"], accept_multiple_files=False)
    biblio_from = st.text_input("Biblio fromdate (YYYY-MM-DD)", value="")
    biblio_to = st.text_input("Biblio todate (YYYY-MM-DD)", value="")

excluded_raw = st.text_input("EXCLUDED_AUTHORS (separera med ;)", value="")

st.divider()

def _save_upload(upload, suffix: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = uploads_dir / f"{ts}_{suffix}"
    path.write_bytes(upload.getvalue())
    return str(path)

analyze_clicked = st.button("🔎 Analysera", type="primary", disabled=(elib_file is None))

if analyze_clicked:
    try:
        elib_path = _save_upload(elib_file, "elib.csv")

        biblio_path = None
        if biblio_file is not None:
            biblio_path = _save_upload(biblio_file, "biblio.xlsx")

        load_inputs_to_env(
            elib_csv_path=elib_path,
            biblio_xlsx_path=biblio_path,
            biblio_from=biblio_from or None,
            biblio_to=biblio_to or None,
        )

        with get_db() as db:
            result = analyze_run(db, excluded_authors_raw=excluded_raw)

        st.session_state["analysis"] = {
            "period_key": result["summary"]["period_key"],
            "period_label": result["summary"]["period_label"],
            "excluded_raw": excluded_raw,
        }
        st.session_state["run_plan"] = result["run_plan"]
        st.session_state["items_df"] = result["items_df"]
        st.session_state["summary"] = result["summary"]

        st.success("Analys klar! Scrolla ned för preview och godkännande.")
    except Exception as e:
        st.error(f"Fel vid analys: {e}")

if "summary" in st.session_state:
    s = st.session_state["summary"]
    st.subheader("Sammanfattning")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Period", s["period_label"])
    c2.metric("Kommer betalas ut", s["num_will_payout"])
    c3.metric("Totalsumma (inkl moms)", f'{s["total_payout_inc_vat"]:.2f} SEK')
    c4.metric("Saknar IBAN", s["num_missing_iban"])

    st.subheader("Preview: Run items")
    st.dataframe(st.session_state["items_df"], use_container_width=True, hide_index=True)

    run_plan = st.session_state["run_plan"]
    tabs = st.tabs(["Saknas i DB", "Redan körda", "Exkluderade i CSV"])
    with tabs[0]:
        st.write(run_plan.missing_names if run_plan.missing_names else "—")
    with tabs[1]:
        st.write(run_plan.already_run_names if run_plan.already_run_names else "—")
    with tabs[2]:
        st.write(run_plan.excluded_in_csv if run_plan.excluded_in_csv else "—")

    st.divider()
    st.subheader("Godkänn körning")

    st.warning(
        "Detta skapar en **RoyaltyRun** i databasen (som underlag för bankfil). "
        "Det skickar inga mejl och gör inga utbetalningar ännu."
    )

    approve = st.button("✅ Godkänn körning (spara i DB)")

    if approve:
        try:
            with get_db() as db:
                run_id = approve_run_plan(
                    db=db,
                    run_plan=run_plan,
                    period_key=s["period_key"],
                    period_label=s["period_label"],
                )
            st.session_state["approved_run_id"] = run_id
            st.success(f"Körningen är sparad! run_id={run_id}. Gå nu till 'Kör & Export'.")
        except Exception as e:
            st.error(f"Fel vid godkännande: {e}")

if "approved_run_id" in st.session_state:
    st.info(f"Aktuell godkänd run_id: {st.session_state['approved_run_id']}")