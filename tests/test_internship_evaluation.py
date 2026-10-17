"""Condition evaluation: qualification, gap, and negotiability are separate.

The assertions that matter most are the ones guarding the reviewer's concerns:

1. A definite shortfall is recorded as ``gap`` even when it might be waivable,
   and ``confirmation_required`` is derived, not substituted.
2. Only a *mandatory* eligibility gate can make someone ``not_qualified``; a
   plain mention or a preference never does.
3. ``not_qualified`` does not delete the row — ``retained`` stays True.

Fixtures are synthetic and reuse the documented default test profile
(graduation 2028-06, preferred cities 西安/郑州, ~2 months in summer).
"""

from __future__ import annotations

from packages.internship.evaluation import evaluate_conditions, preferences_from_profile
from packages.internship.models import (
    ConditionName,
    ConditionResult,
    ConditionStatus,
    InternshipPreferences,
    InternshipRequirements,
    InternshipSeason,
    Negotiability,
    Qualification,
    RequirementStrength,
    RequirementValue,
    SeasonAvailability,
)
from packages.internship.requirements import extract_requirements


def _profile(**overrides) -> InternshipPreferences:
    base = dict(
        graduation="2028-06",
        degree="本科",
        preferred_cities=["西安", "郑州"],
        availability=SeasonAvailability(summer_months=2, winter_months=1.5),
        max_days_per_week=3,
        earliest_start="2028-07",
    )
    base.update(overrides)
    return InternshipPreferences(**base)


def _requirements(
    duration=None, days=None, degree=None, cities=None,
    degree_strength=RequirementStrength.DESCRIPTIVE, **overrides
) -> InternshipRequirements:
    base = dict(
        degree=RequirementValue(
            value=degree,
            raw_text="本科及以上" if degree else "",
            strength=degree_strength if degree else RequirementStrength.UNKNOWN,
        ),
        cities=RequirementValue(value=cities, raw_text="工作地点：西安" if cities else ""),
        duration_months=RequirementValue(
            value=duration,
            raw_text=f"连续实习{duration}个月" if duration else "",
            strength=RequirementStrength.MANDATORY if duration else RequirementStrength.UNKNOWN,
        ),
        days_per_week=RequirementValue(
            value=days,
            raw_text=f"每周到岗{days}天" if days else "",
            strength=RequirementStrength.MANDATORY if days else RequirementStrength.UNKNOWN,
        ),
    )
    base.update(overrides)
    return InternshipRequirements(**base)


SUMMER = [InternshipSeason.SUMMER]


# --- gap vs negotiability --------------------------------------------------


def test_shortfall_is_a_gap_and_negotiability_is_separate():
    """要求3个月/可实习2个月：既记录1个月差距，也记录"可协商性未知"。"""

    evaluation = evaluate_conditions(
        _requirements(duration=3),
        _profile(availability=SeasonAvailability(summer_months=2)),
        seasons=SUMMER,
    )

    duration = evaluation.by_name(ConditionName.DURATION_MONTHS)

    assert duration.status is ConditionStatus.GAP
    assert duration.negotiability is Negotiability.UNKNOWN
    assert duration.confirmation_required is True
    assert "1 个月差距" in duration.note
    assert duration in evaluation.gaps
    assert duration in evaluation.confirmation_required


def test_explicit_refusal_makes_the_gap_non_negotiable():
    evaluation = evaluate_conditions(
        _requirements(
            duration=3,
            explicit_rejections=["不接受短期实习"],
        ),
        _profile(availability=SeasonAvailability(summer_months=2)),
        seasons=SUMMER,
    )

    duration = evaluation.by_name(ConditionName.DURATION_MONTHS)

    assert duration.status is ConditionStatus.GAP
    assert duration.negotiability is Negotiability.NOT_NEGOTIABLE
    assert duration.confirmation_required is False
    assert "不接受短期实习" in duration.note


def test_explicit_flexibility_makes_the_gap_negotiable():
    evaluation = evaluate_conditions(
        _requirements(duration=3, explicit_flexibilities=["实习时长可协商"]),
        _profile(availability=SeasonAvailability(summer_months=2)),
        seasons=SUMMER,
    )

    duration = evaluation.by_name(ConditionName.DURATION_MONTHS)

    assert duration.status is ConditionStatus.GAP
    assert duration.negotiability is Negotiability.NEGOTIABLE
    assert duration.confirmation_required is False


def test_duration_never_makes_a_candidate_ineligible():
    evaluation = evaluate_conditions(
        _requirements(duration=6, degree="本科"),
        _profile(availability=SeasonAvailability(summer_months=2)),
        seasons=SUMMER,
    )

    assert evaluation.by_name(ConditionName.DURATION_MONTHS).status is ConditionStatus.GAP
    assert evaluation.mandatory_failures == []
    assert evaluation.qualification is not Qualification.NOT_QUALIFIED
    assert evaluation.retained is True


