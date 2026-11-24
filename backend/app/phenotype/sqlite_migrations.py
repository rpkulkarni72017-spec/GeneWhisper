"""Explicit, versioned SQLite migrations and safeguards for phenotype storage.

The SQLite repository appends review events and updates ``review_heads`` in one
transaction. It validates every head against the complete authoritative event
log before reads and after writes, rejecting inconsistencies without repair.
"""

import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .serialization import _parse_utc_datetime

DATABASE_FILENAME = "phenotype-review.sqlite3"
MINIMUM_FREE_BYTES = 20 * 1024 * 1024 * 1024
_MIGRATIONS: Dict[int, str] = {
    1: "001_initial.sql",
    2: "002_mention_order.sql",
    3: "003_phenotype_cases.sql",
}
_EXPECTED_COLUMNS = {
    "schema_migrations": (
        ("version", "INTEGER", 0, 1),
        ("applied_at_utc", "TEXT", 1, 0),
    ),
    "phenotype_mentions": (
        ("mention_id", "TEXT", 1, 1),
        ("case_id", "TEXT", 1, 0),
        ("record_json", "TEXT", 1, 0),
    ),
    "review_events": (
        ("event_id", "TEXT", 1, 1),
        ("mention_id", "TEXT", 1, 0),
        ("reviewed_at_utc", "TEXT", 1, 0),
        ("supersedes_event_id", "TEXT", 0, 0),
        ("record_json", "TEXT", 1, 0),
    ),
    "review_heads": (
        ("mention_id", "TEXT", 1, 1),
        ("latest_event_id", "TEXT", 1, 0),
    ),
}
_EXPECTED_COLUMNS_BY_VERSION = {
    2: {
        "phenotype_mention_order": (
            ("sequence", "INTEGER", 0, 1),
            ("mention_id", "TEXT", 1, 0),
        ),
    },
    3: {
        "phenotype_cases": (
            ("case_id", "TEXT", 1, 1),
        ),
    },
}
_EXPECTED_FOREIGN_KEYS = {
    "schema_migrations": (),
    "phenotype_mentions": (),
    "review_events": (
        ("phenotype_mentions", "mention_id", "mention_id", "RESTRICT"),
        ("review_events", "mention_id", "mention_id", "RESTRICT"),
        ("review_events", "supersedes_event_id", "event_id", "RESTRICT"),
    ),
    "review_heads": (
        ("phenotype_mentions", "mention_id", "mention_id", "RESTRICT"),
        ("review_events", "mention_id", "mention_id", "RESTRICT"),
        ("review_events", "latest_event_id", "event_id", "RESTRICT"),
    ),
}
_EXPECTED_FOREIGN_KEYS_BY_VERSION = {
    2: {
        "phenotype_mention_order": (
            ("phenotype_mentions", "mention_id", "mention_id", "RESTRICT"),
        ),
    },
    3: {
        "phenotype_cases": (),
    },
}
_EXPECTED_INDEXES = {
    "idx_phenotype_mentions_case": (
        "phenotype_mentions",
        False,
        False,
        ("case_id", "mention_id"),
    ),
    "idx_review_events_mention_time": (
        "review_events",
        False,
        False,
        ("mention_id", "reviewed_at_utc"),
    ),
    "idx_review_events_one_root": (
        "review_events",
        True,
        True,
        ("mention_id",),
    ),
    "idx_review_events_one_successor": (
        "review_events",
        True,
        True,
        ("mention_id", "supersedes_event_id"),
    ),
}


class SQLiteMigrationError(RuntimeError):
    """Raised when a configured SQLite database cannot be migrated safely."""


