"""Import jobs found outside the OfferBiu feed.

OfferBiu is a good base but it is one aggregator: it misses company sites that
never publish a WeChat announcement, and it lags when a company updates its own
career page first.  This module is the second channel — anything that can be
turned into structured rows (a verified official page, another aggregator, a
spreadsheet) can be written into the same tables, tagged with its own source.

The rules stay the same as everywhere else in this project:

* every row must carry the URL it came from, because an unverifiable job is
  worse than a missing one;
* re-importing the same row updates it instead of duplicating it;
* the row's ``source`` is stored, so the UI can say where a job came from.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import select

from packages.intelligence.writer import record_pipeline_events
from packages.storage import CompanySnapshot, JobSnapshot, Storage

DEFAULT_SOURCE = "web"

TRACKS = {"campus": "campus", "internship": "internship", "unknown": "unknown"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = _text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _as_year(value: Any) -> int | None:
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if 2000 <= year <= 2100 else None


@dataclass
class ExternalJob:
    """One posting from a non-OfferBiu source."""

    company: str
    title: str
    url: str
    city: str = ""
    published_at: date | None = None
    deadline_date: date | None = None
    deadline_text: str = ""
    track: str = "campus"
    cohort: int | None = None
    positions: tuple[str, ...] = ()
    source: str = DEFAULT_SOURCE
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any], *, source: str) -> "ExternalJob | None":
        company = _text(row.get("company") or row.get("company_name"))
        title = _text(row.get("title") or row.get("job") or row.get("position"))
        url = _text(row.get("url") or row.get("link"))
        if not company or not title or not url:
            # No URL means nobody can verify or apply — refuse it instead of
            # storing a row the reader can only stare at.
            return None
        track = _text(row.get("track")).lower()
        positions = row.get("positions") or ()
        if isinstance(positions, str):
            positions = tuple(part.strip() for part in positions.split(",") if part.strip())
        return cls(
            company=company,
            title=title,
            url=url,
            city=_text(row.get("city")),
            published_at=_as_date(row.get("published_at") or row.get("publish_date")),
            deadline_date=_as_date(row.get("deadline") or row.get("deadline_date")),
            deadline_text=_text(row.get("deadline_text")),
            track=TRACKS.get(track, "unknown"),
            cohort=_as_year(row.get("cohort")),
            positions=tuple(str(p).strip() for p in positions if str(p).strip()),
            source=_text(row.get("source")) or source,
            note=_text(row.get("note")),
            raw=dict(row),
        )


def load_jobs_file(path: Path | str, *, source: str = DEFAULT_SOURCE) -> list[ExternalJob]:
    """Read a JSON or CSV file of postings.

    JSON accepts ``{"source": ..., "items": [...]}`` or a bare list; CSV uses
    the same column names as the JSON keys.
    """

    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"找不到文件：{target}")
    suffix = target.suffix.lower()
    rows: list[Mapping[str, Any]]
    file_source = source
    if suffix == ".csv":
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    else:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            file_source = _text(payload.get("source")) or source
            rows = list(payload.get("items") or [])
        elif isinstance(payload, list):
            rows = payload
        else:
            raise ValueError("JSON 需要是数组，或 {\"items\": [...]} 对象")

    jobs: list[ExternalJob] = []
    for row in rows:
        job = ExternalJob.from_mapping(row, source=file_source)
        if job is not None:
            jobs.append(job)
    return jobs


def _job_digest(job: ExternalJob) -> str:
    seed = "|".join((job.source, job.company, job.title, job.city))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _job_row(job: ExternalJob, *, now: datetime) -> tuple[JobSnapshot, str]:
    digest = _job_digest(job)
    company_id = f"{job.source}:{job.company}"
    evidence = {
        "source": job.source,
        "note": job.note,
        "url": job.url,
        "positions": list(job.positions),
        "imported_at": now.isoformat(),
    }
    row = JobSnapshot(
        id=f"{job.source}:{digest}",
        company_id=company_id,
        title=job.title[:512],
        city=job.city[:255] or None,
        detail_url=job.url[:2048],
        jd_raw="\n".join(job.positions)[:20000] if job.positions else None,
        cohort=job.cohort,
        cohort_status="confirmed" if job.cohort else "unknown",
        batch="formal",
        first_seen_at=now,
        last_seen_at=now,
        source=job.source,
        source_ref=f"{job.source}:{digest}:fp:{hashlib.sha256(job.url.encode()).hexdigest()[:64]}",
        availability_status="active",
        track=job.track,
        publish_date=job.published_at,
        deadline_date=job.deadline_date,
        deadline_text=job.deadline_text[:64] or None,
        capture_evidence=evidence,
        created_at=now,
        updated_at=now,
    )
    return row, company_id


def ingest_external_jobs(
    storage: Storage,
    jobs: Iterable[ExternalJob],
    *,
    run_id: str | None = None,
    now: datetime | None = None,
    record_events: bool = True,
) -> dict[str, Any]:
    """Upsert externally collected postings and record change events."""

    if not isinstance(storage, Storage):
        raise TypeError("storage must be a Storage instance")
    storage.initialize()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    run = run_id or f"external-{uuid4().hex[:16]}"

    inserted = updated = skipped = 0
    by_track: dict[str, int] = {}
    company_names: dict[str, str] = {}
    new_ids: list[str] = []
    changed_ids: list[str] = []

    with storage.session() as session:
        existing = {
            row.id: row
            for row in session.scalars(select(JobSnapshot)).all()
        }
        known_companies = {row.id: row for row in session.scalars(select(CompanySnapshot)).all()}

        for job in jobs:
            row, company_id = _job_row(job, now=current)
            by_track[row.track] = by_track.get(row.track, 0) + 1
            company_names[company_id] = job.company

            previous = existing.get(row.id)
            if previous is None:
                session.add(row)
                existing[row.id] = row
                inserted += 1
                new_ids.append(row.id)
                continue

            content_changed = (
                (previous.title or "") != row.title
                or (previous.detail_url or "") != row.detail_url
                or (previous.city or "") != (row.city or "")
                or previous.deadline_date != row.deadline_date
                or (previous.deadline_text or "") != (row.deadline_text or "")
            )
            previous.title = row.title
            previous.city = row.city
            previous.detail_url = row.detail_url
            previous.cohort = row.cohort or previous.cohort
            previous.cohort_status = row.cohort_status or previous.cohort_status
            previous.track = row.track
            # 发布日期不会变，晚到的空值不许覆盖已知值；截止日期会延期，跟源走。
            previous.publish_date = row.publish_date or previous.publish_date
            previous.deadline_date = row.deadline_date
            previous.deadline_text = row.deadline_text
            previous.capture_evidence = {**(previous.capture_evidence or {}), **row.capture_evidence}
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
                        name=name[:250],
                        aliases=[],
                        crawler_key=company_id.split(":", 1)[0],
                        integration_status="connected",
                        source=company_id.split(":", 1)[0],
                        source_ref=company_id[:512],
                        created_at=current,
                        updated_at=current,
                    )
                )
            else:
                snapshot.name = name[:250]
                snapshot.updated_at = current
        session.commit()

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

    return {
        "run_id": run,
        "fetched": inserted + updated + skipped,
        "inserted": inserted,
        "updated": updated,
        "companies_written": len(company_names),
        "skipped_invalid": skipped,
        "by_track": by_track,
        "events": events,
    }


__all__ = [
    "DEFAULT_SOURCE",
    "ExternalJob",
    "ingest_external_jobs",
    "load_jobs_file",
]
