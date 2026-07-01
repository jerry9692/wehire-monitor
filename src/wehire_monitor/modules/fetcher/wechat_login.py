"""微信公众号扫码登录模块

借鉴 CSDN 爬虫实战三方案，纯 httpx 实现，无浏览器依赖。
调用微信公众平台 scanloginqrcode 接口完成扫码登录。

参考: https://blog.csdn.net/qq_44780372/article/details/143250640
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse, parse_qs

import httpx
from loguru import logger
from PIL import Image

from wehire_monitor.domain.models import LoginResult

# 微信公众平台接口
_MP_BASE = "https://mp.weixin.qq.com"
_MP_STARTLOGIN = f"{_MP_BASE}/cgi-bin/bizlogin?action=startlogin"
_MP_QRCODE = f"{_MP_BASE}/cgi-bin/scanloginqrcode?action=getqrcode"
_MP_ASK = f"{_MP_BASE}/cgi-bin/scanloginqrcode?action=ask&token=&lang=zh_CN&f=json&ajax=1"
_MP_LOGIN = f"{_MP_BASE}/cgi-bin/bizlogin?action=login"

# 默认 User-Agent
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

# 轮询参数
_POLL_INTERVAL = 2  # 秒
_POLL_TIMEOUT = 120  # 秒


class _ShowImageThread(Thread):
    """在独立线程中显示二维码图片(避免阻塞主线程)"""

    def __init__(self, data: bytes):
        super().__init__(daemon=True)
        self._data = data

    def run(self) -> None:
        try:
            img = Image.open(BytesIO(self._data))
            img.show()
        except Exception as e:
            logger.warning(f"显示二维码失败: {e}")


class WeChatLogin:
    """微信公众号扫码登录

    用法:
        login = WeChatLogin()
        if not login.is_cookie_valid():
            result = login.login()
            if result.success:
                login.save_to_env()
    """

    def __init__(
        self,
        cookie_file: str = "data/wechat_cookie.json",
        user_agent: str = _DEFAULT_UA,
    ) -> None:
        self._cookie_file = Path(cookie_file)
        self._cookie: str = ""
        self._token: str = ""
        self._ua = user_agent
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Referer": _MP_BASE + "/",
                "Host": "mp.weixin.qq.com",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        # 尝试从文件加载已有 Cookie
        self._load_from_file()

    def close(self) -> None:
        self._client.close()

    def _request(self, url: str, method: str = "GET", data: str | None = None) -> dict:
        """发起请求并返回 JSON"""
        if method == "GET":
            resp = self._client.get(url)
        else:
            resp = self._client.post(url, content=data)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            # 非 JSON 响应(可能是图片或 HTML)
            return {}

    def is_cookie_valid(self) -> bool:
        """检测已有 Cookie 是否有效

        调用 scanloginqrcode?action=ask 检查 base_resp.ret
        ret=0 表示 Cookie 仍有效
        """
        if not self._cookie or not self._token:
            return False
        try:
            data = self._request(_MP_ASK)
            ret = data.get("base_resp", {}).get("ret", -1)
            if ret == 0:
                logger.info("已有 Cookie 有效，无需扫码")
                return True
            else:
                logger.info(f"Cookie 已失效 (ret={ret})，需要重新登录")
                return False
        except Exception as e:
            logger.warning(f"Cookie 检测失败: {e}")
            return False

    def _load_from_file(self) -> None:
        """从本地文件加载 Cookie/Token"""
        if not self._cookie_file.exists():
            return
        try:
            with open(self._cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cookie = data.get("cookie", "")
            self._token = data.get("token", "")
            if self._cookie:
                # 设置到 client headers
                self._client.headers["Cookie"] = self._cookie
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"加载 Cookie 文件失败: {e}")

    def _save_to_file(self) -> None:
        """保存 Cookie/Token 到本地文件"""
        self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cookie_file, "w", encoding="utf-8") as f:
            json.dump(
                {"cookie": self._cookie, "token": self._token},
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info(f"Cookie 已保存到 {self._cookie_file}")

    def login(self) -> LoginResult:
        """执行扫码登录完整流程

        流程:
        1. GET mp.weixin.qq.com 获取初始 Cookie
        2. POST startlogin 初始化登录会话
        3. GET getqrcode 下载二维码图片并弹窗显示
        4. 轮询 ask 等待扫码确认
        5. POST login 完成登录,从 redirect_url 提取 token
        6. GET redirect_url 获取完整 Cookie
        7. 持久化并返回 LoginResult
        """
        # 1. 访问首页获取初始 Cookie
        self._client.get(_MP_BASE)

        # 2. startlogin
        session_id = str(int(time.time() * 1000))
        startlogin_data = (
            f"userlang=zh_CN&redirect_url=&login_type=3"
            f"&sessionid={session_id}&token=&lang=zh_CN&f=json&ajax=1"
        )
        resp = self._request(_MP_STARTLOGIN, method="POST", data=startlogin_data)
        ret = resp.get("base_resp", {}).get("ret", -1)
        if ret != 0:
            err = resp.get("base_resp", {}).get("err_msg", f"ret={ret}")
            return LoginResult(success=False, error=f"startlogin 失败: {err}")

        # 3. 下载二维码并弹窗显示
        qr_resp = self._client.get(f"{_MP_QRCODE}&random={int(time.time() * 1000)}")
        if qr_resp.status_code != 200:
            return LoginResult(success=False, error="下载二维码失败")
        qr_thread = _ShowImageThread(qr_resp.content)
        qr_thread.start()
        logger.info("二维码已弹出，请用手机微信扫码确认")

        # 4. 轮询扫码状态
        elapsed = 0
        while elapsed < _POLL_TIMEOUT:
            data = self._request(_MP_ASK)
            status = data.get("status", -1)
            if status == 0:
                logger.info("等待扫码...")
            elif status == 6:
                logger.info("已扫码，请在手机上确认登录")
            elif status == 1:
                logger.info("已确认，登录成功")
                break
            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        else:
            return LoginResult(success=False, error=f"扫码超时（{_POLL_TIMEOUT}秒）")

        # 5. POST login 完成登录
        login_data = (
            "userlang=zh_CN&redirect_url=&cookie_forbidden=0&cookie_cleaned=1"
            "&plugin_used=0&login_type=3&token=&lang=zh_CN&f=json&ajax=1"
        )
        resp = self._request(_MP_LOGIN, method="POST", data=login_data)
        ret = resp.get("base_resp", {}).get("ret", -1)
        if ret != 0:
            err = resp.get("base_resp", {}).get("err_msg", f"ret={ret}")
            return LoginResult(success=False, error=f"login 失败: {err}")

        # 从 redirect_url 提取 token
        redirect_url = resp.get("redirect_url", "")
        token = parse_qs(urlparse(redirect_url).query).get("token", [None])[0]
        if not token:
            return LoginResult(success=False, error="未从 redirect_url 提取到 token")

        # 6. 访问 redirect_url 获取完整 Cookie
        self._client.get(f"{_MP_BASE}{redirect_url}")

        # 从 client cookies 拼接 cookie 字符串
        cookie_str = "; ".join(
            f"{name}={value}" for name, value in self._client.cookies.items()
        )

        self._cookie = cookie_str
        self._token = token

        # 7. 持久化
        self._save_to_file()

        logger.info(f"登录成功，token={token}")
        return LoginResult(success=True, cookie=cookie_str, token=token)
