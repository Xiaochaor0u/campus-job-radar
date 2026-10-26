"""HTTP API for the job intelligence change feed.

Read-only endpoints backed by the ``job_events`` log.  The digest powers the
workbench "今日新发布" view; the preview endpoint renders exactly what a
configured group bot would push, without sending anything.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from packages.config import get_settings
from packages.intelligence.digest import build_daily_digest
from packages.intelligence.webhook import (
    NotifyConfigError,
    format_digest_text,
    load_notify_config,
)
from packages.storage import Storage

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


@lru_cache(maxsize=1)
def _storage() -> Storage:
    """One engine per process; create_all keeps a fresh SQLite usable."""

    return Storage.from_url(get_settings().database_url, initialize=True)


def _resolve_hours(config_hours: float, query_hours: float | None) -> float:
    return config_hours if query_hours is None else query_hours


@router.get("/digest")
def intelligence_digest(
    hours: float | None = Query(default=None, ge=0.5, le=24 * 30),
    company_id: str | None = Query(default=None, max_length=255),
    track: str | None = Query(default=None, max_length=32),
    max_items: int | None = Query(default=None, ge=1, le=100),
) -> dict[str, Any]:
    """Aggregate new/updated/removed events for the recent window."""

    try:
        notify = load_notify_config()
    except NotifyConfigError:
        notify = None
    effective_hours = _resolve_hours(
        notify.digest_hours if notify else 24.0,
        hours,
    )
    try:
        digest = build_daily_digest(
            _storage(),
            hours=effective_hours,
            company_id=company_id or None,
            track=track or None,
            max_items_per_type=max_items or 20,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return digest.to_dict()


@router.get("/events")
def intelligence_events(
    event_type: str | None = Query(default=None, pattern="^(new|updated|removed|baseline)$"),
    company_id: str | None = Query(default=None, max_length=255),
    hours: float = Query(default=24.0, ge=0.5, le=24 * 30),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Flat event feed, newest first."""

    digest = build_daily_digest(
        _storage(),
        hours=hours,
        company_id=company_id or None,
        event_types=(event_type,) if event_type else ("new", "updated", "removed"),
        max_items_per_type=limit,
    )
    items: list[dict[str, Any]] = []
    for company in digest.companies:
        for item in company.items:
            if event_type and item.event_type != event_type:
                continue
            items.append(item.to_dict())
    items.sort(key=lambda row: row.get("detected_at") or "", reverse=True)
    return {
        "hours": hours,
        "company_id": company_id,
        "event_type": event_type,
        "total": len(items),
        "items": items[:limit],
    }


@router.get("/jobs")
def intelligence_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    track: str | None = Query(default=None, max_length=32),
    batch: str | None = Query(default=None, max_length=32),
    source: str | None = Query(default=None, max_length=32),
    company_id: str | None = Query(default=None, max_length=255),
    keyword: str | None = Query(default=None, max_length=100),
    campus_only: bool = Query(default=False),
) -> dict[str, Any]:
    """Browse everything stored, without the upstream 2027-cohort gate.

    The "27届校招" view hides rows whose graduation year is unconfirmed; this
    endpoint is how a student still sees them (and how the first OfferBiu
    import is inspectable at all).
    """

    from sqlalchemy import func, or_, select

    from packages.storage import JobSnapshot

    conditions = [JobSnapshot.availability_status == "active"]
    if track:
        conditions.append(JobSnapshot.track == track)
    if batch:
        conditions.append(JobSnapshot.batch == batch)
    if source:
        conditions.append(JobSnapshot.source == source)
    if company_id:
        conditions.append(JobSnapshot.company_id == company_id)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                JobSnapshot.title.ilike(pattern),
                JobSnapshot.company_id.ilike(pattern),
                JobSnapshot.city.ilike(pattern),
            )
        )
    if campus_only:
        conditions.extend(
            [
                JobSnapshot.cohort == 2027,
                func.lower(JobSnapshot.cohort_status) == "confirmed",
            ]
        )

    storage = _storage()
    offset = (page - 1) * page_size
    with storage.session() as session:
        total = session.scalar(
            select(func.count()).select_from(JobSnapshot).where(*conditions)
        ) or 0
        rows = list(
            session.scalars(
                select(JobSnapshot)
                .where(*conditions)
                .order_by(JobSnapshot.first_seen_at.desc(), JobSnapshot.id.asc())
                .offset(offset)
                .limit(page_size)
            ).all()
        )
    return {
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "pages": (int(total) + page_size - 1) // page_size,
        "items": [
            {
                "id": row.id,
                "company_id": row.company_id,
                "title": row.title,
                "city": row.city,
                "detail_url": row.detail_url,
                "batch": row.batch,
                "track": row.track,
                "source": row.source,
                "cohort": row.cohort,
                "cohort_status": row.cohort_status,
                "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else "",
                "deadline_text": (row.capture_evidence or {}).get("deadline_text", ""),
                "recruit_type": (row.capture_evidence or {}).get("recruit_type", ""),
                "industry": (row.capture_evidence or {}).get("industry", ""),
            }
            for row in rows
        ],
    }


