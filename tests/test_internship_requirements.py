"""Requirement extraction: values, binding strength, and the "do not invent" rule.

Two things are asserted for every extracted condition: the parsed value and how
binding the JD made it.  A bare mention must never become a mandatory gate.

Fixtures are synthetic.
"""

from __future__ import annotations

import pytest

from packages.internship.models import RequirementStrength, UNLIMITED
from packages.internship.requirements import extract_requirements


def _job(jd_raw: str = "", **extra):
    return {"title": "Python开发实习生", "jd_raw": jd_raw, **extra}


def test_full_jd_is_extracted_with_evidence():
    jd = (
        "岗位职责：参与内部工具开发。\n"
        "任职要求：本科及以上学历，2028届毕业生优先，工作地点：西安，"
        "每周到岗3天，连续实习3个月以上，能尽快到岗。"
    )

    requirements = extract_requirements(_job(jd))

    assert requirements.degree.value == "本科"
    assert requirements.degree.strength is RequirementStrength.MANDATORY
    assert requirements.cohort_year.value == 2028
    assert requirements.cohort_year.strength is RequirementStrength.PREFERRED
    assert requirements.cities.value == ["西安"]
    assert requirements.days_per_week.value == 3
    assert requirements.duration_months.value == 3
    assert requirements.duration_months.strength is RequirementStrength.MANDATORY
    assert requirements.start_date.known is True
    for item in (
        requirements.degree,
        requirements.cities,
        requirements.duration_months,
        requirements.days_per_week,
    ):
        assert item.raw_text, "每个抽取到的条件都必须带 JD 原文片段"
    assert requirements.evidence


def test_sparse_jd_invents_nothing():
    requirements = extract_requirements(_job("岗位职责：参与内部工具开发。"))

    assert requirements.degree.value is None
    assert requirements.cohort_year.value is None
    assert requirements.duration_months.value is None
    assert requirements.days_per_week.value is None
    assert requirements.start_date.value is None
    assert requirements.stated() == {}


def test_unlimited_degree_is_not_confused_with_missing():
    requirements = extract_requirements(_job("学历要求：不限。负责接口开发。"))

    assert requirements.degree.value == UNLIMITED
    assert requirements.degree.known is True
    assert requirements.degree.unlimited is True


@pytest.mark.parametrize(
    "text,expected",
    [
        ("实习期不少于3个月", 3),
        ("连续实习三个月以上", 3),
        ("实习6个月", 6),
        ("实习期 十二 个月", 12),
        ("实习3-6个月", 3),
    ],
)
def test_duration_parsing(text, expected):
    requirements = extract_requirements(_job(f"任职要求：{text}。"))

    assert requirements.duration_months.value == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("每周到岗3天", 3),
        ("每周至少4天", 4),
        ("每周实习5天", 5),
        ("到岗天数：每周2天", 2),
    ],
)
def test_days_per_week_parsing(text, expected):
    requirements = extract_requirements(_job(f"任职要求：{text}。"))

    assert requirements.days_per_week.value == expected


@pytest.mark.parametrize(
    "jd,value,strength",
    [
        ("任职要求：本科及以上。", "本科", RequirementStrength.MANDATORY),
        ("任职要求：本科优先。", "本科", RequirementStrength.PREFERRED),
        ("任职要求：要求本科。", "本科", RequirementStrength.MANDATORY),
        ("团队成员多为本科。", "本科", RequirementStrength.DESCRIPTIVE),
    ],
)
def test_degree_strength_follows_wording(jd, value, strength):
    requirements = extract_requirements(_job(jd))

    assert requirements.degree.value == value
    assert requirements.degree.strength is strength


@pytest.mark.parametrize(
    "jd,strength",
    [
        ("仅限2027届毕业生。", RequirementStrength.MANDATORY),
        ("2027届优先。", RequirementStrength.PREFERRED),
        ("面向2027届毕业生。", RequirementStrength.DESCRIPTIVE),
    ],
)
def test_cohort_strength_follows_wording(jd, strength):
    requirements = extract_requirements(_job(jd))

    assert requirements.cohort_year.value == 2027
    assert requirements.cohort_year.strength is strength


def test_preferred_degree_does_not_override_a_requirement():
    requirements = extract_requirements(_job("任职要求：本科及以上，硕士优先。"))

    assert requirements.degree.value == "本科"
    assert requirements.degree.strength is RequirementStrength.MANDATORY


def test_applicant_experience_is_not_a_requirement():
    """"有3个月实习经历者优先" 不能变成"要求实习3个月"。"""

    requirements = extract_requirements(
        _job("任职要求：有3个月实习经历者优先；具备相关项目经验。")
    )

    assert requirements.duration_months.value is None


def test_explicit_rejection_is_captured_verbatim():
    requirements = extract_requirements(_job("任职要求：连续实习3个月以上，不接受短期实习。"))

    assert requirements.duration_months.value == 3
    assert any("不接受短期实习" in item for item in requirements.explicit_rejections)


def test_explicit_flexibility_is_captured():
    requirements = extract_requirements(_job("任职要求：实习时长可协商。"))

    assert requirements.explicit_flexibilities


def test_city_comes_from_structured_field_first():
    requirements = extract_requirements(_job("工作地点：北京", city="西安市"))

    assert requirements.cities.value == ["西安"]
    assert requirements.cities.source == "struct"


def test_multiple_cities_are_split_and_normalised():
    requirements = extract_requirements(_job("工作地点：西安、郑州市/北京"))

    assert requirements.cities.value == ["西安", "郑州", "北京"]


def test_jd_without_city_yields_unknown():
    requirements = extract_requirements(_job("负责内部工具开发。"))

    assert requirements.cities.value is None