def test_weekly_attendance_shortfall_is_a_gap_with_confirmation():
    evaluation = evaluate_conditions(_requirements(days=5), _profile(max_days_per_week=3))

    days = evaluation.by_name(ConditionName.DAYS_PER_WEEK)

    assert days.status is ConditionStatus.GAP
    assert days.negotiability is Negotiability.UNKNOWN
    assert days.confirmation_required is True


def test_duration_met():
    evaluation = evaluate_conditions(
        _requirements(duration=2),
        _profile(availability=SeasonAvailability(summer_months=2)),
        seasons=SUMMER,
    )

    assert evaluation.by_name(ConditionName.DURATION_MONTHS).status is ConditionStatus.MET


# --- eligibility gates -----------------------------------------------------


def test_mandatory_degree_gate_disqualifies_but_keeps_the_row():
    requirements = _requirements(
        degree="硕士",
        degree_strength=RequirementStrength.MANDATORY,
    )

    evaluation = evaluate_conditions(requirements, _profile(degree="本科"))

    degree = evaluation.by_name(ConditionName.DEGREE)

    assert degree.status is ConditionStatus.GAP
    assert degree.mandatory_failure is True
    assert evaluation.qualification is Qualification.NOT_QUALIFIED
    assert degree in evaluation.mandatory_failures
    # 资格判断与保留策略分开：不符合资格不等于删除岗位。
    assert evaluation.retained is True


def test_preferred_degree_never_disqualifies():
    requirements = _requirements(
        degree="硕士",
        degree_strength=RequirementStrength.PREFERRED,
    )

    evaluation = evaluate_conditions(requirements, _profile(degree="本科"))

    degree = evaluation.by_name(ConditionName.DEGREE)

    assert degree.status is ConditionStatus.GAP
    assert degree.mandatory_failure is False
    assert evaluation.qualification is not Qualification.NOT_QUALIFIED
    assert evaluation.retained is True


def test_descriptive_degree_never_disqualifies():
    requirements = _requirements(
        degree="硕士",
        degree_strength=RequirementStrength.DESCRIPTIVE,
    )

    evaluation = evaluate_conditions(requirements, _profile(degree="本科"))

    assert evaluation.qualification is not Qualification.NOT_QUALIFIED


def test_degree_above_threshold_is_qualified():
    requirements = _requirements(
        degree="本科",
        degree_strength=RequirementStrength.MANDATORY,
    )

    evaluation = evaluate_conditions(requirements, _profile(degree="硕士"))

    assert evaluation.by_name(ConditionName.DEGREE).status is ConditionStatus.MET
    assert evaluation.qualification is Qualification.QUALIFIED


def test_mandatory_cohort_gate_disqualifies():
    requirements = InternshipRequirements(
        cohort_year=RequirementValue(
            value=2027,
            raw_text="仅限2027届毕业生",
            strength=RequirementStrength.MANDATORY,
        ),
        degree=RequirementValue(
            value="本科", raw_text="本科及以上", strength=RequirementStrength.MANDATORY
        ),
    )

    evaluation = evaluate_conditions(requirements, _profile(graduation="2028-06"))

    cohort = evaluation.by_name(ConditionName.COHORT_YEAR)

    assert cohort.status is ConditionStatus.GAP
    assert cohort.mandatory_failure is True
    assert evaluation.qualification is Qualification.NOT_QUALIFIED
    assert evaluation.retained is True


def test_preferred_cohort_is_not_a_gate():
    requirements = InternshipRequirements(
        cohort_year=RequirementValue(
            value=2027,
            raw_text="2027届优先",
            strength=RequirementStrength.PREFERRED,
        )
    )

    evaluation = evaluate_conditions(requirements, _profile(graduation="2028-06"))

    cohort = evaluation.by_name(ConditionName.COHORT_YEAR)

    assert cohort.status is ConditionStatus.GAP
    assert cohort.mandatory_failure is False
    assert evaluation.qualification is Qualification.UNDETERMINED


def test_no_stated_gate_is_undetermined_not_qualified():
    requirements = InternshipRequirements(
        cities=RequirementValue(value=["西安"], raw_text="工作地点：西安")
    )

    evaluation = evaluate_conditions(requirements, _profile())

    assert evaluation.qualification is Qualification.UNDETERMINED
    assert evaluation.notes


# --- city / unknown --------------------------------------------------------


def test_city_mismatch_is_a_gap_without_confirmation():
    evaluation = evaluate_conditions(_requirements(cities=["北京", "上海"]), _profile())

    city = evaluation.by_name(ConditionName.CITY)

    assert city.status is ConditionStatus.GAP
    # Naming 北京/上海 proves where the role is, not that the location is fixed.
    # Only explicit JD wording may claim negotiable / not_negotiable, so the
    # field stays unknown - and a gap with unknown flexibility must be confirmed
    # with the recruiter rather than assumed closed.
    assert city.negotiability is Negotiability.UNKNOWN
    assert city.confirmation_required is True
    assert evaluation.retained is True


