"""TASK-003: internship mode wired into the existing screening flow.

Two properties are asserted here and they pull in opposite directions:

* **Campus behaviour must not move.**  Every historical outcome is reproduced by
  ``screen_job`` with no policy, including "Python开发工程师" staying
  ``direction_out`` — the gap TASK-003 is *not* allowed to silently close for
  campus users.
* **Internship mode must actually work.**  Internships pass the internship gate,
  skip the 2027 cohort gate, gain the Python direction, and carry the extracted
  requirements plus the condition evaluation on the result.

Fixtures are synthetic.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import pytest

from packages.matching.models import (
    INTERNSHIP_DIRECTIONS,
    LEGACY_DIRECTIONS,
    AnalysisStatus,
    Direction,
)
from packages.matching.policy import (
    CohortPolicy,
    ScreeningMode,
    ScreeningPolicy,
    resolve_policy,
)
from packages.matching.rules import screen_job
from packages.matching.title_policy import screen_title_job
from packages.recruitment_core import job_filters

CAMPUS = ScreeningPolicy()
INTERNSHIP = ScreeningPolicy(mode=ScreeningMode.INTERNSHIP)
ALL = ScreeningPolicy(mode=ScreeningMode.ALL)


def _jd(body: str) -> str:
    return (
        "岗位职责：参与业务系统的开发、测试与线上问题定位。\n"
        "任职要求：" + body
    )


CAMPUS_JD = _jd("计算机相关专业，熟悉 C++、Linux，具备良好的编程基础。")
PYTHON_JD = _jd("熟悉 Python 与 Django，具备良好的编程基础。")
INTERNSHIP_JD = _jd(
    "本科及以上，2028届，工作地点：西安，"
    "能连续实习3个月以上，每周到岗3天。表现优秀可转正。"
)


def _job(
    *,
    title: str = "C++软件开发工程师",
    jd_raw: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": "internops-screening-job",
        "title": title,
        "job_type": "校园招聘",
        "batch": "formal",
        "cohort": 2027,
        "cohort_status": "confirmed",
        "city": "西安",
        "jd_raw": jd_raw if jd_raw is not None else CAMPUS_JD,
    }
    job.update(overrides)
    job.setdefault(
        "capture_evidence",
        {
            "status": "complete",
            "method": "test_fixture",
            "source_url": "https://example.test/jobs/internops",
            "identity_verified": True,
            "terminal_observed": True,
            "remaining_controls": [],
            "content_sha256": sha256(job["jd_raw"].encode("utf-8")).hexdigest(),
        },
    )
    return job


def _internship_job(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Python开发暑期实习生",
        "job_type": "实习",
        "batch": "unknown",
        "cohort": None,
        "cohort_status": "unconfirmed",
        "jd_raw": INTERNSHIP_JD,
    }
    payload.update(overrides)
    return _job(**payload)


def _candidate() -> dict[str, Any]:
    return {
        "degree": "本科",
        "internship": {
            "graduation": "2028-06",
            "preferred_cities": ["西安", "郑州"],
            "available_months": {"summer": 2},
            "max_days_per_week": 3,
        },
    }


def _condition(result: Any, name: str) -> dict[str, Any]:
    evaluation = result.condition_evaluation or {}
    for item in evaluation.get("conditions", []):
        if item["name"] == name:
            return item
    raise AssertionError(f"condition {name!r} missing from evaluation")


# --- policy semantics ------------------------------------------------------


def test_default_policy_reproduces_the_campus_gate():
    policy = ScreeningPolicy()

    assert policy.mode is ScreeningMode.CAMPUS
    assert policy.requires_confirmed_2027 is True
    assert policy.accepts_internships is False
    assert policy.requires_internship is False
    assert policy.direction_fallback == LEGACY_DIRECTIONS


def test_internship_policy_drops_the_cohort_gate_and_adds_python():
    assert INTERNSHIP.cohort_policy is CohortPolicy.ANY
    assert INTERNSHIP.requires_confirmed_2027 is False
    assert INTERNSHIP.requires_internship is True
    assert INTERNSHIP.direction_fallback == INTERNSHIP_DIRECTIONS
    assert Direction.PYTHON_DEV in INTERNSHIP.direction_fallback


def test_legacy_include_internships_flag_maps_to_all_mode():
    assert resolve_policy(include_internships=True).mode is ScreeningMode.ALL
    assert resolve_policy(include_internships=False).mode is ScreeningMode.CAMPUS
    assert resolve_policy(INTERNSHIP).mode is ScreeningMode.INTERNSHIP


# --- campus compatibility --------------------------------------------------


@pytest.mark.parametrize(
    "job,expected",
    [
        (_job(), AnalysisStatus.ELIGIBLE),
        (_job(cohort=None, cohort_status="unconfirmed"), AnalysisStatus.COHORT_UNCONFIRMED),
        # The internship gate does not move: campus still rejects internships.
        # The cohort gate still runs first when the posting has no confirmed
        # cohort, which is the historical order.
        (_internship_job(), AnalysisStatus.COHORT_UNCONFIRMED),
        (
            _internship_job(cohort=2027, cohort_status="confirmed"),
            AnalysisStatus.INTERNSHIP,
        ),
        # The known gap is preserved for campus: Python has no campus direction.
        (_job(title="Python开发工程师", jd_raw=PYTHON_JD), AnalysisStatus.DIRECTION_OUT),
        (
            _job(title="销售实习生", jd_raw=_jd("本科及以上，沟通能力强。")),
            AnalysisStatus.INTERNSHIP,
        ),
        (
            _job(title="销售经理", jd_raw=_jd("本科及以上，三年以上渠道销售经验。")),
            AnalysisStatus.DIRECTION_OUT,
        ),
    ],
)
def test_campus_outcomes_are_identical_with_and_without_an_explicit_policy(job, expected):
    assert screen_job(job, None).analysis_status is expected
    assert screen_job(job, None, policy=CAMPUS).analysis_status is expected


def test_campus_default_directions_do_not_gain_python():
    classification_direction = screen_job(
        _job(title="Python开发工程师", jd_raw=PYTHON_JD), None
    )

    assert classification_direction.matched_directions == []
    assert classification_direction.analysis_status is AnalysisStatus.DIRECTION_OUT


# --- internship mode -------------------------------------------------------


def test_internship_job_passes_without_a_2027_cohort():
    result = screen_job(_internship_job(), _candidate(), policy=INTERNSHIP)

    assert result.eligible is True
    assert result.analysis_status is AnalysisStatus.ELIGIBLE
    assert result.track == "internship"
    assert result.seasons == ["summer"]
    assert result.conversion_opportunity == "yes"


def test_internship_result_carries_requirements_and_evaluation():
    result = screen_job(_internship_job(), _candidate(), policy=INTERNSHIP)

    assert result.internship_requirements is not None
    assert result.internship_requirements["duration_months"]["value"] == 3
    assert result.condition_evaluation is not None
    assert result.condition_evaluation["qualification"] == "qualified"


def test_duration_shortfall_keeps_the_posting_and_asks_for_confirmation():
    result = screen_job(_internship_job(), _candidate(), policy=INTERNSHIP)

    duration = _condition(result, "duration_months")

    assert result.eligible is True
    assert duration["status"] == "gap"
    assert duration["negotiability"] == "unknown"
    assert duration["confirmation_required"] is True
    assert result.condition_evaluation["retained"] is True


def test_mandatory_degree_gate_marks_not_qualified_but_keeps_the_row():
    job = _internship_job(
        jd_raw=_jd("硕士及以上，2028届，工作地点：西安，能连续实习3个月以上。")
    )

    result = screen_job(job, _candidate(), policy=INTERNSHIP)

    assert result.eligible is False
    assert result.analysis_status is AnalysisStatus.NOT_QUALIFIED
    assert "degree_not_met" in result.reasons
    # 资格判断不负责删除：评测结果依然 retained，供上游决定是否保留。
    assert result.condition_evaluation["retained"] is True
    assert result.track == "internship"


def test_internship_mode_rejects_non_internship_postings():
    result = screen_job(_job(), _candidate(), policy=INTERNSHIP)

    assert result.eligible is False
    assert result.analysis_status is AnalysisStatus.NOT_INTERNSHIP


def test_internship_mode_gains_the_python_direction():
    job = _job(
        title="Python开发工程师",
        job_type="实习",
        jd_raw=PYTHON_JD,
    )

    result = screen_job(job, _candidate(), policy=INTERNSHIP)

    assert Direction.PYTHON_DEV in result.matched_directions
    assert result.primary_match_direction is Direction.PYTHON_DEV


def test_all_mode_judges_each_track_by_its_own_rules():
    internship = screen_job(_internship_job(), _candidate(), policy=ALL)
    campus = screen_job(_job(), _candidate(), policy=ALL)
    odd_campus = screen_job(_job(cohort=None, cohort_status="unconfirmed"), _candidate(), policy=ALL)

    assert internship.eligible is True and internship.track == "internship"
    assert campus.eligible is True
    assert odd_campus.analysis_status is AnalysisStatus.COHORT_UNCONFIRMED


def test_title_policy_tags_the_track_when_internships_are_accepted():
    tag_internship = screen_title_job(_internship_job(), None, policy=INTERNSHIP)
    legacy_flag = screen_title_job(_internship_job(), None, include_internships=True)

    assert tag_internship.eligible is True
    assert tag_internship.track == "internship"
    assert tag_internship.seasons == ["summer"]
    assert legacy_flag.eligible is True
    assert legacy_flag.track == "internship"


def test_title_policy_still_rejects_internships_by_default():
    assert screen_title_job(_internship_job(), None).eligible is False


# --- G4: one internship implementation -------------------------------------


@pytest.mark.parametrize(
    "job",
    [
        {"title": "测试开发实习生"},
        {"title": "测试开发工程师", "job_type": "实习"},
        {"title": "2027届校园招聘-测试开发"},
        {"title": "软件工程师", "jd_raw": "任职要求：有相关实习经历者优先"},
        {"title": "软件工程师", "jd_raw": "实习期不少于3个月，每周到岗3天"},
        {"title": "Internal Tools Engineer"},
    ],
)
def test_job_filters_and_matching_share_one_internship_classifier(job):
    from packages.matching.rules import internship_reason as matching_reason

    assert job_filters.internship_reason(job) == matching_reason(job)


def test_job_filters_still_excludes_internships_from_formal_campus():
    kept, dropped = job_filters.filter_formal_campus_jobs(
        [{"id": "1", "title": "C++开发工程师"}, {"id": "2", "title": "测试开发实习生"}]
    )

    assert [job["id"] for job in kept] == ["1"]
    assert [job["id"] for job in dropped] == ["2"]
