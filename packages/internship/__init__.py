"""Internship recruitment domain for InternOps.

This package is the single source of truth for *internship-specific* business
facts: which recruitment track a posting is, which season(s) it can be done in,
whether it offers conversion, what structural conditions its JD states and how
binding each one is, and how a candidate's declared availability compares.

Rules enforced here (see docs/internops/TASK-002a-report.md):

* ``None`` always means "the JD does not state it".  It never means "the
  candidate does not satisfy it".  Missing information is ``UNKNOWN``.
* Whether a stated condition can disqualify someone is decided by
  ``RequirementStrength`` read from the JD wording, not by the field name.
* A definite shortfall is recorded as ``ConditionStatus.GAP`` even when it might
  be waivable; waivability is a separate ``Negotiability`` value.
* Qualification and retention are separate outputs: a posting the candidate is
  not qualified for is still stored and explained, never dropped.

Skill and technology-direction matching is deliberately not duplicated here:
``packages.matching`` already classifies directions and scores skills.
"""

from .classification import (
    classify_track,
    internship_reason,
    is_internship,
    is_social,
)
from .evaluation import (
    MatchEvaluation,
    evaluate_conditions,
    preferences_from_profile,
)
from .models import (
    ConditionName,
    ConditionResult,
    ConditionStatus,
    ConversionOpportunity,
    EvidenceRef,
    InternshipPreferences,
    InternshipRequirements,
    InternshipSeason,
    MatchEvaluation,
    Negotiability,
    Qualification,
    RequirementStrength,
    RequirementValue,
    SeasonAvailability,
    Track,
    TrackDecision,
    UNLIMITED,
)
from .requirements import extract_requirements

__all__ = [
    "ConditionName",
    "ConditionResult",
    "ConditionStatus",
    "ConversionOpportunity",
    "EvidenceRef",
    "InternshipPreferences",
    "InternshipRequirements",
    "InternshipSeason",
    "MatchEvaluation",
    "Negotiability",
    "Qualification",
    "RequirementStrength",
    "RequirementValue",
    "SeasonAvailability",
    "Track",
    "TrackDecision",
    "UNLIMITED",
    "classify_track",
    "evaluate_conditions",
    "extract_requirements",
    "internship_reason",
    "is_internship",
    "is_social",
    "preferences_from_profile",
]
