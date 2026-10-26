"""On-demand profile for one posting: stated bar, salary wording, peer context.

Why on demand: the corpus is ~22k announcements and only a small slice carries
salary wording or a stated degree bar. Scoring every job up front would burn
model calls on rows that have nothing to read, so this module is built to be
called one job at a time from the UI.

Ground rules (they matter more than the feature):
  * Salary comes from the announcement text only. When it is absent we say so.
    We never estimate a number, because a wrong salary figure is worse for a
    student than "公告未披露".
  * "录取学生层次" is not observable. No offer-holder roster is public, so this
    module reads the *stated* bar (degree / school / experience wording) and
    labels it as a stated bar, not as an admission record.
  * Every extracted field keeps the sentence it came from, so the UI can show
    evidence instead of asking for trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from packages.storage import JobSnapshot, Storage

DISCLAIMER = (
    "以上「层次」来自招聘公告写明的条件，不是真实录取名单——没有公开数据能支持"
    "「实际录了哪些学校的学生」这种结论。薪资只转述公告原文，未披露时会直接说明。"
)

# --- salary -----------------------------------------------------------------

_SALARY_WORDS = r"(?:年薪|年包|年度总包|总包|年收入|首年|起薪|月薪|月收入|薪资|薪酬|待遇|工资|实习补贴|日薪|时薪)"
#: 「薪资大概 4-6k起步」这类写法在修饰语上非常随意，允许一小段非数字间隔。
_GAP = r"[^0-9\n]{0,14}"
_MONEY = r"[0-9]+(?:\.[0-9]+)?\s*(?:万|w|W|千|k|K|元)"
#: 「25-35 万」只写一次单位，所以区间左端必须允许没有单位。
_MONEY_OPEN = r"[0-9]+(?:\.[0-9]+)?\s*(?:万|w|W|千|k|K|元)?"
_RANGE = rf"{_MONEY_OPEN}\s*[-~—–至到]\s*[0-9]+(?:\.[0-9]+)?\s*(?:万|w|W|千|k|K|元)"
_SUFFIX = r"(?:左右|上下|起步|以上|起)?"
_PER = r"(?:\s*/\s*(?:天|日|月|年|小时))?"
_SALARY_PATTERNS = (
    # 「年薪大概 15-20 万」「薪资 4-6k 起步」「实习补贴 130-170 元/日」
    # 单位后缀放在捕获组里：「元」和「元/日」是两个完全不同的结论。
    re.compile(rf"{_SALARY_WORDS}{_GAP}({_RANGE}{_SUFFIX}{_PER})"),
    # 「130-170 元/日」「15-20 万/年」不带薪资词时也能认出来
    re.compile(rf"({_RANGE}{_PER})"),
    # 「实习薪资 3500/月」「月薪 8k」
    re.compile(rf"{_SALARY_WORDS}{_GAP}({_MONEY}{_PER})"),
    # 「年薪能有 20 万左右」只认带万/千/K 的单个数字
    re.compile(rf"{_SALARY_WORDS}{_GAP}([0-9]+(?:\.[0-9]+)?\s*(?:万|w|W|千|k|K){_SUFFIX})"),
    # 「300 元/天」单独出现也算（实习日薪的常见写法）
    re.compile(r"([0-9]{2,4}\s*元\s*/\s*(?:天|日|小时))"),
)

# --- stated bar -------------------------------------------------------------

_DEGREES = ("大专", "本科", "硕士", "博士")
_DEGREE_ORDER = {name: index for index, name in enumerate(_DEGREES)}
_SCHOOL_TIER = re.compile(r"985|211|双一流|C9|重点院校|重点大学|QS\s*(?:前)?\s*\d+", re.I)
_EXPERIENCE = re.compile(
    r"([0-9]+)\s*年(?:及)?以上[^。；;\n]{0,8}(?:工作)?经[验历]"
    r"|需要?[^。；;\n]{0,6}实习经[验历]"
    r"|有[^。；;\n]{0,8}(?:项目|实习)[^。；;\n]{0,6}经[验历]"
)
_MAJOR = re.compile(r"(?:专业要求|专业方向|相关专业|所学专业)\s*[:：]\s*([^\n。；;]{2,40})")
_CERT = re.compile(r"CET-?[46]|英语[四六]级|雅思|托福|CPA|司法考试|执业医", re.I)


def _sentence_around(text: str, start: int, end: int, pad: int = 40) -> str:
    """Return a short readable excerpt so the UI can quote the source line."""

    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def extract_salary(text: str) -> dict[str, Any]:
    """Find salary wording in the announcement, or report that it is absent."""

    body = str(text or "")
    if not body.strip():
        return {"found": False, "text": "", "evidence": "", "kind": "none"}
    for pattern in _SALARY_PATTERNS:
        match = pattern.search(body)
        if match:
            # group(1) is the money token itself; the rest of the match is just
            # wording like 「薪资大概」and reads badly in a table cell.
            amount = re.sub(r"\s+", "", match.group(1) or match.group(0))
            return {
                "found": True,
                "text": amount,
                "evidence": _sentence_around(body, match.start(), match.end()),
                "kind": "range" if re.search(r"[-~—–至到]", amount) else "single",
            }
    return {"found": False, "text": "", "evidence": "", "kind": "none"}


def extract_requirements(text: str) -> list[dict[str, str]]:
    """Pull only the bar the announcement *states*, with the sentence it came from."""

    body = str(text or "")
    found: list[dict[str, str]] = []
    if not body.strip():
        return found

    degrees = [name for name in _DEGREES if name in body]
    if degrees:
        highest = max(degrees, key=lambda name: _DEGREE_ORDER[name])
        # 只报最高学历，同时把「及以上」这类写法留在证据里
        found.append({
            "label": "学历要求",
            "value": f"{highest}（公告提到：{'、'.join(degrees)}）" if len(degrees) > 1 else highest,
            "evidence": _first_sentence_with(body, highest),
        })

    tier = _SCHOOL_TIER.search(body)
    if tier:
        found.append({
            "label": "院校门槛",
            "value": tier.group(0),
            "evidence": _sentence_around(body, tier.start(), tier.end()),
        })

    experience = _EXPERIENCE.search(body)
    if experience:
        found.append({
            "label": "经验要求",
            "value": re.sub(r"\s+", "", experience.group(0))[:40],
            "evidence": _sentence_around(body, experience.start(), experience.end()),
        })

    major = _MAJOR.search(body)
    if major:
        found.append({
            "label": "专业方向",
            "value": major.group(1).strip()[:40],
            "evidence": _sentence_around(body, major.start(), major.end()),
        })

    cert = _CERT.search(body)
    if cert:
        found.append({
            "label": "证书 / 语言",
            "value": cert.group(0),
            "evidence": _sentence_around(body, cert.start(), cert.end()),
        })
    return found


def _first_sentence_with(text: str, needle: str) -> str:
    index = text.find(needle)
    if index < 0:
        return ""
    return _sentence_around(text, index, index + len(needle))


# --- peer context -----------------------------------------------------------

#: Title noise that says nothing about the direction itself.
_TITLE_NOISE = frozenset({
    "实习", "实习生", "校招", "秋招", "春招", "提前批", "应届", "应届生", "社招",
    "岗位", "职位", "招聘", "工程师", "助理", "研究员", "专员", "管培生", "储备",
    "2026", "2027", "2028", "26", "27", "28",
})
#: 「自动化测试工程师」→「自动化测试」，否则同方向的岗位永远对不上。
_TITLE_SUFFIX = re.compile(
    r"(?:工程师|研究员|实习生|实习|助理|专员|经理|主管|专家|顾问|管培生|储备|岗位|岗|开发)+$"
)
#: 「J20217」「A0123」这类招聘系统内部编号不是方向。
_JOB_CODE = re.compile(r"^[A-Za-z]{1,4}[-_]?\d{3,}$")


def direction_tokens(title: str) -> list[str]:
    """Rough direction words from a posting title, noise removed."""

    raw = str(title or "")
    parts = re.split(r"[（）()【】\[\]/、,;，；|·\s-]+", raw)
    tokens: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        token = _TITLE_SUFFIX.sub("", stripped) or stripped
        # 只留 3 字以上的方向词：像「AI」「PM」这种两字词会在任何标题里命中，
        # 用它判同方向等于把所有岗位算成同一类。
        if len(token) < 3 or token in _TITLE_NOISE or _JOB_CODE.match(token):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _company_peer_context(storage: Storage, job: JobSnapshot) -> dict[str, Any]:
    """How this posting sits among the same company's other postings."""

    tokens = direction_tokens(job.title)
    with storage.session() as session:
        rows = session.scalars(
            select(JobSnapshot).where(JobSnapshot.company_id == job.company_id)
        ).all()

    same_direction = [
        row for row in rows
        if row.id != job.id and any(token in (row.title or "") for token in tokens)
    ]
    cities: dict[str, int] = {}
    for row in rows:
        city = (row.city or "").strip()
        if city:
            cities[city] = cities.get(city, 0) + 1
    seasons: dict[str, int] = {}
    for row in rows:
        label = f"{row.cohort}届" if row.cohort else (row.batch or "未知批次")
        seasons[label] = seasons.get(label, 0) + 1
    # 城市字段常写成「北京 / 上海」，按第一个地点归并已经够用
    top_cities = sorted(cities.items(), key=lambda item: (-item[1], item[0]))[:4]
    return {
        "company_jobs": len(rows),
        "same_direction_jobs": len(same_direction),
        "same_direction_titles": [row.title for row in same_direction[:5]],
        "top_cities": top_cities,
        "seasons": sorted(seasons.items(), key=lambda item: -item[1])[:4],
    }


