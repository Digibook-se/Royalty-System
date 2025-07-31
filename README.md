# Royalty-System

Det här projektet automatiserar toutomatisering av royaltyhantering för författare i fyra regelbundna utbetalningar per år. Följande sektioner beskriver uppbyggnaden, dataprocessen och flödet genom programmet.

---

## Projektstruktur

```
royalty-system/
├── .env                  # Miljövariabler (API-nycklar, databaskonfig)
├── src/
│   ├── main.py           # Huvudskript: orchestrerar hela flödet
│   ├── elib_client.py    # Hämtar och konsoliderar CSV-data från eLib
│   ├── report.py         # Genererar PDF-rapporter för varje författare
│   ├── emailer.py        # Skickar ut rapporter via SMTP eller Mailgun API
│   ├── models.py         # SQLAlchemy-modeller och DB-setup
│   ├── seed_authors.py   # Script för att läsa in författarinfo från Excel/CSV
│   ├── api.py            # FastAPI-webhook för WPForms → sparar i DB
│   └── test_mailgun.py   # Enkel Mailgun-integrationstest
├── tests/                # Enhetstester med pytest
├── requirements.txt      # Pythonberoenden
└── README.md             # Denna översikt
```

### Miljövariabler i `.env`

```dotenv
# WordPress WPForms-webhook
WP_URL=https://www.digibook.se
WP_USER=<api_user>
WP_APP_PW=<application_password>
WP_FORM_ID=<form_id>

# eLib-påloggning
ELIB_USER=<din_elib_användare>
ELIB_PW=<ditt_elib_lösenord>

# Mailgun API (eller SMTP-inställningar om du använder emailer.py med SMTP)
MAILGUN_DOMAIN=<din_sandbox_domän>
MAILGUN_API_KEY=<din_mailgun_api_nyckel>
FROM_EMAIL=<avsändaradress>

# Testkonfiguration (kan tas bort i produktion)
TEST_EMAIL=<din_testadress>
TEST_LIMIT=1

# Databas
DATABASE_URL=sqlite:///./royalty.db
```

> **Observera:** Aldrig checka in `.env` med verkliga lösenord i versionshantering.

---

## 1. Författardata → DB

1. Författare fyller i WPForms-formulär på WordPress (plugin).
2. WPForms-webhook POST\:ar JSON till FastAPI-endpoint `/webhook` i `api.py`.
3. `api.py` validerar och sparar eller uppdaterar författarinfo (`authors`-tabell) i databasen.

## 2. Hämta och aggregera royaltydata

1. `main.py` anropar `elib_client.fetch_invoice_csv()`:

   * Loggar in på eLib via HTTP POST.
   * Hämtar senaste CSV (baserat på senaste filen).
   * Läser in som pandas DataFrame.
2. `elib_client.aggregate(df)`:

   * Grupperar på `Författarnamn`, `Titel`, och `ISBN`.
   * Summerar `Nettobelopp` per titel.
   * Beräknar 70% för författaren (`AuthorShare`) och 30% för förlaget (`PublisherShare`).

## 3. Generera PDF-rapporter

* `report.py` använder ReportLab:

  * Skapar enkel tabell eller lista med titel + belopp.
  * Normaliserar text till ASCII för rubriker.
  * Returnerar PDF som bytes.

## 4. Skicka e-post

* `emailer.py` har två varianter:

  1. **SMTP** (STARTTLS/SSL) mot ex. One.com
  2. **HTTP API** (Mailgun)
* `main.py` anropar `send_report(recipient, pdf_bytes, quarter)`.
* I testläge (`TEST_EMAIL`) skickas alltid dit och stoppas efter `TEST_LIMIT` mejl.

## 5. Spara royaltyposter

* Efter lyckad e-post körs:

  ```python
  royalty = Royalty(
      author_id=..., isbn=..., title=..., net_amount=..., author_share=..., publisher_share=...
  )
  db.add(royalty)
  ```
* `royalties`-tabellen ger full historik över utbetalningar.

---

## Körning & produktion

1. **Installera**:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Konfigurera** `.env` med API-nycklar, DB-URL, testinställningar.
3. **Initiera DB** och webhook:

   ```bash
   python src/main.py  # skapar tabeller, inga mejl skickas om ingen CSV hämtas
   ```
4. **Kör pipeline** (test):

   ```bash
   export TEST_EMAIL=you@yourdomain.com
   export TEST_LIMIT=1
   python3 -X utf8 src/main.py
   ```
5. **Produktion**:

   * Ta bort `TEST_EMAIL`/`TEST_LIMIT`.
   * Schemalägg med cron:

     ```cron
     0 8 1,4,7,10 * * cd /path/to/royalty-system && source venv/bin/activate && python3 src/main.py
     ```

---

Nu har du en helhets­bild av projektets uppbyggnad, processflöde och hur allt hänger ihop!
