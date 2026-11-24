"""Typed data structures for phenotype evidence and reviewer decisions."""
#module uses cosistent data over all files
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

from ..hpo.schemas import MatchType

_HPO_ID_PATTERN = re.compile(r"^HP:\d+$")


class AssertionStatus(str, Enum):
    """Whether a finding is affirmed, negated, or uncertain."""

    POSITIVE = "positive"
    NEGATED = "negated"
    UNCERTAIN = "uncertain"


class Temporality(str, Enum):
    """When the finding applies to the patient or family member."""

    CURRENT = "current"
    HISTORICAL = "historical"


class Experiencer(str, Enum):
    """Who is described by the phenotype mention."""

    PATIENT = "patient"
    FAMILY_MEMBER = "family_member"


class ReviewDecision(str, Enum):
    """A human review action; pending is derived from no review event."""

    ACCEPTED_POSITIVE = "accepted_positive"
    CONFIRMED_NEGATED = "confirmed_negated"
    REJECTED = "rejected"
    MODIFIED = "modified"
    DEFERRED = "deferred"


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(field_name))


def _require_hpo_id(hpo_id: str, field_name: str) -> None:
    if not isinstance(hpo_id, str) or not _HPO_ID_PATTERN.fullmatch(hpo_id):
        raise ValueError("{} must be an HPO identifier".format(field_name))


@dataclass(frozen=True)
class CaseRecord:
    """An independently persisted phenotype case identity."""

    case_id: str

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")


@dataclass(frozen=True)
class SourceDocumentRevision:
    """An immutable decoded source document revision and its UTF-8 digest."""

    case_id: str
    document_ref: str
    revision: str
    sha256: str
    text: str

    def __post_init__(self) -> None:
        for field_name in ("case_id", "document_ref", "revision"):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.sha256
        ):
            raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")


@dataclass(frozen=True)
class HpoSuggestion:
    """An HPO candidate retained for human review, never a confirmation."""

    canonical_hpo_id: str
    matched_hpo_id: str
    term_name: str
    match_type: MatchType
    explanation: str
    is_obsolete: bool
    replacement_ids: Tuple[str, ...] = ()
    consider_ids: Tuple[str, ...] = ()
    human_review_required: bool = True

    def __post_init__(self) -> None:
        _require_hpo_id(self.canonical_hpo_id, "canonical_hpo_id")
        _require_hpo_id(self.matched_hpo_id, "matched_hpo_id")
        _require_text(self.term_name, "term_name")
        _require_text(self.explanation, "explanation")
        if not isinstance(self.match_type, MatchType):
            raise TypeError("match_type must be a MatchType")
        if self.human_review_required is not True:
            raise ValueError("HPO suggestions must require human review")
        if not isinstance(self.is_obsolete, bool):
            raise TypeError("is_obsolete must be a bool")
        for field_name, identifiers in (
            ("replacement_ids", self.replacement_ids),
            ("consider_ids", self.consider_ids),
        ):
            if not isinstance(identifiers, tuple):
                raise TypeError("{} must be a tuple".format(field_name))
            for hpo_id in identifiers:
                _require_hpo_id(hpo_id, field_name)


@dataclass(frozen=True)
class PhenotypeMention:
    """An extracted mention linked to immutable source evidence.

    Offsets are zero-based, half-open Unicode code-point offsets into the exact
    decoded text of ``source_document_revision``.
    """

    mention_id: str
    case_id: str
    source_document_ref: str
    original_text: str
    start_offset: int
    end_offset: int
    phenotype_text: str
    assertion: AssertionStatus
    temporality: Temporality
    experiencer: Experiencer
    hpo_version: str
    suggestions: Tuple[HpoSuggestion, ...] = ()
    family_relationship: Optional[str] = None
    extraction_confidence: Optional[float] = None
    extractor_version: Optional[str] = None
    source_document_revision: Optional[str] = None
    hpo_mapping_ambiguous: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "mention_id",
            "case_id",
            "source_document_ref",
            "original_text",
            "phenotype_text",
            "hpo_version",
        ):
            _require_text(getattr(self, field_name), field_name)

        if not isinstance(self.start_offset, int) or not isinstance(self.end_offset, int):
            raise TypeError("source offsets must be integers")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("source offsets must define a non-empty span")
        if len(self.original_text) != self.end_offset - self.start_offset:
            raise ValueError("original_text length must match the source span offsets")

        if not isinstance(self.assertion, AssertionStatus):
            raise TypeError("assertion must be an AssertionStatus")
        if not isinstance(self.temporality, Temporality):
            raise TypeError("temporality must be a Temporality")
        if not isinstance(self.experiencer, Experiencer):
            raise TypeError("experiencer must be an Experiencer")
        if self.experiencer == Experiencer.FAMILY_MEMBER:
            _require_text(self.family_relationship or "", "family_relationship")
        elif self.family_relationship is not None:
            raise ValueError("family_relationship is only valid for a family member")

        if self.extraction_confidence is not None:
            if isinstance(self.extraction_confidence, bool) or not isinstance(
                self.extraction_confidence, (int, float)
            ):
                raise TypeError("extraction_confidence must be a number")
            if not 0.0 <= self.extraction_confidence <= 1.0:
                raise ValueError("extraction_confidence must be between 0 and 1")
        if self.extractor_version is not None:
            _require_text(self.extractor_version, "extractor_version")
        if self.source_document_revision is not None:
            _require_text(self.source_document_revision, "source_document_revision")
        if not isinstance(self.hpo_mapping_ambiguous, bool):
            raise TypeError("hpo_mapping_ambiguous must be a bool")
        if not isinstance(self.suggestions, tuple) or any(
            not isinstance(suggestion, HpoSuggestion)
            for suggestion in self.suggestions
        ):
            raise TypeError("suggestions must be a tuple of HpoSuggestion values")


