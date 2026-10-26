"""Persist pipeline classifications into the ``job_events`` change feed.

Called after every scheduled crawl.  Input is the serialized
``DailyPipelineResult`` mapping (the ``payload["pipeline"]`` object the
scheduler already produces), so no crawler, matcher or storage write is
duplicated here.  The writer only appends event rows; it never mutates
``job_snapshots``.

Removed events come from the offline reconciliation that the daily sync runs
before this writer: any job whose row was marked inactive inside the run
window gets one ``removed`` event.  Jobs that were already removed before
this run (no event-worthy transition) are skipped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from packages.storage import JobEvent, JobSnapshot, Storage

BASELINE_MARKER_PREFIX = "__baseline__"
BASELINE_TITLE = "baseline"
_INACTIVE_PREFIX = "recruitops-offline:v1:inactive:"


def baseline_marker_job_id(company_id: str) -> str:
    """Per-company marker id.

    The ``(run_id, job_id, event_type)`` unique constraint would otherwise
    collapse several companies' baseline rows from one run into a single row,
    silently leaving the other companies without a baseline.
    """

    return f"{BASELINE_MARKER_PREFIX}:{company_id}"[:255]


@dataclass(frozen=True, slots=True)
class EventWriteResult:
    """Stable summary of one event write."""

    run_id: str
    written: bool
    baseline_companies: tuple[str, ...] = ()
    new_event_count: int = 0
    updated_event_count: int = 0
    removed_event_count: int = 0
    baseline_skipped_new_count: int = 0
    missing_job_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "written": self.written,
            "baseline_companies": list(self.baseline_companies),
            "new_events": self.new_event_count,
            "updated_events": self.updated_event_count,
            "removed_events": self.removed_event_count,
            "baseline_skipped_new": self.baseline_skipped_new_count,
            "missing_job_ids": list(self.missing_job_ids),
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _job_fingerprint(source_ref: Any) -> str | None:
    """Extract the trailing ``:fp:<hash>`` marker the pipeline stores."""

    text = str(source_ref or "")
    marker = ":fp:"
    index = text.rfind(marker)
    if index < 0:
        return None
    fingerprint = text[index + len(marker):]
    return fingerprint if len(fingerprint) == 64 else None


def _company_names(pipeline_payload: Mapping[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in pipeline_payload.get("companies") or ():
        if not isinstance(row, Mapping):
            continue
        company_id = str(row.get("company_id") or "").strip()
        name = str(row.get("company_name") or "").strip()
        if company_id and name:
            names.setdefault(company_id, name)
    return names


def _string_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item or "").strip())


def _new_event_row(
    *,
    event_type: str,
    job: JobSnapshot | Mapping[str, Any],
    company_name: str,
    run_id: str,
    detected_at: datetime,
) -> JobEvent:
    """Build one JobEvent from a snapshot row or an explicit payload."""

    def field(name: str, default: Any = "") -> Any:
        if isinstance(job, Mapping):
            return job.get(name, default)
        return getattr(job, name, default)

    return JobEvent(
        id=uuid4().hex,
        event_type=event_type,
        job_id=str(field("id") or ""),
        company_id=str(field("company_id") or ""),
        company_name=company_name or str(field("company_id") or ""),
        title=str(field("title") or ""),
        city=field("city"),
        batch=str(field("batch") or "unknown"),
        track=str(field("track") or "unknown"),
        detail_url=str(field("detail_url") or ""),
        content_fingerprint=_job_fingerprint(field("source_ref")),
        detected_at=detected_at,
        run_id=run_id,
    )


def record_pipeline_events(
    storage: Storage,
    pipeline_payload: Mapping[str, Any] | Any,
    *,
    run_id: str,
    run_started_at: datetime | None = None,
    clock: Any = None,
) -> EventWriteResult:
    """Append new/updated/removed events for one finished pipeline run.

    ``pipeline_payload`` may be a ``DailyPipelineResult`` instance or its
    serialized mapping; both expose ``new_job_ids`` and ``changed_job_ids``.
    The call is idempotent per ``run_id``: re-running with the same id skips
    rows that already exist.
    """

    if not isinstance(storage, Storage):
        raise TypeError("storage must be a Storage instance")
    run_id = str(run_id or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    # Idempotent create_all: SQLite deployments have no migration runner, so the
    # first event write must be able to materialise its own table.
    storage.initialize()

    converter = getattr(pipeline_payload, "to_dict", None)
    payload = converter() if callable(converter) else pipeline_payload
    if not isinstance(payload, Mapping):
        raise TypeError("pipeline_payload must be a mapping or provide to_dict()")

    now = _utcnow() if clock is None else _as_utc(clock() if callable(clock) else clock)
    window_start = _as_utc(run_started_at)
    names = _company_names(payload)
    new_job_ids = _string_ids(payload.get("new_job_ids"))
    changed_job_ids = _string_ids(payload.get("changed_job_ids"))

    with storage.session() as session:
        already_recorded = (
            session.scalars(
                select(JobEvent.id).where(JobEvent.run_id == run_id).limit(1)
            ).first()
            is not None
        )

        baseline_rows: list[JobEvent] = []
        event_rows: list[JobEvent] = []
        baseline_companies: list[str] = []
        baseline_skipped: dict[str, int] = {}
        missing: list[str] = []

        # --- baseline detection per company ---------------------------------
        crawled_company_ids = sorted(names.keys())
        companies_with_baseline: set[str] = set()
        for company_id in crawled_company_ids:
            marker = session.scalars(
                select(JobEvent.id)
                .where(
                    JobEvent.company_id == company_id,
                    JobEvent.event_type == "baseline",
                )
                .limit(1)
            ).first()
            if marker:
                companies_with_baseline.add(company_id)

        # --- new / updated events -------------------------------------------
        if new_job_ids or changed_job_ids:
            snapshot_rows = {
                row.id: row
                for row in session.scalars(
                    select(JobSnapshot).where(
                        JobSnapshot.id.in_([*new_job_ids, *changed_job_ids])
                    )
                ).all()
            }

            def job_company(job_id: str) -> str:
                row = snapshot_rows.get(job_id)
                if row is None:
                    return ""
                return str(row.company_id or "")

            def company_name_of(job_id: str) -> str:
                company_id = job_company(job_id)
                return names.get(company_id) or company_id

            for job_id in new_job_ids:
                row = snapshot_rows.get(job_id)
                if row is None:
                    missing.append(job_id)
                    continue
                company_id = str(row.company_id or "")
                if company_id not in companies_with_baseline:
                    # First complete crawl: record the baseline marker once and
                    # swallow this "new" batch; it is a catalog import, not news.
                    baseline_skipped[company_id] = baseline_skipped.get(company_id, 0) + 1
                    if company_id not in baseline_companies:
                        baseline_companies.append(company_id)
                        baseline_rows.append(
                            JobEvent(
                                id=uuid4().hex,
                                event_type="baseline",
                                job_id=baseline_marker_job_id(company_id),
                                company_id=company_id,
                                company_name=names.get(company_id) or company_id,
                                title=BASELINE_TITLE,
                                batch="unknown",
                                track="unknown",
                                detail_url="",
                                detected_at=now,
                                run_id=run_id,
                            )
                        )
                    continue
                event_rows.append(
                    _new_event_row(
                        event_type="new",
                        job=row,
                        company_name=company_name_of(job_id),
                        run_id=run_id,
                        detected_at=now,
                    )
                )

            for job_id in changed_job_ids:
                row = snapshot_rows.get(job_id)
                if row is None:
                    if job_id not in missing:
                        missing.append(job_id)
                    continue
                company_id = str(row.company_id or "")
                if company_id and company_id not in companies_with_baseline:
                    # Pre-existing catalog rows changed on the very first event
                    # run (for example an upgrade deployment): treat the company
                    # as baselined too, then record the update as real news.
                    if company_id not in baseline_companies:
                        baseline_companies.append(company_id)
                        baseline_rows.append(
                            JobEvent(
                                id=uuid4().hex,
                                event_type="baseline",
                                job_id=baseline_marker_job_id(company_id),
                                company_id=company_id,
                                company_name=names.get(company_id) or company_id,
                                title=BASELINE_TITLE,
                                batch="unknown",
                                track="unknown",
                                detail_url="",
                                detected_at=now,
                                run_id=run_id,
                            )
                        )
                        companies_with_baseline.add(company_id)
                event_rows.append(
                    _new_event_row(
                        event_type="updated",
                        job=row,
                        company_name=company_name_of(job_id),
                        run_id=run_id,
                        detected_at=now,
                    )
                )

        # --- removed events (offline reconciliation output) ------------------
        removed_rows = list(
            session.scalars(
                select(JobSnapshot).where(
                    (JobSnapshot.availability_status == "inactive")
                    | JobSnapshot.source_ref.like(f"{_INACTIVE_PREFIX}%"),
                    JobSnapshot.updated_at >= window_start,
                    JobSnapshot.updated_at <= now,
                )
            ).all()
        )
        for row in removed_rows:
            company_id = str(row.company_id or "")
            if company_id and company_id not in companies_with_baseline:
                # Never baselined companies cannot lose jobs meaningfully.
                continue
            last_event_type = session.scalars(
                select(JobEvent.event_type)
                .where(JobEvent.job_id == row.id)
                .order_by(JobEvent.detected_at.desc(), JobEvent.created_at.desc())
                .limit(1)
            ).first()
            if last_event_type == "removed":
                continue
            event_rows.append(
                _new_event_row(
                    event_type="removed",
                    job=row,
                    company_name=names.get(company_id) or company_id,
                    run_id=run_id,
                    detected_at=now,
                )
            )

        # --- persist ----------------------------------------------------------
        persisted: list[JobEvent] = []
        if not already_recorded:
            seen_keys: set[tuple[str, str, str]] = set()
            for row in (*baseline_rows, *event_rows):
                key = (run_id, row.job_id, row.event_type)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                exists = session.scalars(
                    select(JobEvent.id)
                    .where(
                        JobEvent.run_id == run_id,
                        JobEvent.job_id == row.job_id,
                        JobEvent.event_type == row.event_type,
                    )
                    .limit(1)
                ).first()
                if exists:
                    continue
                session.add(row)
                persisted.append(row)
            session.commit()

    result = EventWriteResult(
        run_id=run_id,
        written=bool(persisted),
        baseline_companies=tuple(baseline_companies),
        new_event_count=sum(1 for row in persisted if row.event_type == "new"),
        updated_event_count=sum(1 for row in persisted if row.event_type == "updated"),
        removed_event_count=sum(1 for row in persisted if row.event_type == "removed"),
        baseline_skipped_new_count=sum(baseline_skipped.values()),
        missing_job_ids=tuple(missing),
    )
    return result
