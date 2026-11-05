"""Collect only the postings announced inside a window you choose.

The workbench button is convenient but fixed to "everything the feed has".
This script answers the narrower question students actually ask: *what was
published in the last 24 hours / 7 days, and only in my field?*

It reads the same public feed as the site, keeps the announcements whose
publish date falls inside the window, applies an industry/keyword scope, then
writes them into the local database.  The workbench reads that database
directly, so a finished run shows up on the next page refresh — no import step.

Examples:
    python scripts/collect_recent.py                       # 近 24 小时，计算机 + 材料
    python scripts/collect_recent.py --days 7              # 近 7 天，同上
    python scripts/collect_recent.py --preset materials --days 30
    python scripts/collect_recent.py --hours 12 --preset cs
    python scripts/collect_recent.py --since 2026-10-28 --until 2026-11-04
    python scripts/collect_recent.py --days 7 --dry-run    # 只看会采到什么，不写库

Publish date precision: the feed exposes a day-level source timestamp, so the
window is applied per calendar day (Asia/Shanghai).  Deadlines keep whatever
the announcement said — an exact date, 「尽快投递」, or 「未公布」.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.config import get_settings  # noqa: E402
from packages.industry_scope import (  # noqa: E402
    MATERIAL_KEYWORDS,
    matches_industry_scope,
    matches_role_scope,
)
from packages.job_filters import (  # noqa: E402
    cohort_label,
    matches_city,
    matches_cohort,
    selected_place_label,
)
from packages.intelligence.offerbiu_source import (  # noqa: E402
    CST,
    OfferBiuError,
    announcement_deadline,
    announcement_publish_date,
    fetch_offerbiu_postings,
    ingest_offerbiu_postings,
)
from packages.storage import Storage  # noqa: E402

DEFAULT_TYPES = ("实习", "秋招", "春招")

#: Scope presets.  Matching happens on the announcement's position list, not on
#: its industry: an internet company also hires designers and HR, and those are
#: not "计算机类" postings just because the company is tagged internet-tech.
#: Precision matters more than recall here — a wrong row costs the reader a
#: click, a missing row costs nothing they cannot find with --groups.
PRESETS: dict[str, dict[str, Any]] = {
    "cs": {
        "label": "计算机类",
        "keywords": (
            "算法", "后端", "前端", "客户端", "服务端", "全栈", "软件开发", "软件工程师",
            "软件研发", "测试开发", "测试工程师", "运维", "网络安全", "数据开发",
            "数据仓库", "机器学习", "深度学习", "大模型", "自然语言", "计算机视觉",
            "人工智能", "AI 应用", "AI应用", "Java", "C++", "Python", "Golang",
            "嵌入式", "云计算", "游戏开发", "移动端", "iOS", "Android", "大数据",
        ),
    },
    "materials": {
        "label": "材料类",
        # 与网页「材料/新材料」勾选项、scripts 的 --preset materials 共用同一份
        # 关键词，三处口径永远一致。
        "keywords": MATERIAL_KEYWORDS,
    },
}


def _preset_keys(name: str) -> tuple[str, ...]:
    if name == "both":
        return ("cs", "materials")
    if name not in PRESETS:
        raise SystemExit(f"未知预设：{name}（可选 cs / materials / both）")
    return (name,)


def _merge_scope(presets: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """Presets contribute keywords only; industry groups are opt-in via --groups."""

    keywords: list[str] = []
    labels: list[str] = []
    for key in presets:
        preset = PRESETS[key]
        labels.append(str(preset["label"]))
        for word in preset["keywords"]:
            if word not in keywords:
                keywords.append(word)
    return (), tuple(keywords), " + ".join(labels)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positions(item: dict[str, Any]) -> str:
    """Only the position list counts — company and industry add noise."""

    return _text(item.get("positionsText")).casefold()


def _match_reason(
    item: dict[str, Any],
    *,
    groups: frozenset[str],
    keywords: tuple[str, ...],
    role_match: bool = False,
) -> str:
    """Return "group", "keyword" or "" — why this posting is in scope."""

    if keywords:
        positions = _positions(item)
        if any(word.casefold() in positions for word in keywords):
            return "keyword"
    # 普通分组比 industryGroupCodes；「材料/新材料」这类组合组还要看岗位名。
    # role_match 打开时，公司行业对不上但岗位名对得上的也算（郑商所的软件研发岗）。
    if groups and (
        matches_industry_scope(item, groups)
        or (role_match and matches_role_scope(item, groups))
    ):
        return "group"
    return ""


def _window_bounds(args: argparse.Namespace) -> tuple[date, date, str]:
    """Resolve CLI flags into an inclusive [start, end] day range."""

    today = datetime.now(CST).date()
    if args.since:
        start = date.fromisoformat(args.since)
        end = date.fromisoformat(args.until) if args.until else today
        label = f"{start.isoformat()} ~ {end.isoformat()}"
    elif args.until:
        end = date.fromisoformat(args.until)
        start = end - timedelta(days=max(args.days, 1) - 1)
        label = f"{start.isoformat()} ~ {end.isoformat()}"
    elif args.hours:
        start = (datetime.now(CST) - timedelta(hours=args.hours)).date()
        end = today
        label = f"最近 {args.hours} 小时（{start.isoformat()} 起）"
    else:
        start = today - timedelta(days=max(args.days, 1) - 1)
        end = today
        label = f"最近 {args.days} 天（{start.isoformat()} ~ {end.isoformat()}）"
    if start > end:
        raise SystemExit("时间范围不合法：开始日期晚于结束日期")
    return start, end, label


def _deadline_label(item: dict[str, Any]) -> str:
    closes, wording = announcement_deadline(item)
    if closes:
        return closes.isoformat()
    return wording or "未公布"


def _short(value: Any, limit: int) -> str:
    raw = _text(value).replace("\n", " ")
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--hours", type=int, default=None, help="最近多少小时（默认 24）")
    window.add_argument("--days", type=int, default=7, help="最近多少个自然日（默认 7）")
    parser.add_argument("--since", default=None, help="起始日期 YYYY-MM-DD（含）")
    parser.add_argument("--until", default=None, help="结束日期 YYYY-MM-DD（含，默认今天）")
    parser.add_argument(
        "--preset",
        default="both",
        choices=("cs", "materials", "both"),
        help="领域预设：cs 计算机 / materials 材料 / both 两者（默认）",
    )
    parser.add_argument("--keywords", default=None, help="额外关键词，逗号分隔；给了就只保留命中的")
    parser.add_argument("--groups", default=None, help="自定义行业组 code，逗号分隔（覆盖预设）")
    parser.add_argument(
        "--city", "--cities", dest="city", default="",
        help="只看这些城市的岗位，如 郑州 或 郑州,洛阳（默认全国）",
    )
    parser.add_argument(
        "--role-match", dest="role_match", action="store_true", default=None,
        help="除公司行业标签外，也按岗位名判断方向（郑商所的软件研发岗也算互联网）",
    )
    parser.add_argument(
        "--no-role-match", dest="role_match", action="store_false",
        help="严格只按公司行业标签匹配",
    )
    parser.add_argument(
        "--cohort", type=int, default=0,
        help="只看某届校招，如 2027（0＝不限；实习不受此限，寒假/日常/春季实习都会收）",
    )
    parser.add_argument(
        "--types", nargs="+", default=list(DEFAULT_TYPES),
        help="招聘类型：实习 / 秋招 / 春招（可多选）",
    )
    parser.add_argument("--max-pages", type=int, default=300, help="每种类型最多拉取页数")
    parser.add_argument("--delay", type=float, default=0.2, help="每页间隔秒数")
    parser.add_argument("--limit-print", type=int, default=30, help="报告里最多列多少条新增")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser


def preset_matches(item: dict[str, Any]) -> tuple[str, ...]:
    """Which presets this posting belongs to — used for friendly summaries."""

    positions = _positions(item)
    return tuple(
        key
        for key, preset in PRESETS.items()
        if any(word.casefold() in positions for word in preset["keywords"])
    )


def collect_window(
    *,
    start: date,
    end: date,
    groups: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
    city: str = "",
    cohort: int = 0,
    role_match: bool = False,
    recruit_types: tuple[str, ...] = DEFAULT_TYPES,
    max_pages: int = 300,
    delay: float = 0.2,
    dry_run: bool = False,
    limit_print: int = 30,
    progress: Any = None,
) -> dict[str, Any]:
    """Fetch one window, filter it, and (unless ``dry_run``) write it to the database.

    Both the CLI and the double-click wizard call this, so the two entry points
    can never drift apart in how they decide what counts as "in scope".
    """

    log = progress or (lambda message: print(message, file=sys.stderr))
    postings = fetch_offerbiu_postings(
        recruit_types=tuple(recruit_types),
        max_pages_per_type=max_pages,
        delay_seconds=delay,
        # The feed is date-ordered: stop at the window edge instead of reading
        # all ~450 pages, most of which are older than the window.
        stop_before=start,
        logger=log,
    )

    in_window: list[dict[str, Any]] = []
    for item in postings:
        published = announcement_publish_date(item)
        if published is not None and start <= published <= end:
            in_window.append(item)

    # 城市和届别比行业更"硬"，先筛掉，后面的命中数才对得上用户的选择。
    in_place = [item for item in in_window if matches_city(item, city)]
    in_cohort = [item for item in in_place if matches_cohort(item, cohort)]

    group_set = frozenset(groups)
    scoped: list[dict[str, Any]] = []
    reasons: dict[str, int] = {"group": 0, "keyword": 0}
    by_preset: dict[str, int] = {key: 0 for key in PRESETS}
    for item in in_cohort:
        reason = _match_reason(
            item, groups=group_set, keywords=keywords, role_match=role_match
        )
        if not reason:
            continue
        reasons[reason] += 1
        scoped.append(item)
        for key in preset_matches(item):
            by_preset[key] = by_preset.get(key, 0) + 1

    payload: dict[str, Any] = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "fetched": len(postings),
        "in_window": len(in_window),
        "in_place": len(in_place),
        "in_cohort": len(in_cohort),
        "city": city,
        "cohort": cohort,
        "role_match": bool(role_match),
        "matched": len(scoped),
        "matched_by": reasons,
        "by_preset": by_preset,
        "dry_run": bool(dry_run),
        "items": scoped,
    }

    if dry_run:
        payload["sample"] = [
            {
                "company": _text(item.get("companyName")),
                "types": _text(item.get("recruitType")),
                "publish": announcement_publish_date(item).isoformat()
                if announcement_publish_date(item)
                else "",
                "deadline": _deadline_label(item),
                "positions": _short(item.get("positionsText"), 60),
            }
            for item in scoped[:limit_print]
        ]
        return payload

    settings = get_settings()
    storage = Storage.from_url(settings.database_url, initialize=True)
    result = ingest_offerbiu_postings(storage, scoped, record_events=True)
    payload["result"] = result.to_dict()
    payload["new_jobs"] = [
        {
            "company": _text(item.get("companyName")),
            "types": _text(item.get("recruitType")),
            "publish": announcement_publish_date(item).isoformat()
            if announcement_publish_date(item)
            else "",
            "deadline": _deadline_label(item),
            "positions": _short(item.get("positionsText"), 80),
            "url": _text(item.get("applyUrl")) or _text(item.get("announcementUrl")),
        }
        for item in scoped[:limit_print]
    ]
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    start, end, window_label = _window_bounds(args)

    presets = _preset_keys(args.preset)
    groups, keywords, scope_label = _merge_scope(presets)
    if args.groups:
        # 明确指定行业组就是"按行业选"，不再叠加预设关键词，否则 A 公司的
        # 软件岗会绕过行业限制进来（--keywords 仍可显式追加）。
        groups = tuple(part.strip() for part in args.groups.split(",") if part.strip())
        keywords = ()
    if args.keywords:
        keywords = tuple(part.strip() for part in args.keywords.split(",") if part.strip())

    try:
        payload = collect_window(
            start=start,
            end=end,
            groups=groups,
            keywords=keywords,
            city=args.city,
            cohort=args.cohort,
            role_match=(
                get_settings().offerbiu_collect_role_match
                if args.role_match is None
                else args.role_match
            ),
            recruit_types=tuple(args.types),
            max_pages=args.max_pages,
            delay=args.delay,
            dry_run=args.dry_run,
            limit_print=args.limit_print,
        )
    except OfferBiuError as exc:
        print(f"采集失败：{exc}", file=sys.stderr)
        return 2

    payload["window"]["label"] = window_label
    payload["scope"] = {
        "label": scope_label,
        "groups": list(groups),
        "keywords": list(keywords),
        "city": args.city,
        "cohort": args.cohort,
    }

    if args.json:
        payload.pop("items", None)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("")
    print(f"时间窗：{window_label}")
    print(f"范围：{scope_label}（{len(groups)} 个行业组 + {len(keywords)} 个关键词）")
    if args.city:
        print(f"城市：{selected_place_label(args.city)}")
    if args.cohort:
        print(f"届别：{cohort_label(args.cohort)}（实习不受届别限制）")
    place_note = f" · 城市内 {payload['in_place']} 条" if args.city else ""
    cohort_note = f" · 届别内 {payload['in_cohort']} 条" if args.cohort else ""
    print(
        f"拉取 {payload['fetched']} 条 · 窗口内 {payload['in_window']} 条"
        f"{place_note}{cohort_note} · "
        f"命中 {payload['matched']} 条（行业 {payload['matched_by']['group']} / "
        f"关键词扩展 {payload['matched_by']['keyword']}）"
    )
    if args.dry_run:
        print("预演模式：没有写库。去掉 --dry-run 才会导入。")
        rows = payload.get("sample") or []
        if rows:
            print("")
            print(f"命中示例（前 {len(rows)} 条）")
            for row in rows:
                print(
                    f"  {row['publish']}  截止 {row['deadline']:<10} "
                    f"{row['company']}｜{row['positions']}"
                )
        return 0

    result = payload["result"]
    print(
        f"入库：新增 {result['inserted']} · 更新 {result['updated']} "
        f"· 公司 {result['companies_written']} · 跳过 {result['skipped_invalid']}"
    )
    rows = payload.get("new_jobs") or []
    if rows:
        print("")
        print(f"新增岗位（前 {len(rows)} 条，发布时间为源站收录日）")
        for row in rows:
            print(
                f"  {row['publish']}  截止 {row['deadline']:<10} "
                f"{row['company']}｜{row['positions']}"
            )
    print("")
    print("打开 http://127.0.0.1:8010/?view=intelligence 刷新即可看到。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
