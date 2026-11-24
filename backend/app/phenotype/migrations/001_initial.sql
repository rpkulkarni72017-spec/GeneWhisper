CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE phenotype_mentions (
    mention_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(mention_id)) > 0),
    case_id TEXT NOT NULL CHECK (length(trim(case_id)) > 0),
    record_json TEXT NOT NULL CHECK (length(record_json) > 0)
);

CREATE INDEX idx_phenotype_mentions_case
    ON phenotype_mentions(case_id, mention_id);

CREATE TABLE review_events (
    event_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(event_id)) > 0),
    mention_id TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL
        CHECK (
            length(reviewed_at_utc) = 32
            AND reviewed_at_utc GLOB
                '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]+00:00'
            AND CAST(substr(reviewed_at_utc, 1, 4) AS INTEGER) BETWEEN 1 AND 9999
            AND CAST(substr(reviewed_at_utc, 6, 2) AS INTEGER) BETWEEN 1 AND 12
            AND CAST(substr(reviewed_at_utc, 9, 2) AS INTEGER) BETWEEN 1 AND
                CASE CAST(substr(reviewed_at_utc, 6, 2) AS INTEGER)
                    WHEN 2 THEN
                        CASE
                            WHEN CAST(substr(reviewed_at_utc, 1, 4) AS INTEGER) % 4 = 0
                                 AND (
                                     CAST(substr(reviewed_at_utc, 1, 4) AS INTEGER) % 100 <> 0
                                     OR CAST(substr(reviewed_at_utc, 1, 4) AS INTEGER) % 400 = 0
                                 )
                            THEN 29
                            ELSE 28
                        END
                    WHEN 4 THEN 30
                    WHEN 6 THEN 30
                    WHEN 9 THEN 30
                    WHEN 11 THEN 30
                    ELSE 31
                END
            AND CAST(substr(reviewed_at_utc, 12, 2) AS INTEGER) BETWEEN 0 AND 23
            AND CAST(substr(reviewed_at_utc, 15, 2) AS INTEGER) BETWEEN 0 AND 59
            AND CAST(substr(reviewed_at_utc, 18, 2) AS INTEGER) BETWEEN 0 AND 59
        ),
    supersedes_event_id TEXT,
    record_json TEXT NOT NULL CHECK (length(record_json) > 0),
    UNIQUE (mention_id, event_id),
    FOREIGN KEY (mention_id)
        REFERENCES phenotype_mentions(mention_id) ON DELETE RESTRICT,
    FOREIGN KEY (mention_id, supersedes_event_id)
        REFERENCES review_events(mention_id, event_id) ON DELETE RESTRICT,
    CHECK (
        supersedes_event_id IS NULL
        OR supersedes_event_id <> event_id
    )
);

CREATE INDEX idx_review_events_mention_time
    ON review_events(mention_id, reviewed_at_utc);

CREATE UNIQUE INDEX idx_review_events_one_root
    ON review_events(mention_id)
    WHERE supersedes_event_id IS NULL;

CREATE UNIQUE INDEX idx_review_events_one_successor
    ON review_events(mention_id, supersedes_event_id)
    WHERE supersedes_event_id IS NOT NULL;

CREATE TABLE review_heads (
    mention_id TEXT PRIMARY KEY NOT NULL,
    latest_event_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY (mention_id)
        REFERENCES phenotype_mentions(mention_id) ON DELETE RESTRICT,
    FOREIGN KEY (mention_id, latest_event_id)
        REFERENCES review_events(mention_id, event_id) ON DELETE RESTRICT
);

CREATE TRIGGER phenotype_mentions_no_update
BEFORE UPDATE ON phenotype_mentions
BEGIN
    SELECT RAISE(ABORT, 'phenotype mentions are immutable');
END;

CREATE TRIGGER phenotype_mentions_no_delete
BEFORE DELETE ON phenotype_mentions
BEGIN
    SELECT RAISE(ABORT, 'phenotype mentions are immutable');
END;

CREATE TRIGGER review_events_no_update
BEFORE UPDATE ON review_events
BEGIN
    SELECT RAISE(ABORT, 'review events are append-only');
END;

CREATE TRIGGER review_events_no_delete
BEFORE DELETE ON review_events
BEGIN
    SELECT RAISE(ABORT, 'review events are append-only');
END;

CREATE TRIGGER review_events_validate_append
BEFORE INSERT ON review_events
BEGIN
    SELECT CASE
        WHEN NEW.supersedes_event_id IS NULL
             AND (
                 EXISTS (
                     SELECT 1
                     FROM review_events
                     WHERE mention_id = NEW.mention_id
                 )
                 OR EXISTS (
                     SELECT 1
                     FROM review_heads
                     WHERE mention_id = NEW.mention_id
                 )
             )
        THEN RAISE(ABORT, 'a mention may have only one root review event')
        WHEN NEW.supersedes_event_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1
                 FROM review_heads AS head
                 JOIN review_events AS predecessor
                   ON predecessor.mention_id = head.mention_id
                  AND predecessor.event_id = head.latest_event_id
                 WHERE head.mention_id = NEW.mention_id
                   AND head.latest_event_id = NEW.supersedes_event_id
                   AND predecessor.reviewed_at_utc < NEW.reviewed_at_utc
             )
        THEN RAISE(
            ABORT,
            'a review event must supersede the current same-mention head at a later timestamp'
        )
    END;
END;
