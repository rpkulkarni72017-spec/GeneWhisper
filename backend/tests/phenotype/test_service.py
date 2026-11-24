"""Focused tests for the phenotype application service."""

import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from app.hpo.ontology import HpoOntology
from app.hpo.schemas import HPO_RELEASE_METADATA, HpoTerm, MatchType
from app.phenotype.extraction import ExtractionResult
from app.phenotype.repository import (
    DuplicateIdentifierError,
    InMemoryPhenotypeReviewRepository,
    RepositoryStorageError,
    StaleReviewSubmissionError,
)
from app.phenotype.schemas import (
    AssertionStatus,
    Experiencer,
    HpoSuggestion,
    ReviewDecision,
    ReviewEvent,
    SourceDocumentRevision,
    Temporality,
)
from app.phenotype.service import (
    PhenotypeCaseNotFoundError,
    PhenotypeMentionNotFoundError,
    PhenotypeReviewService,
)


HPO_ID = "HP:9999999"
AMBIGUOUS_HPO_ID = "HP:9999998"


class _SyntheticPinnedOntology(HpoOntology):
    def __init__(self):
        super().__init__(
            {
                HPO_ID: HpoTerm(
                    hpo_id=HPO_ID,
                    name="Synthetic phenotype finding",
                )
            }
        )
        self._release_metadata = HPO_RELEASE_METADATA


class _AmbiguousSyntheticOntology(HpoOntology):
    def __init__(self):
        super().__init__(
            {
                HPO_ID: HpoTerm(
                    hpo_id=HPO_ID,
                    name="Synthetic phenotype finding",
                ),
                AMBIGUOUS_HPO_ID: HpoTerm(
                    hpo_id=AMBIGUOUS_HPO_ID,
                    name="Synthetic phenotype finding",
                ),
            }
        )
        self._release_metadata = HPO_RELEASE_METADATA


class PhenotypeReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryPhenotypeReviewRepository()
        self.ontology = _SyntheticPinnedOntology()
        self.service = PhenotypeReviewService(self.repository, self.ontology)
        text = "synthetic phenotype finding"
        self.document = SourceDocumentRevision(
            case_id="synthetic-case",
            document_ref="synthetic-document",
            revision="revision-1",
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
        )
        self.result = ExtractionResult(
            mention_id="synthetic-mention",
            start_offset=0,
            end_offset=len(text),
            phenotype_text=text,
            assertion=AssertionStatus.POSITIVE,
            temporality=Temporality.CURRENT,
            experiencer=Experiencer.PATIENT,
        )

    def add_mention_with_attributes(
        self,
        mention_id,
        *,
        case_id,
        assertion=AssertionStatus.POSITIVE,
        temporality=Temporality.CURRENT,
        experiencer=Experiencer.PATIENT,
        family_relationship=None,
        service=None,
    ):
        document = replace(
            self.document,
            case_id=case_id,
            document_ref="synthetic-document-{}".format(case_id),
        )
        result = replace(
            self.result,
            mention_id=mention_id,
            assertion=assertion,
            temporality=temporality,
            experiencer=experiencer,
            family_relationship=family_relationship,
        )
        review_service = self.service if service is None else service
        return review_service.add_extracted_mention(
            document,
            result,
            extractor_version="synthetic-extractor-1",
        )

    def make_event(
        self,
        event_id,
        mention_id="synthetic-mention",
        *,
        decision=ReviewDecision.DEFERRED,
        second=0,
        selected_hpo_id=None,
        supersedes_event_id=None,
    ):
        return ReviewEvent(
            event_id=event_id,
            mention_id=mention_id,
            decision=decision,
            reviewer_id="synthetic-reviewer",
            reviewed_at=datetime(
                2026, 11, 23, 12, 0, second, tzinfo=timezone.utc
            ),
            hpo_version=HPO_RELEASE_METADATA.release,
            selected_hpo_id=selected_hpo_id,
            supersedes_event_id=supersedes_event_id,
        )

    def test_pinned_ontology_is_required(self):
        with self.assertRaises(ValueError):
            PhenotypeReviewService(
                self.repository,
                HpoOntology({HPO_ID: HpoTerm(HPO_ID, "Synthetic phenotype finding")}),
            )

    def test_create_and_get_case_preserve_duplicate_behavior(self):
        case = self.service.create_case("empty-case")
        self.assertEqual(self.service.get_case(case.case_id), case)
        self.assertIsNone(self.service.get_case("unknown-case"))
        with self.assertRaises(DuplicateIdentifierError):
            self.service.create_case(case.case_id)

    def test_add_extracted_mention_validates_maps_and_persists_candidate(self):
        mention = self.service.add_extracted_mention(
            self.document,
            self.result,
            extractor_version="synthetic-extractor-1",
        )

        self.assertEqual(mention.case_id, self.document.case_id)
        self.assertEqual(mention.source_document_revision, self.document.revision)
        self.assertEqual(len(mention.suggestions), 1)
        self.assertEqual(mention.suggestions[0].canonical_hpo_id, HPO_ID)
        self.assertTrue(mention.suggestions[0].human_review_required)
        self.assertEqual(self.repository.get_mention(mention.mention_id), mention)
        self.assertIsNotNone(self.service.get_case(self.document.case_id))

    def test_invalid_source_evidence_is_rejected_before_persistence(self):
        invalid_document = SourceDocumentRevision(
            case_id=self.document.case_id,
            document_ref=self.document.document_ref,
            revision=self.document.revision,
            sha256="0" * 64,
            text=self.document.text,
        )

        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.service.add_extracted_mention(
                invalid_document,
                self.result,
                extractor_version="synthetic-extractor-1",
            )
        self.assertIsNone(self.repository.get_mention(self.result.mention_id))

    def test_invalid_extraction_span_is_rejected_before_case_creation(self):
        invalid_result = replace(
            self.result,
            mention_id="synthetic-invalid-span",
            end_offset=len(self.document.text) + 1,
        )

        with self.assertRaisesRegex(ValueError, "span within the document"):
            self.service.add_extracted_mention(
                self.document,
                invalid_result,
                extractor_version="synthetic-extractor-1",
            )

        self.assertFalse(self.repository.case_exists(self.document.case_id))
        self.assertIsNone(self.repository.get_mention(invalid_result.mention_id))

    def test_pending_reviews_and_history_require_existing_records(self):
        mention = self.service.add_extracted_mention(
            self.document,
            self.result,
            extractor_version="synthetic-extractor-1",
        )
        self.assertEqual(self.service.get_pending_reviews(mention.case_id), (mention,))

        event = self.make_event("review-1")
        self.service.submit_review(event, expected_latest_event_id=None)
        successor = self.make_event(
            "review-2",
            decision=ReviewDecision.DEFERRED,
            second=1,
            supersedes_event_id=event.event_id,
        )
        self.service.submit_review(
            successor,
            expected_latest_event_id=event.event_id,
        )

        self.assertEqual(self.service.get_pending_reviews(mention.case_id), ())
        self.assertEqual(
            self.service.get_case_review_history(mention.case_id),
            (event, successor),
        )
        self.assertEqual(
            self.service.get_mention_review_history(mention.mention_id),
            (event, successor),
        )
        with self.assertRaises(PhenotypeCaseNotFoundError):
            self.service.get_pending_reviews("unknown-case")
        with self.assertRaises(PhenotypeCaseNotFoundError):
            self.service.get_case_review_history("unknown-case")
        with self.assertRaises(PhenotypeMentionNotFoundError):
            self.service.get_mention_review_history("unknown-mention")

    def test_submit_review_preserves_stale_and_reference_validation(self):
        mention = self.service.add_extracted_mention(
            self.document,
            self.result,
            extractor_version="synthetic-extractor-1",
        )
        with self.assertRaises(PhenotypeMentionNotFoundError):
            self.service.submit_review(
                self.make_event("unknown-review", "unknown-mention"),
                expected_latest_event_id=None,
            )
        with self.assertRaises(ValueError):
            self.service.submit_review(
                self.make_event(
                    "invalid-candidate",
                    decision=ReviewDecision.ACCEPTED_POSITIVE,
                    selected_hpo_id="HP:9999998",
                ),
                expected_latest_event_id=None,
            )

        root = self.make_event("review-root")
        self.service.submit_review(root, expected_latest_event_id=None)
        with self.assertRaises(StaleReviewSubmissionError):
            self.service.submit_review(
                self.make_event(
                    "review-stale",
                    decision=ReviewDecision.DEFERRED,
                    second=1,
                    supersedes_event_id=root.event_id,
                ),
                expected_latest_event_id=None,
            )
        self.assertEqual(
            self.service.get_mention_review_history(mention.mention_id),
            (root,),
        )

    def test_submit_review_rejects_suggestion_missing_from_pinned_ontology(self):
        mention = self.service.add_extracted_mention(
            self.document,
            self.result,
            extractor_version="synthetic-extractor-1",
        )
        stale_suggestion = HpoSuggestion(
            canonical_hpo_id="HP:9999998",
            matched_hpo_id="HP:9999998",
            term_name="Synthetic stale candidate",
            match_type=MatchType.TERM_ID,
            explanation="Synthetic stale candidate for validation.",
            is_obsolete=False,
        )
        stale_mention = replace(
            mention,
            mention_id="synthetic-stale-mention",
            suggestions=(stale_suggestion,),
        )
        self.repository.add_mention(stale_mention)

        with self.assertRaisesRegex(ValueError, "pinned ontology"):
            self.service.submit_review(
                self.make_event(
                    "stale-candidate-review",
                    stale_mention.mention_id,
                    decision=ReviewDecision.ACCEPTED_POSITIVE,
                    selected_hpo_id=stale_suggestion.canonical_hpo_id,
                ),
                expected_latest_event_id=None,
            )
        self.assertEqual(
            self.repository.get_mention_review_history(stale_mention.mention_id),
            (),
        )

    def test_projection_excludes_negated_uncertain_and_nonpatient_mentions(self):
        scenarios = (
            (
                "negated-positive-review",
                AssertionStatus.NEGATED,
                Experiencer.PATIENT,
                None,
                ReviewDecision.ACCEPTED_POSITIVE,
            ),
            (
                "confirmed-negation",
                AssertionStatus.NEGATED,
                Experiencer.PATIENT,
                None,
                ReviewDecision.CONFIRMED_NEGATED,
            ),
            (
                "uncertain-finding",
                AssertionStatus.UNCERTAIN,
                Experiencer.PATIENT,
                None,
                ReviewDecision.ACCEPTED_POSITIVE,
            ),
            (
                "family-member-finding",
                AssertionStatus.POSITIVE,
                Experiencer.FAMILY_MEMBER,
                "mother",
                ReviewDecision.ACCEPTED_POSITIVE,
            ),
        )

        for index, (case_id, assertion, experiencer, relationship, decision) in enumerate(
            scenarios
        ):
            with self.subTest(case_id=case_id):
                mention = self.add_mention_with_attributes(
                    "mention-{}".format(case_id),
                    case_id=case_id,
                    assertion=assertion,
                    experiencer=experiencer,
                    family_relationship=relationship,
                )
                event = self.make_event(
                    "review-{}".format(case_id),
                    mention_id=mention.mention_id,
                    decision=decision,
                    second=index,
                    selected_hpo_id=HPO_ID,
                )
                self.service.submit_review(event, expected_latest_event_id=None)

                projection = self.service.get_confirmed_positive_set(case_id)

                self.assertEqual(projection.phenotypes, ())
                self.assertEqual(projection.conflicts, ())

    def test_historical_positive_review_preserves_historical_temporality(self):
        mention = self.add_mention_with_attributes(
            "historical-mention",
            case_id="historical-case",
            temporality=Temporality.HISTORICAL,
        )
        event = self.make_event(
            "historical-review",
            mention_id=mention.mention_id,
            decision=ReviewDecision.ACCEPTED_POSITIVE,
            selected_hpo_id=HPO_ID,
        )
        self.service.submit_review(event, expected_latest_event_id=None)

        projection = self.service.get_confirmed_positive_set(mention.case_id)

        self.assertEqual(len(projection.phenotypes), 1)
        evidence = projection.phenotypes[0].evidence[0]
        self.assertEqual(evidence.temporality, Temporality.HISTORICAL)

    def test_rejected_suggestion_does_not_enter_positive_projection(self):
        mention = self.add_mention_with_attributes(
            "rejected-mention",
            case_id="rejected-case",
        )
        event = self.make_event(
            "rejected-review",
            mention_id=mention.mention_id,
            decision=ReviewDecision.REJECTED,
            selected_hpo_id=HPO_ID,
        )
        self.service.submit_review(event, expected_latest_event_id=None)

        self.assertEqual(
            self.service.get_confirmed_positive_set(mention.case_id).phenotypes,
            (),
        )
        self.assertEqual(
            self.service.get_mention_review_history(mention.mention_id),
            (event,),
        )

    def test_ambiguous_mapping_requires_explicit_human_selection(self):
        ontology = _AmbiguousSyntheticOntology()
        service = PhenotypeReviewService(self.repository, ontology)
        mention = self.add_mention_with_attributes(
            "ambiguous-mention",
            case_id="ambiguous-case",
            service=service,
        )

        self.assertTrue(mention.hpo_mapping_ambiguous)
        self.assertEqual(
            {
                suggestion.canonical_hpo_id
                for suggestion in mention.suggestions
            },
            {HPO_ID, AMBIGUOUS_HPO_ID},
        )
        self.assertTrue(
            all(suggestion.human_review_required for suggestion in mention.suggestions)
        )
        self.assertEqual(service.get_confirmed_positive_set(mention.case_id).phenotypes, ())

        selected_id = AMBIGUOUS_HPO_ID
        event = self.make_event(
            "ambiguous-review",
            mention_id=mention.mention_id,
            decision=ReviewDecision.ACCEPTED_POSITIVE,
            selected_hpo_id=selected_id,
        )
        service.submit_review(event, expected_latest_event_id=None)

        projection = service.get_confirmed_positive_set(mention.case_id)
        self.assertEqual(len(projection.phenotypes), 1)
        self.assertEqual(projection.phenotypes[0].hpo_id, selected_id)

    def test_confirmed_positive_projection_uses_review_logic(self):
        mention = self.service.add_extracted_mention(
            self.document,
            self.result,
            extractor_version="synthetic-extractor-1",
        )
        self.service.submit_review(
            self.make_event(
                "accepted-review",
                decision=ReviewDecision.ACCEPTED_POSITIVE,
                selected_hpo_id=HPO_ID,
            ),
            expected_latest_event_id=None,
        )

        projection = self.service.get_confirmed_positive_set(mention.case_id)

        self.assertEqual(len(projection.phenotypes), 1)
        self.assertEqual(projection.phenotypes[0].hpo_id, HPO_ID)
        self.assertEqual(projection.phenotypes[0].evidence[0].mention_id, mention.mention_id)
        self.assertEqual(projection.conflicts, ())
        with self.assertRaises(PhenotypeCaseNotFoundError):
            self.service.get_confirmed_positive_set("unknown-case")

    def test_repository_storage_errors_are_not_rewrapped(self):
        service_error = RepositoryStorageError("synthetic storage failure")

        class FailingRepository(InMemoryPhenotypeReviewRepository):
            def get_case(self, case_id):
                raise service_error

        failing_service = PhenotypeReviewService(FailingRepository(), self.ontology)
        with self.assertRaises(RepositoryStorageError) as raised:
            failing_service.get_case("case")
        self.assertIs(raised.exception, service_error)

    def test_duplicate_mention_and_event_ids_are_not_overwritten(self):
        mention = self.service.add_extracted_mention(
            self.document,
            self.result,
            extractor_version="synthetic-extractor-1",
        )
        with self.assertRaises(DuplicateIdentifierError):
            self.service.add_extracted_mention(
                self.document,
                self.result,
                extractor_version="synthetic-extractor-1",
            )
        self.assertEqual(self.repository.get_mention(mention.mention_id), mention)

        event = self.make_event("duplicate-event")
        self.service.submit_review(event, expected_latest_event_id=None)
        with self.assertRaises(DuplicateIdentifierError):
            self.service.submit_review(
                event,
                expected_latest_event_id=event.event_id,
            )
        self.assertEqual(
            self.service.get_mention_review_history(mention.mention_id),
            (event,),
        )


if __name__ == "__main__":
    unittest.main()
