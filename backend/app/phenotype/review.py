"""Pure helpers for review history and confirmed phenotype projections."""
#automatically extracted symptoms are checked before analysis

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from ..hpo.ontology import HpoOntology
from .schemas import (
    AssertionStatus,
    Experiencer,
    PhenotypeMention,
    ReviewDecision,
    ReviewEvent,
    Temporality,
)

_HPO_ID_PATTERN = re.compile(r"^HP:\d+$")


@dataclass(frozen=True)
class PhenotypeEvidence:
    """Source evidence retained for a reviewed patient phenotype."""

    mention_id: str
    case_id: str
    source_document_ref: str
    original_text: str
    start_offset: int
    end_offset: int
    assertion: AssertionStatus
    temporality: Temporality
    experiencer: Experiencer
    review_event_id: str


@dataclass(frozen=True)
class ConfirmedPhenotype:
    """One canonical HPO term per case with every supporting mention."""

    case_id: str
    hpo_id: str
    evidence: Tuple[PhenotypeEvidence, ...]


@dataclass(frozen=True)
class EvidenceConflict:
    """Human-reviewed positive and negative evidence for the same case and term."""

    case_id: str
    hpo_id: str
    positive_evidence: Tuple[PhenotypeEvidence, ...]
    negative_evidence: Tuple[PhenotypeEvidence, ...]


@dataclass(frozen=True)
class ConfirmedPositivePhenotypeSet:
    """Deduplicated positive terms plus contradictory evidence flags."""

    phenotypes: Tuple[ConfirmedPhenotype, ...]
    conflicts: Tuple[EvidenceConflict, ...]


def latest_review_events(
    review_events: Iterable[ReviewEvent],
) -> Mapping[str, ReviewEvent]:
    """Return each mention's chronologically latest event without mutating input.

    Supersession references must point to an earlier event for the same mention.
    Equal-time latest events are rejected instead of selecting a winner.
    """

    events_by_id: Dict[str, ReviewEvent] = {}
    grouped: Dict[str, List[ReviewEvent]] = {}
    for event in review_events:
        if not isinstance(event, ReviewEvent):
            raise TypeError("review_events must contain ReviewEvent values")
        if event.event_id in events_by_id:
            raise ValueError("duplicate review event ID: {}".format(event.event_id))
        events_by_id[event.event_id] = event
        grouped.setdefault(event.mention_id, []).append(event)

    for event in events_by_id.values():
        if event.supersedes_event_id is None:
            continue
        superseded = events_by_id.get(event.supersedes_event_id)
        if superseded is None:
            raise ValueError(
                "superseded event {!r} is missing from history".format(
                    event.supersedes_event_id
                )
            )
        if superseded.mention_id != event.mention_id:
            raise ValueError("an event cannot supersede another mention's event")
        if superseded.reviewed_at >= event.reviewed_at:
            raise ValueError("a superseding event must be later than its predecessor")

    latest: Dict[str, ReviewEvent] = {}
    for mention_id, mention_events in grouped.items():
        most_recent_at = max(event.reviewed_at for event in mention_events)
        most_recent = [
            event for event in mention_events if event.reviewed_at == most_recent_at
        ]
        if len(most_recent) != 1:
            raise ValueError(
                "mention {!r} has multiple latest review events".format(mention_id)
            )
        latest[mention_id] = most_recent[0]
    return MappingProxyType(latest)


def is_pending(
    mention_id: str,
    latest_events: Mapping[str, ReviewEvent],
) -> bool:
    """Pending is derived solely from the absence of a review event."""

    return mention_id not in latest_events


