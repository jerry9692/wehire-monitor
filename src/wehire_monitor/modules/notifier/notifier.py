"""飞书/钉钉 Webhook 推送

v0.1 仅推送标题 + 链接 + 来源。
使用列表格式(飞书/钉钉 markdown 均支持),不使用表格。
"""
import re
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
        """构建 Markdown 日报内容(列表格式,不使用表格)

        飞书 Markdown 组件不支持 ## 标题和 > 引用,用加粗文本替代。
        """
        # 飞书用加粗替代 ## 标题
        title_prefix = "**" if platform == "feishu" else "## "
        title_suffix = "**" if platform == "feishu" else ""
        lines = [
            f"{title_prefix}今日精准招聘日报｜{report.date}{title_suffix}",
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
            # 飞书不支持 > 引用,用普通文本
            extra = f"\n还有 {len(report.items) - self.max_per_run} 条未展示，将在下次推送"
            if platform == "feishu":
                extra = f"\n{extra.strip()}"
            lines.append(extra)

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

        # 任一渠道成功即视为推送成功(避免部分失败导致重复推送)
        any_success = any(r.success for r in results)
        all_success = all(r.success for r in results)
        return NotifyResult(
            success=any_success,
            pushed_count=shown_count if any_success else 0,
            message="; ".join(r.message for r in results) + ("" if all_success else " (部分渠道失败)"),
        )

    def send_alert(self, title: str, message: str) -> NotifyResult:
        """发送告警通知(公开方法)"""
        alert_md = f"⚠️ **{title}**\n\n{message}"
        results: list[NotifyResult] = []
        if self.feishu_webhook:
            results.append(self._send_feishu(alert_md, card_title=title))
        if self.dingtalk_webhook:
            results.append(self._send_dingtalk(alert_md, msg_title=title))
        if not results:
            return NotifyResult(success=False, pushed_count=0, message="no webhook configured")
        any_success = any(r.success for r in results)
        return NotifyResult(
            success=any_success,
            pushed_count=0,
            message="; ".join(r.message for r in results),
        )

    def _send_feishu(self, md_content: str, card_title: str = "招聘监控日报") -> NotifyResult:
        """推送飞书(interactive card 带 header)"""
        try:
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": card_title},
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
            # 飞书成功时 code=0 或 StatusCode=0;要求字段必须存在且为 0
            code = data.get("code")
            status_code = data.get("StatusCode")
            if (code is not None and code == 0) or (status_code is not None and status_code == 0):
                logger.info("飞书推送成功")
                return NotifyResult(success=True, message="feishu ok")
            return NotifyResult(success=False, message=f"feishu error: {data}")
        except httpx.HTTPError as e:
            logger.error(f"飞书推送 HTTP 失败: {e}")
            return NotifyResult(success=False, message=f"feishu http error: {e}")
        except (ValueError, KeyError) as e:
            logger.error(f"飞书推送数据错误: {e}")
            return NotifyResult(success=False, message=f"feishu data error: {e}")

    def _send_dingtalk(self, md_content: str, msg_title: str = "招聘监控日报") -> NotifyResult:
        """推送钉钉(markdown 消息类型)"""
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": msg_title,
                    "text": md_content,
                },
            }
            resp = self._client.post(self.dingtalk_webhook, json=payload)
            resp.raise_for_status()
            data = resp.json()
            # 钉钉成功时 errcode=0;要求字段必须存在且为 0
            errcode = data.get("errcode")
            if errcode is not None and errcode == 0:
                logger.info("钉钉推送成功")
                return NotifyResult(success=True, message="dingtalk ok")
            return NotifyResult(success=False, message=f"dingtalk error: {data}")
        except httpx.HTTPError as e:
            logger.error(f"钉钉推送 HTTP 失败: {e}")
            return NotifyResult(success=False, message=f"dingtalk http error: {e}")
        except (ValueError, KeyError) as e:
            logger.error(f"钉钉推送数据错误: {e}")
            return NotifyResult(success=False, message=f"dingtalk data error: {e}")

    # ========== v0.2: 结构化 Markdown 表格日报 ==========

    def _mask_email(self, email: str | None) -> str:
        """邮箱脱敏

        - email_mask=True 时: hr@example.com → hr***@example.com
        - email_mask=False 时: 原样返回
        - None → "-"
        """
        if not email:
            return "-"
        if not self.email_mask:
            return email
        # hr@example.com → hr***@example.com
        match = re.match(r"^([a-zA-Z0-9._%+-]{1,3})", email)
        prefix = match.group(1) if match else "?"
        domain = email.split("@")[-1] if "@" in email else ""
        return f"{prefix}***@{domain}" if domain else f"{prefix}***"

    def build_structured_markdown(
        self,
        date: str,
        matched_jobs: list,
        review_jobs: list,
        total_fetched: int,
        total_candidates: int,
        platform: str = "dingtalk",
    ) -> str:
        """构建 v0.2 结构化 Markdown 日报(表格+复核区)

        飞书不支持 ## 标题和 > 引用,用 **加粗** 替代。
        空列表时显示"今日无新增命中岗位"。
        """
        # 飞书用加粗替代 ## 标题
        title_prefix = "**" if platform == "feishu" else "## "
        title_suffix = "**" if platform == "feishu" else ""
        lines = [
            f"{title_prefix}今日精准招聘日报｜{date}{title_suffix}",
            "",
            f"抓取 {total_fetched} 篇，候选 {total_candidates} 篇，命中 {len(matched_jobs)} 个岗位。",
            "",
        ]

        if not matched_jobs and not review_jobs:
            lines.append("今日无新增命中岗位。")
            return "\n".join(lines)

        # 主表: 命中岗位(截断到 max_per_run)
        if matched_jobs:
            shown = matched_jobs[: self.max_per_run]
            lines.append(f"{title_prefix}命中岗位{title_suffix}")
            lines.append("")
            lines.append("| 公司 | 岗位 | 地点 | 截止日期 | 投递方式 | 来源 |")
            lines.append("|---|---|---|---|---|---|")
            for m in shown:
                job = m.job
                email_display = self._mask_email(job.email)
                # 投递方式: 优先 apply_channel,但若其为邮箱则同样脱敏,避免泄漏
                if job.apply_channel and "@" in job.apply_channel:
                    apply = self._mask_email(job.apply_channel)
                elif job.apply_channel:
                    apply = job.apply_channel
                elif job.email:
                    apply = email_display
                else:
                    apply = "-"
                source = getattr(m, "account_name", "-")
                lines.append(
                    f"| {job.company_name or '-'} | {job.job_name or '-'} | "
                    f"{job.location or '-'} | {job.deadline.date or '-'} | "
                    f"{apply} | {source} |"
                )
            if len(matched_jobs) > self.max_per_run:
                lines.append(f"\n还有 {len(matched_jobs) - self.max_per_run} 条未展示")
            lines.append("")

        # 复核区: 低置信度岗位
        if review_jobs:
            lines.append(f"{title_prefix}需人工复核{title_suffix}")
            lines.append("")
            lines.append("| 公司 | 岗位 | 地点 | 置信度 | 原因 |")
            lines.append("|---|---|---|---|---|")
            for m in review_jobs:
                job = m.job
                warnings = job.source_evidence.get("_warnings", [])
                reason = ", ".join(warnings) if warnings else "低置信度"
                lines.append(
                    f"| {job.company_name or '-'} | {job.job_name or '-'} | "
                    f"{job.location or '-'} | {job.confidence} | {reason} |"
                )

        return "\n".join(lines)

    def close(self) -> None:
        self._client.close()