def validate_database_directory(
    database_directory: Union[str, os.PathLike],
) -> Path:
    """Return an existing resolved D: directory, rejecting off-volume redirects."""

    try:
        configured_path = Path(database_directory)
    except (TypeError, ValueError) as error:
        raise ValueError("database_directory must be an explicit filesystem path") from error
    if not configured_path.is_absolute():
        raise ValueError("database_directory must be an absolute path on D:")
    if os.path.normcase(configured_path.drive) != os.path.normcase("D:"):
        raise ValueError("database_directory must be configured on D:")

    try:
        resolved_path = configured_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(
            "database_directory must already exist and resolve to an accessible path"
        ) from error
    if not resolved_path.is_dir():
        raise ValueError("database_directory must be an existing directory")
    if os.path.normcase(resolved_path.drive) != os.path.normcase("D:"):
        raise ValueError(
            "database_directory resolves outside the approved D: location"
        )

    try:
        approved_root = Path("D:/").resolve(strict=True)
        common_path = os.path.commonpath(
            (os.path.normcase(str(approved_root)), os.path.normcase(str(resolved_path)))
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("the approved D: location is not accessible") from error
    if common_path != os.path.normcase(str(approved_root)):
        raise ValueError("database_directory resolves outside the approved D: location")
    if resolved_path == approved_root:
        raise ValueError("database_directory must be below the D: drive root")

    return resolved_path


def apply_migrations(
    database_directory: Union[str, os.PathLike],
    *,
    max_database_bytes: int,
    max_sidecar_bytes: int,
    max_migration_bytes: int,
) -> Path:
    """Apply migrations within configured growth budgets on an existing D: directory.

    The page limit hard-caps the main database file. SQLite's journal-size limit
    applies after a transaction or checkpoint, not to active WAL/journal growth;
    migration temporary-file growth is therefore a preflight allowance only.
    SQLite TEMP tables and indexes use verified in-memory storage, but their
    process-memory use is not bounded.
    Free-space checks are not reservations and cannot constrain other processes.

    Connecting to a missing database file creates it. If its first migration
    fails, SQLite rolls back the schema and version row but leaves that empty,
    unversioned file and the configured page/journal limits in place. Existing
    files are never removed.
    """

    for name, value in (
        ("max_database_bytes", max_database_bytes),
        ("max_sidecar_bytes", max_sidecar_bytes),
        ("max_migration_bytes", max_migration_bytes),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError("{} must be a positive integer".format(name))

    directory = validate_database_directory(database_directory)
    database_path = directory / DATABASE_FILENAME
    _validate_database_file(database_path)
    _check_storage_budget(
        directory,
        database_path,
        max_database_bytes,
        max_sidecar_bytes,
        max_migration_bytes,
    )
    validate_delete_journal_sidecars(directory, database_path)

    try:
        connection = sqlite3.connect(
            str(database_path),
            timeout=30.0,
            isolation_level=None,
        )
    except sqlite3.Error as error:
        raise SQLiteMigrationError("could not open the configured SQLite database") from error

    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
        if (
            journal_mode is None
            or not isinstance(journal_mode[0], str)
            or journal_mode[0].lower() != "delete"
        ):
            raise SQLiteMigrationError(
                "phenotype migrations require rollback-journal DELETE mode"
            )

        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys_enabled = connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()
        if foreign_keys_enabled is None or foreign_keys_enabled[0] != 1:
            raise SQLiteMigrationError("SQLite foreign-key enforcement is unavailable")

        if sqlite3.sqlite_version_info < (3, 8, 0):
            raise SQLiteMigrationError(
                "SQLite 3.8.0 or newer is required for partial unique indexes"
            )

        _set_temporary_storage_to_memory(connection)
        applied_versions = _read_applied_versions(connection)
        if applied_versions:
            _validate_schema_objects(connection, applied_versions)
        _set_database_growth_limit(connection, max_database_bytes)
        _set_sidecar_growth_limit(connection, max_sidecar_bytes)

        for version in sorted(_MIGRATIONS):
            if version in applied_versions:
                continue
            _check_storage_budget(
                directory,
                database_path,
                max_database_bytes,
                max_sidecar_bytes,
                max_migration_bytes,
            )
            _apply_one_migration(connection, version)
            applied_versions = tuple(sorted(applied_versions + (version,)))

        _validate_schema_objects(connection, applied_versions)
        return database_path
    except SQLiteMigrationError:
        raise
    except sqlite3.Error as error:
        raise SQLiteMigrationError("SQLite migration failed") from error
    finally:
        connection.close()


def validate_existing_database_paths(
    database_directory: Union[str, os.PathLike],
) -> Tuple[Path, Path]:
    """Validate an existing database and its sidecars without opening them."""

    directory = validate_database_directory(database_directory)
    database_path = directory / DATABASE_FILENAME
    _validate_database_file(database_path)
    if not database_path.is_file():
        raise FileNotFoundError(
            "the configured phenotype database must already exist: {}".format(
                database_path
            )
        )
    _validate_sidecar_files(directory, database_path)
    return directory, database_path


def validate_delete_journal_sidecars(
    directory: Path,
    database_path: Path,
) -> None:
    """Validate sidecar targets and reject WAL files without opening SQLite."""

    _validate_sidecar_files(directory, database_path)
    for suffix in ("-wal", "-shm"):
        sidecar_path = database_path.with_name(database_path.name + suffix)
        if sidecar_path.exists() or sidecar_path.is_symlink():
            raise SQLiteMigrationError(
                "rollback-journal mode does not permit an existing {} sidecar".format(
                    suffix
                )
            )


def validate_existing_schema(connection: sqlite3.Connection) -> Tuple[int, ...]:
    """Validate, without migrating, that a connection has the latest schema."""

    applied_versions = _read_applied_versions(connection)
    latest_versions = tuple(sorted(_MIGRATIONS))
    if applied_versions != latest_versions:
        raise SQLiteMigrationError(
            "the latest phenotype schema is required; found {!r}, expected {!r}".format(
                applied_versions,
                latest_versions,
            )
        )
    _validate_schema_objects(connection, applied_versions)
    return applied_versions


def check_repository_storage_budget(
    directory: Path,
    database_path: Path,
    max_database_bytes: int,
    max_sidecar_bytes: int,
) -> None:
    """Preflight ordinary repository growth without a migration temp allowance."""

    _check_storage_budget(
        directory,
        database_path,
        max_database_bytes,
        max_sidecar_bytes,
        0,
    )


def configure_repository_connection(
    connection: sqlite3.Connection,
    *,
    max_database_bytes: int,
    max_sidecar_bytes: int,
) -> None:
    """Apply per-connection safeguards without changing journal mode.

    SQLite TEMP tables and indexes are forced to memory and their setting is
    verified. This avoids disk-backed TEMP files, but does not cap memory use.
    """

    if sqlite3.sqlite_version_info < (3, 8, 0):
        raise SQLiteMigrationError(
            "SQLite 3.8.0 or newer is required for partial unique indexes"
        )

    connection.execute("PRAGMA foreign_keys = ON")
    foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys_enabled is None or foreign_keys_enabled[0] != 1:
        raise SQLiteMigrationError("SQLite foreign-key enforcement is unavailable")

    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
    if (
        journal_mode is None
        or not isinstance(journal_mode[0], str)
        or journal_mode[0].lower() != "delete"
    ):
        raise SQLiteMigrationError(
            "the phenotype repository requires rollback-journal DELETE mode"
        )

    _set_temporary_storage_to_memory(connection)

    _set_database_growth_limit(connection, max_database_bytes)
    _set_sidecar_growth_limit(connection, max_sidecar_bytes)


def _set_temporary_storage_to_memory(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("PRAGMA temp_store = MEMORY")
        temp_store = connection.execute("PRAGMA temp_store").fetchone()
    except sqlite3.Error as error:
        raise SQLiteMigrationError(
            "SQLite could not configure in-memory TEMP storage"
        ) from error
    if (
        temp_store is None
        or type(temp_store[0]) is not int
        or temp_store[0] != 2
    ):
        raise SQLiteMigrationError(
            "SQLite could not verify in-memory TEMP storage"
        )


def _validate_database_file(database_path: Path) -> None:
    if not database_path.exists() and not database_path.is_symlink():
        return
    try:
        resolved_path = database_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("the existing database file cannot be resolved") from error
    if (
        os.path.normcase(resolved_path.drive) != os.path.normcase("D:")
        or resolved_path.parent != database_path.parent
        or not resolved_path.is_file()
    ):
        raise ValueError(
            "the existing database file must be a regular file in the approved D: directory"
        )


def _check_storage_budget(
    directory: Path,
    database_path: Path,
    max_database_bytes: int,
    max_sidecar_bytes: int,
    max_migration_bytes: int = 0,
) -> None:
    try:
        _validate_sidecar_files(directory, database_path)
        database_size = database_path.stat().st_size if database_path.exists() else 0
        sidecar_size = sum(
            path.stat().st_size if path.exists() else 0
            for path in (
                database_path.with_name(database_path.name + "-journal"),
                database_path.with_name(database_path.name + "-wal"),
                database_path.with_name(database_path.name + "-shm"),
            )
        )
        free_bytes = shutil.disk_usage(str(directory)).free
    except OSError as error:
        raise SQLiteMigrationError("could not verify database storage capacity") from error

    if database_size > max_database_bytes:
        raise SQLiteMigrationError(
            "the main database already exceeds max_database_bytes"
        )
    if sidecar_size > max_sidecar_bytes:
        raise SQLiteMigrationError(
            "existing SQLite journal/WAL sidecars exceed max_sidecar_bytes"
        )
    remaining_database_growth = max_database_bytes - database_size
    remaining_sidecar_growth = max_sidecar_bytes - sidecar_size
    required_free = (
        MINIMUM_FREE_BYTES
        + remaining_database_growth
        + remaining_sidecar_growth
        + max_migration_bytes
    )
    if free_bytes < required_free:
        raise SQLiteMigrationError(
            "configured database, sidecar, and migration budgets would violate "
            "the 20 GiB free-space reserve on D:"
        )


def _validate_sidecar_files(directory: Path, database_path: Path) -> None:
    approved_directory = directory.resolve(strict=True)
    approved_text = os.path.normcase(str(approved_directory))
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar_path = database_path.with_name(database_path.name + suffix)
        if not sidecar_path.exists() and not sidecar_path.is_symlink():
            continue
        try:
            resolved_path = sidecar_path.resolve(strict=True)
            resolved_text = os.path.normcase(str(resolved_path))
            common_path = os.path.commonpath((approved_text, resolved_text))
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError(
                "existing SQLite sidecar cannot be resolved safely: {}".format(
                    sidecar_path.name
                )
            ) from error
        if (
            common_path != approved_text
            or not resolved_path.is_file()
        ):
            raise ValueError(
                "existing SQLite sidecar must resolve to a file within the "
                "approved database directory: {}".format(sidecar_path.name)
            )


def _set_database_growth_limit(
    connection: sqlite3.Connection,
    max_database_bytes: int,
) -> None:
    page_size_row = connection.execute("PRAGMA page_size").fetchone()
    if page_size_row is None or type(page_size_row[0]) is not int or page_size_row[0] <= 0:
        raise SQLiteMigrationError("SQLite reported an invalid database page size")

    max_page_count = max_database_bytes // page_size_row[0]
    if max_page_count < 1:
        raise SQLiteMigrationError("max_database_bytes is smaller than one SQLite page")
    current_page_count = connection.execute("PRAGMA page_count").fetchone()
    if current_page_count is None or current_page_count[0] > max_page_count:
        raise SQLiteMigrationError(
            "the existing database exceeds max_database_bytes"
        )

    result = connection.execute(
        "PRAGMA max_page_count = {}".format(max_page_count)
    ).fetchone()
    if result is None or result[0] > max_page_count:
        raise SQLiteMigrationError("SQLite could not apply the configured growth limit")


def _set_sidecar_growth_limit(
    connection: sqlite3.Connection,
    max_sidecar_bytes: int,
) -> None:
    result = connection.execute(
        "PRAGMA journal_size_limit = {}".format(max_sidecar_bytes)
    ).fetchone()
    if (
        result is None
        or type(result[0]) is not int
        or result[0] < 0
        or result[0] > max_sidecar_bytes
    ):
        raise SQLiteMigrationError(
            "SQLite could not apply the configured journal-size limit"
        )


def _read_applied_versions(connection: sqlite3.Connection) -> Tuple[int, ...]:
    objects = _user_schema_objects(connection)
    schema_migrations = objects.get("schema_migrations")
    if schema_migrations is None or schema_migrations[0] != "table":
        if objects:
            raise SQLiteMigrationError(
                "unversioned SQLite database contains existing tables; refusing to alter it"
            )
        return ()

    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
    }
    if columns != {"version", "applied_at_utc"}:
        raise SQLiteMigrationError("schema_migrations has an unknown structure")

    rows = connection.execute(
        "SELECT version, applied_at_utc FROM schema_migrations"
    ).fetchall()
    if not rows:
        raise SQLiteMigrationError(
            "schema_migrations is empty; refusing to repair an existing database"
        )

    versions: List[int] = []
    for row in rows:
        version = row[0]
        if type(version) is not int or version not in _MIGRATIONS:
            raise SQLiteMigrationError(
                "database contains an unknown or newer schema version: {!r}".format(
                    version
                )
            )
        try:
            _parse_utc_datetime(row[1])
        except (TypeError, ValueError) as error:
            raise SQLiteMigrationError(
                "schema_migrations contains a malformed applied_at_utc timestamp"
            ) from error
        versions.append(version)
    versions.sort()
    if versions != list(range(1, max(versions) + 1)):
        raise SQLiteMigrationError("schema migration history is not contiguous")
    return tuple(versions)


def _user_schema_objects(
    connection: sqlite3.Connection,
) -> Dict[str, Tuple[str, Optional[str]]]:
    rows = connection.execute(
        "SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _apply_one_migration(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    migration_path = (
        Path(__file__).parent / "migrations" / _MIGRATIONS[version]
    )
    try:
        script = migration_path.read_text(encoding="utf-8")
    except OSError as error:
        raise SQLiteMigrationError(
            "could not read migration {}".format(_MIGRATIONS[version])
        ) from error

    connection.execute("BEGIN IMMEDIATE")
    try:
        _execute_sql_script(connection, script)
        _validate_schema_objects(connection, tuple(range(1, version + 1)))
        applied_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at_utc) VALUES (?, ?)",
            (version, applied_at),
        )
        connection.commit()
    except BaseException as migration_error:
        if connection.in_transaction:
            try:
                connection.rollback()
            except BaseException as rollback_error:
                migration_error.rollback_error = rollback_error
                add_note = getattr(migration_error, "add_note", None)
                if add_note is not None:
                    add_note(
                        "SQLite rollback also failed: {!r}".format(rollback_error)
                    )
        raise


