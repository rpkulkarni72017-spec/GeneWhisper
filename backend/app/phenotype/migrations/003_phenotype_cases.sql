CREATE TABLE phenotype_cases (
    case_id TEXT PRIMARY KEY NOT NULL
        CHECK (length(trim(case_id)) > 0)
);

INSERT INTO phenotype_cases (case_id)
SELECT DISTINCT case_id
FROM phenotype_mentions
ORDER BY case_id;

CREATE TRIGGER phenotype_cases_no_update
BEFORE UPDATE ON phenotype_cases
BEGIN
    SELECT RAISE(ABORT, 'phenotype cases are immutable');
END;

CREATE TRIGGER phenotype_cases_no_delete
BEFORE DELETE ON phenotype_cases
BEGIN
    SELECT RAISE(ABORT, 'phenotype cases are append-only');
END;

CREATE TRIGGER phenotype_mentions_require_case
BEFORE INSERT ON phenotype_mentions
WHEN NOT EXISTS (
    SELECT 1
    FROM phenotype_cases
    WHERE case_id = NEW.case_id
)
BEGIN
    SELECT RAISE(ABORT, 'phenotype mention references an unknown case');
END;
