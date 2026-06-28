"""飞书/钉钉 Webhook 推送

v0.1 仅推送标题 + 链接 + 来源。
"""
from dataclasses import dataclass, field
from typing import Literal

import httpx
from loguru import logger


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
    message: str = ""


class Notifier:
    """飞书/钉钉推送器"""

    def __init__(
        self,
        feishu_webhook: str | None = None,
        dingtalk_webhook: str | None = None,
        max_per_run: int = 20,
        push_when_empty: bool = False,
    ):
        self.feishu_webhook = feishu_webhook
        self.dingtalk_webhook = dingtalk_webhook
        self.max_per_run = max_per_run
        self.push_when_empty = push_when_empty
        self._client = httpx.Client(timeout=10.0)

    def build_markdown(self, report: DailyReport) -> str:
        """构建 Markdown 日报内容"""
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

        lines.extend([
            "| 标题 | 来源 |",
            "|---|---|",
        ])

        for item in report.items[: self.max_per_run]:
            lines.append(f"| [{item.title}]({item.url}) | {item.account_name} |")

        if len(report.items) > self.max_per_run:
            lines.append(f"\n> 还有 {len(report.items) - self.max_per_run} 条未展示")

        return "\n".join(lines)

    def send_daily(self, report: DailyReport) -> NotifyResult:
        """推送日报到飞书/钉钉"""
        if not report.items and not self.push_when_empty:
            logger.info("无命中且 push_when_empty=False,跳过推送")
            return NotifyResult(success=True, message="skipped (no items)")

        md_content = self.build_markdown(report)
        results: list[NotifyResult] = []

        if self.feishu_webhook:
            results.append(self._send_feishu(md_content))
        if self.dingtalk_webhook:
            results.append(self._send_dingtalk(md_content))

        if not results:
            logger.warning("未配置任何 Webhook")
            return NotifyResult(success=False, message="no webhook configured")

        all_success = all(r.success for r in results)
        return NotifyResult(
            success=all_success,
            message="; ".join(r.message for r in results),
        )

    def _send_feishu(self, md_content: str) -> NotifyResult:
        """推送飞书"""
        try:
            payload = {
                "msg_type": "interactive",
                "card": {
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
            if data.get("code", 0) == 0:
                logger.info("飞书推送成功")
                return NotifyResult(success=True, message="feishu ok")
            return NotifyResult(success=False, message=f"feishu error: {data}")
        except Exception as e:
            logger.error(f"飞书推送失败: {e}")
            return NotifyResult(success=False, message=f"feishu error: {e}")

    def _send_dingtalk(self, md_content: str) -> NotifyResult:
        """推送钉钉"""
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "招聘日报",
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
        except Exception as e:
            logger.error(f"钉钉推送失败: {e}")
            return NotifyResult(success=False, message=f"dingtalk error: {e}")

    def close(self) -> None:
        self._client.close()
