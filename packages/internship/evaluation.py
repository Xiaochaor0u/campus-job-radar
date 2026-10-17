"""Compare one posting's stated conditions against user availability.

Two questions are answered separately, on purpose:

* **Qualification** — does the candidate meet the eligibility gates the JD makes
  mandatory?  Only ``QUALIFICATION_GATES`` (degree, cohort year, other explicit
  gates) can produce ``NOT_QUALIFIED``, and only when the JD marks them
  ``MANDATORY``.  A duration or attendance shortfall never makes a candidate
  ineligible, because an internship schedule is routinely negotiated.
* **Retention** — does the row stay stored and visible?  Always.  The evaluator
  never sets ``retained=False``; qualification is a label, not a delete.

Comparison and negotiability are also separate fields.  A candidate who can
intern 2 months against a 3-month requirement gets
``status=gap`` (the 1-month shortfall is a fact) **and**
``negotiability=unknown`` (whether it can be waived is not a fact we have), which
yields ``confirmation_required=True``.  Collapsing those two into one
``needs_confirmation`` label would lose the shortfall.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .models import (
    QUALIFICATION_GATES,
    ConditionName,
    ConditionResult,
    ConditionStatus,
    InternshipPreferences,
    InternshipRequirements,
    InternshipSeason,
    MatchEvaluation,
    Negotiability,
    Qualification,
    RequirementStrength,
    RequirementValue,
    SeasonAvailability,
)
from .values import degree_rank, field, text


_YM_RE = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})")


def _result(
    name: ConditionName,
    status: ConditionStatus,
    *,
    strength: RequirementStrength = RequirementStrength.DESCRIPTIVE,
    negotiability: Negotiability = Negotiability.UNKNOWN,
    required: Any = None,
    candidate: Any = None,
    jd_excerpt: str = "",
    note: str = "",
) -> ConditionResult:
    if status is ConditionStatus.UNKNOWN and not jd_excerpt:
        # No JD wording means we know nothing about this condition - neither
        # whether it applies nor whether it could be waived.  Claiming
        # NOT_NEGOTIABLE here would assert a recruitment fact the posting never
        # stated, so the field stays UNKNOWN.
        negotiability = Negotiability.UNKNOWN
    return ConditionResult(
        name=name,
        status=status,
        strength=strength,
        negotiability=negotiability,
        required=required,
        candidate=candidate,
        jd_excerpt=jd_excerpt,
        note=note,
    )


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _negotiability(
    requirements: InternshipRequirements, *keywords: str
) -> tuple[Negotiability, str]:
    """Read negotiability from JD wording only; never infer it."""

    for phrase in requirements.explicit_rejections:
        if any(keyword in phrase for keyword in keywords):
            return Negotiability.NOT_NEGOTIABLE, phrase
    if requirements.explicit_flexibilities:
        return Negotiability.NEGOTIABLE, requirements.explicit_flexibilities[0]
    return Negotiability.UNKNOWN, ""


def _gap_note(base: str, negotiability: Negotiability, phrase: str) -> str:
    """Append the negotiability finding without ever changing the gap itself."""

    if negotiability is Negotiability.NOT_NEGOTIABLE:
        return f"{base}；JD 写明「{phrase}」，该差距不可协商"
    if negotiability is Negotiability.NEGOTIABLE:
        return f"{base}；JD 写明可放宽（「{phrase}」）"
    return f"{base}；JD 未说明是否可协商，需向招聘方核实"


def _evaluate_degree(
    requirement: RequirementValue, preferences: InternshipPreferences
) -> ConditionResult:
    if not requirement.known:
        return _result(
            ConditionName.DEGREE,
            ConditionStatus.UNKNOWN,
            candidate=preferences.degree,
            note="JD 未写明学历要求，无法判断",
        )
    if requirement.unlimited:
        return _result(
            ConditionName.DEGREE,
            ConditionStatus.MET,
            strength=requirement.strength,
            required="不限",
            candidate=preferences.degree,
            jd_excerpt=requirement.raw_text,
            note="JD 明确学历不限",
        )
    required_rank = degree_rank(requirement.value)
    candidate_rank = preferences.degree_rank
    if candidate_rank is None or required_rank is None:
        return _result(
            ConditionName.DEGREE,
            ConditionStatus.UNKNOWN,
            strength=requirement.strength,
            required=requirement.value,
            candidate=preferences.degree,
            jd_excerpt=requirement.raw_text,
            note="缺少学历信息，无法判断",
        )
    if candidate_rank >= required_rank:
        return _result(
            ConditionName.DEGREE,
            ConditionStatus.MET,
            strength=requirement.strength,
            required=requirement.value,
            candidate=preferences.degree,
            jd_excerpt=requirement.raw_text,
        )
    suffix = (
        "该学历为该 JD 的明确资格门槛"
        if requirement.mandatory
        else "该学历只是 JD 的普通描述或优先条件"
    )
    return _result(
        ConditionName.DEGREE,
        ConditionStatus.GAP,
        strength=requirement.strength,
        required=requirement.value,
        candidate=preferences.degree,
        jd_excerpt=requirement.raw_text,
        note=f"学历低于 JD 表述；{suffix}",
    )


def _evaluate_cohort_year(
    requirements: InternshipRequirements, preferences: InternshipPreferences
) -> ConditionResult:
    requirement = requirements.cohort_year
    if not requirement.known:
        return _result(
            ConditionName.COHORT_YEAR,
            ConditionStatus.UNKNOWN,
            candidate=preferences.graduation,
            note="JD 未写明毕业届别",
        )
    if preferences.graduation_year is None:
        return _result(
            ConditionName.COHORT_YEAR,
            ConditionStatus.UNKNOWN,
            strength=requirement.strength,
            required=requirement.value,
            jd_excerpt=requirement.raw_text,
            note="未配置毕业时间，无法判断届别",
        )
    if int(preferences.graduation_year) == int(requirement.value):
        return _result(
            ConditionName.COHORT_YEAR,
            ConditionStatus.MET,
            strength=requirement.strength,
            required=requirement.value,
            candidate=preferences.graduation,
            jd_excerpt=requirement.raw_text,
        )
    suffix = (
        "届别为该 JD 的明确资格门槛"
        if requirement.mandatory
        else "届别只是 JD 的普通描述或优先条件，不构成资格淘汰"
    )
    return _result(
        ConditionName.COHORT_YEAR,
        ConditionStatus.GAP,
        strength=requirement.strength,
        required=requirement.value,
        candidate=preferences.graduation,
        jd_excerpt=requirement.raw_text,
        note=f"毕业届别与 JD 表述不一致；{suffix}",
    )


def _evaluate_city(
    requirement: RequirementValue,
    preferences: InternshipPreferences,
    requirements: InternshipRequirements,
) -> ConditionResult:
    if not requirement.known:
        return _result(
            ConditionName.CITY, ConditionStatus.UNKNOWN, note="岗位未给出工作城市"
        )
    required = [str(item) for item in (requirement.value or [])]
    wanted = [str(item).removesuffix("市") for item in preferences.preferred_cities]
    if not required:
        return _result(
            ConditionName.CITY,
            ConditionStatus.UNKNOWN,
            strength=requirement.strength,
            jd_excerpt=requirement.raw_text,
            note="城市字段为空",
        )
    if not wanted:
        return _result(
            ConditionName.CITY,
            ConditionStatus.UNKNOWN,
            strength=requirement.strength,
            required=required,
            jd_excerpt=requirement.raw_text,
            note="未配置意向城市，无法判断",
        )
    if any(city in wanted for city in required):
        return _result(
            ConditionName.CITY,
            ConditionStatus.MET,
            strength=requirement.strength,
            required=required,
            candidate=wanted,
            jd_excerpt=requirement.raw_text,
        )
    # Whether the location could be adjusted is a separate question that only
    # explicit JD wording can answer; naming a city proves nothing either way.
    negotiability, phrase = _negotiability(requirements, "地点", "城市", "工作地")
    note = "城市不在意向范围内（属于偏好差距，不是资格淘汰）"
    if negotiability is Negotiability.NOT_NEGOTIABLE:
        note += f"；JD 明确不接受调整（{phrase}）"
    elif negotiability is Negotiability.NEGOTIABLE:
        note += f"；JD 提到可协商（{phrase}）"
    else:
        note += "；JD 未说明是否可调整，需向招聘方核实"
    return _result(
        ConditionName.CITY,
        ConditionStatus.GAP,
        strength=requirement.strength,
        negotiability=negotiability,
        required=required,
        candidate=wanted,
        jd_excerpt=requirement.raw_text,
        note=note,
    )


def _evaluate_duration(
    requirements: InternshipRequirements,
    preferences: InternshipPreferences,
    seasons: list[InternshipSeason] | None,
) -> ConditionResult:
    requirement = requirements.duration_months
    available = preferences.availability.months_for(seasons)
    if not requirement.known:
        return _result(
            ConditionName.DURATION_MONTHS,
            ConditionStatus.UNKNOWN,
            candidate=available,
            note="JD 未写明最短实习时长",
        )
    if available is None:
        return _result(
            ConditionName.DURATION_MONTHS,
            ConditionStatus.UNKNOWN,
            strength=requirement.strength,
            required=requirement.value,
            jd_excerpt=requirement.raw_text,
            note="未配置可实习时长，无法判断",
        )
    if float(available) >= float(requirement.value):
        return _result(
            ConditionName.DURATION_MONTHS,
            ConditionStatus.MET,
            strength=requirement.strength,
            required=requirement.value,
            candidate=available,
            jd_excerpt=requirement.raw_text,
        )
    negotiability, phrase = _negotiability(requirements, "短期", "个月", "实习")
    shortfall = float(requirement.value) - float(available)
    base = (
        f"可实习 {_fmt(available)} 个月，少于要求的 {_fmt(requirement.value)} 个月，"
        f"明确存在 {_fmt(shortfall)} 个月差距"
    )
    note = _gap_note(base, negotiability, phrase)
    return _result(
        ConditionName.DURATION_MONTHS,
        ConditionStatus.GAP,
        strength=requirement.strength,
        negotiability=negotiability,
        required=requirement.value,
        candidate=available,
        jd_excerpt=requirement.raw_text,
        note=note,
    )


def _evaluate_days_per_week(
    requirements: InternshipRequirements, preferences: InternshipPreferences
) -> ConditionResult:
    requirement = requirements.days_per_week
    available = preferences.max_days_per_week
    if not requirement.known:
        return _result(
            ConditionName.DAYS_PER_WEEK,
            ConditionStatus.UNKNOWN,
            candidate=available,
            note="JD 未写明每周到岗天数",
        )
    if available is None:
        return _result(
            ConditionName.DAYS_PER_WEEK,
            ConditionStatus.UNKNOWN,
            strength=requirement.strength,
            required=requirement.value,
            jd_excerpt=requirement.raw_text,
            note="未配置每周可到岗天数，无法判断",
        )
    if int(available) >= int(requirement.value):
        return _result(
            ConditionName.DAYS_PER_WEEK,
            ConditionStatus.MET,
            strength=requirement.strength,
            required=requirement.value,
            candidate=available,
            jd_excerpt=requirement.raw_text,
        )
    negotiability, phrase = _negotiability(requirements, "每周", "到岗", "出勤", "实习")
    base = (
        f"每周可到岗 {_fmt(available)} 天，少于要求的 {_fmt(requirement.value)} 天，"
        f"明确存在 {_fmt(int(requirement.value) - int(available))} 天差距"
    )
    note = _gap_note(base, negotiability, phrase)
    return _result(
        ConditionName.DAYS_PER_WEEK,
        ConditionStatus.GAP,
        strength=requirement.strength,
        negotiability=negotiability,
        required=requirement.value,
        candidate=available,
        jd_excerpt=requirement.raw_text,
        note=note,
    )


def _parse_year_month(value: Any) -> tuple[int, int] | None:
    """Parse only unambiguous year-month values; "6月" alone stays ambiguous."""

    match = _YM_RE.search(text(value))
    if match is None:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return int(match.group(1)), month


def _evaluate_start_date(
    requirements: InternshipRequirements, preferences: InternshipPreferences
) -> ConditionResult:
    requirement = requirements.start_date
    if not requirement.known:
        return _result(
            ConditionName.START_DATE,
            ConditionStatus.UNKNOWN,
            candidate=preferences.earliest_start,
            note="JD 未写明明确定岗时间",
        )
    required = _parse_year_month(requirement.value)
    available = _parse_year_month(preferences.earliest_start)
    if required is None or available is None:
        return _result(
            ConditionName.START_DATE,
            ConditionStatus.UNKNOWN,
            strength=requirement.strength,
            required=requirement.value,
            candidate=preferences.earliest_start,
            jd_excerpt=requirement.raw_text,
            note="到岗时间表述不明确，无法比较",
        )
    if available <= required:
        return _result(
            ConditionName.START_DATE,
            ConditionStatus.MET,
            strength=requirement.strength,
            required=requirement.value,
            candidate=preferences.earliest_start,
            jd_excerpt=requirement.raw_text,
        )
    negotiability, phrase = _negotiability(requirements, "到岗", "入职", "时间")
    base = "最早可到岗时间晚于 JD 期望，存在时间差距"
    note = _gap_note(base, negotiability, phrase)
    return _result(
        ConditionName.START_DATE,
        ConditionStatus.GAP,
        strength=requirement.strength,
        negotiability=negotiability,
        required=requirement.value,
        candidate=preferences.earliest_start,
        jd_excerpt=requirement.raw_text,
        note=note,
    )


def _qualification(
    conditions: Sequence[ConditionResult],
) -> tuple[Qualification, list[str]]:
    """Decide eligibility from *mandatory* gates only."""

    gates = [item for item in conditions if item.name in QUALIFICATION_GATES]
    mandatory_gates = [item for item in gates if item.strength is RequirementStrength.MANDATORY]
    if any(item.status is ConditionStatus.GAP for item in mandatory_gates):
        return Qualification.NOT_QUALIFIED, ["存在未满足的明确资格门槛"]
    if mandatory_gates and all(
        item.status is ConditionStatus.MET for item in mandatory_gates
    ):
        return Qualification.QUALIFIED, []
    return (
        Qualification.UNDETERMINED,
        ["JD 未给出明确的资格门槛，或门槛信息不足，无法判定是否符合资格"],
    )


def evaluate_conditions(
    requirements: InternshipRequirements,
    preferences: InternshipPreferences,
    *,
    seasons: Sequence[InternshipSeason] | None = None,
    extra_conditions: Sequence[ConditionResult] = (),
) -> MatchEvaluation:
    """Evaluate every stated condition and aggregate the result.

    ``extra_conditions`` lets callers contribute conditions computed elsewhere
    (for example technology direction from ``packages.matching``) so there is one
    evaluation per posting rather than two competing ones.
    """

    season_list = list(seasons or [])
    conditions: list[ConditionResult] = [
        _evaluate_degree(requirements.degree, preferences),
        _evaluate_cohort_year(requirements, preferences),
        _evaluate_city(requirements.cities, preferences, requirements),
        _evaluate_duration(requirements, preferences, season_list),
        _evaluate_days_per_week(requirements, preferences),
        _evaluate_start_date(requirements, preferences),
    ]
    conditions.extend(extra_conditions)
    qualification, notes = _qualification(conditions)
    return MatchEvaluation(
        qualification=qualification,
        conditions=conditions,
        mandatory_failures=[item for item in conditions if item.mandatory_failure],
        gaps=[item for item in conditions if item.status is ConditionStatus.GAP],
        unknowns=[item for item in conditions if item.status is ConditionStatus.UNKNOWN],
        confirmation_required=[item for item in conditions if item.confirmation_required],
        notes=notes,
    )


def _months_from(value: Any) -> SeasonAvailability:
    if isinstance(value, SeasonAvailability):
        return value
    if isinstance(value, Mapping):
        return SeasonAvailability(
            summer_months=_number(value.get("summer") or value.get("summer_months")),
            winter_months=_number(value.get("winter") or value.get("winter_months")),
            daily_months=_number(value.get("daily") or value.get("daily_months")),
        )
    return SeasonAvailability()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested(value: Any, *names: str) -> Any:
    """Look a field up on the profile and on its ``internship`` / ``matching`` blocks."""

    for name in names:
        direct = field(value, name, None)
        if direct is not None:
            return direct
    for block_name in ("internship", "matching"):
        block = field(value, block_name, None)
        if block is None:
            continue
        for name in names:
            found = field(block, name, None)
            if found is not None:
                return found
    return None


def preferences_from_profile(profile: Any) -> InternshipPreferences:
    """Build preferences from user configuration; never hard-code one person."""

    cities = _nested(profile, "preferred_cities", "cities", "target_cities") or []
    if isinstance(cities, str):
        cities = [item for item in re.split(r"[、,，/\s]+", cities) if item]
    return InternshipPreferences(
        graduation=text(_nested(profile, "graduation", "graduation_date", "graduation_time"))
        or None,
        degree=text(_nested(profile, "degree")) or None,
        preferred_cities=[str(item) for item in cities],
        availability=_months_from(_nested(profile, "availability", "available_months")),
        max_days_per_week=_int_or_none(_nested(profile, "max_days_per_week", "days_per_week")),
        earliest_start=text(_nested(profile, "earliest_start", "available_from")) or None,
        directions=[str(item) for item in (_nested(profile, "directions") or [])],
        secondary_directions=[
            str(item) for item in (_nested(profile, "secondary_directions") or [])
        ],
    )


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


__all__ = ["evaluate_conditions", "preferences_from_profile"]
