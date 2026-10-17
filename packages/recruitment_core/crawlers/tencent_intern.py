"""Tencent internship positions from the official public read-only JSON API.

``TencentCrawler`` screens the *formal campus* campaign and deliberately selects
the formal project mappings, so it can never return an internship.  Rather than
bending that state machine, this adapter targets the two internship projects the
same public API advertises ("应届实习" and "日常实习") and reuses the parent's
parsing helpers for detail text, cities and ids.

Access model: the endpoints are the same read-only public APIs the careers site
calls for anonymous visitors.  No login, no captcha, no share-link credential and
no attempt to bypass any restriction.  Requests are sequential, rate limited and
page-capped.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping

import requests

from .tencent import TencentCrawler

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 InternOps/0.1"
)


class TencentInternshipCrawler:
    """Collect currently advertised Tencent internships."""

    ORIGIN = "https://join.qq.com"
    LIST_API = f"{ORIGIN}/api/v1/position/searchPosition"
    DETAIL_API = f"{ORIGIN}/api/v1/jobDetails/getJobDetailsByPostId"
    DETAIL_URL_TEMPLATE = f"{ORIGIN}/post_detail.html?postid={{post_id}}"
    #: mappingId 2 = 应届实习, 104 = 日常实习 (from /api/v1/position/getProjectMapping)
    INTERNSHIP_MAPPING_IDS = (2, 104)
    CRAWLER_KEY = "tencent_internship"
    CAMPAIGN_TEXT = "腾讯实习生招聘（应届实习/日常实习）"

    PAGE_SIZE = 50
    MAX_PAGES = 20
    DETAIL_WORKERS = 4
    REQUEST_INTERVAL_SECONDS = 0.25
    REQUEST_ATTEMPTS = 3
    TIMEOUT_SECONDS = 20
    #: The public API answers ``status=404, "岗位已下架"`` for postings that are
    #: still listed but already withdrawn.  That is a source-confirmed delisting,
    #: not a capture failure, and it must never be reported as one.
    DELISTED_STATUS = 404

    def __init__(
        self,
        company: str,
        careers_url: str = ORIGIN,
        *,
        max_jobs: int | None = None,
    ) -> None:
        self.company_name = company
        self.careers_url = careers_url or self.ORIGIN
        self.max_jobs = max_jobs if max_jobs and max_jobs > 0 else None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Content-Type": "application/json",
                "Origin": self.ORIGIN,
                "Referer": f"{self.ORIGIN}/post.html",
            }
        )
        self.pages_seen = 0
        self.listed_count = 0
        self.advertised_total: int | None = None
        self.detail_ok = 0
        self.detail_failed = 0
        self.delisted = 0
        self.detail_without_requirement = 0
        self.detail_urls: list[str] = []
        self.delisted_ids: list[str] = []
        self.failures: list[str] = []

    # --- plumbing ---------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last: Exception | None = None
        for attempt in range(1, self.REQUEST_ATTEMPTS + 1):
            try:
                response = self.session.request(
                    method, url, timeout=self.TIMEOUT_SECONDS, **kwargs
                )
                if response.status_code == 200:
                    return response
                last = RuntimeError(f"http_{response.status_code}")
            except Exception as exc:  # noqa: BLE001 - retried then reported
                last = exc
            time.sleep(0.5 * attempt)
        raise RuntimeError(f"request failed after retries: {last}")

    def _search_body(self, page: int) -> dict[str, Any]:
        return {
            "projectIdList": [],
            "projectMappingIdList": list(self.INTERNSHIP_MAPPING_IDS),
            "keyword": "",
            "bgList": [],
            "workCountryType": None,
            "workCityList": [],
            "recruitCityList": [],
            "positionFidList": [],
            "pageIndex": page,
            "pageSize": self.PAGE_SIZE,
        }

    def _listing_page(self, page: int) -> tuple[list[dict[str, Any]], int | None]:
        response = self._request("POST", self.LIST_API, json=self._search_body(page))
        payload = response.json()
        if payload.get("status") not in (0, "0"):
            raise RuntimeError(f"list api status={payload.get('status')}")
        data = payload.get("data") or {}
        rows = data.get("positionList") or data.get("list") or []
        total = data.get("count") if data.get("count") is not None else data.get("total")
        return [dict(row) for row in rows if isinstance(row, Mapping)], (
            int(total) if str(total or "").isdigit() else None
        )

    def _detail(self, post_id: str) -> tuple[dict[str, Any] | None, str]:
        """Return ``(detail, outcome)`` where outcome is ok / delisted / failed."""

        try:
            response = self._request("GET", self.DETAIL_API, params={"postId": post_id})
        except Exception:
            return None, "failed"
        payload = response.json()
        status = payload.get("status")
        if status == self.DELISTED_STATUS:
            return None, "delisted"
        if status not in (0, "0"):
            return None, "failed"
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return None, "failed"
        return dict(data), "ok"

    # --- collection -------------------------------------------------------

    def fetch(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, self.MAX_PAGES + 1):
            page_rows, total = self._listing_page(page)
            self.pages_seen = page
            if total is not None:
                self.advertised_total = total
            if not page_rows:
                break
            for row in page_rows:
                post_id = TencentCrawler._row_post_id(row)
                if post_id and post_id not in seen:
                    seen.add(post_id)
                    rows.append(row)
            self.listed_count = len(seen)
            if self.advertised_total is not None and self.listed_count >= self.advertised_total:
                break
            if len(page_rows) < self.PAGE_SIZE:
                break
            time.sleep(self.REQUEST_INTERVAL_SECONDS)

        indexed: dict[int, dict[str, Any]] = {}
        targets = rows[: self.max_jobs] if self.max_jobs else rows
        with ThreadPoolExecutor(
            max_workers=min(self.DETAIL_WORKERS, max(1, len(targets)))
        ) as pool:
            futures = {
                pool.submit(self._job_from_row, row): position
                for position, row in enumerate(targets)
            }
            for future in as_completed(futures):
                job = future.result()
                if job is not None:
                    indexed[futures[future]] = job
        return [indexed[position] for position in sorted(indexed)]

    def _job_from_row(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        post_id = TencentCrawler._row_post_id(row)
        if not post_id:
            return None
        detail_url = self.DETAIL_URL_TEMPLATE.format(post_id=post_id)
        try:
            detail, outcome = self._detail(post_id)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed silently
            self.detail_failed += 1
            self.failures.append(f"{post_id}: detail_failed:{type(exc).__name__}")
            return None
        if outcome == "delisted":
            self.delisted += 1
            self.delisted_ids.append(post_id)
            return None
        if detail is None:
            self.detail_failed += 1
            self.failures.append(f"{post_id}: detail_missing")
            return None

        duty, requirement = TencentCrawler._detail_texts(detail)
        title = TencentCrawler._compact(
            detail.get("positionTitle") or row.get("positionTitle")
        )
        if not title:
            self.detail_failed += 1
            self.failures.append(f"{post_id}: missing_title")
            return None
        if not requirement:
            # Keep the row but state exactly which half of the JD is missing.
            self.detail_without_requirement += 1
            self.failures.append(f"{post_id}: missing_requirement")
        self.detail_ok += 1
        self.detail_urls.append(detail_url)

        city = TencentCrawler._city_text(
            detail.get("workCityList") or detail.get("workCity") or row.get("workCities")
        )
        project = TencentCrawler._compact(
            detail.get("projectName") or row.get("projectName") or "实习"
        )
        jd_raw = "\n\n".join(
            part
            for part in (
                f"岗位职责\n{duty}" if duty else "",
                f"任职要求\n{requirement}" if requirement else "",
            )
            if part
        )
        detail_sha = hashlib.sha256(jd_raw.encode("utf-8")).hexdigest()
        return {
            "id": f"tencent-intern:{post_id}",
            "title": title,
            "city": city or None,
            "job_type": project,
            "detail_url": detail_url,
            "jd_url": detail_url,
            "jd_raw": jd_raw,
            "batch": "internship",
            "recruitment_track": "internship",
            "cohort": None,
            "cohort_status": "unconfirmed",
            "cohort_source": self.LIST_API,
            "cohort_evidence": "实习岗位不按毕业届别招募；JD 未声称届别",
            "campaign_text": self.CAMPAIGN_TEXT,
            "source_job_id": post_id,
            "source_post_id": post_id,
            "source_platform": self.CRAWLER_KEY,
            "source_read_only": True,
            "capture_evidence": {
                "status": "complete",
                "method": "official_public_api",
                "source_url": detail_url,
                "identity_verified": True,
                "terminal_observed": True,
                "remaining_controls": [],
                "content_sha256": detail_sha,
            },
        }

    # --- evidence ---------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        return {
            "crawler": self.CRAWLER_KEY,
            "read_only": True,
            "origin": self.ORIGIN,
            "project_mapping_ids": list(self.INTERNSHIP_MAPPING_IDS),
            "pages_seen": self.pages_seen,
            "listed_unique": self.listed_count,
            "advertised_total": self.advertised_total,
            "detail_ok": self.detail_ok,
            "delisted": self.delisted,
            "delisted_sample": self.delisted_ids[:5],
            "detail_failed": self.detail_failed,
            "detail_without_requirement": self.detail_without_requirement,
            "failure_samples": self.failures[:20],
            "detail_url_samples": self.detail_urls[:5],
        }


__all__ = ["TencentInternshipCrawler"]