def rate_bar(requirements: list[dict[str, str]], peer: dict[str, Any]) -> dict[str, Any]:
    """Turn stated requirements into a coarse, explainable bar estimate."""

    score = 0
    reasons: list[str] = []
    for item in requirements:
        label, value = item["label"], item["value"]
        if label == "学历要求":
            if "博士" in value:
                score += 3
                reasons.append("公告写明博士学历")
            elif "硕士" in value:
                score += 2
                reasons.append("公告写明硕士学历")
            elif "本科" in value:
                reasons.append("公告写明本科即可")
        elif label == "院校门槛":
            score += 2
            reasons.append(f"公告提到院校门槛（{value}）")
        elif label == "经验要求":
            if "年以上" in value or "经" in value:
                score += 1
                reasons.append("公告要求相关经验")
        elif label == "证书 / 语言":
            score += 1
            reasons.append(f"公告要求{value}")
    company_jobs = int(peer.get("company_jobs") or 0)
    if company_jobs and company_jobs <= 3:
        score += 1
        reasons.append(f"该公司库内仅 {company_jobs} 个岗位，名额可能较少")
    elif company_jobs >= 60:
        score -= 1
        reasons.append(f"该公司库内 {company_jobs} 个岗位，规模较大")

    if not requirements:
        return {
            "level": "信息不足",
            "score": 0,
            "reasons": ["公告没有写明学历、院校或经验要求，无法据此判断门槛"],
        }
    if score >= 4:
        level = "要求偏高"
    elif score >= 2:
        level = "要求中等"
    else:
        level = "要求较低"
    return {"level": level, "score": max(0, score), "reasons": tuple(reasons)}


