-- The fact store (design section 4.2; ADR 0003). Each fact version is one row, valid from first_seen to last_seen
-- (inclusive); last_seen is NULL while the version is current.
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    taken_at TEXT NOT NULL
);

CREATE TABLE entities (
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    attributes TEXT NOT NULL,
    hash TEXT NOT NULL,
    sources TEXT NOT NULL,
    first_seen INTEGER NOT NULL REFERENCES snapshots (id),
    last_seen INTEGER REFERENCES snapshots (id),
    PRIMARY KEY (id, first_seen)
);
CREATE UNIQUE INDEX entities_current ON entities (id) WHERE last_seen IS NULL;
CREATE INDEX entities_kind ON entities (kind) WHERE last_seen IS NULL;

CREATE TABLE relations (
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    attributes TEXT NOT NULL,
    hash TEXT NOT NULL,
    sources TEXT NOT NULL,
    first_seen INTEGER NOT NULL REFERENCES snapshots (id),
    last_seen INTEGER REFERENCES snapshots (id),
    PRIMARY KEY (source_id, kind, target_id, first_seen)
);
CREATE UNIQUE INDEX relations_current ON relations (source_id, kind, target_id) WHERE last_seen IS NULL;
CREATE INDEX relations_target ON relations (target_id) WHERE last_seen IS NULL;
