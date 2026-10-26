"""Tests for the ownership label used by 公司 / 全部岗位.

The label is display-only, so the contract is narrow: collapse the announcement
vocabulary, refuse to pass industry strings off as ownership, and let the
curated map fill gaps without overriding what the announcement actually said.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.intelligence.company_nature import (
    load_nature_map,
    lookup_nature,
    normalize_nature,
    resolve_nature,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("央国企", "国资"),
        ("国企", "国资"),
        ("央企", "国资"),
        ("民企", "民营"),
        ("民营企业", "民营"),
        ("私企", "民营"),
        ("外企", "外资"),
        ("中外合资", "合资"),
        ("事业单位", "其他"),
        ("银行", "其他"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_collapses_announcement_vocabulary(raw, expected) -> None:
    assert normalize_nature(raw) == expected


@pytest.mark.parametrize(
    "industry",
    ["通信/电子/半导体", "IT/互联网/游戏/电商", "汽车/新能源", "快消/零售/消费品"],
)
def test_industry_strings_are_not_reported_as_ownership(industry: str) -> None:
    # OfferBiu leaks industry into the nature column for a small slice of rows.
    assert normalize_nature(industry) == ""


def test_nature_map_keys_both_id_and_name(tmp_path: Path) -> None:
    path = tmp_path / "company_nature.yaml"
    path.write_text(
        "companies:\n  transsion: 民营\n  h3c: 国资\n  传音控股: 民营\n",
        encoding="utf-8",
    )
    mapping = load_nature_map(path)

    assert mapping["transsion"] == "民营"
    assert mapping["传音控股"] == "民营"
    assert lookup_nature(mapping, "h3c") == "国资"


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_nature_map(tmp_path / "nope.yaml") == {}


def test_announcement_wording_beats_the_curated_map() -> None:
    mapping = {"h3c": "国资"}

    # 公告说民企时以公告为准：我们的人工标注只是补缺，不能覆盖一手信息。
    assert resolve_nature(mapping, company_id="h3c", raw_nature="民企") == "民营"
    assert resolve_nature(mapping, company_id="h3c", raw_nature="") == "国资"


def test_offerbiu_prefix_is_stripped_before_lookup() -> None:
    mapping = {"transsion": "民营"}

    assert resolve_nature(mapping, company_id="offerbiu:transsion") == "民营"


def test_unknown_company_returns_empty_not_a_guess() -> None:
    assert resolve_nature({}, company_id="acme", company_name="不知名公司") == ""
