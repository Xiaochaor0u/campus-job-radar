"""Industry-group matching, including the composite 「材料类」 option.

The source exposes fourteen industry-group codes and has no group of its own
for materials: materials postings live inside chemical/energy, manufacturing,
semiconductor and automotive.  Listing them as a fifteenth code would therefore
never match anything, and simply unioning those four codes would pull in every
mechanical and vehicle posting.

So 「材料类」 is a *composite* group: it narrows to those four codes first, then
keeps only announcements whose position list actually mentions a materials word.
Two stages, one definition, used by both the web button and the CLI script.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: The four real codes materials postings are filed under.
MATERIAL_INDUSTRY_CODES: tuple[str, ...] = (
    "energy-chemical-environment",
    "manufacturing-equipment",
    "semiconductor-hardware",
    "auto-transport-equipment",
)

#: Words that make a posting a materials posting once the industry is right.
#: Matched against the position list only — a battery company's HR role is not a
#: materials job just because the company is tagged 能源/化工.
MATERIAL_KEYWORDS: tuple[str, ...] = (
    "材料",
    "高分子",
    "金属",
    "陶瓷",
    "复合材料",
    "冶金",
    "化工",
    "化学",
    "电池",
    "新能源",
    "薄膜",
    "晶体",
    "粉末",
    "热处理",
    "焊接",
    "涂层",
    "纳米",
    "力学",
    "表征",
    "封装",
    "电镀",
    "腐蚀",
    "烧结",
    "改性",
    "聚合",
    "纺丝",
    "润滑",
    "摩擦学",
    "无机",
    "有机",
    "合金",
    "铸造",
    "锻造",
    "模具",
    "硅片",
    "晶圆",
    "光刻",
)

#: Words that make a posting a software/IT posting, whatever the company's
#: industry tag says.  A stock exchange that hires 软件研发工程师 is an IT job
#: for a CS student even though the source files it under 金融业.
CS_KEYWORDS: tuple[str, ...] = (
    "算法",
    "后端",
    "前端",
    "客户端",
    "服务端",
    "全栈",
    "软件开发",
    "软件工程",
    "软件研发",
    "开发工程师",
    "测试开发",
    "测试工程师",
    "运维",
    "网络安全",
    "信息安全",
    "数据开发",
    "数据分析",
    "数据挖掘",
    "机器学习",
    "深度学习",
    "大模型",
    "自然语言",
    "计算机视觉",
    "人工智能",
    "Java",
    "C++",
    "Python",
    "Golang",
    "嵌入式",
    "云计算",
    "游戏开发",
    "移动端",
    "iOS",
    "Android",
    "大数据",
    "IT类",
    "信息化",
)

#: Which selected groups also have a "the job itself is the field" reading.
ROLE_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "internet-tech": CS_KEYWORDS,
    "materials": MATERIAL_KEYWORDS,
}

#: Composite groups: a code that is not a source code, plus how to resolve it.
COMPOSITE_GROUPS: Mapping[str, Mapping[str, Any]] = {
    "materials": {
        "codes": MATERIAL_INDUSTRY_CODES,
        "keywords": MATERIAL_KEYWORDS,
    },
}


def group_codes(group: str) -> tuple[str, ...]:
    """Source codes a group maps to (itself for ordinary groups)."""

    spec = COMPOSITE_GROUPS.get(group)
    if spec is None:
        return (group,)
    return tuple(spec["codes"])


def expand_industry_codes(groups: Iterable[str]) -> tuple[str, ...]:
    """Flatten a selection into real source codes, order preserved.

    Used wherever a code list has to be handed to something that only knows the
    source's own codes (the discovery registry, the snapshot filters).
    """

    expanded: list[str] = []
    for group in groups:
        for code in group_codes(str(group)):
            if code not in expanded:
                expanded.append(code)
    return tuple(expanded)


def _positions(item: Mapping[str, Any]) -> str:
    return str(item.get("positionsText") or "").strip().casefold()


#: 本身含关键词、但意思是另一回事的词。先抹成顿号再匹配，否则
#: 「自动化工程师」会被当成「化工」、「动力学仿真」会被当成「力学」。
#: 顺序有讲究：长词在前，先整词抹掉再处理短词。
FALSE_FRIEND_TOKENS: tuple[str, ...] = (
    "自动化工程",
    "新能源汽车",
    "工程动力学",
    "动力学",
    "热力学",
    "工程力学",
)


def _matchable_positions(item: Mapping[str, Any]) -> str:
    """Position list with known false-friend words removed."""

    text = _positions(item)
    for token in FALSE_FRIEND_TOKENS:
        text = text.replace(token, "、")
    return text


def matches_industry_scope(
    item: Mapping[str, Any], groups: Iterable[str]
) -> bool:
    """Does this announcement belong to any of the selected groups?

    Ordinary groups match on ``industryGroupCodes``.  Composite groups match on
    their codes *and* their keywords, so ticking 「材料类」 means "materials
    jobs", not "everyone in four industries".
    """

    codes = item.get("industryGroupCodes")
    if not isinstance(codes, list):
        return False
    announced = {str(code) for code in codes}
    if not announced:
        return False

    wanted = tuple(str(group) for group in groups)
    positions = _matchable_positions(item)
    for group in wanted:
        spec = COMPOSITE_GROUPS.get(group)
        if spec is None:
            if group in announced:
                return True
            continue
        if not announced.intersection(spec["codes"]):
            continue
        keywords = tuple(spec["keywords"])
        if not keywords or any(word.casefold() in positions for word in keywords):
            return True
    return False


def matches_role_scope(item: Mapping[str, Any], groups: Iterable[str]) -> bool:
    """Does the position list name a role in one of the selected fields?

    This is the cross-industry reading of 行业范围: 郑州商品交易所 is filed under
    金融业, but it hires 软件研发岗, and a CS student filtering 郑州 + 互联网
    means that job.  Only used when the caller opts in (``role_match``).
    """

    words: list[str] = []
    for group in groups:
        for word in ROLE_KEYWORDS.get(str(group), ()):
            if word not in words:
                words.append(word)
    if not words:
        return False
    positions = _matchable_positions(item)
    return any(word.casefold() in positions for word in words)


__all__ = [
    "COMPOSITE_GROUPS",
    "CS_KEYWORDS",
    "ROLE_KEYWORDS",
    "matches_role_scope",
    "MATERIAL_INDUSTRY_CODES",
    "MATERIAL_KEYWORDS",
    "expand_industry_codes",
    "group_codes",
    "matches_industry_scope",
]
