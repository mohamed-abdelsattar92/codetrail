-- The digests each catch-up wrote (design section 25): a JSON list of [target, digest id] pairs, in the order
-- written. NULL means not recorded: the catch-up ran before this column existed.
ALTER TABLE catch_ups ADD COLUMN digests TEXT;
