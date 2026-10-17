"""The internship evaluation set runs as part of the suite.

Each case carries a written rationale, so a failure is a disagreement worth
reading rather than a number to re-baseline.  ``python -m evals.internship_eval``
prints the same table for manual review.
"""

from __future__ import annotations

from evals.internship_eval import run_eval


def test_internship_eval_set_matches_expectations():
    rows = run_eval()

    failures = [row for row in rows if not row["passed"]]

    assert rows, "evaluation set must not be empty"
    assert not failures, "\n".join(
        f"{row['kind']}:{row['id']} -> {row['problems']}" for row in failures
    )
    assert {row["kind"] for row in rows} == {"synthetic", "real"}
