CREATE TABLE phenotype_mention_order (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK (sequence > 0),
    mention_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(mention_id)) > 0),
    FOREIGN KEY (mention_id)
        REFERENCES phenotype_mentions(mention_id) ON DELETE RESTRICT
);

INSERT INTO phenotype_mention_order (mention_id)
SELECT mention_id
FROM phenotype_mentions
ORDER BY mention_id;

CREATE TRIGGER phenotype_mentions_assign_order
AFTER INSERT ON phenotype_mentions
BEGIN
    INSERT INTO phenotype_mention_order (mention_id)
    VALUES (NEW.mention_id);
END;

CREATE TRIGGER phenotype_mention_order_no_update
BEFORE UPDATE ON phenotype_mention_order
BEGIN
    SELECT RAISE(ABORT, 'phenotype mention ordering is immutable');
END;

CREATE TRIGGER phenotype_mention_order_no_delete
BEFORE DELETE ON phenotype_mention_order
BEGIN
    SELECT RAISE(ABORT, 'phenotype mention ordering is immutable');
END;
