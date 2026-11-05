"""直接抓企业自己的招聘系统，把岗位导进站点。

OfferBiu 是公告聚合站，公司官网更新了它不一定收；这条通道直接读企业招聘
系统（北森 / hotjob / Moka / 飞书），拿到的是**官网口径**的岗位和投递链接。

支持的平台（与 config/companies.yaml 的 crawler 字段一致）：
    beisen   *.zhiye.com
    hotjob   *.hotjob.cn
    moka     app.mokahr.com/...
    feishu   jobs.*.feishu.cn

只保留校招/实习岗：社招岗位（"电气维修工"这类）不会进库，避免把职场岗位
混进校招雷达。想全收就加 --all。

用法：
    python scripts/crawl_company_sites.py --list
    python scripts/crawl_company_sites.py --company shuanghui --dry-run
    python scripts/crawl_company_sites.py --company shuanghui,cmoc
    python scripts/crawl_company_sites.py --connected          # 所有已接入的
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.config import get_settings  # noqa: E402
from packages.intelligence.external_import import (  # noqa: E402
    ExternalJob,
    ingest_external_jobs,
)
from packages.recruitment_core.crawlers import CRAWLER_MAP  # noqa: E402
from packages.storage import Storage  # noqa: E402

SOURCE = "company-site"

#: 这些平台的爬虫可以直接用；其余是公司专用爬虫，需要逐个确认，默认不跑。
PLATFORM_CRAWLERS = ("beisen", "beisen_mobile", "hotjob", "moka", "feishu")

_CAMPUS_MARKERS = ("校招", "校园", "届", "管培", "应届", "储备生", "培训生")
_INTERN_MARKERS = ("实习",)


def load_companies(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [row for row in payload.get("companies") or [] if isinstance(row, dict)]


def city_of(raw: Any) -> str:
    """把 "河南省·漯河市" / "洛阳市-栾川县" 收敛成一个城市名。"""

    text = str(raw or "").strip()
    if not text:
        return ""
    parts = [part for part in re.split(r"[·\-/、,，\s]+", text) if part]
    for part in parts:
        if part.endswith(("市", "区", "县", "州", "盟")):
            return part.rstrip("市")
    return parts[0] if parts else ""


def track_of(title: str) -> str:
    if any(marker in title for marker in _INTERN_MARKERS):
        return "internship"
    if any(marker in title for marker in _CAMPUS_MARKERS):
        return "campus"
    return "unknown"


def cohort_of(title: str) -> int | None:
    """标题里的 "27届" / "2027届" 就是届别；没写就不猜。"""

    match = re.search(r"(20\d{2})\s*届", title)
    if match:
        return int(match.group(1))
    match = re.search(r"(?<!\d)(2[6-9])\s*[&和、]\s*(2[6-9])\s*届", title)
    if match:
        return 2000 + int(match.group(2))
    match = re.search(r"(?<!\d)(2[6-9])\s*届", title)
    if match:
        return 2000 + int(match.group(1))
    return None


def published_of(raw: Any) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            from datetime import datetime

            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None


def crawl_one(company: dict[str, Any]) -> tuple[list[ExternalJob], dict[str, int]]:
    crawler_key = str(company.get("crawler") or "").strip()
    crawler_cls = CRAWLER_MAP.get(crawler_key)
    if crawler_cls is None:
        raise ValueError(f"没有对应的爬虫：{crawler_key}")
    crawler = crawler_cls(str(company.get("name") or company.get("id")), str(company.get("careers_url") or ""))
    raw_jobs = crawler.fetch() or []

    stats = {"fetched": len(raw_jobs), "campus": 0, "internship": 0, "skipped": 0}
    jobs: list[ExternalJob] = []
    seen: set[tuple[str, str, str]] = set()
    for row in raw_jobs:
        title = str(row.get("title") or "").strip()
        url = str(row.get("jd_url") or "").strip()
        if not title or not url:
            stats["skipped"] += 1
            continue
        track = track_of(title)
        stats[track] = stats.get(track, 0) + 1
        key = (title, city_of(row.get("city")), url)
        if key in seen:
            stats["skipped"] += 1
            continue
        seen.add(key)
        jobs.append(
            ExternalJob(
                company=str(row.get("company") or company.get("name") or ""),
                title=title,
                url=url,
                city=city_of(row.get("city")),
                published_at=published_of(row.get("published_at")),
                track=track,
                cohort=cohort_of(title),
                positions=(),
                source=SOURCE,
                note=f"官网直采 · {crawler_key}",
                raw=row,
            )
        )
    return jobs, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--company", default=None, help="公司 id，逗号分隔")
    parser.add_argument("--crawler", default=None, help="只跑某个平台的爬虫")
    parser.add_argument("--connected", action="store_true", help="跑所有 integration_status=connected 的")
    parser.add_argument("--all", action="store_true", help="社招岗位也收（默认只收校招/实习）")
    parser.add_argument("--list", action="store_true", help="列出可用公司和平台，不抓取")
    parser.add_argument("--dry-run", action="store_true", help="只抓取和预览，不写库")
    parser.add_argument("--limit", type=int, default=0, help="每家最多抓多少条（0＝不限）")
    args = parser.parse_args(argv)

    settings = get_settings()
    companies = load_companies(settings.agent_root / "config" / "companies.yaml")
    usable = [
        row for row in companies
        if str(row.get("crawler") or "") in PLATFORM_CRAWLERS and row.get("careers_url")
    ]

    if args.list:
        print(f"可直采的公司（{len(usable)} 家，按平台）：")
        by_platform: dict[str, list[dict[str, Any]]] = {}
        for row in usable:
            by_platform.setdefault(str(row["crawler"]), []).append(row)
        for platform, rows in sorted(by_platform.items()):
            print(f"\n  [{platform}]")
            for row in rows:
                mark = "✓" if row.get("integration_status") == "connected" else "·"
                print(f"    {mark} {str(row.get('id')):<16} {str(row.get('name')):<14} {row.get('careers_url')}")
        return 0

    targets: list[dict[str, Any]] = usable
    if args.company:
        wanted = {part.strip() for part in args.company.split(",") if part.strip()}
        targets = [row for row in targets if str(row.get("id")) in wanted]
    elif args.crawler:
        targets = [row for row in targets if str(row.get("crawler")) == args.crawler]
    elif args.connected:
        targets = [row for row in targets if row.get("integration_status") == "connected"]
    else:
        parser.error("请用 --company / --connected / --crawler / --list 指定范围")

    if not targets:
        print("没有匹配的公司。", file=sys.stderr)
        return 2

    all_jobs: list[ExternalJob] = []
    summary: list[dict[str, Any]] = []
    for company in targets:
        name = str(company.get("name") or company.get("id"))
        try:
            jobs, stats = crawl_one(company)
        except Exception as exc:  # noqa: BLE001 - 一家失败不该拖垮整批
            print(f"  ✗ {name}：{type(exc).__name__}: {exc}", file=sys.stderr)
            summary.append({"company": name, "error": str(exc)[:200]})
            continue
        if not args.all:
            jobs = [job for job in jobs if job.track in ("campus", "internship")]
        if args.limit:
            jobs = jobs[: args.limit]
        print(
            f"  ✓ {name}：抓到 {stats['fetched']}，校招 {stats.get('campus', 0)}"
            f" / 实习 {stats.get('internship', 0)}，入库候选 {len(jobs)}"
        )
        summary.append({"company": name, **stats, "kept": len(jobs)})
        all_jobs.extend(jobs)

    if not all_jobs:
        print("没有可入库的岗位。")
        return 0

    if args.dry_run:
        print(f"\n预演：共 {len(all_jobs)} 条（没有写库）")
        for job in all_jobs[:30]:
            print(f"  {job.company:<12} {job.title[:34]:<34} {job.city:<6} {job.cohort or ''}")
        return 0

    storage = Storage.from_url(settings.database_url, initialize=True)
    result = ingest_external_jobs(storage, all_jobs)
    print(
        f"\n入库：新增 {result['inserted']} · 更新 {result['updated']} "
        f"· 公司 {result['companies_written']}"
    )
    print("打开 http://127.0.0.1:8010/?view=intelligence 刷新即可看到。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
