"""Offline tests for the job intelligence change feed.

Covers the writer's baseline rule, idempotency, removed detection, digest
aggregation and the four webhook payload shapes.  No network and no crawler is
involved: the pipeline result is supplied as the same mapping the scheduler
already produces.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from packages.intelligence import (
    NotifyConfigError,
    build_daily_digest,
    format_digest_text,
    load_notify_config,
    record_pipeline_events,
)
from packages.intelligence.webhook import (
    NotifyConfig,
    WebhookTarget,
    build_webhook_payload,
)
from packages.storage import (
    JobEvent,
    JobSnapshot,
    Storage,
    create_storage_engine,
    initialize_schema,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 9, 30, tzinfo=UTC)


def _storage() -> Storage:
    engine = create_storage_engine("sqlite:///:memory:")
    initialize_schema(engine)
    return Storage(engine)


def _job(
    job_id: str,
    company_id: str,
    *,
    title: str | None = None,
    availability: str = "active",
    updated_at: datetime | None = None,
    track: str = "campus",
) -> JobSnapshot:
    stamp = updated_at or NOW - timedelta(hours=2)
    return JobSnapshot(
        id=job_id,
        company_id=company_id,
        title=title or f"岗位 {job_id}",
        city="上海",
        detail_url=f"https://example.test/jobs/{job_id}",
        cohort=2027,
        cohort_status="confirmed",
        batch="formal",
        first_seen_at=stamp,
        last_seen_at=stamp,
        availability_status=availability,
        track=track,
        source="fixture",
        source_ref=f"fixture:job:{job_id}:fp:{'a' * 64}",
        created_at=stamp,
        updated_at=stamp,
    )


def _save(storage: Storage, *jobs: JobSnapshot) -> None:
    with storage.session() as session:
        session.add_all(jobs)
        session.commit()


def _events(storage: Storage) -> list[JobEvent]:
    with storage.session() as session:
        return list(session.query(JobEvent).order_by(JobEvent.created_at).all())


def _payload(
    *,
    new: tuple[str, ...] = (),
    changed: tuple[str, ...] = (),
    companies: dict[str, str] | None = None,
) -> dict:
    return {
        "new_job_ids": list(new),
        "changed_job_ids": list(changed),
        "companies": [
            {"company_id": company_id, "company_name": name}
            for company_id, name in (companies or {}).items()
        ],
        "written": True,
    }


def test_first_run_records_baseline_and_swallows_new_events() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"), _job("job-2", "acme"))

    result = record_pipeline_events(
        storage,
        _payload(new=("job-1", "job-2"), companies={"acme": "Acme"}),
        run_id="run-1",
        run_started_at=NOW - timedelta(minutes=10),
        clock=NOW,
    )

    assert result.baseline_companies == ("acme",)
    assert result.baseline_skipped_new_count == 2
    assert result.new_event_count == 0
    rows = _events(storage)
    assert [row.event_type for row in rows] == ["baseline"]
    assert rows[0].job_id == "__baseline__:acme"


def test_baseline_is_written_once_per_company_in_one_run() -> None:
    """Regression: the (run_id, job_id, event_type) key must not collapse
    several companies' baselines into one row."""

    storage = _storage()
    _save(
        storage,
        _job("job-1", "acme"),
        _job("job-2", "beta"),
        _job("job-3", "gamma"),
    )

    result = record_pipeline_events(
        storage,
        _payload(
            new=("job-1", "job-2", "job-3"),
            companies={"acme": "Acme", "beta": "Beta", "gamma": "Gamma"},
        ),
        run_id="run-1",
        run_started_at=NOW - timedelta(minutes=10),
        clock=NOW,
    )

    assert sorted(result.baseline_companies) == ["acme", "beta", "gamma"]
    baselines = [row for row in _events(storage) if row.event_type == "baseline"]
    assert sorted(row.company_id for row in baselines) == ["acme", "beta", "gamma"]
    assert len({row.job_id for row in baselines}) == 3

    # All three companies are baselined, so the next run reports every new job.
    _save(storage, _job("job-4", "acme"), _job("job-5", "beta"), _job("job-6", "gamma"))
    second = record_pipeline_events(
        storage,
        _payload(
            new=("job-4", "job-5", "job-6"),
            companies={"acme": "Acme", "beta": "Beta", "gamma": "Gamma"},
        ),
        run_id="run-2",
        run_started_at=NOW,
        clock=NOW + timedelta(minutes=1),
    )
    assert second.new_event_count == 3
    assert second.baseline_skipped_new_count == 0


