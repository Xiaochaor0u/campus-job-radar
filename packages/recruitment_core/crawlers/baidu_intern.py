"""Baidu internship postings.

Same public gateway as the campus crawler, but ``recruitType="实习生"``.  The API
rejects any other spelling ("实习" returns zero rows, "校招" is the campus track).
Internship rows are tagged with the internship batch so the pipeline keeps them.
"""

from __future__ import annotations

from .baidu import BaiduCrawler


class BaiduInternshipCrawler(BaiduCrawler):
    CRAWLER_KEY = "baidu_internship"
    RECRUIT_TYPE = "实习生"
