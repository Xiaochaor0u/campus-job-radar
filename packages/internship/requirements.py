"""Extract *structural* internship conditions from a posting.

Deterministic and traceable: every value carries the JD fragment that produced
it, and a ``RequirementStrength`` read from that fragment's wording.  Strength is
what decides eligibility later — never the field name, so a posting that merely
mentions a graduation year does not disqualify anybody.

Two dimensions are deliberately not handled here, because they already have
owners: technology direction lives in ``packages.matching``, and required skills
live in ``DeepSeekMatchPayload.score_breakdown.required_skills``.
"""

from __future__ import annotations

import re
from typing import Any

from .models import (
    EvidenceRef,
    InternshipRequirements,
    RequirementStrength,
    RequirementValue,
    UNLIMITED,
)
from .values import (
    DEGREE_RANK,
    excerpt,
    field,
    is_negated,
    strip_experience_mentions,
    text,
)


_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_COUNT_CLASS = "一二三四五六七八九十两零\\d"


def _parse_count(raw: str) -> float | int | None:
    """Parse "3", "三", "十", "十二", "两" into a number, else ``None``."""

    label = text(raw)
    if not label:
        return None
    if label == "半":
        return 0.5
    if label.isdigit():
        return _tidy(float(int(label)))
    label = label.removesuffix("个").removesuffix("天").removesuffix("月")
    if not label:
        return None
    if label.isdigit():
        return _tidy(float(int(label)))
    if "十" in label:
        left, _, right = label.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return _tidy(float(tens * 10 + ones))
    if label in _CN_DIGITS:
        return _tidy(float(_CN_DIGITS[label]))
    return None


def _tidy(value: float) -> float | int:
    "Return an ``int`` when integral, so stored JSON stays readable."
    return int(value) if float(value).is_integer() else value


# --- how binding is this condition? ---------------------------------------

_PREFERRED_RE = re.compile(r"优先|更佳|加分|preferred", re.I)
_MANDATORY_RE = re.compile(
    r"仅限|必须|必需|须|要求|及以上|以上|不少于|不低于|至少|minimum|required|only", re.I
)


def _strength(context: str) -> RequirementStrength:
    """Read the binding strength from the wording around one requirement.

    Deliberately conservative: without an explicit requirement marker the
    condition is ``DESCRIPTIVE``, so a bare mention can never disqualify someone.
    """

    if _PREFERRED_RE.search(context):
        return RequirementStrength.PREFERRED
    if _MANDATORY_RE.search(context):
        return RequirementStrength.MANDATORY
    return RequirementStrength.DESCRIPTIVE


# --- patterns --------------------------------------------------------------

_DURATION_RANGE_RE = re.compile(
    rf"实习(?:期|时间|时长|周期)?[^。；;，,\n]{{0,10}}?"
    rf"([{_COUNT_CLASS}]+)\s*[-~－—至到]\s*([{_COUNT_CLASS}]+)\s*个?月"
)
_DURATION_RE = re.compile(
    rf"实习(?:期|时间|时长|周期)?[^。；;，,\n]{{0,10}}?"
    rf"([{_COUNT_CLASS}]+)\s*个?月"
)
_DAYS_PER_WEEK_RE = re.compile(
    rf"每周(?:至少|最少|不少于)?\s*(?:到岗|实习|出勤|工作)?\s*([{_COUNT_CLASS}]+)\s*天"
)
_DAYS_PER_WEEK_ALT_RE = re.compile(
    rf"(?:到岗|出勤)(?:天数)?\s*[:：]?\s*(?:每周)?\s*([{_COUNT_CLASS}]+)\s*天"
)

_DEGREE_TOKEN_RE = re.compile(r"(博士|硕士|研究生|本科|大专|专科)")
_DEGREE_UNLIMITED_RE = re.compile(r"学历不限|不限学历|学历要求\s*[:：]?\s*不限")

_COHORT_RE = re.compile(r"(?<!\d)(20\d{2})\s*届|(?<!\d)(20\d{2})\s*年(?:毕业|应届)")

_START_DATE_KEYWORD_FIRST_RE = re.compile(
    r"(?:入职|到岗|开始实习|实习开始)(?:时间|日期)?\s*[:：]?\s*"
    r"(尽快|立即|随时|\d{4}\s*[-/.年]\s*\d{1,2}\s*月?|\d{1,2}\s*月)"
)
_START_DATE_VALUE_FIRST_RE = re.compile(r"(尽快|立即|随时)(?:时间)?(?:到岗|入职|开始实习|开始)")

