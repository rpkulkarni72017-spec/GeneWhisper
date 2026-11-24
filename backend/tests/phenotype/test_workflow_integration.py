"""End-to-end phenotype workflow tests using the configured pinned HPO release."""

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.hpo.ontology import load_pinned_ontology
from app.hpo.schemas import HPO_RELEASE_METADATA
from app.phenotype.extraction import ExtractionResult
from app.phenotype.repository import InMemoryPhenotypeReviewRepository
from app.phenotype.schemas import (
    AssertionStatus,
    CaseRecord,
    Experiencer,
    ReviewDecision,
    ReviewEvent,
    SourceDocumentRevision,
    Temporality,
)
from app.phenotype.service import PhenotypeReviewService
from app.phenotype.sqlite_migrations import apply_migrations
from app.phenotype.sqlite_repository import SQLitePhenotypeReviewRepository


PROJECT_ROOT = Path(r"D:\engineering\major\GeneWhisper")
HPO_OBO_PATH = PROJECT_ROOT / "data" / "raw" / "hpo" / "hp.obo"
MAX_DATABASE_BYTES = 4 * 1024 * 1024
MAX_SIDECAR_BYTES = 2 * 1024 * 1024
MAX_MIGRATION_BYTES = 2 * 1024 * 1024
HPO_TERM_ID = "HP:0000118"
HPO_TERM_NAME = "Phenotypic abnormality"


class PhenotypeWorkflowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ontology = load_pinned_ontology(
            HPO_OBO_PATH,
            metadata=HPO_RELEASE_METADATA,
        )

    def run_workflow(self, repository, case_id):
        service = PhenotypeReviewService(repository, self.ontology)
        case = service.create_case(case_id)
        self.assertEqual(service.get_case(case_id), case)

        text = HPO_TERM_NAME
        document = SourceDocumentRevision(
            case_id=case_id,
            document_ref="synthetic-document-{}".format(case_id),
            revision="synthetic-revision-1",
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
        )
        result = ExtractionResult(
            mention_id="synthetic-mention-{}".format(case_id),
            start_offset=0,
            end_offset=len(text),
            phenotype_text=text,
            assertion=AssertionStatus.POSITIVE,
            temporality=Temporality.CURRENT,
            experiencer=Experiencer.PATIENT,
        )
        mention = service.add_extracted_mention(
            document,
            result,
            extractor_version="synthetic-extractor-1",
        )

        self.assertEqual(mention.case_id, case_id)
        self.assertEqual(mention.source_document_ref, document.document_ref)
        self.assertEqual(mention.source_document_revision, document.revision)
        self.assertEqual(
            (mention.start_offset, mention.end_offset, mention.original_text),
            (0, len(text), text),
        )
        self.assertTrue(
            any(
                suggestion.canonical_hpo_id == HPO_TERM_ID
                and not suggestion.is_obsolete
                and suggestion.human_review_required
                for suggestion in mention.suggestions
            )
        )
        self.assertEqual(service.get_pending_reviews(case_id), (mention,))
        self.assertEqual(
            service.get_confirmed_positive_set(case_id).phenotypes,
            (),
        )

        root_event = ReviewEvent(
            event_id="synthetic-review-root-{}".format(case_id),
            mention_id=mention.mention_id,
            decision=ReviewDecision.DEFERRED,
            reviewer_id="synthetic-human-reviewer",
            reviewed_at=datetime(2026, 11, 23, 11, 59, tzinfo=timezone.utc),
            hpo_version=HPO_RELEASE_METADATA.release,
        )
        self.assertEqual(
            service.submit_review(
                root_event,
                expected_latest_event_id=None,
            ),
            root_event,
        )
        event = ReviewEvent(
            event_id="synthetic-review-accepted-{}".format(case_id),
            mention_id=mention.mention_id,
            decision=ReviewDecision.ACCEPTED_POSITIVE,
            reviewer_id="synthetic-human-reviewer",
            reviewed_at=datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc),
            hpo_version=HPO_RELEASE_METADATA.release,
            selected_hpo_id=HPO_TERM_ID,
            supersedes_event_id=root_event.event_id,
        )
        self.assertEqual(
            service.submit_review(
                event,
                expected_latest_event_id=root_event.event_id,
            ),
            event,
        )
        self.assertEqual(service.get_pending_reviews(case_id), ())
        return service, mention, (root_event, event)

    def assert_projected_workflow(self, service, mention, review_events):
        self.assertEqual(
            service.get_case_review_history(mention.case_id),
            review_events,
        )
        self.assertEqual(
            service.get_mention_review_history(mention.mention_id),
            review_events,
        )

        projection = service.get_confirmed_positive_set(mention.case_id)
        self.assertEqual(len(projection.phenotypes), 1)
        self.assertEqual(projection.phenotypes[0].case_id, mention.case_id)
        self.assertEqual(projection.phenotypes[0].hpo_id, HPO_TERM_ID)
        evidence = projection.phenotypes[0].evidence[0]
        self.assertEqual(evidence.mention_id, mention.mention_id)
        self.assertEqual(evidence.case_id, mention.case_id)
        self.assertEqual(evidence.source_document_ref, mention.source_document_ref)
        self.assertEqual(evidence.original_text, mention.original_text)
        self.assertEqual(
            (evidence.start_offset, evidence.end_offset),
            (mention.start_offset, mention.end_offset),
        )
        self.assertEqual(evidence.assertion, mention.assertion)
        self.assertEqual(evidence.temporality, mention.temporality)
        self.assertEqual(evidence.experiencer, mention.experiencer)
        self.assertEqual(evidence.review_event_id, review_events[-1].event_id)
        self.assertEqual(projection.conflicts, ())

    def test_end_to_end_workflow_with_in_memory_repository(self):
        repository = InMemoryPhenotypeReviewRepository()
        service, mention, review_events = self.run_workflow(
            repository,
            "synthetic-memory-case",
        )
        other_service, other_mention, other_events = self.run_workflow(
            repository,
            "synthetic-memory-other-case",
        )

        self.assert_projected_workflow(service, mention, review_events)
        self.assert_projected_workflow(
            other_service,
            other_mention,
            other_events,
        )
        self.assertEqual(repository.list_case_mentions(mention.case_id), (mention,))
        self.assertEqual(
            repository.get_case_data(mention.case_id).review_events,
            review_events,
        )
        self.assertEqual(
            repository.list_case_mentions(other_mention.case_id),
            (other_mention,),
        )
        self.assertEqual(
            repository.get_case_data(other_mention.case_id).review_events,
            other_events,
        )

    def test_end_to_end_workflow_survives_sqlite_repository_reopen(self):
        with tempfile.TemporaryDirectory(
            prefix="stage4n9-",
            dir=str(PROJECT_ROOT),
        ) as temporary_directory:
            database_directory = Path(temporary_directory)
            apply_migrations(
                database_directory,
                max_database_bytes=MAX_DATABASE_BYTES,
                max_sidecar_bytes=MAX_SIDECAR_BYTES,
                max_migration_bytes=MAX_MIGRATION_BYTES,
            )

            repository = SQLitePhenotypeReviewRepository(
                database_directory,
                max_database_bytes=MAX_DATABASE_BYTES,
                max_sidecar_bytes=MAX_SIDECAR_BYTES,
            )
            service, mention, review_events = self.run_workflow(
                repository,
                "synthetic-sqlite-case",
            )
            other_service, other_mention, other_events = self.run_workflow(
                repository,
                "synthetic-sqlite-other-case",
            )
            del service, other_service, repository

            reopened_repository = SQLitePhenotypeReviewRepository(
                database_directory,
                max_database_bytes=MAX_DATABASE_BYTES,
                max_sidecar_bytes=MAX_SIDECAR_BYTES,
            )
            reopened_service = PhenotypeReviewService(
                reopened_repository,
                self.ontology,
            )
            reopened_other_service = PhenotypeReviewService(
                reopened_repository,
                self.ontology,
            )
            self.assertEqual(
                reopened_service.get_case(mention.case_id),
                CaseRecord(mention.case_id),
            )
            self.assertEqual(
                reopened_other_service.get_case(other_mention.case_id),
                CaseRecord(other_mention.case_id),
            )
            self.assertEqual(
                reopened_repository.get_mention(mention.mention_id),
                mention,
            )
            self.assertEqual(
                reopened_repository.get_mention(other_mention.mention_id),
                other_mention,
            )
            self.assertEqual(
                reopened_repository.list_case_mentions(mention.case_id),
                (mention,),
            )
            self.assertEqual(
                reopened_repository.list_case_mentions(other_mention.case_id),
                (other_mention,),
            )
            self.assert_projected_workflow(
                reopened_service,
                mention,
                review_events,
            )
            self.assert_projected_workflow(
                reopened_other_service,
                other_mention,
                other_events,
            )


if __name__ == "__main__":
    unittest.main()
