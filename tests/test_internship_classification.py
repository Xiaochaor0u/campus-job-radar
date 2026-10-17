"""Track, season, and conversion classification.

Season and conversion are asserted independently, because one posting can be a
summer internship *and* offer conversion.  No conversion statement must stay
``UNKNOWN`` rather than becoming ``NO``.

Every fixture is synthetic test data written for this file; it is not collected
from a real site and must never be presented as recruitment data.
"""

from __future__ import annotations

import pytest

from packages.internship.classification import (
    classify_track,
    internship_reason,
    is_internship,
    is_social,
)
from packages.internship.models import (
    ConversionOpportunity,
    InternshipSeason,
    Track,
)


@pytest.mark.parametrize(
    "job,expected_seasons",
    [
        ({"title": "Python开发实习生"}, []),
        ({"title": "2027暑期实习-测试开发"}, [InternshipSeason.SUMMER]),
        ({"title": "寒假实习生（西安）"}, [InternshipSeason.WINTER]),
        ({"title": "日常实习生-数据分析"}, [InternshipSeason.DAILY]),
        ({"title": "Software Engineer Intern"}, []),
        ({"title": "软件开发工程师", "job_type": "实习"}, []),
        ({"title": "软件开发工程师", "batch": "internship"}, []),
        (
            {"title": "软件开发工程师", "jd_raw": "招聘项目：2027暑期实习计划"},
            [InternshipSeason.SUMMER],
        ),
        (
            {"title": "软件开发工程师", "jd_url": "https://example.test/job/internship/1"},
            [],
        ),
    ],
)
def test_internship_is_recognised(job, expected_seasons):
    decision = classify_track(job)

    assert decision.track is Track.INTERNSHIP
    assert decision.seasons == expected_seasons
    assert decision.confidence >= 0.75
    assert decision.evidence, "分类必须留下可审计的证据"


def test_summer_and_conversion_are_independent_attributes():
    """既能转正又是暑期实习，两个属性都要保留，不能二选一。"""

    decision = classify_track({"title": "可转正暑期实习-后端开发"})

    assert decision.track is Track.INTERNSHIP
    assert decision.seasons == [InternshipSeason.SUMMER]
    assert decision.conversion_opportunity is ConversionOpportunity.YES


def test_multiple_seasons_are_all_kept():
    decision = classify_track({"title": "暑期/寒假实习-测试开发"})

    assert set(decision.seasons) == {InternshipSeason.SUMMER, InternshipSeason.WINTER}
    assert decision.primary_season is InternshipSeason.SUMMER


@pytest.mark.parametrize(
    "jd,expected",
    [
        ("表现优秀可转正，有留用机会。", ConversionOpportunity.YES),
        ("本岗位无转正机会。", ConversionOpportunity.NO),
        ("不提供转正。", ConversionOpportunity.NO),
        ("参与内部工具开发。", ConversionOpportunity.UNKNOWN),
    ],
)
def test_conversion_is_tri_state(jd, expected):
    decision = classify_track({"title": "软件开发实习生", "jd_raw": jd})

    assert decision.conversion_opportunity is expected


def test_absence_of_conversion_statement_is_unknown_not_no():
    decision = classify_track({"title": "测试开发实习生", "jd_raw": "负责接口自动化测试。"})

    assert decision.conversion_opportunity is ConversionOpportunity.UNKNOWN


@pytest.mark.parametrize(
    "job",
    [
        {"title": "Internal Tools Engineer"},
        {"title": "Internalization Platform Engineer"},
        {"title": "软件工程师", "jd_raw": "任职要求：有相关实习经历者优先"},
        {"title": "软件工程师", "jd_raw": "具备3个月实习经验者优先"},
        {"title": "软件工程师", "jd_raw": "有暑期实习经历更佳"},
    ],
)
def test_internship_lookalikes_are_not_matched(job):
    decision = classify_track(job)

    assert decision.track is not Track.INTERNSHIP
    assert is_internship(job) is False


def test_duration_requirement_proves_internship():
    job = {"title": "软件开发工程师", "jd_raw": "实习期不少于3个月，每周到岗3天"}

    decision = classify_track(job)

    assert decision.track is Track.INTERNSHIP
    assert decision.evidence[0].signal == "internship_commitment"


@pytest.mark.parametrize(
    "job",
    [
        {"title": "2027届校园招聘-软件开发"},
        {"title": "C++工程师", "batch": "formal"},
        {"title": "C++工程师", "batch": "early"},
        {"title": "C++工程师", "jd_raw": "本岗位属于校园招聘，面向应届毕业生"},
    ],
)
def test_campus_is_recognised(job):
    assert classify_track(job).track is Track.CAMPUS


@pytest.mark.parametrize(
    "job",
    [
        {"title": "高级后端工程师", "jd_raw": "招聘类型：社会招聘"},
        {"title": "资深算法工程师"},
        {"title": "后端工程师", "jd_url": "https://example.test/social/recruitment/1"},
    ],
)
def test_social_is_recognised(job):
    assert classify_track(job).track is Track.SOCIAL
    assert is_social(job) is True


@pytest.mark.parametrize(
    "job",
    [
        {},
        {"title": "软件开发工程师"},
        {"title": "机械臂运动控制工程师", "jd_raw": "负责机械臂轨迹规划。"},
    ],
)
def test_no_evidence_is_unknown_not_campus(job):
    decision = classify_track(job)

    assert decision.track is Track.UNKNOWN
    assert decision.confidence == 0.0
    assert decision.evidence[0].signal == "no_track_evidence"


@pytest.mark.parametrize(
    "job,expected_fragment",
    [
        ({"title": "测试开发实习生"}, "标题明确标注实习"),
        ({"title": "测试开发工程师", "batch": "2027实习"}, "招聘批次明确标注实习"),
        ({"title": "测试开发工程师", "job_type": "实习生"}, "招聘类型明确标注实习"),
    ],
)
def test_internship_reason_is_human_readable(job, expected_fragment):
    reason = internship_reason(job)

    assert reason is not None
    assert expected_fragment in reason


def test_internship_reason_is_none_for_campus():
    assert internship_reason({"title": "2027届校园招聘-测试开发"}) is None


def test_internship_wins_over_inherited_campus_campaign():
    job = {
        "title": "2027届校园招聘",
        "batch": "campus",
        "job_type": "暑期实习",
        "jd_raw": "日常实习岗位，可转正。",
    }

    decision = classify_track(job)

    assert decision.track is Track.INTERNSHIP
    assert InternshipSeason.SUMMER in decision.seasons
    assert decision.conversion_opportunity is ConversionOpportunity.YES