@dataclass(frozen=True)
class ReviewEvent:
    """An immutable reviewer action; corrections append and supersede events."""

    event_id: str
    mention_id: str
    decision: ReviewDecision
    reviewer_id: str
    reviewed_at: datetime
    hpo_version: str
    notes: str = ""
    selected_hpo_id: Optional[str] = None
    modified_assertion: Optional[AssertionStatus] = None
    modified_temporality: Optional[Temporality] = None
    modified_experiencer: Optional[Experiencer] = None
    modified_phenotype_text: Optional[str] = None
    supersedes_event_id: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in ("event_id", "mention_id", "reviewer_id", "hpo_version"):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.decision, ReviewDecision):
            raise TypeError("decision must be a ReviewDecision")
        if not isinstance(self.reviewed_at, datetime):
            raise TypeError("reviewed_at must be a datetime")
        if (
            self.reviewed_at.tzinfo is None
            or self.reviewed_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("reviewed_at must be timezone-aware UTC")

        if self.selected_hpo_id is not None:
            _require_hpo_id(self.selected_hpo_id, "selected_hpo_id")
        if self.decision == ReviewDecision.ACCEPTED_POSITIVE:
            if self.selected_hpo_id is None:
                raise ValueError("accepted_positive requires selected_hpo_id")
            if (
                self.modified_assertion is not None
                and self.modified_assertion != AssertionStatus.POSITIVE
            ):
                raise ValueError("accepted_positive cannot record a non-positive assertion")
        if self.decision == ReviewDecision.MODIFIED and not any(
            value is not None
            for value in (
                self.selected_hpo_id,
                self.modified_assertion,
                self.modified_temporality,
                self.modified_experiencer,
                self.modified_phenotype_text,
            )
        ):
            raise ValueError("modified decisions must record at least one change")

        if self.modified_assertion is not None and not isinstance(
            self.modified_assertion, AssertionStatus
        ):
            raise TypeError("modified_assertion must be an AssertionStatus")
        if self.modified_temporality is not None and not isinstance(
            self.modified_temporality, Temporality
        ):
            raise TypeError("modified_temporality must be a Temporality")
        if self.modified_experiencer is not None and not isinstance(
            self.modified_experiencer, Experiencer
        ):
            raise TypeError("modified_experiencer must be an Experiencer")
        if self.modified_phenotype_text is not None:
            _require_text(self.modified_phenotype_text, "modified_phenotype_text")
        if self.supersedes_event_id is not None:
            _require_text(self.supersedes_event_id, "supersedes_event_id")
            if self.supersedes_event_id == self.event_id:
                raise ValueError("an event cannot supersede itself")


def validate_mention_evidence(
    document: SourceDocumentRevision,
    mention: PhenotypeMention,
) -> None:
    """Validate a mention's identity, UTF-8 document digest, and exact text span."""

    if not isinstance(document, SourceDocumentRevision):
        raise TypeError("document must be a SourceDocumentRevision")
    if not isinstance(mention, PhenotypeMention):
        raise TypeError("mention must be a PhenotypeMention")
    if document.case_id != mention.case_id:
        raise ValueError("document and mention must belong to the same case")
    if document.document_ref != mention.source_document_ref:
        raise ValueError("document reference does not match the mention")
    if mention.source_document_revision is None:
        raise ValueError("mention must identify a source document revision")
    if document.revision != mention.source_document_revision:
        raise ValueError("document revision does not match the mention")

    try:
        actual_sha256 = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as error:
        raise ValueError("document text must be encodable as UTF-8") from error
    if actual_sha256 != document.sha256:
        raise ValueError("document SHA-256 does not match its UTF-8 text")

    if (
        isinstance(mention.start_offset, bool)
        or isinstance(mention.end_offset, bool)
        or not isinstance(mention.start_offset, int)
        or not isinstance(mention.end_offset, int)
    ):
        raise TypeError("source offsets must be integer code-point offsets")
    if (
        mention.start_offset < 0
        or mention.end_offset <= mention.start_offset
        or mention.end_offset > len(document.text)
    ):
        raise ValueError("source offsets must define a non-empty span within the document")
    if mention.original_text != document.text[mention.start_offset : mention.end_offset]:
        raise ValueError("mention evidence does not match the source document text slice")
