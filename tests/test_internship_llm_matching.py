"""TASK-006: internship LLM scoring — prompt policy and structured-output guard.

The model is a deterministic fake wired through ``DeepSeekClient(transport=...)``,
so these tests never call a provider.  They check three contracts:

1. An internship run no longer tells the model "cohort=2027 / 非实习"; it receives
   its own verdict plus the rule-extracted conditions.
2. A model claim that cannot be quoted from the JD is discarded, not shown.
3. Campus prompts and campus behaviour are untouched.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from packages.matching.client import DeepSeekClient
from packages.matching.models import AnalysisStatus
from packages.matching.policy import ScreeningMode, ScreeningPolicy
from packages.matching.rules import screen_job
from packages.matching.service import (
    MatchingService,
    _prompt_system,
    condition_gap_lines,
    ground_model_claims,
)


INTERNSHIP = ScreeningPolicy(mode=ScreeningMode.INTERNSHIP)
PROFILE: dict[str, Any] = {
    "degree": "本科",
    "skills": ["Python"],
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["西安", "郑州"],
        "available_months": {"summer": 2},
        "max_days_per_week": 3,
    },
}

JD = (
    "岗位职责：参与内部工具与自动化脚本的开发与维护，配合测试同学完成接口验证。\n"
    "任职要求：本科及以上，2028届，工作地点：西安，能连续实习3个月以上，每周到岗3天。"
)

JOB = {
    "id": "intern-llm",
    "title": "Python开发暑期实习生",
    "city": "西安",
    "job_type": "实习",
    "batch": "internship",
    "cohort": None,
    "cohort_status": "unconfirmed",
    "jd_raw": JD,
    "capture_evidence": {
        "status": "complete",
        "method": "test_fixture",
        "source_url": "https://example.test/intern/intern-llm",
        "identity_verified": True,
        "terminal_observed": True,
        "remaining_controls": [],
        "content_sha256": __import__("hashlib").sha256(JD.encode("utf-8")).hexdigest(),
    },
}


def _internship_screening():
    return screen_job(JOB, PROFILE, policy=INTERNSHIP)


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "matched_directions": ["python_dev"],
        "primary_match_direction": "python_dev",
        "score_breakdown": {
            "core_direction": 24,
            "required_skills": 20,
            "project_evidence": 16,
            "engineering_stack": 10,
        },
        "evidence_level": "partial",
        "evidence": [
            {
                "jd_requirement": "能连续实习3个月以上",
                "profile_evidence": "暑假可实习2个月",
                "relation": "adjacent",
                "requirement_type": "core",
            }
        ],
        "missing_core_requirements": [],
        "advantages": ["有Python项目经验"],
        "gaps": ["实习时长存在差距"],
        "summary": "方向匹配，实习时长需确认。",
    }
    base.update(overrides)
    return base


class _StubClient:
    """Minimal ``DeepSeekClientProtocol`` implementation: no network, no schema."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> Any:
        from packages.matching.models import DeepSeekResponse

        self.calls.append(kwargs)
        return DeepSeekResponse(
            content=json.dumps(self.response, ensure_ascii=False),
            model="deepseek-flash",
            input_tokens=10,
            output_tokens=5,
        )


def _analyze(responses: list[Any], job: dict[str, Any] = JOB, profile: dict[str, Any] | None = None):
    client = _StubClient(responses[0])
    service = MatchingService(client)
    outcome = service.analyze(job, profile or PROFILE, policy=INTERNSHIP)
    return outcome, client.calls


# --- prompt policy ---------------------------------------------------------


def test_internship_prompt_describes_its_own_gate():
    system = _prompt_system(_internship_screening())

    assert "track=internship" in system
    assert "cohort=2027" not in system
    assert "非实习" not in system
    assert "逐字片段" in system


def test_campus_prompt_is_unchanged():
    campus = screen_job(
        {
            "id": "campus-1",
            "title": "C++软件开发工程师",
            "batch": "formal",
            "cohort": 2027,
            "cohort_status": "confirmed",
            "jd_raw": "岗位职责：负责核心系统研发。任职要求：熟悉 C++、Linux。",
        },
        None,
    )

    assert "cohort=2027、cohort_status=confirmed、非实习且非博士限定" in _prompt_system(campus)
    assert "track=internship" not in _prompt_system(campus)


def test_model_receives_the_rule_extracted_conditions():
    _, calls = _analyze([_payload()])

    user_payload = json.loads(calls[0]["user_prompt"])
    conditions = user_payload["internship_conditions"]

    assert conditions["qualification"] in {"qualified", "undetermined", "not_qualified"}
    names = {item["name"] for item in conditions["conditions"]}
    assert {"duration_months", "degree", "city"} <= names


# --- structured-output guard ----------------------------------------------


def test_grounded_requirement_is_kept():
    outcome, _ = _analyze([_payload()])

    quotes = [item.jd_requirement for item in outcome.result.evidence]

    assert "能连续实习3个月以上" in quotes


def test_invented_requirement_is_discarded():
    invented = _payload(
        evidence=[
            {
                "jd_requirement": "必须持有硕士学位并具备5年工作经验",
                "profile_evidence": "本科",
                "relation": "adjacent",
                "requirement_type": "core",
            }
        ]
    )

    outcome, _ = _analyze([invented])

    assert outcome.result.evidence == []
    assert outcome.result.analysis_status is not AnalysisStatus.FAILED


def test_invented_missing_requirement_is_discarded_but_real_one_is_kept():
    mixed = _payload(
        missing_core_requirements=[
            "能连续实习3个月以上",          # 逐字来自 JD
            "需具备英语六级与发表论文",      # JD 中不存在
        ]
    )

    outcome, _ = _analyze([mixed])

    assert outcome.result.gaps  # 记录仍然生成
    assert "能连续实习3个月以上" in outcome.result.gaps
    assert "需具备英语六级与发表论文" not in outcome.result.gaps


def test_guard_is_inert_for_campus_screenings():
    payload = _payload(
        evidence=[
            {
                "jd_requirement": "熟悉 C++",
                "profile_evidence": "有 C++ 项目",
                "relation": "direct",
                "requirement_type": "core",
            }
        ]
    )
    grounded, dropped = ground_model_claims(
        _screening_payload(payload), JOB, None
    )

    assert dropped == []
    assert len(grounded.evidence) == 1


def _screening_payload(raw: dict[str, Any]):
    from packages.matching.models import DeepSeekMatchPayload

    return DeepSeekMatchPayload.model_validate(raw)


# --- rule evidence in the result ------------------------------------------


def test_rule_condition_gaps_are_rendered_with_jd_excerpts():
    lines = condition_gap_lines(_internship_screening())

    assert any(line.startswith("duration_months") for line in lines)
    assert any("JD：" in line for line in lines)


def test_record_gaps_include_rule_conditions_before_model_gaps():
    outcome, _ = _analyze([_payload()])

    gaps = outcome.result.gaps

    assert any(gap.startswith("duration_months") for gap in gaps)
    assert "实习时长存在差距" in gaps          # 模型补充的差距也保留


def test_records_carry_condition_evaluation_for_review():
    evaluation = _internship_screening().condition_evaluation

    assert evaluation["retained"] is True
    duration = next(i for i in evaluation["conditions"] if i["name"] == "duration_months")
    assert duration["status"] == "gap"
    assert duration["negotiability"] == "unknown"
    assert duration["confirmation_required"] is True
