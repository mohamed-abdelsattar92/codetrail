-- A team's catch-ups (design section 24): each run's targets and its overview, and which overviews were read.
-- A target's database gets the tables too, since a team's database shares the schema.
CREATE TABLE catch_ups (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    targets TEXT NOT NULL,
    overview_id TEXT,
    overview_problem TEXT
);

CREATE TABLE overview_reads (
    overview_id TEXT PRIMARY KEY,
    read_at TEXT NOT NULL
);
