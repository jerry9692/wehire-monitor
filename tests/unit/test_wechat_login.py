"""WeChatLogin 扫码登录测试"""
import json
import time
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

from wehire_monitor.modules.fetcher.wechat_login import WeChatLogin


@pytest.fixture
def tmp_cookie_file(tmp_path):
    """临时 cookie 持久化文件"""
    return tmp_path / "wechat_cookie.json"


def test_is_cookie_valid_true(tmp_cookie_file):
    """已有 Cookie 有效时返回 True"""
    login = WeChatLogin(cookie_file=str(tmp_cookie_file))
    login._cookie = "slave_sid=abc; data_ticket=xyz"
    login._token = "123456"

    with patch.object(login, "_request") as mock_req:
        mock_req.return_value = {"base_resp": {"ret": 0}}
        assert login.is_cookie_valid() is True
    login.close()


def test_is_cookie_valid_false(tmp_cookie_file):
    """Cookie 失效时返回 False"""
    login = WeChatLogin(cookie_file=str(tmp_cookie_file))
    login._cookie = "slave_sid=expired"
    login._token = "123456"

    with patch.object(login, "_request") as mock_req:
        mock_req.return_value = {"base_resp": {"ret": 200003}}
        assert login.is_cookie_valid() is False
    login.close()


def test_is_cookie_valid_no_cookie(tmp_cookie_file):
    """无 Cookie 时返回 False"""
    login = WeChatLogin(cookie_file=str(tmp_cookie_file))
    assert login.is_cookie_valid() is False
    login.close()


def test_login_success(tmp_cookie_file):
    """完整扫码登录流程 mock 测试"""
    login = WeChatLogin(cookie_file=str(tmp_cookie_file))

    # mock _request 返回不同阶段的响应
    call_count = {"value": 0}

    def mock_request(url, method="GET", data=None):
        call_count["value"] += 1
        if "startlogin" in url:
            return {"base_resp": {"ret": 0}}
        if "getqrcode" in url:
            return {}  # 二维码图片返回空 dict(实际是 bytes)
        if "ask" in url:
            return {"status": 1, "base_resp": {"ret": 0}}  # 已确认登录
        if "bizlogin?action=login" in url:
            return {
                "base_resp": {"ret": 0},
                "redirect_url": "/cgi-bin/home?t=home/index&lang=zh_CN&token=999888",
            }
        return {"base_resp": {"ret": 0}}

    # mock 二维码图片下载(返回 bytes)
    def mock_get_qrcode(url):
        class FakeResp:
            status_code = 200
            content = b"fake_png_data"
        return FakeResp()

    # mock client cookies（login() 从 client.cookies 拼接 cookie 字符串）
    login._client.cookies = {"slave_sid": "abc", "data_ticket": "xyz"}

    with patch.object(login, "_request", side_effect=mock_request), \
         patch.object(login._client, "get", side_effect=mock_get_qrcode), \
         patch("wehire_monitor.modules.fetcher.wechat_login._ShowImageThread") as mock_thread:
        result = login.login()

    assert result.success is True
    assert result.token == "999888"
    assert "slave_sid" in result.cookie or len(result.cookie) > 0
    assert call_count["value"] >= 3  # startlogin + ask + login
    login.close()


def test_login_timeout(tmp_cookie_file):
    """扫码超时返回失败"""
    login = WeChatLogin(cookie_file=str(tmp_cookie_file))

    def mock_request(url, method="GET", data=None):
        if "startlogin" in url:
            return {"base_resp": {"ret": 0}}
        if "getqrcode" in url:
            return {}
        if "ask" in url:
            return {"status": 0, "base_resp": {"ret": 0}}  # 始终未扫码
        return {"base_resp": {"ret": 0}}

    def mock_get_qrcode(url):
        class FakeResp:
            status_code = 200
            content = b"fake_png_data"
        return FakeResp()

    with patch.object(login, "_request", side_effect=mock_request), \
         patch.object(login._client, "get", side_effect=mock_get_qrcode), \
         patch("wehire_monitor.modules.fetcher.wechat_login._ShowImageThread"), \
         patch("wehire_monitor.modules.fetcher.wechat_login._POLL_TIMEOUT", 4), \
         patch("wehire_monitor.modules.fetcher.wechat_login._POLL_INTERVAL", 1):
        result = login.login()

    assert result.success is False
    assert "超时" in result.error or "timeout" in result.error.lower()
    login.close()


def test_login_startlogin_fail(tmp_cookie_file):
    """startlogin 失败返回错误"""
    login = WeChatLogin(cookie_file=str(tmp_cookie_file))

    with patch.object(login, "_request") as mock_req:
        mock_req.return_value = {"base_resp": {"ret": -1, "err_msg": "too many attempts"}}
        result = login.login()

    assert result.success is False
    assert "startlogin" in result.error or "-1" in result.error
    login.close()


def test_save_and_load_cookie(tmp_cookie_file):
    """Cookie 保存后重新加载能恢复"""
    login1 = WeChatLogin(cookie_file=str(tmp_cookie_file))
    login1._cookie = "slave_sid=abc; data_ticket=xyz"
    login1._token = "123456"
    login1._save_to_file()
    login1.close()

    # 重新创建实例，应自动加载
    login2 = WeChatLogin(cookie_file=str(tmp_cookie_file))
    assert login2._cookie == "slave_sid=abc; data_ticket=xyz"
    assert login2._token == "123456"
    login2.close()


def test_load_nonexistent_file(tmp_cookie_file):
    """文件不存在时不报错，Cookie 为空"""
    login = WeChatLogin(cookie_file=str(tmp_cookie_file / "nonexistent.json"))
    assert login._cookie == ""
    assert login._token == ""
    login.close()


def test_load_corrupted_file(tmp_cookie_file):
    """文件损坏时不报错，Cookie 为空"""
    tmp_cookie_file.write_text("{invalid json")
    login = WeChatLogin(cookie_file=str(tmp_cookie_file))
    assert login._cookie == ""
    login.close()
