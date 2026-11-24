"""Application services coordinating phenotype extraction and human review."""
#connect different phenotype operations

from typing import Optional, Tuple

from ..hpo.ontology import HpoOntology
from ..hpo.schemas import HPO_RELEASE_METADATA
from .extraction import ExtractionResult, build_mentions_from_results
from .mapping import map_hpo_candidates
from .repository import (
    InvalidReviewReferenceError,
    PhenotypeReviewRepository,
)
from .review import (
    ConfirmedPositivePhenotypeSet,
    build_confirmed_positive_set,
)
from .schemas import (
    CaseRecord,
    PhenotypeMention,
    ReviewEvent,
    SourceDocumentRevision,
)


class PhenotypeCaseNotFoundError(LookupError):
    """Raised when an operation requires a case that is not stored."""


class PhenotypeMentionNotFoundError(InvalidReviewReferenceError):
    """Raised when a review operation references an unknown mention."""


class PhenotypeReviewService:
    """Coordinate phenotype workflows over injected repository and ontology."""

    def __init__(
        self,
        repository: PhenotypeReviewRepository,
        ontology: HpoOntology,
    ) -> None:
        if not isinstance(ontology, HpoOntology):
            raise TypeError("ontology must be a loaded HpoOntology")
        if ontology.release_metadata != HPO_RELEASE_METADATA:
            raise ValueError("service requires the configured pinned HPO ontology")
        self._repository = repository
        self._ontology = ontology

    def create_case(self, case_id: str) -> CaseRecord:
        """Create and return an independently persisted case."""

        case = CaseRecord(case_id)
        self._repository.create_case(case)
        return case

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        """Return a stored case, or ``None`` when it is unknown."""

        return self._repository.get_case(case_id)

    def add_extracted_mention(
        self,
        document: SourceDocumentRevision,
        result: ExtractionResult,
        *,
        extractor_version: str,
    ) -> PhenotypeMention:
        """Validate, map, and persist one extracted mention.

        The repository atomically creates the case if this mention is its first
        record. Mapped HPO terms remain suggestions for human review.
        """

        mentions = build_mentions_from_results(
            document,
            (result,),
            extractor_version=extractor_version,
        )
        mapped_mention = map_hpo_candidates(mentions, self._ontology)[0]
        self._repository.add_mention(mapped_mention)
        return mapped_mention

    def get_pending_reviews(
        self,
        case_id: str,
    ) -> Tuple[PhenotypeMention, ...]:
        """Return pending mentions for a known case in repository order."""

        self._require_case(case_id)
        return self._repository.list_pending_mentions(case_id)

    def submit_review(
        self,
        event: ReviewEvent,
        *,
        expected_latest_event_id: Optional[str],
    ) -> ReviewEvent:
        """Append a human review after validating its mention and HPO evidence."""

        if not isinstance(event, ReviewEvent):
            raise TypeError("event must be a ReviewEvent")
        if event.hpo_version != self._ontology.release:
            raise ValueError("review HPO version does not match the pinned ontology")

        mention = self._repository.get_mention(event.mention_id)
        if mention is None:
            raise PhenotypeMentionNotFoundError(
                "review event references unknown mention: {}".format(event.mention_id)
            )
        if event.selected_hpo_id is not None:
            if not any(
                suggestion.canonical_hpo_id == event.selected_hpo_id
                and not suggestion.is_obsolete
                for suggestion in mention.suggestions
            ):
                raise ValueError(
                    "selected HPO identifier is not an active suggestion for the mention"
                )
            selected_term = self._ontology.get_term(event.selected_hpo_id)
            if selected_term is None or selected_term.is_obsolete:
                raise ValueError(
                    "selected HPO identifier is not active in the pinned ontology"
                )

        return self._repository.append_review_event(
            event,
            expected_latest_event_id=expected_latest_event_id,
        )

    def get_case_review_history(self, case_id: str) -> Tuple[ReviewEvent, ...]:
        """Return all review events for a known case."""

        self._require_case(case_id)
        return self._repository.get_case_review_history(case_id)

    def get_mention_review_history(
        self,
        mention_id: str,
    ) -> Tuple[ReviewEvent, ...]:
        """Return all review events for a known mention."""

        if self._repository.get_mention(mention_id) is None:
            raise PhenotypeMentionNotFoundError(
                "unknown phenotype mention: {}".format(mention_id)
            )
        return self._repository.get_mention_review_history(mention_id)

    def get_confirmed_positive_set(
        self,
        case_id: str,
    ) -> ConfirmedPositivePhenotypeSet:
        """Project one consistent case snapshot using existing review logic."""

        self._require_case(case_id)
        case_data = self._repository.get_case_data(case_id)
        return build_confirmed_positive_set(
            case_data.mentions,
            case_data.review_events,
            self._ontology,
        )

    def _require_case(self, case_id: str) -> CaseRecord:
        case = self._repository.get_case(case_id)
        if case is None:
            raise PhenotypeCaseNotFoundError(
                "unknown phenotype case: {}".format(case_id)
            )
        return case
