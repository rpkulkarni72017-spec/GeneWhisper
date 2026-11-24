"""Read-only parser and lookup indexes for a local HPO OBO file."""

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from .schemas import (
    HPO_RELEASE_METADATA,
    HpoReleaseMetadata,
    HpoTerm,
    LookupResult,
    LookupSuggestion,
    MatchType,
)

LOGGER = logging.getLogger(__name__)

_HPO_ID_PATTERN = re.compile(r"^HP:\d+$", re.IGNORECASE)
_SYNONYM_SCOPE_PATTERN = re.compile(r"^\s+(EXACT|BROAD|NARROW|RELATED)\b")
_RELEVANT_TAGS = {
    "id",
    "name",
    "def",
    "synonym",
    "alt_id",
    "is_a",
    "relationship",
    "is_obsolete",
    "replaced_by",
    "consider",
}


class HpoOntology:
    """In-memory HPO terms and indexes loaded from an existing OBO file."""

    def __init__(
        self,
        terms: Dict[str, HpoTerm],
        parse_warnings: Sequence[str] = (),
        canonical_conflicts: Optional[Dict[str, Sequence[HpoTerm]]] = None,
    ) -> None:
        self._release_metadata: Optional[HpoReleaseMetadata] = None
        self._terms = dict(terms)
        self._canonical_conflicts = {
            hpo_id: tuple(conflicting_terms)
            for hpo_id, conflicting_terms in (canonical_conflicts or {}).items()
        }
        self.parse_warnings = tuple(parse_warnings)
        self._label_index: Dict[str, Set[str]] = {}
        self._synonym_index: Dict[str, Set[str]] = {}
        self._alternative_id_index: Dict[str, Set[str]] = {}
        self._terms_by_id: Dict[str, Tuple[HpoTerm, ...]] = {}
        alternative_id_counts: Dict[str, int] = {}

        terms_by_id: Dict[str, List[HpoTerm]] = {}
        all_terms = list(self._terms.values())
        for conflicting_terms in self._canonical_conflicts.values():
            all_terms.extend(conflicting_terms)

        for term in all_terms:
            terms_by_id.setdefault(term.hpo_id, []).append(term)
            self._label_index.setdefault(_normalize(term.name), set()).add(term.hpo_id)
            for synonym in term.synonyms:
                self._synonym_index.setdefault(_normalize(synonym), set()).add(term.hpo_id)
            for alternative_id in term.alternative_ids:
                normalized_alternative_id = _normalize_id(alternative_id)
                self._alternative_id_index.setdefault(normalized_alternative_id, set()).add(
                    term.hpo_id
                )
                alternative_id_counts[normalized_alternative_id] = (
                    alternative_id_counts.get(normalized_alternative_id, 0) + 1
                )

        self._terms_by_id = {
            hpo_id: tuple(matching_terms)
            for hpo_id, matching_terms in terms_by_id.items()
        }
        self._ambiguous_alternative_ids = {
            hpo_id for hpo_id, count in alternative_id_counts.items() if count > 1
        }

    @classmethod
    def from_obo(cls, obo_path: Union[str, Path]) -> "HpoOntology":
        """Load terms from an OBO file without modifying it.

        Malformed term stanzas are skipped and recorded in ``parse_warnings``.
        File access and decoding errors are raised to the caller.
        """

        path = Path(obo_path)
        if not path.is_file():
            raise FileNotFoundError("HPO OBO file not found: {}".format(path))

        terms: Dict[str, HpoTerm] = {}
        canonical_conflicts: Dict[str, List[HpoTerm]] = {}
        warnings: List[str] = []
        stanza_lines: List[Tuple[int, str]] = []

        def finish_stanza() -> None:
            if not stanza_lines:
                return
            try:
                term = _parse_term_stanza(stanza_lines)
            except ValueError as error:
                warning = "Skipped malformed HPO term stanza: {}".format(error)
                warnings.append(warning)
                LOGGER.warning(warning)
                return

            if term is None:
                return
            if term.hpo_id in terms:
                warning = "Duplicate canonical HPO identifier: {}".format(term.hpo_id)
                warnings.append(warning)
                LOGGER.warning(warning)
                canonical_conflicts.setdefault(term.hpo_id, []).append(term)
                return
            terms[term.hpo_id] = term

        with path.open("r", encoding="utf-8") as obo_file:
            in_term_stanza = False
            for line_number, raw_line in enumerate(obo_file, start=1):
                line = raw_line.rstrip("\r\n")
                if line == "[Term]":
                    finish_stanza()
                    stanza_lines = []
                    in_term_stanza = True
                elif line.startswith("["):
                    finish_stanza()
                    stanza_lines = []
                    in_term_stanza = False
                elif in_term_stanza and line.strip() and not line.lstrip().startswith("!"):
                    stanza_lines.append((line_number, line.strip()))
            finish_stanza()

        if not terms:
            raise ValueError("No valid HPO term stanzas were found in: {}".format(path))
        return cls(terms, warnings, canonical_conflicts)

    def get_term(self, hpo_id: str) -> Optional[HpoTerm]:
        """Return a canonical term by ID, or ``None`` if it is not present."""

        normalized_id = _normalize_id(hpo_id)
        if self._identifier_is_ambiguous(normalized_id):
            raise ValueError(
                "Ambiguous HPO identifier {!r}; use resolve_id() to inspect candidates.".format(
                    hpo_id
                )
            )
        return self._terms.get(normalized_id)

    def resolve_id(self, hpo_id: str) -> LookupResult:
        """Look up a canonical or alternate HPO ID without replacing it silently."""

        query = hpo_id.strip()
        if not query or not _HPO_ID_PATTERN.match(query):
            return _no_match(query, "Enter a valid HPO identifier.")

        normalized_id = _normalize_id(query)
        suggestions: List[LookupSuggestion] = []

        for canonical_term in self._terms_by_id.get(normalized_id, ()):
            suggestions.append(
                _make_suggestion(
                    matched_hpo_id=query,
                    term=canonical_term,
                    match_type=MatchType.TERM_ID,
                )
            )

        for canonical_id in self._alternative_id_index.get(normalized_id, set()):
            for term in self._terms_by_id.get(canonical_id, ()):
                suggestions.append(
                    _make_suggestion(
                        matched_hpo_id=query,
                        term=term,
                        match_type=MatchType.ALTERNATIVE_ID,
                    )
                )

        ambiguous = self._identifier_is_ambiguous(normalized_id)
        ordered = tuple(
            sorted(
                suggestions,
                key=lambda item: (
                    item.canonical_hpo_id,
                    item.match_type.value,
                    item.term_name,
                ),
            )
        )
        if not ordered:
            return _no_match(query, "No HPO term found for identifier {!r}.".format(query))
        message = None
        if ambiguous:
            message = (
                "Identifier {!r} is ambiguous or conflicts with a canonical HPO ID; "
                "review all candidates.".format(query)
            )
        return LookupResult(
            query=query,
            suggestions=ordered,
            message=message,
            ambiguous=ambiguous,
        )

    def _identifier_is_ambiguous(self, normalized_id: str) -> bool:
        has_canonical_id = normalized_id in self._terms_by_id
        has_alternative_id = normalized_id in self._alternative_id_index
        return (
            normalized_id in self._canonical_conflicts
            or normalized_id in self._ambiguous_alternative_ids
            or (has_canonical_id and has_alternative_id)
        )

    def search(self, query: str) -> LookupResult:
        """Search preferred labels, synonyms, canonical IDs, and alternate IDs.

        Label and synonym searches are case-insensitive and ignore repeated
        whitespace. An empty ``suggestions`` tuple is an explicit no-match result.
        All suggestions require human review, including obsolete terms.
        """

        original_query = query
        normalized_query = _normalize(query)
        if not normalized_query:
            return _no_match(original_query, "Enter an HPO ID, term label, or synonym.")

        if _HPO_ID_PATTERN.match(query.strip()):
            return self.resolve_id(query)

        candidate_matches: Dict[int, LookupSuggestion] = {}
        matched_term_ids = self._label_index.get(normalized_query, set())
        for term_id in matched_term_ids:
            for term in self._terms_by_id.get(term_id, ()):
                match_type = (
                    MatchType.EXACT_LABEL
                    if query == term.name
                    else MatchType.NORMALIZED_LABEL
                )
                candidate_matches[id(term)] = _make_suggestion(
                    matched_hpo_id=term.hpo_id,
                    term=term,
                    match_type=match_type,
                )

        synonym_term_ids = self._synonym_index.get(normalized_query, set())
        for term_id in synonym_term_ids:
            for term in self._terms_by_id.get(term_id, ()):
                synonym_suggestion = _make_suggestion(
                    matched_hpo_id=term.hpo_id,
                    term=term,
                    match_type=MatchType.SYNONYM,
                )
                existing = candidate_matches.get(id(term))
                if existing is None or _match_priority(
                    synonym_suggestion
                ) < _match_priority(existing):
                    candidate_matches[id(term)] = synonym_suggestion

        ordered = tuple(
            sorted(
                candidate_matches.values(),
                key=lambda item: (
                    _match_priority(item),
                    item.canonical_hpo_id,
                ),
            )
        )
        if not ordered:
            return _no_match(
                original_query,
                "No HPO term found for query {!r}.".format(original_query),
            )
        ambiguous = len(ordered) > 1
        message = None
        if ambiguous:
            message = (
                "Multiple HPO candidates match query {!r}; review all candidates."
                .format(original_query)
            )
        return LookupResult(
            query=original_query,
            suggestions=ordered,
            message=message,
            ambiguous=ambiguous,
        )

    @property
    def term_count(self) -> int:
        """Return the number of successfully parsed canonical terms."""

        return len(self._terms)

    @property
    def release_metadata(self) -> Optional[HpoReleaseMetadata]:
        """Return verified release metadata, or ``None`` for an unpinned load."""

        return self._release_metadata

    @property
    def release(self) -> Optional[str]:
        """Return the verified HPO release identifier, if pinned."""

        if self._release_metadata is None:
            return None
        return self._release_metadata.release

    @property
    def sha256(self) -> Optional[str]:
        """Return the verified source-file digest, if pinned."""

        if self._release_metadata is None:
            return None
        return self._release_metadata.sha256


