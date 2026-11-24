"""Thread-safe in-memory storage for phenotype mentions and review events."""

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Mapping, Optional, Protocol, Tuple

from .review import is_pending, latest_review_events
from .schemas import CaseRecord, PhenotypeMention, ReviewEvent


class DuplicateIdentifierError(ValueError):
    """Raised when a case, mention, or review event identifier is already stored."""


class InvalidReviewReferenceError(ValueError):
    """Raised when an event refers to a missing or invalid review-history entry."""


class StaleReviewSubmissionError(ValueError):
    """Raised when the expected latest review event no longer matches."""


class RepositoryStorageError(RuntimeError):
    """Raised for unexpected durable-storage failures.

    Storage adapters should preserve the original exception with ``raise ... from``.
    """


@dataclass(frozen=True)
class CasePhenotypeData:
    """Immutable case-scoped inputs for phenotype projection."""

    case_id: str
    mentions: Tuple[PhenotypeMention, ...]
    review_events: Tuple[ReviewEvent, ...]


class PhenotypeReviewRepository(Protocol):
    """Case, mention, and review storage contract shared by repository adapters."""

    def create_case(self, case: CaseRecord) -> None: ...

    def get_case(self, case_id: str) -> Optional[CaseRecord]: ...

    def case_exists(self, case_id: str) -> bool: ...

    def add_mention(self, mention: PhenotypeMention) -> None: ...

    def get_mention(self, mention_id: str) -> Optional[PhenotypeMention]: ...

    def list_case_mentions(self, case_id: str) -> Tuple[PhenotypeMention, ...]: ...

    def append_review_event(
        self,
        event: ReviewEvent,
        *,
        expected_latest_event_id: Optional[str],
    ) -> ReviewEvent: ...

    def get_mention_review_history(
        self,
        mention_id: str,
    ) -> Tuple[ReviewEvent, ...]: ...

    def get_case_review_history(self, case_id: str) -> Tuple[ReviewEvent, ...]: ...

    def get_latest_review_event(
        self,
        mention_id: str,
    ) -> Optional[ReviewEvent]: ...

    def get_case_latest_review_events(
        self,
        case_id: str,
    ) -> Mapping[str, ReviewEvent]: ...

    def list_pending_mentions(self, case_id: str) -> Tuple[PhenotypeMention, ...]: ...

    def get_case_data(self, case_id: str) -> CasePhenotypeData: ...


@dataclass(frozen=True)
class _RepositoryState:
    """One immutable snapshot of all repository indexes."""

    cases: Mapping[str, CaseRecord]
    mentions: Mapping[str, PhenotypeMention]
    mention_ids_by_case: Mapping[str, Tuple[str, ...]]
    event_ids_by_mention: Mapping[str, Tuple[str, ...]]
    events_by_id: Mapping[str, ReviewEvent]