def _execute_sql_script(
    connection: sqlite3.Connection,
    script: str,
) -> None:
    for statement in _sql_statements(script):
        connection.execute(statement)


def _sql_statements(script: str) -> List[str]:
    pending = ""
    statements = []
    for line in script.splitlines():
        pending += line + "\n"
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise SQLiteMigrationError("migration SQL contains an incomplete statement")
    return statements


def _validate_schema_objects(
    connection: sqlite3.Connection,
    applied_versions: Tuple[int, ...],
) -> None:
    expected_definitions = {}
    for version in applied_versions:
        migration_path = Path(__file__).parent / "migrations" / _MIGRATIONS[version]
        try:
            script = migration_path.read_text(encoding="utf-8")
        except OSError as error:
            raise SQLiteMigrationError(
                "could not read migration {}".format(_MIGRATIONS[version])
            ) from error
        for statement in _sql_statements(script):
            match = re.match(
                r"CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX|TRIGGER)\s+"
                r"([A-Za-z_][A-Za-z0-9_]*)\b",
                statement,
                flags=re.IGNORECASE,
            )
            if match is not None:
                object_type = match.group(1).lower()
                object_name = match.group(2)
                expected_definitions[object_name] = (
                    object_type,
                    _normalize_sql(statement),
                )

    actual_objects = _user_schema_objects(connection)
    if set(actual_objects) != set(expected_definitions):
        missing = set(expected_definitions) - set(actual_objects)
        unexpected = set(actual_objects) - set(expected_definitions)
        details = []
        if missing:
            details.append("missing: {}".format(", ".join(sorted(missing))))
        if unexpected:
            details.append("unexpected: {}".format(", ".join(sorted(unexpected))))
        raise SQLiteMigrationError(
            "database schema objects do not match migrations ({})".format(
                "; ".join(details)
            )
        )

    for name, (expected_type, expected_sql) in expected_definitions.items():
        actual_type, actual_sql = actual_objects[name]
        if actual_type != expected_type or actual_sql is None:
            raise SQLiteMigrationError(
                "database schema object {!r} has an unexpected type".format(name)
            )
        if _normalize_sql(actual_sql) != expected_sql:
            raise SQLiteMigrationError(
                "database schema object {!r} differs from its migration definition".format(
                    name
                )
            )

    _validate_table_columns(connection, applied_versions)
    _validate_foreign_keys(connection, applied_versions)
    _validate_indexes(connection)
    violations = connection.execute("PRAGMA foreign_key_check").fetchone()
    if violations is not None:
        raise SQLiteMigrationError("database contains foreign-key violations")


