"""一键采集向导：只测"把回答翻译成参数"和"把结果讲成人话"这两段。

真正的抓取与入库由 collect_window 负责，那边已有测试；这里守住的是
向导自己的逻辑——选错数字要退回默认值，日期要按自然日算，公告里的
一串岗位名不能把一屏刷满。
"""

from __future__ import annotations

from datetime import date

from scripts import easy_collect


def test_scope_choices_map_to_presets():
    assert easy_collect.resolve_scope("1") == ("cs",)
    assert easy_collect.resolve_scope("2") == ("materials",)
    assert easy_collect.resolve_scope("3") == ("cs", "materials")


def test_unknown_scope_falls_back_to_both():
    assert easy_collect.resolve_scope("9") == ("cs", "materials")


def test_window_choices_map_to_days():
    assert easy_collect.resolve_days("1") == 1
    assert easy_collect.resolve_days("4") == 30


def test_unknown_window_falls_back_to_a_week():
    assert easy_collect.resolve_days("") == 7


def test_window_includes_today_and_counts_backwards():
    start, end = easy_collect.window_for(7, today=date(2026, 11, 5))
    assert end == date(2026, 11, 5)
    assert start == date(2026, 10, 30)


def test_deadline_sentence_says_days_left():
    today = date(2026, 11, 5)
    assert easy_collect.deadline_sentence("2026-11-30", "", today) == "11月30日（还剩 25 天）"
    assert easy_collect.deadline_sentence("2026-11-05", "", today) == "11月5日（今天截止）"
    assert easy_collect.deadline_sentence("2026-11-01", "", today) == "11月1日（已截止）"


def test_deadline_sentence_keeps_the_announcement_wording():
    today = date(2026, 11, 5)
    assert easy_collect.deadline_sentence("", "尽快投递", today) == "尽快投递"
    assert easy_collect.deadline_sentence("", "", today) == "未公布"


def test_rows_are_deduped_for_display_only():
    rows = [
        {"company": "A", "positions": "开发", "deadline": "2026-11-30"},
        {"company": "A", "positions": "开发", "deadline": "2026-11-30"},  # 2026/2027 届
        {"company": "A", "positions": "测试", "deadline": "2026-11-30"},
    ]
    assert len(easy_collect._unique_rows(rows)) == 2


def test_long_position_lists_are_summarised():
    text = "算法,后端,前端,测试,运维,安全"
    summary = easy_collect._short_positions(text, keep=4)
    assert summary.startswith("算法 / 后端 / 前端 / 测试")
    assert "6 个岗位" in summary


def test_empty_positions_get_a_readable_placeholder():
    assert "未列出" in easy_collect._short_positions("")


def test_company_selection_understands_ranges():
    assert easy_collect._parse_selection("1,3,5-7", 10) == [1, 3, 5, 6, 7]
    assert easy_collect._parse_selection("2，4", 10) == [2, 4]


def test_company_selection_drops_junk_and_out_of_range():
    assert easy_collect._parse_selection("", 5) == []
    assert easy_collect._parse_selection("9,abc,0", 5) == []
    assert easy_collect._parse_selection("3-1", 5) == []


def test_ask_which_companies_can_skip_everything(monkeypatch):
    groups = [("A", [{"companyName": "A"}]), ("B", [{"companyName": "B"}])]
    monkeypatch.setattr(easy_collect, "_ask", lambda *a, **k: "3")
    assert easy_collect._ask_which_companies(groups) == []


def test_ask_which_companies_defaults_to_everything(monkeypatch):
    groups = [("A", []), ("B", []), ("C", [])]
    monkeypatch.setattr(easy_collect, "_ask", lambda *a, **k: "1")
    assert easy_collect._ask_which_companies(groups) == [1, 2, 3]


def test_ask_which_companies_reads_the_numbers(monkeypatch):
    groups = [("A", []), ("B", []), ("C", [])]
    monkeypatch.setattr(easy_collect, "_ask", lambda *a, **k: "2")
    monkeypatch.setattr(easy_collect, "_ask_text", lambda *a, **k: "1,3")
    assert easy_collect._ask_which_companies(groups) == [1, 3]


def test_companies_are_grouped_and_sorted_by_volume():
    from scripts.collect_recent import company_groups

    items = [
        {"companyName": "少量公司"},
        {"companyName": "多家公司"},
        {"companyName": "多家公司"},
        {"companyName": "多家公司"},
    ]
    groups = company_groups(items)
    assert [name for name, _rows in groups] == ["多家公司", "少量公司"]
    assert len(groups[0][1]) == 3


def test_only_companies_keeps_the_named_ones(monkeypatch):
    """命令行里按公司挑选入库：其余公告直接丢弃。"""

    from datetime import datetime, timezone

    from scripts import collect_recent

    stamp = datetime.now(timezone.utc).isoformat()
    rows = [
        {"companyName": "米哈游", "positionsText": "游戏客户端开发", "locations": ["上海"],
         "industryGroupCodes": ["internet-tech"], "recruitType": "秋招", "sourceUpdatedAt": stamp},
        {"companyName": "某银行", "positionsText": "软件开发", "locations": ["上海"],
         "industryGroupCodes": ["finance"], "recruitType": "秋招", "sourceUpdatedAt": stamp},
    ]
    monkeypatch.setattr(
        collect_recent, "fetch_offerbiu_postings", lambda **kwargs: rows
    )
    payload = collect_recent.collect_window(
        start=date.today(),
        end=date.today(),
        keywords=("开发",),
        only_companies=("米哈游",),
        dry_run=True,
    )
    assert payload["matched"] == 1
    assert payload["items"][0]["companyName"] == "米哈游"
