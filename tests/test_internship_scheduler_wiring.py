"""InternOps: the internship crawler is reachable through the app's own pipeline.

The daily pipeline resolves each company's crawler from ``CRAWLER_MAP`` and
screens with the policy it is given.  Before this wiring, the internship crawler
only existed behind a standalone script, so the scheduler and the workbench could
not collect internships at all.
"""

from __future__ import annotations

from pathlib import Path

from packages.config import Settings
from packages.matching.policy import DEFAULT_POLICY, ScreeningMode, policy_for_mode
from packages.recruitment_core.crawlers import CRAWLER_MAP
from packages.recruitment_core.crawlers.tencent_intern import TencentInternshipCrawler

ROOT = Path(__file__).resolve().parents[1]


def test_internship_crawler_is_registered():
    assert CRAWLER_MAP.get("tencent_internship") is TencentInternshipCrawler


def test_registered_crawler_constructs_without_network():
    crawler = CRAWLER_MAP["tencent_internship"]("Tencent Internship", "https://join.qq.com")

    assert crawler is not None
    assert hasattr(crawler, "fetch")


def test_screening_mode_defaults_to_campus(monkeypatch):
    monkeypatch.delenv("RECRUITOPS_SCREENING_MODE", raising=False)

    settings = Settings()

    assert settings.screening_mode == "campus"
    assert policy_for_mode(settings.screening_mode) == DEFAULT_POLICY


def test_screening_mode_is_configurable(monkeypatch):
    monkeypatch.setenv("RECRUITOPS_SCREENING_MODE", "internship")

    assert Settings().screening_mode == "internship"
    assert policy_for_mode("internship").mode is ScreeningMode.INTERNSHIP


def test_scheduler_passes_the_configured_policy():
    """The scheduler must not silently fall back to campus."""

    source = (ROOT / "packages" / "scheduler" / "runtime.py").read_text(encoding="utf-8")

    assert "policy_for_mode(" in source
    assert 'getattr(configured, "screening_mode", "campus")' in source


def test_internship_company_is_configured():
    text = (ROOT / "config" / "companies.example.yaml").read_text(encoding="utf-8")

    assert "crawler: tencent_internship" in text
    assert "- internship" in text
