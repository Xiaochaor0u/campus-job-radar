"""API smoke tests for the intelligence endpoints.

Uses a throwaway SQLite file and mounts only the intelligence router, so the
test never touches the application lifespan (workers, mail sync, desktop
recovery) and stays offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.intelligence import record_pipeline_events
from packages.storage import JobSnapshot, Storage

UTC = timezone.utc
NOW = datetime.now(UTC)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database = tmp_path / "intelligence.sqlite"
    monkeypatch.setenv(
        "RECRUITOPS_DATABASE_URL", f"sqlite+pysqlite:///{database.as_posix()}"
    )
    from packages.config import get_settings

    get_settings.cache_clear()

    storage = Storage.from_url(get_settings().database_url, initialize=True)
    stamp = NOW - timedelta(hours=3)
    with storage.session() as session:
        session.add_all(
            [
                JobSnapshot(
                    id="job-1",
                    company_id="acme",
                    title="后端开发工程师",
                    city="上海",
                    detail_url="https://example.test/jobs/1",
                    cohort=2027,
                    cohort_status="confirmed",
                    batch="formal",
                    first_seen_at=stamp,
                    last_seen_at=stamp,
                    source="fixture",
                    source_ref=f"fixture:job:1:fp:{'b' * 64}",
                    created_at=stamp,
                    updated_at=stamp,
                )
            ]
        )
        session.commit()
    record_pipeline_events(
        storage,
        {"new_job_ids": ["job-1"], "changed_job_ids": [], "companies": [
            {"company_id": "acme", "company_name": "Acme 科技"}
        ]},
        run_id="run-1",
        run_started_at=NOW - timedelta(hours=1),
        clock=NOW,
    )
    with storage.session() as session:
        session.add(
            JobSnapshot(
                id="job-2",
                company_id="acme",
                title="算法工程师",
                city="北京",
                detail_url="https://example.test/jobs/2",
                cohort=2027,
                cohort_status="confirmed",
                batch="formal",
                first_seen_at=NOW,
                last_seen_at=NOW,
                source="fixture",
                source_ref=f"fixture:job:2:fp:{'c' * 64}",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
    record_pipeline_events(
        storage,
        {"new_job_ids": ["job-2"], "changed_job_ids": [], "companies": [
            {"company_id": "acme", "company_name": "Acme 科技"}
        ]},
        run_id="run-2",
        run_started_at=NOW - timedelta(minutes=5),
        clock=NOW,
    )

    from apps.api.intelligence import router, _storage

    _storage.cache_clear()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client
    _storage.cache_clear()
    get_settings.cache_clear()


def test_digest_returns_new_events(client: TestClient) -> None:
    response = client.get("/api/intelligence/digest", params={"hours": 24})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_new"] == 1
    assert payload["companies"][0]["company_name"] == "Acme 科技"
    assert payload["companies"][0]["items"][0]["title"] == "算法工程师"


def test_digest_track_filter_excludes_campus_rows(client: TestClient) -> None:
    response = client.get(
        "/api/intelligence/digest", params={"hours": 24, "track": "internship"}
    )
    assert response.status_code == 200
    assert response.json()["empty"] is True


def test_events_feed_filters_by_type(client: TestClient) -> None:
    response = client.get(
        "/api/intelligence/events", params={"hours": 24, "event_type": "new"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["event_type"] == "new"


def test_push_preview_renders_without_sending(client: TestClient) -> None:
    response = client.get("/api/intelligence/push-preview", params={"hours": 24})
    assert response.status_code == 200
    payload = response.json()
    assert "岗位情报日报" in payload["plain_message"]
    assert payload["webhook_count"] == 0
