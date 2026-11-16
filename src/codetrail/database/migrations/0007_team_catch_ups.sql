-- A team's catch-ups (design section 24): each run's ticked targets, why any failed (a JSON object of target to
-- reason), the estimate's lines left unticked (a JSON list), and its overview; and which overviews were read.
-- A target's database gets the tables too, since a team's database shares the schema.
CREATE TABLE catch_ups (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    targets TEXT NOT NULL,
    failed TEXT NOT NULL DEFAULT '{}',
    unticked TEXT NOT NULL DEFAULT '[]',
    overview_id TEXT,
    overview_problem TEXT
);

CREATE TABLE overview_reads (
    overview_id TEXT PRIMARY KEY,
    read_at TEXT NOT NULL
);
