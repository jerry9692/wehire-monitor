"""飞书/钉钉 Webhook 推送

v0.1 仅推送标题 + 链接 + 来源。
使用列表格式(飞书/钉钉 markdown 均支持),不使用表格。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Shanghai")
except ImportError:
    _TZ = None  # type: ignore[assignment]


@dataclass
class ReportItem:
    """日报条目(v0.1: 标题+链接+来源)"""
    title: str
    url: str
    account_name: str


@dataclass
class DailyReport:
    """日报"""
    date: str
    items: list[ReportItem]
    total_fetched: int = 0
    total_candidates: int = 0


@dataclass
class NotifyResult:
    """推送结果"""
    success: bool
    pushed_count: int = 0
    message: str = ""


class Notifier:
    """飞书/钉钉推送器(支持上下文管理器)"""

    def __init__(
        self,
        feishu_webhook: str | None = None,
        dingtalk_webhook: str | None = None,
        max_per_run: int = 20,
        push_when_empty: bool = False,
        email_mask: bool = True,
    ):
        self.feishu_webhook = feishu_webhook
        self.dingtalk_webhook = dingtalk_webhook
        self.max_per_run = max_per_run
        self.push_when_empty = push_when_empty
        self.email_mask = email_mask
        self._client = httpx.Client(timeout=10.0)

    def __enter__(self) -> "Notifier":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def _today_str() -> str:
        """获取上海时区的今天日期字符串"""
        if _TZ:
            return datetime.now(_TZ).strftime("%Y-%m-%d")
        return datetime.now().strftime("%Y-%m-%d")

    def _build_item_line(self, item: ReportItem) -> str:
        """构建单条条目(列表格式,兼容飞书/钉钉 markdown)"""
        return f"- [{item.title}]({item.url}) — {item.account_name}"

    def build_markdown(self, report: DailyReport, platform: Literal["feishu", "dingtalk"] = "dingtalk") -> str:
        """构建 Markdown 日报内容(列表格式,不使用表格)"""
        lines = [
            f"## 今日精准招聘日报｜{report.date}",
            "",
            f"抓取 {report.total_fetched} 篇，候选 {report.total_candidates} 篇。",
            "",
        ]

        if not report.items:
            if self.push_when_empty:
                lines.append("今日无新增招聘信息。")
            else:
                lines.append("今日无新增命中岗位。")
            return "\n".join(lines)

        shown = report.items[: self.max_per_run]
        for item in shown:
            lines.append(self._build_item_line(item))

        if len(report.items) > self.max_per_run:
            lines.append(f"\n> 还有 {len(report.items) - self.max_per_run} 条未展示，将在下次推送")

        return "\n".join(lines)

    def send_daily(self, report: DailyReport) -> NotifyResult:
        """推送日报到飞书/钉钉,返回实际推送条数"""
        if not report.items and not self.push_when_empty:
            logger.info("无命中且 push_when_empty=False,跳过推送")
            return NotifyResult(success=True, pushed_count=0, message="skipped (no items)")

        # 实际可推送的条目数(受 max_per_run 限制)
        shown_count = min(len(report.items), self.max_per_run) if report.items else 0
        results: list[NotifyResult] = []

        if self.feishu_webhook:
            md_feishu = self.build_markdown(report, platform="feishu")
            results.append(self._send_feishu(md_feishu))
        if self.dingtalk_webhook:
            md_dingtalk = self.build_markdown(report, platform="dingtalk")
            results.append(self._send_dingtalk(md_dingtalk))

        if not results:
            logger.warning("未配置任何 Webhook")
            return NotifyResult(success=False, pushed_count=0, message="no webhook configured")

        all_success = all(r.success for r in results)
        return NotifyResult(
            success=all_success,
            pushed_count=shown_count if all_success else 0,
            message="; ".join(r.message for r in results),
        )

    def send_alert(self, title: str, message: str) -> NotifyResult:
        """发送告警通知(公开方法)"""
        alert_md = f"⚠️ **{title}**\n\n{message}"
        results: list[NotifyResult] = []
        if self.feishu_webhook:
            results.append(self._send_feishu(alert_md))
        if self.dingtalk_webhook:
            results.append(self._send_dingtalk(alert_md))
        if not results:
            return NotifyResult(success=False, pushed_count=0, message="no webhook configured")
        return NotifyResult(
            success=all(r.success for r in results),
            pushed_count=0,
            message="; ".join(r.message for r in results),
        )

    def _send_feishu(self, md_content: str) -> NotifyResult:
        """推送飞书(interactive card 带 header)"""
        try:
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": "招聘监控日报"},
                        "template": "blue",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": md_content,
                        }
                    ],
                },
            }
            resp = self._client.post(self.feishu_webhook, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code", 0) == 0 or data.get("StatusCode", 0) == 0:
                logger.info("飞书推送成功")
                return NotifyResult(success=True, message="feishu ok")
            return NotifyResult(success=False, message=f"feishu error: {data}")
        except httpx.HTTPError as e:
            logger.error(f"飞书推送 HTTP 失败: {e}")
            return NotifyResult(success=False, message=f"feishu http error: {e}")
        except (ValueError, KeyError) as e:
            logger.error(f"飞书推送数据错误: {e}")
            return NotifyResult(success=False, message=f"feishu data error: {e}")

    def _send_dingtalk(self, md_content: str) -> NotifyResult:
        """推送钉钉(markdown 消息类型)"""
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "招聘监控日报",
                    "text": md_content,
                },
            }
            resp = self._client.post(self.dingtalk_webhook, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode", 0) == 0:
                logger.info("钉钉推送成功")
                return NotifyResult(success=True, message="dingtalk ok")
            return NotifyResult(success=False, message=f"dingtalk error: {data}")
        except httpx.HTTPError as e:
            logger.error(f"钉钉推送 HTTP 失败: {e}")
            return NotifyResult(success=False, message=f"dingtalk http error: {e}")
        except (ValueError, KeyError) as e:
            logger.error(f"钉钉推送数据错误: {e}")
            return NotifyResult(success=False, message=f"dingtalk data error: {e}")

    def close(self) -> None:
        self._client.close()
