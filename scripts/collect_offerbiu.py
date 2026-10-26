"""Collect OfferBiu recruitment announcements into the local database.

OfferBiu aggregates company hiring announcements (秋招 / 春招 / 实习) with
target graduation years, cities, position names, deadlines and apply links.
It is the fastest way to widen coverage beyond the per-company crawlers.

Examples:
    python scripts/collect_offerbiu.py --types 实习 --max-pages 50
    python scripts/collect_offerbiu.py --types 秋招 春招 --industry "IT/互联网/游戏"
    python scripts/collect_offerbiu.py --types 实习 秋招 --all-industries --max-pages 80
    python scripts/collect_offerbiu.py --types 实习 --dry-run
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
from packages.intelligence.offerbiu_source import (  # noqa: E402
    OfferBiuError,
    fetch_offerbiu_postings,
    ingest_offerbiu_postings,
)
from packages.storage import Storage  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--types", nargs="+", default=["实习"],
        help="招聘类型：实习 / 秋招 / 春招（可多选，默认实习）",
    )
    parser.add_argument(
        "--industry", default=None,
        help="行业筛选，例如 'IT/互联网/游戏'；不填表示全部行业",
    )
    parser.add_argument("--season-year", type=int, default=None, help="招聘季年份，例如 2027")
    parser.add_argument("--max-pages", type=int, default=50, help="每种类型最多拉取页数（每页 50 条）")
    parser.add_argument("--delay", type=float, default=0.2, help="每页间隔秒数，礼貌抓取")
    parser.add_argument("--no-events", action="store_true", help="只入库，不写变更事件")
    parser.add_argument("--dry-run", action="store_true", help="只拉取并统计，不写库")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()

    try:
        postings = fetch_offerbiu_postings(
            recruit_types=tuple(args.types),
            industry=args.industry,
            season_year=args.season_year,
            max_pages_per_type=args.max_pages,
            delay_seconds=args.delay,
            logger=lambda message: print(message, file=sys.stderr),
        )
    except OfferBiuError as exc:
        print(f"采集失败: {exc}", file=sys.stderr)
        return 2

    print(f"拉取到 {len(postings)} 条公告", file=sys.stderr)
    if args.dry_run:
        sample = [
            {
                "company": item.get("companyName"),
                "type": item.get("recruitType"),
                "years": item.get("targetYears"),
                "positions": str(item.get("positionsText"))[:60],
            }
            for item in postings[:10]
        ]
        print(json.dumps(sample, ensure_ascii=False, indent=2))
        return 0

    storage = Storage.from_url(settings.database_url, initialize=True)
    result = ingest_offerbiu_postings(
        storage, postings, record_events=not args.no_events
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"入库完成：新增 {result.inserted} · 更新 {result.updated} "
            f"· 公司 {result.companies_written} · 跳过 {result.skipped_invalid}"
        )
        print(f"轨道分布：{dict(result.by_track)}")
        print(f"类型分布：{dict(result.by_recruit_type)}")
        if result.events:
            summary = result.events.to_dict()
            baseline = summary.get("baseline_companies") or []
            if len(baseline) > 5:
                summary["baseline_companies"] = baseline[:5] + [f"...共 {len(baseline)} 家"]
            print(f"事件：{summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
