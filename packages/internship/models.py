"""Value objects for internship classification, requirements, and evaluation.

Four independent axes, deliberately kept orthogonal:

``Track`` / seasons / conversion
    Which channel the posting is, which season(s) it can be done in, and whether
    it offers conversion to a full-time role.  These are *three separate facts*.
    A summer internship that can convert to full-time must be representable, and
    "no conversion offered" must be distinguishable from "not mentioned".

``RequirementStrength``
    How binding a stated condition is: an explicit requirement, a preference, a
    plain mention, or unclear.  This is read from the JD wording and is what
    decides eligibility — never the field name.

``ConditionStatus``
    The comparison result only: met / gap / unknown.  A gap means a definite
    shortfall and is recorded even when the shortfall might be waivable.

``Negotiability``
    Whether the gap could be waived.  Kept separate from ``ConditionStatus`` so
    that "we know there is a 1-month gap" and "we do not know whether they will
    accept it" are both preserved instead of being collapsed into one label.

Eligibility and retention are also separate: a candidate can be
``NOT_QUALIFIED`` for a posting that is still stored, listed, and explained.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .values import degree_rank, text


#: Sentinel for "the JD explicitly says there is no limit" (e.g. 学历不限).
#: A non-``None`` sentinel keeps ``None`` meaning exactly one thing: not stated.
UNLIMITED = "unlimited"


class InternshipModel(BaseModel):
    """Reject unknown fields so a silent typo cannot drop evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Track(StrEnum):
    CAMPUS = "campus"
    INTERNSHIP = "internship"
    SOCIAL = "social"
    UNKNOWN = "unknown"


class InternshipSeason(StrEnum):
    """When the internship happens.  Ordered from specific to generic."""

    SUMMER = "summer"
    WINTER = "winter"
    DAILY = "daily"
    UNSPECIFIED = "unspecified"


class ConversionOpportunity(StrEnum):
    """Whether the posting offers conversion to a full-time role.

    ``UNKNOWN`` is the default.  Absence of a conversion statement is *not*
    evidence that no conversion exists.
    """

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class RequirementStrength(StrEnum):
    """How binding a condition is, derived from the JD wording."""

    MANDATORY = "mandatory"
    PREFERRED = "preferred"
    DESCRIPTIVE = "descriptive"
    UNKNOWN = "unknown"


class ConditionStatus(StrEnum):
    """The comparison outcome for one condition.  No negotiability here."""

    MET = "met"
    GAP = "gap"
    UNKNOWN = "unknown"


class Negotiability(StrEnum):
    """Whether a recorded gap could be waived, judged only from JD wording."""

    NEGOTIABLE = "negotiable"
    NOT_NEGOTIABLE = "not_negotiable"
    UNKNOWN = "unknown"


class Qualification(StrEnum):
    """Eligibility verdict.  Independent from whether the row is retained."""

    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    UNDETERMINED = "undetermined"


class ConditionName(StrEnum):
    DEGREE = "degree"
    COHORT_YEAR = "cohort_year"
    CITY = "city"
    DURATION_MONTHS = "duration_months"
    DAYS_PER_WEEK = "days_per_week"
    START_DATE = "start_date"
    DIRECTION = "direction"
    OTHER_GATE = "other_gate"


#: Conditions that describe *eligibility* rather than *feasibility*.  Only these
#: can produce ``NOT_QUALIFIED``; a duration or attendance shortfall is recorded
#: as a gap but never makes the candidate ineligible, because an internship
#: schedule is routinely negotiated.
QUALIFICATION_GATES: frozenset[ConditionName] = frozenset(
    {ConditionName.DEGREE, ConditionName.COHORT_YEAR, ConditionName.OTHER_GATE}
)


class EvidenceRef(InternshipModel):
    """One auditable reason for a decision, pointing back at the posting text."""

    source: str
    signal: str
    excerpt: str = ""
    reason: str = ""


class RequirementValue(InternshipModel):
    """One extracted requirement, its binding strength, and its source.

    ``value is None`` means "not stated".  ``value == UNLIMITED`` means "the JD
    explicitly states no limit".  Anything else is the parsed requirement.
    """

    value: Any | None = None
    strength: RequirementStrength = RequirementStrength.UNKNOWN
    raw_text: str = ""
    source: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def unlimited(self) -> bool:
        return self.value == UNLIMITED

    @property
    def mandatory(self) -> bool:
        return self.strength is RequirementStrength.MANDATORY


class InternshipRequirements(InternshipModel):
    """Structural internship conditions stated by one posting.

    Skill and technology-direction matching stay in ``packages.matching``; this
    object only covers internship-specific conditions.
    """

    degree: RequirementValue = Field(default_factory=RequirementValue)
    cohort_year: RequirementValue = Field(default_factory=RequirementValue)
    cities: RequirementValue = Field(default_factory=RequirementValue)
    duration_months: RequirementValue = Field(default_factory=RequirementValue)
    days_per_week: RequirementValue = Field(default_factory=RequirementValue)
    start_date: RequirementValue = Field(default_factory=RequirementValue)
    #: Verbatim JD phrases that refuse a candidate class outright.
    explicit_rejections: list[str] = Field(default_factory=list)
    #: Verbatim JD phrases that explicitly allow flexibility.
    explicit_flexibilities: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    def stated(self) -> dict[str, Any]:
        """Return only the conditions the JD actually states."""

        result: dict[str, Any] = {}
        for name in ConditionName:
            value = getattr(self, name.value, None)
            if isinstance(value, RequirementValue) and value.known:
                result[name.value] = value.value
        return result


