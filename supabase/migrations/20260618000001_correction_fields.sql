-- Add CORRECTED status and correction detail columns to match_results.
-- Run in Supabase SQL Editor (Dashboard → SQL Editor).

ALTER TYPE match_status ADD VALUE IF NOT EXISTS 'CORRECTED';

ALTER TABLE match_results
  ADD COLUMN IF NOT EXISTS corrected_amount  NUMERIC(15, 2),
  ADD COLUMN IF NOT EXISTS corrected_date    DATE,
  ADD COLUMN IF NOT EXISTS correction_reason TEXT,
  ADD COLUMN IF NOT EXISTS correction_notes  TEXT;