# --- model comment ----------------------------------------------------------

_COMMENT_SYSTEM = """你在帮一名大学生判断一个岗位的竞争情况。你只能使用用户给出的字段，
不得引入任何外部知识。硬性规则：
1. 薪资字段 found=false 时，禁止出现任何薪资数字、区间或「大概/通常/听说」之类的估计。
2. 不得编造录取学生的学校、学历分布、录取比例等数据；只能引用 requirements 里写明的条件。
3. 输出 2-3 句中文，先说岗位门槛信号，再说这个岗位在该公司的相对位置，最后给一句可执行的建议。
4. 不要复述 disclaimer，不要用「综上所述」。"""


def _model_comment(payload: dict[str, Any]) -> tuple[str, str]:
    """Optional model read-out. Returns (text, status)."""

    try:
        from packages.config import get_settings
    except Exception:  # pragma: no cover - configuration is always importable
        return "", "disabled"
    settings = get_settings()
    if not (settings.llm_enabled and settings.llm_api_key.strip()):
        return "", "disabled"
    try:
        import json

        from packages.matching.client import DeepSeekClient, DeepSeekClientError

        client = DeepSeekClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            endpoint=settings.llm_endpoint,
            api_style=settings.model_api_style,
            timeout=min(45.0, settings.llm_timeout_seconds),
            max_tokens=600,
            max_attempts=1,
        )
        response = client.complete(
            system_prompt=_COMMENT_SYSTEM,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            max_tokens=600,
        )
        text = str(getattr(response, "content", "") or "").strip()
        if not text:
            return "", "empty"
        # Cheap guard: a salary number must not appear when the announcement had none.
        if not payload["salary"]["found"] and re.search(r"[0-9]+\s*(?:万|k|K)", text):
            return "", "blocked_salary_guess"
        return text, "ok"
    except DeepSeekClientError as error:
        return "", f"model_error:{getattr(error, 'code', 'unknown')}"
    except Exception:  # noqa: BLE001 - analysis must never break the page
        return "", "model_error"


