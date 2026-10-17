"""TASK-007: the internship workbench view and the API surface behind it.

Two layers are checked:

* the repository/API contract the view consumes — ``browse_jobs(track=...,
  city=...)`` and ``get_job`` must expose the internship facts, and a campus row
  must not leak into the internship view;
* the static wiring of the existing workbench — the nav entry, the ``track``
  query parameter and the card rendering.  These are string assertions on the
  shipped assets, because the repository's browser-based UI tests need a
  Playwright Chromium download that is not available here.
"""

from __future__ import annotations

from pathlib import Path

from packages.repositories.postgres import PostgresRecruitmentRepository
from packages.storage import Storage
from packages.storage.models import JobSnapshot

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"


def _storage() -> Storage:
    storage = Storage.from_url("sqlite:///:memory:")
    storage.initialize()
    with storage.write_transaction() as session:
        session.add(
            JobSnapshot(
                id="intern-1",
                source_ref="intern-1",
                company_id="tencent",
                title="Python开发暑期实习生",
                city="西安",
                detail_url="https://example.test/intern/1",
                cohort_status="unconfirmed",
                batch="internship",
                source="test",
                track="internship",
                internship_seasons=["summer"],
                conversion_opportunity="yes",
                internship_requirements={"duration_months": {"value": 3}},
                match_conditions={
                    "qualification": "qualified",
                    "retained": True,
                    "conditions": [
                        {
                            "name": "duration_months",
                            "status": "gap",
                            "negotiability": "unknown",
                            "confirmation_required": True,
                            "jd_excerpt": "能连续实习3个月以上",
                        }
                    ],
                },
            )
        )
        session.add(
            JobSnapshot(
                id="campus-1",
                source_ref="campus-1",
                company_id="tencent",
                title="C++软件开发工程师",
                city="西安",
                detail_url="https://example.test/campus/1",
                cohort=2027,
                cohort_status="confirmed",
                batch="formal",
                source="test",
                track="unknown",
            )
        )
    return storage


def test_internship_view_returns_only_internships():
    repo = PostgresRecruitmentRepository(_storage())

    page = repo.browse_jobs(track="internship")

    assert [item.id for item in page.items] == ["intern-1"]
    assert page.items[0].track == "internship"
    assert page.items[0].internship_seasons == ["summer"]
    assert page.items[0].conversion_opportunity == "yes"


def test_city_filter_narrows_the_internship_view():
    repo = PostgresRecruitmentRepository(_storage())

    assert [i.id for i in repo.browse_jobs(track="internship", city="西安").items] == [
        "intern-1"
    ]
    assert repo.browse_jobs(track="internship", city="北京").items == []
    # campus default is untouched by the new filter
    assert [i.id for i in repo.browse_jobs(city="西安").items] == ["campus-1"]


def test_job_detail_carries_the_internship_evidence():
    repo = PostgresRecruitmentRepository(_storage())

    detail = repo.get_job("intern-1")

    assert detail is not None
    assert detail.job.track == "internship"
    assert detail.job.internship_seasons == ["summer"]
    assert detail.job.conversion_opportunity == "yes"
    conditions = detail.job.match_conditions["conditions"]
    assert conditions[0]["jd_excerpt"] == "能连续实习3个月以上"


def test_workbench_has_an_internship_view_entry():
    html = (WEB / "index.html").read_text(encoding="utf-8")

    assert 'data-job-nav-mode="internship"' in html
    assert "实习岗位" in html


def test_workbench_requests_the_internship_track_and_labels_cards():
    script = (WEB / "app.js").read_text(encoding="utf-8")

    assert 'params.set("track", "internship")' in script
    assert 'job.track === "internship"' in script
    # The campus view must not start sending a track parameter.
    assert 'params.set("track", "campus")' not in script
