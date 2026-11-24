"""Synthetic Exomiser Phenopacket projection tests."""

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.phenotype.schemas import (
    AssertionStatus,
    Experiencer,
    PhenotypeMention,
    ReviewDecision,
    ReviewEvent,
    Temporality,
)
from app.exomiser.phenopacket import project_exomiser_phenopacket


class ExomiserPhenopacketTests(unittest.TestCase):
    def test_only_confirmed_positive_is_projected(self):
        positive = PhenotypeMention(
            mention_id="positive",
            case_id="synthetic-case",
            source_document_ref="document",
            original_text="finding",
            start_offset=0,
            end_offset=7,
            phenotype_text="finding",
            assertion=AssertionStatus.POSITIVE,
            temporality=Temporality.HISTORICAL,
            experiencer=Experiencer.PATIENT,
            hpo_version="2026-09-01",
        )
        negated = PhenotypeMention(
            mention_id="negated",
            case_id="synthetic-case",
            source_document_ref="document",
            original_text="no finding",
            start_offset=0,
            end_offset=10,
            phenotype_text="no finding",
            assertion=AssertionStatus.NEGATED,
            temporality=Temporality.CURRENT,
            experiencer=Experiencer.PATIENT,
            hpo_version="2026-09-01",
        )
        event = ReviewEvent(
            event_id="positive-review",
            mention_id="positive",
            decision=ReviewDecision.ACCEPTED_POSITIVE,
            reviewer_id="synthetic-reviewer",
            reviewed_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
            hpo_version="2026-09-01",
            selected_hpo_id="HP:0000118",
        )
        negated_event = ReviewEvent(
            event_id="negated-review",
            mention_id="negated",
            decision=ReviewDecision.CONFIRMED_NEGATED,
            reviewer_id="synthetic-reviewer",
            reviewed_at=datetime(2026, 11, 23, 0, 0, 1, tzinfo=timezone.utc),
            hpo_version="2026-09-01",
            selected_hpo_id="HP:0000118",
        )
        projection = SimpleNamespace(
            phenotypes=(SimpleNamespace(case_id="synthetic-case", hpo_id="HP:0000118"),)
        )
        snapshot = project_exomiser_phenopacket(
            case_id="synthetic-case",
            phenotype_snapshot_id="snapshot-1",
            subject_id="synthetic-subject",
            hts_uri="synthetic.vcf",
            phenopacket_assembly="hg19",
            hpo_release="2026-09-01",
            mentions=(positive, negated),
            review_events=(event, negated_event),
            confirmed_positive_set=projection,
        )
        document = json.loads(snapshot.phenopacket_yaml)
        self.assertEqual(snapshot.positive_hpo_ids, ("HP:0000118",))
        self.assertEqual(document["subject"]["id"], "synthetic-subject")
        self.assertEqual(document["phenotypicFeatures"][0]["negated"], False)
        exclusions = {
            item.mention_id: item.exclusion_reason
            for item in snapshot.provenance
        }
        self.assertIsNone(exclusions["positive"])
        self.assertEqual(exclusions["negated"], "confirmed_negated")


if __name__ == "__main__":
    unittest.main()
