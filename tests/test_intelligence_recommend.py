"""Tests for the deterministic recommendation layer.

No model, no network: profile keywords are matched against posting titles and
the announcement metadata that OfferBiu imports carry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from packages.intelligence.recommend import (
    MAX_SCORE,
    profile_signals,
    recommend_jobs,
    score_posting,
)
from packages.intelligence.resume_keywords import extract_profile_hints
from packages.storage import JobSnapshot, Storage, create_storage_engine, initialize_schema

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)

PROFILE = {
    "degree": "硕士",
    "job_type": "campus",
    "skills": ["Python", "PyTorch", "大模型"],
    "matching": {
        "title_keywords": ["算法", "大模型", "Agent"],
        "primary_directions": ["大模型与智能体", "算法工程师"],
        "secondary_directions": [],
    },
}


def _job(job_id: str, title: str, *, track: str = "campus", city: str = "北京") -> JobSnapshot:
    return JobSnapshot(
        id=job_id,
        company_id="acme",
        title=title,
        city=city,
        detail_url=f"https://example.test/{job_id}",
        cohort=2027,
        cohort_status="confirmed",
        batch="formal" if track == "campus" else "internship",
        track=track,
        availability_status="active",
        source="fixture",
        source_ref=f"fixture:{job_id}",
        capture_evidence={"industry": "IT/互联网/游戏"},
        created_at=NOW,
        updated_at=NOW,
    )


def _storage(*jobs: JobSnapshot) -> Storage:
    engine = create_storage_engine("sqlite:///:memory:")
    initialize_schema(engine)
    storage = Storage(engine)
    with storage.session() as session:
        session.add_all(jobs)
        session.commit()
    return storage


def test_profile_signals_splits_phrases() -> None:
    signals = profile_signals(PROFILE)
    # "大模型与智能体" is split on the connector so it can match real titles.
    assert "大模型" in signals["directions"]
    assert "智能体" in signals["directions"]
    assert "算法工程师" in signals["directions"]
    assert "算法" in signals["keywords"]
    assert "python" in signals["skills"]


def test_score_rewards_direction_and_keyword_hits() -> None:
    signals = profile_signals(PROFILE)
    hit, reasons = score_posting("大模型算法工程师（Agent 方向）", signals, track="campus", job_type="campus")
    miss, _ = score_posting("行政专员", signals, track="campus", job_type="campus")

    assert hit > miss
    assert hit >= 60
    assert miss == 0
    assert any("方向命中" in reason for reason in reasons)


def test_score_caps_at_max() -> None:
    signals = {"directions": ["算法"], "keywords": ["算法"], "skills": ["python"]}
    score, _ = score_posting("算法 python 算法 python", signals, track="campus", job_type="campus")
    assert score <= MAX_SCORE


def test_no_profile_returns_hint_instead_of_results() -> None:
    storage = _storage(_job("job-1", "算法工程师"))
    result = recommend_jobs(storage, {})
    assert result.profile_ready is False
    assert result.items == ()
    assert result.hint


def test_recommend_orders_by_score_and_respects_limit() -> None:
    storage = _storage(
        _job("job-1", "大模型算法工程师"),
        _job("job-2", "行政专员"),
        _job("job-3", "Agent 算法实习生", track="internship"),
    )
    result = recommend_jobs(storage, PROFILE, limit=2, min_score=1)

    assert result.profile_ready is True
    assert result.total_scanned == 3
    assert len(result.items) == 2
    assert result.items[0].score >= result.items[1].score
    assert all(item.score >= 1 for item in result.items)
    assert "行政专员" not in [item.title for item in result.items]


def test_track_filter_and_job_type_alignment() -> None:
    storage = _storage(
        _job("job-1", "大模型算法工程师", track="campus"),
        _job("job-2", "大模型算法实习生", track="internship"),
    )
    only_intern = recommend_jobs(storage, PROFILE, track="internship", min_score=1)
    assert [item.job_id for item in only_intern.items] == ["job-2"]

    internship_profile = dict(PROFILE, job_type="internship")
    campus_scores = {
        item.job_id: item.score
        for item in recommend_jobs(storage, internship_profile, min_score=1, limit=10).items
    }
    # The internship posting earns the track-alignment bonus, the campus one does not.
    assert campus_scores["job-2"] > campus_scores["job-1"]


def test_min_score_filters_everything_when_too_high() -> None:
    storage = _storage(_job("job-1", "行政专员"))
    result = recommend_jobs(storage, PROFILE, min_score=90)
    assert result.items == ()
    assert result.hint


def test_resume_extract_finds_skills_directions_and_degree() -> None:
    text = """
    教育背景：某某大学 计算机科学与技术 硕士，2027 届毕业生。
    技能：熟悉 Python、PyTorch，做过大模型 RAG 与 Agent 项目，了解 Docker 与 Redis。
    实习经历：某公司 算法工程师实习生，负责推荐算法优化。
    """
    hints = extract_profile_hints(text)

    assert "Python" in hints["skills"]
    assert "PyTorch" in hints["skills"]
    assert "大模型" in hints["skills"]
    assert hints["degree"] == "硕士"
    assert hints["graduation_year"] == 2027
    assert "大模型与智能体" in hints["directions"]
    assert hints["title_keywords"]


def test_resume_extract_ignores_short_garbage() -> None:
    hints = extract_profile_hints("你好")
    assert hints["skills"] == []
    assert hints["directions"] == []
    assert hints["matched_count"] == 0
