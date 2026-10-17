"""Offline regression for the Tencent internship adapter.

The payloads in ``tests/fixtures/tencent_intern_sample.json`` are **real responses**
recorded from the public API (see the ``fetched_at`` field).  They are replayed
through a stubbed transport, so these tests exercise the parsing, normalisation
and classification of genuine data without touching the network.

Network collection itself is evidenced by
``docs/internops/evidence/task-005-tencent-*.json``; this file proves the parsing
stays correct between runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packages.internship.classification import classify_track
from packages.internship.requirements import extract_requirements
from packages.recruitment_core.crawlers.tencent import TencentCrawler
from packages.recruitment_core.crawlers.tencent_intern import TencentInternshipCrawler

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tencent_intern_sample.json"


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _crawler(recorded: dict[str, Any], *, delisted: bool = False) -> TencentInternshipCrawler:
    crawler = TencentInternshipCrawler("腾讯", max_jobs=None)
    details = {item["post_id"]: item for item in recorded["details"]}
    row = recorded["details"][0]["list_row"]

    def fake_request(method: str, url: str, **kwargs: Any) -> _Response:
        if url.endswith("/searchPosition"):
            return _Response({"status": 0, "data": {"count": 1, "positionList": [row]}})
        post_id = str((kwargs.get("params") or {}).get("postId"))
        if delisted:
            return _Response({"status": 404, "message": "岗位已下架"})
        return _Response({"status": 0, "data": details[post_id]["detail"]})

    crawler._request = fake_request  # type: ignore[method-assign]
    return crawler


def test_recorded_fixture_is_real_and_attributed(recorded):
    assert recorded["details"], "fixture must contain at least one recorded posting"
    assert recorded["fetched_at"]
    assert recorded["advertised_total"] == 255
    assert recorded["project_mapping_ids"] == [2, 104]


def test_adapter_normalises_a_recorded_posting(recorded):
    jobs = _crawler(recorded).fetch()

    assert len(jobs) == 1
    job = jobs[0]
    row = recorded["details"][0]["list_row"]

    assert job["id"].startswith("tencent-intern:")
    assert job["title"] == row["positionTitle"]           # title comes from the list row
    assert job["city"]                                     # cities are present
    assert job["batch"] == "internship"
    assert job["recruitment_track"] == "internship"
    assert job["cohort"] is None
    assert job["cohort_status"] == "unconfirmed"
    assert job["detail_url"].startswith("https://join.qq.com/post_detail.html?postid=")
    assert "岗位职责" in job["jd_raw"]
    assert "任职要求" in job["jd_raw"]
    evidence = job["capture_evidence"]
    assert evidence["status"] == "complete"
    assert len(evidence["content_sha256"]) == 64


def test_real_jd_is_classified_as_internship_and_parsed(recorded):
    job = _crawler(recorded).fetch()[0]

    decision = classify_track(job)
    requirements = extract_requirements(job)

    assert decision.track.value == "internship"
    # 项目名是"应届实习"/"日常实习"，不含暑期/寒假字样。空列表正确表示
    # "已分析但没有识别到明确季节"，而不是猜一个。
    assert decision.seasons == []
    assert requirements.evidence, "真实 JD 应至少抽到一条可审计条件"


def test_delisted_posting_is_reported_separately_from_failure(recorded):
    crawler = _crawler(recorded, delisted=True)

    jobs = crawler.fetch()
    metrics = crawler.metrics()

    assert jobs == []
    assert metrics["delisted"] == 1
    assert metrics["detail_failed"] == 0
    assert metrics["failure_samples"] == []


@pytest.mark.parametrize(
    "title,jd,expected",
    [
        # 正式岗位，JD 只是把"实习经历"当加分项：不得判为实习。
        (
            "C++软件开发工程师",
            "岗位职责：负责核心系统研发。任职要求：本科及以上，有相关实习经历者优先。",
            "campus_or_unknown",
        ),
        (
            "算法工程师",
            "岗位职责：负责推荐算法。任职要求：硕士及以上，有暑期实习经验者加分。",
            "campus_or_unknown",
        ),
        # 真实实习岗：标题或招聘类型明确标注。
        (
            "测试开发实习生",
            "岗位职责：负责接口自动化。任职要求：本科及以上，每周到岗3天。",
            "internship",
        ),
        (
            "后台开发",
            "岗位职责：负责服务端开发。任职要求：本科及以上，实习期不少于3个月。",
            "internship",
        ),
    ],
)
def test_experience_phrases_do_not_turn_formal_roles_into_internships(title, jd, expected):
    """第 5 项要求：区分"实习经历/暑期实习"这类表述与真正的实习岗位。"""

    decision = classify_track({"title": title, "jd_raw": jd, "job_type": "校园招聘"})

    if expected == "internship":
        assert decision.track.value == "internship"
    else:
        assert decision.track.value != "internship"
