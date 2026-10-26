"""Watch rules: answer "which postings appeared in the last N days that I care about".

Design notes:

* **Source of truth is ``job_events``**, not the job table.  A posting's "new"
  event carries the exact detection time plus the title/city/track needed to
  filter, so a rule never has to re-scan the whole catalog.
* **Conditions are AND-ed, values OR-ed.**  An empty field means "no constraint",
  so "只看实习" is expressible without inventing a keyword.
* **Synonyms widen recall.**  A posting titled "Agent 研发" never contains the
  word "算法", so keyword-only matching would miss it; the synonym table maps a
  user's word onto the variants that actually appear in titles.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from packages.storage import JobEvent, Storage, WatchRule

DEFAULT_WINDOW = "today"
VALID_WINDOWS = ("today", "3d", "7d", "30d")
MAX_RULES = 50

#: User word -> close variants that appear in job titles.
#:
#: Kept deliberately short: widening "算法" to AI/NLP/CV/SLAM turned 1.2k rows
#: into "matches" out of 4.7k, which is noise, not a filter.  Each entry lists
#: true synonyms / rephrasings, not sub-domains.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "算法": ("机器学习", "深度学习"),
    "机器学习": ("算法", "深度学习"),
    "大模型": ("llm", "agent", "rag", "智能体"),
    "后端": ("服务端", "java开发", "golang"),
    "前端": ("web前端", "react", "vue"),
    "客户端": ("android", "ios", "移动端"),
    "测试": ("测开", "qa", "测试开发"),
    "数据": ("数据分析", "数据开发", "数仓"),
    "运维": ("sre", "devops"),
    "安全": ("安全工程师", "渗透"),
    "产品": ("产品经理", "产品设计"),
    "运营": ("运营专员", "用户运营", "内容运营"),
    "设计": ("ui", "ux", "交互设计", "视觉设计"),
    "硬件": ("硬件工程师", "电路", "嵌入式"),
    "嵌入式": ("单片机", "固件", "驱动开发"),
    "机器人": ("具身智能", "运动控制"),
    "自动驾驶": ("感知算法", "规划控制"),
    "游戏": ("unity", "ue4", "ue5", "引擎开发"),
    "芯片": ("数字前端", "芯片验证", "ic设计"),
}


def _clean_list(values: Any, limit: int = 30) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [part for part in values.replace("，", ",").split(",")]
    if not isinstance(values, Sequence) or isinstance(values, (bytes,)):
        return []
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if text and text not in result:
            result.append(text[:60])
    return result[:limit]


def expand_terms(values: Iterable[str], *, include_original: bool = False) -> list[str]:
    """Widen user terms with the synonym table (lower-cased, de-duplicated).

    ``include_original=False`` returns only the *other* spellings, which lets
    callers tell an exact title hit apart from a synonym hit.
    """

    expanded: list[str] = []
    for value in values:
        raw = str(value or "").strip().casefold()
        if not raw:
            continue
        if include_original and raw not in expanded:
            expanded.append(raw)
        for key, aliases in _SYNONYMS.items():
            if key in raw or raw in key:
                for alias in aliases:
                    token = alias.casefold()
                    if token != raw and token not in expanded:
                        expanded.append(token)
    return expanded


def window_start(window: str, now: datetime | None = None) -> datetime:
    """Local-day-aligned start of the window, returned in UTC."""

    normalized = (window or DEFAULT_WINDOW).strip().casefold()
    if normalized not in VALID_WINDOWS:
        raise ValueError(f"window must be one of: {', '.join(VALID_WINDOWS)}")
    current = now or datetime.now(timezone.utc)
    local = current.astimezone()
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized != "today":
        start_local -= timedelta(days=int(normalized[:-1]) - 1)
    return start_local.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class WatchCondition:
    """Filter values; empty sequence = unconstrained."""

    keywords: tuple[str, ...] = ()
    directions: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    track: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | Any) -> "WatchCondition":
        def get(name: str, default: Any = None) -> Any:
            if isinstance(payload, Mapping):
                return payload.get(name, default)
            return getattr(payload, name, default)

        track = str(get("track") or "").strip().casefold()
        return cls(
            keywords=tuple(_clean_list(get("keywords"))),
            directions=tuple(_clean_list(get("directions"))),
            cities=tuple(_clean_list(get("cities"))),
            track=track if track in {"campus", "internship"} else "",
        )

    @property
    def empty(self) -> bool:
        return not (self.keywords or self.directions or self.cities or self.track)


@dataclass(frozen=True, slots=True)
class WatchHit:
    job_id: str
    company_id: str
    company_name: str
    title: str
    city: str | None
    track: str
    detail_url: str
    detected_at: str
    matched: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "title": self.title,
            "city": self.city,
            "track": self.track,
            "detail_url": self.detail_url,
            "detected_at": self.detected_at,
            "matched": list(self.matched),
        }


@dataclass(frozen=True, slots=True)
class WatchResult:
    window: str
    since: str
    scanned_events: int = 0
    total_hits: int = 0
    condition: Mapping[str, Any] = field(default_factory=dict)
    rule_id: str | None = None
    rule_name: str = ""
    hits: tuple[WatchHit, ...] = ()
    by_company: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "since": self.since,
            "scanned_events": self.scanned_events,
            "total_hits": self.total_hits,
            "condition": dict(self.condition),
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "hits": [hit.to_dict() for hit in self.hits],
            "by_company": [{"company": name, "count": count} for name, count in self.by_company],
        }


def _matches(
    event: JobEvent, condition: WatchCondition
) -> tuple[bool, tuple[str, ...]]:
    """Decide whether one event passes the condition, with the reasons."""

    title = str(event.title or "").casefold()
    city = str(event.city or "").casefold()
    track = str(event.track or "").casefold()

    reasons: list[str] = []

    if condition.track and track != condition.track:
        return False, ()

    if condition.cities:
        if not any(term.casefold() in city for term in condition.cities):
            return False, ()
        reasons.append("城市")

    if condition.directions:
        exact = [term.casefold() for term in condition.directions if term.casefold() in title]
        if exact:
            reasons.append("方向")
        elif any(term in title for term in expand_terms(condition.directions)):
            reasons.append("方向近义")
        else:
            return False, ()

    if condition.keywords:
        exact = [term.casefold() for term in condition.keywords if term.casefold() in title]
        if exact:
            reasons.append("关键词")
        elif any(term in title for term in expand_terms(condition.keywords)):
            reasons.append("关键词近义")
        else:
            return False, ()

    return True, tuple(reasons)


def list_rules(storage: Storage, *, enabled_only: bool = False) -> list[WatchRule]:
    storage.initialize()
    with storage.session() as session:
        query = session.query(WatchRule)
        if enabled_only:
            query = query.filter(WatchRule.enabled.is_(True))
        return list(query.order_by(WatchRule.created_at.asc()).all())


def save_rule(
    storage: Storage,
    condition: WatchCondition,
    *,
    name: str,
    rule_id: str | None = None,
    enabled: bool = True,
) -> WatchRule:
    """Create or update a rule; keeps the id stable when updating."""

    storage.initialize()
    label = " ".join(str(name or "").split())[:120]
    if not label:
        raise ValueError("订阅名称不能为空")
    if condition.empty:
        raise ValueError("订阅至少要有一个筛选条件（关键词 / 方向 / 城市 / 类型）")

    with storage.session() as session:
        existing = session.get(WatchRule, rule_id) if rule_id else None
        if existing is None:
            count = session.query(WatchRule).count()
            if count >= MAX_RULES:
                raise ValueError(f"最多保存 {MAX_RULES} 条订阅，请先删除不用的")
            row = WatchRule(
                id=rule_id or uuid4().hex,
                name=label,
                keywords=list(condition.keywords),
                directions=list(condition.directions),
                cities=list(condition.cities),
                track=condition.track,
                enabled=enabled,
            )
            session.add(row)
        else:
            row = existing
            row.name = label
            row.keywords = list(condition.keywords)
            row.directions = list(condition.directions)
            row.cities = list(condition.cities)
            row.track = condition.track
            row.enabled = enabled
        session.commit()
        session.refresh(row)
        return row


def delete_rule(storage: Storage, rule_id: str) -> bool:
    storage.initialize()
    with storage.session() as session:
        row = session.get(WatchRule, rule_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def run_watch(
    storage: Storage,
    condition: WatchCondition,
    *,
    window: str = DEFAULT_WINDOW,
    limit: int = 100,
    rule_id: str | None = None,
    rule_name: str = "",
    now: datetime | None = None,
) -> WatchResult:
    """Collect ``new`` events inside the window that pass the condition."""

    if not isinstance(storage, Storage):
        raise TypeError("storage must be a Storage instance")
    if limit < 1:
        raise ValueError("limit must be positive")
    storage.initialize()

    current = now or datetime.now(timezone.utc)
    since = window_start(window, current)

    with storage.session() as session:
        events = list(
            session.scalars(
                select(JobEvent)
                .where(
                    JobEvent.event_type == "new",
                    JobEvent.detected_at >= since,
                    JobEvent.detected_at <= current,
                )
                .order_by(JobEvent.detected_at.desc())
            ).all()
        )

    hits: list[WatchHit] = []
    company_counts: dict[str, int] = {}
    for event in events:
        ok, reasons = _matches(event, condition)
        if not ok:
            continue
        company_name = event.company_name or str(event.company_id or "").replace("offerbiu:", "")
        company_counts[company_name] = company_counts.get(company_name, 0) + 1
        hits.append(
            WatchHit(
                job_id=event.job_id,
                company_id=event.company_id,
                company_name=company_name,
                title=event.title,
                city=event.city,
                track=event.track,
                detail_url=event.detail_url,
                detected_at=event.detected_at.isoformat() if event.detected_at else "",
                matched=reasons,
            )
        )

    by_company = tuple(
        sorted(company_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    # Exact title hits first; synonyms are kept but ranked below.  Python's sort
    # is stable, so recency order survives inside each group.
    hits.sort(key=lambda hit: any("近义" in reason for reason in hit.matched))
    return WatchResult(
        window=(window or DEFAULT_WINDOW),
        since=since.isoformat(),
        scanned_events=len(events),
        total_hits=len(hits),
        condition={
            "keywords": list(condition.keywords),
            "directions": list(condition.directions),
            "cities": list(condition.cities),
            "track": condition.track,
        },
        rule_id=rule_id,
        rule_name=rule_name,
        hits=tuple(hits[:limit]),
        by_company=by_company[:15],
    )


def latest_event_at(storage: Storage) -> str | None:
    """When the newest event was recorded — the honest "data freshness" signal."""

    from sqlalchemy import func

    storage.initialize()
    with storage.session() as session:
        value = session.scalar(select(func.max(JobEvent.detected_at)))
    return value.isoformat() if value else None


__all__ = [
    "DEFAULT_WINDOW",
    "MAX_RULES",
    "VALID_WINDOWS",
    "WatchCondition",
    "WatchHit",
    "WatchResult",
    "delete_rule",
    "expand_terms",
    "latest_event_at",
    "list_rules",
    "run_watch",
    "save_rule",
    "window_start",
]
