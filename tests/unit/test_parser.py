"""Parser 测试"""
import hashlib
from unittest.mock import patch, MagicMock

from wehire_monitor.modules.parser.parser import Parser
from wehire_monitor.domain.models import ArticleMeta
from datetime import datetime, timezone


SAMPLE_HTML = """
<html><body>
<div id="js_content">
  <p>某某集团2026年社会招聘公告</p>
  <p>岗位:数据分析师</p>
  <p>投递邮箱:hr@example.com</p>
  <img data-src="https://mmbiz.qpic.cn/test1.jpg" />
  <img src="https://mmbiz.qpic.cn/test2.jpg" />
</div>
</body></html>
"""


def test_parse_extracts_title_and_text():
    meta = ArticleMeta(
        account_name="测试号",
        title="招聘公告",
        url="https://mp.weixin.qq.com/s/xxx",
        publish_time=datetime(2026, 6, 28, tzinfo=timezone.utc),
    )
    parser = Parser(data_dir="/tmp/wehire_test")
    with patch.object(parser, "_fetch_html", return_value=SAMPLE_HTML), \
         patch.object(parser, "_download_image", return_value=("/tmp/img.jpg", 800, 600, "sha256abc")):
        result = parser.parse(meta)
    assert result.title == "招聘公告"
    assert "数据分析师" in result.plain_text
    assert "hr@example.com" in result.plain_text
    parser.close()


def test_parse_extracts_images():
    meta = ArticleMeta(
        account_name="测试号",
        title="招聘公告",
        url="https://mp.weixin.qq.com/s/xxx",
        publish_time=datetime(2026, 6, 28, tzinfo=timezone.utc),
    )
    parser = Parser(data_dir="/tmp/wehire_test")
    with patch.object(parser, "_fetch_html", return_value=SAMPLE_HTML), \
         patch.object(parser, "_download_image", return_value=("/tmp/img.jpg", 800, 600, "sha256abc")):
        result = parser.parse(meta)
    assert len(result.images) == 2
    assert result.images[0].url == "https://mmbiz.qpic.cn/test1.jpg"
    assert result.images[1].url == "https://mmbiz.qpic.cn/test2.jpg"
    parser.close()


def test_parse_article_id_is_url_hash():
    meta = ArticleMeta(
        account_name="测试号",
        title="招聘公告",
        url="https://mp.weixin.qq.com/s/xxx",
        publish_time=datetime(2026, 6, 28, tzinfo=timezone.utc),
    )
    expected_id = hashlib.sha256(meta.url.encode()).hexdigest()
    parser = Parser(data_dir="/tmp/wehire_test")
    with patch.object(parser, "_fetch_html", return_value=SAMPLE_HTML), \
         patch.object(parser, "_download_image", return_value=("/tmp/img.jpg", 800, 600, "sha256abc")):
        result = parser.parse(meta)
    assert result.article_id == expected_id
    parser.close()


def test_parse_content_hash_is_deterministic():
    meta = ArticleMeta(
        account_name="测试号",
        title="招聘公告",
        url="https://mp.weixin.qq.com/s/xxx",
        publish_time=datetime(2026, 6, 28, tzinfo=timezone.utc),
    )
    parser = Parser(data_dir="/tmp/wehire_test")
    with patch.object(parser, "_fetch_html", return_value=SAMPLE_HTML), \
         patch.object(parser, "_download_image", return_value=("/tmp/img.jpg", 800, 600, "sha256abc")):
        result1 = parser.parse(meta)
        result2 = parser.parse(meta)
    assert result1.content_hash == result2.content_hash
    parser.close()
