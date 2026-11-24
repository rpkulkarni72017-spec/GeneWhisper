"""Strict, database-independent serialization for phenotype domain records."""

from datetime import datetime, timedelta
from typing import Any, Dict, Mapping, Optional, Set, Tuple, Type, TypeVar

from ..hpo.schemas import MatchType
from .schemas import (
    AssertionStatus,
    Experiencer,
    HpoSuggestion,
    PhenotypeMention,
    ReviewDecision,
    ReviewEvent,
    Temporality,
)

_EnumType = TypeVar("_EnumType")

_SUGGESTION_FIELDS = {
    "canonical_hpo_id",
    "matched_hpo_id",
    "term_name",
    "match_type",
    "explanation",
    "is_obsolete",
    "replacement_ids",
    "consider_ids",
    "human_review_required",
}
_MENTION_FIELDS = {
    "mention_id",
    "case_id",
    "source_document_ref",
    "original_text",
    "start_offset",
    "end_offset",
    "phenotype_text",
    "assertion",
    "temporality",
    "experiencer",
    "hpo_version",
    "suggestions",
    "family_relationship",
    "extraction_confidence",
    "extractor_version",
    "source_document_revision",
    "hpo_mapping_ambiguous",
}
_REVIEW_EVENT_FIELDS = {
    "event_id",
    "mention_id",
    "decision",
    "reviewer_id",
    "reviewed_at",
    "hpo_version",
    "notes",
    "selected_hpo_id",
    "modified_assertion",
    "modified_temporality",
    "modified_experiencer",
    "modified_phenotype_text",
    "supersedes_event_id",
}


def hpo_suggestion_to_dict(suggestion: HpoSuggestion) -> Dict[str, Any]:
    """Serialize a suggestion using explicit JSON-compatible fields."""

    if not isinstance(suggestion, HpoSuggestion):
        raise TypeError("suggestion must be an HpoSuggestion")
    if not isinstance(suggestion.match_type, MatchType):
        raise TypeError("match_type must be a MatchType")
    if type(suggestion.is_obsolete) is not bool:
        raise TypeError("is_obsolete must be a bool")
    if suggestion.human_review_required is not True:
        raise ValueError("HPO suggestions must require human review")

    return {
        "canonical_hpo_id": suggestion.canonical_hpo_id,
        "matched_hpo_id": suggestion.matched_hpo_id,
        "term_name": suggestion.term_name,
        "match_type": suggestion.match_type.value,
        "explanation": suggestion.explanation,
        "is_obsolete": suggestion.is_obsolete,
        "replacement_ids": _string_tuple_to_list(
            suggestion.replacement_ids, "replacement_ids"
        ),
        "consider_ids": _string_tuple_to_list(
            suggestion.consider_ids, "consider_ids"
        ),
        "human_review_required": suggestion.human_review_required,
    }


def hpo_suggestion_from_dict(data: Mapping[str, Any]) -> HpoSuggestion:
    """Deserialize a suggestion, rejecting missing or additional fields."""

    values = _require_fields(data, _SUGGESTION_FIELDS, "HpoSuggestion")
    replacement_ids = _string_list_to_tuple(values["replacement_ids"], "replacement_ids")
    consider_ids = _string_list_to_tuple(values["consider_ids"], "consider_ids")
    return HpoSuggestion(
        canonical_hpo_id=_required_text(values["canonical_hpo_id"], "canonical_hpo_id"),
        matched_hpo_id=_required_text(values["matched_hpo_id"], "matched_hpo_id"),
        term_name=_required_text(values["term_name"], "term_name"),
        match_type=_parse_enum(values["match_type"], MatchType, "match_type"),
        explanation=_required_text(values["explanation"], "explanation"),
        is_obsolete=_required_bool(values["is_obsolete"], "is_obsolete"),
        replacement_ids=replacement_ids,
        consider_ids=consider_ids,
        human_review_required=_required_bool(
            values["human_review_required"], "human_review_required"
        ),
    )


