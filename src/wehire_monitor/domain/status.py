"""文章状态机枚举

状态流转:
    discovered → fetched → parsed → ignored | candidate → notified → archived

错误分支:
    error_fetch, error_parse,
    need_cookie, need_captcha, need_review
"""
from enum import Enum


class Status(str, Enum):
    DISCOVERED = "discovered"
    FETCHED = "fetched"
    PARSED = "parsed"
    IGNORED = "ignored"
    CANDIDATE = "candidate"
    NOTIFIED = "notified"
    ARCHIVED = "archived"

    # 错误状态
    ERROR_FETCH = "error_fetch"
    ERROR_PARSE = "error_parse"

    # 待处理状态
    NEED_COOKIE = "need_cookie"
    NEED_CAPTCHA = "need_captcha"
    NEED_REVIEW = "need_review"
