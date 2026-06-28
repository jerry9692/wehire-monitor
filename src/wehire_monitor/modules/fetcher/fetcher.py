# src/wehire_monitor/modules/fetcher/fetcher.py
"""微信公众号文章抓取器

参考 wechat_articles_spider 的 token/cookie 维护与限频思路,自研实现。
使用微信公众平台后台接口搜索公众号、获取文章列表。
"""
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from loguru import logger

from wehire_monitor.domain.models import ArticleMeta, CookieStatus
from wehire_monitor.infra.rate_limiter import RateLimiter
from wehire_monitor.modules.fetcher.exceptions import (
    AccountNotFoundError,
    CaptchaRequiredError,
    CookieInvalidError,
    RateLimitedError,
)

# 微信公众平台后台接口
_MP_SEARCH_URL = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
_MP_ARTICLE_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg"


class Fetcher:
    """微信公众号文章抓取器"""

    def __init__(self, cookie: str, token: str, user_agent: str):
        self.cookie = cookie
        self.token = token
        self.user_agent = user_agent
        self.search_limiter = RateLimiter.search_limiter()
        self.article_limiter = RateLimiter.article_limiter()
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Cookie": cookie,
                "Referer": "https://mp.weixin.qq.com/cgi-bin/appmsg",
            },
            timeout=30.0,
        )

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """发起请求并返回 JSON"""
        params["token"] = self.token
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        # 检测验证码
        if "needcaptcha" in str(data).lower() or data.get("need_captcha"):
            raise CaptchaRequiredError("检测到验证码要求")

        # 检测限流
        base_resp = data.get("base_resp", {})
        ret = base_resp.get("ret", 0)
        if ret in (-1, 200013):
            raise RateLimitedError(f"被限流, ret={ret}")

        return data

    def check_cookie(self) -> CookieStatus:
        """检测 Cookie 有效性"""
        import os
        from wehire_monitor.config.loader import ConfigLoader

        updated_str = os.environ.get("COOKIE_UPDATED_AT", "")
        try:
            updated = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            age = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        except ValueError:
            age = 999.0

        try:
            # 用一个轻量请求测试 Cookie
            data = self._request(_MP_SEARCH_URL, {"action": "search_biz", "query": "test", "begin": 0, "count": 1})
            is_valid = data.get("base_resp", {}).get("ret", 0) == 0
        except (CookieInvalidError, RateLimitedError, httpx.HTTPError):
            is_valid = False

        return CookieStatus(
            is_valid=is_valid,
            updated_at=updated_str,
            age_hours=age,
        )

    def search_account(self, name: str, alias: list[str]) -> dict[str, str]:
        """搜索公众号,返回 {fakeid, nickname}"""
        keywords = [name] + [a for a in alias if a]
        for kw in keywords:
            self.search_limiter.wait()
            data = self._request(
                _MP_SEARCH_URL,
                {"action": "search_biz", "query": kw, "begin": 0, "count": 5},
            )
            accounts = data.get("list", [])
            if accounts:
                logger.info(f"找到公众号: {accounts[0]['nickname']} (fakeid={accounts[0]['fakeid']})")
                return {"fakeid": accounts[0]["fakeid"], "nickname": accounts[0]["nickname"]}

        raise AccountNotFoundError(f"未找到公众号: {name} (alias={alias})")

    def list_articles(
        self, account: dict[str, str], window_hours: int = 36
    ) -> list[ArticleMeta]:
        """获取公众号近 window_hours 小时的文章"""
        self.article_limiter.wait()
        data = self._request(
            _MP_ARTICLE_URL,
            {
                "action": "list_ex",
                "fakeid": account["fakeid"],
                "begin": 0,
                "count": 10,
                "type": 9,
            },
        )

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=window_hours)
        articles: list[ArticleMeta] = []

        for item in data.get("app_msg_list", []):
            # update_time 格式 "2026-06-28 09:30:00"
            time_str = item.get("update_time") or item.get("create_time", "")
            try:
                pub_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning(f"无法解析时间: {time_str}, 跳过")
                continue

            if pub_time < cutoff:
                continue

            articles.append(
                ArticleMeta(
                    account_name=account["nickname"],
                    title=item["title"],
                    url=item["url"],
                    publish_time=pub_time,
                )
            )

        logger.info(f"公众号 {account['nickname']}: 获取 {len(articles)} 篇近 {window_hours}h 文章")
        return articles

    def close(self) -> None:
        self._client.close()
