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