def test_second_run_writes_new_events() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"))
    record_pipeline_events(
        storage,
        _payload(new=("job-1",), companies={"acme": "Acme"}),
        run_id="run-1",
        run_started_at=NOW - timedelta(minutes=10),
        clock=NOW,
    )

    _save(storage, _job("job-2", "acme", title="新岗位"))
    result = record_pipeline_events(
        storage,
        _payload(new=("job-2",), companies={"acme": "Acme"}),
        run_id="run-2",
        run_started_at=NOW - timedelta(minutes=5),
        clock=NOW + timedelta(minutes=1),
    )

    assert result.new_event_count == 1
    assert result.written is True
    new_events = [row for row in _events(storage) if row.event_type == "new"]
    assert len(new_events) == 1
    assert new_events[0].job_id == "job-2"
    assert new_events[0].title == "新岗位"
    assert new_events[0].company_name == "Acme"


def test_writer_is_idempotent_per_run_id() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"))
    record_pipeline_events(
        storage, _payload(new=("job-1",), companies={"acme": "Acme"}),
        run_id="run-1", run_started_at=NOW - timedelta(minutes=10), clock=NOW,
    )
    _save(storage, _job("job-2", "acme"))
    payload = _payload(new=("job-2",), companies={"acme": "Acme"})

    first = record_pipeline_events(
        storage, payload, run_id="run-2",
        run_started_at=NOW - timedelta(minutes=1), clock=NOW,
    )
    second = record_pipeline_events(
        storage, payload, run_id="run-2",
        run_started_at=NOW - timedelta(minutes=1), clock=NOW,
    )

    assert first.new_event_count == 1
    assert second.written is False
    assert len([row for row in _events(storage) if row.event_type == "new"]) == 1


def test_changed_jobs_become_updated_events() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"))
    record_pipeline_events(
        storage, _payload(new=("job-1",), companies={"acme": "Acme"}),
        run_id="run-1", run_started_at=NOW - timedelta(minutes=10), clock=NOW,
    )
    result = record_pipeline_events(
        storage,
        _payload(changed=("job-1",), companies={"acme": "Acme"}),
        run_id="run-2",
        run_started_at=NOW - timedelta(minutes=1),
        clock=NOW + timedelta(minutes=1),
    )

    assert result.updated_event_count == 1
    updated = [row for row in _events(storage) if row.event_type == "updated"]
    assert updated[0].content_fingerprint == "a" * 64


def test_removed_event_for_inactive_job_inside_window() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"))
    record_pipeline_events(
        storage, _payload(new=("job-1",), companies={"acme": "Acme"}),
        run_id="run-1", run_started_at=NOW - timedelta(minutes=10), clock=NOW,
    )

    # Offline reconciliation marks the job inactive during the next run.
    with storage.session() as session:
        job = session.get(JobSnapshot, "job-1")
        assert job is not None
        job.availability_status = "inactive"
        job.updated_at = NOW + timedelta(minutes=2)
        session.commit()

    result = record_pipeline_events(
        storage,
        _payload(companies={"acme": "Acme"}),
        run_id="run-2",
        run_started_at=NOW + timedelta(minutes=1),
        clock=NOW + timedelta(minutes=5),
    )

    assert result.removed_event_count == 1
    removed = [row for row in _events(storage) if row.event_type == "removed"]
    assert removed[0].job_id == "job-1"


def test_removed_event_not_repeated_when_already_removed() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"))
    record_pipeline_events(
        storage, _payload(new=("job-1",), companies={"acme": "Acme"}),
        run_id="run-1", run_started_at=NOW - timedelta(minutes=10), clock=NOW,
    )
    with storage.session() as session:
        job = session.get(JobSnapshot, "job-1")
        job.availability_status = "inactive"
        job.updated_at = NOW + timedelta(minutes=2)
        session.commit()
    record_pipeline_events(
        storage, _payload(companies={"acme": "Acme"}), run_id="run-2",
        run_started_at=NOW + timedelta(minutes=1), clock=NOW + timedelta(minutes=5),
    )
    second = record_pipeline_events(
        storage, _payload(companies={"acme": "Acme"}), run_id="run-3",
        run_started_at=NOW + timedelta(minutes=6), clock=NOW + timedelta(minutes=9),
    )

    assert second.removed_event_count == 0


def test_digest_groups_by_company_and_excludes_baseline() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"), _job("job-2", "beta"))
    record_pipeline_events(
        storage,
        _payload(new=("job-1",), companies={"acme": "Acme", "beta": "Beta"}),
        run_id="run-1", run_started_at=NOW - timedelta(minutes=10), clock=NOW,
    )
    _save(storage, _job("job-3", "acme", title="后端开发"))
    with storage.session() as session:
        beta_job = session.get(JobSnapshot, "job-2")
        beta_job.track = "internship"
        beta_job.title = "实习岗"
        session.commit()

    record_pipeline_events(
        storage, _payload(new=("job-3",), companies={"acme": "Acme"}),
        run_id="run-2", run_started_at=NOW, clock=NOW + timedelta(minutes=1),
    )
    record_pipeline_events(
        storage, _payload(new=("job-2",), companies={"beta": "Beta"}),
        run_id="run-3", run_started_at=NOW, clock=NOW + timedelta(minutes=2),
    )

    digest = build_daily_digest(storage, hours=24, now=NOW + timedelta(minutes=10))
    assert digest.total_new == 1  # beta was baselined by run-1 as well
    names = [company.company_name for company in digest.companies]
    assert names == ["Acme"]
    assert digest.companies[0].items[0].title == "后端开发"

    internship = build_daily_digest(
        storage, hours=24, track="internship", now=NOW + timedelta(minutes=10)
    )
    assert internship.empty is True


