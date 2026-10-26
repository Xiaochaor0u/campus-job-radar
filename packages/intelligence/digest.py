"""Build the "what changed" digest shown in the workbench and pushed to chats.

Reads only the ``job_events`` append-only log.  ``baseline`` rows are excluded
everywhere: they exist to suppress the first-crawl flood, not to be shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from packages.storage import JobEvent, Storage

DIGEST_EVENT_TYPES = ("new", "updated", "removed")
DEFAULT_DIGEST_HOURS = 24.0
DEFAULT_MAX_ITEMS_PER_TYPE = 20


@dataclass(frozen=True, slots=True)
class DigestItem:
    """One user-visible event."""

    event_type: str
    job_id: str
    company_id: str
    company_name: str
    title: str
    city: str | None
    batch: str
    track: str
    detail_url: str
    detected_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "job_id": self.job_id,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "title": self.title,
            "city": self.city,
            "batch": self.batch,
            "track": self.track,
            "detail_url": self.detail_url,
            "detected_at": self.detected_at,
        }


@dataclass(frozen=True, slots=True)
class CompanyDigest:
    """Events grouped by company."""

    company_id: str
    company_name: str
    items: tuple[DigestItem, ...] = ()

    @property
    def new_count(self) -> int:
        return sum(1 for item in self.items if item.event_type == "new")

    @property
    def updated_count(self) -> int:
        return sum(1 for item in self.items if item.event_type == "updated")

    @property
    def removed_count(self) -> int:
        return sum(1 for item in self.items if item.event_type == "removed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "company_name": self.company_name,
            "new_count": self.new_count,
            "updated_count": self.updated_count,
            "removed_count": self.removed_count,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class DigestResult:
    """Stable digest payload for the API and the webhook formatter."""

    generated_at: str
    since: str
    hours: float
    companies: tuple[CompanyDigest, ...] = ()
    total_new: int = 0
    total_updated: int = 0
    total_removed: int = 0
    truncated: bool = False
    filters: Mapping[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.companies

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "since": self.since,
            "hours": self.hours,
            "empty": self.empty,
            "total_new": self.total_new,
            "total_updated": self.total_updated,
            "total_removed": self.total_removed,
            "truncated": self.truncated,
            "filters": dict(self.filters),
            "companies": [company.to_dict() for company in self.companies],
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_daily_digest(
    storage: Storage,
    *,
    hours: float = DEFAULT_DIGEST_HOURS,
    company_id: str | None = None,
    track: str | None = None,
    event_types: tuple[str, ...] = DIGEST_EVENT_TYPES,
    max_items_per_type: int = DEFAULT_MAX_ITEMS_PER_TYPE,
    now: datetime | None = None,
) -> DigestResult:
    """Collect user-visible events detected within the last ``hours``."""

    if not isinstance(storage, Storage):
        raise TypeError("storage must be a Storage instance")
    if hours <= 0:
        raise ValueError("hours must be positive")
    if max_items_per_type < 1:
        raise ValueError("max_items_per_type must be positive")
    # A fresh SQLite checkout has no migration runner; create_all is idempotent
    # and makes the empty ("no changes yet") answer work instead of crashing.
    storage.initialize()

    current = now or _utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    since = current - timedelta(hours=float(hours))

    normalized_types = tuple(
        item for item in (t.strip().casefold() for t in event_types) if item
    ) or DIGEST_EVENT_TYPES
    filters: dict[str, Any] = {"hours": hours, "event_types": list(normalized_types)}
    conditions = [
        JobEvent.event_type.in_(normalized_types),
        JobEvent.detected_at >= since,
        JobEvent.detected_at <= current,
    ]
    if company_id:
        conditions.append(JobEvent.company_id == str(company_id).strip())
        filters["company_id"] = str(company_id).strip()
    if track:
        filters["track"] = str(track).strip()

    with storage.session() as session:
        rows = list(
            session.scalars(
                select(JobEvent)
                .where(*conditions)
                .order_by(JobEvent.detected_at.desc(), JobEvent.company_id.asc())
            ).all()
        )

    items: list[DigestItem] = []
    for row in rows:
        if track and str(row.track or "unknown") != str(track).strip():
            continue
        items.append(
            DigestItem(
                event_type=row.event_type,
                job_id=row.job_id,
                company_id=row.company_id,
                company_name=row.company_name,
                title=row.title,
                city=row.city,
                batch=row.batch,
                track=row.track,
                detail_url=row.detail_url,
                detected_at=row.detected_at.isoformat() if row.detected_at else "",
            )
        )

    by_company: dict[str, list[DigestItem]] = {}
    for item in items:
        by_company.setdefault(item.company_id, []).append(item)

    truncated = False
    companies: list[CompanyDigest] = []
    total_new = total_updated = total_removed = 0
    for company_key in sorted(by_company):
        group = by_company[company_key]
        counts = {"new": 0, "updated": 0, "removed": 0}
        kept: list[DigestItem] = []
        for digest_item in group:
            kind = digest_item.event_type
            if kind in counts:
                counts[kind] += 1
            if len(kept) >= max_items_per_type * len(DIGEST_EVENT_TYPES):
                truncated = True
                continue
            kept.append(digest_item)
        total_new += counts["new"]
        total_updated += counts["updated"]
        total_removed += counts["removed"]
        companies.append(
            CompanyDigest(
                company_id=company_key,
                company_name=group[0].company_name or company_key,
                items=tuple(kept),
            )
        )

    return DigestResult(
        generated_at=current.isoformat(),
        since=since.isoformat(),
        hours=float(hours),
        companies=tuple(companies),
        total_new=total_new,
        total_updated=total_updated,
        total_removed=total_removed,
        truncated=truncated,
        filters=filters,
    )
