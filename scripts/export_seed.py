"""Export the local job database into a portable seed file.

The point is distribution: commit the exported file to the repository so anyone
who clones the project starts with real postings instead of an empty database.

Format is JSONL, gzipped — one record per line, three record kinds:

    {"kind": "meta",    "schema": 1, "exported_at": ..., "counts": {...}}
    {"kind": "company", "id": ..., "name": ...}
    {"kind": "job",     "id": ..., "company_id": ..., "title": ... }

Design choices:
  * JD bodies are **omitted by default** (`--with-jd` to include). Job
    descriptions are third-party text; shipping a curated catalog of titles,
    companies, cities, links and deadlines keeps the bundle redistributable and
    small. Import still works without them.
  * Only active rows are exported by default, so retired postings don't
    resurrect in someone else's database.

Examples:
    python scripts/export_seed.py
    python scripts/export_seed.py --out data/seed/jobs.jsonl.gz --with-jd
    python scripts/export_seed.py --source offerbiu --limit 3000
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

SEED_SCHEMA = 1
DEFAULT_OUTPUT = ROOT / "data" / "seed" / "jobs.jsonl.gz"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="输出路径（.jsonl.gz）")
    parser.add_argument("--source", default=None, help="只导出某个来源，例如 offerbiu")
    parser.add_argument("--track", default=None, choices=["campus", "internship"], help="只导出某个轨道")
    parser.add_argument("--limit", type=int, default=0, help="最多导出多少条（0 = 全部）")
    parser.add_argument("--with-jd", action="store_true", help="包含 JD 正文（体积会大很多）")
    parser.add_argument(
        "--include-inactive", action="store_true", help="同时导出已下架岗位"
    )
    parser.add_argument("--json", action="store_true", help="打印统计 JSON")
    return parser


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    storage = Storage.from_url(settings.database_url, initialize=True)

    with storage.session() as session:
        query = session.query(JobSnapshot)
        if not args.include_inactive:
            query = query.filter(JobSnapshot.availability_status == "active")
        if args.source:
            query = query.filter(JobSnapshot.source == args.source)
        if args.track:
            query = query.filter(JobSnapshot.track == args.track)
        query = query.order_by(JobSnapshot.first_seen_at.desc(), JobSnapshot.id.asc())
        if args.limit:
            query = query.limit(args.limit)
        rows = query.all()
        company_names = {
            row.id: row.name for row in session.query(CompanySnapshot).all()
        }

    if not rows:
        print("数据库里没有可导出的岗位。先跑一次采集：", file=sys.stderr)
        print("  python scripts/collect_offerbiu.py --types 实习 秋招", file=sys.stderr)
        return 1

    company_ids = sorted({row.company_id for row in rows})
    by_track = Counter(row.track for row in rows)
    by_source = Counter(row.source for row in rows)
    exported_at = datetime.now(timezone.utc).isoformat()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "kind": "meta",
            "schema": SEED_SCHEMA,
            "exported_at": exported_at,
            "job_count": len(rows),
            "company_count": len(company_ids),
            "by_track": dict(by_track),
            "by_source": dict(by_source),
            "with_jd": bool(args.with_jd),
            "note": "岗位元数据快照，用于本地演示；JD 正文默认不含，实际投递请以官网为准。",
        }, ensure_ascii=False) + "\n")

        for company_id in company_ids:
            handle.write(json.dumps({
                "kind": "company",
                "id": company_id,
                "name": company_names.get(company_id) or company_id.replace("offerbiu:", ""),
            }, ensure_ascii=False) + "\n")

        for row in rows:
            evidence = row.capture_evidence or {}
            record = {
                "kind": "job",
                "id": row.id,
                "company_id": row.company_id,
                "title": row.title,
                "city": row.city,
                "detail_url": row.detail_url,
                "batch": row.batch,
                "track": row.track,
                "cohort": row.cohort,
                "cohort_status": row.cohort_status,
                "source": row.source,
                "first_seen_at": _iso(row.first_seen_at),
                "last_seen_at": _iso(row.last_seen_at),
                "deadline_text": evidence.get("deadline_text") or "",
                "recruit_type": evidence.get("recruit_type") or "",
                "industry": evidence.get("industry") or "",
            }
            if args.with_jd and row.jd_raw:
                record["jd"] = row.jd_raw
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    size_kb = args.out.stat().st_size / 1024
    summary = {
        "out": str(args.out),
        "jobs": len(rows),
        "companies": len(company_ids),
        "by_track": dict(by_track),
        "by_source": dict(by_source),
        "with_jd": bool(args.with_jd),
        "size_kb": round(size_kb, 1),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"已导出 {len(rows)} 个岗位 · {len(company_ids)} 家公司 -> {args.out}")
        print(f"轨道：{dict(by_track)}  来源：{dict(by_source)}")
        print(f"体积：{size_kb:.0f} KB（含 JD: {'是' if args.with_jd else '否'}）")
        print("把该文件提交到仓库后，别人拉取项目执行 python scripts/import_seed.py 即可自带这些岗位。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
