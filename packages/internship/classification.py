"""Single implementation of "which recruitment track is this posting?".

Before this module the repository answered that question twice
(``packages.matching.rules.internship_reason`` and
``packages.recruitment_core.job_filters.internship_reason``).  The copies were
close but not equivalent and both were reachable from production, so one posting
could receive two verdicts.  This module is the one implementation.

Season and conversion are detected independently: a "可转正的暑期实习" is a
summer internship *and* a conversion opportunity, not a fourth category.  A
statement that no conversion is offered is recorded as ``NO``; silence stays
``UNKNOWN``.
"""

from __future__ import annotations

import re
from typing import Any

from .models import (
    ConversionOpportunity,
    EvidenceRef,
    InternshipSeason,
    Track,
    TrackDecision,
)
from .values import (
    compact,
    excerpt,
    field,
    is_negated,
    strip_experience_mentions,
    text,
)


# --- internship signals ----------------------------------------------------

#: ``intern``/``internship`` as a standalone token; "internal" must not match.
_INTERN_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])intern(?:ship|s)?(?![A-Za-z0-9_])", re.I)

#: Named internship projects or programmes, explicit enough to be a signal.
_INTERN_PROJECT_RE = re.compile(
    r"日常实习|暑期实习|寒假实习|春季实习|秋季实习|长期实习|"
    r"实习生(?:专项)?招聘(?:计划)?|实习招聘|实习计划|"
    r"可转正实习|转正机会|实习(?:生)?培养",
    re.I,
)

#: ``实习`` used as a standalone metadata token, e.g. a header line "招聘类型 | 实习".
_INTERN_METADATA_RE = re.compile(r"(?:^|[\s|·/、])实习(?:生)?(?:[\s|·/、]|$)")

_INTERN_DURATION_RE = re.compile(
    r"实习(?:期|时间|时长|周期)?[^。；;\n]{0,10}?"
    r"[一二三四五六七八九十两\d]+\s*个?月(?:以上)?|"
    r"每周(?:至少)?(?:到岗|实习|出勤)\s*[一二三四五六七八九十两\d]+\s*天",
    re.I,
)
_INTERN_COMMITMENT_RE = re.compile(
    r"(?:保证|保障|确保)\s*(?:持续|稳定)\s*(?:的)?\s*实习|"
    r"(?:连续|稳定)\s*实习\s*[一二三四五六七八九十两\d]+\s*个?月",
    re.I,
)
_INTERN_URL_RE = re.compile(
    r"(?:[?&#](?:type|recruitType)=|/)(?:internship|intern)(?:[/?&#]|$)", re.I
)
_INTERN_BODY_EN_RE = re.compile(
    r"(?:complete|undertake|join)\s+an?\s+internship|"
    r"internship\s+(?:position|program|programme|opportunity|duration)",
    re.I,
)

# --- season / conversion ---------------------------------------------------

_SEASON_PATTERNS: tuple[tuple[InternshipSeason, re.Pattern[str]], ...] = (
    (InternshipSeason.SUMMER, re.compile(r"暑期|暑假|summer", re.I)),
    (InternshipSeason.WINTER, re.compile(r"寒假|winter", re.I)),
    (InternshipSeason.DAILY, re.compile(r"日常实习|长期实习|学期内|日常", re.I)),
)

_CONVERSION_NO_RE = re.compile(
    r"无转正|不提供转正|不予转正|不转正|无留用|不留用|无法转正|不承诺转正", re.I
)
_CONVERSION_YES_RE = re.compile(
    r"可转正|转正机会|有转正|转正式|留用机会|表现优秀[^。；;\n]{0,8}转正", re.I
)

# --- campus / social -------------------------------------------------------

_CAMPUS_RE = re.compile(
    r"校园招聘|校招|应届(?:生|毕业生)?|(?<!\d)20\d{2}\s*届|graduate\s+programme?", re.I
)
#: Structured batch values that already mean "campus campaign".  ``Domain.
#: RecruitmentBatch`` uses formal/early; the other spellings appear in imported
#: and legacy snapshots.
_CAMPUS_BATCH_VALUES = frozenset(
    {"formal", "early", "early_batch", "campus", "campus_recruitment", "校招", "秋招", "春招"}
)
_SOCIAL_URL_RE = re.compile(r"social[-_/]?recruitment|/social|experienced", re.I)
_SOCIAL_RE = re.compile(
    r"(?:招聘类型|岗位类型|职位类型|项目类型)\s*[:：|·]\s*(?:社会招聘|社招)|"
    r"(?:^|[\r\n])\s*(?:社会招聘|社招)\s*(?:[|·]|$)|"
    r"三年及以上|五年及以上|资深|社招",
    re.I,
)


def _evidence(source: str, signal: str, excerpt_text: str, reason: str) -> EvidenceRef:
    return EvidenceRef(source=source, signal=signal, excerpt=excerpt(excerpt_text), reason=reason)


def _detect_seasons(*texts: str) -> list[InternshipSeason]:
    joined = "\n".join(part for part in texts if part)
    return [season for season, pattern in _SEASON_PATTERNS if pattern.search(joined)]


