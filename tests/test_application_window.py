"""Publish date / deadline handling, the windowed fetch, and the CLI scope rules.

These three pieces share one failure mode worth guarding against: a missing
date must stay missing.  Guessing a deadline is worse than showing 「未公布」,
and silently widening the window would make the "last 24 hours" promise false.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

import pytest

from packages.intelligence import offerbiu_source as source
from scripts import collect_recent


def _item(**overrides):
    base = {
        "id": "rec-1",
        "companyName": "示例材料",
        "companyId": "rc-示例材料",
        "positionsText": "材料研发工程师",
        "applyUrl": "https://example.com/job",
        "recruitType": "秋招",
        "industryGroupCodes": ["energy-chemical-environment"],
        "deadlineAt": "2026-11-30",
        "deadlineText": "2026/11/30",
        "sourceUpdatedAt": "2026-11-03T16:00:00Z",
        "status": "ACTIVE",
    }
    base.update(overrides)
    return base


# --- mapping ---------------------------------------------------------------


def test_publish_date_uses_china_time_not_utc():
    # 16:00Z on the 3rd is 00:00 on the 4th in Beijing: the announcement date
    # students see is the 4th, and shifting it to UTC would look a day early.
    assert source.announcement_publish_date(_item()) == date(2026, 11, 4)


def test_publish_date_is_none_when_the_feed_has_no_timestamp():
    assert source.announcement_publish_date(_item(sourceUpdatedAt="", importedAt="")) is None


def test_deadline_keeps_exact_date():
    closes, wording = source.announcement_deadline(_item())
    assert closes == date(2026, 11, 30)
    assert wording == "2026/11/30"


def test_deadline_falls_back_to_wording_without_inventing_a_date():
    closes, wording = source.announcement_deadline(
        _item(deadlineAt=None, deadlineText="尽快投递")
    )
    assert closes is None
    assert wording == "尽快投递"


def test_job_row_carries_both_dates_and_records_precision():
    row, _ = source._job_row(_item(), now=datetime(2026, 11, 4, tzinfo=source.CST))
    assert row.publish_date == date(2026, 11, 4)
    assert row.deadline_date == date(2026, 11, 30)
    assert row.deadline_text == "2026/11/30"
    assert row.capture_evidence["publish_precision"] == "day"
    assert row.capture_evidence["deadline_precision"] == "day"


def test_unknown_deadline_is_stored_as_absent_not_as_a_guess():
    row, _ = source._job_row(
        _item(deadlineAt=None, deadlineText=""), now=datetime(2026, 11, 4, tzinfo=source.CST)
    )
    assert row.deadline_date is None
    assert row.deadline_text is None


# --- windowed fetch --------------------------------------------------------


class _FakeResponse:
    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"items": self._items, "totalPages": 99}}


class _FakeSession:
    """Serves pages in descending date order, like the real feed."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = 0
        self.headers = {}

    def get(self, _url, params=None, timeout=None):
        self.calls += 1
        index = int(params["page"]) - 1
        return _FakeResponse(self.pages[index] if index < len(self.pages) else [])

    def close(self):
        return None


def test_stop_before_ends_paging_at_the_window_edge():
    pages = [
        [_item(id="a", sourceUpdatedAt="2026-11-03T16:00:00Z")],
        [_item(id="b", sourceUpdatedAt="2026-11-02T16:00:00Z")],
        [_item(id="c", sourceUpdatedAt="2026-10-20T16:00:00Z")],  # past the edge
        [_item(id="d", sourceUpdatedAt="2026-10-19T16:00:00Z")],
    ]
    session = _FakeSession(pages)
    postings = source.fetch_offerbiu_postings(
        recruit_types=["秋招"],
        stop_before=date(2026, 10, 29),
        session=session,
        delay_seconds=0,
    )
    # Third page is read (it contains the edge) and then paging stops: the
    # fourth page is never requested.
    assert session.calls == 3
    assert [item["id"] for item in postings] == ["a", "b", "c"]


def test_without_stop_before_paging_only_ends_on_an_empty_page():
    session = _FakeSession([[_item(id="a")], [_item(id="b")]])
    postings = source.fetch_offerbiu_postings(
        recruit_types=["秋招"], session=session, delay_seconds=0
    )
    # Two data pages plus the empty probe that proves the feed ended.
    assert session.calls == 3
    assert [item["id"] for item in postings] == ["a", "b"]


# --- CLI scope -------------------------------------------------------------


def test_keywords_match_positions_not_company_or_industry():
    # An internet company hiring a designer is not a 计算机类 posting.
    item = _item(
        companyName="某某科技有限公司",
        industry="IT/互联网/游戏",
        positionsText="美宣平面设计师,招聘专员",
    )
    reason = collect_recent._match_reason(
        item, groups=frozenset(), keywords=("算法", "开发")
    )
    assert reason == ""


def test_industry_groups_only_count_when_explicitly_requested():
    item = _item(positionsText="市场专员", industryGroupCodes=["internet-tech"])
    preset = collect_recent._merge_scope(("cs",))
    assert preset[0] == ()  # presets contribute no industry scope
    assert collect_recent._match_reason(
        item, groups=frozenset(), keywords=preset[1]
    ) == ""
    assert collect_recent._match_reason(
        item, groups=frozenset({"internet-tech"}), keywords=()
    ) == "group"


def test_day_window_includes_today_and_the_previous_n_minus_one_days():
    args = argparse.Namespace(days=7, hours=None, since=None, until=None)
    start, end, _ = collect_recent._window_bounds(args)
    today = datetime.now(collect_recent.CST).date()
    assert end == today
    assert start == today - timedelta(days=6)


def test_hours_window_never_starts_in_the_future():
    args = argparse.Namespace(days=7, hours=24, since=None, until=None)
    start, end, _ = collect_recent._window_bounds(args)
    assert start <= end


def test_unknown_preset_is_rejected():
    with pytest.raises(SystemExit):
        collect_recent._preset_keys("design")
