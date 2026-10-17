"""Small shared helpers for the internship package.

Kept in one place because the three modules used to carry near-identical private
copies of ``_field``/``_text``/``_evidence`` and, more importantly, two slightly
different "strip the applicant's own internship experience" regexes.  Two copies
of a *semantic* rule is exactly the duplication this package exists to remove.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any


def field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a mapping or an attribute-style object."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def compact(value: Any) -> str:
    return " ".join(text(value).split())


def excerpt(value: Any, limit: int = 180) -> str:
    """Collapse whitespace so a JD fragment stays on one readable line."""

    return compact(value)[:limit]


#: Phrases about the candidate's *own* internship experience.  They describe the
#: applicant rather than the posting, so every detector removes them first.
EXPERIENCE_MENTION_RE = re.compile(
    r"[^。；;，,\n]{0,10}实习[^。；;，,\n]{0,8}(?:经历|经验|背景|项目)"
)


def strip_experience_mentions(value: str) -> str:
    return EXPERIENCE_MENTION_RE.sub(" ", value)


#: A commitment phrase is only a signal when it is not negated.  "不要求保证稳定
#: 的实习投入时间" describes what the role does *not* require, so it must not make
#: the posting look like an internship.  Kept to explicit phrases so that
#: "实习期不少于3个月" (which contains 不) is not mistaken for a negation.
NEGATED_REQUIREMENT_RE = re.compile(
    r"(?:不要求|无需|无须|不需|不用|不须|不强制|不硬性|免除)\s*$"
)


def is_negated(value: str, start: int, *, window: int = 10) -> bool:
    """Return True when a requirement is preceded by an explicit negation."""

    return bool(NEGATED_REQUIREMENT_RE.search(value[max(0, start - window) : start]))


DEGREE_RANK: dict[str, int] = {
    "大专": 1,
    "专科": 1,
    "本科": 2,
    "学士": 2,
    "硕士": 3,
    "研究生": 3,
    "博士": 4,
}

_DEGREE_ALIASES: dict[str, int] = {
    **DEGREE_RANK,
    "associate": 1,
    "college": 1,
    "bachelor": 2,
    "undergraduate": 2,
    "master": 3,
    "postgraduate": 3,
    "phd": 4,
    "ph.d": 4,
    "doctor": 4,
    "doctorate": 4,
}


def degree_rank(value: Any) -> int | None:
    """Map Chinese and English degree wording onto one ordinal scale."""

    label = text(value).casefold()
    if not label:
        return None
    if label in _DEGREE_ALIASES:
        return _DEGREE_ALIASES[label]
    for alias, rank in _DEGREE_ALIASES.items():
        if alias in label:
            return rank
    return None


__all__ = [
    "DEGREE_RANK",
    "EXPERIENCE_MENTION_RE",
    "NEGATED_REQUIREMENT_RE",
    "compact",
    "degree_rank",
    "excerpt",
    "field",
    "is_negated",
    "strip_experience_mentions",
    "text",
]
