"""公司人数规模：显示规则的核心是「没有出处就不显示」。

这些测试守三件事：数字格式不夸大精度、别名和工商后缀能匹配上、坏数据不会
变成看起来像真的数字。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.intelligence.company_size import (
    CompanySize,
    format_headcount,
    load_size_map,
    normalize_company_name,
    resolve_size,
)


def write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "company_size.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- 显示格式 ---------------------------------------------------------------


def test_headcount_reads_in_wan_above_ten_thousand():
    assert format_headcount(15551) == "1.6 万人"
    assert format_headcount(131988) == "13.2 万人"
    assert format_headcount(968872) == "96.9 万人"


def test_headcount_keeps_exact_people_below_ten_thousand():
    assert format_headcount(5034) == "5,034 人"
    assert format_headcount(999) == "999 人"


def test_round_ten_thousand_does_not_keep_a_trailing_zero():
    assert format_headcount(10000) == "1 万人"
    assert format_headcount(20000) == "2 万人"


# --- 名称归一化 -------------------------------------------------------------


def test_registration_suffixes_are_stripped():
    assert normalize_company_name("中国石油天然气股份有限公司") == "中国石油天然气"
    assert normalize_company_name("*ST椰岛") == "椰岛"


def test_group_and_holding_words_are_not_stripped():
    """「中国稀土集团」和「中国稀土」是两家主体，剥掉"集团"就会张冠李戴。"""

    assert normalize_company_name("中国稀土集团") == "中国稀土集团"
    assert normalize_company_name("中国稀土") == "中国稀土"


def test_exchange_letter_only_stripped_for_chinese_names():
    mapping = {"万科": CompanySize(131429, "最新", "F10")}
    # 万科A / 万科 都能查到；Tesla 不会被截成 Tesl
    assert resolve_size(mapping, company_name="万科A") is not None
    assert normalize_company_name("Tesla") == "tesla"


# --- 加载与合并 -------------------------------------------------------------


def test_yaml_entry_carries_source_and_date(tmp_path):
    path = write_yaml(
        tmp_path,
        """
companies:
  科大讯飞:
    aliases: [iflytek]
    employees: 15551
    as_of: "2024年末"
    source: "2024 年年度报告"
""",
    )
    mapping = load_size_map(path)
    assert mapping["科大讯飞"].employees == 15551
    assert mapping["iflytek"].employees == 15551
    assert mapping["科大讯飞"].describe() == "2024年末 · 2024 年年度报告"


def test_yaml_accepts_a_plain_number(tmp_path):
    path = write_yaml(tmp_path, "companies:\n  某某有限公司: 1200\n")
    mapping = load_size_map(path)
    # 键归一化为"某某"，所以工商全称和简称都能对上
    assert resolve_size(mapping, company_name="某某").employees == 1200


def test_entries_without_a_usable_number_are_dropped(tmp_path):
    path = write_yaml(
        tmp_path,
        """
companies:
  没有数字:
    source: "内部估计"
  零人:
    employees: 0
  乱写:
    employees: "很多人"
  正常:
    employees: 800
""",
    )
    mapping = load_size_map(path)
    assert set(mapping) == {"正常"}


def test_missing_file_means_no_badges(tmp_path):
    assert load_size_map(tmp_path / "nope.yaml") == {}


def test_hand_checked_layer_wins_over_the_fetched_cache(tmp_path):
    yaml_path = write_yaml(
        tmp_path,
        """
companies:
  比亚迪:
    employees: 968872
    as_of: "2024年末"
    source: 年报
""",
    )
    cache_path = tmp_path / "listed.json"
    cache_path.write_text(
        '{"companies": {"比亚迪": {"employees": 900000, "as_of": "最新", "source": "F10"}}}',
        encoding="utf-8",
    )
    info = resolve_size(load_size_map(yaml_path, cache_path), company_name="比亚迪")
    assert info.employees == 968872
    assert info.source == "年报"


def test_fetched_cache_fills_companies_the_yaml_does_not_cover(tmp_path):
    yaml_path = write_yaml(tmp_path, "companies: {}\n")
    cache_path = tmp_path / "listed.json"
    cache_path.write_text(
        '{"companies": {"某科技": {"employees": 3400, "as_of": "最新", "source": "F10"}}}',
        encoding="utf-8",
    )
    assert resolve_size(load_size_map(yaml_path, cache_path), company_name="某科技").label == "3,400 人"


def test_resolve_matches_name_id_prefix_and_alias():
    mapping = {
        "科大讯飞": CompanySize(15551, "2024年末", "年报"),
        "iflytek": CompanySize(15551, "2024年末", "年报"),
    }
    assert resolve_size(mapping, company_id="offerbiu:科大讯飞").employees == 15551
    assert resolve_size(mapping, company_name="科大讯飞").employees == 15551
    assert resolve_size(mapping, company_id="iflytek").employees == 15551
    assert resolve_size(mapping, company_name="不存在的公司") is None


# --- 仓库自带的数据 ---------------------------------------------------------


def test_shipped_config_is_valid():
    """每条都必须有正数、出处和截止时间——这是「不清楚就不写」的自动化检查。"""

    root = Path(__file__).resolve().parents[1]
    mapping = load_size_map(root / "config" / "company_size.yaml")
    assert len(mapping) >= 10
    for info in mapping.values():
        assert info.employees > 0
        assert info.source, "每条规模都必须写清出处"
        assert info.as_of, "人数会变，必须标注截止时间"


def test_shipped_listed_cache_matches_our_company_names():
    """抓取层至少要覆盖到库里的一部分公司，否则这个功能等于没开。"""

    root = Path(__file__).resolve().parents[1]
    cache = load_size_map(
        root / "config" / "company_size.yaml",
        root / "config" / "company_size_listed.json",
    )
    assert len(cache) > 1000
    assert resolve_size(cache, company_name="比亚迪") is not None


@pytest.mark.parametrize("name", ["科大讯飞", "中兴通讯", "海康威视", "传音控股", "隆基绿能"])
def test_known_employers_resolve(name):
    root = Path(__file__).resolve().parents[1]
    cache = load_size_map(
        root / "config" / "company_size.yaml",
        root / "config" / "company_size_listed.json",
    )
    info = resolve_size(cache, company_name=name)
    assert info is not None and info.employees > 1000