def load_ontology(obo_path: Union[str, Path]) -> HpoOntology:
    """Load an HPO ontology from the supplied local OBO file."""

    return HpoOntology.from_obo(obo_path)


def load_pinned_ontology(
    obo_path: Union[str, Path],
    *,
    metadata: HpoReleaseMetadata,
) -> HpoOntology:
    """Verify the pinned HPO file before parsing and loading its terms."""

    if not isinstance(metadata, HpoReleaseMetadata):
        raise TypeError("metadata must be an HpoReleaseMetadata value")
    if metadata != HPO_RELEASE_METADATA:
        raise ValueError("metadata does not match the configured HPO release")

    path = Path(obo_path)
    if not path.is_file():
        raise FileNotFoundError("HPO OBO file not found: {}".format(path))

    digest = hashlib.sha256()
    with path.open("rb") as obo_file:
        for chunk in iter(lambda: obo_file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != metadata.sha256:
        raise ValueError("HPO OBO SHA-256 does not match the pinned release metadata")

    ontology = HpoOntology.from_obo(path)
    ontology._release_metadata = metadata
    return ontology


def _parse_term_stanza(lines: Sequence[Tuple[int, str]]) -> Optional[HpoTerm]:
    fields: Dict[str, List[str]] = {}
    for line_number, line in lines:
        tag, separator, value = line.partition(":")
        if not separator:
            raise ValueError("line {} has no tag separator".format(line_number))
        if tag in _RELEVANT_TAGS:
            fields.setdefault(tag, []).append(value.strip())

    identifiers = fields.get("id", [])
    names = fields.get("name", [])
    if len(identifiers) != 1 or len(names) != 1:
        raise ValueError("term stanza must contain exactly one id and one name")

    hpo_id = _value_before_comment(identifiers[0])
    if not _HPO_ID_PATTERN.match(hpo_id):
        raise ValueError("invalid HPO identifier {!r}".format(hpo_id))
    name = names[0]
    if not name:
        raise ValueError("term {} has an empty name".format(hpo_id))

    definitions = fields.get("def", [])
    if len(definitions) > 1:
        raise ValueError("term {} has multiple definitions".format(hpo_id))
    definition = _parse_quoted_value(definitions[0])[0] if definitions else None

    synonyms: List[str] = []
    for synonym_value in fields.get("synonym", []):
        synonym, remainder = _parse_quoted_value(synonym_value)
        if not _SYNONYM_SCOPE_PATTERN.match(remainder):
            raise ValueError("term {} has a malformed synonym".format(hpo_id))
        synonyms.append(synonym)

    obsolete_values = fields.get("is_obsolete", [])
    if len(obsolete_values) > 1:
        raise ValueError("term {} has multiple obsolete flags".format(hpo_id))
    obsolete_value = obsolete_values[0].lower() if obsolete_values else "false"
    if obsolete_value not in ("true", "false"):
        raise ValueError("term {} has an invalid obsolete flag".format(hpo_id))

    parent_ids = {
        _value_before_comment(value)
        for value in fields.get("is_a", [])
    }
    for relationship in fields.get("relationship", []):
        relationship_parts = relationship.split()
        if len(relationship_parts) >= 2 and relationship_parts[0] == "part_of":
            parent_ids.add(relationship_parts[1])

    return HpoTerm(
        hpo_id=_normalize_id(hpo_id),
        name=name,
        definition=definition,
        synonyms=tuple(dict.fromkeys(synonyms)),
        alternative_ids=tuple(
            _normalize_id(_value_before_comment(value))
            for value in fields.get("alt_id", [])
        ),
        parent_ids=tuple(sorted(parent_ids)),
        is_obsolete=obsolete_value == "true",
        replacement_ids=tuple(
            dict.fromkeys(
                _normalize_id(_value_before_comment(value))
                for value in fields.get("replaced_by", [])
            )
        ),
        consider_ids=tuple(
            dict.fromkeys(
                _normalize_id(_value_before_comment(value))
                for value in fields.get("consider", [])
            )
        ),
    )


def _parse_quoted_value(value: str) -> Tuple[str, str]:
    if not value.startswith('"'):
        raise ValueError("expected a quoted OBO value")

    characters: List[str] = []
    index = 1
    while index < len(value):
        character = value[index]
        if character == '"':
            return "".join(characters), value[index + 1 :]
        if character == "\\":
            index += 1
            if index >= len(value):
                raise ValueError("unterminated escape in quoted OBO value")
            escaped = value[index]
            if escaped == '"':
                characters.append('"')
            elif escaped == "\\":
                characters.append(escaped)
            else:
                # Preserve OBO escapes this parser does not interpret.
                characters.extend(("\\", escaped))
        else:
            characters.append(character)
        index += 1

    raise ValueError("unterminated quoted OBO value")


def _value_before_comment(value: str) -> str:
    return value.partition(" ! ")[0].strip()


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalize_id(value: str) -> str:
    return value.strip().upper()


def _make_suggestion(
    matched_hpo_id: str,
    term: HpoTerm,
    match_type: MatchType,
) -> LookupSuggestion:
    explanations = {
        MatchType.EXACT_LABEL: "Exact match to the preferred HPO label.",
        MatchType.NORMALIZED_LABEL: (
            "Matches the preferred HPO label after case and whitespace normalization."
        ),
        MatchType.SYNONYM: "Matches an HPO synonym.",
        MatchType.TERM_ID: "Matches the canonical HPO identifier.",
        MatchType.ALTERNATIVE_ID: (
            "The supplied identifier is an alternate ID for the canonical term."
        ),
    }
    return LookupSuggestion(
        matched_hpo_id=matched_hpo_id,
        canonical_hpo_id=term.hpo_id,
        term_name=term.name,
        match_type=match_type,
        explanation=explanations[match_type],
        is_obsolete=term.is_obsolete,
        human_review_required=True,
        replacement_ids=term.replacement_ids,
        consider_ids=term.consider_ids,
    )


def _match_priority(suggestion: LookupSuggestion) -> int:
    priorities = {
        MatchType.EXACT_LABEL: 0,
        MatchType.NORMALIZED_LABEL: 1,
        MatchType.SYNONYM: 2,
        MatchType.TERM_ID: 0,
        MatchType.ALTERNATIVE_ID: 1,
    }
    return priorities[suggestion.match_type]


def _no_match(query: str, message: str) -> LookupResult:
    return LookupResult(query=query, suggestions=(), message=message)
