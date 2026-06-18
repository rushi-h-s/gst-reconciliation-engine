-- ============================================================
-- GST Reconciliation Engine — initial schema
-- Every table carries org_id; RLS is the tenant-isolation boundary.
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor) for full
-- privileges. Running via a pooler connection will skip the auth.org_id()
-- helper because the auth schema is restricted; RLS policies must be applied
-- from the dashboard in that case.
-- ============================================================

-- Extensions
-- pgmq must be enabled from Dashboard → Database → Extensions before running this.
DO $$ BEGIN
    CREATE EXTENSION IF NOT EXISTS pgmq;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgmq not available — enable from Dashboard → Database → Extensions';
END $$;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Enums (idempotent) ────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE doc_type AS ENUM ('invoice', 'credit_note', 'debit_note');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE match_status AS ENUM (
        'MATCHED', 'PROBABLE', 'MISMATCH', 'BOOKS_ONLY', 'TWOB_ONLY'
    );
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE extraction_status AS ENUM ('pending', 'extracted', 'failed', 'review');
EXCEPTION WHEN duplicate_object THEN null; END $$;

-- ── Tenant root ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orgs (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT        NOT NULL,
    gstin      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Clients (a CA's customers; single-business tenants have one self-client) ──
CREATE TABLE IF NOT EXISTS clients (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    gstin      TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Purchase register entries (from VLM extraction) ───────────
CREATE TABLE IF NOT EXISTS purchase_register_entries (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    client_id            UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period               TEXT        NOT NULL,       -- YYYY-MM
    supplier_gstin       TEXT        NOT NULL,
    supplier_name        TEXT,
    inv_no               TEXT        NOT NULL,
    inv_date             DATE        NOT NULL,
    taxable_value        NUMERIC(15, 2) NOT NULL,
    cgst                 NUMERIC(15, 2) NOT NULL DEFAULT 0,
    sgst                 NUMERIC(15, 2) NOT NULL DEFAULT 0,
    igst                 NUMERIC(15, 2) NOT NULL DEFAULT 0,
    -- Generated totals (never float arithmetic in app code)
    total_tax            NUMERIC(15, 2) GENERATED ALWAYS AS (cgst + sgst + igst) STORED,
    invoice_value        NUMERIC(15, 2) GENERATED ALWAYS AS (taxable_value + cgst + sgst + igst) STORED,
    is_rcm               BOOLEAN     NOT NULL DEFAULT false,
    doc_type             doc_type    NOT NULL DEFAULT 'invoice',
    source_image_hash    TEXT,
    extraction_id        UUID,
    -- Normalised forms used by the matching engine
    norm_inv_no          TEXT,
    norm_supplier_gstin  TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── GSTR-2B entries (from portal JSON/Excel upload) ──────────
CREATE TABLE IF NOT EXISTS gstr2b_entries (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    client_id            UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period               TEXT        NOT NULL,
    supplier_gstin       TEXT        NOT NULL,
    supplier_name        TEXT,
    inv_no               TEXT        NOT NULL,
    inv_date             DATE        NOT NULL,
    taxable_value        NUMERIC(15, 2) NOT NULL,
    cgst                 NUMERIC(15, 2) NOT NULL DEFAULT 0,
    sgst                 NUMERIC(15, 2) NOT NULL DEFAULT 0,
    igst                 NUMERIC(15, 2) NOT NULL DEFAULT 0,
    doc_type             doc_type    NOT NULL DEFAULT 'invoice',
    norm_inv_no          TEXT,
    norm_supplier_gstin  TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Raw VLM extraction records ────────────────────────────────
CREATE TABLE IF NOT EXISTS extractions (
    id               UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           UUID             NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    image_hash       TEXT             NOT NULL,  -- SHA-256; prevents re-extraction
    storage_path     TEXT             NOT NULL,
    raw_vlm_json     JSONB,
    status           extraction_status NOT NULL DEFAULT 'pending',
    confidence       NUMERIC(4, 3),
    validation_errors JSONB,
    created_at       TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ      NOT NULL DEFAULT now(),
    UNIQUE (org_id, image_hash)       -- content-hash dedup
);

-- ── Reconciliation results ────────────────────────────────────
CREATE TABLE IF NOT EXISTS match_results (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    client_id         UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period            TEXT        NOT NULL,
    pr_entry_id       UUID        REFERENCES purchase_register_entries(id),
    gstr2b_entry_id   UUID        REFERENCES gstr2b_entries(id),
    status            match_status NOT NULL,
    confidence        NUMERIC(4, 3),
    mismatched_fields JSONB,       -- array of field names that differ
    reviewed_by       UUID,        -- auth.uid() of the checker
    reviewed_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Every row must reference at least one side
    CONSTRAINT at_least_one_entry CHECK (
        pr_entry_id IS NOT NULL OR gstr2b_entry_id IS NOT NULL
    )
);

-- ── Vendor master (static seed in MVP; learning in phase 2) ───
CREATE TABLE IF NOT EXISTS vendor_master (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID    NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    normalized_name TEXT    NOT NULL,
    gstin           TEXT,
    pan             TEXT    GENERATED ALWAYS AS (
        CASE WHEN gstin IS NOT NULL AND length(gstin) = 15
             THEN substring(gstin, 3, 10)
             ELSE NULL
        END
    ) STORED,
    aliases         TEXT[]  DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, normalized_name)
);

-- ── Row-Level Security ────────────────────────────────────────
ALTER TABLE orgs                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchase_register_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE gstr2b_entries            ENABLE ROW LEVEL SECURITY;
ALTER TABLE extractions               ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_results             ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_master             ENABLE ROW LEVEL SECURITY;

-- Helper: extract org_id from the Supabase JWT app_metadata claim.
-- Service-role keys bypass RLS entirely (used by worker + migrations).
-- NOTE: This function requires superuser access to the auth schema.
-- If running via pooler connection, create it manually in the SQL Editor.
DO $$
BEGIN
    CREATE OR REPLACE FUNCTION auth.org_id() RETURNS UUID
    LANGUAGE sql STABLE AS $fn$
        SELECT (
            (current_setting('request.jwt.claims', true)::jsonb)
            -> 'app_metadata' ->> 'org_id'
        )::UUID;
    $fn$;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'Skipped auth.org_id() — run in SQL Editor with superuser access';
END $$;

-- One policy per table — all authenticated users see only their org.
-- These are no-ops if auth.org_id() was skipped above; service-role key bypasses them anyway.
DO $$
BEGIN
    CREATE POLICY org_isolation ON orgs
        FOR ALL TO authenticated USING (id = auth.org_id());
    CREATE POLICY org_isolation ON clients
        FOR ALL TO authenticated USING (org_id = auth.org_id());
    CREATE POLICY org_isolation ON purchase_register_entries
        FOR ALL TO authenticated USING (org_id = auth.org_id());
    CREATE POLICY org_isolation ON gstr2b_entries
        FOR ALL TO authenticated USING (org_id = auth.org_id());
    CREATE POLICY org_isolation ON extractions
        FOR ALL TO authenticated USING (org_id = auth.org_id());
    CREATE POLICY org_isolation ON match_results
        FOR ALL TO authenticated USING (org_id = auth.org_id());
    CREATE POLICY org_isolation ON vendor_master
        FOR ALL TO authenticated USING (org_id = auth.org_id());
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Skipped RLS policies — run in SQL Editor or policies already exist';
END $$;

-- ── Indexes ───────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_pr_lookup    ON purchase_register_entries (org_id, client_id, period, norm_supplier_gstin, norm_inv_no);
CREATE INDEX IF NOT EXISTS idx_2b_lookup    ON gstr2b_entries            (org_id, client_id, period, norm_supplier_gstin, norm_inv_no);
CREATE INDEX IF NOT EXISTS idx_ext_hash     ON extractions               (org_id, image_hash);
CREATE INDEX IF NOT EXISTS idx_match_period ON match_results             (org_id, client_id, period, status);

-- ── pgmq queue ────────────────────────────────────────────────
-- Only runs if pgmq extension is installed.
DO $$
BEGIN
    PERFORM pgmq.create('invoice_extraction');
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgmq queue creation skipped — enable pgmq extension first';
END $$;
