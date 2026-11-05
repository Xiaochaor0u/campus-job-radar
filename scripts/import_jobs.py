"""把 OfferBiu 以外抓到的岗位导进站点。

为什么需要它：OfferBiu 只是一个聚合站，公司官网、高校就业网、集团招聘页
上的岗位它不一定收。这个脚本是第二条通道——任何能整理成结构化数据的来源
（官网公告、别家聚合站、Excel 导出）都能写进同一张表，并带上来源标记。

必须带 URL：没有投递/公告链接的岗位一律拒绝，宁可少一条也不存一条查不到
出处的记录。

用法：
    python scripts/import_jobs.py --file .data/incoming/zz.json --dry-run
    python scripts/import_jobs.py --file .data/incoming/zz.json
    python scripts/import_jobs.py --file jobs.csv --source company-site

文件格式（JSON）：
    {
      "source": "company-site",
      "items": [
        {
          "company": "郑州商品交易所",
          "title": "软件研发岗",
          "city": "郑州",
          "url": "https://www.czce.com.cn/...",
          "published_at": "2026-11-01",
          "deadline": "2026-11-20",
          "track": "campus",
          "cohort": 2027,
          "positions": ["软件研发", "网络安全"]
        }
      ]
    }

CSV 用同样的列名，一列一个字段，positions 用逗号分隔。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.config import get_settings  # noqa: E402
from packages.intelligence.external_import import (  # noqa: E402
    DEFAULT_SOURCE,
    ingest_external_jobs,
    load_jobs_file,
)
from packages.storage import Storage  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", required=True, help="JSON 或 CSV 文件路径")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="来源标记，默认 web")
    parser.add_argument("--dry-run", action="store_true", help="只校验和预览，不写库")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--limit-print", type=int, default=30, help="预览条数")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        jobs = load_jobs_file(args.file, source=args.source)
    except (OSError, ValueError) as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2

    if not jobs:
        print("文件里没有可用岗位（每条都需要 company / title / url）。", file=sys.stderr)
        return 2

    by_source: dict[str, int] = {}
    for job in jobs:
        by_source[job.source] = by_source.get(job.source, 0) + 1

    if args.dry_run:
        payload = {
            "dry_run": True,
            "total": len(jobs),
            "by_source": by_source,
            "sample": [
                {
                    "company": job.company,
                    "title": job.title,
                    "city": job.city,
                    "published": job.published_at.isoformat() if job.published_at else "",
                    "deadline": job.deadline_date.isoformat() if job.deadline_date else job.deadline_text,
                    "url": job.url,
                }
                for job in jobs[: args.limit_print]
            ],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(f"共 {len(jobs)} 条，来源 {by_source}（预演，没有写库）：")
        for row in payload["sample"]:
            print(
                f"  {row['published'] or '日期未公布':<12} "
                f"{row['company']:<18} {row['title'][:28]:<28} "
                f"截止 {row['deadline'] or '未公布'}"
            )
        return 0

    settings = get_settings()
    storage = Storage.from_url(settings.database_url, initialize=True)
    result = ingest_external_jobs(storage, jobs)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(
        f"入库：新增 {result['inserted']} · 更新 {result['updated']} "
        f"· 公司 {result['companies_written']} · 来源 {by_source}"
    )
    print("打开 http://127.0.0.1:8010/?view=intelligence 刷新即可看到。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