@router.get("/recommend")
def intelligence_recommend(
    limit: int = Query(default=30, ge=1, le=200),
    min_score: int = Query(default=1, ge=0, le=100),
    track: str | None = Query(default=None, max_length=32),
    company_id: str | None = Query(default=None, max_length=255),
) -> dict[str, Any]:
    """Rank every stored posting against the candidate profile.

    Deterministic and offline: no model call, no key required.  The profile
    itself comes from the 配置 page (resume upload writes it).
    """

    from packages.candidate_profile.loader import (
        CandidateProfileError,
        load_candidate_profile,
    )
    from packages.intelligence.recommend import recommend_jobs

    settings = get_settings()
    try:
        profile = load_candidate_profile(settings.candidate_profile_config)
    except (CandidateProfileError, OSError, ValueError):
        profile = {}

    result = recommend_jobs(
        _storage(),
        profile,
        limit=limit,
        min_score=min_score,
        track=track or None,
        company_id=company_id or None,
    )
    return result.to_dict()


@router.post("/job-insight")
def intelligence_job_insight(payload: dict[str, Any]) -> dict[str, Any]:
    """Profile one posting on demand: stated bar, salary wording, peer context.

    On-demand by design. Salary wording exists in well under 1% of the stored
    announcements, so pre-computing this for every row would spend model calls
    on rows that have nothing to read.
    """

    from packages.intelligence.insight import build_job_insight

    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise HTTPException(422, "job_id 不能为空")
    use_model = payload.get("use_model", True) is not False
    result = build_job_insight(_storage(), job_id, use_model=use_model)
    return result.to_dict()


@router.post("/resume-extract")
def intelligence_resume_extract(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn pasted resume text into profile keywords without a model call.

    Fallback for users who have no API key: the 配置 page's parser needs a
    model, this one only needs a word list.  Results are suggestions the user
    reviews before saving.
    """

    text = str(payload.get("text") or "").strip()
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="简历内容太短，至少需要 20 个字符")
    if len(text) > 200_000:
        raise HTTPException(status_code=400, detail="简历内容过长，请截取主要部分")
    from packages.intelligence.resume_keywords import extract_profile_hints

    return extract_profile_hints(text)


@router.post("/profile-save")
def intelligence_profile_save(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist recommendation preferences into the candidate profile.

    Only the fields this endpoint owns are replaced; everything else already in
    the profile (project evidence, excluded keywords, …) is preserved.  This is
    the key-free path: paste a resume, accept the extracted words, save.
    """

    import yaml

    settings = get_settings()
    path = Path(settings.candidate_profile_config)
    path.parent.mkdir(parents=True, exist_ok=True)

    document: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                document = loaded
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=400, detail=f"现有简历配置无法读取：{exc}") from exc

    profile = document.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    matching = profile.get("matching")
    if not isinstance(matching, dict):
        matching = {}

    def _clean_list(value: Any, limit: int = 40) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = " ".join(str(item or "").split())
            if text and text not in result:
                result.append(text[:60])
        return result[:limit]

    if "skills" in payload:
        profile["skills"] = _clean_list(payload.get("skills"))
    if "degree" in payload:
        degree = str(payload.get("degree") or "").strip()
        if degree:
            profile["degree"] = degree
    if "job_type" in payload:
        job_type = str(payload.get("job_type") or "").strip()
        if job_type in {"campus", "internship"}:
            profile["job_type"] = job_type
    if "title_keywords" in payload:
        matching["title_keywords"] = _clean_list(payload.get("title_keywords"))
    if "primary_directions" in payload:
        matching["primary_directions"] = _clean_list(payload.get("primary_directions"))
    matching.setdefault("direction_policy", "parallel")

    profile["matching"] = matching
    document["profile"] = profile

    try:
        path.write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入简历配置失败：{exc}") from exc

    return {
        "saved": True,
        "path": str(path),
        "skills": profile.get("skills") or [],
        "title_keywords": matching.get("title_keywords") or [],
        "primary_directions": matching.get("primary_directions") or [],
        "degree": profile.get("degree") or "",
        "job_type": profile.get("job_type") or "",
    }


@router.get("/watch/meta")
def watch_meta() -> dict[str, Any]:
    """Rules plus data freshness — the page needs both to be honest."""

    from packages.intelligence.watch import VALID_WINDOWS, latest_event_at, list_rules

    rules = list_rules(_storage())
    return {
        "windows": list(VALID_WINDOWS),
        "latest_event_at": latest_event_at(_storage()),
        "rules": [
            {
                "id": row.id,
                "name": row.name,
                "keywords": row.keywords or [],
                "directions": row.directions or [],
                "cities": row.cities or [],
                "track": row.track or "",
                "enabled": bool(row.enabled),
            }
            for row in rules
        ],
    }


