"""Controlled tests for the phenotype SQLite migration safeguards."""

import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.phenotype import sqlite_migrations


PROJECT_ROOT = Path(r"D:\engineering\major\GeneWhisper")
MAX_DATABASE_BYTES = 4 * 1024 * 1024
MAX_SIDECAR_BYTES = 2 * 1024 * 1024
MAX_MIGRATION_BYTES = 2 * 1024 * 1024
CANONICAL_TIME = "2026-11-20T12:34:56.123456+00:00"


class SQLiteMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="stage4n5k-",
            dir=str(PROJECT_ROOT),
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.test_root = Path(self.temporary_directory.name)
        self.database_directory = self.test_root / "database"
        self.database_directory.mkdir()
        self.database_path = (
            self.database_directory / sqlite_migrations.DATABASE_FILENAME
        )

    def apply_migrations(self, database_directory=None):
        return sqlite_migrations.apply_migrations(
            database_directory or self.database_directory,
            max_database_bytes=MAX_DATABASE_BYTES,
            max_sidecar_bytes=MAX_SIDECAR_BYTES,
            max_migration_bytes=MAX_MIGRATION_BYTES,
        )

    def connect(self, path=None):
        connection = sqlite3.connect(str(path or self.database_path))
        connection.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(connection.close)
        return connection

    def create_history_table(self, connection):
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
        )

    def test_fresh_migration_and_repeated_migration(self):
        self.assertEqual(self.apply_migrations(), self.database_path)
        self.assertEqual(self.apply_migrations(), self.database_path)

        with self.connect() as connection:
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual(versions, [(1,), (2,), (3,)])
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name IN "
                    "('phenotype_mentions', 'review_events', 'review_heads', "
                    "'phenotype_mention_order', 'phenotype_cases')"
                ).fetchone()[0],
                5,
            )

    def test_v3_backfills_distinct_cases_and_preserves_legacy_records(self):
        legacy_directory = self.test_root / "legacy-case-data"
        legacy_directory.mkdir()
        legacy_path = legacy_directory / sqlite_migrations.DATABASE_FILENAME
        migration_directory = Path(sqlite_migrations.__file__).parent / "migrations"
        initial_script = (migration_directory / "001_initial.sql").read_text(
            encoding="utf-8"
        )
        order_script = (migration_directory / "002_mention_order.sql").read_text(
            encoding="utf-8"
        )
        with closing(sqlite3.connect(str(legacy_path))) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for statement in sqlite_migrations._sql_statements(initial_script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at_utc) "
                "VALUES (?, ?)",
                (1, CANONICAL_TIME),
            )
            connection.executemany(
                "INSERT INTO phenotype_mentions "
                "(mention_id, case_id, record_json) VALUES (?, ?, ?)",
                (
                    ("mention-a", "case-z", '{"legacy":1}'),
                    ("mention-b", "case-z", '{"legacy":2}'),
                    ("mention-c", "case-a", '{"legacy":3}'),
                ),
            )
            connection.execute(
                "INSERT INTO review_events "
                "(event_id, mention_id, reviewed_at_utc, supersedes_event_id, "
                "record_json) VALUES (?, ?, ?, ?, ?)",
                ("event-a", "mention-a", CANONICAL_TIME, None, '{"legacy":4}'),
            )
            connection.execute(
                "INSERT INTO review_heads (mention_id, latest_event_id) VALUES (?, ?)",
                ("mention-a", "event-a"),
            )
            for statement in sqlite_migrations._sql_statements(order_script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at_utc) "
                "VALUES (?, ?)",
                (2, CANONICAL_TIME),
            )

        self.apply_migrations(legacy_directory)

        with closing(sqlite3.connect(str(legacy_path))) as connection, connection:
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                [(1,), (2,), (3,)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT case_id FROM phenotype_cases ORDER BY case_id"
                ).fetchall(),
                [("case-a",), ("case-z",)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT mention_id, case_id, record_json "
                    "FROM phenotype_mentions ORDER BY mention_id"
                ).fetchall(),
                [
                    ("mention-a", "case-z", '{"legacy":1}'),
                    ("mention-b", "case-z", '{"legacy":2}'),
                    ("mention-c", "case-a", '{"legacy":3}'),
                ],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT event_id, mention_id, reviewed_at_utc, "
                    "supersedes_event_id, record_json FROM review_events"
                ).fetchall(),
                [
                    (
                        "event-a",
                        "mention-a",
                        CANONICAL_TIME,
                        None,
                        '{"legacy":4}',
                    )
                ],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT mention_id, latest_event_id FROM review_heads"
                ).fetchall(),
                [("mention-a", "event-a")],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT mention_id FROM phenotype_mention_order ORDER BY sequence"
                ).fetchall(),
                [("mention-a",), ("mention-b",), ("mention-c",)],
            )

    def test_missing_and_empty_migration_history_are_rejected(self):
        with self.connect() as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        with self.connect() as connection:
            with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
                sqlite_migrations._read_applied_versions(connection)

        empty_history_path = self.test_root / "empty-history.sqlite3"
        with self.connect(empty_history_path) as connection:
            self.create_history_table(connection)
        with self.connect(empty_history_path) as connection:
            with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
                sqlite_migrations._read_applied_versions(connection)

    def test_malformed_unknown_newer_and_noncontiguous_histories_are_rejected(self):
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE schema_migrations "
                "(version, applied_at_utc TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)",
                ("not-an-integer", CANONICAL_TIME),
            )
        with self.connect() as connection:
            with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
                sqlite_migrations._read_applied_versions(connection)

        unknown_path = self.test_root / "unknown-version.sqlite3"
        with self.connect(unknown_path) as connection:
            self.create_history_table(connection)
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)",
                (4, CANONICAL_TIME),
            )
        with self.connect(unknown_path) as connection:
            with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
                sqlite_migrations._read_applied_versions(connection)

        noncontiguous_path = self.test_root / "noncontiguous.sqlite3"
        with self.connect(noncontiguous_path) as connection:
            self.create_history_table(connection)
            connection.executemany(
                "INSERT INTO schema_migrations VALUES (?, ?)",
                ((1, CANONICAL_TIME), (3, CANONICAL_TIME)),
            )
        migration_map = {1: "001_initial.sql", 2: "002.sql", 3: "003.sql"}
        with self.connect(noncontiguous_path) as connection:
            with mock.patch.object(sqlite_migrations, "_MIGRATIONS", migration_map):
                with self.assertRaisesRegex(
                    sqlite_migrations.SQLiteMigrationError, "not contiguous"
                ):
                    sqlite_migrations._read_applied_versions(connection)

    def test_malformed_migration_timestamps_are_rejected(self):
        invalid_values = (
            "2026-02-30T12:34:56.123456+00:00",
            "2026-11-20T12:34:56.123456+01:00",
            "2026-11-20T12:34:56+00:00",
            "2026-11-20T12:34:56.123+00:00",
            "2026-11-20T12:34:56.123456Z",
        )
        with self.connect() as connection:
            self.create_history_table(connection)
            for value in invalid_values:
                with self.subTest(value=value):
                    connection.execute("DELETE FROM schema_migrations")
                    connection.execute(
                        "INSERT INTO schema_migrations VALUES (?, ?)",
                        (1, value),
                    )
                    with self.assertRaisesRegex(
                        sqlite_migrations.SQLiteMigrationError,
                        "malformed applied_at_utc",
                    ):
                        sqlite_migrations._read_applied_versions(connection)

    def test_schema_tampering_is_rejected(self):
        migration_path = (
            Path(sqlite_migrations.__file__).parent
            / "migrations"
            / "001_initial.sql"
        )
        script = migration_path.read_text(encoding="utf-8")
        self.assertIn("T[0-9][0-9]:", script)
        altered_script = script.replace("T[0-9][0-9]:", "t[0-9][0-9]:", 1)

        tampered_path = self.test_root / "tampered.sqlite3"
        with self.connect(tampered_path) as connection:
            for statement in sqlite_migrations._sql_statements(altered_script):
                connection.execute(statement)
            with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
                sqlite_migrations._validate_schema_objects(connection, (1,))

    def test_foreign_keys_append_only_and_review_chain_constraints(self):
        self.apply_migrations()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO phenotype_cases (case_id) VALUES (?)",
                ("case-1",),
            )
            connection.execute(
                "INSERT INTO phenotype_mentions VALUES (?, ?, ?)",
                ("mention-1", "case-1", "{}"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO phenotype_mentions VALUES (?, ?, ?)",
                    ("mention-unknown-case", "missing-case", "{}"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO review_events VALUES (?, ?, ?, ?, ?)",
                    ("missing-mention-event", "missing", CANONICAL_TIME, None, "{}"),
                )

            root = ("event-1", "mention-1", CANONICAL_TIME, None, "{}")
            connection.execute(
                "INSERT INTO review_events VALUES (?, ?, ?, ?, ?)", root
            )
            connection.execute(
                "INSERT INTO review_heads VALUES (?, ?)", ("mention-1", "event-1")
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO review_events VALUES (?, ?, ?, ?, ?)",
                    ("event-duplicate-root", "mention-1", CANONICAL_TIME, None, "{}"),
                )

            successor = (
                "event-2",
                "mention-1",
                "2026-11-20T12:34:57.123456+00:00",
                "event-1",
                "{}",
            )
            connection.execute(
                "INSERT INTO review_events VALUES (?, ?, ?, ?, ?)", successor
            )
            connection.execute(
                "UPDATE review_heads SET latest_event_id = ? WHERE mention_id = ?",
                ("event-2", "mention-1"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO review_events VALUES (?, ?, ?, ?, ?)",
                    (
                        "event-3-branch",
                        "mention-1",
                        "2026-11-20T12:34:58.123456+00:00",
                        "event-1",
                        "{}",
                    ),
                )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE phenotype_mentions SET case_id = ? WHERE mention_id = ?",
                    ("case-2", "mention-1"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM phenotype_mentions WHERE mention_id = ?",
                    ("mention-1",),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE phenotype_cases SET case_id = ? WHERE case_id = ?",
                    ("renamed-case", "case-1"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM phenotype_cases WHERE case_id = ?",
                    ("case-1",),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE review_events SET record_json = ? WHERE event_id = ?",
                    ('{"changed":true}', "event-1"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM review_events WHERE event_id = ?",
                    ("event-1",),
                )

    def test_page_and_journal_limits_are_applied(self):
        limit_path = self.test_root / "limits.sqlite3"
        with self.connect(limit_path) as connection:
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute("CREATE TABLE payload (data BLOB)")
            sqlite_migrations._set_database_growth_limit(
                connection, 2 * page_size
            )
            max_pages = connection.execute("PRAGMA max_page_count").fetchone()[0]
            self.assertLessEqual(max_pages, 2)

            sqlite_migrations._set_sidecar_growth_limit(
                connection, MAX_SIDECAR_BYTES
            )
            journal_limit = connection.execute(
                "PRAGMA journal_size_limit"
            ).fetchone()[0]
            self.assertGreaterEqual(journal_limit, 0)
            self.assertLessEqual(journal_limit, MAX_SIDECAR_BYTES)

            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO payload VALUES (?)", (b"x" * (1024 * 1024),)
                )
            self.assertLessEqual(
                limit_path.stat().st_size,
                2 * page_size,
            )

        class FakeCursor:
            def __init__(self, value):
                self.value = value

            def fetchone(self):
                return self.value

        class FakeConnection:
            def __init__(self, value):
                self.value = value

            def execute(self, statement):
                return FakeCursor(self.value)

        with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
            sqlite_migrations._set_sidecar_growth_limit(
                FakeConnection((-1,)), MAX_SIDECAR_BYTES
            )

    def test_in_memory_temp_storage_verification_fails_closed(self):
        temp_store = mock.Mock()
        temp_store.fetchone.return_value = (1,)
        connection = mock.Mock()
        connection.execute.side_effect = [
            None,
            temp_store,
        ]

        with self.assertRaisesRegex(
            sqlite_migrations.SQLiteMigrationError,
            "in-memory TEMP storage",
        ):
            sqlite_migrations._set_temporary_storage_to_memory(connection)

    def test_insufficient_space_preflight_does_not_connect(self):
        available = (
            sqlite_migrations.MINIMUM_FREE_BYTES
            + MAX_DATABASE_BYTES
            + MAX_SIDECAR_BYTES
            + MAX_MIGRATION_BYTES
            - 1
        )
        with mock.patch.object(
            sqlite_migrations.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=available),
        ):
            with mock.patch.object(sqlite_migrations.sqlite3, "connect") as connect:
                with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
                    self.apply_migrations()
                connect.assert_not_called()

    def test_failed_first_migration_rolls_back_and_leaves_empty_file(self):
        original_executor = sqlite_migrations._execute_sql_script

        def execute_then_fail(connection, script):
            original_executor(connection, script)
            raise RuntimeError("synthetic migration failure")

        with mock.patch.object(
            sqlite_migrations,
            "_execute_sql_script",
            side_effect=execute_then_fail,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic migration failure"):
                self.apply_migrations()

        self.assertTrue(self.database_path.is_file())
        with self.connect() as connection:
            user_tables = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            self.assertEqual(user_tables, [])

    def test_v2_backfill_failure_rolls_back_only_the_v2_migration(self):
        legacy_directory = self.test_root / "legacy-v1"
        legacy_directory.mkdir()
        legacy_path = legacy_directory / sqlite_migrations.DATABASE_FILENAME
        initial_path = (
            Path(sqlite_migrations.__file__).parent
            / "migrations"
            / "001_initial.sql"
        )
        initial_script = initial_path.read_text(encoding="utf-8")
        with closing(sqlite3.connect(str(legacy_path))) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for statement in sqlite_migrations._sql_statements(initial_script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at_utc) "
                "VALUES (?, ?)",
                (1, CANONICAL_TIME),
            )
            connection.execute(
                "INSERT INTO phenotype_mentions (mention_id, case_id, record_json) "
                "VALUES (?, ?, ?)",
                ("legacy-mention", "case-1", "{}"),
            )

        def execute_through_backfill_then_fail(connection, script):
            for statement in sqlite_migrations._sql_statements(script):
                connection.execute(statement)
                if statement.lstrip().upper().startswith(
                    "INSERT INTO PHENOTYPE_MENTION_ORDER"
                ):
                    raise RuntimeError("synthetic v2 backfill failure")

        with mock.patch.object(
            sqlite_migrations,
            "_execute_sql_script",
            side_effect=execute_through_backfill_then_fail,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic v2 backfill failure",
            ):
                self.apply_migrations(legacy_directory)

        with closing(sqlite3.connect(str(legacy_path))) as connection, connection:
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall(),
                [(1,)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT mention_id, record_json FROM phenotype_mentions"
                ).fetchall(),
                [("legacy-mention", "{}")],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'phenotype_mention_order'"
                ).fetchone()[0],
                0,
            )

    def test_sidecar_symlink_is_rejected_before_connect(self):
        outside_file = self.test_root / "outside-sidecar-target"
        outside_file.write_text("synthetic", encoding="utf-8")
        symlink = self.database_path.with_name(
            self.database_path.name + "-wal"
        )
        try:
            symlink.symlink_to(outside_file)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Windows symlink creation is unavailable: {}".format(error))

        with mock.patch.object(sqlite_migrations.sqlite3, "connect") as connect:
            with self.assertRaises(ValueError):
                self.apply_migrations()
            connect.assert_not_called()

        symlink.unlink()

    def test_resolved_sidecar_outside_directory_is_rejected(self):
        sidecar = self.database_path.with_name(
            self.database_path.name + "-wal"
        )
        sidecar.write_text("synthetic", encoding="utf-8")
        outside_target = self.test_root / "outside-target"
        outside_target.write_text("synthetic", encoding="utf-8")
        original_resolve = Path.resolve

        def resolve_sidecar_outside(path, strict=False):
            if path == sidecar:
                return outside_target
            return original_resolve(path, strict=strict)

        with mock.patch.object(Path, "resolve", new=resolve_sidecar_outside):
            with self.assertRaises(ValueError):
                sqlite_migrations._validate_sidecar_files(
                    self.database_directory,
                    self.database_path,
                )

    def test_nonfile_sidecar_is_rejected_before_connect(self):
        nonfile = self.database_path.with_name(
            self.database_path.name + "-shm"
        )
        nonfile.mkdir()
        with mock.patch.object(sqlite_migrations.sqlite3, "connect") as connect:
            with self.assertRaises(ValueError):
                self.apply_migrations()
            connect.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_sidecar_junction_to_external_directory_is_rejected(self):
        target_directory = self.test_root / "junction-target"
        target_directory.mkdir()
        junction = self.database_path.with_name(
            self.database_path.name + "-wal"
        )
        command = 'mklink /J "{}" "{}"'.format(junction, target_directory)
        result = subprocess.run(
            ["cmd.exe", "/c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("Windows junction creation is unavailable")

        try:
            with mock.patch.object(sqlite_migrations.sqlite3, "connect") as connect:
                with self.assertRaises(ValueError):
                    self.apply_migrations()
                connect.assert_not_called()
        finally:
            if junction.exists():
                os.rmdir(str(junction))

    def test_normalizer_preserves_tokens_comments_and_line_endings(self):
        normalize = sqlite_migrations._normalize_sql
        self.assertEqual(
            normalize("CREATE TABLE t (x TEXT)"),
            normalize("create   table t (x text)"),
        )
        self.assertNotEqual(
            normalize("CREATE TABLE t (x TEXT DEFAULT 'Case  Value')"),
            normalize("CREATE TABLE t (x TEXT DEFAULT 'case  value')"),
        )
        self.assertNotEqual(
            normalize('CREATE TABLE "QuotedName" (x TEXT)'),
            normalize('CREATE TABLE "quotedname" (x TEXT)'),
        )
        self.assertEqual(
            normalize("CREATE TABLE t (x TEXT DEFAULT 'O''Reilly')"),
            normalize("create table t (x text default 'O''Reilly')"),
        )
        self.assertEqual(
            normalize('CREATE TABLE "A""B" (x TEXT)'),
            normalize('create table "A""B" (x text)'),
        )
        self.assertNotEqual(
            normalize("-- note\n token"),
            normalize("-- note token"),
        )
        self.assertNotEqual(
            normalize("CREATE TABLE t (x TEXT /* keep  spaces */)"),
            normalize("CREATE TABLE t (x TEXT /* keep spaces */)"),
        )
        with self.assertRaises(sqlite_migrations.SQLiteMigrationError):
            normalize("CREATE TABLE t (x TEXT /* unterminated")

    def test_import_has_no_database_connection_or_directory_creation(self):
        from importlib import reload

        with mock.patch.object(
            sqlite_migrations.sqlite3,
            "connect",
            side_effect=AssertionError("import attempted a database connection"),
        ):
            with mock.patch.object(
                Path,
                "mkdir",
                side_effect=AssertionError("import attempted directory creation"),
            ):
                reload(sqlite_migrations)


if __name__ == "__main__":
    unittest.main()
