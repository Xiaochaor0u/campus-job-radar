"""Extract profile hints from pasted resume text using word lists only.

The 配置 page's parser calls a model.  Users without an API key would otherwise
be stuck at "upload a resume" — this module gives them a deterministic fallback:
scan the text for known skills / directions / degree / graduation year and
return suggestions the user confirms in the UI.

Deliberately conservative: it only reports what it literally matched, and
returns the matched surface text as evidence so the user can reject noise.
"""

from __future__ import annotations

import re
from typing import Any

# (canonical name, aliases) — aliases are matched case-insensitively.
_SKILL_TABLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Python", ("python",)),
    ("Java", ("java",)),
    ("C++", ("c++", "cpp")),
    ("C#", ("c#", "csharp")),
    ("Go", ("golang", "go语言")),
    ("Rust", ("rust",)),
    ("JavaScript", ("javascript", "js", "es6")),
    ("TypeScript", ("typescript", "ts")),
    ("React", ("react",)),
    ("Vue", ("vue",)),
    ("Node.js", ("node.js", "nodejs")),
    ("Spring", ("spring", "springboot", "spring boot")),
    ("Django", ("django",)),
    ("Flask", ("flask",)),
    ("MySQL", ("mysql",)),
    ("PostgreSQL", ("postgresql", "postgres")),
    ("Redis", ("redis",)),
    ("Kafka", ("kafka",)),
    ("Docker", ("docker",)),
    ("Kubernetes", ("kubernetes", "k8s")),
    ("Linux", ("linux",)),
    ("Git", ("git",)),
    ("PyTorch", ("pytorch",)),
    ("TensorFlow", ("tensorflow",)),
    ("机器学习", ("机器学习", "machine learning", "ml")),
    ("深度学习", ("深度学习", "deep learning")),
    ("大模型", ("大模型", "llm", "大语言模型")),
    ("RAG", ("rag", "检索增强")),
    ("Agent", ("agent", "智能体")),
    ("NLP", ("nlp", "自然语言处理")),
    ("计算机视觉", ("计算机视觉", "cv", "视觉算法")),
    ("推荐系统", ("推荐系统", "推荐算法")),
    ("数据挖掘", ("数据挖掘",)),
    ("数据分析", ("数据分析",)),
    ("SQL", ("sql",)),
    ("Spark", ("spark",)),
    ("Hadoop", ("hadoop",)),
    ("Flink", ("flink",)),
    ("自动化测试", ("自动化测试", "pytest", "selenium")),
    ("性能测试", ("性能测试", "压测")),
    ("运维", ("运维", "devops", "sre")),
    ("Android", ("android",)),
    ("iOS", ("ios",)),
    ("Flutter", ("flutter",)),
    ("嵌入式", ("嵌入式", "单片机", "stm32", "freertos")),
    ("FPGA", ("fpga",)),
    ("电路设计", ("电路", "原理图", "pcb")),
    ("机械设计", ("机械设计", "solidworks", "cad")),
    ("结构设计", ("结构设计",)),
    ("产品经理", ("产品经理", "产品设计", "prd")),
    ("用户运营", ("用户运营",)),
    ("内容运营", ("内容运营",)),
    ("数据分析工具", ("tableau", "power bi", "帆软")),
    ("UI设计", ("ui设计", "视觉设计")),
    ("交互设计", ("交互设计", "ux")),
    ("项目管理", ("项目管理", "pmp")),
    ("财务", ("财务", "会计")),
    ("法务", ("法务", "法律")),
    ("人力资源", ("人力资源", "招聘", "hr")),
)

