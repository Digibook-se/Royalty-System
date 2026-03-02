-- SQLite migration: add VAT fields to authors
ALTER TABLE authors ADD COLUMN vat_registered INTEGER NOT NULL DEFAULT 0;
ALTER TABLE authors ADD COLUMN vat_number TEXT;
