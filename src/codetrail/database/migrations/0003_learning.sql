-- Learning state (design section 8.4): marks apply to a page version; passes to a check's hash.
CREATE TABLE page_marks (
    page_id TEXT PRIMARY KEY,
    read_version TEXT,
    read_at TEXT,
    learned_version TEXT,
    learned_commit TEXT,
    learned_at TEXT
);

CREATE TABLE check_passes (
    page_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    check_hash TEXT NOT NULL,
    passed_at TEXT NOT NULL,
    PRIMARY KEY (page_id, check_id, check_hash)
);

CREATE TABLE check_attempts (
    id INTEGER PRIMARY KEY,
    page_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    check_hash TEXT NOT NULL,
    answer TEXT NOT NULL,
    verdict TEXT NOT NULL,
    feedback TEXT NOT NULL,
    language TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);

CREATE TABLE digest_reads (
    digest_id TEXT PRIMARY KEY,
    read_at TEXT NOT NULL
);
