import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd

from services.db import init_db, get_db
from models import RoyaltyRun, PayoutBatch, PayoutTransaction

init_db()

st.title("🏠 Dashboard")

with get_db() as db:
    runs = db.query(RoyaltyRun).order_by(RoyaltyRun.created_at.desc()).limit(20).all()
    batches = db.query(PayoutBatch).order_by(PayoutBatch.created_at.desc()).limit(20).all()

st.subheader("Senaste royalty runs")
if not runs:
    st.info("Inga runs ännu.")
else:
    df_runs = pd.DataFrame([{
        "run_id": r.id,
        "period": r.period,
        "period_label": r.period_label,
        "vat_rate": float(r.vat_rate),
        "payout_threshold": float(r.payout_threshold),
        "created_at": r.created_at,
    } for r in runs])
    st.dataframe(df_runs, use_container_width=True, hide_index=True)

st.subheader("Senaste payout batches")
if not batches:
    st.info("Inga payout batches ännu.")
else:
    with get_db() as db:
        # räkna blocked per batch
        blocked_counts = {}
        for b in batches:
            blocked_counts[b.id] = db.query(PayoutTransaction).filter(
                PayoutTransaction.batch_id == b.id,
                PayoutTransaction.status == "blocked",
            ).count()

    df_batches = pd.DataFrame([{
        "batch_id": b.id,
        "run_id": b.run_id,
        "period": b.period,
        "status": b.status,
        "total_amount": float(b.total_amount),
        "num_transactions": b.num_transactions,
        "blocked": blocked_counts.get(b.id, 0),
        "created_at": b.created_at,
    } for b in batches])
    st.dataframe(df_batches, use_container_width=True, hide_index=True)

st.caption("Tips: Gå till 'Ny körning' för att analysera nya filer.")