"""Fetcher 测试"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from wehire_monitor.modules.fetcher.fetcher import Fetcher
from wehire_monitor.modules.fetcher.exceptions import (
    CookieInvalidError,
    CaptchaRequiredError,
    AccountNotFoundError,
)
from wehire_monitor.domain.models import CookieStatus


@pytest.fixture(autouse=True)
def _mock_rate_limiters():
    """全局 mock 限速器,避免测试中真实 sleep"""
    with patch("wehire_monitor.modules.fetcher.fetcher.RateLimiter") as mock_rl_cls:
        mock_limiter = MagicMock()
        mock_limiter.wait = MagicMock()
        mock_rl_cls.search_limiter.return_value = mock_limiter
        mock_rl_cls.article_limiter.return_value = mock_limiter
        yield


def test_check_cookie_valid():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {"base_resp": {"ret": 0}}
        status = fetcher.check_cookie()
        assert status.is_valid is True
    fetcher.close()


def test_check_cookie_invalid():
    fetcher = Fetcher(cookie="bad", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {"base_resp": {"ret": -1}}
        status = fetcher.check_cookie()
        assert status.is_valid is False
    fetcher.close()


def test_search_account_found_by_name():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {
            "list": [{"fakeid": "fake123", "nickname": "上海国资招聘"}]
        }
        meta = fetcher.search_account("上海国资招聘", [])
        assert meta["fakeid"] == "fake123"
    fetcher.close()


def test_search_account_fallback_to_alias():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    call_count = [0]

    def mock_response(url, params):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"list": []}  # name 搜索无结果
        return {"list": [{"fakeid": "fake456", "nickname": "国资招聘"}]}

    with patch.object(fetcher, "_request", side_effect=mock_response):
        meta = fetcher.search_account("上海国资招聘", ["国资招聘"])
        assert meta["fakeid"] == "fake456"
    fetcher.close()


def test_search_account_not_found():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {"list": []}
        with pytest.raises(AccountNotFoundError):
            fetcher.search_account("不存在", ["也不存在"])
    fetcher.close()


def test_list_articles_filters_by_time_window():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    now = datetime.now(timezone.utc)
    old_ts = int((now - timedelta(hours=48)).timestamp())
    new_ts = int((now - timedelta(hours=12)).timestamp())

    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {
            "app_msg_list": [
                {"title": "旧文章", "link": "http://old", "update_time": old_ts, "create_time": old_ts},
                {"title": "新文章", "link": "http://new", "update_time": new_ts, "create_time": new_ts},
            ]
        }
        articles = fetcher.list_articles(
            account={"fakeid": "fake123", "nickname": "测试号"},
            window_hours=36,
        )
        assert len(articles) == 1
        assert articles[0].title == "新文章"
    fetcher.close()