_CITY_FIELD_NAMES = ("city", "work_city", "job_city", "location")
_CITY_TEXT_RE = re.compile(
    r"(?:工作地点|工作城市|工作地|实习地点|base地)\s*[:：|·]\s*([^\n。；;，,|]{2,40})"
)
_CITY_SPLIT_RE = re.compile(r"[、/，,|·；;和及\s]+")

#: Phrases that refuse a candidate class outright.
_REJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"不接受短期实习|短期实习(?:不予考虑|不考虑|勿投|请勿|勿扰)"),
    re.compile(
        rf"(?:低于|少于|不足)\s*[{_COUNT_CLASS}]+\s*个?月[^。；;\n]{{0,12}}"
        r"(?:不予考虑|不考虑|勿投|无法受理|恕不|不予录用)"
    ),
    re.compile(
        r"(?:无法|不能)(?:保证|满足)\s*(?:连续|稳定)?\s*实习[^。；;\n]{0,12}"
        r"(?:不予考虑|不考虑|勿投|无法受理)"
    ),
    re.compile(r"仅限\s*能\s*(?:保证|满足)\s*连续实习"),
)

#: Phrases that explicitly say a condition can be relaxed.  Recorded so the
#: evaluator can mark a gap as negotiable instead of guessing.
_FLEXIBILITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"可协商|可放宽|可视情况|可沟通|时间可议|可调整|弹性"),
)


def _evidence(source: str, signal: str, raw: str, reason: str) -> EvidenceRef:
    return EvidenceRef(source=source, signal=signal, excerpt=excerpt(raw), reason=reason)


def _value(
    value: Any, raw: str, *, source: str = "jd_raw", confidence: float = 0.9,
    strength: RequirementStrength = RequirementStrength.DESCRIPTIVE,
) -> RequirementValue:
    return RequirementValue(
        value=value, strength=strength, raw_text=excerpt(raw), source=source,
        confidence=confidence,
    )


def _window(body: str, start: int, end: int, before: int = 10, after: int = 10) -> str:
    return body[max(0, start - before) : end + after]


def _extract_degree(body: str, evidence: list[EvidenceRef]) -> RequirementValue:
    unlimited = _DEGREE_UNLIMITED_RE.search(body)
    if unlimited:
        evidence.append(
            _evidence("jd_raw", "degree_unlimited", unlimited.group(0), "JD 明确学历不限")
        )
        return _value(UNLIMITED, unlimited.group(0), confidence=0.95)

    # A preferred mention is kept (so the information is not lost) but never
    # allowed to override a real requirement, and never treated as a threshold.
    binding: tuple[int, RequirementValue] | None = None
    preferred: tuple[int, RequirementValue] | None = None
    for match in _DEGREE_TOKEN_RE.finditer(body):
        token = match.group(1)
        context = _window(body, match.start(), match.end(), 8, 6)
        # Preference must be judged tightly: in "本科及以上，硕士优先" the 优先
        # belongs to 硕士, not to 本科.
        preferred_only = bool(_PREFERRED_RE.search(body[match.end() : match.end() + 4]))
        if preferred_only:
            strength = RequirementStrength.PREFERRED
        elif _MANDATORY_RE.search(context):
            strength = RequirementStrength.MANDATORY
        else:
            strength = RequirementStrength.DESCRIPTIVE
        rank = DEGREE_RANK.get(token, 0)
        value = _value(token, body[match.start() : match.end() + 4], strength=strength)
        slot = preferred if preferred_only else binding
        if slot is None or rank > slot[0]:
            if preferred_only:
                preferred = (rank, value)
            else:
                binding = (rank, value)
            evidence.append(
                _evidence(
                    "jd_raw",
                    "degree_preferred" if preferred_only else "degree_requirement",
                    context,
                    f"JD 提到{token}学历（{strength.value}）",
                )
            )
    chosen = binding or preferred
    return chosen[1] if chosen else RequirementValue()


def _extract_cohort(body: str, evidence: list[EvidenceRef]) -> RequirementValue:
    match = _COHORT_RE.search(body)
    if match is None:
        return RequirementValue()
    year = int(match.group(1) or match.group(2))
    context = _window(body, match.start(), match.end(), 8, 8)
    strength = _strength(context)
    evidence.append(
        _evidence(
            "jd_raw",
            "cohort_year",
            context,
            f"JD 提到 {year} 届（{strength.value}）",
        )
    )
    return _value(year, context, confidence=0.9, strength=strength)


