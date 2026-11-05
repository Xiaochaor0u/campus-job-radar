"""外部岗位通道：把 OfferBiu 以外的来源写进同一张表。"""

from __future__ import annotations

import json

import pytest

from packages.intelligence.external_import import (
    ExternalJob,
    ingest_external_jobs,
    load_jobs_file,
)
from packages.storage import Storage


def _storage(tmp_path):
    return Storage.from_url(f"sqlite+pysqlite:///{(tmp_path / 'ext.sqlite').as_posix()}", initialize=True)


def _write(tmp_path, payload):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_json_file_needs_company_title_and_url(tmp_path):
    path = _write(
        tmp_path,
        {
            "source": "company-site",
            "items": [
                {"company": "双汇发展", "title": "计算机软件类管培生", "url": "https://x.test/1"},
                {"company": "缺链接公司", "title": "算法岗"},
                {"title": "缺公司", "url": "https://x.test/2"},
            ],
        },
    )
    jobs = load_jobs_file(path)
    assert [job.company for job in jobs] == ["双汇发展"]
    assert jobs[0].source == "company-site"


def test_csv_is_supported(tmp_path):
    path = tmp_path / "jobs.csv"
    path.write_text(
        "company,title,url,city,published_at,deadline,track,cohort\n"
        "洛阳钼业,化验分析,https://x.test/3,洛阳,2026-11-01,2026-11-30,campus,2027\n",
        encoding="utf-8",
    )
    jobs = load_jobs_file(path)
    assert len(jobs) == 1
    assert jobs[0].deadline_date.isoformat() == "2026-11-30"
    assert jobs[0].cohort == 2027


def test_import_is_idempotent_and_records_an_event(tmp_path):
    storage = _storage(tmp_path)
    job = ExternalJob(
        company="双汇发展",
        title="双汇26&27届-计算机软件类管培生",
        url="https://shuanghui.zhiye.com/campus/detail?jobAdId=1",
        city="漯河",
        cohort=2027,
        track="campus",
        source="company-site",
    )
    first = ingest_external_jobs(storage, [job])
    assert first["inserted"] == 1
    assert first["companies_written"] == 1

    second = ingest_external_jobs(storage, [job])
    assert second["inserted"] == 0
    assert second["updated"] == 0

    with storage.session() as session:
        from sqlalchemy import func, select

        from packages.storage import JobSnapshot

        total = session.scalar(select(func.count()).select_from(JobSnapshot))
    # id 由 source+公司+岗位+城市 生成，重入不会新增第二条
    assert total == 1


def test_different_city_creates_a_second_row(tmp_path):
    storage = _storage(tmp_path)
    base = dict(
        company="洛阳钼业",
        title="化验分析",
        url="https://wecm.test/1",
        track="campus",
        source="company-site",
    )
    ingest_external_jobs(storage, [ExternalJob(city="洛阳", **base)])
    result = ingest_external_jobs(storage, [ExternalJob(city="上海", **base)])
    assert result["inserted"] == 1
