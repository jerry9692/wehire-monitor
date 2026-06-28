"""领域模型"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class ArticleMeta:
    """文章元信息(抓取阶段输出)"""
    account_name: str
    title: str
    url: str
    publish_time: datetime
    source: Literal["wechat_mp_backend"] = "wechat_mp_backend"


@dataclass
class ImageAsset:
    """图片资产"""
    index: int
    url: str
    local_path: str | None
    width: int
    height: int
    sha256: str
    status: Literal["ok", "image_download_failed"] = "ok"


@dataclass
class ParsedArticle:
    """解析后的文章(解析阶段输出)"""
    article_id: str          # sha256(url)
    title: str
    plain_text: str
    images: list[ImageAsset]
    content_hash: str


@dataclass
class CookieStatus:
    """Cookie 有效性状态"""
    is_valid: bool
    updated_at: str          # ISO8601
    age_hours: float


@dataclass
class RunLog:
    """运行日志"""
    run_id: str
    started_at: str          # ISO8601
    ended_at: str | None = None
    fetched_count: int = 0
    candidate_count: int = 0
    error_summary: str | None = None
