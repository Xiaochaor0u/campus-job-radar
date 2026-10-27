"""On-demand OfferBiu collection triggered from the web workbench.

The digest ("今日新发布") only contains what a collection pass observed, so a
user who never installs the scheduled tasks would see a permanently stale
feed.  This module gives the UI a "collect now" button: it runs the same
fetch + ingest as ``scripts/collect_offerbiu.py`` in a worker thread and
reports progress through a small in-process status object.

Scope: the caller picks industry groups (the same codes as
``offerbiu_industry_groups`` in preferences) and recruit types.  The public
feed cannot filter by group server-side, so group filtering happens here,
row by row, on the ``industryGroupCodes`` field each announcement carries.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any

from packages.intelligence.offerbiu_source import (
    fetch_offerbiu_postings,
    ingest_offerbiu_postings,
)
from packages.storage import Storage

VALID_RECRUIT_TYPES = ("实习", "秋招", "春招")

_PAGE_RE = re.compile(r"\[offerbiu\]\s*(\S+)\s+page\s+(\d+)/(\d+)\s+collected=(\d+)")


class CollectionAlreadyRunning(RuntimeError):
    """Raised when a second collection is requested mid-run."""


class _Collector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {
            "running": False,
            "phase": "",
            "progress": {},
            "started_at": "",
            "finished_at": "",
            "run_id": "",
            "scope": {},
            "result": None,
            "error": "",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def start(
        self,
        storage: Storage,
        *,
        industry_groups: tuple[str, ...] | list[str],
        recruit_types: tuple[str, ...] | list[str],
        max_pages_per_type: int = 300,
    ) -> dict[str, Any]:
        groups = tuple(dict.fromkeys(str(code) for code in industry_groups))
        types = tuple(dict.fromkeys(str(value) for value in recruit_types))
        if not groups:
            raise ValueError("至少选择一个行业范围")
        if not types or any(value not in VALID_RECRUIT_TYPES for value in types):
            raise ValueError("招聘类型只能是：实习 / 秋招 / 春招")
        with self._lock:
            if self._status.get("running"):
                raise CollectionAlreadyRunning("采集正在进行中")
            self._status = {
                "running": True,
                "phase": "fetching",
                "progress": {},
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": "",
                "run_id": "",
                "scope": {"industry_groups": list(groups), "recruit_types": list(types)},
                "result": None,
                "error": "",
            }
            snapshot = dict(self._status)
            self._thread = threading.Thread(
                target=self._run,
                args=(storage, groups, types, max_pages_per_type),
                name="offerbiu-manual-collect",
                daemon=True,
            )
            self._thread.start()
        return snapshot

    def _update(self, **fields: Any) -> None:
        with self._lock:
            self._status.update(fields)

    def _progress(self, message: str) -> None:
        match = _PAGE_RE.search(str(message))
        if not match:
            return
        recruit_type, page, total_pages, collected = match.groups()
        self._update(
            progress={
                "recruit_type": recruit_type,
                "page": int(page),
                "total_pages": int(total_pages),
                "fetched": int(collected),
            }
        )

    def _run(
        self,
        storage: Storage,
        groups: tuple[str, ...],
        recruit_types: tuple[str, ...],
        max_pages_per_type: int,
    ) -> None:
        try:
            postings = fetch_offerbiu_postings(
                recruit_types=recruit_types,
                max_pages_per_type=max_pages_per_type,
                logger=self._progress,
            )
            fetched = len(postings)
            wanted = set(groups)
            scoped = [
                item
                for item in postings
                if isinstance(item.get("industryGroupCodes"), list)
                and any(code in wanted for code in item["industryGroupCodes"])
            ]
            self._update(
                phase="ingesting",
                progress={
                    "fetched": fetched,
                    "in_scope": len(scoped),
                    "filtered_out": fetched - len(scoped),
                },
            )
            result = ingest_offerbiu_postings(
                storage, scoped, record_events=True
            )
            payload = result.to_dict()
            payload["fetched_all"] = fetched
            payload["in_scope"] = len(scoped)
            payload["filtered_out"] = fetched - len(scoped)
            self._update(
                running=False,
                phase="done",
                finished_at=datetime.now(timezone.utc).isoformat(),
                run_id=payload.get("run_id", ""),
                result=payload,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced through the status API
            self._update(
                running=False,
                phase="failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc)[:500],
            )


_collector = _Collector()


def start_collection(
    storage: Storage,
    *,
    industry_groups: tuple[str, ...] | list[str],
    recruit_types: tuple[str, ...] | list[str],
    max_pages_per_type: int = 300,
) -> dict[str, Any]:
    """Start one collection run; raises if another one is still going."""

    return _collector.start(
        storage,
        industry_groups=industry_groups,
        recruit_types=recruit_types,
        max_pages_per_type=max_pages_per_type,
    )


def collection_status() -> dict[str, Any]:
    return _collector.status()


__all__ = [
    "CollectionAlreadyRunning",
    "VALID_RECRUIT_TYPES",
    "collection_status",
    "start_collection",
]
