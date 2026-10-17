
import pytest

from packages.matching.title_policy import screen_title_job


def test_default_rejects_internship():
    job = {
        "title": "测试开发实习生",
        "job_type": "实习"
    }

    result = screen_title_job(job)

    assert result.eligible is False


@pytest.mark.parametrize(
    "job",
    [
        {
            "title": "测试开发实习生",
            "job_type": "实习"
        },
        {
            "title": "测试开发工程师",
            "job_type": "实习"
        },
    ],
)
def test_include_internships(job):
    result = screen_title_job(
        job,
        include_internships=True
    )

    assert result.eligible is True


def test_unrelated_internship_rejected():
    job = {
        "title": "销售实习生",
        "job_type": "实习"
    }

    result = screen_title_job(
        job,
        include_internships=True
    )

    assert result.eligible is False