_DIRECTION_TABLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("后端开发", ("后端", "服务端", "java开发", "golang开发", "python开发")),
    ("前端开发", ("前端", "web开发", "h5")),
    ("客户端开发", ("客户端", "android开发", "ios开发")),
    ("算法工程师", ("算法工程师", "算法岗", "算法研究")),
    ("大模型与智能体", ("大模型", "llm", "agent", "智能体", "rag", "aigc")),
    ("机器学习", ("机器学习", "深度学习", "模型训练")),
    ("计算机视觉", ("计算机视觉", "视觉算法", "图像算法")),
    ("自然语言处理", ("自然语言处理", "nlp")),
    ("推荐与搜索", ("推荐算法", "搜索算法", "推荐系统")),
    ("数据开发", ("数据开发", "数仓", "大数据开发")),
    ("数据分析", ("数据分析", "商业分析", "经营分析")),
    ("测试开发", ("测试开发", "自动化测试", "测开")),
    ("运维与安全", ("运维", "sre", "安全工程师", "渗透测试")),
    ("嵌入式与硬件", ("嵌入式", "硬件工程师", "电路", "fpga", "单片机")),
    ("游戏开发", ("游戏开发", "unity", "ue4", "ue5", "cocos")),
    ("产品经理", ("产品经理", "产品岗", "产品设计")),
    ("运营", ("运营", "用户增长", "内容运营")),
    ("设计", ("ui设计", "交互设计", "视觉设计")),
    ("机器人", ("机器人", "具身智能", "运动控制", "slam")),
    ("自动驾驶", ("自动驾驶", "感知算法", "规划控制")),
)

_DEGREE_TABLE = (
    ("博士", ("博士", "phd", "doctor")),
    ("硕士", ("硕士", "研究生", "master")),
    ("本科", ("本科", "学士", "bachelor")),
)

_GRAD_YEAR_RE = re.compile(r"(20\d{2})\s*(?:届|年(?:应届)?毕业|年6月毕业)")
_EN_TOKEN_RE = r"(?<![a-z0-9]){token}(?![a-z0-9])"


def _match_tokens(lowered: str, aliases: tuple[str, ...]) -> str | None:
    """Return the alias that appears in the text, if any."""

    for alias in aliases:
        token = alias.casefold()
        if token.isascii():
            if re.search(_EN_TOKEN_RE.format(token=re.escape(token)), lowered):
                return alias
        elif token in lowered:
            return alias
    return None


def extract_profile_hints(text: str) -> dict[str, Any]:
    """Return suggested skills / directions / degree / graduation year."""

    lowered = text.casefold()
    skills: list[str] = []
    skill_evidence: dict[str, str] = {}
    for canonical, aliases in _SKILL_TABLE:
        hit = _match_tokens(lowered, aliases)
        if hit is not None:
            skills.append(canonical)
            skill_evidence[canonical] = hit

    directions: list[str] = []
    direction_evidence: dict[str, str] = {}
    for canonical, aliases in _DIRECTION_TABLE:
        hit = _match_tokens(lowered, aliases)
        if hit is not None:
            directions.append(canonical)
            direction_evidence[canonical] = hit

    degree = None
    for canonical, aliases in _DEGREE_TABLE:
        if _match_tokens(lowered, aliases) is not None:
            degree = canonical
            break

    grad_match = _GRAD_YEAR_RE.search(text)
    graduation_year = int(grad_match.group(1)) if grad_match else None

    # Title keywords drive the first-pass title screen; directions are the most
    # useful seeds, topped up with the strongest technical skills.
    title_keywords: list[str] = []
    for word in directions:
        if word not in title_keywords:
            title_keywords.append(word)
    for word in skills[:8]:
        if word not in title_keywords:
            title_keywords.append(word)

    return {
        "skills": skills,
        "directions": directions,
        "degree": degree,
        "graduation_year": graduation_year,
        "title_keywords": title_keywords[:12],
        "evidence": {
            "skills": skill_evidence,
            "directions": direction_evidence,
        },
        "matched_count": len(skills) + len(directions),
        "note": "以上是按关键词表匹配出的建议，可能有误判，请在页面上自行删改后再保存。",
    }


__all__ = ["extract_profile_hints"]
