"""OfferBiu announcement ingestion.

OfferBiu is the upstream project's company-discovery source.  Here it is also
used as a *content* source: its public feed carries company, industry,
recruitment type (秋招/春招/实习), target graduation years, cities, position
names, deadlines and an apply link — exactly what a student wants to know when
a company opens hiring.

Each announcement is stored as one ``job_snapshots`` row (source
``offerbiu``) and one ``company_snapshots`` row, so the existing change-feed,
digest and group-bot push work without modification.  Announcements are
coarser than per-position crawls (``positionsText`` may name several roles),
which is why the row title is the position text itself.

Baseline semantics are inherited from the intelligence writer: the first
ingest baselines each company instead of emitting a flood of "new" events.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests
from sqlalchemy import select

from packages.storage import CompanySnapshot, JobSnapshot, Storage

from .writer import EventWriteResult, record_pipeline_events

OFFERBIU_ENDPOINT = "https://offerbiu.com/api/recruitment/postings"
OFFERBIU_SOURCE = "offerbiu"
DEFAULT_PAGE_SIZE = 50
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_DELAY_SECONDS = 0.2
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
#: OfferBiu labels internships as 实习 and campus drives as 秋招 / 春招.
CAMPUS_RECRUIT_TYPES = frozenset({"秋招", "春招", "提前批", "秋招补录"})
INTERNSHIP_RECRUIT_TYPES = frozenset({"实习", "暑期实习", "日常实习"})
_SOCIAL_RECRUIT_TYPES = frozenset({"社招", "社会招聘"})


class OfferBiuError(RuntimeError):
    """Raised when the public feed cannot be read."""


@dataclass(frozen=True, slots=True)
class OfferBiuIngestResult:
    """Stable summary of one ingestion run."""

    run_id: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    companies_written: int = 0
    skipped_invalid: int = 0
    by_track: Mapping[str, int] = field(default_factory=dict)
    by_recruit_type: Mapping[str, int] = field(default_factory=dict)
    events: EventWriteResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "fetched": self.fetched,
            "inserted": self.inserted,
            "updated": self.updated,
            "companies_written": self.companies_written,
            "skipped_invalid": self.skipped_invalid,
            "by_track": dict(self.by_track),
            "by_recruit_type": dict(self.by_recruit_type),
            "events": self.events.to_dict() if self.events else None,
        }


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_offerbiu_postings(
    *,
    recruit_types: Sequence[str] = ("实习", "秋招", "春招"),
    industry: str | None = None,
    season_year: int | None = None,
    max_pages_per_type: int = 60,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    session: requests.Session | None = None,
    logger: Any = None,
) -> list[dict[str, Any]]:
    """Read the public announcement feed, one paginated pass per recruit type.

    Returns de-duplicated raw items.  Per-type paging keeps the request count
    predictable (a single mixed pass would need ~490 pages for everything).
    """

    own_session = session is None
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", _USER_AGENT)
    collected: dict[str, dict[str, Any]] = {}
    try:
        for recruit_type in recruit_types:
            page = 1
            total_pages = 1
            while page <= total_pages and page <= max_pages_per_type:
                params: dict[str, Any] = {"page": page, "page_size": page_size}
                if recruit_type:
                    params["recruitType"] = recruit_type
                if industry:
                    params["industry"] = industry
                if season_year:
                    params["seasonYear"] = season_year
                try:
                    response = http.get(
                        OFFERBIU_ENDPOINT, params=params, timeout=timeout
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (requests.RequestException, ValueError) as exc:
                    raise OfferBiuError(
                        f"OfferBiu 第 {page} 页读取失败（{recruit_type}）: {exc}"
                    ) from exc
                data = payload.get("data") if isinstance(payload, Mapping) else None
                if not isinstance(data, Mapping):
                    raise OfferBiuError("OfferBiu 返回结构异常")
                items = data.get("items") or []
                total_pages = int(data.get("totalPages") or 1)
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    item_id = str(item.get("id") or "").strip()
                    if item_id and item_id not in collected:
                        collected[item_id] = dict(item)
                if logger is not None:
                    logger(
                        f"[offerbiu] {recruit_type} page {page}/{min(total_pages, max_pages_per_type)} "
                        f"collected={len(collected)}"
                    )
                if not items:
                    break
                page += 1
                if delay_seconds:
                    import time

                    time.sleep(delay_seconds)
    finally:
        if own_session:
            http.close()
    return list(collected.values())


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value or "").strip()


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _cohort_years(item: Mapping[str, Any]) -> tuple[int | None, str | None]:
    years = item.get("targetYears")
    if isinstance(years, Sequence) and not isinstance(years, (str, bytes)):
        for raw in years:
            try:
                year = int(raw)
            except (TypeError, ValueError):
                continue
            if 2000 <= year <= 2100:
                return year, f"targetYears={year}"
    return None, None


def _track_and_batch(recruit_type: str) -> tuple[str, str]:
    """Map a recruit-type label to (track, batch).

    OfferBiu labels are sometimes compound (``"春招, 实习"``, ``"实习, 秋招专场"``),
    so this matches by containment with internship taking precedence: a posting
    that is also open as an internship is most useful filed as one.
    """

    label = _text(recruit_type)
    if any(token in label for token in INTERNSHIP_RECRUIT_TYPES):
        return "internship", "internship"
    if any(token in label for token in CAMPUS_RECRUIT_TYPES):
        return "campus", "formal"
    if any(token in label for token in _SOCIAL_RECRUIT_TYPES):
        return "social", "formal"
    return "unknown", "unknown"


def _company_id(item: Mapping[str, Any]) -> str:
    raw = _text(item.get("companyId")) or _text(item.get("companyName"))
    if raw.startswith("rc-"):
        raw = raw[3:]
    return f"offerbiu:{raw}"[:255]


def _detail_url(item: Mapping[str, Any]) -> str:
    return _text(item.get("applyUrl")) or _text(item.get("announcementUrl"))


def _summary(item: Mapping[str, Any]) -> str:
    """Render the announcement fields into readable JD-like text."""

    lines: list[str] = []
    company = _text(item.get("companyName"))
    if company:
        lines.append(f"公司：{company}")
    for label, key in (
        ("性质", "companyNature"),
        ("行业", "industry"),
        ("招聘类型", "recruitType"),
        ("笔试政策", "examPolicy"),
    ):
        value = _text(item.get(key))
        if value:
            lines.append(f"{label}：{value}")
    years = item.get("targetYears")
    if isinstance(years, Sequence) and not isinstance(years, (str, bytes)) and years:
        lines.append("面向届别：" + "、".join(str(y) for y in years))
    locations = item.get("locations")
    if isinstance(locations, Sequence) and not isinstance(locations, (str, bytes)) and locations:
        lines.append("工作地点：" + "、".join(str(c) for c in locations))
    positions = _text(item.get("positionsText"))
    if positions:
        lines.append("招聘方向：" + positions)
    deadline = _text(item.get("deadlineText"))
    if deadline:
        lines.append("截止时间：" + deadline)
    note = _text(item.get("noteText"))
    if note:
        lines.append("备注：" + note)
    return "\n".join(lines)


def _job_row(item: Mapping[str, Any], *, now: datetime) -> tuple[JobSnapshot, str] | None:
    """Map one announcement to a JobSnapshot plus its company id."""

    item_id = _text(item.get("id"))
    company_name = _text(item.get("companyName"))
    if not item_id or not company_name:
        return None
    track, batch = _track_and_batch(_text(item.get("recruitType")))
    positions = _text(item.get("positionsText"))
    title = _clip(positions or f"{company_name}招聘", 500)
    detail = _detail_url(item)
    if not detail:
        return None
    cohort, cohort_source = _cohort_years(item)
    locations = item.get("locations")
    city = None
    if isinstance(locations, Sequence) and not isinstance(locations, (str, bytes)) and locations:
        city = _clip("、".join(str(c) for c in locations), 250)
    status = _text(item.get("status")).upper()
    refreshed = _text(item.get("sourceUpdatedAt")) or _text(item.get("importedAt"))
    try:
        last_seen = datetime.fromisoformat(refreshed.replace("Z", "+00:00")) if refreshed else now
    except ValueError:
        last_seen = now
    company_id = _company_id(item)
    row = JobSnapshot(
        id=f"offerbiu-{item_id}",
        company_id=company_id,
        title=title,
        city=city,
        detail_url=_clip(detail, 2040),
        jd_raw=_summary(item),
        cohort=cohort,
        cohort_status="confirmed" if cohort else "unconfirmed",
        batch=batch,
        track=track,
        capture_status="complete",
        capture_evidence={
            "cohort_source": cohort_source or "",
            "cohort_evidence": f"OfferBiu targetYears={cohort}" if cohort else "",
            "recruit_type": _text(item.get("recruitType")),
            "deadline_text": _text(item.get("deadlineText")),
            "company_nature": _text(item.get("companyNature")),
            "industry": _text(item.get("industry")),
            "announcement_url": _text(item.get("announcementUrl")),
        },
        availability_status="active" if status in ("", "ACTIVE") else "inactive",
        first_seen_at=last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc),
        last_seen_at=now,
        source=OFFERBIU_SOURCE,
        source_ref=item_id[:512],
        created_at=now,
        updated_at=now,
    )
    return row, company_id


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest_offerbiu_postings(
    storage: Storage,
    postings: Iterable[Mapping[str, Any]],
    *,
    run_id: str | None = None,
    now: datetime | None = None,
    record_events: bool = True,
) -> OfferBiuIngestResult:
    """Upsert announcements as job/company snapshots, then record events."""

    if not isinstance(storage, Storage):
        raise TypeError("storage must be a Storage instance")
    storage.initialize()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    run = run_id or f"offerbiu-{uuid4().hex[:16]}"

    inserted = updated = skipped = 0
    by_track: dict[str, int] = {}
    by_type: dict[str, int] = {}
    company_names: dict[str, str] = {}
    new_ids: list[str] = []
    changed_ids: list[str] = []
    fetched = 0

    with storage.session() as session:
        existing = {
            row.id: row
            for row in session.scalars(
                select(JobSnapshot).where(JobSnapshot.source == OFFERBIU_SOURCE)
            ).all()
        }
        known_companies = {
            row.id: row
            for row in session.scalars(select(CompanySnapshot)).all()
        }
        for raw in postings:
            fetched += 1
            mapped = _job_row(raw, now=current)
            if mapped is None:
                skipped += 1
                continue
            row, company_id = mapped
            recruit_type = _text(raw.get("recruitType")) or "未知"
            by_type[recruit_type] = by_type.get(recruit_type, 0) + 1
            by_track[row.track] = by_track.get(row.track, 0) + 1
            company_names[company_id] = _text(raw.get("companyName"))

            previous = existing.get(row.id)
            if previous is None:
                session.add(row)
                existing[row.id] = row
                inserted += 1
                new_ids.append(row.id)
            else:
                content_changed = (
                    (previous.title or "") != row.title
                    or (previous.detail_url or "") != row.detail_url
                    or (previous.jd_raw or "") != row.jd_raw
                    or (previous.availability_status or "") != row.availability_status
                )
                # Keep stable discovery timestamps across re-ingestion.
                previous.title = row.title
                previous.city = row.city
                previous.detail_url = row.detail_url
                previous.jd_raw = row.jd_raw
                previous.cohort = row.cohort or previous.cohort
                previous.cohort_status = row.cohort_status or previous.cohort_status
                previous.batch = row.batch
                previous.track = row.track
                previous.availability_status = row.availability_status
                previous.last_seen_at = current
                previous.updated_at = current
                if content_changed:
                    updated += 1
                    changed_ids.append(row.id)

        for company_id, name in company_names.items():
            snapshot = known_companies.get(company_id)
            if snapshot is None:
                session.add(
                    CompanySnapshot(
                        id=company_id,
                        name=_clip(name, 250),
                        aliases=[],
                        crawler_key=OFFERBIU_SOURCE,
                        integration_status="connected",
                        source=OFFERBIU_SOURCE,
                        source_ref=company_id[:512],
                        created_at=current,
                        updated_at=current,
                    )
                )
            else:
                snapshot.name = _clip(name, 250)
                snapshot.updated_at = current
        session.commit()
        companies_written = len(company_names)

    events = None
    if record_events and (new_ids or changed_ids):
        events = record_pipeline_events(
            storage,
            {
                "new_job_ids": new_ids,
                "changed_job_ids": changed_ids,
                "companies": [
                    {"company_id": company_id, "company_name": name}
                    for company_id, name in company_names.items()
                ],
            },
            run_id=run,
            run_started_at=current,
            clock=current,
        )

    return OfferBiuIngestResult(
        run_id=run,
        fetched=fetched,
        inserted=inserted,
        updated=updated,
        companies_written=companies_written,
        skipped_invalid=skipped,
        by_track=by_track,
        by_recruit_type=by_type,
        events=events,
    )


__all__ = [
    "OfferBiuError",
    "OfferBiuIngestResult",
    "fetch_offerbiu_postings",
    "ingest_offerbiu_postings",
]
