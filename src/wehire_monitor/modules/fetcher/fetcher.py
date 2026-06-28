"""微信公众号文章抓取器

参考 wechat_articles_spider 的 token/cookie 维护与限频思路,自研实现。
使用微信公众平台后台接口搜索公众号、获取文章列表。
"""
import hashlib
import json as _json
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
    FetcherError,
)

# 微信公众平台后台接口
_MP_SEARCH_URL = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
_MP_ARTICLE_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg"

# 限流/登录 ret 值
_RET_RATE_LIMITED = {-1, 200002, 200013}
_RET_COOKIE_INVALID = {200003, 200040}


class Fetcher:
    """微信公众号文章抓取器(支持上下文管理器)"""

    def __init__(self, cookie: str, token: str, user_agent: str):
        if not cookie:
            raise CookieInvalidError("WECHAT_MP_COOKIE 不能为空")
        if not token:
            raise CookieInvalidError("WECHAT_MP_TOKEN 不能为空")
        self.cookie = cookie
        self.token = token
        self.user_agent = user_agent
        self.search_limiter = RateLimiter.search_limiter()
        self.article_limiter = RateLimiter.article_limiter()
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Cookie": cookie,
            },
            timeout=30.0,
            follow_redirects=True,
        )

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """发起请求并返回 JSON,检测验证码/Cookie失效/限流"""
        params["token"] = self.token
        params["lang"] = "zh_CN"
        params["f"] = "json"
        params["ajax"] = "1"

        # 根据 URL 设置 Referer
        referer = "https://mp.weixin.qq.com/"
        if "appmsg" in url:
            referer = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        elif "searchbiz" in url:
            referer = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
        resp = self._client.get(url, params=params, headers={"Referer": referer})

        # 检测 HTTP 限流(403/429)→ RateLimitedError,不要抛 HTTPStatusError
        if resp.status_code in (403, 429):
            raise RateLimitedError(f"HTTP {resp.status_code} 被限流")
        resp.raise_for_status()

        # 检测空响应 → 限流(不是 Cookie 失效)
        if not resp.text.strip():
            raise RateLimitedError("空响应,可能被限流")

        # 检测是否返回 HTML 登录页(Cookie 失效的标志)
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type or not resp.text.strip().startswith(("{", "[")):
            raise CookieInvalidError("Cookie 已失效(返回 HTML 登录页)")

        try:
            data = resp.json()
        except _json.JSONDecodeError as e:
            raise CookieInvalidError(f"响应非 JSON,可能 Cookie 失效: {e}") from e

        # 检测验证码
        if data.get("need_captcha") or "needcaptcha" in str(data).lower():
            raise CaptchaRequiredError("检测到验证码要求")

        # 检测 base_resp.ret
        base_resp = data.get("base_resp", {})
        ret = base_resp.get("ret", 0)
        if ret in _RET_COOKIE_INVALID:
            raise CookieInvalidError(f"Cookie 失效, ret={ret}")
        if ret in _RET_RATE_LIMITED:
            raise RateLimitedError(f"被限流, ret={ret}")
        if ret != 0:
            raise FetcherError(f"接口返回错误, ret={ret}, err_msg={base_resp.get('err_msg', '')}")

        return data

    def check_cookie(self) -> CookieStatus:
        """检测 Cookie 有效性(API 级验证)"""
        from datetime import datetime, timezone as _tz
        nickname = ""
        message = ""
        try:
            data = self._request(
                _MP_SEARCH_URL,
                {"action": "search_biz", "query": "test", "begin": 0, "count": 1},
            )
            is_valid = data.get("base_resp", {}).get("ret", 0) == 0
            if is_valid:
                # 尝试提取账号信息
                accounts = data.get("list", [])
                if accounts:
                    nickname = accounts[0].get("nickname", "")
            else:
                message = f"接口返回 ret={data.get('base_resp', {}).get('ret')}"
        except (CookieInvalidError, CaptchaRequiredError, RateLimitedError, httpx.HTTPError, FetcherError) as e:
            logger.warning(f"Cookie 检测失败: {e}")
            is_valid = False
            message = str(e)

        # 从环境变量获取更新时间
        import os
        updated_str = os.environ.get("COOKIE_UPDATED_AT", "")
        age = 0.0
        if updated_str:
            try:
                if "T" in updated_str or "+" in updated_str:
                    updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                else:
                    from zoneinfo import ZoneInfo
                    naive = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S")
                    updated = naive.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                age = (datetime.now(_tz.utc) - updated.astimezone(_tz.utc)).total_seconds() / 3600
            except ValueError:
                age = 999.0

        return CookieStatus(
            is_valid=is_valid,
            updated_at=updated_str,
            age_hours=age,
            nickname=nickname,
            message=message,
        )

    def search_account(self, name: str, alias: list[str], max_articles: int = 10) -> dict[str, str]:
        """搜索公众号,优先精确匹配 name,回退 alias,全部无精确匹配时取首个结果

        流程:
        1. 遍历 [name] + alias,对每个关键词搜索并尝试精确匹配
        2. 精确匹配成功立即返回
        3. 全部关键词都无精确匹配时,取首个非空搜索结果的首条记录
        """
        keywords = [name] + [a for a in alias if a]
        first_result: dict[str, str] | None = None  # 记录首个非空搜索结果

        for kw in keywords:
            self.search_limiter.wait()
            data = self._request(
                _MP_SEARCH_URL,
                {"action": "search_biz", "query": kw, "begin": 0, "count": 10},
            )
            accounts = data.get("list", [])
            if not accounts:
                continue

            # 优先精确匹配昵称
            for acc in accounts:
                if acc.get("nickname", "") == kw:
                    logger.info(f"精确匹配公众号: {acc.get('nickname')} (fakeid={acc.get('fakeid')})")
                    return {
                        "fakeid": acc.get("fakeid", ""),
                        "nickname": acc.get("nickname", ""),
                    }

            # 记录首个非空搜索结果(用于最终回退)
            if first_result is None:
                first_result = {
                    "fakeid": accounts[0].get("fakeid", ""),
                    "nickname": accounts[0].get("nickname", ""),
                }

        # 所有关键词都无精确匹配,回退到首个搜索结果
        if first_result is not None:
            logger.warning(f"未精确匹配 '{name}',使用首个搜索结果: {first_result['nickname']}")
            return first_result

        raise AccountNotFoundError(f"未找到公众号: {name} (alias={alias})")

    def list_articles(
        self, account: dict[str, str], window_hours: int = 36, max_count: int = 10
    ) -> list[ArticleMeta]:
        """获取公众号近 window_hours 小时的文章"""
        self.article_limiter.wait()
        data = self._request(
            _MP_ARTICLE_URL,
            {
                "action": "list_ex",
                "fakeid": account["fakeid"],
                "begin": 0,
                "count": max_count,
                "type": 9,
                "query": "",
            },
        )

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=window_hours)
        articles: list[ArticleMeta] = []

        for item in data.get("app_msg_list", []):
            # update_time/create_time 是 Unix 时间戳(整数秒)
            ts = item.get("update_time")
            if ts is None:
                ts = item.get("create_time", 0)
            if not isinstance(ts, (int, float)):
                try:
                    ts = int(ts)
                except (ValueError, TypeError):
                    logger.warning(f"无法解析时间戳: {ts}, 跳过")
                    continue
            try:
                pub_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except (ValueError, OSError) as e:
                logger.warning(f"时间戳转换失败: {ts} — {e}")
                continue

            if pub_time < cutoff:
                continue

            articles.append(
                ArticleMeta(
                    account_name=account["nickname"],
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    publish_time=pub_time,
                )
            )

        logger.info(f"公众号 {account['nickname']}: 获取 {len(articles)} 篇近 {window_hours}h 文章")
        return articles

    def close(self) -> None:
        self._client.close()