def test_digest_window_excludes_old_events() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"))
    record_pipeline_events(
        storage, _payload(new=("job-1",), companies={"acme": "Acme"}),
        run_id="run-1", run_started_at=NOW - timedelta(minutes=10), clock=NOW,
    )
    _save(storage, _job("job-2", "acme"))
    record_pipeline_events(
        storage, _payload(new=("job-2",), companies={"acme": "Acme"}),
        run_id="run-2", run_started_at=NOW, clock=NOW,
    )

    recent = build_daily_digest(storage, hours=1, now=NOW + timedelta(minutes=30))
    stale = build_daily_digest(storage, hours=1, now=NOW + timedelta(days=2))
    assert recent.total_new == 1
    assert stale.empty is True


def test_notify_config_missing_file_disables_push(tmp_path: Path) -> None:
    config = load_notify_config(tmp_path / "absent.yaml")
    assert config.enabled is False


def test_notify_config_reads_targets_and_digest(tmp_path: Path) -> None:
    path = tmp_path / "notify.yaml"
    path.write_text(
        "webhooks:\n"
        "  - name: 宿舍群\n"
        "    type: wecom\n"
        "    url: https://example.test/hook\n"
        "digest:\n"
        "  hours: 48\n"
        "  max_items_per_push: 3\n",
        encoding="utf-8",
    )
    config = load_notify_config(path)
    assert config.enabled is True
    assert config.webhooks[0].type == "wecom"
    assert config.webhooks[0].label == "宿舍群"
    assert config.digest_hours == 48.0
    assert config.max_items_per_push == 3


def test_notify_config_rejects_unknown_type(tmp_path: Path) -> None:
    path = tmp_path / "notify.yaml"
    path.write_text(
        "webhooks:\n  - type: telegram\n    url: https://example.test/hook\n",
        encoding="utf-8",
    )
    with pytest.raises(NotifyConfigError):
        load_notify_config(path)


def test_webhook_payload_shapes() -> None:
    wecom = WebhookTarget(type="wecom", url="https://example.test/wecom")
    url, body = build_webhook_payload(wecom, "**标题**")
    assert url == "https://example.test/wecom"
    assert b'"msgtype": "markdown"' in body or b'"msgtype":"markdown"' in body

    dingtalk = WebhookTarget(
        type="dingtalk", url="https://example.test/dd", secret="SECtest"
    )
    url, body = build_webhook_payload(dingtalk, "标题")
    assert "timestamp=" in url and "sign=" in url
    assert b"markdown" in body

    feishu = WebhookTarget(type="feishu", url="https://example.test/fs", secret="s")
    _url, body = build_webhook_payload(feishu, "标题")
    assert b'"msg_type": "text"' in body or b'"msg_type":"text"' in body
    assert b"sign" in body

    generic = WebhookTarget(type="generic", url="http://127.0.0.1:5700/send_group_msg")
    _url, body = build_webhook_payload(generic, "群消息")
    assert b'"message"' in body


def test_digest_text_mentions_counts() -> None:
    storage = _storage()
    _save(storage, _job("job-1", "acme"))
    record_pipeline_events(
        storage, _payload(new=("job-1",), companies={"acme": "Acme"}),
        run_id="run-1", run_started_at=NOW - timedelta(minutes=10), clock=NOW,
    )
    _save(storage, _job("job-2", "acme", title="算法工程师"))
    record_pipeline_events(
        storage, _payload(new=("job-2",), companies={"acme": "Acme"}),
        run_id="run-2", run_started_at=NOW, clock=NOW,
    )

    digest = build_daily_digest(storage, hours=24, now=NOW + timedelta(minutes=5))
    message = format_digest_text(digest, max_items_per_type=5, markdown=False)
    assert "岗位情报日报" in message
    assert "算法工程师" in message
    assert "新增 1" in message


def test_push_daily_digest_without_config_sends_nothing() -> None:
    from packages.intelligence import push_daily_digest

    storage = _storage()
    digest = build_daily_digest(storage, hours=24, now=NOW)
    assert push_daily_digest(digest, NotifyConfig()) == []
