"""v0.3 CLI 测试: review 命令 + run --stats

使用 typer.testing.CliRunner + mock PipelineRunner。
main.py 中通过 `with PipelineRunner(...) as runner:` 使用上下文管理器,
因此 mock 时需要设置 `__enter__` 返回值。
"""
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from wehire_monitor.main import app
from wehire_monitor.domain.status import Status


cli = CliRunner()


def _make_mock_runner(articles=None, runs=None):
    """构造 mock PipelineRunner 实例及其 repo。

    返回 mock_runner,调用方需将其赋给
    `mock_cls.return_value.__enter__.return_value`。
    """
    mock_runner = MagicMock()
    mock_runner.repo.query_by_status.return_value = articles or []
    mock_runner.repo.get_recent_runs.return_value = runs or []
    return mock_runner


# ---------------------------------------------------------------------------
# review --list
# ---------------------------------------------------------------------------

@patch("wehire_monitor.pipeline.runner.PipelineRunner")
def test_review_list_empty(mock_cls):
    """无 need_review 文章时提示为空"""
    mock_runner = _make_mock_runner(articles=[])
    mock_cls.return_value.__enter__.return_value = mock_runner

    result = cli.invoke(app, ["review", "--list"])

    assert result.exit_code == 0
    assert "暂无待复核文章" in result.output
    mock_runner.repo.query_by_status.assert_called_once_with(Status.NEED_REVIEW)


@patch("wehire_monitor.pipeline.runner.PipelineRunner")
def test_review_list_with_items(mock_cls):
    """有文章时展示列表(id/title/account_name/url)"""
    articles = [
        {
            "id": "abc123def456",
            "title": "招聘公告A",
            "account_name": "号A",
            "url": "https://mp.weixin.qq.com/s/a",
        },
        {
            "id": "xyz789ghi012",
            "title": "招聘公告B",
            "account_name": "号B",
            "url": "https://mp.weixin.qq.com/s/b",
        },
    ]
    mock_runner = _make_mock_runner(articles=articles)
    mock_cls.return_value.__enter__.return_value = mock_runner

    result = cli.invoke(app, ["review", "--list"])

    assert result.exit_code == 0
    assert "待复核文章" in result.output
    assert "招聘公告A" in result.output
    assert "招聘公告B" in result.output
    assert "号A" in result.output
    assert "号B" in result.output
    mock_runner.repo.query_by_status.assert_called_once_with(Status.NEED_REVIEW)


# ---------------------------------------------------------------------------
# review --approve / --reject
# ---------------------------------------------------------------------------

@patch("wehire_monitor.pipeline.runner.PipelineRunner")
def test_review_approve(mock_cls):
    """复核通过: force_status(id, CANDIDATE) 以便重新提取"""
    mock_runner = _make_mock_runner()
    mock_cls.return_value.__enter__.return_value = mock_runner

    result = cli.invoke(app, ["review", "--approve", "abc123def456"])

    assert result.exit_code == 0
    mock_runner.repo.force_status.assert_called_once_with("abc123def456", Status.CANDIDATE)
    assert "candidate" in result.output or "通过" in result.output or "CANDIDATE" in result.output


@patch("wehire_monitor.pipeline.runner.PipelineRunner")
def test_review_reject(mock_cls):
    """复核拒绝: force_status(id, ARCHIVED)"""
    mock_runner = _make_mock_runner()
    mock_cls.return_value.__enter__.return_value = mock_runner

    result = cli.invoke(app, ["review", "--reject", "abc123def456"])

    assert result.exit_code == 0
    mock_runner.repo.force_status.assert_called_once_with("abc123def456", Status.ARCHIVED)
    assert "归档" in result.output or "拒绝" in result.output


# ---------------------------------------------------------------------------
# run --stats
# ---------------------------------------------------------------------------

@patch("wehire_monitor.pipeline.runner.PipelineRunner")
def test_run_stats(mock_cls):
    """run --stats 展示最近运行统计表格"""
    runs = [
        {
            "run_id": "run-aaaa1111",
            "started_at": "2026-06-28T08:30:00+00:00",
            "ended_at": "2026-06-28T08:45:00+00:00",
            "fetched_count": 30,
            "candidate_count": 5,
            "ocr_count": 2,
            "llm_count": 3,
            "vlm_count": 1,
            "cost_estimate": 0.15,
            "error_summary": None,
        },
        {
            "run_id": "run-bbbb2222",
            "started_at": "2026-06-27T08:30:00+00:00",
            "ended_at": "2026-06-27T08:50:00+00:00",
            "fetched_count": 20,
            "candidate_count": 3,
            "ocr_count": 1,
            "llm_count": 2,
            "vlm_count": 0,
            "cost_estimate": 0.08,
            "error_summary": "Cookie expired",
        },
    ]
    mock_runner = _make_mock_runner(runs=runs)
    mock_cls.return_value.__enter__.return_value = mock_runner

    result = cli.invoke(app, ["run", "--stats"])

    assert result.exit_code == 0
    mock_runner.repo.get_recent_runs.assert_called_once_with(limit=10)
    # 两条记录的 run_id 均应展示
    assert "run-aaaa1111" in result.output
    assert "run-bbbb2222" in result.output
    # 表头关键字段
    assert "fetched" in result.output
    assert "cand" in result.output
    assert "llm" in result.output
    assert "ocr" in result.output
    assert "vlm" in result.output
    assert "cost" in result.output
    # error 列内容
    assert "Cookie expired" in result.output