def _normalize_sql(statement: str) -> str:
    normalized = []
    index = 0
    pending_space = False
    while index < len(statement):
        character = statement[index]
        if character.isspace():
            pending_space = True
            index += 1
            continue

        if pending_space and normalized:
            normalized.append(" ")
        pending_space = False

        if character in ("'", '"', "`", "["):
            closing = "]" if character == "[" else character
            start = index
            index += 1
            while index < len(statement):
                if statement[index] == closing:
                    if (
                        closing != "]"
                        and index + 1 < len(statement)
                        and statement[index + 1] == closing
                    ):
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise SQLiteMigrationError(
                    "schema definition contains an unterminated quoted value"
                )
            normalized.append(statement[start:index])
            continue

        if statement.startswith("--", index):
            end = statement.find("\n", index)
            if end == -1:
                end = len(statement)
            else:
                end += 1
            normalized.append(statement[index:end])
            index = end
            continue

        if statement.startswith("/*", index):
            end = statement.find("*/", index + 2)
            if end == -1:
                raise SQLiteMigrationError(
                    "schema definition contains an unterminated comment"
                )
            normalized.append(statement[index : end + 2])
            index = end + 2
            continue

        if "A" <= character <= "Z":
            character = chr(ord(character) + ord("a") - ord("A"))
        normalized.append(character)
        index += 1

    normalized_statement = "".join(normalized).rstrip()
    if normalized_statement.endswith(";"):
        normalized_statement = normalized_statement[:-1].rstrip()
    return normalized_statement


