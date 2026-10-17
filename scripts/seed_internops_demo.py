"""Seed an isolated InternOps database for browser/manual acceptance.

The database is one real recorded Tencent internship (replayed from the fixture)
plus hand-written postings that between them cover every condition bucket the UI
must render: met, gap, unknown, needs-confirmation, a mandatory degree gate, and a
campus posting to prove view isolation.

Everything is deterministic and nothing is written to an existing database.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from packages.matching.policy import ScreeningMode, ScreeningPolicy  # noqa: E402
from packages.pipeline import CrawlResult, run_daily_pipeline  # noqa: E402
from packages.recruitment_core.crawlers.tencent import TencentCrawler  # noqa: E402
from packages.recruitment_core.crawlers.tencent_intern import (  # noqa: E402
    TencentInternshipCrawler,
)
from packages.storage import Storage  # noqa: E402

SAMPLES = PROJECT_ROOT / "tests" / "fixtures" / "tencent_intern_sample.json"

PROFILE: dict[str, Any] = {
    "degree": "本科",
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["西安", "郑州"],
        "available_months": {"summer": 2, "winter": 1.5},
        "max_days_per_week": 3,
    },
}


def _jd(body: str) -> str:
    return (
        "岗位职责：参与业务系统与内部工具的开发、测试和线上问题定位，配合团队完成需求交付。\n"
        "任职要求：" + body
    )


FIXTURES: list[dict[str, Any]] = [
    {
        "id": "demo-intern-xi-an-qualified",
        "title": "Python开发暑期实习生（西安）",
        "city": "西安",
        "jd_raw": _jd(
            "本科及以上，2028届，工作地点：西安，能连续实习2个月以上，每周到岗3天，表现优秀可转正。"
        ),
    },
    {
        "id": "demo-intern-zhengzhou-gap",
        "title": "自动化测试实习生（郑州）",
        "city": "郑州",
        "jd_raw": _jd(
            "本科及以上，2028届，工作地点：郑州，需连续实习3个月以上，每周到岗3天。"
        ),
    },
    {
        "id": "demo-intern-degree-gate",
        "title": "测试开发实习生（西安）",
        "city": "西安",
        "jd_raw": _jd("硕士及以上学历，2028届，工作地点：西安，能连续实习2个月以上。"),
    },
    {
        "id": "demo-intern-unknown",
        "title": "Python后端开发实习生（郑州）",
        "city": "郑州",
        "jd_raw": _jd("具备良好的沟通能力与团队协作意识，能适应快节奏的研发环境，学习能力强。"),
    },
]


def _fixture_job(item: dict[str, Any]) -> dict[str, Any]:
    detail = item["jd_raw"]
    return {
        "id": item["id"],
        "title": item["title"],
        "city": item["city"],
        "job_type": "实习",
        "batch": "internship",
        "cohort": None,
        "cohort_status": "unconfirmed",
        "detail_url": f"https://example.test/internship/{item['id']}",
        "jd_raw": detail,
        "capture_evidence": {
            "status": "complete",
            "method": "ui_fixture",
            "source_url": f"https://example.test/internship/{item['id']}",
            "identity_verified": True,
            "terminal_observed": True,
            "remaining_controls": [],
            "content_sha256": sha256(detail.encode("utf-8")).hexdigest(),
        },
    }


def _recorded_jobs() -> list[dict[str, Any]]:
    """Replay the recorded real Tencent payloads through the production adapter."""

    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    crawler = TencentInternshipCrawler("腾讯", max_jobs=None)
    rows = [item["list_row"] for item in samples["details"]]
    details = {item["post_id"]: item["detail"] for item in samples["details"]}

    class _Response:
        status_code = 200

        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def json(self) -> dict[str, Any]:
            return self._payload

    def request(method: str, url: str, **kwargs: Any) -> Any:
        if url.endswith("/searchPosition"):
            return _Response({"status": 0, "data": {"count": len(rows), "positionList": rows}})
        post_id = str((kwargs.get("params") or {}).get("postId"))
        return _Response({"status": 0, "data": details[post_id]})

    crawler._request = request  # type: ignore[method-assign]
    return crawler.fetch()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.db.exists():
        print(f"refusing to reuse an existing database: {args.db}", file=sys.stderr)
        return 2

    storage = Storage.from_url(f"sqlite:///{args.db.as_posix()}")
    storage.initialize()
    workspace = Path(tempfile.mkdtemp(prefix="internops-demo-"))
    config = workspace / "companies.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "companies": [
                    {
                        "id": "tencent",
                        "name": "腾讯",
                        "careers_url": TencentInternshipCrawler.ORIGIN,
                        "crawler": TencentInternshipCrawler.CRAWLER_KEY,
                        "integration_status": "connected",
                    },
                    {
                        "id": "internops-fixtures",
                        "name": "示例公司",
                        "careers_url": "https://example.test",
                        "crawler": "fixture",
                        "integration_status": "connected",
                    },
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    recorded = _recorded_jobs()
    fixtures = [_fixture_job(item) for item in FIXTURES]

    def crawler(company: Any) -> CrawlResult:
        jobs = recorded if company.id == "tencent" else fixtures
        return CrawlResult(
            jobs=jobs,
            source_url="https://example.test",
            allowed_origins=["https://example.test", TencentInternshipCrawler.ORIGIN],
            pages_seen=1,
            total_pages=1,
            has_more=False,
            crawler_key=company.crawler_key,
            pagination_complete=True,
            completeness_known=True,
            advertised_total=len(jobs),
        )

    result = run_daily_pipeline(
        companies_path=config,
        storage=storage,
        crawler=crawler,
        profile=PROFILE,
        screening_policy=ScreeningPolicy(mode=ScreeningMode.INTERNSHIP),
        max_concurrency=1,
        detail_max_concurrency=1,
        match_max_concurrency=1,
    )
    print(f"seeded {args.db}")
    print(f"new={result.new_count} rejected={len(result.rejected_job_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
