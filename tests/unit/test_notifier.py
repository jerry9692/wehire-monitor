"""Notifier 测试"""
import json
from unittest.mock import patch, MagicMock

from wehire_monitor.modules.notifier.notifier import Notifier, DailyReport, ReportItem


def test_build_markdown_content():
    """日报应生成 Markdown 表格"""
    notifier = Notifier(feishu_webhook="https://example.com/hook", dingtalk_webhook=None)
    report = DailyReport(
        date="2026-06-28",
        items=[
            ReportItem(
                title="某某集团社会招聘公告",
                url="https://mp.weixin.qq.com/s/xxx",
                account_name="上海国资招聘",
            ),
        ],
        total_fetched=15,
        total_candidates=3,
    )
    md = notifier.build_markdown(report)
    assert "今日精准招聘日报" in md
    assert "2026-06-28" in md
    assert "某某集团社会招聘公告" in md
    assert "https://mp.weixin.qq.com/s/xxx" in md


def test_build_empty_report():
    """无命中时应生成无新增提示"""
    notifier = Notifier(feishu_webhook="https://example.com/hook", dingtalk_webhook=None)
    report = DailyReport(date="2026-06-28", items=[], total_fetched=10, total_candidates=0)
    md = notifier.build_markdown(report)
    assert "无新增" in md or "0" in md


def test_send_to_feishu():
    """应调用飞书 Webhook"""
    notifier = Notifier(feishu_webhook="https://open.feishu.cn/hook/xxx", dingtalk_webhook=None, push_when_empty=True)
    report = DailyReport(date="2026-06-28", items=[], total_fetched=0, total_candidates=0)

    with patch.object(notifier, "_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_client.post.return_value = mock_resp

        result = notifier.send_daily(report)
        assert result.success is True
        mock_client.post.assert_called_once()


def test_max_per_run_limit():
    """推送数量不超过 max_per_run"""
    notifier = Notifier(feishu_webhook="https://example.com/hook", dingtalk_webhook=None, max_per_run=2)
    items = [
        ReportItem(title=f"标题{i}", url=f"https://example.com/{i}", account_name="号")
        for i in range(5)
    ]
    report = DailyReport(date="2026-06-28", items=items, total_fetched=5, total_candidates=5)
    md = notifier.build_markdown(report)
    # 只应包含前 2 条
    assert "标题0" in md
    assert "标题1" in md
    assert "标题2" not in md
