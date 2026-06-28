# src/wehire_monitor/modules/fetcher/exceptions.py
"""Fetcher 异常"""


class FetcherError(Exception):
    """Fetcher 基础异常"""


class CookieInvalidError(FetcherError):
    """Cookie 失效"""


class CaptchaRequiredError(FetcherError):
    """需要验证码"""


class AccountNotFoundError(FetcherError):
    """公众号未找到"""


class RateLimitedError(FetcherError):
    """被限流"""
