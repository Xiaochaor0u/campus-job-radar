"""Preview or push the job intelligence digest from the local database.

Examples:
    python scripts/intelligence_digest.py                    # 打印最近 24 小时变化
    python scripts/intelligence_digest.py --hours 72          # 最近 3 天
    python scripts/intelligence_digest.py --track internship  # 只看实习
    python scripts/intelligence_digest.py --push              # 推送到 notify.yaml 里的群

`--push` never runs the crawler: it only formats and sends what the database
already knows.  Scheduled use goes through the ``daily_intelligence_digest``
task instead (see scripts/install_windows_tasks.ps1).
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
from packages.intelligence import (  # noqa: E402
    NotifyConfigError,
    build_daily_digest,
    format_digest_text,
    load_notify_config,
    push_daily_digest,
)
from packages.storage import Storage  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=None, help="统计窗口（小时），默认取 notify.yaml")
    parser.add_argument("--track", default=None, choices=["campus", "internship"], help="只看某一类岗位")
    parser.add_argument("--company", default=None, help="只看某个 company_id")
    parser.add_argument("--max-items", type=int, default=None, help="每个企业每种类型最多展示多少条")
    parser.add_argument("--push", action="store_true", help="推送到 config/notify.yaml 配置的群机器人")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        notify = load_notify_config(settings.notify_config)
    except NotifyConfigError as exc:
        print(f"notify 配置有误: {exc}", file=sys.stderr)
        return 2

    hours = args.hours if args.hours is not None else notify.digest_hours
    digest = build_daily_digest(
        Storage.from_url(settings.database_url),
        hours=hours,
        company_id=args.company,
        track=args.track,
        max_items_per_type=args.max_items or max(notify.max_items_per_push, 20),
    )

    if args.json:
        print(json.dumps(digest.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_digest_text(digest, max_items_per_type=notify.max_items_per_push, markdown=False))

    if not args.push:
        return 0
    if not notify.enabled:
        print("未配置 config/notify.yaml 的 webhooks，跳过推送。", file=sys.stderr)
        return 1
    deliveries = push_daily_digest(digest, notify)
    for delivery in deliveries:
        state = "OK" if delivery.ok else "FAILED"
        detail = delivery.error or f"HTTP {delivery.status_code}"
        print(f"[{state}] {delivery.target}: {detail}", file=sys.stderr)
    return 0 if all(delivery.ok for delivery in deliveries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
