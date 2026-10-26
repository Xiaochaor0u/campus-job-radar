"""Company ownership label, for display only.

Two sources, in order:
  1. the announcement's own ``companyNature`` field (OfferBiu carry ~44% of rows)
  2. ``config/company_nature.yaml``, hand-curated for the crawl registry

The labels are deliberately coarse (民营 / 国资 / 外资 / 合资 / 其他). Ownership is
genuinely ambiguous for mixed-ownership groups, so a fine-grained claim would be
worse than a rough one — and the UI says it is a rough label.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

LABELS = ("国资", "民营", "外资", "合资", "高校", "其他")

#: OfferBiu uses a different vocabulary per record; collapse it.
_SYNONYMS: dict[str, str] = {
    "央国企": "国资",
    "国企": "国资",
    "央企": "国资",
    "国有企业": "国资",
    "中央企业": "国资",
    "国资": "国资",
    "国有": "国资",
    "事业单位": "其他",
    "社会机构": "其他",
    "银行": "其他",
    "民企": "民营",
    "民营企业": "民营",
    "私企": "民营",
    "民营": "民营",
    "外企": "外资",
    "外资": "外资",
    "外商独资": "外资",
    "外企/合资": "外资",
    "合资": "合资",
    "中外合资": "合资",
    "股份/集体/混合/其他性质": "其他",
    "其他": "其他",
}

#: Industry strings leak into the nature column for a small slice of records.
#: They say nothing about ownership, so they must not be shown as if they did.
_INDUSTRY_NOISE = (
    "通信", "电子", "半导体", "IT/", "互联网", "游戏", "电商", "金融", "机械",
    "制造", "设备", "能源", "化工", "环保", "房地产", "建筑", "物业", "汽车",
    "新能源", "快消", "零售", "消费品", "交通", "物流", "出行", "生物", "医疗",
    "医药", "制药", "教育", "培训", "农业", "林业", "人工智能", "机器人", "文化",
    "传媒", "出版", "具身",
)


def normalize_nature(raw: Any) -> str:
    """Collapse a raw nature string into one of :data:`LABELS`, or ``""``."""

    text = str(raw or "").strip()
    if not text:
        return ""
    if text in _SYNONYMS:
        return _SYNONYMS[text]
    if any(word in text for word in _INDUSTRY_NOISE):
        return ""
    for key, label in _SYNONYMS.items():
        if key and key in text:
            return label
    return ""


def load_nature_map(path: Path | str) -> dict[str, str]:
    """Read ``company_nature.yaml`` into a lookup keyed by id and by name."""

    import yaml

    target = Path(path)
    if not target.is_file():
        return {}
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    rows = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}
    mapping: dict[str, str] = {}
    for key, value in rows.items():
        label = normalize_nature(value)
        if not label:
            continue
        mapping[str(key).strip()] = label
        mapping[str(key).strip().casefold()] = label
    return mapping


def lookup_nature(mapping: dict[str, str], *keys: Any) -> str:
    """First non-empty label among the candidate keys."""

    for key in keys:
        text = str(key or "").strip()
        if text and text in mapping:
            return mapping[text]
        folded = text.casefold()
        if folded and folded in mapping:
            return mapping[folded]
    return ""


def resolve_nature(
    mapping: dict[str, str],
    *,
    company_id: Any = "",
    company_name: Any = "",
    raw_nature: Any = "",
) -> str:
    """Announcement wording wins; the curated map fills the rest."""

    from_announcement = normalize_nature(raw_nature)
    if from_announcement:
        return from_announcement
    plain_id = str(company_id or "")
    if plain_id.startswith("offerbiu:"):
        plain_id = plain_id.split(":", 1)[1]
    return lookup_nature(mapping, plain_id, company_name)
