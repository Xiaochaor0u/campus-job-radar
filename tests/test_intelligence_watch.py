"""Tests for watch rules (每日新增 + 条件筛选)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.intelligence.watch import (
    WatchCondition,
    delete_rule,
    expand_terms,
    list_rules,
    run_watch,
    save_rule,
    window_start,
)
from packages.storage import JobEvent, Storage, create_storage_engine, initialize_schema

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _storage() -> Storage:
    engine = create_storage_engine("sqlite:///:memory:")
    initialize_schema(engine)
    return Storage(engine)


def _event(
    job_id: str,
    title: str,
    *,
    event_type: str = "new",
    city: str = "北京",
    track: str = "campus",
    detected_at: datetime | None = None,
    company: str = "Acme",
) -> JobEvent:
    return JobEvent(
        id=f"evt-{job_id}-{event_type}",
        event_type=event_type,
        job_id=job_id,
        company_id=f"offerbiu:{company}",
        company_name=company,
        title=title,
        city=city,
        batch="formal" if track == "campus" else "internship",
        track=track,
        detail_url=f"https://example.test/{job_id}",
        detected_at=detected_at or NOW - timedelta(hours=1),
        run_id="run-1",
        created_at=detected_at or NOW - timedelta(hours=1),
    )


def _save(storage: Storage, *events: JobEvent) -> None:
    with storage.session() as session:
        session.add_all(events)
        session.commit()


def test_expand_terms_maps_to_title_variants() -> None:
    # Exact spellings are excluded by default so callers can rank exact hits higher.
    synonyms = expand_terms(["算法"])
    assert "算法" not in synonyms
    assert "机器学习" in synonyms
    # A posting titled "Agent 研发" should be reachable from "大模型"
    assert "agent" in expand_terms(["大模型"])

    assert "算法" in expand_terms(["算法"], include_original=True)


def test_window_start_aligns_to_local_midnight() -> None:
    start_today = window_start("today", NOW)
    start_3d = window_start("3d", NOW)
    assert start_today <= NOW
    assert start_3d < start_today
    assert (start_today - start_3d).days == 2


def test_window_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        window_start("14d", NOW)


def test_condition_from_mapping_normalises_input() -> None:
    condition = WatchCondition.from_mapping(
        {"keywords": ["", " 算法 ", "算法"], "cities": ["北京"], "track": "INTERNSHIP"}
    )
    assert condition.keywords == ("算法",)
    assert condition.cities == ("北京",)
    assert condition.track == "internship"


def test_run_watch_filters_by_keyword_via_synonyms() -> None:
    storage = _storage()
    _save(
        storage,
        _event("job-1", "机器学习算法工程师"),
        _event("job-2", "行政专员"),
    )
    result = run_watch(storage, WatchCondition(keywords=("算法",)), window="today", now=NOW)

    assert result.scanned_events == 2
    assert result.total_hits == 1
    assert result.hits[0].job_id == "job-1"
    assert "关键词" in result.hits[0].matched


def test_run_watch_respects_track_and_city() -> None:
    storage = _storage()
    _save(
        storage,
        _event("job-1", "算法实习生", track="internship", city="上海"),
        _event("job-2", "算法工程师", track="campus", city="北京"),
    )
    internship = run_watch(
        storage,
        WatchCondition(keywords=("算法",), track="internship"),
        window="today",
        now=NOW,
    )
    assert [hit.job_id for hit in internship.hits] == ["job-1"]

    beijing = run_watch(
        storage, WatchCondition(cities=("北京",)), window="today", now=NOW
    )
    assert [hit.job_id for hit in beijing.hits] == ["job-2"]


def test_run_watch_ignores_non_new_events_and_old_events() -> None:
    storage = _storage()
    _save(
        storage,
        _event("job-1", "算法工程师", event_type="updated"),
        _event("job-2", "算法工程师", detected_at=NOW - timedelta(days=10)),
        _event("job-3", "算法工程师", detected_at=NOW - timedelta(hours=3)),
    )
    result = run_watch(storage, WatchCondition(keywords=("算法",)), window="today", now=NOW)
    assert [hit.job_id for hit in result.hits] == ["job-3"]


def test_run_watch_orders_by_recency_and_aggregates_companies() -> None:
    storage = _storage()
    _save(
        storage,
        _event("job-1", "算法工程师", detected_at=NOW - timedelta(hours=5), company="A公司"),
        _event("job-2", "算法工程师", detected_at=NOW - timedelta(hours=1), company="B公司"),
        _event("job-3", "算法实习生", detected_at=NOW - timedelta(hours=2), company="B公司"),
    )
    result = run_watch(storage, WatchCondition(keywords=("算法",)), window="today", now=NOW)

    assert [hit.job_id for hit in result.hits] == ["job-2", "job-3", "job-1"]
    assert result.by_company[0] == ("B公司", 2)


def test_rule_lifecycle() -> None:
    storage = _storage()
    condition = WatchCondition(keywords=("大模型",), cities=("北京",), track="internship")

    rule = save_rule(storage, condition, name="大模型实习-北京")
    assert rule.id
    assert [row.name for row in list_rules(storage)] == ["大模型实习-北京"]

    updated = save_rule(storage, condition, name="改个名字", rule_id=rule.id)
    assert updated.id == rule.id
    assert updated.name == "改个名字"

    assert delete_rule(storage, rule.id) is True
    assert delete_rule(storage, rule.id) is False
    assert list_rules(storage) == []


def test_save_rule_rejects_empty_condition_and_blank_name() -> None:
    storage = _storage()
    with pytest.raises(ValueError, match="筛选条件"):
        save_rule(storage, WatchCondition(), name="空的")
    with pytest.raises(ValueError, match="名称"):
        save_rule(storage, WatchCondition(keywords=("算法",)), name="   ")


def test_run_watch_by_rule_id_uses_saved_condition() -> None:
    storage = _storage()
    _save(storage, _event("job-1", "大模型算法实习生", track="internship"))
    rule = save_rule(
        storage,
        WatchCondition(keywords=("大模型",), track="internship"),
        name="大模型实习",
    )
    condition = WatchCondition.from_mapping(rule)
    result = run_watch(
        storage, condition, window="today", rule_id=rule.id, rule_name=rule.name, now=NOW
    )
    assert result.rule_id == rule.id
    assert result.total_hits == 1
    assert result.to_dict()["by_company"] == [{"company": "Acme", "count": 1}]
