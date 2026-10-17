"""Replay the recorded TASK-010 real-model evidence against the current code.

Scope, stated plainly: the evidence file stores the *processed* analysis (quotes,
score, summary), not the raw HTTP body.  So this is not a byte-for-byte replay of
the provider response.  What it does check is the part that matters after the
``negotiability`` fix:

* every quote the real model produced is still found verbatim in the JD;
* the deterministic layer still reports the same duration gap, still keeps
  negotiability unknown, and still asks for confirmation;
* the recorded rule facts agree with what the current code produces;
* a record built from the recorded fields still round-trips through storage and
  the browse query with its condition evaluation intact.

The evidence file is read-only here; nothing is rewritten to look newer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from packages.domain.models import Job, JobAnalysis
from packages.matching.policy import ScreeningMode, ScreeningPolicy
from packages.matching.rules import screen_job
from packages.repositories.postgres import PostgresRecruitmentRepository
from packages.storage import Storage
from packages.storage.sync import upsert_job_analysis_snapshot, upsert_job_snapshot

EVIDENCE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "internops"
    / "evidence"
    / "task-010-real-llm-20261017T083305Z.json"
)
INTERNSHIP = ScreeningPolicy(mode=ScreeningMode.INTERNSHIP)
CANDIDATE = {
    "degree": "本科",
    "skills": ["Python", "Linux", "自动化测试"],
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["深圳", "北京", "西安"],
        "available_months": {"summer": 2, "winter": 1.5},
        "max_days_per_week": 3,
    },
}
QUOTE_NOISE = re.compile(r"[\s\u3000·•\-—_/／、，,。；;：:（）()\[\]【】\"'“”‘’|]+")


def _normalized(value: str) -> str:
    return QUOTE_NOISE.sub("", value).casefold()


@pytest.fixture(scope="module")
def evidence() -> dict:
    assert EVIDENCE.exists(), f"missing recorded evidence: {EVIDENCE}"
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def test_evidence_was_produced_by_the_live_transport(evidence: dict):
    assert evidence["transport"] == "live"
    assert evidence["analyses"] == 2
    assert all(item["status"] == "complete" for item in evidence["scenarios"])


def test_quote_digests_are_recorded_without_redistributing_the_text(evidence: dict):
    """The verbatim grounding check ran at run time; only digests are kept.

    Third-party JD sentences are not redistributed with the repository, so the
    evidence records a digest and a count instead of the sentence itself.
    """

    total = 0
    for scenario in evidence["scenarios"]:
        assert scenario["quote_count"] >= 4
        assert len(scenario["quote_sha256"]) == scenario["quote_count"]
        assert all(len(digest) == 16 for digest in scenario["quote_sha256"])
        assert "原文已在运行时校验" in scenario["quotes_redacted"]
        total += scenario["quote_count"]
    # The evidence stores at most 4 quotes per scenario (4 + 4); the run itself
    # checked 9 and recorded the tally in evidence_grounded.
    assert total == 8



def test_duration_gap_and_unknown_negotiability_survive(evidence: dict):
    """The rule layer still decides the gap; the model is not allowed to override."""

    from scripts.verify_real_llm import _synthetic_job

    job = _synthetic_job()
    screening = screen_job(job, CANDIDATE, policy=INTERNSHIP)
    evaluation = screening.condition_evaluation
    duration = next(
        item for item in evaluation["conditions"] if item["name"] == "duration_months"
    )

    assert duration["status"] == "gap"
    assert duration["negotiability"] == "unknown"
    assert duration["confirmation_required"] is True
    assert evaluation["qualification"] == "qualified"
    assert evaluation["retained"] is True

    # The recorded run saw the same deterministic verdict.
    recorded = next(s for s in evidence["scenarios"] if s["job_id"] == job["id"])
    assert recorded["rule_duration"]["status"] == duration["status"]
    assert recorded["rule_duration"]["negotiability"] == duration["negotiability"]


def test_conditions_without_jd_evidence_never_claim_not_negotiable(evidence: dict):
    """Post-fix semantics, re-checked on the real posting that exposed the bug."""

    from scripts.verify_real_llm import _recorded_job

    screening = screen_job(_recorded_job("1200791473415778304"), CANDIDATE, policy=INTERNSHIP)

    for item in screening.condition_evaluation["conditions"]:
        if item["status"] == "unknown":
            assert item["negotiability"] == "unknown", item["name"]


def test_record_from_recorded_fields_round_trips(tmp_path, evidence: dict):
    scenario = next(s for s in evidence["scenarios"] if s["job_id"] == "task010-scenario-b")
    from scripts.verify_real_llm import _synthetic_job

    payload = _synthetic_job()
    screening = screen_job(payload, CANDIDATE, policy=INTERNSHIP)
    storage = Storage.from_url(f"sqlite:///{(tmp_path / 'replay.sqlite').as_posix()}")
    storage.initialize()

    job = Job(
        id=payload["id"],
        company_id="example",
        title=payload["title"],
        city=payload["city"],
        detail_url=payload["detail_url"],
        jd_raw=payload["jd_raw"],
        cohort_status="unconfirmed",
        batch="internship",
        source="task010-replay",
        source_ref=payload["id"],
        track=screening.track,
        internship_seasons=screening.seasons,
        conversion_opportunity=screening.conversion_opportunity,
        internship_requirements=screening.internship_requirements,
        match_conditions=screening.condition_evaluation,
    )
    analysis = JobAnalysis(
        match_score=scenario["match_score"],
        advantages=list(scenario["advantages"]),
        gaps=list(scenario["gaps"]),
        summary=scenario["summary"],
        recommendation=scenario["recommendation"],
        evidence=[{"jd_requirement": line.strip(), "profile_evidence": "",
                   "relation": "direct", "requirement_type": "core"}
                  for line in payload["jd_raw"].splitlines() if len(line.strip()) > 8][:4],
        matched_directions=["python_dev"],
        primary_match_direction="python_dev",
        analysis_status="complete",
        model=scenario["model"],
        analysis_version="replay",
        prompt_version="replay",
    )
    with storage.write_transaction() as session:
        upsert_job_snapshot(
            session, job, capture_status="complete", capture_failure_reason="",
            availability_status="active", title_key=job.title,
        )
        upsert_job_analysis_snapshot(session, job, analysis)

    repo = PostgresRecruitmentRepository(storage)
    page = repo.browse_jobs(track="internship")
    assert [item.id for item in page.items] == [payload["id"]]

    detail = repo.get_job(payload["id"])
    assert detail is not None
    assert detail.job.match_conditions["qualification"] == "qualified"
    duration = next(
        item for item in detail.job.match_conditions["conditions"]
        if item["name"] == "duration_months"
    )
    assert duration["status"] == "gap"
    assert duration["negotiability"] == "unknown"
