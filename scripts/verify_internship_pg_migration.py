"""Verify migration 027 against a disposable PostgreSQL instance.

SQLite cannot prove the PostgreSQL behaviour that migration 027 relies on: JSONB
column types, the ``recruitops_valid_internship_seasons`` helper function, and
CHECK constraints over that function.  This script therefore starts a throwaway
container, applies 001-026, seeds representative pre-existing rows, then applies
027 and checks that:

* every seeded row still exists and its pre-existing columns are byte-identical;
* the new columns are NULL / 'unknown' (no fabricated backfill);
* the seasons constraint rejects duplicates, non-strings, non-arrays and unknown
  labels, while accepting NULL, [] and a valid array;
* the enum CHECK constraints reject out-of-range values;
* the ledger records 27 applied migrations and a re-run is a no-op.

The container is removed afterwards.  Nothing here touches an existing database.

Usage:
    python scripts/verify_internship_pg_migration.py
    python scripts/verify_internship_pg_migration.py --keep   # keep container
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from scripts.apply_migrations import apply_migrations, check_migrations  # noqa: E402

DEFAULT_MIGRATIONS = PROJECT_ROOT / "migrations"
DEFAULT_IMAGE = "pgvector/pgvector:pg16"
MIGRATION_027 = "027_internship_track.sql"

#: Values for every NOT NULL column that has no database default, keyed by column.
SEED_VALUES: dict[str, Any] = {
    "id": None,  # filled per row
    "company_id": "seed-company",
    "title": None,  # filled per row
    "detail_url": None,  # filled per row
    "cohort_status": "unconfirmed",
    "batch": "unknown",
    "source": "migration-verification",
}

SEED_ROWS: tuple[dict[str, str], ...] = (
    {
        "id": "seed-interview-001",
        "title": "Python开发实习生",
        "detail_url": "https://example.test/seed/1",
    },
    {
        "id": "seed-formal-002",
        "title": "2027届校园招聘-C++工程师",
        "detail_url": "https://example.test/seed/2",
    },
    {
        "id": "seed-odd-003",
        "title": "测试'引号\"与\\反斜杠/实习生",
        "detail_url": "https://example.test/seed/3?x=1&y=2",
    },
)

#: Columns whose values must be untouched by the migration.
UNCHANGED_COLUMNS = (
    "id",
    "title",
    "detail_url",
    "cohort",
    "cohort_status",
    "batch",
    "capture_status",
    "availability_status",
    "match_score",
    "title_key",
    "source",
    "source_ref",
)


class CheckFailed(RuntimeError):
    pass


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise CheckFailed(f"command failed: {' '.join(command)}\n{result.stderr.strip()}")
    return result


def _start_container(image: str, name: str) -> str:
    _run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", "POSTGRES_USER=recruitops",
            "-e", "POSTGRES_PASSWORD=recruitops",
            "-e", "POSTGRES_DB=recruitops",
            "-p", "127.0.0.1::5432",
            image,
        ]
    )
    port_output = _run(["docker", "port", name, "5432"]).stdout.strip()
    # e.g. "127.0.0.1:55231"
    port = port_output.rsplit(":", 1)[-1].strip()
    if not port.isdigit():
        raise CheckFailed(f"could not determine mapped port from {port_output!r}")
    return port


def _wait_ready(url: str, *, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - readiness probe
            last_error = str(exc)
            time.sleep(1.5)
        finally:
            engine.dispose()
    raise CheckFailed(f"postgres did not become ready: {last_error}")


def _staging_dir(migrations: Path, *, before_version: int) -> Path:
    """Copy migrations up to (excluding) ``before_version`` into a temp directory."""

    directory = Path(tempfile.mkdtemp(prefix="recruitops-026-"))
    for path in sorted(migrations.glob("*.sql")):
        if int(path.name.split("_", 1)[0]) < before_version:
            shutil.copy2(path, directory / path.name)
    return directory


def _required_columns(engine) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'job_snapshots'
                  AND is_nullable = 'NO'
                  AND column_default IS NULL
                ORDER BY ordinal_position
                """
            )
        ).scalars().all()
    return list(rows)


def _seed(engine) -> None:
    required = _required_columns(engine)
    missing = [name for name in required if name not in SEED_VALUES]
    if missing:
        raise CheckFailed(
            "seed data does not cover required columns: " + ", ".join(missing)
        )
    with engine.begin() as connection:
        for row in SEED_ROWS:
            values = {**SEED_VALUES, **row}
            columns = ", ".join(required)
            placeholders = ", ".join(f":{name}" for name in required)
            connection.execute(
                text(
                    f"INSERT INTO job_snapshots ({columns}) VALUES ({placeholders})"
                ),
                {name: values[name] for name in required},
            )


def _snapshot(engine) -> list[tuple[Any, ...]]:
    columns = ", ".join(UNCHANGED_COLUMNS)
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(f"SELECT {columns} FROM job_snapshots ORDER BY id")
            )
        ]


def _try_sql(engine, sql: str, params: dict[str, Any] | None = None) -> str | None:
    """Run one statement in its own transaction; return the error text if it fails."""

    try:
        with engine.begin() as connection:
            connection.execute(text(sql), params or {})
    except Exception as exc:  # noqa: BLE001 - we are probing constraint behaviour
        return f"{type(exc).__name__}: {exc}".splitlines()[0][:160]
    return None


