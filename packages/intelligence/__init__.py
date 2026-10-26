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
    "NotifyConfig",
    "NotifyConfigError",
    "OfferBiuError",
    "OfferBiuIngestResult",
    "WebhookDelivery",
    "build_daily_digest",
    "fetch_offerbiu_postings",
    "format_digest_text",
    "ingest_offerbiu_postings",
    "load_notify_config",
    "push_daily_digest",
    "record_pipeline_events",
    "send_webhook_message",
]
