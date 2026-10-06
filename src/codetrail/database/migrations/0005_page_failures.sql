-- Pages whose drafts failed validation twice (design section 6.5), with the hash of their scope at the time: an
-- update skips such a page until its scope changes, or until the reader asks for a retry.
CREATE TABLE page_failures (
    page_id TEXT PRIMARY KEY,
    scope_hash TEXT NOT NULL,
    failed_at TEXT NOT NULL
);
