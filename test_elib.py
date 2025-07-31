# test_elib.py
import os
from dotenv import load_dotenv
from pandas import DataFrame

# 1) Ladda .env
load_dotenv(os.path.join(os.getcwd(), ".env"))

# 2) Importera funktionerna
from elib_client import fetch_invoice_csv, aggregate

# 3) Hämta rådata
df: DataFrame = fetch_invoice_csv()
print(f"Antal rader rådata: {len(df)}")
print(df.head().to_string(index=False))

# 4) Konsolidera
agg: DataFrame = aggregate(df)
print(f"\nAntal rader efter konsolidering: {len(agg)}")
print(agg.head().to_string(index=False))
