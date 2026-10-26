"""Job intelligence: turn consecutive crawls into a change feed for students.

The upstream pipeline already classifies every observed job as new, changed or
reused against its own snapshots.  This package persists that classification
as an append-only ``job_events`` log so the web workbench and group-bot push
can answer one question: what changed since I last looked?

Baselines: the first complete crawl of a company would report every posting as
"new", flooding the digest.  The writer therefore records a single ``baseline``
marker for such companies and skips their ``new`` events on that run.
"""

from .writer import EventWriteResult, record_pipeline_events
from .digest import DigestResult, build_daily_digest
from .offerbiu_source import (
    OfferBiuError,
    OfferBiuIngestResult,
    fetch_offerbiu_postings,
    ingest_offerbiu_postings,
)
from .recommend import RecommendationItem, RecommendationResult, recommend_jobs
from .insight import JobInsight, build_job_insight
from .resume_keywords import extract_profile_hints
from .watch import (
    VALID_WINDOWS,
    WatchCondition,
    WatchHit,
    WatchResult,
    delete_rule,
    expand_terms,
    latest_event_at,
    list_rules,
    run_watch,
    save_rule,
    window_start,
)
from .webhook import (
    NotifyConfig,
    NotifyConfigError,
    WebhookDelivery,
    format_digest_text,
    load_notify_config,
    push_daily_digest,
    send_webhook_message,
)

__all__ = [
    "DigestResult",
    "EventWriteResult",
    "JobInsight",
    "NotifyConfig",
    "NotifyConfigError",
    "OfferBiuError",
    "OfferBiuIngestResult",
    "RecommendationItem",
    "RecommendationResult",
    "WebhookDelivery",
    "VALID_WINDOWS",
    "WatchCondition",
    "WatchHit",
    "WatchResult",
    "build_daily_digest",
    "build_job_insight",
    "delete_rule",
    "expand_terms",
    "extract_profile_hints",
    "fetch_offerbiu_postings",
    "format_digest_text",
    "ingest_offerbiu_postings",
    "latest_event_at",
    "list_rules",
    "load_notify_config",
    "push_daily_digest",
    "recommend_jobs",
    "record_pipeline_events",
    "run_watch",
    "save_rule",
    "send_webhook_message",
    "window_start",
]