def phenotype_mention_to_dict(mention: PhenotypeMention) -> Dict[str, Any]:
    """Serialize every PhenotypeMention field, including optional values."""

    if not isinstance(mention, PhenotypeMention):
        raise TypeError("mention must be a PhenotypeMention")
    _required_int(mention.start_offset, "start_offset")
    _required_int(mention.end_offset, "end_offset")
    _required_bool(mention.hpo_mapping_ambiguous, "hpo_mapping_ambiguous")
    _optional_text(mention.family_relationship, "family_relationship")
    _optional_text(mention.extractor_version, "extractor_version")
    _optional_text(mention.source_document_revision, "source_document_revision")
    _optional_confidence(mention.extraction_confidence)
    if not isinstance(mention.suggestions, tuple):
        raise TypeError("suggestions must be a tuple")

    return {
        "mention_id": mention.mention_id,
        "case_id": mention.case_id,
        "source_document_ref": mention.source_document_ref,
        "original_text": mention.original_text,
        "start_offset": mention.start_offset,
        "end_offset": mention.end_offset,
        "phenotype_text": mention.phenotype_text,
        "assertion": _enum_value(mention.assertion, AssertionStatus, "assertion"),
        "temporality": _enum_value(mention.temporality, Temporality, "temporality"),
        "experiencer": _enum_value(mention.experiencer, Experiencer, "experiencer"),
        "hpo_version": mention.hpo_version,
        "suggestions": [
            hpo_suggestion_to_dict(suggestion) for suggestion in mention.suggestions
        ],
        "family_relationship": mention.family_relationship,
        "extraction_confidence": mention.extraction_confidence,
        "extractor_version": mention.extractor_version,
        "source_document_revision": mention.source_document_revision,
        "hpo_mapping_ambiguous": mention.hpo_mapping_ambiguous,
    }


def phenotype_mention_from_dict(data: Mapping[str, Any]) -> PhenotypeMention:
    """Deserialize a mention and all nested suggestions without discarding data."""

    values = _require_fields(data, _MENTION_FIELDS, "PhenotypeMention")
    suggestions_data = values["suggestions"]
    if not isinstance(suggestions_data, list):
        raise TypeError("suggestions must be a list")
    suggestions = tuple(
        hpo_suggestion_from_dict(suggestion) for suggestion in suggestions_data
    )

    return PhenotypeMention(
        mention_id=_required_text(values["mention_id"], "mention_id"),
        case_id=_required_text(values["case_id"], "case_id"),
        source_document_ref=_required_text(
            values["source_document_ref"], "source_document_ref"
        ),
        original_text=_required_text(values["original_text"], "original_text"),
        start_offset=_required_int(values["start_offset"], "start_offset"),
        end_offset=_required_int(values["end_offset"], "end_offset"),
        phenotype_text=_required_text(values["phenotype_text"], "phenotype_text"),
        assertion=_parse_enum(values["assertion"], AssertionStatus, "assertion"),
        temporality=_parse_enum(values["temporality"], Temporality, "temporality"),
        experiencer=_parse_enum(values["experiencer"], Experiencer, "experiencer"),
        hpo_version=_required_text(values["hpo_version"], "hpo_version"),
        suggestions=suggestions,
        family_relationship=_optional_text(
            values["family_relationship"], "family_relationship"
        ),
        extraction_confidence=_optional_confidence(values["extraction_confidence"]),
        extractor_version=_optional_text(
            values["extractor_version"], "extractor_version"
        ),
        source_document_revision=_optional_text(
            values["source_document_revision"], "source_document_revision"
        ),
        hpo_mapping_ambiguous=_required_bool(
            values["hpo_mapping_ambiguous"], "hpo_mapping_ambiguous"
        ),
    )


def review_event_to_dict(event: ReviewEvent) -> Dict[str, Any]:
    """Serialize every field; timestamps use UTC ISO format with six fractional digits."""

    if not isinstance(event, ReviewEvent):
        raise TypeError("event must be a ReviewEvent")
    if not isinstance(event.notes, str):
        raise TypeError("notes must be a string")
    if not isinstance(event.reviewed_at, datetime):
        raise TypeError("reviewed_at must be a datetime")
    reviewed_at = _format_utc_datetime(event.reviewed_at)

    return {
        "event_id": event.event_id,
        "mention_id": event.mention_id,
        "decision": _enum_value(event.decision, ReviewDecision, "decision"),
        "reviewer_id": event.reviewer_id,
        "reviewed_at": reviewed_at,
        "hpo_version": event.hpo_version,
        "notes": event.notes,
        "selected_hpo_id": event.selected_hpo_id,
        "modified_assertion": _optional_enum_value(
            event.modified_assertion, AssertionStatus, "modified_assertion"
        ),
        "modified_temporality": _optional_enum_value(
            event.modified_temporality, Temporality, "modified_temporality"
        ),
        "modified_experiencer": _optional_enum_value(
            event.modified_experiencer, Experiencer, "modified_experiencer"
        ),
        "modified_phenotype_text": event.modified_phenotype_text,
        "supersedes_event_id": event.supersedes_event_id,
    }


