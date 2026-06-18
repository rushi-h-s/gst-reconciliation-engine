-- Runs once when the pgmq_db container is first created.
-- This DB is used ONLY for the pgmq queue transport.
-- Application tables (orgs, clients, purchase_register_entries, etc.)
-- live in Supabase cloud and are accessed via the REST client.

CREATE EXTENSION IF NOT EXISTS pgmq;

-- Create the extraction queue.
-- pgmq.create() is idempotent in newer versions; the DO block guards older ones.
DO $$
BEGIN
    PERFORM pgmq.create('invoice_extraction');
EXCEPTION WHEN others THEN
    -- Queue already exists — safe to ignore.
    NULL;
END;
$$;
