"""v0.4 login 命令集成测试"""
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from wehire_monitor.main import app

runner = CliRunner()


def test_login_command_success():
    """login 命令成功执行"""
    from wehire_monitor.domain.models import LoginResult

    mock_result = LoginResult(
        success=True,
        cookie="slave_sid=abc; data_ticket=xyz",
        token="999888",
    )

    with patch(
        "wehire_monitor.modules.fetcher.wechat_login.WeChatLogin"
    ) as MockLogin:
        instance = MockLogin.return_value
        instance.is_cookie_valid.return_value = False
        instance.login.return_value = mock_result
        instance._cookie = mock_result.cookie
        instance._token = mock_result.token

        # mock ConfigLoader.update_env_cookie
        with patch(
            "wehire_monitor.config.loader.ConfigLoader.update_env_cookie"
        ):
            result = runner.invoke(app, ["login"])

    assert result.exit_code == 0
    assert "成功" in result.stdout
    instance.login.assert_called_once()


def test_login_command_cookie_already_valid():
    """Cookie 仍有效时不扫码"""
    with patch(
        "wehire_monitor.modules.fetcher.wechat_login.WeChatLogin"
    ) as MockLogin:
        instance = MockLogin.return_value
        instance.is_cookie_valid.return_value = True
        instance._cookie = "existing_cookie"
        instance._token = "existing_token"

        with patch(
            "wehire_monitor.config.loader.ConfigLoader.update_env_cookie"
        ):
            result = runner.invoke(app, ["login"])

    assert result.exit_code == 0
    assert "有效" in result.stdout or "无需" in result.stdout
    instance.login.assert_not_called()


def test_login_command_failure():
    """登录失败时退出码非 0"""
    from wehire_monitor.domain.models import LoginResult

    mock_result = LoginResult(
        success=False,
        error="扫码超时（120秒）",
    )

    with patch(
        "wehire_monitor.modules.fetcher.wechat_login.WeChatLogin"
    ) as MockLogin:
        instance = MockLogin.return_value
        instance.is_cookie_valid.return_value = False
        instance.login.return_value = mock_result

        result = runner.invoke(app, ["login"])

    assert result.exit_code != 0
    assert "失败" in result.output or "超时" in result.output
