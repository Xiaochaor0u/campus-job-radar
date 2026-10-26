"""Group-bot push for the daily job digest.

Supported webhook targets:

* ``wecom``     — 企业微信群机器人 (WeCom group bot, markdown message)
* ``dingtalk``  — 钉钉群机器人 (DingTalk group bot, optional HMAC signing)
* ``feishu``    — 飞书群机器人 (Feishu/Lark group bot, optional signing)
* ``generic``   — any HTTP endpoint accepting a JSON body with a ``message``
                  field; this covers QQ group bots bridged through OneBot or
                  any self-hosted relay.

Configuration lives in ``config/notify.yaml`` (see ``notify.example.yaml``).
Requests use a short timeout and never raise into the scheduler: a failed
delivery is reported in the result instead of failing the whole task.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml

from .digest import DigestResult

SUPPORTED_WEBHOOK_TYPES = ("wecom", "dingtalk", "feishu", "generic")
DEFAULT_TIMEOUT_SECONDS = 10.0
_WECOM_MARKDOWN_LIMIT = 4096
_DINGTALK_MARKDOWN_LIMIT = 20000
_FEISHU_TEXT_LIMIT = 40000


class NotifyConfigError(RuntimeError):
    """Raised when notify.yaml cannot be read or has an invalid shape."""


@dataclass(frozen=True, slots=True)
class WebhookTarget:
    """One configured group bot endpoint."""

    type: str
    url: str
    secret: str = ""
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.type


@dataclass(frozen=True, slots=True)
class NotifyConfig:
    """Parsed notify.yaml."""

    webhooks: tuple[WebhookTarget, ...] = ()
    digest_hours: float = 24.0
    max_items_per_push: int = 8

    @property
    def enabled(self) -> bool:
        return bool(self.webhooks)


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    """Result of one delivery attempt."""

    target: str
    ok: bool
    status_code: int | None = None
    error: str = ""
    payload_bytes: int = 0


def _compact(value: Any) -> str:
    return str(value or "").strip()


def load_notify_config(path: Path | str | None = None) -> NotifyConfig:
    """Read notify.yaml; a missing file yields an empty (disabled) config."""

    if path is None:
        path = Path(__file__).resolve().parents[2] / "config" / "notify.yaml"
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        return NotifyConfig()
    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise NotifyConfigError(f"unable to read notify config: {resolved}") from exc
    if payload is None:
        return NotifyConfig()
    if not isinstance(payload, Mapping):
        raise NotifyConfigError("notify.yaml must be a mapping")

    raw_webhooks = payload.get("webhooks")
    if raw_webhooks is None:
        raw_webhooks = []
    if not isinstance(raw_webhooks, Sequence) or isinstance(raw_webhooks, (str, bytes)):
        raise NotifyConfigError("notify.yaml webhooks must be a list")
    webhooks: list[WebhookTarget] = []
    for index, raw in enumerate(raw_webhooks):
        if not isinstance(raw, Mapping):
            raise NotifyConfigError(f"webhooks[{index}] must be a mapping")
        url = _compact(raw.get("url"))
        hook_type = _compact(raw.get("type")).casefold()
        if not url:
            raise NotifyConfigError(f"webhooks[{index}] requires a url")
        if hook_type not in SUPPORTED_WEBHOOK_TYPES:
            raise NotifyConfigError(
                f"webhooks[{index}] type must be one of: {', '.join(SUPPORTED_WEBHOOK_TYPES)}"
            )
        webhooks.append(
            WebhookTarget(
                type=hook_type,
                url=url,
                secret=_compact(raw.get("secret")),
                name=_compact(raw.get("name")),
            )
        )

    digest_section = payload.get("digest")
    hours = 24.0
    max_items = 8
    if isinstance(digest_section, Mapping):
        try:
            hours = float(digest_section.get("hours", 24.0))
        except (TypeError, ValueError):
            raise NotifyConfigError("digest.hours must be a number")
        try:
            max_items = int(digest_section.get("max_items_per_push", 8))
        except (TypeError, ValueError):
            raise NotifyConfigError("digest.max_items_per_push must be an integer")
    if hours <= 0:
        raise NotifyConfigError("digest.hours must be positive")
    if max_items < 1:
        raise NotifyConfigError("digest.max_items_per_push must be positive")

    return NotifyConfig(
        webhooks=tuple(webhooks),
        digest_hours=hours,
        max_items_per_push=max_items,
    )


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_digest_text(
    digest: DigestResult,
    *,
    max_items_per_type: int = 8,
    markdown: bool = False,
) -> str:
    """Render the digest as a chat message (plain text or markdown)."""

    if digest.empty:
        header = "岗位情报 · 最近无变化"
        body = "监控的企业在统计窗口内没有新发布的岗位。"
        return f"{header}\n{body}" if not markdown else f"**{header}**\n\n{body}"

    lines: list[str] = []
    header = "岗位情报日报"
    summary = (
        f"新增 {digest.total_new} · 变更 {digest.total_updated} · 下架 {digest.total_removed}"
    )
    lines.append(header if not markdown else f"**{header}**")
    lines.append(summary)
    lines.append("")

    for company in digest.companies:
        badges: list[str] = []
        if company.new_count:
            badges.append(f"新增 {company.new_count}")
        if company.updated_count:
            badges.append(f"变更 {company.updated_count}")
        if company.removed_count:
            badges.append(f"下架 {company.removed_count}")
        company_line = f"{company.company_name}（{'，'.join(badges)}）"
        lines.append(company_line if not markdown else f"**{company_line}**")

        shown = {"new": 0, "updated": 0, "removed": 0}
        for item in company.items:
            if item.event_type in shown:
                if shown[item.event_type] >= max_items_per_type:
                    continue
                shown[item.event_type] += 1
            kind_label = {
                "new": "新增",
                "updated": "变更",
                "removed": "下架",
            }.get(item.event_type, item.event_type)
            city = f"·{item.city}" if item.city else ""
            track_label = "实习" if item.track == "internship" else ""
            line = f"- [{kind_label}] {item.title}{city}{track_label}"
            lines.append(line)
        for kind, count in (
            ("new", company.new_count),
            ("updated", company.updated_count),
            ("removed", company.removed_count),
        ):
            label = {"new": "新增", "updated": "变更", "removed": "下架"}[kind]
            if count > max_items_per_type:
                lines.append(f"- …{label}共 {count} 条，仅展示前 {max_items_per_type} 条")
        lines.append("")

    if digest.truncated:
        lines.append("（部分企业条目较多，已截断，详见本地工作台）")
    lines.append("详情见本地工作台「今日新发布」视图。")
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    """Clip to the byte limit (multi-byte safe) and append an ellipsis."""

    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    clipped = encoded[: max(0, limit - 24)]
    while clipped:
        try:
            partial = clipped.decode("utf-8")
            break
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    else:
        partial = ""
    return partial + "\n…（已截断）"


# ---------------------------------------------------------------------------
# Webhook payloads
# ---------------------------------------------------------------------------

def _dingtalk_signed_url(url: str, secret: str) -> str:
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    sign = base64.b64encode(
        hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    )
    separator = "&" if "?" in url else "?"
    return (
        f"{url}{separator}timestamp={timestamp}"
        f"&sign={urllib.parse.quote_plus(sign)}"
    )


def _feishu_signature(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_webhook_payload(
    target: WebhookTarget,
    message: str,
) -> tuple[str, bytes]:
    """Return the final URL and JSON body for one target."""

    if target.type == "wecom":
        body = {
            "msgtype": "markdown",
            "markdown": {"content": _clip(message, _WECOM_MARKDOWN_LIMIT)},
        }
        return target.url, json.dumps(body, ensure_ascii=False).encode("utf-8")
    if target.type == "dingtalk":
        url = target.url
        if target.secret:
            url = _dingtalk_signed_url(target.url, target.secret)
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": "岗位情报日报",
                "text": _clip(message, _DINGTALK_MARKDOWN_LIMIT),
            },
        }
        return url, json.dumps(body, ensure_ascii=False).encode("utf-8")
    if target.type == "feishu":
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": _clip(message, _FEISHU_TEXT_LIMIT)},
        }
        if target.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = _feishu_signature(timestamp, target.secret)
        return target.url, json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # generic: any JSON endpoint; the message rides in "message".
    body = {"message": message}
    return target.url, json.dumps(body, ensure_ascii=False).encode("utf-8")


def send_webhook_message(
    target: WebhookTarget,
    message: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> WebhookDelivery:
    """POST one formatted message; never raises."""

    try:
        url, body = build_webhook_payload(target, message)
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.status
            response.read(4096)
        return WebhookDelivery(
            target=target.label,
            ok=200 <= status_code < 300,
            status_code=status_code,
            payload_bytes=len(body),
        )
    except Exception as exc:  # noqa: BLE001 - delivery failures must not propagate
        return WebhookDelivery(
            target=target.label,
            ok=False,
            status_code=getattr(exc, "code", None),
            error=f"{type(exc).__name__}: {exc}",
        )


def push_daily_digest(
    digest: DigestResult,
    config: NotifyConfig,
) -> list[WebhookDelivery]:
    """Format once per target and push to every configured webhook."""

    if not config.webhooks:
        return []
    deliveries: list[WebhookDelivery] = []
    for target in config.webhooks:
        use_markdown = target.type in {"wecom", "dingtalk"}
        message = format_digest_text(
            digest,
            max_items_per_type=config.max_items_per_push,
            markdown=use_markdown,
        )
        deliveries.append(send_webhook_message(target, message))
    return deliveries
