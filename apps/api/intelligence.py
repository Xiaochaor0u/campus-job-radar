"""HTTP API for the job intelligence change feed.

Read-only endpoints backed by the ``job_events`` log.  The digest powers the
workbench "今日新发布" view; the preview endpoint renders exactly what a
configured group bot would push, without sending anything.
"""

from __future__ import annotations

from functools import lru_cache
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
