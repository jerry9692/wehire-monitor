"""领域模型测试"""
from datetime import datetime, timezone

from wehire_monitor.domain.models import (
    ArticleMeta,
    ImageAsset,
    ParsedArticle,
    RunLog,
    CookieStatus,
)


def test_article_meta_creation():
    meta = ArticleMeta(
        account_name="上海国资招聘",
        title="某某集团2026年社会招聘公告",
        url="https://mp.weixin.qq.com/s/xxx",
        publish_time=datetime(2026, 6, 28, 9, 30, tzinfo=timezone.utc),
        source="wechat_mp_backend",
    )
    assert meta.account_name == "上海国资招聘"
    assert meta.source == "wechat_mp_backend"


def test_image_asset_defaults():
    asset = ImageAsset(
        index=0,
        url="https://mmbiz.qpic.cn/xxx.jpg",
        local_path=None,
        width=1080,
        height=560,
        sha256="abc123",
        status="ok",
    )
    assert asset.status == "ok"


def test_parsed_article_creation():
    article = ParsedArticle(
        article_id="sha256hash",
        title="测试标题",
        plain_text="正文内容",
        images=[],
        content_hash="contenthash",
    )
    assert article.article_id == "sha256hash"
    assert article.images == []


def test_run_log_defaults():
    log = RunLog(run_id="run-001", started_at="2026-06-28T08:30:00+08:00")
    assert log.fetched_count == 0
    assert log.candidate_count == 0
    assert log.error_summary is None


def test_cookie_status_fields():
    cs = CookieStatus(is_valid=True, updated_at="2026-06-28T08:00:00+08:00", age_hours=2.0)
    assert cs.is_valid is True
    assert cs.age_hours == 2.0