def review_event_from_dict(data: Mapping[str, Any]) -> ReviewEvent:
    """Require every field and canonical UTC timestamps with six fractional digits."""

    values = _require_fields(data, _REVIEW_EVENT_FIELDS, "ReviewEvent")
    notes = values["notes"]
    if not isinstance(notes, str):
        raise TypeError("notes must be a string")

    return ReviewEvent(
        event_id=_required_text(values["event_id"], "event_id"),
        mention_id=_required_text(values["mention_id"], "mention_id"),
        decision=_parse_enum(values["decision"], ReviewDecision, "decision"),
        reviewer_id=_required_text(values["reviewer_id"], "reviewer_id"),
        reviewed_at=_parse_utc_datetime(values["reviewed_at"]),
        hpo_version=_required_text(values["hpo_version"], "hpo_version"),
        notes=notes,
        selected_hpo_id=_optional_text(values["selected_hpo_id"], "selected_hpo_id"),
        modified_assertion=_parse_optional_enum(
            AssertionStatus, values["modified_assertion"], "modified_assertion"
        ),
        modified_temporality=_parse_optional_enum(
            Temporality, values["modified_temporality"], "modified_temporality"
        ),
        modified_experiencer=_parse_optional_enum(
            Experiencer, values["modified_experiencer"], "modified_experiencer"
        ),
        modified_phenotype_text=_optional_text(
            values["modified_phenotype_text"], "modified_phenotype_text"
        ),
        supersedes_event_id=_optional_text(
            values["supersedes_event_id"], "supersedes_event_id"
        ),
    )


def _require_fields(
    data: Mapping[str, Any],
    expected_fields: Set[str],
    record_name: str,
) -> Mapping[str, Any]:
    """Require all declared fields, including optional ones, and reject extras."""

    if not isinstance(data, Mapping):
        raise TypeError("{} data must be a mapping".format(record_name))
    if any(not isinstance(key, str) for key in data):
        raise TypeError("{} field names must be strings".format(record_name))

    actual_fields = set(data)
    missing = expected_fields - actual_fields
    additional = actual_fields - expected_fields
    if missing or additional:
        details = []
        if missing:
            details.append("missing fields: {}".format(", ".join(sorted(missing))))
        if additional:
            details.append("unknown fields: {}".format(", ".join(sorted(additional))))
        raise ValueError("{} {}".format(record_name, "; ".join(details)))
    return data


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(field_name))
    return value


def _optional_text(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _required_text(value, field_name)


def _required_int(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError("{} must be an integer".format(field_name))
    return value


def _required_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError("{} must be a bool".format(field_name))
    return value


def _optional_confidence(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("extraction_confidence must be a number or None")
    if not 0.0 <= value <= 1.0:
        raise ValueError("extraction_confidence must be between 0 and 1")
    return value


def _string_tuple_to_list(value: Tuple[str, ...], field_name: str) -> list:
    if not isinstance(value, tuple):
        raise TypeError("{} must be a tuple".format(field_name))
    return [_required_text(item, field_name) for item in value]


def _string_list_to_tuple(value: Any, field_name: str) -> Tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("{} must be a list".format(field_name))
    return tuple(_required_text(item, field_name) for item in value)


def _enum_value(value: Any, enum_type: Type[_EnumType], field_name: str) -> str:
    if not isinstance(value, enum_type):
        raise TypeError("{} must be a {}".format(field_name, enum_type.__name__))
    return value.value


def _optional_enum_value(
    value: Any,
    enum_type: Type[_EnumType],
    field_name: str,
) -> Optional[str]:
    if value is None:
        return None
    return _enum_value(value, enum_type, field_name)


def _parse_enum(value: Any, enum_type: Type[_EnumType], field_name: str) -> _EnumType:
    if type(value) is not str:
        raise TypeError("{} must be a string enum value".format(field_name))
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(
            "unknown {} value: {!r}".format(field_name, value)
        ) from error


def _parse_optional_enum(
    enum_type: Type[_EnumType],
    value: Any,
    field_name: str,
) -> Optional[_EnumType]:
    if value is None:
        return None
    return _parse_enum(value, enum_type, field_name)


def _format_utc_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("reviewed_at must be timezone-aware UTC")
    return value.isoformat(timespec="microseconds")


def _parse_utc_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("reviewed_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("reviewed_at must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("reviewed_at must be timezone-aware UTC")
    if parsed.isoformat(timespec="microseconds") != value:
        raise ValueError(
            "reviewed_at must use canonical UTC format with microsecond precision"
        )
    return parsed