def _extract_duration(body: str, evidence: list[EvidenceRef]) -> RequirementValue:
    ranged = _DURATION_RANGE_RE.search(body)
    if ranged and not is_negated(body, ranged.start()):
        low = _parse_count(ranged.group(1))
        high = _parse_count(ranged.group(2))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            required = min(low, high)
            context = _window(body, ranged.start(), ranged.end())
            evidence.append(
                _evidence(
                    "jd_raw", "duration_range", ranged.group(0),
                    f"JD 要求实习 {ranged.group(1)}-{ranged.group(2)} 个月，取下限 {required} 个月",
                )
            )
            return _value(required, ranged.group(0), strength=_strength(context))
    match = _DURATION_RE.search(body)
    if match is None or is_negated(body, match.start()):
        return RequirementValue()
    months = _parse_count(match.group(1))
    if months is None:
        return RequirementValue()
    context = _window(body, match.start(), match.end())
    evidence.append(
        _evidence("jd_raw", "duration_months", match.group(0), f"JD 要求实习不少于 {months} 个月")
    )
    return _value(months, match.group(0), strength=_strength(context))


def _extract_days(body: str, evidence: list[EvidenceRef]) -> RequirementValue:
    match = _DAYS_PER_WEEK_RE.search(body) or _DAYS_PER_WEEK_ALT_RE.search(body)
    if match is None or is_negated(body, match.start()):
        return RequirementValue()
    days = _parse_count(match.group(1))
    if not isinstance(days, (int, float)) or days == 0:
        return RequirementValue()
    days = int(days)
    context = _window(body, match.start(), match.end())
    evidence.append(
        _evidence("jd_raw", "days_per_week", match.group(0), f"JD 要求每周到岗 {days} 天")
    )
    return _value(days, match.group(0), strength=_strength(context))


def _normalize_cities(raw: str) -> list[str]:
    cities: list[str] = []
    for part in _CITY_SPLIT_RE.split(raw):
        item = part.strip().removesuffix("市")
        if not item or item in {"不限", "全国", "多地"}:
            continue
        if item not in cities:
            cities.append(item)
    return cities


def _extract_cities(job: Any, evidence: list[EvidenceRef]) -> RequirementValue:
    for name in _CITY_FIELD_NAMES:
        raw = text(field(job, name, ""))
        if not raw:
            continue
        cities = _normalize_cities(raw)
        if cities:
            evidence.append(_evidence("struct", "city_field", raw, "岗位结构字段给出工作城市"))
            return _value(cities, raw, source="struct", confidence=0.9,
                          strength=RequirementStrength.DESCRIPTIVE)
    match = _CITY_TEXT_RE.search(text(field(job, "jd_raw", "")))
    if match is None:
        return RequirementValue()
    cities = _normalize_cities(match.group(1))
    if not cities:
        return RequirementValue()
    evidence.append(_evidence("jd_raw", "city_text", match.group(0), "JD 正文给出工作城市"))
    return _value(cities, match.group(0), confidence=0.75,
                  strength=RequirementStrength.DESCRIPTIVE)


def _extract_start_date(body: str, evidence: list[EvidenceRef]) -> RequirementValue:
    """Capture "到岗时间：尽快" and "能尽快到岗" alike."""

    match = _START_DATE_KEYWORD_FIRST_RE.search(body)
    value = match.group(1) if match else None
    if match is None:
        prefix = _START_DATE_VALUE_FIRST_RE.search(body)
        if prefix is not None:
            match, value = prefix, prefix.group(1)
    if match is None or value is None:
        return RequirementValue()
    context = _window(body, match.start(), match.end())
    evidence.append(_evidence("jd_raw", "start_date", match.group(0), "JD 给出到岗时间"))
    return _value(value, match.group(0), confidence=0.7, strength=_strength(context))


def _collect(body: str, patterns: tuple[re.Pattern[str], ...], signal: str,
             reason: str, evidence: list[EvidenceRef]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(body):
            phrase = match.group(0).strip()
            if phrase and phrase not in found:
                found.append(phrase)
                evidence.append(_evidence("jd_raw", signal, phrase, reason))
    return found


def extract_requirements(job: Any) -> InternshipRequirements:
    """Extract every structural condition the posting actually states."""

    body = strip_experience_mentions(text(field(job, "jd_raw", "")))
    evidence: list[EvidenceRef] = []
    return InternshipRequirements(
        degree=_extract_degree(body, evidence),
        cohort_year=_extract_cohort(body, evidence),
        cities=_extract_cities(job, evidence),
        duration_months=_extract_duration(body, evidence),
        days_per_week=_extract_days(body, evidence),
        start_date=_extract_start_date(body, evidence),
        explicit_rejections=_collect(
            body, _REJECTION_PATTERNS, "explicit_rejection",
            "JD 显式拒绝某类候选人", evidence,
        ),
        explicit_flexibilities=_collect(
            body, _FLEXIBILITY_PATTERNS, "explicit_flexibility",
            "JD 显式说明该项可放宽", evidence,
        ),
        evidence=evidence,
    )


__all__ = ["extract_requirements"]
