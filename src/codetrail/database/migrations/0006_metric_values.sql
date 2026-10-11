-- Each documentation metric's value at each update (design section 18.3): its numerator and denominator, the
-- denominator empty for a count, so a small share can be shown as "3 of 4".
CREATE TABLE metric_values (
    snapshot INTEGER NOT NULL REFERENCES snapshots(id),
    metric TEXT NOT NULL,
    numerator INTEGER NOT NULL,
    denominator INTEGER,
    PRIMARY KEY (snapshot, metric)
);
