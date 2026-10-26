"""Recommend postings against the candidate profile without calling a model.

Why a rule layer exists: the LLM scorer in ``packages.matching`` needs a full
JD and an API key.  Most rows in this catalog are announcements (title-level),
and many users never configure a model.  A deterministic scorer over the
profile's own keywords still answers the practical question — "which of these
20k postings should I actually look at?" — and it explains every point it
awards, which a black-box score cannot.

Scoring (max 100):
    direction match   35   a stated target direction appears in the posting
    keyword match     30   profile title keywords appear in title/summary
    skill match       20   profile skills appear in title/summary
    track alignment   15   posting track matches the profile's job_type

Everything is a lowercase substring test so the whole catalog can be scored in
one pass; no crawling, no model, no network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from packages.storage import CompanySnapshot, JobSnapshot, Storage

MAX_SCORE = 100
_DIRECTION_WEIGHT = 35
_KEYWORD_WEIGHT = 30
_SKILL_WEIGHT = 20
_TRACK_WEIGHT = 15
_MIN_TOKEN_LENGTH = 2

#: Words too generic to prove a direction match on their own.
_STOPWORDS = frozenset({
    "and", "the", "with", "for", "of", "to", "in", "on", "a", "an",
    "开发", "工程师", "岗位", "方向", "相关", "经验", "优先", "熟悉", "掌握",
})

#: Chinese connectors that join two skills/directions into one label.
_CONNECTOR_RE = re.compile(r"[与和及]")


@dataclass(frozen=True, slots=True)
class RecommendationItem:
    job_id: str
    company_id: str
    company_name: str
    title: str
    city: str | None
    track: str
    batch: str
    detail_url: str
    score: int
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "title": self.title,
            "city": self.city,
            "track": self.track,
            "batch": self.batch,
            "detail_url": self.detail_url,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    profile_ready: bool
    profile_keywords: Mapping[str, int] = field(default_factory=dict)
    total_scanned: int = 0
    matched: int = 0
    items: tuple[RecommendationItem, ...] = ()
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_ready": self.profile_ready,
            "profile_keywords": dict(self.profile_keywords),
            "total_scanned": self.total_scanned,
            "matched": self.matched,
            "hint": self.hint,
            "items": [item.to_dict() for item in self.items],
        }


def _tokenize(values: Iterable[str]) -> list[str]:
    """Split phrases on separators so "机器人 / C++" matches either part.

    Chinese direction labels are often joined with 与/和/及 ("大模型与智能体"),
    which never appears verbatim in a job title — splitting on the connector
    turns it into the two tokens that do appear.
    """

    tokens: list[str] = []
    for value in values:
        raw = str(value or "").strip().casefold()
        if not raw:
            continue
        for part in re.split(r"[/、,;，；|·\s]+", raw):
            part = part.strip()
            if not part:
                continue
            pieces = [part] if part.isascii() else _CONNECTOR_RE.split(part)
            for piece in pieces:
                piece = piece.strip()
                if len(piece) < _MIN_TOKEN_LENGTH or piece in _STOPWORDS:
                    continue
                if piece not in tokens:
                    tokens.append(piece)
    return tokens


def profile_signals(profile: Mapping[str, Any] | Any) -> dict[str, list[str]]:
    """Extract the searchable signals from a CandidateProfile-like object."""

    def get(name: str, default: Any = None) -> Any:
        if isinstance(profile, Mapping):
            return profile.get(name, default)
        return getattr(profile, name, default)

    matching = get("matching")
    if matching is None:
        matching = {}

    def mget(name: str, default: Any = None) -> Any:
        if isinstance(matching, Mapping):
            return matching.get(name, default)
        return getattr(matching, name, default)

    directions = list(mget("primary_directions") or []) + list(mget("secondary_directions") or [])
    single = get("direction")
    if single:
        directions.append(str(single))
    keywords = list(mget("title_keywords") or [])
    skills = list(get("skills") or []) + list(mget("supporting_skills") or [])
    return {
        "directions": _tokenize(directions),
        "keywords": _tokenize(keywords),
        "skills": _tokenize(skills),
    }


def score_posting(
    text: str,
    signals: Mapping[str, Sequence[str]],
    *,
    track: str = "",
    job_type: str = "",
) -> tuple[int, tuple[str, ...]]:
    """Score one posting's searchable text; returns (score, reasons)."""

    haystack = text.casefold()
    total = 0
    reasons: list[str] = []

    direction_hits = [t for t in signals.get("directions", ()) if t in haystack]
    if direction_hits:
        share = min(1.0, len(direction_hits) / max(1, len(signals["directions"])))
        points = round(_DIRECTION_WEIGHT * max(0.4, share))
        total += points
        reasons.append(f"方向命中 {'/'.join(direction_hits[:3])} +{points}")

    keyword_hits = [t for t in signals.get("keywords", ()) if t in haystack]
    if keyword_hits:
        share = min(1.0, len(keyword_hits) / max(1, len(signals["keywords"])))
        points = round(_KEYWORD_WEIGHT * max(0.4, share))
        total += points
        reasons.append(f"关键词命中 {'/'.join(keyword_hits[:3])} +{points}")

    skill_hits = [t for t in signals.get("skills", ()) if t in haystack]
    if skill_hits:
        share = min(1.0, len(skill_hits) / max(1, len(signals["skills"])))
        points = round(_SKILL_WEIGHT * max(0.3, share))
        total += points
        reasons.append(f"技能命中 {'/'.join(skill_hits[:3])} +{points}")

    normalized_type = str(job_type or "").strip().casefold()
    # Track alignment only refines a posting that already matched something;
    # awarding it on its own would give every row a floor of 15 points.
    if normalized_type and track and total > 0:
        wants_internship = "intern" in normalized_type or "实习" in normalized_type
        if (wants_internship and track == "internship") or (
            not wants_internship and track == "campus"
        ):
            total += _TRACK_WEIGHT
            reasons.append(f"招聘类型匹配 +{_TRACK_WEIGHT}")

    return min(MAX_SCORE, total), tuple(reasons)


