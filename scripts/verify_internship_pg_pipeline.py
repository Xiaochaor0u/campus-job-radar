"""Verify the internship business chain on a disposable PostgreSQL 16 instance.

This is NOT a SQLite test with a PostgreSQL-shaped name.  The script:

1. starts a throwaway ``pgvector/pgvector:pg16`` container with no named volume;
2. proves the target really is PostgreSQL (``version()``) and the database is empty;
3. applies migrations 001-027 and asserts the internship columns are JSONB;
4. replays the **recorded real Tencent internship payloads** through the real
   ``run_daily_pipeline`` against that PostgreSQL, then reads back through the
   production repository;
5. verifies idempotency (second run adds nothing) and campus/internship query
   isolation;
6. destroys the container.

No user database, volume, or configuration is read or modified.

Usage:
    python scripts/verify_internship_pg_pipeline.py [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from packages.matching.policy import ScreeningMode, ScreeningPolicy  # noqa: E402
from packages.pipeline import CrawlResult, run_daily_pipeline  # noqa: E402
from packages.recruitment_core.crawlers.tencent import TencentCrawler  # noqa: E402
from packages.recruitment_core.crawlers.tencent_intern import (  # noqa: E402
    TencentInternshipCrawler,
)
from packages.repositories.postgres import PostgresRecruitmentRepository  # noqa: E402
from packages.storage import Storage  # noqa: E402
from sqlalchemy import select  # noqa: E402

from packages.storage.models import JobSnapshot  # noqa: E402
from scripts.apply_migrations import apply_migrations  # noqa: E402

DEFAULT_IMAGE = "pgvector/pgvector:pg16"
SAMPLES = PROJECT_ROOT / "tests" / "fixtures" / "tencent_intern_sample.json"
EVIDENCE = PROJECT_ROOT / "docs" / "internops" / "evidence"
PROFILE: dict[str, Any] = {
    "degree": "本科",
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["西安", "郑州"],
        "available_months": {"summer": 2, "winter": 1.5},
        "max_days_per_week": 3,
    },
}


class CheckFailed(RuntimeError):
    pass


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise CheckFailed(f"command failed: {' '.join(command)}\n{result.stderr.strip()}")
    return result


def _start(image: str, name: str) -> str:
    _run([
        "docker", "run", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_USER=recruitops", "-e", "POSTGRES_PASSWORD=recruitops",
        "-e", "POSTGRES_DB=recruitops", "-p", "127.0.0.1::5432", image,
    ])
    output = _run(["docker", "port", name, "5432"]).stdout.strip()
    port = output.rsplit(":", 1)[-1].strip()
    if not port.isdigit():
        raise CheckFailed(f"could not determine mapped port from {output!r}")
    return port


def _wait(url: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - readiness probe
            last = str(exc)
            time.sleep(1.5)
        finally:
            engine.dispose()
    raise CheckFailed(f"postgres did not become ready: {last}")


def _recorded_crawler(samples: dict[str, Any], limit: int) -> Any:
    """Serve the recorded real payloads through the production adapter."""

    crawler = TencentInternshipCrawler("腾讯", max_jobs=limit)
    rows = [item["list_row"] for item in samples["details"]]
    details = {item["post_id"]: item["detail"] for item in samples["details"]}

    class _Response:
        status_code = 200

        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def json(self) -> dict[str, Any]:
            return self._payload

    def fake_request(method: str, url: str, **kwargs: Any) -> Any:
        if url.endswith("/searchPosition"):
            return _Response({"status": 0, "data": {"count": len(rows), "positionList": rows}})
        post_id = str((kwargs.get("params") or {}).get("postId"))
        return _Response({"status": 0, "data": details[post_id]})

    crawler._request = fake_request  # type: ignore[method-assign]

    def run(company: Any) -> CrawlResult:
        jobs = crawler.fetch()
        return CrawlResult(
            jobs=jobs,
            source_url=TencentInternshipCrawler.ORIGIN,
            allowed_origins=[TencentInternshipCrawler.ORIGIN],
            pages_seen=1,
            total_pages=1,
            has_more=False,
            crawler_key=TencentInternshipCrawler.CRAWLER_KEY,
            pagination_complete=True,
            completeness_known=True,
            advertised_total=len(jobs),
        )

    return run


def _companies(directory: Path) -> Path:
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


def verify(url: str, samples: dict[str, Any], report: list[tuple[bool, str]]) -> dict[str, Any]:
    def check(ok: bool, label: str, detail: str = "") -> None:
        report.append((ok, f"{label}{(' — ' + detail) if detail and not ok else ''}"))

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version()")).scalar_one()
            tables = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
            ).scalar_one()
        check("PostgreSQL" in version, "target is PostgreSQL", version[:60])
        check(version.split()[1].startswith("16"), "major version is 16", version[:40])
        check(tables == 0, "database starts empty", f"{tables} tables")
    finally:
        engine.dispose()

    applied = apply_migrations(url)
    check(len(applied.applied) == 27, "migrations 001-027 applied", str(len(applied.applied)))

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            types = dict(
                connection.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='job_snapshots' "
                        "AND column_name IN ('internship_seasons',"
                        " 'internship_requirements', 'match_conditions')"
                    )
                ).all()
            )
        check(set(types.values()) == {"jsonb"}, "internship columns are JSONB", str(types))
    finally:
        engine.dispose()

    storage = Storage.from_url(url)
    workspace = Path(tempfile.mkdtemp(prefix="internops-pg-"))
    try:
        config = _companies(workspace)
        crawler = _recorded_crawler(samples, limit=len(samples["details"]))
        policy = ScreeningPolicy(mode=ScreeningMode.INTERNSHIP)
        first = run_daily_pipeline(
            companies_path=config, storage=storage, crawler=crawler, profile=PROFILE,
            screening_policy=policy, max_concurrency=1, detail_max_concurrency=1,
            match_max_concurrency=1,
        )
        with storage.session() as session:
            rows = list(session.scalars(select(JobSnapshot)))
        check(len(rows) >= 1, "internship rows written", str(len(rows)))
        check(all(row.track == "internship" for row in rows), "all rows are internships")
        check(
            all(row.jd_raw and "任职要求" in row.jd_raw for row in rows),
            "JD stored with requirement section",
        )
        check(
            all(row.match_conditions for row in rows),
            "condition evaluation stored as JSONB",
        )

        repo = PostgresRecruitmentRepository(storage)
        internship_page = repo.browse_jobs(track="internship")
        campus_page = repo.browse_jobs(track="campus")
        check(internship_page.total == len(rows), "internship query returns the rows",
              f"{internship_page.total}")
        check(campus_page.total == 0, "campus query is isolated", str(campus_page.total))

        second = run_daily_pipeline(
            companies_path=config, storage=storage, crawler=crawler, profile=PROFILE,
            screening_policy=policy, max_concurrency=1, detail_max_concurrency=1,
            match_max_concurrency=1,
        )
        with storage.session() as session:
            after = list(session.scalars(select(JobSnapshot)))
        check(len(after) == len(rows), "idempotent re-run keeps row count",
              f"{len(rows)} -> {len(after)}")
        check(second.new_count == 0, "re-run inserts nothing", str(second.new_count))

        detail = repo.get_job(rows[0].id)
        check(detail is not None and detail.job.track == "internship",
              "repository detail exposes the track")
        return {
            "rows": len(rows),
            "ids": [row.id for row in rows],
            "qualification": sorted(
                {
                    (row.match_conditions or {}).get("qualification") or "unknown"
                    for row in rows
                }
            ),
            "first_run_new": first.new_count,
            "second_run_new": second.new_count,
            "internship_query_total": internship_page.total,
            "campus_query_total": campus_page.total,
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    name = f"recruitops-pg-pipeline-{os.getpid()}"
    port = _start(args.image, name)
    url = f"postgresql+psycopg://recruitops:recruitops@127.0.0.1:{port}/recruitops"
    report: list[tuple[bool, str]] = []
    summary: dict[str, Any] = {}
    try:
        _wait(url)
        summary = verify(url, samples, report)
    except CheckFailed as exc:
        report.append((False, f"aborted: {exc}"))
    finally:
        if args.keep:
            print(f"container kept: {name} (port {port})")
        else:
            _run(["docker", "rm", "-f", name], check=False)

    print("== PostgreSQL business-chain verification ==")
    for ok, label in report:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    failed = sum(1 for ok, _ in report if not ok)
    print(f"\nRESULT: {'PASSED' if failed == 0 else 'FAILED'} ({len(report) - failed}/{len(report)})")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = EVIDENCE / f"task-008-pg-pipeline-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "database": "PostgreSQL 16 (disposable container, no volume)",
                "image": args.image,
                "sample_source": str(SAMPLES.relative_to(PROJECT_ROOT)),
                "checks": [{"passed": ok, "label": label} for ok, label in report],
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"evidence: {path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