class InMemoryPhenotypeReviewRepository:
    """Store cases, immutable mentions, and append-only events in process memory.

    This repository is a local prototype component; its data is not durable
    across process restarts or shared across application workers.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._state = _RepositoryState(
            cases=MappingProxyType({}),
            mentions=MappingProxyType({}),
            mention_ids_by_case=MappingProxyType({}),
            event_ids_by_mention=MappingProxyType({}),
            events_by_id=MappingProxyType({}),
        )

    def create_case(self, case: CaseRecord) -> None:
        """Create an independent case record once."""

        if not isinstance(case, CaseRecord):
            raise TypeError("case must be a CaseRecord")
        with self._lock:
            state = self._state
            if case.case_id in state.cases:
                raise DuplicateIdentifierError(
                    "duplicate phenotype case ID: {}".format(case.case_id)
                )

            cases = dict(state.cases)
            cases[case.case_id] = case
            self._state = _RepositoryState(
                cases=MappingProxyType(cases),
                mentions=state.mentions,
                mention_ids_by_case=state.mention_ids_by_case,
                event_ids_by_mention=state.event_ids_by_mention,
                events_by_id=state.events_by_id,
            )

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        """Return a case record, or ``None`` if it is unknown."""

        with self._lock:
            return self._state.cases.get(case_id)

    def case_exists(self, case_id: str) -> bool:
        """Return whether a case has been explicitly or implicitly created."""

        with self._lock:
            return case_id in self._state.cases

    def add_mention(self, mention: PhenotypeMention) -> None:
        """Add a mention once; existing mentions cannot be replaced."""

        if not isinstance(mention, PhenotypeMention):
            raise TypeError("mention must be a PhenotypeMention")
        with self._lock:
            state = self._state
            if mention.mention_id in state.mentions:
                raise DuplicateIdentifierError(
                    "duplicate phenotype mention ID: {}".format(mention.mention_id)
                )

            mentions = dict(state.mentions)
            mentions[mention.mention_id] = mention
            cases = dict(state.cases)
            cases.setdefault(mention.case_id, CaseRecord(mention.case_id))
            mention_ids_by_case = dict(state.mention_ids_by_case)
            mention_ids_by_case[mention.case_id] = (
                mention_ids_by_case.get(mention.case_id, ()) + (mention.mention_id,)
            )
            event_ids_by_mention = dict(state.event_ids_by_mention)
            event_ids_by_mention[mention.mention_id] = ()

            new_state = _RepositoryState(
                cases=MappingProxyType(cases),
                mentions=MappingProxyType(mentions),
                mention_ids_by_case=MappingProxyType(mention_ids_by_case),
                event_ids_by_mention=MappingProxyType(event_ids_by_mention),
                events_by_id=state.events_by_id,
            )
            self._state = new_state

    def get_mention(self, mention_id: str) -> Optional[PhenotypeMention]:
        """Return an immutable mention snapshot, or ``None`` if absent."""

        with self._lock:
            return self._state.mentions.get(mention_id)

    def list_case_mentions(self, case_id: str) -> Tuple[PhenotypeMention, ...]:
        """Return immutable mention records for a case."""

        with self._lock:
            return tuple(
                self._state.mentions[mention_id]
                for mention_id in self._state.mention_ids_by_case.get(case_id, ())
            )

    def append_review_event(
        self,
        event: ReviewEvent,
        *,
        expected_latest_event_id: Optional[str],
    ) -> ReviewEvent:
        """Append an event if its expected predecessor is still current.

        The first event for a mention must expect ``None`` and have no
        supersession reference. Later events must expect and supersede the
        mention's current latest event.
        """

        if not isinstance(event, ReviewEvent):
            raise TypeError("event must be a ReviewEvent")
        if expected_latest_event_id is not None and (
            not isinstance(expected_latest_event_id, str)
            or not expected_latest_event_id.strip()
        ):
            raise ValueError("expected_latest_event_id must be non-empty or None")

        with self._lock:
            state = self._state
            if event.event_id in state.events_by_id:
                raise DuplicateIdentifierError(
                    "duplicate review event ID: {}".format(event.event_id)
                )
            if event.mention_id not in state.mentions:
                raise InvalidReviewReferenceError(
                    "review event references unknown mention: {}".format(
                        event.mention_id
                    )
                )

            superseded_event = None
            if event.supersedes_event_id is not None:
                superseded_event = state.events_by_id.get(event.supersedes_event_id)
                if superseded_event is None:
                    raise InvalidReviewReferenceError(
                        "superseded review event does not exist: {}".format(
                            event.supersedes_event_id
                        )
                    )
                if superseded_event.mention_id != event.mention_id:
                    raise InvalidReviewReferenceError(
                        "a review event cannot supersede another mention's event"
                    )

            current = self._latest_event_unlocked(event.mention_id)
            current_id = current.event_id if current is not None else None
            if expected_latest_event_id != current_id:
                raise StaleReviewSubmissionError(
                    "expected latest review event {!r}, found {!r}".format(
                        expected_latest_event_id,
                        current_id,
                    )
                )

            if current is None:
                if event.supersedes_event_id is not None:
                    raise InvalidReviewReferenceError(
                        "the first review event cannot supersede another event"
                    )
            else:
                if event.supersedes_event_id != current.event_id:
                    raise InvalidReviewReferenceError(
                        "a new review event must supersede the current latest event"
                    )
                if event.reviewed_at <= current.reviewed_at:
                    raise InvalidReviewReferenceError(
                        "a superseding review event must have a later timestamp"
                    )

            events_by_id = dict(state.events_by_id)
            events_by_id[event.event_id] = event
            event_ids_by_mention = dict(state.event_ids_by_mention)
            event_ids_by_mention[event.mention_id] = (
                event_ids_by_mention[event.mention_id] + (event.event_id,)
            )

            new_state = _RepositoryState(
                cases=state.cases,
                mentions=state.mentions,
                mention_ids_by_case=state.mention_ids_by_case,
                event_ids_by_mention=MappingProxyType(event_ids_by_mention),
                events_by_id=MappingProxyType(events_by_id),
            )
            self._state = new_state
            return event

    def get_mention_review_history(
        self,
        mention_id: str,
    ) -> Tuple[ReviewEvent, ...]:
        """Return the complete append-ordered event history for a mention."""

        with self._lock:
            return tuple(
                self._state.events_by_id[event_id]
                for event_id in self._state.event_ids_by_mention.get(mention_id, ())
            )

    def get_case_review_history(self, case_id: str) -> Tuple[ReviewEvent, ...]:
        """Return the complete case history in timestamp order."""

        with self._lock:
            events = [
                self._state.events_by_id[event_id]
                for mention_id in self._state.mention_ids_by_case.get(case_id, ())
                for event_id in self._state.event_ids_by_mention[mention_id]
            ]
            return tuple(sorted(events, key=lambda event: (event.reviewed_at, event.event_id)))

    def get_latest_review_event(
        self,
        mention_id: str,
    ) -> Optional[ReviewEvent]:
        """Return the latest event for a mention, or ``None`` if it is pending."""

        with self._lock:
            return self._latest_event_unlocked(mention_id)

    def get_case_latest_review_events(
        self,
        case_id: str,
    ) -> Mapping[str, ReviewEvent]:
        """Return a read-only mapping of case mention IDs to their latest events."""

        return latest_review_events(self.get_case_review_history(case_id))

    def list_pending_mentions(self, case_id: str) -> Tuple[PhenotypeMention, ...]:
        """Return case mentions with no review event."""

        with self._lock:
            mentions = self.list_case_mentions(case_id)
            latest = latest_review_events(self.get_case_review_history(case_id))
            return tuple(
                mention
                for mention in mentions
                if is_pending(mention.mention_id, latest)
            )

    def get_case_data(self, case_id: str) -> CasePhenotypeData:
        """Return complete case inputs for ``build_confirmed_positive_set``."""

        with self._lock:
            mentions = self.list_case_mentions(case_id)
            events = self.get_case_review_history(case_id)
            return CasePhenotypeData(
                case_id=case_id,
                mentions=mentions,
                review_events=events,
            )

    def _latest_event_unlocked(self, mention_id: str) -> Optional[ReviewEvent]:
        state = self._state
        event_ids = state.event_ids_by_mention.get(mention_id, ())
        if not event_ids:
            return None
        latest = latest_review_events(
            state.events_by_id[event_id] for event_id in event_ids
        )
        return latest.get(mention_id)
