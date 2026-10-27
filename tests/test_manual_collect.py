"""Tests for the on-demand, industry-scoped OfferBiu collection."""

import json
from types import SimpleNamespace

import pytest

from packages.intelligence import manual_collect


def _posting(code: str, company: str, title: str) -> dict:
    return {
        "companyName": company,
        "industryGroupCodes": [code],
        "title": title,
    }


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    """Never touch the real .data record while testing."""

    monkeypatch.setattr(manual_collect, "_state_path", lambda: tmp_path / "last-collect.json")


@pytest.fixture()
def patched(monkeypatch):
    """Replace network + DB work with recorded calls."""

    captured = {}

    def fake_fetch(*, recruit_types, max_pages_per_type, logger=None, **kwargs):
        captured["recruit_types"] = tuple(recruit_types)
        captured["max_pages"] = max_pages_per_type
        if logger:
            logger("[offerbiu] 秋招 page 3/10 collected=30")
        return [
            _posting("internet-tech", "A 公司", "后端开发工程师"),
            _posting("finance", "B 银行", "风控岗"),
            _posting("internet-tech", "C 公司", "算法实习生"),
            _posting("semiconductor-hardware", "D 公司", "芯片验证"),
            {},  # a row without industry tags must never raise
        ]

    def fake_ingest(storage, postings, *, record_events=True, **kwargs):
        captured["ingested"] = [row.get("companyName") for row in postings]
        captured["record_events"] = record_events
        return SimpleNamespace(
            to_dict=lambda: {
                "run_id": "run-1",
                "companies_written": len(postings),
                "inserted": len(postings),
                "updated": 0,
            }
        )

    monkeypatch.setattr(manual_collect, "fetch_offerbiu_postings", fake_fetch)
    monkeypatch.setattr(manual_collect, "ingest_offerbiu_postings", fake_ingest)
    return captured


def _wait_for_result():
    status = manual_collect.collection_status()
    deadline = 0
    while status.get("running") and deadline < 200:
        status = manual_collect.collection_status()
        deadline += 1
        if status.get("running"):
            manual_collect._collector._thread.join(timeout=0.05)
    return status


def test_scoped_collection_filters_by_industry(patched):
    manual_collect.start_collection(
        object(), industry_groups=["internet-tech"], recruit_types=["实习"]
    )
    status = _wait_for_result()
    assert status["phase"] == "done"
    assert patched["recruit_types"] == ("实习",)
    assert patched["ingested"] == ["A 公司", "C 公司"]
    assert patched["record_events"] is True
    assert status["result"]["fetched_all"] == 5
    assert status["result"]["in_scope"] == 2
    assert status["result"]["filtered_out"] == 3
    assert status["run_id"] == "run-1"
    assert status["scope"]["industry_groups"] == ["internet-tech"]


def test_multiple_groups_are_unioned(patched):
    manual_collect.start_collection(
        object(),
        industry_groups=["internet-tech", "semiconductor-hardware"],
        recruit_types=["秋招", "春招"],
    )
    status = _wait_for_result()
    assert patched["ingested"] == ["A 公司", "C 公司", "D 公司"]
    assert status["result"]["in_scope"] == 3


def test_page_log_line_updates_progress():
    collector = manual_collect._Collector()
    collector._progress("[offerbiu] 秋招 page 7/120 collected=350")
    collector._progress("something unrelated")
    assert collector.status()["progress"] == {
        "recruit_type": "秋招",
        "page": 7,
        "total_pages": 120,
        "fetched": 350,
    }


def test_empty_group_selection_is_rejected():
    with pytest.raises(ValueError, match="行业范围"):
        manual_collect.start_collection(object(), industry_groups=[], recruit_types=["实习"])


def test_invalid_recruit_type_is_rejected():
    with pytest.raises(ValueError, match="招聘类型"):
        manual_collect.start_collection(
            object(), industry_groups=["internet-tech"], recruit_types=["社招"]
        )


def test_last_run_survives_a_restart(patched, tmp_path):
    manual_collect.start_collection(
        object(), industry_groups=["internet-tech"], recruit_types=["实习"]
    )
    manual_collect._collector._thread.join(timeout=2)
    persisted = json.loads((tmp_path / "last-collect.json").read_text(encoding="utf-8"))
    assert persisted["phase"] == "done"
    assert persisted["result"]["in_scope"] == 2

    fresh = manual_collect._Collector()
    restored = fresh.status()
    assert restored["running"] is False
    assert restored["phase"] == "done"
    assert restored["result"]["in_scope"] == 2
    assert restored["scope"]["industry_groups"] == ["internet-tech"]