class SeasonAvailability(InternshipModel):
    """How long the candidate can actually intern, per season."""

    summer_months: float | None = Field(default=None, ge=0.0)
    winter_months: float | None = Field(default=None, ge=0.0)
    daily_months: float | None = Field(default=None, ge=0.0)

    def months_for_season(self, season: InternshipSeason) -> float | None:
        if season is InternshipSeason.SUMMER:
            return self.summer_months
        if season is InternshipSeason.WINTER:
            return self.winter_months
        if season is InternshipSeason.DAILY:
            return self.daily_months
        return None

    def months_for(self, seasons: list[InternshipSeason] | None) -> float | None:
        """Return the best available window for the listed seasons.

        With no stated season, the largest configured window is used, because an
        unspecified internship can be scheduled in whichever break fits.  The
        evaluation always records the candidate figure it compared against.
        """

        values: list[float] = []
        for season in seasons or []:
            months = self.months_for_season(season)
            if months is not None:
                values.append(months)
        if values:
            return max(values)
        configured = [
            months
            for months in (self.daily_months, self.summer_months, self.winter_months)
            if months is not None
        ]
        return max(configured) if configured else None


class InternshipPreferences(InternshipModel):
    """User-declared availability.  Never hard-coded in business logic."""

    graduation: str | None = None
    degree: str | None = None
    preferred_cities: list[str] = Field(default_factory=list)
    availability: SeasonAvailability = Field(default_factory=SeasonAvailability)
    max_days_per_week: int | None = Field(default=None, ge=0, le=7)
    earliest_start: str | None = None
    directions: list[str] = Field(default_factory=list)
    secondary_directions: list[str] = Field(default_factory=list)

    @property
    def graduation_year(self) -> int | None:
        return _year_of(self.graduation)

    @property
    def degree_rank(self) -> int | None:
        return degree_rank(self.degree)


class TrackDecision(InternshipModel):
    track: Track
    #: Only seasons with explicit evidence; empty means "internship, season unstated".
    seasons: list[InternshipSeason] = Field(default_factory=list)
    conversion_opportunity: ConversionOpportunity = ConversionOpportunity.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @property
    def primary_season(self) -> InternshipSeason:
        return self.seasons[0] if self.seasons else InternshipSeason.UNSPECIFIED

    @property
    def reason(self) -> str:
        return self.evidence[0].reason if self.evidence else "未发现可判定的招聘类型证据"


class ConditionResult(InternshipModel):
    """One condition's comparison outcome, binding strength, and evidence."""

    name: ConditionName
    status: ConditionStatus
    strength: RequirementStrength = RequirementStrength.UNKNOWN
    negotiability: Negotiability = Negotiability.UNKNOWN
    required: Any | None = None
    candidate: Any | None = None
    jd_excerpt: str = ""
    note: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mandatory_failure(self) -> bool:
        """A gate the JD requires and the candidate definitely fails."""

        return (
            self.name in QUALIFICATION_GATES
            and self.strength is RequirementStrength.MANDATORY
            and self.status is ConditionStatus.GAP
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confirmation_required(self) -> bool:
        """A definite gap whose waivability nobody has established yet."""

        return (
            self.status is ConditionStatus.GAP
            and self.negotiability is Negotiability.UNKNOWN
        )


class MatchEvaluation(InternshipModel):
    """Aggregated evaluation of one posting against one candidate profile.

    ``qualification`` answers "does this candidate meet the stated eligibility
    gates".  ``retained`` answers "does the row stay in the database" and is a
    separate policy: a not-qualified posting is still stored and explained, never
    silently dropped.
    """

    qualification: Qualification
    conditions: list[ConditionResult] = Field(default_factory=list)
    mandatory_failures: list[ConditionResult] = Field(default_factory=list)
    gaps: list[ConditionResult] = Field(default_factory=list)
    unknowns: list[ConditionResult] = Field(default_factory=list)
    confirmation_required: list[ConditionResult] = Field(default_factory=list)
    #: Retention policy output.  Qualification logic never sets this to False.
    retained: bool = True
    notes: list[str] = Field(default_factory=list)

    def by_name(self, name: ConditionName) -> ConditionResult | None:
        return next((item for item in self.conditions if item.name is name), None)

    def to_payload(self) -> dict[str, Any]:
        """JSON-shaped value for the ``match_conditions`` column."""

        return self.model_dump(mode="json")


def _year_of(value: Any) -> int | None:
    """Extract a 4-digit year from values such as ``2028-06`` or ``2028届``."""

    for token in "".join(char if char.isdigit() else " " for char in text(value)).split():
        if len(token) == 4:
            return int(token)
    return None


__all__ = [
    "ConditionName",
    "ConditionResult",
    "ConditionStatus",
    "ConversionOpportunity",
    "EvidenceRef",
    "InternshipModel",
    "InternshipPreferences",
    "InternshipRequirements",
    "InternshipSeason",
    "MatchEvaluation",
    "Negotiability",
    "QUALIFICATION_GATES",
    "Qualification",
    "RequirementStrength",
    "RequirementValue",
    "SeasonAvailability",
    "Track",
    "TrackDecision",
    "UNLIMITED",
]
