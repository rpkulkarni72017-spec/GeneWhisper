"""Durable SQLite implementation of the phenotype review repository."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Optional, Tuple, Union

from . import sqlite_migrations
from .repository import (
    CasePhenotypeData,
    DuplicateIdentifierError,
    InvalidReviewReferenceError,
    RepositoryStorageError,
    StaleReviewSubmissionError,
)
from .review import is_pending, latest_review_events
from .schemas import CaseRecord, PhenotypeMention, ReviewEvent
from .serialization import (
    phenotype_mention_from_dict,
    phenotype_mention_to_dict,
    review_event_from_dict,
    review_event_to_dict,
)


@dataclass(frozen=True)
class _DatabaseSnapshot:
    cases: Mapping[str, CaseRecord]
    mentions: Tuple[PhenotypeMention, ...]
    mentions_by_id: Mapping[str, PhenotypeMention]
    events: Tuple[ReviewEvent, ...]
    events_by_id: Mapping[str, ReviewEvent]
    events_by_mention: Mapping[str, Tuple[ReviewEvent, ...]]
    latest_events: Mapping[str, ReviewEvent]


class SQLitePhenotypeReviewRepository:
    """Persist cases, mentions, and review records in an explicitly migrated DB.

    The adapter never creates a database or runs migrations. Call
    ``sqlite_migrations.apply_migrations`` explicitly before constructing it.

    SQLite TEMP tables and indexes are forced to memory and verified on each
    connection. This prevents disk-backed SQLite TEMP sorting/materialization,
    but does not bound process memory or provide a filesystem-wide growth cap.
    """

    def __init__(
        self,
        database_directory: Union[str, Path],
        *,
        max_database_bytes: int,
        max_sidecar_bytes: int,
    ) -> None:
        for name, value in (
            ("max_database_bytes", max_database_bytes),
            ("max_sidecar_bytes", max_sidecar_bytes),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("{} must be a positive integer".format(name))

        self._configured_directory = Path(database_directory)
        self._database_directory = sqlite_migrations.validate_database_directory(
            self._configured_directory
        )
        self._database_path = (
            self._database_directory / sqlite_migrations.DATABASE_FILENAME
        )
        self._max_database_bytes = max_database_bytes
        self._max_sidecar_bytes = max_sidecar_bytes

        with self._transaction(write=False, check_storage_budget=True):
            pass

    def create_case(self, case: CaseRecord) -> None:
        """Create an independent case record once."""

        if not isinstance(case, CaseRecord):
            raise TypeError("case must be a CaseRecord")
        with self._transaction(write=True) as (connection, snapshot):
            if case.case_id in snapshot.cases:
                raise DuplicateIdentifierError(
                    "duplicate phenotype case ID: {}".format(case.case_id)
                )
            connection.execute(
                "INSERT INTO phenotype_cases (case_id) VALUES (?)",
                (case.case_id,),
            )
            self._load_snapshot(connection)

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        """Return a case record, or ``None`` if it is unknown."""

        with self._transaction(write=False) as (_, snapshot):
            return snapshot.cases.get(case_id)

    def case_exists(self, case_id: str) -> bool:
        """Return whether a case has been explicitly or implicitly created."""

        with self._transaction(write=False) as (_, snapshot):
            return case_id in snapshot.cases

    def add_mention(self, mention: PhenotypeMention) -> None:
        """Add a mention once; its ordering record is inserted by SQL trigger."""

        if not isinstance(mention, PhenotypeMention):
            raise TypeError("mention must be a PhenotypeMention")
        serialized = phenotype_mention_to_dict(mention)
        record_json = _encode_json(serialized)

        with self._transaction(write=True) as (connection, snapshot):
            if mention.mention_id in snapshot.mentions_by_id:
                raise DuplicateIdentifierError(
                    "duplicate phenotype mention ID: {}".format(mention.mention_id)
                )
            if mention.case_id not in snapshot.cases:
                connection.execute(
                    "INSERT INTO phenotype_cases (case_id) VALUES (?)",
                    (mention.case_id,),
                )
            connection.execute(
                "INSERT INTO phenotype_mentions "
                "(mention_id, case_id, record_json) VALUES (?, ?, ?)",
                (mention.mention_id, mention.case_id, record_json),
            )
            self._load_snapshot(connection)

    def get_mention(self, mention_id: str) -> Optional[PhenotypeMention]:
        """Return a mention, or ``None`` if it is absent."""

        with self._transaction(write=False) as (_, snapshot):
            return snapshot.mentions_by_id.get(mention_id)

    def list_case_mentions(self, case_id: str) -> Tuple[PhenotypeMention, ...]:
        """Return case mentions in their durable insertion order."""

        with self._transaction(write=False) as (_, snapshot):
            return tuple(
                mention for mention in snapshot.mentions if mention.case_id == case_id
            )

    def append_review_event(
        self,
        event: ReviewEvent,
        *,
        expected_latest_event_id: Optional[str],
    ) -> ReviewEvent:
        """Append one event and advance its head atomically."""

        if not isinstance(event, ReviewEvent):
            raise TypeError("event must be a ReviewEvent")
        if expected_latest_event_id is not None and (
            not isinstance(expected_latest_event_id, str)
            or not expected_latest_event_id.strip()
        ):
            raise ValueError("expected_latest_event_id must be non-empty or None")

        with self._transaction(write=True) as (connection, snapshot):
            if event.event_id in snapshot.events_by_id:
                raise DuplicateIdentifierError(
                    "duplicate review event ID: {}".format(event.event_id)
                )
            if event.mention_id not in snapshot.mentions_by_id:
                raise InvalidReviewReferenceError(
                    "review event references unknown mention: {}".format(
                        event.mention_id
                    )
                )

            superseded_event = None
            if event.supersedes_event_id is not None:
                superseded_event = snapshot.events_by_id.get(
                    event.supersedes_event_id
                )
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

            current = snapshot.latest_events.get(event.mention_id)
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

            serialized = review_event_to_dict(event)
            record_json = _encode_json(serialized)
            connection.execute(
                "INSERT INTO review_events "
                "(event_id, mention_id, reviewed_at_utc, supersedes_event_id, "
                "record_json) VALUES (?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.mention_id,
                    serialized["reviewed_at"],
                    event.supersedes_event_id,
                    record_json,
                ),
            )

            if current is None:
                connection.execute(
                    "INSERT INTO review_heads (mention_id, latest_event_id) "
                    "VALUES (?, ?)",
                    (event.mention_id, event.event_id),
                )
            else:
                cursor = connection.execute(
                    "UPDATE review_heads SET latest_event_id = ? "
                    "WHERE mention_id = ? AND latest_event_id = ?",
                    (event.event_id, event.mention_id, current.event_id),
                )
                if cursor.rowcount != 1:
                    raise StaleReviewSubmissionError(
                        "expected latest review event {!r}, found {!r}".format(
                            expected_latest_event_id,
                            current.event_id,
                        )
                    )

            self._load_snapshot(connection)
            return event

    def get_mention_review_history(
        self,
        mention_id: str,
    ) -> Tuple[ReviewEvent, ...]:
        """Return the complete append-ordered event history for a mention."""

        with self._transaction(write=False) as (_, snapshot):
            return snapshot.events_by_mention.get(mention_id, ())

    def get_case_review_history(self, case_id: str) -> Tuple[ReviewEvent, ...]:
        """Return case events in timestamp and event-ID order."""

        with self._transaction(write=False) as (_, snapshot):
            return tuple(
                event
                for event in snapshot.events
                if snapshot.mentions_by_id[event.mention_id].case_id == case_id
            )

    def get_latest_review_event(
        self,
        mention_id: str,
    ) -> Optional[ReviewEvent]:
        """Return the latest event, or ``None`` if the mention is pending."""

        with self._transaction(write=False) as (_, snapshot):
            return snapshot.latest_events.get(mention_id)

    def get_case_latest_review_events(
        self,
        case_id: str,
    ) -> Mapping[str, ReviewEvent]:
        """Return a read-only mapping of case mention IDs to latest events."""

        with self._transaction(write=False) as (_, snapshot):
            case_events = (
                event
                for event in snapshot.events
                if snapshot.mentions_by_id[event.mention_id].case_id == case_id
            )
            return latest_review_events(case_events)

    def list_pending_mentions(self, case_id: str) -> Tuple[PhenotypeMention, ...]:
        """Return case mentions with no review event, in insertion order."""

        with self._transaction(write=False) as (_, snapshot):
            case_mentions = tuple(
                mention for mention in snapshot.mentions if mention.case_id == case_id
            )
            case_events = (
                event
                for event in snapshot.events
                if snapshot.mentions_by_id[event.mention_id].case_id == case_id
            )
            latest = latest_review_events(case_events)
            return tuple(
                mention
                for mention in case_mentions
                if is_pending(mention.mention_id, latest)
            )

    def get_case_data(self, case_id: str) -> CasePhenotypeData:
        """Return a consistent, complete case snapshot."""

        with self._transaction(write=False) as (_, snapshot):
            mentions = tuple(
                mention for mention in snapshot.mentions if mention.case_id == case_id
            )
            events = tuple(
                event
                for event in snapshot.events
                if snapshot.mentions_by_id[event.mention_id].case_id == case_id
            )
            return CasePhenotypeData(
                case_id=case_id,
                mentions=mentions,
                review_events=events,
            )

    @contextmanager
    def _transaction(
        self,
        *,
        write: bool,
        check_storage_budget: bool = False,
    ) -> Iterator[Tuple[sqlite3.Connection, _DatabaseSnapshot]]:
        connection = self._open_connection(
            check_storage_budget=write or check_storage_budget
        )
        primary_error = None
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            snapshot = self._load_snapshot(connection)
            yield connection, snapshot
            if write:
                self._check_storage_budget()
            connection.commit()
        except BaseException as error:
            primary_error = error
            rollback_error = _rollback(connection)
            if rollback_error is not None:
                _attach_secondary_error(
                    error,
                    rollback_error,
                    "SQLite rollback also failed",
                )
            if isinstance(error, sqlite3.Error):
                wrapped = RepositoryStorageError("SQLite repository operation failed")
                if rollback_error is not None:
                    _attach_secondary_error(
                        wrapped,
                        rollback_error,
                        "SQLite rollback also failed",
                    )
                primary_error = wrapped
                raise wrapped from error
            if isinstance(error, sqlite_migrations.SQLiteMigrationError):
                wrapped = RepositoryStorageError(
                    "the SQLite database is not compatible with this repository"
                )
                if rollback_error is not None:
                    _attach_secondary_error(
                        wrapped,
                        rollback_error,
                        "SQLite rollback also failed",
                    )
                primary_error = wrapped
                raise wrapped from error
            raise
        finally:
            try:
                connection.close()
            except sqlite3.Error as close_error:
                if primary_error is None:
                    raise RepositoryStorageError(
                        "could not close the SQLite repository connection"
                    ) from close_error
                _attach_secondary_error(
                    primary_error,
                    close_error,
                    "SQLite connection close also failed",
                )

    def _open_connection(
        self,
        *,
        check_storage_budget: bool,
    ) -> sqlite3.Connection:
        try:
            directory, database_path = (
                sqlite_migrations.validate_existing_database_paths(
                    self._configured_directory
                )
            )
            if (
                directory != self._database_directory
                or database_path != self._database_path
            ):
                raise ValueError(
                    "the configured database directory resolved to a different path"
                )
            sqlite_migrations.validate_delete_journal_sidecars(
                directory,
                database_path,
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            sqlite_migrations.SQLiteMigrationError,
        ) as error:
            raise RepositoryStorageError(
                "the configured database or sidecar paths are not safe"
            ) from error

        if check_storage_budget:
            self._check_storage_budget()

        database_uri = database_path.as_uri() + "?mode=rw"
        try:
            connection = sqlite3.connect(
                database_uri,
                uri=True,
                timeout=30.0,
                isolation_level=None,
            )
        except sqlite3.Error as error:
            raise RepositoryStorageError(
                "could not open the existing phenotype database"
            ) from error

        connection.row_factory = sqlite3.Row
        try:
            sqlite_migrations.configure_repository_connection(
                connection,
                max_database_bytes=self._max_database_bytes,
                max_sidecar_bytes=self._max_sidecar_bytes,
            )
        except sqlite_migrations.SQLiteMigrationError as error:
            _close_preserving_error(connection, error)
            raise RepositoryStorageError(
                "could not configure the phenotype database safely"
            ) from error
        except sqlite3.Error as error:
            _close_preserving_error(connection, error)
            raise RepositoryStorageError(
                "could not configure the phenotype database safely"
            ) from error
        return connection

    def _check_storage_budget(self) -> None:
        try:
            sqlite_migrations.check_repository_storage_budget(
                self._database_directory,
                self._database_path,
                self._max_database_bytes,
                self._max_sidecar_bytes,
            )
        except (sqlite_migrations.SQLiteMigrationError, OSError, ValueError) as error:
            raise RepositoryStorageError(
                "the configured database budgets or D: free-space reserve are not satisfied"
            ) from error

    @staticmethod
    def _load_snapshot(connection: sqlite3.Connection) -> _DatabaseSnapshot:
        sqlite_migrations.validate_existing_schema(connection)

        case_rows = connection.execute(
            "SELECT case_id FROM phenotype_cases"
        ).fetchall()
        cases = {}
        for row in case_rows:
            try:
                case = CaseRecord(row["case_id"])
            except (TypeError, ValueError) as error:
                raise RepositoryStorageError(
                    "a stored phenotype case has an invalid case ID"
                ) from error
            if case.case_id in cases:
                _raise_corrupt_data("the database contains duplicate case IDs")
            cases[case.case_id] = case

        mention_rows = connection.execute(
            "SELECT mention.mention_id, mention.case_id, mention.record_json, "
            "mention_order.sequence "
            "FROM phenotype_mentions AS mention "
            "LEFT JOIN phenotype_mention_order AS mention_order "
            "ON mention_order.mention_id = mention.mention_id"
        ).fetchall()
        mentions_with_order = []
        mentions_by_id = {}
        for row in mention_rows:
            if type(row["sequence"]) is not int or row["sequence"] <= 0:
                _raise_corrupt_data("a phenotype mention has no valid order record")
            mention = _decode_mention(row)
            if mention.case_id not in cases:
                _raise_corrupt_data(
                    "a phenotype mention references an unknown case"
                )
            if mention.mention_id in mentions_by_id:
                _raise_corrupt_data("the database contains duplicate mention IDs")
            mentions_with_order.append((row["sequence"], mention))
            mentions_by_id[mention.mention_id] = mention

        order_count = connection.execute(
            "SELECT COUNT(*) FROM phenotype_mention_order"
        ).fetchone()[0]
        if order_count != len(mentions_with_order):
            _raise_corrupt_data(
                "phenotype mention ordering rows do not match stored mentions"
            )

        mentions = tuple(
            mention
            for _, mention in sorted(
                mentions_with_order,
                key=lambda item: item[0],
            )
        )
        event_rows = connection.execute(
            "SELECT event_id, mention_id, reviewed_at_utc, "
            "supersedes_event_id, record_json "
            "FROM review_events"
        ).fetchall()
        events = []
        events_by_id = {}
        events_by_mention = {}
        for row in event_rows:
            event = _decode_event(row)
            if event.event_id in events_by_id:
                _raise_corrupt_data("the database contains duplicate event IDs")
            if event.mention_id not in mentions_by_id:
                _raise_corrupt_data(
                    "a review event references a missing phenotype mention"
                )
            events.append(event)
            events_by_id[event.event_id] = event
            events_by_mention.setdefault(event.mention_id, []).append(event)

        for mention_id, mention_events in events_by_mention.items():
            roots = [
                event
                for event in mention_events
                if event.supersedes_event_id is None
            ]
            successors = {}
            for event in mention_events:
                predecessor_id = event.supersedes_event_id
                if predecessor_id is None:
                    continue
                if predecessor_id in successors:
                    _raise_corrupt_data(
                        "a review event has multiple successors for mention {!r}".format(
                            mention_id
                        )
                    )
                successors[predecessor_id] = event.event_id
            if len(roots) != 1:
                _raise_corrupt_data(
                    "review history for mention {!r} must have exactly one root".format(
                        mention_id
                    )
                )

        try:
            latest = latest_review_events(events)
        except (TypeError, ValueError) as error:
            raise RepositoryStorageError(
                "the authoritative review event log is corrupt"
            ) from error

        head_rows = connection.execute(
            "SELECT mention_id, latest_event_id FROM review_heads"
        ).fetchall()
        heads = {row["mention_id"]: row["latest_event_id"] for row in head_rows}
        expected_heads = {
            mention_id: event.event_id for mention_id, event in latest.items()
        }
        if heads != expected_heads:
            _raise_corrupt_data(
                "review_heads does not match the complete authoritative event log"
            )

        ordered_events = tuple(
            sorted(events, key=lambda event: (event.reviewed_at, event.event_id))
        )
        ordered_events_by_mention = {
            mention_id: tuple(
                sorted(
                    mention_events,
                    key=lambda event: (event.reviewed_at, event.event_id),
                )
            )
            for mention_id, mention_events in events_by_mention.items()
        }
        return _DatabaseSnapshot(
            cases=cases,
            mentions=mentions,
            mentions_by_id=mentions_by_id,
            events=ordered_events,
            events_by_id=events_by_id,
            events_by_mention=ordered_events_by_mention,
            latest_events=latest,
        )


def _encode_json(record: Mapping[str, object]) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_mention(row: sqlite3.Row) -> PhenotypeMention:
    try:
        mention = phenotype_mention_from_dict(json.loads(row["record_json"]))
    except (TypeError, ValueError) as error:
        raise RepositoryStorageError(
            "a stored phenotype mention cannot be deserialized"
        ) from error
    if mention.mention_id != row["mention_id"] or mention.case_id != row["case_id"]:
        _raise_corrupt_data(
            "stored phenotype mention identity does not match its SQL columns"
        )
    return mention


def _decode_event(row: sqlite3.Row) -> ReviewEvent:
    try:
        event = review_event_from_dict(json.loads(row["record_json"]))
    except (TypeError, ValueError) as error:
        raise RepositoryStorageError(
            "a stored review event cannot be deserialized"
        ) from error
    if (
        event.event_id != row["event_id"]
        or event.mention_id != row["mention_id"]
        or event.supersedes_event_id != row["supersedes_event_id"]
        or review_event_to_dict(event)["reviewed_at"] != row["reviewed_at_utc"]
    ):
        _raise_corrupt_data(
            "stored review event identity does not match its SQL columns"
        )
    return event


def _raise_corrupt_data(message: str) -> None:
    cause = ValueError(message)
    raise RepositoryStorageError(message) from cause


def _rollback(connection: sqlite3.Connection) -> Optional[BaseException]:
    try:
        if connection.in_transaction:
            connection.rollback()
    except BaseException as error:
        return error
    return None


def _close_preserving_error(
    connection: sqlite3.Connection,
    primary: BaseException,
) -> None:
    try:
        connection.close()
    except sqlite3.Error as close_error:
        _attach_secondary_error(
            primary,
            close_error,
            "SQLite connection close also failed",
        )


def _attach_secondary_error(
    primary: BaseException,
    secondary: BaseException,
    note: str,
) -> None:
    try:
        attribute = "rollback_error" if "rollback" in note.lower() else "secondary_error"
        setattr(primary, attribute, secondary)
    except (AttributeError, TypeError):
        pass
    add_note = getattr(primary, "add_note", None)
    if add_note is not None:
        add_note("{}: {!r}".format(note, secondary))
