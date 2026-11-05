"""Company headcount, shown only where a public source states it.

招聘公告本身几乎不写公司规模；这一层补的是「这家公司大概多大」这一个判断。
两层数据，按可信度排序：

1. ``config/company_size.yaml`` —— 人工核对，逐条带出处和数据截止时间；
2. ``config/company_size_listed.json`` —— 从东方财富 F10 抓的上市公司最新披露
   员工数（``scripts/fetch_company_size.py`` 生成），覆盖约 1.3 万家。

三条硬规则：

* **没有来源就不写。** 宁可页面上什么都不显示，也不给一个看起来专业的错数字。
* **口径统一。** 只收「员工总数」，不混用参保人数、劳务派遣、外包人数。
* **同名只做精确匹配。** 名字归一化后仍对不上就不显示——宁可漏，不可错。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CompanySize:
    """One company's headcount, with the evidence needed to trust it."""

    employees: int
    as_of: str = ""
    source: str = ""

    @property
    def label(self) -> str:
        """Compact display form: 1.6 万人 / 5,034 人."""

        return format_headcount(self.employees)

    def describe(self) -> str:
        """Tooltip: where the number comes from and when it was true."""

        parts = [part for part in (self.as_of, self.source) if part]
        return " · ".join(parts)


def format_headcount(count: int) -> str:
    """Chinese readers think in 万 above ten thousand, and in exact people below."""

    if count >= 10000:
        value = f"{count / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{value} 万人"
    return f"{count:,} 人"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


#: 只剥"法律实体后缀"。"集团 / 控股 / 股份"故意不剥：
#: 「中国稀土集团」和上市公司「中国稀土」是两家不同主体，剥掉"集团"就会
#: 把集团的岗位标成子公司的人数——宁可匹配不上，也不能张冠李戴。
_SUFFIXES = (
    "集团股份有限公司",
    "股份有限公司",
    "有限责任公司",
    "有限公司",
)


def normalize_company_name(value: Any) -> str:
    """Normalise a company name for exact matching across sources.

    Deliberately conservative: strip spaces, unify brackets, drop registration
    suffixes and an ``ST`` prefix.  No fuzzy matching — a wrong headcount is
    worse than a missing one.
    """

    text = str(value or "").strip()
    text = text.replace("　", "").replace(" ", "")
    text = text.replace("（", "(").replace("）", ")")
    for _ in range(2):  # 有些名字是"集团股份有限公司"，要剥两层
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
    for prefix in ("*ST", "ST"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):]
            break
    return text.casefold()


def _index_keys(value: Any) -> tuple[str, ...]:
    """Keys an entry is reachable by.

    Besides the normalised name, a Chinese name ending in a single A/B/H also
    gets the stripped variant (``万科A`` → ``万科``), because that letter is an
    exchange suffix.  English names that happen to end in "a" (Tesla) are left
    alone.
    """

    normalized = normalize_company_name(value)
    if not normalized:
        return ()
    keys = [normalized]
    if (
        len(normalized) > 2
        and normalized[-1] in {"a", "b", "h"}
        and "\u4e00" <= normalized[-2] <= "\u9fff"
    ):
        keys.append(normalized[:-1])
    return tuple(dict.fromkeys(keys))


# ---------------------------------------------------------------------------
# Layer 1: hand-checked YAML
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _read_yaml(path_text: str, mtime: float) -> dict[str, CompanySize]:
    import yaml

    target = Path(path_text)
    if not target.is_file():
        return {}
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    rows = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}

    mapping: dict[str, CompanySize] = {}
    for raw_key, raw_value in rows.items():
        count: int | None = None
        as_of = ""
        source = ""
        aliases: tuple[str, ...] = ()
        if isinstance(raw_value, dict):
            value = raw_value.get("employees", raw_value.get("count"))
            try:
                count = int(value) if value is not None else None
            except (TypeError, ValueError):
                count = None
            as_of = str(raw_value.get("as_of") or "").strip()
            source = str(raw_value.get("source") or "").strip()
            extra = raw_value.get("aliases") or ()
            if isinstance(extra, (list, tuple)):
                aliases = tuple(str(item) for item in extra)
        else:
            try:
                count = int(raw_value)
            except (TypeError, ValueError):
                count = None
        if not count or count <= 0:
            continue
        info = CompanySize(employees=count, as_of=as_of, source=source)
        for key in (raw_key, *aliases):
            for normalized in _index_keys(key):
                mapping[normalized] = info
    return mapping


def load_size_map_yaml(path: Path | str) -> dict[str, CompanySize]:
    """Hand-checked layer; missing file simply means no badges from this layer."""

    target = Path(path)
    if not target.is_file():
        return {}
    return dict(_read_yaml(str(target), _mtime(target)))


# ---------------------------------------------------------------------------
# Layer 2: fetched listed-company cache
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _read_cache(path_text: str, mtime: float) -> dict[str, CompanySize]:
    """Parse the ~2 MB cache once per file version, not once per request."""

    import json

    target = Path(path_text)
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}

    mapping: dict[str, CompanySize] = {}
    for raw_key, raw_value in rows.items():
        if not isinstance(raw_value, dict):
            continue
        try:
            count = int(raw_value.get("employees"))
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        info = CompanySize(
            employees=count,
            as_of=str(raw_value.get("as_of") or ""),
            source=str(raw_value.get("source") or ""),
        )
        for normalized in _index_keys(raw_key):
            mapping[normalized] = info
    return mapping


def load_size_cache(path: Path | str) -> dict[str, CompanySize]:
    """Fetched layer (see scripts/fetch_company_size.py)."""

    target = Path(path)
    if not target.is_file():
        return {}
    return dict(_read_cache(str(target), _mtime(target)))


# ---------------------------------------------------------------------------
# Merge + lookup
# ---------------------------------------------------------------------------

def load_size_map(
    path: Path | str, cache_path: Path | str | None = None
) -> dict[str, CompanySize]:
    """Both layers, hand-checked YAML winning on conflicts."""

    mapping = load_size_cache(cache_path) if cache_path else {}
    mapping.update(load_size_map_yaml(path))
    return mapping


def resolve_size(
    mapping: dict[str, CompanySize],
    *,
    company_id: Any = "",
    company_name: Any = "",
) -> CompanySize | None:
    """Look up by company name first, then by the id with its source prefix removed."""

    plain_id = str(company_id or "").strip()
    if plain_id.startswith("offerbiu:"):
        plain_id = plain_id.split(":", 1)[1]
    for key in (company_name, plain_id):
        for normalized in _index_keys(key):
            if normalized in mapping:
                return mapping[normalized]
    return None


__all__ = [
    "CompanySize",
    "format_headcount",
    "load_size_cache",
    "load_size_map",
    "load_size_map_yaml",
    "normalize_company_name",
    "resolve_size",
]
