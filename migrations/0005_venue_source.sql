-- Migration 0005: where each meet's venue came from (2026-10-18).
--
-- R1.11 requires every added value to record its source. Migration 0004's
-- weather rows do (raw_sha256, raw_s3_key), and a meet's date and race start
-- times come from those same feeds, but meets.venue comes from the RunSignup
-- race record, which the loader now archives under raw/runsignup-race/.
-- This records which one, e.g.
--   'runsignup race 154767: raw/runsignup-race/154767-3bf051c4add8f3e8.json'.
ALTER TABLE meets ADD COLUMN venue_source TEXT;