def build_confirmed_positive_set(
    mentions: Iterable[PhenotypeMention],
    review_events: Iterable[ReviewEvent],
    ontology: HpoOntology,
) -> ConfirmedPositivePhenotypeSet:
    """Build per-case positive terms from eligible human-reviewed findings.

    Only active HPO suggestions explicitly selected by a reviewer are eligible.
    Historical temporality is retained on evidence and never changed to current.
    """

    if not isinstance(ontology, HpoOntology):
        raise TypeError("ontology must be a loaded HpoOntology")

    mentions_by_id: Dict[str, PhenotypeMention] = {}
    for mention in mentions:
        if not isinstance(mention, PhenotypeMention):
            raise TypeError("mentions must contain PhenotypeMention values")
        if mention.mention_id in mentions_by_id:
            raise ValueError("duplicate phenotype mention ID: {}".format(mention.mention_id))
        mentions_by_id[mention.mention_id] = mention

    latest = latest_review_events(review_events)
    unknown_mentions = set(latest) - set(mentions_by_id)
    if unknown_mentions:
        raise ValueError(
            "review history references unknown mention IDs: {}".format(
                ", ".join(sorted(unknown_mentions))
            )
        )

    for event in latest.values():
        if event.selected_hpo_id is not None:
            _validate_selected_hpo_id(ontology, event.selected_hpo_id)

    positive: Dict[Tuple[str, str], List[PhenotypeEvidence]] = {}
    negative: Dict[Tuple[str, str], List[PhenotypeEvidence]] = {}

    for mention_id, event in latest.items():
        mention = mentions_by_id[mention_id]
        assertion: Optional[AssertionStatus] = None
        experiencer: Optional[Experiencer] = None
        effective_temporality = mention.temporality
        destination: Optional[Dict[Tuple[str, str], List[PhenotypeEvidence]]] = None

        if event.decision == ReviewDecision.ACCEPTED_POSITIVE:
            if (
                mention.assertion == AssertionStatus.POSITIVE
                and mention.experiencer == Experiencer.PATIENT
            ):
                assertion = AssertionStatus.POSITIVE
                experiencer = Experiencer.PATIENT
                destination = positive
        elif event.decision == ReviewDecision.CONFIRMED_NEGATED:
            if mention.experiencer == Experiencer.PATIENT:
                assertion = AssertionStatus.NEGATED
                experiencer = Experiencer.PATIENT
                destination = negative
        elif event.decision == ReviewDecision.MODIFIED:
            effective_temporality = (
                event.modified_temporality or mention.temporality
            )
            if (
                event.modified_assertion == AssertionStatus.POSITIVE
                and event.modified_experiencer == Experiencer.PATIENT
            ):
                assertion = AssertionStatus.POSITIVE
                experiencer = Experiencer.PATIENT
                destination = positive
            elif (
                event.modified_assertion == AssertionStatus.NEGATED
                and event.modified_experiencer == Experiencer.PATIENT
            ):
                assertion = AssertionStatus.NEGATED
                experiencer = Experiencer.PATIENT
                destination = negative

        selected_id = event.selected_hpo_id
        if (
            destination is None
            or assertion is None
            or experiencer is None
            or selected_id is None
            or not _has_unique_active_suggestion(mention, selected_id)
        ):
            continue

        evidence = PhenotypeEvidence(
            mention_id=mention.mention_id,
            case_id=mention.case_id,
            source_document_ref=mention.source_document_ref,
            original_text=mention.original_text,
            start_offset=mention.start_offset,
            end_offset=mention.end_offset,
            assertion=assertion,
            temporality=effective_temporality,
            experiencer=experiencer,
            review_event_id=event.event_id,
        )
        destination.setdefault((mention.case_id, selected_id), []).append(evidence)

    phenotypes = tuple(
        ConfirmedPhenotype(
            case_id=case_id,
            hpo_id=hpo_id,
            evidence=tuple(sorted(evidence, key=lambda item: item.mention_id)),
        )
        for (case_id, hpo_id), evidence in sorted(positive.items())
    )
    conflicts = tuple(
        EvidenceConflict(
            case_id=case_id,
            hpo_id=hpo_id,
            positive_evidence=tuple(
                sorted(positive[(case_id, hpo_id)], key=lambda item: item.mention_id)
            ),
            negative_evidence=tuple(
                sorted(negative[(case_id, hpo_id)], key=lambda item: item.mention_id)
            ),
        )
        for case_id, hpo_id in sorted(set(positive) & set(negative))
    )
    return ConfirmedPositivePhenotypeSet(
        phenotypes=phenotypes,
        conflicts=conflicts,
    )


def _has_unique_active_suggestion(
    mention: PhenotypeMention,
    selected_hpo_id: str,
) -> bool:
    if not _HPO_ID_PATTERN.fullmatch(selected_hpo_id):
        return False
    matching = [
        suggestion
        for suggestion in mention.suggestions
        if suggestion.canonical_hpo_id == selected_hpo_id
    ]
    distinct_terms = {
        (suggestion.term_name, suggestion.is_obsolete)
        for suggestion in matching
    }
    return (
        len(distinct_terms) == 1
        and all(not suggestion.is_obsolete for suggestion in matching)
    )


def _validate_selected_hpo_id(ontology: HpoOntology, hpo_id: str) -> None:
    try:
        term = ontology.get_term(hpo_id)
    except ValueError as error:
        raise ValueError(
            "Selected HPO identifier {!r} is ambiguous in the loaded ontology.".format(
                hpo_id
            )
        ) from error
    if term is None:
        raise ValueError(
            "Selected HPO identifier {!r} does not exist in the loaded ontology.".format(
                hpo_id
            )
        )
    if term.is_obsolete:
        raise ValueError(
            "Selected HPO identifier {!r} is obsolete in the loaded ontology.".format(
                hpo_id
            )
        )
