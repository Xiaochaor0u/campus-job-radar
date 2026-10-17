"""Real-LLM acceptance for internship matching (TASK-010).

Runs at most two real analyses against the DeepSeek API and pushes the result
through the production storage and query path.

    # In the SAME PowerShell session where the key was set:
    $env:RECRUITOPS_LLM_API_KEY = "..."      # already done by the user
    python scripts/verify_real_llm.py --plan          # shows config, spends nothing
    python scripts/verify_real_llm.py --confirm       # performs 2 calls

Safety:
* the key is never printed, logged or written to the evidence file;
* ``--plan`` performs no network call at all;
* ``--confirm`` performs exactly two analyses (scenario A and B);
* the database is an isolated SQLite file under %TEMP%.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402
from sqlalchemy import select  # noqa: E402

from packages.config import get_settings  # noqa: E402
from packages.domain.models import Job, JobAnalysis  # noqa: E402
from packages.matching.client import DeepSeekClient  # noqa: E402
from packages.matching.policy import ScreeningMode, ScreeningPolicy  # noqa: E402
from packages.matching.rules import screen_job  # noqa: E402
from packages.matching.service import MatchingService, condition_gap_lines  # noqa: E402
from packages.repositories.postgres import PostgresRecruitmentRepository  # noqa: E402
from packages.storage import Storage  # noqa: E402
from packages.storage.models import JobSnapshot  # noqa: E402
from packages.storage.sync import upsert_job_analysis_snapshot, upsert_job_snapshot  # noqa: E402

SAMPLES = PROJECT_ROOT / "tests" / "fixtures" / "tencent_intern_sample.json"
EVIDENCE = PROJECT_ROOT / "docs" / "internops" / "evidence"
INTERNSHIP = ScreeningPolicy(mode=ScreeningMode.INTERNSHIP)

#: Synthetic candidate. No real personal data is involved.
CANDIDATE: dict[str, Any] = {
    "degree": "本科",
    "skills": ["Python", "Linux", "自动化测试"],
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["深圳", "北京", "西安"],
        "available_months": {"summer": 2, "winter": 1.5},
        "max_days_per_week": 3,
    },
}

#: Scenario B uses a synthetic JD (no real posting requires exactly this), clearly
#: labelled in the evidence. Scenario A uses a recorded real Tencent posting.
SCENARIO_B_JD = (
    "岗位职责：参与内部工具与自动化脚本的开发、测试与维护，配合团队完成需求交付。\n"
    "任职要求：本科及以上，2028届，工作地点：西安，需连续实习3个月以上，每周到岗3天。"
)


def _recorded_job(post_id: str) -> dict[str, Any]:
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    item = next(i for i in samples["details"] if i["post_id"] == post_id)
    detail, row = item["detail"], item["list_row"]
    jd = (
        f"岗位职责\n{detail.get('desc') or ''}\n\n任职要求\n{detail.get('request') or ''}"
    ).strip()
    return {
        "id": f"tencent-intern:{post_id}",
        "title": row.get("positionTitle") or detail.get("positionTitle") or "实习岗位",
        "city": row.get("workCities") or "",
        "job_type": detail.get("projectName") or "实习",
        "batch": "internship",
        "cohort": None,
        "cohort_status": "unconfirmed",
        "detail_url": f"https://join.qq.com/post_detail.html?postid={post_id}",
        "jd_raw": jd,
        "capture_evidence": {
            "status": "complete",
            "method": "recorded_public_api",
            "source_url": f"https://join.qq.com/post_detail.html?postid={post_id}",
            "identity_verified": True,
            "terminal_observed": True,
            "remaining_controls": [],
            "content_sha256": sha256(jd.encode("utf-8")).hexdigest(),
        },
    }


def _synthetic_job() -> dict[str, Any]:
    return {
        "id": "task010-scenario-b",
        "title": "Python开发实习生（西安）",
        "city": "西安",
        "job_type": "实习",
        "batch": "internship",
        "cohort": None,
        "cohort_status": "unconfirmed",
        "detail_url": "https://example.test/internship/task010-b",
        "jd_raw": SCENARIO_B_JD,
        "capture_evidence": {
            "status": "complete",
            "method": "synthetic_public_style_jd",
            "source_url": "https://example.test/internship/task010-b",
            "identity_verified": True,
            "terminal_observed": True,
            "remaining_controls": [],
            "content_sha256": sha256(SCENARIO_B_JD.encode("utf-8")).hexdigest(),
        },
    }


def _quote_grounded(quote: str, jd: str) -> bool:
    import re

    strip = re.compile(r"[\s\u3000·•\-—_/／、，,。；;：:（）()\[\]【】\"'“”‘’|]+")
    return bool(quote) and strip.sub("", quote).casefold() in strip.sub("", jd).casefold()


def _job_model_readback(payload: dict[str, Any]) -> Job:
    detail = payload["jd_raw"]
    return Job(
        id=payload["id"],
        company_id="tencent" if payload["id"].startswith("tencent") else "example",
        title=payload["title"],
        city=payload["city"] or None,
        detail_url=payload["detail_url"],
        jd_raw=detail,
        cohort=payload["cohort"],
        cohort_status=payload["cohort_status"],
        batch=payload["batch"],
        source="task010-real-llm",
        source_ref=payload["id"],
        track=payload.get("_screening", {}).get("track", "unknown"),
        internship_seasons=payload.get("_screening", {}).get("seasons"),
        conversion_opportunity=payload.get("_screening", {}).get("conversion", "unknown"),
        internship_requirements=payload.get("_screening", {}).get("requirements"),
        match_conditions=payload.get("_screening", {}).get("evaluation"),
    )


def _analysis_model(result: Any) -> JobAnalysis:
    return JobAnalysis(
        match_score=result.match_score,
        advantages=list(result.advantages),
        gaps=list(result.gaps),
        summary=result.summary,
        recommendation=result.recommendation,
        evidence=[item.model_dump(mode="json") for item in result.evidence],
        matched_directions=list(result.matched_directions),
        primary_match_direction=result.primary_match_direction,
        analysis_status=result.analysis_status.value,
        model=result.model,
        analysis_version=result.analysis_version,
        prompt_version=result.prompt_version,
        filter_reasons=list(result.filter_reasons),
        refusal_reason=result.refusal_reason,
    )


def _stub_transport() -> Any:
    """A fake transport that still quotes the real JD, for plumbing-only runs.

    It is explicitly labelled as a stub in the evidence file, so a plumbing run can
    never be mistaken for a real model result.
    """

    def transport(_endpoint: str, _headers: dict[str, str], payload: Any, _timeout: float) -> Any:
        user_prompt = payload.get("user_prompt") or payload.get("messages") or ""
        jd = ""
        try:
            data = json.loads(user_prompt) if isinstance(user_prompt, str) else {}
            jd = str(data.get("jd_raw") or "")
        except Exception:  # noqa: BLE001 - stub only
            jd = ""
        lines = [line.strip() for line in jd.splitlines() if line.strip()]
        quote = next((line for line in lines if len(line) > 8), lines[0] if lines else "")
        body = {
            "matched_directions": ["python_dev"],
            "primary_match_direction": "python_dev",
            "score_breakdown": {
                "core_direction": 22,
                "required_skills": 18,
                "project_evidence": 14,
                "engineering_stack": 9,
            },
            "evidence_level": "partial",
            "evidence": [
                {
                    "jd_requirement": quote,
                    "profile_evidence": "合成候选人具备 Python 与自动化测试经历",
                    "relation": "direct",
                    "requirement_type": "core",
                }
            ],
            "missing_core_requirements": [],
            "advantages": ["Python 相关项目经历"],
            "gaps": ["实习时长需与招聘方确认"],
            "summary": "stub transport：仅用于验证链路，非真实模型输出。",
        }
        return {
            "model": "stub-transport",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(body, ensure_ascii=False)}
                    ],
                }
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    return transport


def run(confirm: bool, stub: bool = False) -> int:
    settings = get_settings()
    api_key = (settings.llm_api_key or "").strip()
    configured = bool(api_key) or stub
    print(f"API_KEY_CONFIGURED={configured}")
    print(f"provider={settings.model_provider_name} model={settings.llm_model}")
    print(f"endpoint={settings.llm_endpoint}")
    print(f"llm_enabled={settings.llm_enabled} job_analysis_enabled={settings.job_analysis_enabled}")

    if not configured:
        print()
        print("BLOCKED: no API key visible to this process.")
        print("Set it in the SAME PowerShell session, then re-run:")
        print('  $env:RECRUITOPS_LLM_API_KEY = "<your key>"')
        print("  python scripts/verify_real_llm.py --plan")
        print("  python scripts/verify_real_llm.py --confirm")
        return 2
    if not confirm and not stub:
        print()
        print("DRY RUN: nothing was sent. Planned calls: 2 analyses")
        print("  A: recorded public Tencent internship JD")
        print("  B: synthetic JD requiring 3 months, candidate can do 2")
        print("Re-run with --confirm to spend those two calls.")
        return 0

    scenarios = [
        ("A", "recorded_public_tencent", _recorded_job("1200791473415778304")),
        ("B", "synthetic_public_style", _synthetic_job()),
    ]
    if stub:
        client = DeepSeekClient(api_key="stub-mode", model=settings.llm_model, transport=_stub_transport())
    else:
        # max_attempts=1: a configuration/auth failure must cost exactly one call.
        client = DeepSeekClient(api_key=api_key, model=settings.llm_model, max_attempts=1)
    service = MatchingService(client)
    db_path = Path(tempfile.gettempdir()) / f"internops-llm-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.sqlite"
    storage = Storage.from_url(f"sqlite:///{db_path.as_posix()}")
    storage.initialize()

    rows: list[dict[str, Any]] = []
    calls = 0
    for tag, kind, job in scenarios:
        screening = screen_job(job, CANDIDATE, policy=INTERNSHIP)
        if not screening.eligible:
            rows.append({"scenario": tag, "kind": kind, "error": f"screening:{screening.analysis_status}"})
            continue
        job["_screening"] = {
            "track": screening.track,
            "seasons": screening.seasons,
            "conversion": screening.conversion_opportunity,
            "requirements": screening.internship_requirements,
            "evaluation": screening.condition_evaluation,
        }
        calls += 1
        outcome = service.analyze(job, CANDIDATE, screening=screening)
        result = outcome.result
        if result.error_code or result.analysis_status.value == "failed":
            # A configuration / auth / model-id problem must not burn the second call.
            rows.append({
                "scenario": tag,
                "kind": kind,
                "job_id": job["id"],
                "status": result.analysis_status.value,
                "error_code": result.error_code,
                "error_message": (result.error_message or "")[:240],
            })
            print(f"[STOP] scenario {tag} failed: code={result.error_code} message={(result.error_message or '')[:160]}")
            break
        quotes = [item.jd_requirement for item in result.evidence]
        grounded = [q for q in quotes if _quote_grounded(q, job["jd_raw"])]
        rule_gaps = condition_gap_lines(screening)
        duration_gap = next(
            (
                item
                for item in (screening.condition_evaluation or {}).get("conditions", [])
                if item.get("name") == "duration_months"
            ),
            {},
        )
        rows.append(
            {
                "scenario": tag,
                "kind": kind,
                "job_id": job["id"],
                "title": job["title"],
                "status": result.analysis_status.value,
                "match_score": result.match_score,
                "recommendation": result.recommendation,
                "summary": (result.summary or "")[:160],
                "evidence_quotes": quotes[:4],
                "evidence_grounded": f"{len(grounded)}/{len(quotes)}",
                "advantages": result.advantages[:3],
                "gaps": result.gaps[:4],
                "rule_duration": {
                    "status": duration_gap.get("status"),
                    "negotiability": duration_gap.get("negotiability"),
                    "confirmation_required": duration_gap.get("confirmation_required"),
                },
                "rule_gaps_present_in_record": all(g in result.gaps for g in rule_gaps),
                "model": result.model,
                "refusal_reason": (result.refusal_reason or "")[:120],
                "tokens": {"in": result.input_tokens, "out": result.output_tokens},
            }
        )
        with storage.write_transaction() as session:
            upsert_job_snapshot(
                session,
                _job_model_readback(job),
                capture_status="complete",
                capture_failure_reason="",
                availability_status="active",
                title_key=job["title"],
            )
            upsert_job_analysis_snapshot(session, _job_model_readback(job), _analysis_model(result))

    repo = PostgresRecruitmentRepository(storage)
    page = repo.browse_jobs(track="internship", limit=20)
    with storage.session() as session:
        stored = [
            {"id": r.id, "track": r.track, "score": r.match_score}
            for r in session.scalars(select(JobSnapshot))
        ]
    detail_ok = 0
    for row in rows:
        if "job_id" in row and repo.get_job(row["job_id"]) is not None:
            detail_ok += 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": settings.model_provider_name,
        "model": settings.llm_model,
        "endpoint": settings.llm_endpoint,
        "transport": "stub" if stub else "live",
        "analyses": calls,
        "scenarios": rows,
        "storage": {
            "stored_rows": len(stored),
            "browse_internship_total": page.total,
            "detail_readable": detail_ok,
            "browse_ids": [item.id for item in page.items],
        },
        "database": str(db_path),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / f"task-010-real-llm-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nevidence: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true", help="show config, send nothing")
    group.add_argument("--confirm", action="store_true", help="perform the two real calls")
    group.add_argument("--stub", action="store_true", help="exercise the integration path and fail-closed behaviour with a fake transport (no network, no spend)")
    args = parser.parse_args(argv)
    return run(confirm=args.confirm, stub=args.stub)


if __name__ == "__main__":
    raise SystemExit(main())