@router.post("/watch/rules")
def watch_save_rule(payload: dict[str, Any]) -> dict[str, Any]:
    from packages.intelligence.watch import WatchCondition, save_rule

    condition = WatchCondition.from_mapping(payload)
    try:
        row = save_rule(
            _storage(),
            condition,
            name=str(payload.get("name") or ""),
            rule_id=str(payload["id"]) if payload.get("id") else None,
            enabled=bool(payload.get("enabled", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "saved": True,
        "rule": {
            "id": row.id,
            "name": row.name,
            "keywords": row.keywords or [],
            "directions": row.directions or [],
            "cities": row.cities or [],
            "track": row.track or "",
            "enabled": bool(row.enabled),
        },
    }


@router.delete("/watch/rules/{rule_id}")
def watch_delete_rule(rule_id: str) -> dict[str, Any]:
    from packages.intelligence.watch import delete_rule

    if not delete_rule(_storage(), rule_id):
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"deleted": True, "id": rule_id}


@router.get("/watch/run")
def watch_run(
    rule_id: str | None = Query(default=None, max_length=64),
    window: str = Query(default="today", max_length=8),
    keywords: str | None = Query(default=None, max_length=200),
    directions: str | None = Query(default=None, max_length=200),
    cities: str | None = Query(default=None, max_length=200),
    track: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Run one saved rule, or an ad-hoc condition when no rule_id is given."""

    from packages.intelligence.watch import (
        VALID_WINDOWS,
        WatchCondition,
        list_rules,
        run_watch,
    )

    normalized_window = (window or "today").strip().casefold()
    if normalized_window not in VALID_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"window 只能是：{', '.join(VALID_WINDOWS)}",
        )

    storage = _storage()
    rule_name = ""
    if rule_id:
        rule = next((row for row in list_rules(storage) if row.id == rule_id), None)
        if rule is None:
            raise HTTPException(status_code=404, detail="订阅不存在")
        condition = WatchCondition.from_mapping(rule)
        rule_name = rule.name
    else:
        condition = WatchCondition(
            keywords=tuple(part for part in (keywords or "").replace("，", ",").split(",") if part.strip()),
            directions=tuple(part for part in (directions or "").replace("，", ",").split(",") if part.strip()),
            cities=tuple(part for part in (cities or "").replace("，", ",").split(",") if part.strip()),
            track=(track or "").strip().casefold(),
        )
        if condition.empty:
            raise HTTPException(
                status_code=400,
                detail="请至少提供一个筛选条件（关键词 / 方向 / 城市 / 类型）",
            )

    result = run_watch(
        storage,
        condition,
        window=normalized_window,
        limit=limit,
        rule_id=rule_id,
        rule_name=rule_name,
    )
    return result.to_dict()


@router.get("/coverage")
def intelligence_coverage() -> dict[str, Any]:
    """How many postings are actually stored, per company and cohort state.

    The "27届校招" view only shows rows whose cohort is confirmed 2027; postings
    whose title never states a graduation year stay honest-but-hidden there.
    This endpoint makes that gap visible instead of looking like a failed crawl.
    """

    from sqlalchemy import case, func, select

    from packages.storage import JobSnapshot

    storage = _storage()
    with storage.session() as session:
        total = session.scalar(select(func.count()).select_from(JobSnapshot)) or 0
        confirmed = session.scalar(
            select(func.count())
            .select_from(JobSnapshot)
            .where(
                JobSnapshot.cohort == 2027,
                func.lower(JobSnapshot.cohort_status) == "confirmed",
            )
        ) or 0
        active = session.scalar(
            select(func.count())
            .select_from(JobSnapshot)
            .where(JobSnapshot.availability_status == "active")
        ) or 0
        rows = session.execute(
            select(
                JobSnapshot.company_id,
                func.count().label("jobs"),
                func.sum(
                    case(
                        (
                            (JobSnapshot.cohort == 2027)
                            & (func.lower(JobSnapshot.cohort_status) == "confirmed"),
                            1,
                        ),
                        else_=0,
                    )
                ).label("campus_ready"),
            )
            .group_by(JobSnapshot.company_id)
            .order_by(func.count().desc())
        ).all()
    return {
        "total_jobs": int(total),
        "active_jobs": int(active),
        "campus_ready_jobs": int(confirmed),
        "cohort_unknown_jobs": int(total) - int(confirmed),
        "companies": [
            {
                "company_id": row.company_id,
                "jobs": int(row.jobs or 0),
                "campus_ready": int(row.campus_ready or 0),
            }
            for row in rows
        ],
    }


@router.get("/push-preview")
def intelligence_push_preview(
    hours: float | None = Query(default=None, ge=0.5, le=24 * 30),
) -> dict[str, Any]:
    """Render the exact message a group bot would receive (nothing is sent)."""

    try:
        notify = load_notify_config()
    except NotifyConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    effective_hours = _resolve_hours(notify.digest_hours, hours)
    digest = build_daily_digest(
        _storage(),
        hours=effective_hours,
        max_items_per_type=notify.max_items_per_push,
    )
    return {
        "hours": effective_hours,
        "webhook_count": len(notify.webhooks),
        "webhook_types": [target.type for target in notify.webhooks],
        "markdown_message": format_digest_text(
            digest, max_items_per_type=notify.max_items_per_push, markdown=True
        ),
        "plain_message": format_digest_text(
            digest, max_items_per_type=notify.max_items_per_push, markdown=False
        ),
    }
