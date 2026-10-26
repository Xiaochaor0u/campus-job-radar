"""Offline tests for the on-demand job insight (单条岗位画像).

The point of these tests is the honesty contract, not the UI: salary must only
come from the announcement text, a missing bar must say "信息不足" instead of
guessing, and a job that is not in the database must not raise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.intelligence.insight import (
    build_job_insight,
    direction_tokens,
    extract_requirements,
    extract_salary,
    rate_bar,
)
from packages.storage import (
    JobSnapshot,
    Storage,
    create_storage_engine,
    initialize_schema,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 9, 30, tzinfo=UTC)


def _storage() -> Storage:
    engine = create_storage_engine("sqlite:///:memory:")
    initialize_schema(engine)
    return Storage(engine)


def _job(job_id: str, title: str, body: str, *, company_id: str = "acme") -> JobSnapshot:
    return JobSnapshot(
        id=job_id,
        company_id=company_id,
        title=title,
        city="北京",
        detail_url=f"https://example.test/jobs/{job_id}",
        jd_raw=body,
        cohort=2027,
        cohort_status="confirmed",
        batch="formal",
        first_seen_at=NOW - timedelta(hours=3),
        last_seen_at=NOW - timedelta(hours=1),
        availability_status="active",
        track="campus",
        source="fixture",
        source_ref=f"fixture:job:{job_id}:fp:{'a' * 64}",
        created_at=NOW,
        updated_at=NOW,
    )


def _save(storage: Storage, *jobs: JobSnapshot) -> None:
    with storage.session() as session:
        session.add_all(jobs)
        session.commit()


# --- salary -----------------------------------------------------------------


def test_salary_reads_a_range_with_chinese_filler() -> None:
    found = extract_salary("备注：应届生年薪大概 15-20 万，央企属性稳定。")

    assert found["found"] is True
    assert found["text"] == "15-20万"
    assert "15-20" in found["evidence"]


def test_salary_reads_daily_intern_pay() -> None:
    found = extract_salary("实习补贴 130-170 元/日 + 30 元餐补/日")

    assert found["found"] is True
    assert found["text"] == "130-170元/日"


def test_salary_reads_single_value_with_unit() -> None:
    assert extract_salary("应届生首年大概 20 万左右")["text"] == "20万"


def test_salary_ignores_compensation_words_without_numbers() -> None:
    # 「薪酬管理」是 HR 岗位名称，不是薪资信息。
    for text in ("招聘方向：HR实习生（招聘、薪酬管理）", "有竞争力的薪资，有实习补贴", ""):
        assert extract_salary(text)["found"] is False


# --- stated bar -------------------------------------------------------------


def test_requirements_keep_evidence_sentences() -> None:
    found = extract_requirements("任职要求：1. 本科及以上学历，计算机相关专业。2. 有 2 年以上自动化测试经验。3. CET-4 以上。")
    labels = {item["label"]: item for item in found}

    assert labels["学历要求"]["value"].startswith("本科")
    assert "本科" in labels["学历要求"]["evidence"]
    assert "经验要求" in labels
    assert labels["证书 / 语言"]["value"] == "CET-4"


def test_requirements_report_a_school_tier() -> None:
    found = extract_requirements("原则上要求 985/211 院校硕士毕业。")
    labels = {item["label"]: item["value"] for item in found}

    assert labels["院校门槛"] == "985"
    assert "硕士" in labels["学历要求"]


def test_requirements_empty_when_announcement_says_nothing() -> None:
    assert extract_requirements("负责推荐算法相关工作，团队氛围好。") == []


# --- direction tokens -------------------------------------------------------


def test_direction_tokens_strip_suffix_and_job_codes() -> None:
    assert direction_tokens("AI 自动化测试工程师(J20217)") == ["自动化测试"]
    assert direction_tokens("推荐算法助理研究员实习生") == ["推荐算法"]


def test_direction_tokens_drop_two_letter_words() -> None:
    # 「AI」这种两字词会在几乎任何标题里命中，用它判断同方向没有意义。
    assert direction_tokens("AI 工程师") == []


# --- bar rating -------------------------------------------------------------


def test_bar_says_unknown_instead_of_guessing() -> None:
    bar = rate_bar([], {"company_jobs": 10})

    assert bar["level"] == "信息不足"
    assert bar["score"] == 0


def test_bar_rises_with_degree_and_school_tier() -> None:
    requirements = [
        {"label": "学历要求", "value": "博士", "evidence": "博士"},
        {"label": "院校门槛", "value": "985", "evidence": "985"},
    ]
    bar = rate_bar(requirements, {"company_jobs": 3})

    assert bar["level"] == "要求偏高"
    assert bar["score"] >= 4


# --- assembly ---------------------------------------------------------------


def test_insight_is_assembled_from_the_stored_posting() -> None:
    storage = _storage()
    _save(
        storage,
        _job("job-1", "推荐算法工程师", "年薪 25-35 万。要求硕士及以上，985 优先。", company_id="acme"),
        _job("job-2", "推荐算法工程师（北京）", "负责召回策略。", company_id="acme"),
        _job("job-3", "后端开发工程师", "负责服务端。", company_id="acme"),
    )

    result = build_job_insight(storage, "job-1", use_model=False)

    assert result.found is True
    assert result.salary["text"] == "25-35万"
    assert result.peer["company_jobs"] == 3
    assert result.peer["same_direction_jobs"] == 1
    assert result.bar["level"] in {"要求中等", "要求偏高"}
    assert result.comment_status == "disabled"
    assert "不是真实录取名单" in result.disclaimer


def test_insight_reports_a_missing_job_without_raising() -> None:
    result = build_job_insight(_storage(), "does-not-exist", use_model=False)

    assert result.found is False
    assert result.job == {}
    assert "不在库中" in result.message


def test_insight_requires_a_job_id() -> None:
    with pytest.raises(ValueError):
        build_job_insight(_storage(), "  ", use_model=False)
