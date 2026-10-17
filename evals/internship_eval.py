"""Human-reviewable evaluation set for internship analysis.

Run it with ``python -m evals.internship_eval`` to print one row per case: the
posting, what the deterministic layer concluded, and what it was expected to
conclude.  The point is not a score — it is that a person can read each row and
disagree.

Two kinds of case:

``synthetic_cases``
    Hand-written JD text with a precise expected verdict, one per rule we care
    about (duration gap, degree gate, cohort gate, unknown conditions).

``real_cases``
    Real Tencent internship JDs recorded in
    ``tests/fixtures/tencent_intern_sample.json``.  Their expectations are the
    invariants we can justify *a priori* (it is an internship, the JD has both
    sections, at least one condition was extracted with evidence) — not a copy of
    whatever the code currently outputs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.internship.classification import classify_track
from packages.internship.evaluation import evaluate_conditions, preferences_from_profile
from packages.internship.requirements import extract_requirements

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
EVAL_SET = FIXTURES / "internship_eval_set.json"
SAMPLES = FIXTURES / "tencent_intern_sample.json"

PROFILE: dict[str, Any] = {
    "degree": "本科",
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["西安", "郑州"],
        "available_months": {"summer": 2, "winter": 1.5},
        "max_days_per_week": 3,
    },
}


def _condition(evaluation: Any, name: str) -> dict[str, Any]:
    for item in evaluation.conditions:
        if item.name.value == name:
            return item.model_dump(mode="json")
    return {}


def _evaluate(job: dict[str, Any]) -> dict[str, Any]:
    decision = classify_track(job)
    requirements = extract_requirements(job)
    evaluation = evaluate_conditions(
        requirements,
        preferences_from_profile(PROFILE),
        seasons=list(decision.seasons),
    )
    return {
        "track": decision.track.value,
        "seasons": [season.value for season in decision.seasons],
        "qualification": evaluation.qualification.value,
        "retained": evaluation.retained,
        "duration": _condition(evaluation, "duration_months"),
        "degree": _condition(evaluation, "degree"),
        "cohort": _condition(evaluation, "cohort_year"),
        "conditions_with_evidence": sum(
            1 for item in evaluation.conditions if item.jd_excerpt
        ),
        "requirement_evidence": len(requirements.evidence),
    }


def _check_synthetic(case: dict[str, Any]) -> tuple[bool, list[str]]:
    job = {
        "title": case["title"],
        "city": case.get("city"),
        # No implicit internship hints: each case declares the metadata the real
        # posting would carry, so the verdict has to come from title/JD evidence.
        "job_type": case.get("job_type", ""),
        "batch": case.get("batch", ""),
        "jd_raw": case["jd_raw"],
    }
    result = _evaluate(job)
    problems: list[str] = []
    expect = case["expect"]
    if "track" in expect and result["track"] != expect["track"]:
        problems.append(f"track={result['track']} expected {expect['track']}")
    if "qualification" in expect and result["qualification"] != expect["qualification"]:
        problems.append(
            f"qualification={result['qualification']} expected {expect['qualification']}"
        )
    if "duration_status" in expect:
        if result["duration"].get("status") != expect["duration_status"]:
            problems.append(
                f"duration.status={result['duration'].get('status')} "
                f"expected {expect['duration_status']}"
            )
    if "duration_negotiability" in expect:
        if result["duration"].get("negotiability") != expect["duration_negotiability"]:
            problems.append("duration.negotiability mismatch")
    if "retained" in expect and result["retained"] != expect["retained"]:
        problems.append(f"retained={result['retained']} expected {expect['retained']}")
    return not problems, problems


def _check_real(case: dict[str, Any], samples: dict[str, Any]) -> tuple[bool, list[str]]:
    detail = next(
        (item["detail"] for item in samples["details"] if item["post_id"] == case["post_id"]),
        None,
    )
    if detail is None:
        return False, [f"post {case['post_id']} missing from recorded samples"]
    row = next(
        (item["list_row"] for item in samples["details"] if item["post_id"] == case["post_id"]),
        {},
    )
    duty = detail.get("desc") or ""
    requirement = detail.get("request") or ""
    job = {
        "title": row.get("positionTitle") or detail.get("positionTitle") or "",
        "city": row.get("workCities"),
        "job_type": detail.get("projectName") or "实习",
        "batch": "internship",
        "jd_raw": f"岗位职责\n{duty}\n\n任职要求\n{requirement}",
    }
    result = _evaluate(job)
    problems: list[str] = []
    if result["track"] != "internship":
        problems.append(f"track={result['track']}")
    if not duty or not requirement:
        problems.append("recorded sample lacks a JD section")
    if result["requirement_evidence"] < 1:
        problems.append("no requirement extracted")
    return not problems, problems


def run_eval(*, verbose: bool = False) -> list[dict[str, Any]]:
    spec = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for case in spec["synthetic_cases"]:
        passed, problems = _check_synthetic(case)
        rows.append(
            {
                "kind": "synthetic",
                "id": case["id"],
                "passed": passed,
                "problems": problems,
                "rationale": case.get("rationale", ""),
            }
        )
    for case in spec["real_cases"]:
        passed, problems = _check_real(case, samples)
        rows.append(
            {
                "kind": "real",
                "id": case["post_id"],
                "passed": passed,
                "problems": problems,
                "rationale": case.get("rationale", ""),
            }
        )
    if verbose:
        for row in rows:
            status = "PASS" if row["passed"] else "FAIL"
            print(f"[{status}] {row['kind']:9s} {row['id']}")
            if row["rationale"]:
                print(f"         理由: {row['rationale']}")
            for problem in row["problems"]:
                print(f"         问题: {problem}")
        passed_count = sum(1 for row in rows if row["passed"])
        print(f"\n{passed_count}/{len(rows)} cases match the expectation")
    return rows


def main(argv: list[str] | None = None) -> int:
    rows = run_eval(verbose=True)
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