def _detect_conversion(*texts: str) -> ConversionOpportunity:
    joined = "\n".join(part for part in texts if part)
    # Negation is checked first: "无转正机会" also contains "转正机会".
    if _CONVERSION_NO_RE.search(joined):
        return ConversionOpportunity.NO
    if _CONVERSION_YES_RE.search(joined):
        return ConversionOpportunity.YES
    return ConversionOpportunity.UNKNOWN


def _internship_signals(
    title: str, job_type: str, batch: str, url: str, body: str
) -> list[tuple[float, EvidenceRef]]:
    signals: list[tuple[float, EvidenceRef]] = []

    def add(confidence: float, source: str, signal: str, raw: str, reason: str) -> None:
        signals.append((confidence, _evidence(source, signal, raw, reason)))

    if "实习" in batch or "internship" in batch.casefold():
        add(0.95, "batch", "internship_batch", batch, "招聘批次明确标注实习")
    if "实习" in title or _INTERN_TOKEN_RE.search(title):
        add(0.95, "title", "internship_title", title, "标题明确标注实习")
    if "实习" in job_type or _INTERN_TOKEN_RE.search(job_type):
        add(0.95, "job_type", "internship_job_type", job_type, "招聘类型明确标注实习")
    if url and _INTERN_URL_RE.search(url):
        add(0.95, "jd_url", "internship_url", url, "岗位 URL 明确属于实习轨道")

    match = _INTERN_PROJECT_RE.search(body)
    if match:
        add(0.9, "jd_raw", "internship_project", match.group(0), "招聘项目或正文明确标注实习")
    match = _INTERN_METADATA_RE.search(body[:260])
    if match:
        add(0.8, "jd_raw", "internship_metadata", match.group(0), "页面头部元数据明确标注实习")
    match = _INTERN_DURATION_RE.search(body)
    if match is None or is_negated(body, match.start()):
        match = None
    if match is not None:
        add(0.75, "jd_raw", "internship_commitment", match.group(0), "岗位要求当前候选人连续实习")
    else:
        commitment = _INTERN_COMMITMENT_RE.search(body)
        if commitment is not None and not is_negated(body, commitment.start()):
            add(
                0.75,
                "jd_raw",
                "internship_commitment",
                commitment.group(0),
                "岗位要求当前候选人连续实习",
            )
    match = _INTERN_BODY_EN_RE.search(body)
    if match:
        add(0.8, "jd_raw", "internship_body_en", match.group(0), "英文正文明确要求参加实习")

    signals.sort(key=lambda item: item[0], reverse=True)
    return signals


def classify_track(job: Any) -> TrackDecision:
    """Classify one posting into ``campus`` / ``internship`` / ``social`` / ``unknown``."""

    title = compact(field(job, "title", ""))
    job_type = compact(field(job, "job_type", ""))
    batch = compact(field(job, "batch", ""))
    url = compact(field(job, "jd_url", "") or field(job, "detail_url", ""))
    body = strip_experience_mentions(text(field(job, "jd_raw", "")))

    signals = _internship_signals(title, job_type, batch, url, body)
    if signals:
        return TrackDecision(
            track=Track.INTERNSHIP,
            seasons=_detect_seasons(title, job_type, batch, body),
            conversion_opportunity=_detect_conversion(title, job_type, batch, body),
            confidence=signals[0][0],
            evidence=[item for _, item in signals],
        )

    evidence: list[EvidenceRef] = []
    head = body[:500]
    if batch.casefold() in _CAMPUS_BATCH_VALUES:
        evidence.append(_evidence("batch", "campus_batch", batch, "招聘批次属于正式/提前批校招"))
    campus_match = _CAMPUS_RE.search(" ".join((title, job_type, batch, head)))
    if campus_match:
        evidence.append(
            _evidence("title/jd_raw", "campus_marker", campus_match.group(0), "明确标注校园招聘")
        )
    social_match = _SOCIAL_RE.search(" ".join((title, job_type, batch, head)))
    social_url = _SOCIAL_URL_RE.search(url) if url else None
    if social_match or social_url:
        evidence.append(
            _evidence("jd_raw", "social_marker", social_match.group(0) if social_match else url,
                      "明确标注社会招聘")
        )

    if social_match is None and social_url is None and evidence:
        return TrackDecision(track=Track.CAMPUS, confidence=0.7, evidence=evidence)
    if evidence:
        return TrackDecision(track=Track.SOCIAL, confidence=0.7, evidence=evidence)
    return TrackDecision(
        track=Track.UNKNOWN,
        confidence=0.0,
        evidence=[
            _evidence("aggregate", "no_track_evidence", title, "未发现可判定的招聘类型证据")
        ],
    )


def internship_reason(job: Any) -> str | None:
    """Compatibility helper: the first reason proving this is an internship."""

    decision = classify_track(job)
    if decision.track is not Track.INTERNSHIP:
        return None
    return decision.evidence[0].reason if decision.evidence else "明确标注实习"


def is_internship(job: Any) -> bool:
    return classify_track(job).track is Track.INTERNSHIP


def is_social(job: Any) -> bool:
    return classify_track(job).track is Track.SOCIAL


__all__ = [
    "classify_track",
    "internship_reason",
    "is_internship",
    "is_social",
]
