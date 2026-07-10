-- 25-appointments.sql — imported booking history, one row per SESSION DAY.
--
-- The operator's booking system exports appointment history as CSV (a
-- multi-session appointment repeats its appointment_id across several slot
-- dates); studio/appointment_import.py ingests it here. Why not tattoo_history
-- (21-customer-personas.sql): that table is style/artist/date/notes only — no
-- appointment_id, no amounts, no unique key — so a re-upload there could only
-- duplicate. This table is the full-fidelity, additive home for the export:
--   * natural key (tenant_id, appointment_id, slot_date) — re-uploading the
--     same export is a no-op (INSERT ... ON CONFLICT DO NOTHING);
--   * slot_date is the export's VERBATIM date string (keys work even for a
--     format we can't parse); slot_date_parsed carries the typed date when the
--     format was recognizable, NULL otherwise — never guessed;
--   * blanks stay NULL, non-numeric amounts ("TBD") stay NULL, internal_note
--     is stored verbatim.
-- Runtime twin: studio/appointment_import.py ensure_schema (bootstrap_db.py).
-- Idempotent (CREATE ... IF NOT EXISTS) like every other initdb file.

CREATE TABLE IF NOT EXISTS appointments (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    customer_id        TEXT,
    appointment_id     TEXT NOT NULL,
    slot_id            TEXT,
    slot_date          TEXT NOT NULL,
    slot_date_parsed   DATE,
    slot_time          TEXT,
    duration           TEXT,
    slot_type          TEXT,
    slot_title         TEXT,
    status             TEXT,
    tattoo_description TEXT,
    style              TEXT,
    size               TEXT,
    placement          TEXT,
    deposit            NUMERIC,
    total              NUMERIC,
    quoted_amount      NUMERIC,
    tbd                TEXT,
    internal_note      TEXT,
    customer_name      TEXT,
    customer_email     TEXT,
    customer_phone     TEXT,
    source_file        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS appointments_natural_key
    ON appointments (tenant_id, appointment_id, slot_date);

CREATE INDEX IF NOT EXISTS appointments_customer_idx
    ON appointments (tenant_id, customer_id);
