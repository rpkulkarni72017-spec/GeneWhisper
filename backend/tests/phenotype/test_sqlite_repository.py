"""Controlled tests for the durable phenotype SQLite repository."""

import importlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app.phenotype as phenotype_package
from app.phenotype import (
    AssertionStatus,
    CaseRecord,
    DuplicateIdentifierError,
    Experiencer,
    InMemoryPhenotypeReviewRepository,
    InvalidReviewReferenceError,
    PhenotypeMention,
    RepositoryStorageError,
    ReviewDecision,
    ReviewEvent,
    SQLitePhenotypeReviewRepository,
    StaleReviewSubmissionError,
    Temporality,
    phenotype_mention_to_dict,
    review_event_to_dict,
)
from app.phenotype import sqlite_migrations, sqlite_repository


PROJECT_ROOT = Path(r"D:\engineering\major\GeneWhisper")
MAX_DATABASE_BYTES = 4 * 1024 * 1024
MAX_SIDECAR_BYTES = 2 * 1024 * 1024


class SQLitePhenotypeReviewRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="stage4n6c-",
            dir=str(PROJECT_ROOT),
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_directory = Path(self.temporary_directory.name) / "db folder"
        self.database_directory.mkdir()
        self.database_path = (
            self.database_directory / sqlite_migrations.DATABASE_FILENAME
        )
        sqlite_migrations.apply_migrations(
            self.database_directory,
            max_database_bytes=MAX_DATABASE_BYTES,
            max_sidecar_bytes=MAX_SIDECAR_BYTES,
            max_migration_bytes=2 * 1024 * 1024,
        )
        self.repository = self.make_repository()

    def make_repository(
        self,
        database_directory=None,
        *,
        max_database_bytes=MAX_DATABASE_BYTES,
        max_sidecar_bytes=MAX_SIDECAR_BYTES,
    ):
        return SQLitePhenotypeReviewRepository(
            database_directory or self.database_directory,
            max_database_bytes=max_database_bytes,
            max_sidecar_bytes=max_sidecar_bytes,
        )

    def mention(self, mention_id, case_id="case-1"):
        text = "finding"
        return PhenotypeMention(
            mention_id=mention_id,
            case_id=case_id,
            source_document_ref="synthetic-document",
            original_text=text,
            start_offset=0,
            end_offset=len(text),
            phenotype_text=text,
            assertion=AssertionStatus.POSITIVE,
            temporality=Temporality.CURRENT,
            experiencer=Experiencer.PATIENT,
            hpo_version="2026-09-01",
        )

    def event(self, event_id, mention_id, second, supersedes_event_id=None):
        return ReviewEvent(
            event_id=event_id,
            mention_id=mention_id,
            decision=ReviewDecision.DEFERRED,
            reviewer_id="synthetic-reviewer",
            reviewed_at=datetime(
                2026, 11, 20, 12, 0, second, tzinfo=timezone.utc
            ),
            hpo_version="2026-09-01",
            supersedes_event_id=supersedes_event_id,
        )

    def connect(self):
        connection = sqlite3.connect(str(self.database_path))
        connection.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(connection.close)
        return connection

    def create_v1_database(
        self,
        database_directory,
        mentions=(),
        mention_records=(),
    ):
        database_directory.mkdir()
        database_path = database_directory / sqlite_migrations.DATABASE_FILENAME
        initial_script = (
            Path(sqlite_migrations.__file__).parent
            / "migrations"
            / "001_initial.sql"
        ).read_text(encoding="utf-8")
        with closing(sqlite3.connect(str(database_path))) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for statement in sqlite_migrations._sql_statements(initial_script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at_utc) "
                "VALUES (?, ?)",
                (1, "2026-11-20T12:34:56.123456+00:00"),
            )
            legacy_records = [
                (mention_id, "case-1", "{}")
                for mention_id in mentions
            ]
            legacy_records.extend(mention_records)
            connection.executemany(
                "INSERT INTO phenotype_mentions "
                "(mention_id, case_id, record_json) VALUES (?, ?, ?)",
                tuple(legacy_records),
            )
        return database_path

    def test_constructor_does_not_create_or_migrate_database(self):
        missing_directory = Path(self.temporary_directory.name) / "unmigrated"
        missing_directory.mkdir()
        missing_database = (
            missing_directory / sqlite_migrations.DATABASE_FILENAME
        )
        with self.assertRaises(RepositoryStorageError):
            self.make_repository(missing_directory)
        self.assertFalse(missing_database.exists())

        with self.connect() as connection:
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertEqual(versions, [(1,), (2,), (3,)])

        legacy_directory = Path(self.temporary_directory.name) / "legacy-unmigrated"
        legacy_path = self.create_v1_database(legacy_directory)
        with self.assertRaises(RepositoryStorageError):
            self.make_repository(legacy_directory)
        with closing(sqlite3.connect(str(legacy_path))) as connection, connection:
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                [(1,)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'phenotype_mention_order'"
                ).fetchone()[0],
                0,
            )

    def test_delete_journal_mode_is_preserved(self):
        with self.connect() as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "delete",
            )
        self.make_repository()
        with self.connect() as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "delete",
            )

    def test_repository_uses_verified_in_memory_temp_storage(self):
        connection = self.repository._open_connection(check_storage_budget=False)
        try:
            self.assertEqual(
                connection.execute("PRAGMA temp_store").fetchone()[0],
                2,
            )
        finally:
            connection.close()

    def test_repository_enforces_main_database_and_sidecar_budgets(self):
        database_size = self.database_path.stat().st_size
        self.assertGreater(database_size, 1)
        with self.assertRaises(RepositoryStorageError):
            self.make_repository(max_database_bytes=database_size - 1)

        sidecar_path = self.database_path.with_name(
            self.database_path.name + "-journal"
        )
        sidecar_path.write_bytes(b"xx")
        with self.assertRaises(RepositoryStorageError):
            self.make_repository(max_sidecar_bytes=1)

    def test_case_crud_and_implicit_creation_match_in_memory_repository(self):
        memory_repository = InMemoryPhenotypeReviewRepository()
        for repository in (memory_repository, self.repository):
            with self.subTest(repository=type(repository).__name__):
                case = CaseRecord("empty-case")
                self.assertIsNone(repository.get_case(case.case_id))
                self.assertFalse(repository.case_exists(case.case_id))
                repository.create_case(case)
                self.assertEqual(repository.get_case(case.case_id), case)
                self.assertTrue(repository.case_exists(case.case_id))
                self.assertEqual(
                    repository.get_case_data(case.case_id).mentions,
                    (),
                )
                with self.assertRaises(DuplicateIdentifierError):
                    repository.create_case(case)

                implicit_mention = self.mention(
                    "implicit-mention",
                    case_id="implicit-case",
                )
                repository.add_mention(implicit_mention)
                self.assertEqual(
                    repository.get_case("implicit-case"),
                    CaseRecord("implicit-case"),
                )
                self.assertTrue(repository.case_exists("implicit-case"))
                self.assertEqual(
                    repository.get_case_data("implicit-case").mentions,
                    (implicit_mention,),
                )

        with self.assertRaises(ValueError):
            CaseRecord("")
        with self.assertRaises(ValueError):
            CaseRecord("  ")

    def test_implicit_case_creation_rolls_back_if_mention_insert_fails(self):
        original_loader = self.repository._load_snapshot
        calls = {"count": 0}

        def fail_after_insert(connection):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RepositoryStorageError("synthetic post-insert failure")
            return original_loader(connection)

        with mock.patch.object(
            self.repository,
            "_load_snapshot",
            side_effect=fail_after_insert,
        ):
            with self.assertRaises(RepositoryStorageError):
                self.repository.add_mention(
                    self.mention("rolled-back-mention", case_id="rolled-back-case")
                )

        self.assertIsNone(self.repository.get_case("rolled-back-case"))
        self.assertIsNone(self.repository.get_mention("rolled-back-mention"))

    def test_mention_insertion_order_survives_vacuum_and_reopen(self):
        self.repository.add_mention(self.mention("mention-z"))
        self.repository.add_mention(self.mention("mention-a"))
        self.assertEqual(
            [mention.mention_id for mention in self.repository.list_case_mentions("case-1")],
            ["mention-z", "mention-a"],
        )

        with self.connect() as connection:
            connection.execute("VACUUM")

        reopened = self.make_repository()
        self.assertEqual(
            [mention.mention_id for mention in reopened.list_case_mentions("case-1")],
            ["mention-z", "mention-a"],
        )

    def test_protocol_methods_match_in_memory_result_shapes(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        self.assertEqual(self.repository.get_mention("missing"), None)
        self.assertEqual(self.repository.list_case_mentions("missing-case"), ())
        self.assertEqual(self.repository.get_mention_review_history("missing"), ())
        self.assertEqual(self.repository.get_case_review_history("missing-case"), ())
        self.assertEqual(self.repository.get_latest_review_event("missing"), None)
        self.assertEqual(dict(self.repository.get_case_latest_review_events("missing-case")), {})
        self.assertEqual(self.repository.list_pending_mentions("missing-case"), ())
        self.assertEqual(
            self.repository.get_case_data("missing-case").mentions,
            (),
        )
        self.assertEqual(self.repository.get_case_data("missing-case").review_events, ())

        root = self.event("event-1", mention.mention_id, 0)
        self.assertEqual(
            self.repository.append_review_event(
                root,
                expected_latest_event_id=None,
            ),
            root,
        )
        successor = self.event("event-2", mention.mention_id, 1, "event-1")
        self.repository.append_review_event(
            successor,
            expected_latest_event_id="event-1",
        )

        self.assertEqual(
            self.repository.get_mention_review_history(mention.mention_id),
            (root, successor),
        )
        self.assertEqual(
            self.repository.get_case_review_history("case-1"),
            (root, successor),
        )
        self.assertEqual(
            self.repository.get_latest_review_event(mention.mention_id),
            successor,
        )
        latest = self.repository.get_case_latest_review_events("case-1")
        self.assertEqual(dict(latest), {mention.mention_id: successor})
        with self.assertRaises(TypeError):
            latest[mention.mention_id] = root
        self.assertEqual(self.repository.list_pending_mentions("case-1"), ())
        case_data = self.repository.get_case_data("case-1")
        self.assertEqual(case_data.mentions, (mention,))
        self.assertEqual(case_data.review_events, (root, successor))

    def test_domain_errors_and_append_only_records(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        with self.assertRaises(DuplicateIdentifierError):
            self.repository.add_mention(mention)
        with self.assertRaises(InvalidReviewReferenceError):
            self.repository.append_review_event(
                self.event("event-unknown", "missing", 0),
                expected_latest_event_id=None,
            )

        root = self.event("event-1", mention.mention_id, 0)
        self.repository.append_review_event(root, expected_latest_event_id=None)
        with self.assertRaises(DuplicateIdentifierError):
            self.repository.append_review_event(root, expected_latest_event_id=None)
        with self.assertRaises(StaleReviewSubmissionError):
            self.repository.append_review_event(
                self.event("event-stale", mention.mention_id, 1, "event-1"),
                expected_latest_event_id=None,
            )
        with self.assertRaises(InvalidReviewReferenceError):
            self.repository.append_review_event(
                self.event("event-non-successor", mention.mention_id, 1),
                expected_latest_event_id="event-1",
            )

        with self.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE phenotype_mentions SET case_id = ? WHERE mention_id = ?",
                    ("other-case", mention.mention_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM review_events WHERE event_id = ?",
                    ("event-1",),
                )

    def test_inconsistent_head_is_rejected_without_repair(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        self.repository.append_review_event(
            self.event("event-1", mention.mention_id, 0),
            expected_latest_event_id=None,
        )
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM review_heads WHERE mention_id = ?",
                (mention.mention_id,),
            )

        with self.assertRaises(RepositoryStorageError):
            self.repository.get_latest_review_event(mention.mention_id)
        with self.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM review_heads WHERE mention_id = ?",
                    (mention.mention_id,),
                ).fetchone()[0],
                0,
            )

    def test_stale_review_head_is_rejected_without_repair(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        root = self.event("event-1", mention.mention_id, 0)
        successor = self.event("event-2", mention.mention_id, 1, "event-1")
        self.repository.append_review_event(root, expected_latest_event_id=None)
        self.repository.append_review_event(
            successor,
            expected_latest_event_id="event-1",
        )
        with self.connect() as connection:
            connection.execute(
                "UPDATE review_heads SET latest_event_id = ? WHERE mention_id = ?",
                (root.event_id, mention.mention_id),
            )

        with self.assertRaises(RepositoryStorageError):
            self.repository.get_latest_review_event(mention.mention_id)
        with self.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT latest_event_id FROM review_heads WHERE mention_id = ?",
                    (mention.mention_id,),
                ).fetchone()[0],
                root.event_id,
            )

    def test_extra_review_head_is_rejected_without_repair(self):
        first_mention = self.mention("mention-1")
        extra_mention = self.mention("mention-2")
        self.repository.add_mention(first_mention)
        self.repository.add_mention(extra_mention)
        root = self.event("event-1", first_mention.mention_id, 0)
        successor = self.event("event-2", first_mention.mention_id, 1, "event-1")
        self.repository.append_review_event(root, expected_latest_event_id=None)
        self.repository.append_review_event(
            successor,
            expected_latest_event_id="event-1",
        )

        with closing(sqlite3.connect(str(self.database_path))) as connection, connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO review_heads (mention_id, latest_event_id) VALUES (?, ?)",
                (extra_mention.mention_id, root.event_id),
            )

        with self.assertRaises(RepositoryStorageError):
            self.repository.get_latest_review_event(first_mention.mention_id)
        with mock.patch.object(
            sqlite_migrations,
            "validate_existing_schema",
            return_value=(1, 2),
        ):
            with self.assertRaisesRegex(RepositoryStorageError, "review_heads"):
                self.repository.get_latest_review_event(first_mention.mention_id)
        with closing(sqlite3.connect(str(self.database_path))) as connection, connection:
            self.assertEqual(
                connection.execute(
                    "SELECT mention_id, latest_event_id FROM review_heads "
                    "ORDER BY mention_id"
                ).fetchall(),
                [
                    ("mention-1", "event-2"),
                    ("mention-2", "event-1"),
                ],
            )

    def test_corrupt_mention_json_is_rejected(self):
        database_directory = Path(self.temporary_directory.name) / "corrupt-json"
        database_directory.mkdir()
        sqlite_migrations.apply_migrations(
            database_directory,
            max_database_bytes=MAX_DATABASE_BYTES,
            max_sidecar_bytes=MAX_SIDECAR_BYTES,
            max_migration_bytes=2 * 1024 * 1024,
        )
        database_path = database_directory / sqlite_migrations.DATABASE_FILENAME
        with closing(sqlite3.connect(str(database_path))) as connection, connection:
            connection.execute(
                "INSERT INTO phenotype_cases (case_id) VALUES (?)",
                ("case-1",),
            )
            connection.execute(
                "INSERT INTO phenotype_mentions (mention_id, case_id, record_json) "
                "VALUES (?, ?, ?)",
                ("mention-bad-json", "case-1", "{"),
            )

        with self.assertRaises(RepositoryStorageError):
            self.make_repository(database_directory)

    def test_mention_denormalized_columns_must_match_serialized_record(self):
        database_directory = Path(self.temporary_directory.name) / "mention-mismatch"
        database_directory.mkdir()
        sqlite_migrations.apply_migrations(
            database_directory,
            max_database_bytes=MAX_DATABASE_BYTES,
            max_sidecar_bytes=MAX_SIDECAR_BYTES,
            max_migration_bytes=2 * 1024 * 1024,
        )
        database_path = database_directory / sqlite_migrations.DATABASE_FILENAME
        record_json = json.dumps(
            phenotype_mention_to_dict(self.mention("record-mention")),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with closing(sqlite3.connect(str(database_path))) as connection, connection:
            connection.execute(
                "INSERT INTO phenotype_cases (case_id) VALUES (?)",
                ("case-1",),
            )
            connection.execute(
                "INSERT INTO phenotype_mentions (mention_id, case_id, record_json) "
                "VALUES (?, ?, ?)",
                ("column-mention", "case-1", record_json),
            )

        with self.assertRaisesRegex(RepositoryStorageError, "identity"):
            self.make_repository(database_directory)

    def test_event_denormalized_columns_must_match_serialized_record(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        event = self.event("column-event", mention.mention_id, 0)
        serialized = review_event_to_dict(event)
        serialized["event_id"] = "record-event"
        record_json = json.dumps(
            serialized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.connect() as connection:
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

        with self.assertRaisesRegex(RepositoryStorageError, "identity"):
            self.repository.get_latest_review_event(mention.mention_id)

    def test_failed_post_write_validation_rolls_back_event_and_head(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        original_loader = self.repository._load_snapshot
        calls = {"count": 0}

        def fail_after_insert(connection):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RepositoryStorageError("synthetic validation failure")
            return original_loader(connection)

        with mock.patch.object(
            self.repository,
            "_load_snapshot",
            side_effect=fail_after_insert,
        ):
            with self.assertRaises(RepositoryStorageError):
                self.repository.append_review_event(
                    self.event("event-1", mention.mention_id, 0),
                    expected_latest_event_id=None,
                )

        with self.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM review_heads").fetchone()[0],
                0,
            )

    def test_rollback_failure_does_not_replace_primary_sqlite_error(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        primary_error = sqlite3.OperationalError("synthetic primary failure")
        rollback_error = RuntimeError("synthetic rollback failure")
        original_loader = self.repository._load_snapshot
        calls = {"count": 0}

        def fail_after_event_and_head(connection):
            calls["count"] += 1
            if calls["count"] == 2:
                raise primary_error
            return original_loader(connection)

        with mock.patch.object(
            self.repository,
            "_load_snapshot",
            side_effect=fail_after_event_and_head,
        ):
            with mock.patch.object(
                sqlite_repository,
                "_rollback",
                return_value=rollback_error,
            ):
                with self.assertRaises(RepositoryStorageError) as raised:
                    self.repository.append_review_event(
                        self.event("event-1", mention.mention_id, 0),
                        expected_latest_event_id=None,
                    )

        self.assertIs(raised.exception.__cause__, primary_error)
        self.assertIs(raised.exception.rollback_error, rollback_error)
        with self.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM review_heads").fetchone()[0],
                0,
            )

    def test_concurrent_submissions_allow_only_one_successor(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        root = self.event("event-root", mention.mention_id, 0)
        self.repository.append_review_event(root, expected_latest_event_id=None)
        contenders = (
            self.event("event-left", mention.mention_id, 1, root.event_id),
            self.event("event-right", mention.mention_id, 1, root.event_id),
        )
        start = threading.Barrier(2)

        def submit(event):
            start.wait(timeout=10)
            try:
                self.repository.append_review_event(
                    event,
                    expected_latest_event_id=root.event_id,
                )
            except StaleReviewSubmissionError:
                return "stale"
            return "accepted"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(submit, contenders))

        self.assertCountEqual(outcomes, ["accepted", "stale"])
        history = self.repository.get_mention_review_history(mention.mention_id)
        self.assertEqual(len(history), 2)
        self.assertIn(
            self.repository.get_latest_review_event(mention.mention_id).event_id,
            {"event-left", "event-right"},
        )

    def test_read_snapshot_is_consistent_while_writer_is_uncommitted(self):
        mention = self.mention("mention-1")
        self.repository.add_mention(mention)
        reader_loaded = threading.Event()
        release_reader = threading.Event()
        writer_updated = threading.Event()
        writer_loads = {"count": 0}
        original_loader = self.repository._load_snapshot

        def pause_reader_and_observe_writer(connection):
            snapshot = original_loader(connection)
            thread_name = threading.current_thread().name
            if thread_name == "snapshot-reader":
                reader_loaded.set()
                if not release_reader.wait(timeout=10):
                    raise RuntimeError("reader snapshot release timed out")
            elif thread_name == "snapshot-writer":
                writer_loads["count"] += 1
                if writer_loads["count"] == 2:
                    writer_updated.set()
            return snapshot

        def read_case():
            threading.current_thread().name = "snapshot-reader"
            return self.repository.get_case_data("case-1")

        def append_root():
            threading.current_thread().name = "snapshot-writer"
            return self.repository.append_review_event(
                self.event("event-1", mention.mention_id, 0),
                expected_latest_event_id=None,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            with mock.patch.object(
                self.repository,
                "_load_snapshot",
                side_effect=pause_reader_and_observe_writer,
            ):
                reader_future = executor.submit(read_case)
                self.assertTrue(reader_loaded.wait(timeout=10))
                writer_future = executor.submit(append_root)
                try:
                    self.assertTrue(writer_updated.wait(timeout=10))
                finally:
                    release_reader.set()

                reader_snapshot = reader_future.result(timeout=10)
                writer_future.result(timeout=10)

        self.assertEqual(reader_snapshot.mentions, (mention,))
        self.assertEqual(reader_snapshot.review_events, ())
        self.assertEqual(
            self.repository.get_case_data("case-1").review_events,
            (self.event("event-1", mention.mention_id, 0),),
        )

    def test_legacy_order_backfill_is_deterministic_without_rowid_ordering(self):
        database_directory = Path(self.temporary_directory.name) / "legacy"
        database_path = self.create_v1_database(
            database_directory,
            ("mention-z", "mention-a"),
        )

        sqlite_migrations.apply_migrations(
            database_directory,
            max_database_bytes=MAX_DATABASE_BYTES,
            max_sidecar_bytes=MAX_SIDECAR_BYTES,
            max_migration_bytes=2 * 1024 * 1024,
        )

        with closing(sqlite3.connect(str(database_path))) as connection, connection:
            values = connection.execute(
                "SELECT mention_id FROM phenotype_mention_order ORDER BY sequence"
            ).fetchall()
        self.assertEqual(values, [("mention-a",), ("mention-z",)])

    def test_repository_reads_valid_v1_records_after_upgrade(self):
        database_directory = Path(self.temporary_directory.name) / "valid-legacy"
        mention_z = self.mention("mention-z")
        mention_a = self.mention("mention-a")
        mention_records = tuple(
            (
                mention.mention_id,
                mention.case_id,
                json.dumps(
                    phenotype_mention_to_dict(mention),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            for mention in (mention_z, mention_a)
        )
        self.create_v1_database(
            database_directory,
            mention_records=mention_records,
        )
        sqlite_migrations.apply_migrations(
            database_directory,
            max_database_bytes=MAX_DATABASE_BYTES,
            max_sidecar_bytes=MAX_SIDECAR_BYTES,
            max_migration_bytes=2 * 1024 * 1024,
        )

        upgraded = self.make_repository(database_directory)
        self.assertEqual(
            upgraded.list_case_mentions("case-1"),
            (mention_a, mention_z),
        )
        self.assertEqual(upgraded.get_mention("mention-z"), mention_z)

    def test_importing_repository_and_package_has_no_database_side_effects(self):
        with mock.patch.object(
            sqlite3,
            "connect",
            side_effect=AssertionError("import attempted a database connection"),
        ):
            with mock.patch.object(
                Path,
                "mkdir",
                side_effect=AssertionError("import attempted directory creation"),
            ):
                with mock.patch.object(
                    sqlite_migrations,
                    "apply_migrations",
                ) as apply_migrations:
                    importlib.reload(sqlite_repository)
                    importlib.reload(phenotype_package)
                    apply_migrations.assert_not_called()


if __name__ == "__main__":
    unittest.main()
