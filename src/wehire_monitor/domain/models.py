"""领域模型"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class ArticleMeta:
    """文章元信息(抓取阶段输出)"""
    account_name: str
    title: str
    url: str
    publish_time: datetime  # 必须是 timezone-aware datetime
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
    publish_time: str = ""   # ISO8601 发布时间,供后处理校验截止日期使用


@dataclass
class PrefilterResult:
    """预过滤结果"""
    score: int
    reasons: list[str]
    decision: Literal["extract", "ocr_review", "ignore"]


@dataclass
class CookieStatus:
    """Cookie 有效性状态"""
    is_valid: bool
    updated_at: str          # ISO8601
    age_hours: float
    nickname: str = ""       # Cookie 对应的账号昵称(有效时)
    message: str = ""        # 附加信息(无效时给出原因)


@dataclass
class RunLog:
    """运行日志"""
    run_id: str
    started_at: str          # ISO8601
    ended_at: str | None = None
    fetched_count: int = 0
    candidate_count: int = 0
    ocr_count: int = 0
    llm_count: int = 0
    vlm_count: int = 0
    cost_estimate: float = 0.0
    error_summary: str | None = None


@dataclass
class FetchResult:
    """抓取结果"""
    articles: list[ArticleMeta]
    error_accounts: list[str]
    cookie_expired: bool = False
    captcha_required: bool = False


# ========== v0.2 领域模型 ==========


@dataclass
class Deadline:
    """截止日期"""
    date: str | None          # YYYY-MM-DD 或 None
    inferred: bool = False    # 是否推断的年份


@dataclass
class Job:
    """结构化岗位信息"""
    company_name: str | None
    job_name: str | None
    location: str | None
    apply_channel: str | None
    email: str | None
    email_chars: list[str]
    deadline: Deadline
    source_evidence: dict
    confidence: int           # 0-100


@dataclass
class ExtractionResult:
    """提取结果"""
    article_type: Literal[
        "social_recruitment", "campus_recruitment",
        "internship", "non_recruitment", "unknown"
    ]
    jobs: list[Job]
    warnings: list[str]
    llm_calls: int = 0
    vlm_calls: int = 0
    ocr_calls: int = 0


@dataclass
class MatchedJob:
    """匹配后的岗位(含匹配分)"""
    job: Job
    match_score: int          # 0-100
    match_reasons: list[str]
    account_name: str = "-"   # 来源公众号(供日报展示)
    article_title: str = ""   # 文章标题
    article_url: str = ""     # 文章链接


@dataclass
class OCRLine:
    """OCR 单行结果"""
    text: str
    confidence: float         # 0.0-1.0
    box: list[int]            # [x, y, w, h]


@dataclass
class OCRResult:
    """OCR 完整结果"""
    lines: list[OCRLine]
    full_text: str


# ========== v0.3 领域模型 ==========


@dataclass
class SliceMeta:
    """切片元信息(PRD §8.3.2)"""
    image_index: int          # 原图索引(第几张图)
    slice_index: int          # 切片索引(该图的第几个切片)
    y_start: int              # 切片在原图中的 y 起始坐标
    y_end: int                # 切片在原图中的 y 结束坐标
    is_bottom: bool = False   # 是否为底部切片(含邮箱/二维码/报名方式)


@dataclass
class ImageSlice:
    """图片切片"""
    pil_image: object | None       # PIL.Image 对象(内存中,可 None 用于测试)
    local_path: str                # 切片保存的本地路径
    meta: SliceMeta
