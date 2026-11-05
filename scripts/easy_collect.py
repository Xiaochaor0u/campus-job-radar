"""一键采集向导：不用记参数，按数字选就行。

给不想敲命令的人用。双击项目根目录的「一键采集.bat」（macOS 是
「一键采集.command」）就会进到这里，全程只需要按回车或输 1/2/3。

真正的采集逻辑在 ``collect_recent.collect_window`` 里，这个文件只负责
问问题、把答案翻译成参数、再把结果讲成人话。两个入口共用同一套过滤
规则，不会出现"命令行采到的和向导采到的不一样"。
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.intelligence.offerbiu_source import CST, OfferBiuError  # noqa: E402
from scripts.collect_recent import (  # noqa: E402
    _merge_scope,
    collect_window,
)

SITE_BASE = "http://127.0.0.1:8010"
SITE_URL = f"{SITE_BASE}/?view=intelligence"
LAUNCHER = "一键启动.bat"

#: 选项 → 内部取值。键就是用户按下的数字，值保持纯数据，方便测试。
SCOPE_CHOICES: dict[str, tuple[str, ...]] = {
    "1": ("cs",),
    "2": ("materials",),
    "3": ("cs", "materials"),
}
WINDOW_CHOICES: dict[str, int] = {"1": 1, "2": 3, "3": 7, "4": 30}

SCOPE_LABELS = {
    ("cs",): "计算机类",
    ("materials",): "材料类",
    ("cs", "materials"): "计算机类 + 材料类",
}
WINDOW_LABELS = {1: "今天", 3: "最近 3 天", 7: "最近 7 天", 30: "最近 30 天"}

LINE = "─" * 58


def _ask(prompt: str, *, default: str, valid: set[str]) -> str:
    """问一个问题，回车取默认值，输错了重问，Ctrl+C 交给上层处理。"""

    while True:
        raw = input(prompt).strip() or default
        if raw in valid:
            return raw
        print(f"  只认 {' / '.join(sorted(valid))}，再试一次。", flush=True)


def resolve_scope(raw: str) -> tuple[str, ...]:
    """把 1/2/3 变成预设键；非法值退回"两者都要"。"""

    return SCOPE_CHOICES.get(raw, SCOPE_CHOICES["3"])


def resolve_days(raw: str) -> int:
    """把 1/2/3/4 变成天数；非法值退回 7 天。"""

    return WINDOW_CHOICES.get(raw, 7)


def window_for(days: int, today: date | None = None) -> tuple[date, date]:
    """最近 N 个自然日（含今天）。"""

    end = today or datetime.now(CST).date()
    return end - timedelta(days=max(days, 1) - 1), end


def deadline_sentence(deadline: str, text: str, today: date) -> str:
    """把截止时间说成人话：还剩几天 / 已截止 / 公告原话 / 未公布。"""

    if deadline:
        try:
            closes = date.fromisoformat(deadline[:10])
        except ValueError:
            return deadline
        days = (closes - today).days
        if days < 0:
            return f"{closes.month}月{closes.day}日（已截止）"
        if days == 0:
            return f"{closes.month}月{closes.day}日（今天截止）"
        return f"{closes.month}月{closes.day}日（还剩 {days} 天）"
    return text or "未公布"


def _short_positions(value: Any, keep: int = 4) -> str:
    """公告里常有一串岗位，只留前几个，避免一行刷屏。"""

    parts = [part.strip() for part in str(value or "").replace("，", ",").split(",")]
    parts = [part for part in parts if part]
    if not parts:
        return "（公告未列出具体岗位名）"
    shown = " / ".join(parts[:keep])
    return f"{shown} 等 {len(parts)} 个岗位" if len(parts) > keep else shown


def _print_header() -> None:
    print("")
    print(LINE)
    print("  秋招岗位雷达 · 一键采集")
    print("  只把岗位信息下载到你自己电脑，不上传任何地方。")
    print(LINE)


def _ask_questions() -> tuple[tuple[str, ...], int, bool]:
    print("")
    print("第 1 步／共 3 步：想看哪个方向？")
    print("  1) 计算机类 —— 算法、开发、测试、运维、数据…")
    print("  2) 材料类   —— 材料、化工、电池、冶金、封装…")
    print("  3) 两者都要")
    scope_raw = _ask("请输入 1 / 2 / 3（直接回车＝两者都要）：", default="3", valid={"1", "2", "3"})

    print("")
    print("第 2 步／共 3 步：想看最近多久发布的？")
    print("  1) 只今天        2) 最近 3 天")
    print("  3) 最近 7 天     4) 最近 30 天")
    window_raw = _ask("请输入 1-4（直接回车＝最近 7 天）：", default="3", valid={"1", "2", "3", "4"})

    print("")
    print("第 3 步／共 3 步：采到之后做什么？")
    print("  1) 直接加进网站（推荐）")
    print("  2) 先看看有哪些，暂时不加")
    mode_raw = _ask("请输入 1 / 2（直接回车＝直接加进网站）：", default="1", valid={"1", "2"})

    return resolve_scope(scope_raw), resolve_days(window_raw), mode_raw == "2"


def _render_result(payload: dict[str, Any], presets: tuple[str, ...]) -> None:
    today = datetime.now(CST).date()
    window = payload["window"]
    label = WINDOW_LABELS.get(
        (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days + 1,
        7,
    )
    print("")
    print("采集完成")
    print(
        f"  {label}一共 {payload['in_window']} 条招聘公告，"
        f"其中 {payload['matched']} 条是你要的方向"
    )

    if payload["dry_run"]:
        rows = payload.get("sample") or []
        if not rows:
            print("  这个时间段里没有匹配的新公告。可以把时间范围放宽到 7 天或 30 天再试。")
            return
        print(f"  下面是前 {len(rows)} 条（没有写进网站）：")
        _print_rows(rows, today)
        return

    result = payload["result"]
    inserted = int(result.get("inserted") or 0)
    updated = int(result.get("updated") or 0)
    if inserted == 0 and updated == 0:
        print("  没有新岗位：库里已经都有这些公告了。")
        print("  这不是故障——企业没发新公告，采多少次都不会多出来。")
        return
    print(f"  新增 {inserted} 个岗位" + (f"，另有 {updated} 个岗位信息有更新" if updated else ""))

    rows = _unique_rows(payload.get("new_jobs") or [])
    if rows:
        print("")
        print("  新增的岗位（在网站里点岗位名就能打开投递页）：")
        _print_rows(rows, today)
        if payload["matched"] > len(rows):
            print("")
            print(f"  ……还有 {payload['matched'] - len(rows)} 条没列出来，打开网站能看全。")


def _unique_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一份公告常按 2026 届 / 2027 届各存一条，列出来像重复，先合并。

    只影响展示：数据库里两条都保留，因为投递入口和届别确实不同。
    """

    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row.get("company") or ""),
            str(row.get("positions") or ""),
            str(row.get("deadline") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _print_rows(rows: list[dict[str, Any]], today: date, *, with_url: bool = True) -> None:
    for row in rows:
        publish = str(row.get("publish") or "")
        published = publish[5:] if len(publish) >= 10 else "日期未公布"
        print("")
        print(f"  {published}   {row.get('company') or '（公司名缺失）'}")
        print(f"            {_short_positions(row.get('positions'))}")
        print(f"            截止：{deadline_sentence(str(row.get('deadline') or ''), '', today)}")
        if with_url and row.get("url"):
            print(f"            投递：{row['url']}")


def site_is_up() -> bool:
    """本机服务有没有在跑。没跑就让人先双击启动，而不是打开一个打不开的页面。"""

    try:
        # 这台机器可能配了全局代理，访问 127.0.0.1 必须绕开。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{SITE_BASE}/api/intelligence/coverage", timeout=2) as response:
            return int(getattr(response, "status", response.getcode())) == 200
    except Exception:  # noqa: BLE001 - 连不上就是没开，不需要区分原因
        return False


def _open_site() -> None:
    print("")
    print(f"  正在打开网站：{SITE_URL}")
    try:
        webbrowser.open(SITE_URL)
    except Exception:  # noqa: BLE001 - 打不开浏览器不是致命错误
        pass
    print("  如果浏览器没反应，手动复制上面这个地址到浏览器即可。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--defaults", action="store_true", help=argparse.SUPPRESS)
    args, _ = parser.parse_known_args(argv)

    _print_header()
    if args.defaults:
        presets, days, dry_run = ("cs", "materials"), 7, False
        print("（已按默认设置运行：计算机类 + 材料类，最近 7 天）")
    else:
        try:
            presets, days, dry_run = _ask_questions()
        except (KeyboardInterrupt, EOFError):
            print("\n已取消，什么都没有改。")
            return 1

    start, end = window_for(days)
    # 预设只提供关键词；行业组留给命令行里的 --groups，向导不追问这些细节。
    _, keywords, _ = _merge_scope(tuple(presets))
    scope_label = SCOPE_LABELS.get(presets, "计算机类 + 材料类")

    print("")
    print(f"开始采集：{scope_label}，{WINDOW_LABELS.get(days, f'最近 {days} 天')}")
    print("请稍候，通常 10 秒内完成（窗口越大越久）…", flush=True)

    page_re = re.compile(r"\[offerbiu\]\s*(\S+)\s+page\s+(\d+)/(\d+)")

    def _show_progress(message: str) -> None:
        # 分页日志本身对普通用户是噪音，但长时间没反应更像卡死，
        # 所以只把「第几页」压成一行原地刷新。
        match = page_re.search(str(message))
        if match:
            kind, page, total = match.group(1), match.group(2), match.group(3)
            sys.stdout.write(f"\r  正在下载{kind}公告… 第 {page}/{total} 页")
            sys.stdout.flush()

    try:
        payload = collect_window(
            start=start,
            end=end,
            groups=(),
            keywords=keywords,
            dry_run=dry_run,
            progress=_show_progress
        )
    except OfferBiuError as exc:
        print("")
        print(f"连不上招聘数据源：{exc}")
        print("检查一下网络，或在浏览器打开 https://offerbiu.com 试试。")
        return 2
    except KeyboardInterrupt:
        print("\n已中断，本次什么都没有写入。")
        return 1
    finally:
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()

    _render_result(payload, presets)

    if not args.defaults and not dry_run:
        print("")
        if site_is_up():
            answer = _ask("要看网页版吗？ 1) 打开  2) 不用（回车＝打开）：", default="1", valid={"1", "2"})
            if answer == "1":
                _open_site()
        else:
            print(f"网站现在没开。双击项目文件夹里的「{LAUNCHER}」，")
            print(f"等它提示启动成功，再用浏览器打开 {SITE_URL} 就能看到刚采的岗位。")

    print("")
    print(LINE)
    print("  完成。下次想再采，双击同一个文件就行。")
    print(LINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
