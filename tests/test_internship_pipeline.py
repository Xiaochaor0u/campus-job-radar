"""TASK-004: end-to-end internship flow through the real pipeline and storage.

These are **pipeline integration tests**.  They drive ``run_daily_pipeline`` with an
injected fake crawler and a deterministic fixture hydrator, on an isolated SQLite
database, and then read the results back through the production repository.  No
part of the business logic is mocked: the crawl result is normalised, screened,
hydrated, scored by the deterministic matcher, persisted through
``upsert_job_snapshot``, and queried through ``browse_jobs``.

They are NOT a validation against a real recruitment site.  Every posting below
is synthetic text written for this file.

Scenarios covered:
    normal internship, duration gap, mandatory degree gate, explicit cohort gate,
    unknown conditions, non-internship posting in internship mode,
    duplicate collection (idempotent re-run), campus default compatibility,
    database read through the browse path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select

from packages.matching.policy import ScreeningMode, ScreeningPolicy
from packages.pipeline import CrawlResult, run_daily_pipeline
from packages.repositories.postgres import PostgresRecruitmentRepository
from packages.storage import Storage
from packages.storage.models import JobAnalysisSnapshot, JobSnapshot

INTERNSHIP = ScreeningPolicy(mode=ScreeningMode.INTERNSHIP)
CAMPUS = ScreeningPolicy()

INTERNSHIP_PROFILE: dict[str, Any] = {
    "degree": "本科",
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["西安", "郑州"],
        "available_months": {"summer": 2},
        "max_days_per_week": 3,
    },
}


def _jd(body: str) -> str:
    return (
        "岗位职责：参与内部工具与业务系统的开发、测试、上线与线上问题定位，"
        "配合产品与测试同学完成需求交付。\n"
        "任职要求：" + body + "\n其他说明：团队提供完整的新人培养计划与阶段性反馈。"
    )


INTERN_OK_JD = _jd(
    "本科及以上，2028届，工作地点：西安，能连续实习3个月以上，每周到岗3天，表现优秀可转正。"
)
INTERN_DURATION_JD = _jd(
    "本科及以上，2028届，工作地点：西安，需连续实习6个月以上，每周到岗3天。"
)
INTERN_DEGREE_JD = _jd(
    "硕士及以上学历，2028届，工作地点：西安，能连续实习3个月以上，每周到岗3天。"
)
INTERN_COHORT_JD = _jd(
    "本科及以上，仅限2027届毕业生，工作地点：西安，能连续实习3个月以上，每周到岗3天。"
)
INTERN_UNKNOWN_JD = _jd(
    "具备良好的沟通能力与团队协作意识，能适应快节奏的研发环境，有较强的学习能力。"
)
CAMPUS_JD = _jd("计算机相关专业，熟悉 C++、Linux，具备良好的编程基础。")


def _capture_evidence(detail: str, source_url: str) -> dict[str, Any]:
    normalized = detail.strip()
    return {
        "status": "complete",
        "method": "fixture_detail",
        "source_url": source_url,
        "identity_verified": True,
        "terminal_observed": True,
        "remaining_controls": [],
        "content_sha256": sha256(normalized.encode("utf-8")).hexdigest(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _intern(job_id: str, jd_raw: str, **overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": job_id,
        "title": f"Python开发暑期实习生 {job_id}",
        "city": "西安",
        "job_type": "实习",
        "batch": "internship",
        "cohort": None,
        "cohort_status": "unconfirmed",
        "detail_url": f"https://example.test/intern/{job_id}",
        "jd_raw": jd_raw,
        "capture_evidence": _capture_evidence(jd_raw, f"https://example.test/intern/{job_id}"),
    }
    job.update(overrides)
    return job


def _campus(job_id: str, jd_raw: str = CAMPUS_JD) -> dict[str, Any]:
    return {
        "id": job_id,
        "title": f"C++软件开发工程师 {job_id}",
        "city": "西安",
        "job_type": "校园招聘",
        "batch": "formal",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "detail_url": f"https://example.test/campus/{job_id}",
        "jd_raw": jd_raw,
        "capture_evidence": _capture_evidence(jd_raw, f"https://example.test/campus/{job_id}"),
    }


def _crawl_result(*jobs: dict[str, Any]) -> CrawlResult:
    return CrawlResult(
        jobs=list(jobs),
        source_url="https://example.test/campus",
        allowed_origins=["https://example.test"],
        pages_seen=1,
        total_pages=1,
        has_more=False,
    )


def _fixture_hydrator(job: dict[str, Any]) -> dict[str, Any]:
    detail = job.get("jd_raw") or CAMPUS_JD
    detail_url = str(job["detail_url"])
    return {
        "detail": detail,
        "status": "complete",
        "detail_url": detail_url,
        "source": "fixture_detail",
        "capture_evidence": _capture_evidence(detail, detail_url),
    }


class CountingMatcher:
    """Deterministic matcher that records which postings reached scoring."""

    def __init__(self, profile: Any = None, *, policy: Any = None) -> None:
        from packages.pipeline import DeterministicMatcher

        self._inner = DeterministicMatcher(profile, policy=policy)
        self.calls: list[str] = []

    def match(self, job: dict[str, Any], *, existing_analysis: Any = None) -> Any:
        self.calls.append(str(job.get("id")))
        return self._inner.match(job, existing_analysis=existing_analysis)


def _companies_file(tmp_path: Path) -> Path:
    path = tmp_path / "companies.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "companies": [
                    {
                        "id": "example",
                        "name": "Example Co",
                        "careers_url": "https://example.test/campus",
                        "crawler": "fake",
                        "integration_status": "connected",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def _run(
    tmp_path: Path,
    jobs: list[dict[str, Any]],
    *,
    policy: ScreeningPolicy,
    profile: Any = None,
    matcher: Any = None,
    storage: Storage | None = None,
) -> tuple[Any, Storage]:
    store = storage or Storage.from_url("sqlite:///:memory:")
    if storage is None:
        store.initialize()
    result = run_daily_pipeline(
        companies_path=_companies_file(tmp_path),
        storage=store,
        crawler=lambda company: _crawl_result(*jobs),
        jd_hydrator=_fixture_hydrator,
        matcher=matcher,
        profile=profile,
        screening_policy=policy,
        max_concurrency=1,
        detail_max_concurrency=1,
        match_max_concurrency=1,
    )
    return result, store


def _rows(storage: Storage) -> dict[str, JobSnapshot]:
    with storage.session() as session:
        return {row.id: row for row in session.scalars(select(JobSnapshot))}


def _analysis(storage: Storage) -> dict[str, JobAnalysisSnapshot]:
    with storage.session() as session:
        return {row.job_id: row for row in session.scalars(select(JobAnalysisSnapshot))}


def _condition(row: JobSnapshot, name: str) -> dict[str, Any]:
    evaluation = row.match_conditions or {}
    for item in evaluation.get("conditions", []):
        if item["name"] == name:
            return item
    raise AssertionError(f"condition {name!r} missing from persisted evaluation")


# --- 1. normal internship --------------------------------------------------


def test_normal_internship_is_persisted_and_queryable(tmp_path: Path) -> None:
    matcher = CountingMatcher(INTERNSHIP_PROFILE, policy=INTERNSHIP)
    _, storage = _run(
        tmp_path,
        [_intern("intern-ok", INTERN_OK_JD)],
        policy=INTERNSHIP,
        profile=INTERNSHIP_PROFILE,
        matcher=matcher,
    )

    rows = _rows(storage)
    row = rows["intern-ok"]

    assert matcher.calls == ["intern-ok"]
    assert row.track == "internship"
    assert row.internship_seasons == ["summer"]
    assert row.conversion_opportunity == "yes"
    assert row.availability_status == "active"
    assert row.internship_requirements is not None
    assert row.internship_requirements["duration_months"]["value"] == 3
    assert row.match_conditions is not None
    assert row.match_conditions["qualification"] == "qualified"

    analysis = _analysis(storage)["intern-ok"]
    assert analysis.analysis_status == "eligible"
    assert analysis.analysis_version == "deterministic-v1"

    repo = PostgresRecruitmentRepository(storage)
    page = repo.browse_jobs(track="internship")
    assert [item.id for item in page.items] == ["intern-ok"]
    assert page.items[0].track == "internship"
    assert page.items[0].internship_seasons == ["summer"]


# --- 2. duration gap -------------------------------------------------------


def test_duration_gap_is_retained_with_confirmation_required(tmp_path: Path) -> None:
    _, storage = _run(
        tmp_path,
        [_intern("intern-long", INTERN_DURATION_JD)],
        policy=INTERNSHIP,
        profile=INTERNSHIP_PROFILE,
    )

    row = _rows(storage)["intern-long"]
    duration = _condition(row, "duration_months")

    assert row.availability_status == "active"
    assert duration["status"] == "gap"
    assert duration["negotiability"] == "unknown"
    assert duration["confirmation_required"] is True
    assert row.match_conditions["retained"] is True
    # 资格没被否定，所以仍然进入评分
    assert row.match_conditions["qualification"] == "qualified"


# --- 3. mandatory degree gate ---------------------------------------------


def test_mandatory_degree_gate_is_retained_but_not_scored(tmp_path: Path) -> None:
    matcher = CountingMatcher(INTERNSHIP_PROFILE, policy=INTERNSHIP)
    _, storage = _run(
        tmp_path,
        [_intern("intern-degree", INTERN_DEGREE_JD)],
        policy=INTERNSHIP,
        profile=INTERNSHIP_PROFILE,
        matcher=matcher,
    )

    row = _rows(storage)["intern-degree"]
    analysis = _analysis(storage)["intern-degree"]

    assert row.availability_status == "active"          # 保留，不是删除
    assert row.track == "internship"
    assert row.match_conditions["qualification"] == "not_qualified"
    assert row.match_conditions["retained"] is True
    assert analysis.analysis_status == "not_qualified"
    assert analysis.match_score is None
    assert "intern-degree" not in matcher.calls        # 未调用评分器


# --- 4. explicit cohort gate ----------------------------------------------


def test_explicit_jd_cohort_requirement_is_judged_independently(tmp_path: Path) -> None:
    """实习模式关掉了 2027 流水线门槛，但 JD 里写明的届别仍然被判断。"""

    _, storage = _run(
        tmp_path,
        [_intern("intern-cohort", INTERN_COHORT_JD)],
        policy=INTERNSHIP,
        profile=INTERNSHIP_PROFILE,
    )

    row = _rows(storage)["intern-cohort"]
    cohort = _condition(row, "cohort_year")

    assert cohort["required"] == 2027
    assert cohort["status"] == "gap"
    assert cohort["strength"] == "mandatory"
    assert row.match_conditions["qualification"] == "not_qualified"
    assert row.availability_status == "active"


# --- 5. unknown conditions -------------------------------------------------


def test_unknown_conditions_are_recorded_as_unknown_and_kept(tmp_path: Path) -> None:
    _, storage = _run(
        tmp_path,
        [_intern("intern-unknown", INTERN_UNKNOWN_JD, city=None)],
        policy=INTERNSHIP,
        profile=INTERNSHIP_PROFILE,
    )

    row = _rows(storage)["intern-unknown"]
    duration = _condition(row, "duration_months")
    degree = _condition(row, "degree")

    assert duration["status"] == "unknown"
    assert degree["status"] == "unknown"
    assert row.match_conditions["gaps"] == []
    assert row.match_conditions["qualification"] == "undetermined"
    assert row.availability_status == "active"


# --- 6. non-internship posting in internship mode --------------------------


def test_non_internship_posting_is_not_kept_in_the_internship_view(tmp_path: Path) -> None:
    result, storage = _run(
        tmp_path, [_campus("campus-1")], policy=INTERNSHIP, profile=INTERNSHIP_PROFILE
    )

    rows = _rows(storage)
    # The provisional list row stays auditable, but it is retired and never
    # classified as an internship.
    assert rows["campus-1"].availability_status == "inactive"
    assert rows["campus-1"].track == "unknown"
    assert result.rejection_reasons.get("not_internship") == 1

    repo = PostgresRecruitmentRepository(storage)
    assert repo.browse_jobs(track="internship").items == []


# --- 7. duplicate collection is idempotent ---------------------------------


def test_repeated_collection_does_not_duplicate_rows(tmp_path: Path) -> None:
    job = _intern("intern-ok", INTERN_OK_JD)
    _, storage = _run(
        tmp_path, [job], policy=INTERNSHIP, profile=INTERNSHIP_PROFILE
    )
    first = _rows(storage)
    first_analysis = _analysis(storage)["intern-ok"]

    # 同一公司、同一岗位、同一 JD 再跑一次
    _, storage = _run(
        tmp_path, [job], policy=INTERNSHIP, profile=INTERNSHIP_PROFILE, storage=storage
    )
    second = _rows(storage)

    assert list(second) == ["intern-ok"]
    assert len(second) == len(first) == 1
    assert second["intern-ok"].track == "internship"
    assert second["intern-ok"].first_seen_at == first["intern-ok"].first_seen_at
    assert second["intern-ok"].match_conditions == first["intern-ok"].match_conditions
    assert _analysis(storage)["intern-ok"].analysis_status == first_analysis.analysis_status


# --- 8. campus compatibility ----------------------------------------------


def test_campus_default_still_rejects_internships_and_keeps_campus_rows(tmp_path: Path) -> None:
    _, storage = _run(
        tmp_path,
        [_intern("intern-ok", INTERN_OK_JD), _campus("campus-1")],
        policy=CAMPUS,
        profile=INTERNSHIP_PROFILE,
    )

    rows = _rows(storage)

    assert "intern-ok" not in rows          # 校招默认行为不变
    assert rows["campus-1"].track == "unknown"   # 未分类，没有被伪造成 campus
    assert rows["campus-1"].match_conditions is None

    repo = PostgresRecruitmentRepository(storage)
    assert [item.id for item in repo.browse_jobs().items] == ["campus-1"]
    assert repo.browse_jobs(track="internship").items == []


# --- 9. all mode -----------------------------------------------------------


def test_all_mode_stores_both_tracks_and_each_query_returns_its_own(tmp_path: Path) -> None:
    _, storage = _run(
        tmp_path,
        [_intern("intern-ok", INTERN_OK_JD), _campus("campus-1")],
        policy=ScreeningPolicy(mode=ScreeningMode.ALL),
        profile=INTERNSHIP_PROFILE,
    )

    rows = _rows(storage)
    assert set(rows) == {"intern-ok", "campus-1"}
    assert rows["intern-ok"].track == "internship"
    assert rows["campus-1"].track == "unknown"

    repo = PostgresRecruitmentRepository(storage)
    assert [item.id for item in repo.browse_jobs(track="internship").items] == ["intern-ok"]
    assert [item.id for item in repo.browse_jobs(track="campus").items] == ["campus-1"]
    assert {item.id for item in repo.browse_jobs(track="all").items} == {"intern-ok", "campus-1"}


def test_campus_run_cannot_erase_existing_internship_columns(tmp_path: Path) -> None:
    job = _intern("intern-ok", INTERN_OK_JD)
    _, storage = _run(
        tmp_path, [job], policy=INTERNSHIP, profile=INTERNSHIP_PROFILE
    )
    assert _rows(storage)["intern-ok"].track == "internship"

    # 随后用 campus 策略再跑一次：岗位会被校招门拒掉，但不能把已有分类清零。
    _run(tmp_path, [job], policy=CAMPUS, profile=INTERNSHIP_PROFILE, storage=storage)
    row = _rows(storage)["intern-ok"]

    assert row.track == "internship"
    assert row.match_conditions is not None


# --- policy guard ----------------------------------------------------------


@pytest.mark.parametrize(
    "mode,cohort",
    [
        (ScreeningMode.INTERNSHIP, "require_confirmed_2027"),
    ],
)
def test_contradictory_policy_is_rejected(mode, cohort) -> None:
    from packages.matching.policy import CohortPolicy

    with pytest.raises(ValueError):
        ScreeningPolicy(mode=mode, cohort=CohortPolicy(cohort))


def test_campus_query_never_surfaces_a_classified_internship(tmp_path: Path) -> None:
    """历史兼容：track=unknown 的行仍走 campus 门，但已知实习必须被排除。"""

    from packages.repositories.postgres import PostgresRecruitmentRepository
    from packages.storage.models import JobSnapshot

    storage = Storage.from_url("sqlite:///:memory:")
    storage.initialize()
    with storage.write_transaction() as session:
        session.add(
            JobSnapshot(
                id="legacy-unknown",
                source_ref="legacy-1",
                company_id="example",
                title="C++软件开发工程师 legacy",
                detail_url="https://example.test/legacy",
                cohort=2027,
                cohort_status="confirmed",
                batch="formal",
                source="test",
                track="unknown",
            )
        )
        session.add(
            JobSnapshot(
                id="intern-with-campus-cohort",
                source_ref="intern-2",
                company_id="example",
                title="Python开发实习生",
                detail_url="https://example.test/intern2",
                cohort=2027,
                cohort_status="confirmed",
                batch="formal",
                source="test",
                track="internship",
            )
        )

    repo = PostgresRecruitmentRepository(storage)

    assert [item.id for item in repo.browse_jobs(track="campus").items] == ["legacy-unknown"]
    assert [item.id for item in repo.browse_jobs(track="internship").items] == [
        "intern-with-campus-cohort"
    ]


def test_checkpoint_same_mode_resume_is_accepted(tmp_path: Path) -> None:
    """反向证明：拒绝的是跨模式恢复，不是所有恢复。"""

    checkpoint = tmp_path / "checkpoint.json"
    config = _companies_file(tmp_path)
    storage = Storage.from_url("sqlite:///:memory:")
    storage.initialize()
    kwargs = dict(
        companies_path=config,
        storage=storage,
        crawler=lambda company: _crawl_result(_intern("intern-ok", INTERN_OK_JD)),
        jd_hydrator=_fixture_hydrator,
        profile=INTERNSHIP_PROFILE,
        screening_policy=INTERNSHIP,
        checkpoint_path=checkpoint,
        max_concurrency=1,
        detail_max_concurrency=1,
        match_max_concurrency=1,
    )
    run_daily_pipeline(**kwargs)
    assert checkpoint.exists()

    resumed = run_daily_pipeline(**kwargs, resume_from_checkpoint=True)

    assert resumed.selected_companies == 1


def test_checkpoint_scope_isolates_screening_modes(tmp_path: Path) -> None:
    """A checkpoint written by one mode must not be resumed by the other."""

    from packages.pipeline import PipelineError

    checkpoint = tmp_path / "checkpoint.json"
    config = _companies_file(tmp_path)
    storage = Storage.from_url("sqlite:///:memory:")
    storage.initialize()

    run_daily_pipeline(
        companies_path=config,
        storage=storage,
        crawler=lambda company: _crawl_result(_intern("intern-ok", INTERN_OK_JD)),
        jd_hydrator=_fixture_hydrator,
        profile=INTERNSHIP_PROFILE,
        screening_policy=INTERNSHIP,
        checkpoint_path=checkpoint,
        max_concurrency=1,
        detail_max_concurrency=1,
        match_max_concurrency=1,
    )
    assert checkpoint.exists()

    with pytest.raises(PipelineError):
        run_daily_pipeline(
            companies_path=config,
            storage=storage,
            crawler=lambda company: _crawl_result(_intern("intern-ok", INTERN_OK_JD)),
            jd_hydrator=_fixture_hydrator,
            profile=INTERNSHIP_PROFILE,
            screening_policy=CAMPUS,
            checkpoint_path=checkpoint,
            resume_from_checkpoint=True,
            max_concurrency=1,
            detail_max_concurrency=1,
            match_max_concurrency=1,
        )


def test_allowed_batches_follow_the_mode() -> None:
    assert CAMPUS.allowed_batches == ("formal", "early")
    assert "internship" in INTERNSHIP.allowed_batches
    assert "internship" in ScreeningPolicy(mode=ScreeningMode.ALL).allowed_batches
