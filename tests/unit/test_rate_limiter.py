"""限频器测试"""
import time
from unittest.mock import patch

from wehire_monitor.infra.rate_limiter import RateLimiter


def test_rate_limiter_sleeps_within_range():
    """限频器应在指定范围内 sleep"""
    limiter = RateLimiter(min_seconds=5, max_seconds=20)
    with patch("time.sleep") as mock_sleep, patch("random.uniform", return_value=10.0):
        limiter.wait()
        mock_sleep.assert_called_once_with(10.0)


def test_rate_limiter_search_interval():
    """搜索间隔 20-60s"""
    limiter = RateLimiter.search_limiter()
    assert limiter.min_seconds == 20
    assert limiter.max_seconds == 60


def test_rate_limiter_article_interval():
    """文章抓取间隔 5-20s"""
    limiter = RateLimiter.article_limiter()
    assert limiter.min_seconds == 5
    assert limiter.max_seconds == 20
