"""Collect real Tencent internships and push them through the InternOps pipeline.

This is the TASK-005 acceptance driver.  It uses the public read-only Tencent API
through ``TencentInternshipCrawler``, runs the **existing** ``run_daily_pipeline``
in internship mode against an isolated SQLite database, then reads the result back
through the production repository.

It never touches the configured application database unless ``--db`` points at it
explicitly, and it prints/shuffles nothing that would look like fabricated data.

Usage:
    python scripts/collect_tencent_internships.py --limit 40
    python scripts/collect_tencent_internships.py --limit 40 --db C:/tmp/iop.sqlite
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from packages.matching.policy import ScreeningMode, ScreeningPolicy  # noqa: E402
from packages.pipeline import CrawlResult, run_daily_pipeline  # noqa: E402
from packages.recruitment_core.crawlers.tencent_intern import (  # noqa: E402
    TencentInternshipCrawler,
)
from packages.repositories.postgres import PostgresRecruitmentRepository  # noqa: E402
from packages.storage import Storage  # noqa: E402
from packages.storage.models import JobAnalysisSnapshot, JobSnapshot  # noqa: E402

EVIDENCE_DIR = PROJECT_ROOT / "docs" / "internops" / "evidence"
CANDIDATE_PROFILE: dict[str, Any] = {
    "degree": "本科",
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["西安", "郑州"],
        "available_months": {"summer": 2, "winter": 1.5},
        "max_days_per_week": 3,
    },
}


def _companies_file(directory: Path) -> Path:
    path = directory / "companies.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "companies": [
                    {
                        "id": "tencent",
                        "name": "腾讯",
                        "careers_url": TencentInternshipCrawler.ORIGIN,
                        "crawler": TencentInternshipCrawler.CRAWLER_KEY,
                        "integration_status": "connected",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def _crawler(
    limit: int, *, replay: bool = False
) -> tuple[Any, TencentInternshipCrawler]:
    # max_jobs caps how many detail requests are issued, so the run stays polite
    # and a small --limit really means a small number of network calls.
    crawler = TencentInternshipCrawler(
        "腾讯", TencentInternshipCrawler.ORIGIN, max_jobs=limit or None
    )
    if replay:
        # Deterministic replay of the recorded real payloads. Used for UI/e2e runs
        # so a live site changing its postings cannot make a test flaky.
        samples = json.loads(
            (PROJECT_ROOT / "tests" / "fixtures" / "tencent_intern_sample.json").read_text(
                encoding="utf-8"
            )
        )
        rows = [item["list_row"] for item in samples["details"]]
        details = {item["post_id"]: item["detail"] for item in samples["details"]}

        class _Response:
            status_code = 200

            def __init__(self, payload: dict[str, Any]) -> None:
                self._payload = payload

            def json(self) -> dict[str, Any]:
                return self._payload

        def replay_request(method: str, url: str, **kwargs: Any) -> Any:
            if url.endswith("/searchPosition"):
                return _Response(
                    {"status": 0, "data": {"count": len(rows), "positionList": rows}}
                )
            post_id = str((kwargs.get("params") or {}).get("postId"))
            return _Response({"status": 0, "data": details[post_id]})

        crawler._request = replay_request  # type: ignore[method-assign]

    def run(company: Any) -> CrawlResult:
        jobs = crawler.fetch()
        if limit and len(jobs) > limit:
            jobs = jobs[:limit]
        # A truncated sample cannot claim the full advertised total. Saying so is
        # what keeps the pipeline's completeness audit honest instead of reporting
        # a false "pagination incomplete".
        truncated = bool(
            crawler.max_jobs
            and crawler.advertised_total is not None
            and crawler.advertised_total > crawler.max_jobs
        )
        return CrawlResult(
            jobs=jobs,
            source_url=TencentInternshipCrawler.ORIGIN,
            allowed_origins=[TencentInternshipCrawler.ORIGIN],
            pages_seen=crawler.pages_seen,
            total_pages=crawler.pages_seen,
            has_more=False,
            crawler_key=TencentInternshipCrawler.CRAWLER_KEY,
            pagination_complete=None if truncated else True,
            completeness_known=False if truncated else True,
            advertised_total=None if truncated else crawler.advertised_total,
            crawl_source_url=crawler.ORIGIN,
            termination_reasons=("sample_truncated" if truncated else "api_total_reached",),
        )

    return run, crawler


def _snapshot(storage: Storage) -> dict[str, Any]:
    with storage.session() as session:
        rows = list(session.scalars(select(JobSnapshot)))
        analyses = {
            row.job_id: row.analysis_status
            for row in session.scalars(select(JobAnalysisSnapshot))
        }
    tracks: dict[str, int] = {}
    qualification: dict[str, int] = {}
    jd_complete = 0
    with_requirement = 0
    for row in rows:
        tracks[row.track] = tracks.get(row.track, 0) + 1
        verdict = (row.match_conditions or {}).get("qualification", "missing")
        qualification[verdict] = qualification.get(verdict, 0) + 1
        jd = row.jd_raw or ""
        if "任职要求" in jd:
            with_requirement += 1
        if row.capture_status == "complete" and "岗位职责" in jd and "任职要求" in jd:
            jd_complete += 1
    return {
        "rows": len(rows),
        "tracks": tracks,
        "qualification": qualification,
        "jd_complete_rows": jd_complete,
        "rows_with_requirement_section": with_requirement,
        "analysis_status": {
            state: sum(1 for value in analyses.values() if value == state)
            for state in sorted(set(analyses.values()))
        },
    }


def _samples(storage: Storage, limit: int = 5) -> list[dict[str, Any]]:
    with storage.session() as session:
        rows = list(
            session.scalars(
                select(JobSnapshot).where(JobSnapshot.track == "internship").limit(limit)
            )
        )
    return [
        {
            "id": row.id,
            "title": row.title,
            "city": row.city,
            "job_type": row.batch,
            "seasons": row.internship_seasons,
            "conversion": row.conversion_opportunity,
            "qualification": (row.match_conditions or {}).get("qualification"),
            "jd_chars": len(row.jd_raw or ""),
            "detail_url": row.detail_url,
        }
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=40, help="max postings to persist")
    parser.add_argument("--db", type=Path, default=None, help="isolated sqlite path")
    parser.add_argument("--skip-second-run", action="store_true")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="replay the recorded real samples instead of calling the live API",
    )
    args = parser.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db_path = args.db or Path(tempfile.gettempdir()) / f"internops-task005-{stamp}.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        print(f"refusing to reuse an existing database: {db_path}", file=sys.stderr)
        return 2

    storage = Storage.from_url(f"sqlite:///{db_path.as_posix()}")
    storage.initialize()
    workspace = Path(tempfile.mkdtemp(prefix="internops-task005-"))
    config = _companies_file(workspace)
    policy = ScreeningPolicy(mode=ScreeningMode.INTERNSHIP)
    crawler_fn, crawler = _crawler(args.limit, replay=args.replay)

    started = datetime.now(timezone.utc)
    print(f"collecting from {TencentInternshipCrawler.ORIGIN} …")
    first = run_daily_pipeline(
        companies_path=config,
        storage=storage,
        crawler=crawler_fn,
        profile=CANDIDATE_PROFILE,
        screening_policy=policy,
        max_concurrency=1,
        detail_max_concurrency=1,
        match_max_concurrency=1,
    )
    metrics = crawler.metrics()
    after_first = _snapshot(storage)

    second = None
    after_second = None
    if not args.skip_second_run:
        print("re-running to verify duplicate handling …")
        second = run_daily_pipeline(
            companies_path=config,
            storage=storage,
            crawler=crawler_fn,
            profile=CANDIDATE_PROFILE,
            screening_policy=policy,
            max_concurrency=1,
            detail_max_concurrency=1,
            match_max_concurrency=1,
        )
        after_second = _snapshot(storage)

    repo = PostgresRecruitmentRepository(storage)
    queries = {}
    for track in ("internship", "campus", "all"):
        page = repo.browse_jobs(track=track, limit=100)
        queries[track] = {"count": page.total, "ids_sample": [i.id for i in page.items[:3]]}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started.isoformat(),
        "source": {
            "name": "腾讯招聘（join.qq.com）",
            "origin": TencentInternshipCrawler.ORIGIN,
            "list_api": TencentInternshipCrawler.LIST_API,
            "detail_api": TencentInternshipCrawler.DETAIL_API,
            "project_mapping_ids": list(TencentInternshipCrawler.INTERNSHIP_MAPPING_IDS),
            "access": "public read-only JSON API，无登录/无验证码",
        },
        "database": str(db_path),
        "crawler_metrics": metrics,
        "first_run": {
            "new": first.new_count,
            "rejected": len(first.rejected_job_ids),
            "crawl_failures": first.failed_count,
            "rejection_reasons": dict(first.rejection_reasons),
        },
        "after_first_run": after_first,
        "second_run": (
            None
            if second is None
            else {
                "new": second.new_count,
                "rejected": len(second.rejected_job_ids),
                "crawl_failures": second.failed_count,
            }
        ),
        "after_second_run": after_second,
        "queries": queries,
        "samples": _samples(storage),
        "limit_applied": args.limit,
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    evidence_path = EVIDENCE_DIR / f"task-005-tencent-{stamp}.json"
    evidence_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nevidence written: {evidence_path}")
    print(f"isolated database: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