# --- public API -------------------------------------------------------------


@dataclass(frozen=True)
class JobInsight:
    found: bool
    job: dict[str, Any]
    salary: dict[str, Any]
    requirements: tuple[dict[str, str], ...]
    peer: dict[str, Any]
    bar: dict[str, Any]
    comment: str
    comment_status: str
    disclaimer: str = DISCLAIMER
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "job": self.job,
            "salary": self.salary,
            "requirements": list(self.requirements),
            "peer": self.peer,
            "bar": self.bar,
            "comment": self.comment,
            "comment_status": self.comment_status,
            "disclaimer": self.disclaimer,
            "message": self.message,
        }


def build_job_insight(
    storage: Storage,
    job_id: str,
    *,
    use_model: bool = True,
) -> JobInsight:
    """Assemble the profile for one posting. Never raises for a missing job."""

    if not isinstance(storage, Storage):
        raise TypeError("storage must be a Storage instance")
    storage.initialize()
    wanted = str(job_id or "").strip()
    if not wanted:
        raise ValueError("job_id is required")

    with storage.session() as session:
        job = session.scalars(
            select(JobSnapshot).where(JobSnapshot.id == wanted).limit(1)
        ).first()
        if job is None:
            return JobInsight(
                found=False,
                job={},
                salary={"found": False, "text": "", "evidence": "", "kind": "none"},
                requirements=(),
                peer={},
                bar={"level": "信息不足", "score": 0, "reasons": ("岗位不存在",)},
                comment="",
                comment_status="disabled",
                message="这个岗位已不在库中（可能被重新采集时替换了 id），请刷新列表后重试。",
            )
        evidence = dict(job.capture_evidence or {})
        body = str(job.jd_raw or "")
        job_payload = {
            "id": job.id,
            "company_id": str(job.company_id),
            "company_name": str(job.company_id).replace("offerbiu:", ""),
            "title": job.title,
            "city": job.city or "",
            "track": job.track,
            "batch": job.batch,
            "cohort": job.cohort,
            "source": job.source if hasattr(job, "source") else "",
            "posted_at": job.first_seen_at.isoformat() if job.first_seen_at else "",
            "deadline_text": str(evidence.get("deadline_text") or ""),
            "detail_url": job.detail_url or "",
            "text_length": len(body),
        }
        peer = _company_peer_context(storage, job)

    salary = extract_salary(body)
    requirements = extract_requirements(body)
    bar = rate_bar(requirements, peer)

    comment, status = "", "disabled"
    if use_model:
        comment, status = _model_comment({
            "job": job_payload,
            "salary": salary,
            "requirements": requirements,
            "peer": peer,
            "bar": bar,
        })

    return JobInsight(
        found=True,
        job=job_payload,
        salary=salary,
        requirements=tuple(requirements),
        peer=peer,
        bar=bar,
        comment=comment,
        comment_status=status,
    )
