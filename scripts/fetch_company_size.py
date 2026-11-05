"""Fetch listed-company headcount from Eastmoney F10 and cache it locally.

The hand-curated ``config/company_size.yaml`` only covers companies someone has
verified by hand — a few dozen at most.  For the rest, listed companies disclose
their headcount in annual reports, and Eastmoney's F10 endpoint exposes the
latest disclosed number for ~5000 of them.  That is the widest *stated* source
we can reach without guessing, so it becomes the fallback layer.

Priority when displaying: YAML (hand-checked, dated) > this cache (provider's
latest disclosure) > nothing at all.

Usage:
    python scripts/fetch_company_size.py --dry-run      # 只报告覆盖率
    python scripts/fetch_company_size.py                # 写入 config/company_size_listed.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.intelligence.company_size import normalize_company_name  # noqa: E402

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT = "RPT_F10_BASIC_ORGINFO"
OUTPUT = ROOT / "config" / "company_size_listed.json"
SOURCE = "东方财富 F10 公司概况（最新披露）"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; campus-job-radar/0.1)"}


def fetch_all(*, page_size: int = 500, delay: float = 0.4) -> list[dict[str, Any]]:
    """Page through the report; it carries ~5000 listed companies."""

    rows: list[dict[str, Any]] = []
    page = 1
    pages = 1
    while page <= pages:
        response = requests.get(
            ENDPOINT,
            params={
                "reportName": REPORT,
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,EMP_NUM",
                "pageNumber": page,
                "pageSize": page_size,
                "sortColumns": "SECURITY_CODE",
                "sortTypes": 1,
            },
            headers=_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or {}
        pages = int(result.get("pages") or 1)
        rows.extend(result.get("data") or [])
        print(f"  第 {page}/{pages} 页，累计 {len(rows)} 条", file=sys.stderr)
        page += 1
        if delay:
            time.sleep(delay)
    return rows


def build_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """name -> {employees, as_of, source}; skip anything that is not a real count."""

    today = date.today().isoformat()
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
        raw = row.get("EMP_NUM")
        try:
            employees = int(raw)
        except (TypeError, ValueError):
            continue
        if not name or employees <= 0:
            continue
        index[name] = {
            "employees": employees,
            "as_of": f"最新披露（{today} 抓取）",
            "source": SOURCE,
        }
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只报告覆盖率，不写文件")
    args = parser.parse_args(argv)

    print("拉取上市公司员工数…", file=sys.stderr)
    rows = fetch_all()
    index = build_index(rows)
    print(f"拿到 {len(index)} 家有员工数", file=sys.stderr)

    # 报告一下对本库的覆盖：只有名字能对上的才会显示
    from packages.storage import Storage
    from packages.config import get_settings

    storage = Storage.from_url(get_settings().database_url, initialize=True)
    with storage.session() as session:
        from sqlalchemy import select

        from packages.storage import CompanySnapshot

        names = list(session.scalars(select(CompanySnapshot.name)).all())
    normalized = {normalize_company_name(name) for name in names if name}
    matched = sum(1 for name in index if normalize_company_name(name) in normalized)
    print(f"本库 {len(names)} 家公司中，有 {matched} 家能匹配到员工数")

    if args.dry_run:
        sample = list(index.items())[:5]
        print("示例:", json.dumps(dict(sample), ensure_ascii=False)[:300])
        return 0

    OUTPUT.write_text(
        json.dumps(
            {
                "generated_at": date.today().isoformat(),
                "source": SOURCE,
                "companies": index,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"已写入 {OUTPUT.relative_to(ROOT)}（{OUTPUT.stat().st_size // 1024} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