def test_city_stated_in_jd_never_implies_non_negotiable():
    """A listed requirement is not a statement about negotiability."""

    evaluation = evaluate_conditions(_requirements(cities=["西安"]), _profile())

    city = evaluation.by_name(ConditionName.CITY)

    assert city.status is ConditionStatus.MET
    assert city.negotiability is Negotiability.UNKNOWN


def test_explicit_rejection_wording_is_the_only_path_to_not_negotiable():
    requirements = _requirements(cities=["北京"], explicit_rejections=["地点不可调整"])

    city = evaluate_conditions(requirements, _profile()).by_name(ConditionName.CITY)

    assert city.negotiability is Negotiability.NOT_NEGOTIABLE


def test_city_overlap_is_met():
    evaluation = evaluate_conditions(_requirements(cities=["西安", "成都"]), _profile())

    assert evaluation.by_name(ConditionName.CITY).status is ConditionStatus.MET


def test_missing_information_is_unknown_and_never_a_gap():
    evaluation = evaluate_conditions(InternshipRequirements(), InternshipPreferences())

    assert evaluation.gaps == []
    assert evaluation.mandatory_failures == []
    assert {item.status for item in evaluation.conditions} == {ConditionStatus.UNKNOWN}
    assert evaluation.qualification is Qualification.UNDETERMINED
    assert evaluation.retained is True


def test_unstated_requirement_does_not_penalise_a_complete_profile():
    evaluation = evaluate_conditions(_requirements(cities=["西安"]), _profile())

    duration = evaluation.by_name(ConditionName.DURATION_MONTHS)

    assert duration.status is ConditionStatus.UNKNOWN
    assert duration not in evaluation.gaps
    assert duration.confirmation_required is False


def test_extra_conditions_can_be_contributed_by_callers():
    direction = ConditionResult(
        name=ConditionName.DIRECTION,
        status=ConditionStatus.MET,
        required="python_dev",
        candidate=["python_dev", "test_dev"],
        note="标题命中 Python 开发方向",
    )

    evaluation = evaluate_conditions(
        _requirements(cities=["西安"]), _profile(), extra_conditions=[direction]
    )

    assert evaluation.by_name(ConditionName.DIRECTION) is direction


def test_preferences_come_from_configuration_not_code():
    profile = {
        "degree": "本科",
        "internship": {
            "graduation": "2028-06",
            "preferred_cities": ["西安", "郑州"],
            "available_months": {"summer": 2, "winter": 1.5},
            "max_days_per_week": 3,
            "directions": ["python_dev", "test_dev"],
        },
    }

    preferences = preferences_from_profile(profile)

    assert preferences.graduation == "2028-06"
    assert preferences.graduation_year == 2028
    assert preferences.preferred_cities == ["西安", "郑州"]
    assert preferences.availability.months_for([InternshipSeason.SUMMER]) == 2
    assert preferences.max_days_per_week == 3
    assert preferences.directions == ["python_dev", "test_dev"]


def test_default_test_profile_end_to_end_on_synthetic_posting():
    """西安暑期实习（要求3个月）对上默认测试条件：保留 + 记录差距 + 待核实。"""

    job = {
        "title": "Python开发实习生",
        "city": "西安",
        "job_type": "暑期实习",
        "jd_raw": (
            "岗位职责：参与内部工具与自动化脚本开发。\n"
            "任职要求：本科及以上，2028届，工作地点：西安，"
            "能连续实习3个月以上，每周到岗3天。"
        ),
    }
    preferences = preferences_from_profile(
        {
            "degree": "本科",
            "internship": {
                "graduation": "2028-06",
                "preferred_cities": ["西安", "郑州"],
                "available_months": {"summer": 2},
                "max_days_per_week": 3,
            },
        }
    )
    requirements = extract_requirements(job)
    evaluation = evaluate_conditions(
        requirements, preferences, seasons=[InternshipSeason.SUMMER]
    )

    assert requirements.duration_months.value == 3
    assert evaluation.by_name(ConditionName.DEGREE).status is ConditionStatus.MET
    assert evaluation.by_name(ConditionName.CITY).status is ConditionStatus.MET
    assert evaluation.by_name(ConditionName.COHORT_YEAR).status is ConditionStatus.MET
    duration = evaluation.by_name(ConditionName.DURATION_MONTHS)
    assert duration.status is ConditionStatus.GAP
    assert duration.confirmation_required is True
    assert evaluation.qualification is Qualification.QUALIFIED
    assert evaluation.retained is True