def recommend_jobs(
    storage: Storage,
    profile: Mapping[str, Any] | Any,
    *,
    limit: int = 50,
    min_score: int = 1,
    track: str | None = None,
    company_id: str | None = None,
    source: str | None = None,
) -> RecommendationResult:
    """Score every active posting against the profile and return the best."""

    if not isinstance(storage, Storage):
        raise TypeError("storage must be a Storage instance")
    if limit < 1:
        raise ValueError("limit must be positive")
    storage.initialize()

    signals = profile_signals(profile)
    keyword_counts = {key: len(value) for key, value in signals.items()}
    if not any(signals.values()):
        return RecommendationResult(
            profile_ready=False,
            profile_keywords=keyword_counts,
            hint="还没有可用的求职偏好。先到「配置」上传简历（或手动填写岗位关键词与目标方向），再回来查看推荐。",
        )

    job_type = ""
    if isinstance(profile, Mapping):
        job_type = str(profile.get("job_type") or "")
    else:
        job_type = str(getattr(profile, "job_type", "") or "")

    conditions = [JobSnapshot.availability_status == "active"]
    if track:
        conditions.append(JobSnapshot.track == track)
    if company_id:
        conditions.append(JobSnapshot.company_id == company_id)
    if source:
        conditions.append(JobSnapshot.source == source)

    with storage.session() as session:
        rows = session.execute(
            select(
                JobSnapshot.id,
                JobSnapshot.company_id,
                JobSnapshot.title,
                JobSnapshot.city,
                JobSnapshot.track,
                JobSnapshot.batch,
                JobSnapshot.detail_url,
                JobSnapshot.capture_evidence,
            ).where(*conditions)
        ).all()
        company_names = {row.id: row.name for row in session.query(CompanySnapshot).all()}

    scored: list[RecommendationItem] = []
    for row in rows:
        evidence = row.capture_evidence or {}
        text = " ".join(
            str(part or "")
            for part in (
                row.title,
                evidence.get("recruit_type"),
                evidence.get("industry"),
            )
        )
        score, reasons = score_posting(
            text, signals, track=row.track or "", job_type=job_type
        )
        if score < min_score:
            continue
        scored.append(
            RecommendationItem(
                job_id=row.id,
                company_id=row.company_id,
                company_name=company_names.get(row.company_id)
                or str(row.company_id or "").replace("offerbiu:", ""),
                title=row.title or "",
                city=row.city,
                track=row.track or "unknown",
                batch=row.batch or "unknown",
                detail_url=row.detail_url or "",
                score=score,
                reasons=reasons,
            )
        )

    scored.sort(key=lambda item: (-item.score, item.company_name, item.title))
    # Announcement imports can carry the same company + position set in several
    # batches; keep only the best-scoring copy so the list reads as positions,
    # not as crawl history.
    deduped: list[RecommendationItem] = []
    seen: set[tuple[str, str]] = set()
    for item in scored:
        key = (item.company_name, item.title[:48])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    matched = len(deduped)
    return RecommendationResult(
        profile_ready=True,
        profile_keywords=keyword_counts,
        total_scanned=len(rows),
        matched=matched,
        items=tuple(deduped[:limit]),
        hint="" if matched else "没有岗位达到分数门槛，可以在「配置」里补充岗位关键词或降低门槛。",
    )


__all__ = [
    "RecommendationItem",
    "RecommendationResult",
    "profile_signals",
    "recommend_jobs",
    "score_posting",
]
