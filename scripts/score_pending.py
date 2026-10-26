"""Score postings that already have a JD but no match score yet.

The daily task can score, but only inside a full run (crawl -> score), and a
fresh clone has no scores at all — so the「27届校招」page shows 高匹配 0 with no
obvious way to change that. This script runs the same production scoring path
(`build_analysis_resume_plan` + `resume_pending_analyses`) on its own:

    python scripts/score_pending.py --company transsion --limit 20
    python scripts/score_pending.py --company transsion,dahua --dry-run

It never re-crawls and never guesses: postings whose title is outside your
target directions are reported as filtered, not scored.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from packages.config import get_settings  # noqa: E402
from packages.matching import DeepSeekClient, MatchingService  # noqa: E402
from packages.matching.resume import (  # noqa: E402
    build_analysis_resume_plan,
    resume_pending_analyses,
)
from packages.storage import Storage  # noqa: E402


def _profile(settings) -> dict:
    path = Path(settings.candidate_profile_config)
    if not path.is_file():
        raise SystemExit(
            f"找不到候选人画像：{path}\n"
            "请先在「配置」页上传简历，或在「智能推荐」页粘贴简历后保存。"
        )
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload.get("profile", payload) or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给已有 JD 但还没有评分的岗位补评分")
    parser.add_argument("--company", default="", help="逗号分隔的企业 id，留空表示全部")
    parser.add_argument("--limit", type=int, default=0, help="最多评多少条，0 表示不限")
    parser.add_argument("--concurrency", type=int, default=4, help="并发数（默认 4）")
    parser.add_argument("--dry-run", action="store_true", help="只统计待评数量，不调用模型")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="换过简历后使用：先清掉「用旧画像算出来的」评分，再重新评",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.llm_enabled or not settings.llm_api_key.strip():
        raise SystemExit(
            "模型调用未启用。请到「配置」页保存 API 密钥，并把「高级：助理模式」里的"
            "求职助理设为「随有效模型连接启用」，然后点保存。"
        )
    if not settings.job_analysis_enabled:
        raise SystemExit("岗位评分开关关闭（job_analysis_enabled=false）。")

    profile = _profile(settings)
    storage = Storage.from_url(settings.database_url, initialize=True)
    company_ids = tuple(item.strip() for item in args.company.split(",") if item.strip())

    if args.rescore:
        # 评分一旦 COMPLETE 就会被当成"已完成"跳过，所以换简历后旧分数永远不会更新。
        # 这里按画像指纹清掉旧结果，让下面的计划重新把岗位算成待评分。
        from sqlalchemy import or_, select

        from packages.matching.rules import profile_fingerprint
        from packages.storage import JobAnalysisSnapshot, JobSnapshot

        current = profile_fingerprint(profile)
        with storage.transaction(write=True) as session:
            stale = session.scalars(
                select(JobAnalysisSnapshot).where(
                    or_(
                        JobAnalysisSnapshot.profile_fingerprint.is_(None),
                        JobAnalysisSnapshot.profile_fingerprint != current,
                    )
                )
            ).all()
            stale_ids = {row.job_id for row in stale}
            for row in stale:
                session.delete(row)
            if stale_ids:
                for job in session.scalars(
                    select(JobSnapshot).where(JobSnapshot.id.in_(stale_ids))
                ).all():
                    job.match_score = None
        print(f"已清除 {len(stale_ids)} 条「旧画像」评分，下面重新评", flush=True)

    plan = build_analysis_resume_plan(storage, profile, company_ids=company_ids)
    pending = list(plan.pending_jobs)
    if args.limit > 0:
        pending = pending[: args.limit]

    print(f"范围内岗位 {plan.total_jobs} 条，已可评分 {plan.eligible_jobs} 条，待评分 {len(plan.pending_jobs)} 条", flush=True)
    if not pending:
        print("没有待评分岗位。可能全部已评分，或标题都不在你的目标方向内。")
        return 0
    if args.dry_run:
        print("干跑结束，未调用模型。样例：")
        for candidate in pending[:5]:
            print("  -", candidate.payload.get("title", "")[:50])
        return 0

    client = DeepSeekClient(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        endpoint=settings.llm_endpoint,
        api_style=settings.model_api_style,
        timeout=settings.llm_timeout_seconds,
        max_tokens=settings.llm_matching_max_tokens,
        thinking_enabled=settings.llm_matching_thinking_enabled,
        reasoning_effort=settings.llm_matching_reasoning_effort,
    )
    service = MatchingService(client, max_tokens=settings.llm_matching_max_tokens)

    def progress(result) -> None:
        print(
            f"  进度 {result.processed}/{result.planned}"
            f" · 成功 {result.completed} · 过滤/失败 {result.failed}"
            f" · tokens {result.output_tokens}",
            flush=True,
        )

    result = resume_pending_analyses(
        storage,
        profile,
        service,
        pending,
        concurrency=max(1, args.concurrency),
        progress=progress,
    )
    print(
        f"完成：计划 {result.planned} · 成功 {result.completed} · "
        f"过滤/失败 {result.failed} · 错误 {dict(result.errors) or '无'}"
    )
    if result.stopped_reason:
        print(f"提前停止：{result.stopped_reason}")

    from sqlalchemy import select

    from packages.storage import JobSnapshot

    with storage.session() as session:
        rows = session.scalars(select(JobSnapshot).where(JobSnapshot.match_score.is_not(None))).all()
        high = [row for row in rows if (row.match_score or 0) >= 70]
    print(f"现在库里有评分的岗位 {len(rows)} 条，其中 70 分以上 {len(high)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
