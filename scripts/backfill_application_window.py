"""Fill publish/deadline columns for rows imported before they existed.

The first OfferBiu import kept the deadline only as prose inside ``jd_raw``
(「截止时间：2026/11/30」) and inside ``capture_evidence``.  The 030 migration
adds dedicated columns, but guessing three thousand dates during a migration is
not something a schema change should do — hence a separate, explicit script.

It never invents a publish date: OfferBiu has no per-announcement publish
timestamp, so rows whose evidence does not carry one keep NULL, which the UI
renders as 「未公布」.

Examples:
    python scripts/backfill_application_window.py --dry-run
    python scripts/backfill_application_window.py
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import or_, select  # noqa: E402

from packages.config import get_settings  # noqa: E402
from packages.storage import JobSnapshot, Storage  # noqa: E402

_DATE_IN_TEXT = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")


def parse_date(text: str) -> date | None:
    match = _DATE_IN_TEXT.search(text or "")
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    storage = Storage.from_url(settings.database_url, initialize=True)

    filled_deadline = filled_date = scanned = 0
    with storage.session() as session:
        rows = list(
            session.scalars(
                select(JobSnapshot).where(
                    JobSnapshot.source == "offerbiu",
                    or_(
                        JobSnapshot.deadline_text.is_(None),
                        JobSnapshot.deadline_date.is_(None),
                    ),
                )
            ).all()
        )
        scanned = len(rows)
        for row in rows:
            evidence = row.capture_evidence or {}
            wording = str(evidence.get("deadline_text") or "").strip()
            if not wording:
                # Fall back to the human-readable line the importer rendered.
                for line in (row.jd_raw or "").splitlines():
                    if line.startswith("截止时间："):
                        wording = line.split("：", 1)[1].strip()
                        break
            published_raw = str(evidence.get("publish_date") or "").strip()
            published = date.fromisoformat(published_raw) if published_raw else None
            closes = parse_date(wording)

            changed = False
            if wording and not row.deadline_text:
                row.deadline_text = wording[:64]
                filled_deadline += 1
                changed = True
            if closes and not row.deadline_date:
                row.deadline_date = closes
                filled_date += 1
                changed = True
            if published and not row.publish_date:
                row.publish_date = published
                changed = True
            if changed and args.dry_run:
                session.expunge(row)
        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    mode = "预演" if args.dry_run else "完成"
    print(f"回填{mode}：扫描 {scanned} 行 · 补截止文字 {filled_deadline} · 补截止日期 {filled_date}")
    if not args.dry_run:
        print("发布时间没有可回溯的来源，保持「未公布」；下次采集到这些公告时会自动补上。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
