-- Adds the FAAB bid to sleeper_transactions (added 10/21/26). Sleeper puts the
-- bid in each transaction's settings.waiver_bid (waiver claims only; NULL for
-- free-agent adds, drops and trades). Until now it was never stored.
-- Additive and idempotent: existing rows get NULL until backfill_waiver_bids.py
-- (or the daily sync, for recent weeks) fills them. No view or script that lists
-- columns explicitly is affected.
ALTER TABLE sleeper_transactions ADD COLUMN IF NOT EXISTS waiver_bid integer;
