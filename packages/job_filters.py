"""City and cohort filters shared by the web button and the CLI.

Both filters are display-neutral narrowing rules: they decide which
announcements a collection keeps, not how a job is rendered.  Keeping them in
one module means the page button, the wizard and ``collect_recent.py`` can
never disagree about what "只看郑州" or "2027 届" means.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

#: 行政区划后缀。「郑州市」和「郑州」必须是同一个地方，否则用户输入哪个都对不上。
_PLACE_SUFFIXES = ("特别行政区", "自治州", "地区", "市", "盟")

_SPLIT_RE = re.compile(r"[、,，/|;；\s]+")

#: 实习不按届别卡：寒假实习、日常实习、明年春季实习都该被收进来，
#: 它们往往同时写着 2026/2027，或者干脆不写届别。
_COHORT_EXEMPT_TYPES = frozenset({"实习"})


def normalize_place(value: Any) -> str:
    """Normalise a city name for comparison: 郑州市 → 郑州."""

    text = str(value or "").strip().replace(" ", "")
    for suffix in _PLACE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text.casefold()


def parse_places(value: Any) -> tuple[str, ...]:
    """Accept ``"郑州"``, ``"郑州,洛阳"`` or a list; return normalised names."""

    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = _SPLIT_RE.split(str(value or ""))
    return tuple(dict.fromkeys(item for item in (normalize_place(part) for part in raw) if item))


def matches_city(item: Mapping[str, Any], query: Any) -> bool:
    """Does this announcement list any of the wanted cities?

    Matching is prefix-based on the *normalised* name so 「郑州」 also catches
    「郑州航空港区」, while an unrelated city such as 「郑州路」-style noise
    cannot appear because locations are real place names.
    """

    wanted = parse_places(query)
    if not wanted:
        return True
    locations = item.get("locations")
    if not isinstance(locations, (list, tuple)) or not locations:
        return False
    places = {normalize_place(location) for location in locations if str(location).strip()}
    if not places:
        return False
    return any(
        place == want or place.startswith(want)
        for want in wanted
        for place in places
    )


def _as_year(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def matches_cohort(item: Mapping[str, Any], cohort: Any) -> bool:
    """Is this announcement aimed at the given graduating class?

    ``cohort=0`` (or empty) means "any class".  Internships are always kept:
    a 寒假/日常/春季实习 is useful to a 2027 graduate whether or not the
    announcement bothered to state a class year.
    """

    if not cohort:
        return True
    try:
        wanted = int(cohort)
    except (TypeError, ValueError):
        return True
    if wanted <= 0:
        return True
    if str(item.get("recruitType") or "").strip() in _COHORT_EXEMPT_TYPES:
        return True
    years = item.get("targetYears")
    if not isinstance(years, (list, tuple)) or not years:
        return False
    return any(_as_year(year) == wanted for year in years)


def selected_place_label(query: Any) -> str:
    """Human label for reports: 郑州 / 郑州、洛阳 / 全部城市."""

    places = parse_places(query)
    return "、".join(places) if places else "全部城市"


def cohort_label(cohort: Any) -> str:
    try:
        value = int(cohort)
    except (TypeError, ValueError):
        return "不限届别"
    return f"{value} 届" if value > 0 else "不限届别"


__all__ = [
    "cohort_label",
    "matches_city",
    "matches_cohort",
    "normalize_place",
    "parse_places",
    "selected_place_label",
]
