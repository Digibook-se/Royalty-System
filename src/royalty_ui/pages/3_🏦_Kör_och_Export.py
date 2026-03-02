import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from pathlib import Path
import streamlit as st
import pandas as pd

from services.db import init_db, get_db
from services.royalty_service import (
    create_payout_batch_for_run,
    export_blocked_xlsx_bytes,
    export_bookkeeping_csv_bytes,
    export_pain001_path,
)
from models import RoyaltyRun, PayoutBatch, PayoutTransaction, RoyaltyRunItem

init_db()

st.title("🏦 Kör & Export")

st.write("Här skapar du payout batch och laddar ner filer.")

default_run_id = st.session_state.get("approved_run_id", None)
run_id = st.number_input("run_id", min_value=1, step=1, value=int(default_run_id) if default_run_id else 1)

with get_db() as db:
    run = db.query(RoyaltyRun).filter(RoyaltyRun.id == int(run_id)).first()

if not run:
    st.warning("Hittar ingen run med det run_id du angav.")
    st.stop()

st.subheader("Run info")
st.write({
    "run_id": run.id,
    "period": run.period,
    "period_label": run.period_label,
    "created_at": run.created_at,
})

with get_db() as db:
    items = db.query(RoyaltyRunItem).filter(RoyaltyRunItem.run_id == int(run_id)).all()
df_items = pd.DataFrame([{
    "author_id": it.author_id,
    "author_name": it.author_name,
    "payout_inc_vat": float(it.payout_inc_vat),
    "will_payout": bool(it.will_payout),
    "bank_snapshot": it.bank_account_snapshot,
} for it in items])
st.dataframe(df_items, use_container_width=True, hide_index=True)

st.divider()

st.subheader("1) Skapa payout batch")
create_batch = st.button("➕ Skapa batch (från run)", type="primary")

if create_batch:
    try:
        with get_db() as db:
            batch_id = create_payout_batch_for_run(db, int(run_id))
        st.session_state["batch_id"] = batch_id
        st.success(f"Batch skapad! batch_id={batch_id}")
    except Exception as e:
        st.error(f"Fel vid batch-skapande: {e}")

batch_id = st.session_state.get("batch_id", None)
if batch_id:
    with get_db() as db:
        batch = db.query(PayoutBatch).filter(PayoutBatch.id == int(batch_id)).first()
        txs = db.query(PayoutTransaction).filter(PayoutTransaction.batch_id == int(batch_id)).all()

    st.subheader("Batch info")
    st.write({
        "batch_id": batch.id,
        "status": batch.status,
        "total_amount": float(batch.total_amount),
        "num_transactions": batch.num_transactions,
    })

    df_txs = pd.DataFrame([{
        "tx_id": t.id,
        "author_id": t.author_id,
        "name": t.creditor_name,
        "iban": t.creditor_iban,
        "amount": float(t.amount),
        "status": t.status,
        "reason": t.block_reason,
    } for t in txs]).sort_values(["status", "amount"], ascending=[True, False])
    st.dataframe(df_txs, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("2) Export & Ladda ner")

    # Blocked.xlsx
    blocked_bytes = None
    with get_db() as db:
        blocked_bytes = export_blocked_xlsx_bytes(db, int(batch_id))
    st.download_button(
        "⬇️ Ladda ner Blocked.xlsx",
        data=blocked_bytes,
        file_name=f"Blocked_batch_{batch_id}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # Bokföring (CSV)
    with get_db() as db:
        book_bytes = export_bookkeeping_csv_bytes(db, int(run_id))
    st.download_button(
        "⬇️ Ladda ner Bokföring.csv",
        data=book_bytes,
        file_name=f"Bokforing_run_{run_id}.csv",
        mime="text/csv",
    )

    # pain.001
    try:
        pain_path = export_pain001_path(int(batch_id))
        pain_bytes = Path(pain_path).read_bytes()
        st.download_button(
            "⬇️ Ladda ner pain.001.xml",
            data=pain_bytes,
            file_name=Path(pain_path).name,
            mime="application/xml",
        )
        st.success(f"pain.001 genererad: {pain_path}")
    except Exception as e:
        st.error(
            "Kunde inte exportera pain.001 automatiskt.\n\n"
            f"Fel: {e}\n\n"
            "Tips: Lägg till en funktion i export_pain001.py:\n"
            "    export_batch_to_file(batch_id) -> str\n"
            "så kan UI:t anropa den direkt."
        )
else:
    st.info("Skapa en batch först, så dyker exportknapparna upp här.")