def _check(report: list[tuple[bool, str]], ok: bool, label: str, detail: str = "") -> None:
    report.append((ok, f"{label}{(' — ' + detail) if detail and not ok else ''}"))


def verify(database_url: str, migrations: Path) -> list[tuple[bool, str]]:
    report: list[tuple[bool, str]] = []
    staging = _staging_dir(migrations, before_version=27)
    try:
        result = apply_migrations(database_url, staging)
        _check(report, len(result.applied) == 26, "001-026 applied", f"{len(result.applied)}")

        engine = create_engine(database_url)
        try:
            _seed(engine)
            before = _snapshot(engine)
            _check(report, len(before) == len(SEED_ROWS), "seed rows inserted", str(len(before)))

            applied = apply_migrations(database_url, migrations)
            _check(report, list(applied.applied) == [MIGRATION_027], "027 applied", str(applied.applied))

            after = _snapshot(engine)
            _check(
                report,
                before == after,
                "existing rows preserved byte-for-byte",
                f"before={len(before)} after={len(after)}",
            )

            with engine.connect() as connection:
                counts = connection.execute(
                    text("SELECT count(*) FROM job_snapshots")
                ).scalar_one()
            _check(report, counts == len(SEED_ROWS), "row count unchanged", str(counts))

            with engine.connect() as connection:
                legacy = connection.execute(
                    text(
                        "SELECT count(*) FROM job_snapshots "
                        "WHERE track = 'unknown' AND conversion_opportunity = 'unknown'"
                        " AND internship_seasons IS NULL"
                        " AND internship_requirements IS NULL"
                        " AND match_conditions IS NULL"
                    )
                ).scalar_one()
            _check(
                report,
                legacy == len(SEED_ROWS),
                "new columns default to unknown/NULL without backfill",
                str(legacy),
            )

            with engine.connect() as connection:
                types = dict(
                    connection.execute(
                        text(
                            "SELECT column_name, data_type FROM information_schema.columns "
                            "WHERE table_schema='public' AND table_name='job_snapshots' "
                            "AND column_name IN ('internship_seasons',"
                            " 'internship_requirements', 'match_conditions')"
                        )
                    ).all()
                )
            _check(
                report,
                set(types.values()) == {"jsonb"},
                "JSON evidence columns are JSONB",
                str(types),
            )

            with engine.connect() as connection:
                index_names = connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename='job_snapshots' AND indexname='ix_job_snapshots_track'"
                    )
                ).scalars().all()
            _check(report, index_names == ["ix_job_snapshots_track"], "track index exists")

            with engine.connect() as connection:
                function = connection.execute(
                    text(
                        "SELECT proname FROM pg_proc "
                        "WHERE proname='recruitops_valid_internship_seasons'"
                    )
                ).scalars().all()
            _check(report, function, "seasons validation function exists")

            last = "UPDATE job_snapshots SET internship_seasons = CAST(:value AS jsonb) WHERE id = :id"
            base = {"id": SEED_ROWS[0]["id"]}

            for label, value in (
                ("NULL", None),
                ("empty array", "[]"),
                ("valid array", '["summer", "winter"]'),
            ):
                error = _try_sql(engine, last, {**base, "value": value})
                _check(report, error is None, f"seasons accepts {label}", error or "")

            for label, value in (
                ("duplicate labels", '["summer", "summer"]'),
                ("unknown label", '["autumn"]'),
                ("non-string element", "[1]"),
                ("object instead of array", '{"season": "summer"}'),
                ("uppercase label", '["Summer"]'),
            ):
                error = _try_sql(engine, last, {**base, "value": value})
                _check(report, error is not None, f"seasons rejects {label}")

            error = _try_sql(engine, "UPDATE job_snapshots SET track = 'bogus'")
            _check(report, error is not None, "track CHECK rejects out-of-range value")
            error = _try_sql(engine, "UPDATE job_snapshots SET track = 'internship'")
            _check(report, error is None, "track CHECK accepts internship", error or "")
            error = _try_sql(
                engine, "UPDATE job_snapshots SET conversion_opportunity = 'maybe'"
            )
            _check(report, error is not None, "conversion CHECK rejects out-of-range value")

            plan = check_migrations(database_url, migrations)
            _check(report, plan.pending == (), "no pending migrations after 027", str(plan.pending))
            _check(report, len(plan.applied) == 27, "ledger records 27 migrations", str(len(plan.applied)))

            rerun = apply_migrations(database_url, migrations)
            _check(report, rerun.applied == (), "re-run applies nothing", str(rerun.applied))
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS)
    parser.add_argument("--keep", action="store_true", help="keep the container for inspection")
    args = parser.parse_args(argv)

    name = f"recruitops-migration-verify-{os.getpid()}"
    port = _start_container(args.image, name)
    url = f"postgresql+psycopg://recruitops:recruitops@127.0.0.1:{port}/recruitops"
    failed = 0
    try:
        _wait_ready(url)
        report = verify(url, args.migrations_dir)
        for ok, label in report:
            print(f"[{'PASS' if ok else 'FAIL'}] {label}")
            failed += 0 if ok else 1
    except CheckFailed as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"container kept: {name} (port {port})")
        else:
            _run(["docker", "rm", "-f", name], check=False)

    print()
    if failed:
        print(f"RESULT: FAILED ({failed} check(s) failed of {len(report)})")
        return 1
    print(f"RESULT: PASSED ({len(report)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
