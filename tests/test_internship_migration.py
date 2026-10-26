"""Migration 027 / ORM contract tests that run without PostgreSQL.

These cover everything that is provable on SQLite: the ORM declares exactly the
columns the migration adds, the NULL / [] / non-empty distinction survives a
round trip, the season validator rejects bad input, and the enum CHECK
constraints are real.  PostgreSQL-specific behaviour (JSONB, the seasons helper
function) is verified separately by ``scripts/verify_internship_pg_migration.py``
and is NOT claimed as tested here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.internship.models import InternshipSeason
from packages.storage.database import create_storage_engine, initialize_schema
from packages.storage.models import JobSnapshot

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_027 = ROOT / "migrations" / "027_internship_track.sql"

INTERNSHIP_COLUMNS = {
    "track",
    "internship_seasons",
    "conversion_opportunity",
    "internship_requirements",
    "match_conditions",
}
JSON_COLUMNS = {"internship_seasons", "internship_requirements", "match_conditions"}


@pytest.fixture()
def engine():
    engine = create_storage_engine("sqlite+pysqlite:///:memory:")
    initialize_schema(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _job(**overrides) -> JobSnapshot:
    payload = dict(
        id="job-1",
        source_ref="ref-1",
        company_id="company-1",
        title="Python开发实习生",
        detail_url="https://example.test/job/1",
        cohort_status="unconfirmed",
        batch="unknown",
        source="test",
    )
    payload.update(overrides)
    return JobSnapshot(**payload)


# --- schema shape ----------------------------------------------------------


def test_orm_declares_exactly_the_new_columns(engine):
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("job_snapshots")
    }

    assert INTERNSHIP_COLUMNS <= set(columns)
    for name in JSON_COLUMNS:
        assert columns[name]["nullable"] is True, f"{name} 必须可空：NULL 表示未分析"
    for name in ("track", "conversion_opportunity"):
        assert columns[name]["nullable"] is False
        assert columns[name]["default"] is not None


def test_migration_027_adds_the_same_columns_as_the_orm():
    sql = MIGRATION_027.read_text(encoding="utf-8")

    added = set(re.findall(r"ADD COLUMN\s+(\w+)", sql, re.IGNORECASE))

    assert added == INTERNSHIP_COLUMNS


def test_migration_027_is_additive_only():
    sql = MIGRATION_027.read_text(encoding="utf-8").lower()

    assert "drop column" not in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    # 历史数据不回填：迁移里不允许出现 UPDATE 猜测分类。
    assert "update job_snapshots" not in sql


def test_migrations_are_contiguous_and_head_is_latest():
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in (ROOT / "migrations").glob("*.sql")
    )

    # 027 stays present and additive; 028 (job_events) and 029 (watch_rules)
    # were added by the intelligence station.  The contract is "contiguous, no
    # gaps", not "027 is always last".
    assert versions == list(range(1, 30))
    assert {27, 28, 29} <= set(versions)


# --- NULL / [] / non-empty -------------------------------------------------


def test_unclassified_row_reads_as_unknown_and_null(engine):
    with Session(engine) as session:
        session.add(_job())
        session.commit()
        row = session.scalars(select(JobSnapshot)).one()

    assert row.track == "unknown"
    assert row.conversion_opportunity == "unknown"
    assert row.internship_seasons is None
    assert row.internship_requirements is None
    assert row.match_conditions is None


def test_not_analysed_and_analysed_without_season_stay_distinct(engine):
    with Session(engine) as session:
        session.add(_job(id="never-analysed", source_ref="a"))
        session.add(_job(id="analysed-no-season", source_ref="b", internship_seasons=[]))
        session.add(_job(id="summer-intern", source_ref="c", internship_seasons=["summer"]))
        session.commit()
        rows = {row.id: row for row in session.scalars(select(JobSnapshot))}

    assert rows["never-analysed"].internship_seasons is None
    assert rows["analysed-no-season"].internship_seasons == []
    assert rows["summer-intern"].internship_seasons == ["summer"]


def test_seasons_accept_enum_members_and_dedupe_them(engine):
    with Session(engine) as session:
        session.add(
            _job(
                internship_seasons=[
                    InternshipSeason.SUMMER,
                    "summer",
                    InternshipSeason.WINTER,
                ]
            )
        )
        session.commit()
        row = session.scalars(select(JobSnapshot)).one()

    assert row.internship_seasons == ["summer", "winter"]


def test_empty_json_evidence_is_independent_of_null(engine):
    with Session(engine) as session:
        session.add(_job(internship_requirements={}, match_conditions={}))
        session.commit()
        row = session.scalars(select(JobSnapshot)).one()

    assert row.internship_requirements == {}
    assert row.match_conditions == {}
    assert row.internship_seasons is None


# --- validation ------------------------------------------------------------


@pytest.mark.parametrize(
    "seasons",
    [["autumn"], ["summer", "autumn"], [1], [None], "summer"],
)
def test_invalid_seasons_are_rejected_before_reaching_the_database(seasons):
    with pytest.raises(ValueError):
        _job(internship_seasons=seasons)


def test_unspecified_must_be_expressed_as_an_empty_list():
    with pytest.raises(ValueError):
        _job(internship_seasons=["unspecified"])


@pytest.mark.parametrize(
    "field,value",
    [("track", "bogus"), ("conversion_opportunity", "maybe")],
)
def test_enum_check_constraints_are_enforced_by_the_database(engine, field, value):
    with Session(engine) as session:
        session.add(_job(**{field: value}))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "field,value",
    [
        ("track", "internship"),
        ("track", "campus"),
        ("conversion_opportunity", "yes"),
        ("conversion_opportunity", "no"),
    ],
)
def test_allowed_enum_values_are_accepted(engine, field, value):
    with Session(engine) as session:
        session.add(_job(id=f"job-{value}", source_ref=f"ref-{value}", **{field: value}))
        session.commit()
        row = session.scalars(select(JobSnapshot)).one()

    assert getattr(row, field) == value