def _validate_table_columns(
    connection: sqlite3.Connection,
    applied_versions: Tuple[int, ...],
) -> None:
    expected_by_table = dict(_EXPECTED_COLUMNS)
    for version in applied_versions:
        expected_by_table.update(_EXPECTED_COLUMNS_BY_VERSION.get(version, {}))
    for table_name, expected_columns in expected_by_table.items():
        rows = connection.execute(
            "PRAGMA table_info({})".format(table_name)
        ).fetchall()
        actual_columns = tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in rows
        )
        if actual_columns != expected_columns:
            raise SQLiteMigrationError(
                "table {!r} has incompatible columns or key constraints".format(
                    table_name
                )
            )


def _validate_foreign_keys(
    connection: sqlite3.Connection,
    applied_versions: Tuple[int, ...],
) -> None:
    expected_by_table = dict(_EXPECTED_FOREIGN_KEYS)
    for version in applied_versions:
        expected_by_table.update(
            _EXPECTED_FOREIGN_KEYS_BY_VERSION.get(version, {})
        )
    for table_name, expected_keys in expected_by_table.items():
        rows = connection.execute(
            "PRAGMA foreign_key_list({})".format(table_name)
        ).fetchall()
        actual_keys = tuple(
            sorted((row[2], row[3], row[4], row[6].upper()) for row in rows)
        )
        if actual_keys != tuple(sorted(expected_keys)):
            raise SQLiteMigrationError(
                "table {!r} has incompatible foreign keys".format(table_name)
            )


def _validate_indexes(connection: sqlite3.Connection) -> None:
    for index_name, expected in _EXPECTED_INDEXES.items():
        table_name, unique, partial, expected_columns = expected
        rows = connection.execute(
            "PRAGMA index_list({})".format(table_name)
        ).fetchall()
        index_row = next((row for row in rows if row[1] == index_name), None)
        if (
            index_row is None
            or bool(index_row[2]) != unique
            or bool(index_row[4]) != partial
        ):
            raise SQLiteMigrationError(
                "index {!r} has incompatible uniqueness or predicate".format(index_name)
            )

        columns = connection.execute(
            'PRAGMA index_info("{}")'.format(index_name)
        ).fetchall()
        actual_columns = tuple(row[2] for row in columns)
        if actual_columns != expected_columns:
            raise SQLiteMigrationError(
                "index {!r} has incompatible columns".format(index_name)
            )
