"""Import a seed bundle so a fresh clone starts with real postings.

Companion to ``export_seed.py``. Reads ``data/seed/*.jsonl.gz`` and rebuilds
company/job snapshots. Idempotent: records that already exist are skipped, so
it is safe to run repeatedly and to layer a newer bundle over an older one.

Imported rows are marked in ``capture_evidence`` with ``seed_import: true`` and
the bundle's export timestamp, so a seeded posting is never mistaken for a
fresh crawl observation.

Examples:
    python scripts/import_seed.py
    python scripts/import_seed.py --file data/seed/jobs.jsonl.gz
    python scripts/import_seed.py --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.config import get_settings  # noqa: E402
from packages.storage import CompanySnapshot, JobSnapshot, Storage  # noqa: E402

SEED_DIR = ROOT / "data" / "seed"
SUPPORTED_SCHEMA = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", type=Path, default=None, help="种子文件；默认导入 data/seed 下全部 .jsonl.gz")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def _read_bundle(path: Path) -> tuple[dict, list[dict], list[dict]]:
    meta: dict = {}
    companies: list[dict] = []
    jobs: list[dict] = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = record.get("kind")
            if kind == "meta":
                meta = record
            elif kind == "company":
                companies.append(record)
            elif kind == "job":
                jobs.append(record)
    return meta, companies, jobs


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bundles(target: Path | None) -> list[Path]:
    if target is not None:
        if not target.is_file():
            raise FileNotFoundError(target)
        return [target]
    if not SEED_DIR.is_dir():
        return []
    return sorted(SEED_DIR.glob("*.jsonl.gz"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths = _bundles(args.file)
    except FileNotFoundError as exc:
        print(f"种子文件不存在: {exc}", file=sys.stderr)
        return 2
    if not paths:
        print(
            "没有找到种子文件。data/seed/ 目录为空。\n"
            "自己采集后导出：python scripts/export_seed.py",
            file=sys.stderr,
        )
        return 1

    settings = get_settings()
    storage = Storage.from_url(settings.database_url, initialize=True)
    now = datetime.now(timezone.utc)
    inserted_jobs = existing_jobs = inserted_companies = 0
    by_track: Counter[str] = Counter()
    sources: Counter[str] = Counter()

    with storage.session() as session:
        known_jobs = {row_id for (row_id,) in session.query(JobSnapshot.id).all()}
        known_companies = {row_id for (row_id,) in session.query(CompanySnapshot.id).all()}

        for path in paths:
            meta, companies, jobs = _read_bundle(path)
            schema = meta.get("schema")
            if schema and schema != SUPPORTED_SCHEMA:
                print(f"跳过 {path.name}：schema={schema} 与当前支持的 {SUPPORTED_SCHEMA} 不兼容", file=sys.stderr)
                continue
            exported_at = meta.get("exported_at") or ""

            for company in companies:
                company_id = str(company.get("id") or "").strip()
                if not company_id or company_id in known_companies:
                    continue
                session.add(
                    CompanySnapshot(
                        id=company_id[:255],
                        name=str(company.get("name") or company_id)[:250],
                        aliases=[],
                        crawler_key=str(company.get("crawler_key") or "seed"),
                        integration_status="connected",
                        source="seed",
                        source_ref=company_id[:512],
                        created_at=now,
                        updated_at=now,
                    )
                )
                known_companies.add(company_id)
                inserted_companies += 1

            for job in jobs:
                job_id = str(job.get("id") or "").strip()
                if not job_id:
                    continue
                by_track[str(job.get("track") or "unknown")] += 1
                sources[str(job.get("source") or "unknown")] += 1
                if job_id in known_jobs:
                    existing_jobs += 1
                    continue
                first_seen = _parse_time(job.get("first_seen_at")) or now
                last_seen = _parse_time(job.get("last_seen_at")) or first_seen
                evidence = {
                    "seed_import": True,
                    "seed_exported_at": exported_at,
                    "deadline_text": job.get("deadline_text") or "",
                    "recruit_type": job.get("recruit_type") or "",
                    "industry": job.get("industry") or "",
                }
                session.add(
                    JobSnapshot(
                        id=job_id[:255],
                        company_id=str(job.get("company_id") or "")[:255],
                        title=str(job.get("title") or "（无标题）")[:512],
                        city=(job.get("city") or None),
                        detail_url=str(job.get("detail_url") or "")[:2048],
                        jd_raw=job.get("jd"),
                        cohort=job.get("cohort"),
                        cohort_status=str(job.get("cohort_status") or "unconfirmed")[:64],
                        batch=str(job.get("batch") or "unknown")[:64],
                        track=str(job.get("track") or "unknown")[:32],
                        capture_status="complete",
                        availability_status="active",
                        capture_evidence=evidence,
                        first_seen_at=first_seen,
                        last_seen_at=last_seen,
                        source=str(job.get("source") or "seed")[:128],
                        source_ref=job_id[:512],
                        created_at=now,
                        updated_at=now,
                    )
                )
                known_jobs.add(job_id)
                inserted_jobs += 1

        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    summary = {
        "files": [path.name for path in paths],
        "inserted_jobs": inserted_jobs,
        "existing_jobs": existing_jobs,
        "inserted_companies": inserted_companies,
        "by_track": dict(by_track),
        "by_source": dict(sources),
        "dry_run": args.dry_run,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        action = "将导入" if args.dry_run else "已导入"
        print(f"{action} {inserted_jobs} 个岗位 · {inserted_companies} 家公司"
              f"（已存在跳过 {existing_jobs}）")
        print(f"轨道分布：{dict(by_track)}")
        print(f"来源分布：{dict(sources)}")
        if not args.dry_run:
            print("启动工作台查看：uvicorn apps.api.main:app --port 8010  →  http://127.0.0.1:8010/?view=intelligence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
