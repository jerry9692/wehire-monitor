# tests/unit/test_fetcher.py
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


def test_check_cookie_valid():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {"base_resp": {"ret": 0}}
        status = fetcher.check_cookie()
        assert status.is_valid is True


def test_check_cookie_invalid():
    fetcher = Fetcher(cookie="bad", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {"base_resp": {"ret": -1}}
        status = fetcher.check_cookie()
        assert status.is_valid is False


def test_search_account_found_by_name():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {
            "list": [{"fakeid": "fake123", "nickname": "上海国资招聘"}]
        }
        meta = fetcher.search_account("上海国资招聘", [])
        assert meta["fakeid"] == "fake123"


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


def test_search_account_not_found():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {"list": []}
        with pytest.raises(AccountNotFoundError):
            fetcher.search_account("不存在", ["也不存在"])


def test_list_articles_filters_by_time_window():
    fetcher = Fetcher(cookie="abc", token="tok", user_agent="UA")
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    new_time = (now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")

    with patch.object(fetcher, "_request") as mock_req:
        mock_req.return_value = {
            "app_msg_list": [
                {"title": "旧文章", "url": "http://old", "update_time": old_time, "create_time": old_time},
                {"title": "新文章", "url": "http://new", "update_time": new_time, "create_time": new_time},
            ]
        }
        articles = fetcher.list_articles(
            account={"fakeid": "fake123", "nickname": "测试号"},
            window_hours=36,
        )
        assert len(articles) == 1
        assert articles[0].title == "新文章"
