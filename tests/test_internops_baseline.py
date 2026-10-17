
import pytest

from packages.matching.title_policy import screen_title_job


@pytest.mark.parametrize(
    "title,job_type,expected_eligible,expected_status",
    [
        ("测试开发实习生", "实习", False, "internship"),
        ("Python开发实习生", "实习", False, "internship"),
        ("测试开发工程师", "校招", True, "eligible"),
        ("Python开发工程师", "校招", False, "direction_out"),
        ("测试开发工程师", "实习", False, "internship"),
    ],
)
def test_original_title_screening(
    title,
    job_type,
    expected_eligible,
    expected_status,
):
    job = {
        "title": title,
        "job_type": job_type,
    }

    result = screen_title_job(job)

    assert result.eligible is expected_eligible
    assert result.analysis_status.value == expected_status
