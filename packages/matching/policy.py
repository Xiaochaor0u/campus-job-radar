"""Screening policy: which recruitment track and cohort a run is screening for.

Introduced so the internship mode can be switched on without touching the campus
defaults.  ``ScreeningPolicy()`` reproduces the historical behaviour exactly:
require a confirmed 2027 cohort, reject internships, use the four original
directions.  The dataclass is frozen so a policy can be shared by a pipeline and
its worker threads, and so a checkpoint can record it by value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import (
    Direction,
    INTERNSHIP_DIRECTIONS,
    LEGACY_DIRECTIONS,
)


class ScreeningMode(StrEnum):
    #: Historical behaviour: formal campus postings of the 2027 cohort.
    CAMPUS = "campus"
    #: Internship postings only; no cohort requirement.
    INTERNSHIP = "internship"
    #: Internships plus campus postings, each judged by its own rules.
    ALL = "all"


class CohortPolicy(StrEnum):
    #: Keep the original gate: cohort == 2027 and cohort_status == confirmed.
    REQUIRE_CONFIRMED_2027 = "require_confirmed_2027"
    #: Do not gate on the graduation cohort at all.
    ANY = "any"


@dataclass(frozen=True, slots=True)
class ScreeningPolicy:
    mode: ScreeningMode = ScreeningMode.CAMPUS
    #: ``None`` means "derive from the mode", so callers cannot accidentally ask
    #: for an internship run that still requires the 2027 cohort.
    cohort: CohortPolicy | None = None

    def __post_init__(self) -> None:
        """Reject contradictory configurations.

        ``frozen=True`` only makes the object immutable; it says nothing about
        whether the combination makes sense.  The one genuinely contradictory
        pair is an internship run that still requires the confirmed 2027 campus
        cohort: internships are not screened by graduation cohort, and a JD that
        *does* name a cohort year is judged independently by the internship
        condition evaluation, not by this gate.
        """

        if not isinstance(self.mode, ScreeningMode):
            raise ValueError(f"mode must be a ScreeningMode, got {self.mode!r}")
        if self.cohort is not None and not isinstance(self.cohort, CohortPolicy):
            raise ValueError(f"cohort must be a CohortPolicy, got {self.cohort!r}")
        if (
            self.mode is ScreeningMode.INTERNSHIP
            and self.cohort is CohortPolicy.REQUIRE_CONFIRMED_2027
        ):
            raise ValueError(
                "internship mode cannot require the confirmed 2027 campus cohort; "
                "a JD-level cohort requirement is evaluated as an internship "
                "condition instead"
            )

    @property
    def cohort_policy(self) -> CohortPolicy:
        if self.cohort is not None:
            return self.cohort
        if self.mode is ScreeningMode.INTERNSHIP:
            return CohortPolicy.ANY
        return CohortPolicy.REQUIRE_CONFIRMED_2027

    @property
    def requires_confirmed_2027(self) -> bool:
        return self.cohort_policy is CohortPolicy.REQUIRE_CONFIRMED_2027

    @property
    def accepts_internships(self) -> bool:
        """Whether internship postings may pass the internship gate."""

        return self.mode is not ScreeningMode.CAMPUS

    @property
    def requires_internship(self) -> bool:
        """Whether non-internship postings are rejected outright."""

        return self.mode is ScreeningMode.INTERNSHIP

    @property
    def direction_fallback(self) -> tuple[Direction, ...]:
        """Directions used when the profile does not name any.

        Campus keeps the four original directions.  Both internship-accepting
        modes get the superset, because a run that accepts internships must also
        be able to recognise a Python internship.
        """

        if self.mode is ScreeningMode.CAMPUS:
            return LEGACY_DIRECTIONS
        return INTERNSHIP_DIRECTIONS

    @property
    def evaluates_conditions(self) -> bool:
        """Whether this run should build the internship condition evaluation."""

        return self.accepts_internships

    @property
    def allowed_batches(self) -> tuple[str, ...]:
        """Batch labels the crawler audit may accept.

        Campus keeps the historical formal/early gate.  A run that accepts
        internships must not drop rows by their batch label, because internships
        are identified by the classifier and often carry no campus batch at all.
        """

        if self.mode is ScreeningMode.CAMPUS:
            return ("formal", "early")
        return ("formal", "early", "internship", "unknown")


#: The historical behaviour, used whenever no policy is supplied.
DEFAULT_POLICY = ScreeningPolicy()


def resolve_policy(
    policy: ScreeningPolicy | None = None,
    *,
    include_internships: bool = False,
) -> ScreeningPolicy:
    """Return the effective policy.

    ``include_internships`` is the earlier boolean switch kept for backward
    compatibility with existing call sites and tests.  It maps onto
    ``ScreeningMode.ALL`` because "do not reject internships" says nothing about
    whether campus postings should keep being screened.
    """

    if policy is not None:
        return policy
    if include_internships:
        return ScreeningPolicy(mode=ScreeningMode.ALL)
    return DEFAULT_POLICY


def policy_for_mode(mode: str | ScreeningMode | None) -> ScreeningPolicy:
    """Build a policy from a configuration string, defaulting to campus."""

    if mode is None:
        return DEFAULT_POLICY
    return ScreeningPolicy(mode=ScreeningMode(str(mode)))


__all__ = [
    "CohortPolicy",
    "DEFAULT_POLICY",
    "ScreeningMode",
    "ScreeningPolicy",
    "policy_for_mode",
    "resolve_policy",
]
