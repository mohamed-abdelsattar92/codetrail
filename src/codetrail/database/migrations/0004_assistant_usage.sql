-- What each assistant call used (design section 15.3): tokens, and its cost in dollars when known.
CREATE TABLE assistant_calls (
    id INTEGER PRIMARY KEY,
    called_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    cached_input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL
);

CREATE INDEX assistant_calls_by_kind ON assistant_calls (kind, provider, model, id);

-- The latest reading of each subscription usage window a provider reported, and when it was read.
CREATE TABLE plan_usage (
    provider TEXT NOT NULL,
    window TEXT NOT NULL,
    utilization REAL NOT NULL,
    resets_at INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (provider, window)
);
