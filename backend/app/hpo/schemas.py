"""Typed data structures used by the local HPO lookup module."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


@dataclass(frozen=True)
class HpoReleaseMetadata:
    """Verified release identity for a pinned local HPO ontology."""

    release: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.release, str) or not self.release.strip():
            raise ValueError("release must be a non-empty string")
        if not isinstance(self.sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.sha256
        ):
            raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest")


HPO_RELEASE_METADATA = HpoReleaseMetadata(
    release="2026-09-01",
    sha256="93dace952fcb3ec4728818857f6ba76bc2d5312f4d83266519b8694b8e798f22",
)


class MatchType(str, Enum):
    """How a query matched an HPO term."""

    EXACT_LABEL = "exact_label"
    NORMALIZED_LABEL = "normalized_label"
    SYNONYM = "synonym"
    TERM_ID = "term_id"
    ALTERNATIVE_ID = "alternative_id"


@dataclass(frozen=True)
class HpoTerm:
    """A term read from an HPO OBO file."""

    hpo_id: str
    name: str
    definition: Optional[str] = None
    synonyms: Tuple[str, ...] = ()
    alternative_ids: Tuple[str, ...] = ()
    parent_ids: Tuple[str, ...] = ()
    is_obsolete: bool = False
    replacement_ids: Tuple[str, ...] = ()
    consider_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LookupSuggestion:
    """A term suggestion; it is never an automatic reviewer confirmation."""

    matched_hpo_id: str
    canonical_hpo_id: str
    term_name: str
    match_type: MatchType
    explanation: str
    is_obsolete: bool
    human_review_required: bool = True
    replacement_ids: Tuple[str, ...] = ()
    consider_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LookupResult:
    """Search result with an explicit message when no terms match."""

    query: str
    suggestions: Tuple[LookupSuggestion, ...]
    message: Optional[str] = None
    ambiguous: bool = False

    @property
    def has_matches(self) -> bool:
        """Return whether the search produced any suggestions."""

        return bool(self.suggestions)
