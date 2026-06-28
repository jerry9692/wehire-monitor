"""DeepSeek LLM Provider 测试"""
import json
import pytest
from unittest.mock import patch, MagicMock

from wehire_monitor.providers.llm.base import LLMProvider, LLMResponse
from wehire_monitor.providers.llm.deepseek import DeepSeekProvider


def test_deepseek_initialization():
    provider = DeepSeekProvider(api_key="sk-test", model="deepseek-chat")
    assert provider.name == "deepseek"
    assert provider.model == "deepseek-chat"


def test_deepseek_extract_jobs_success():
    """成功提取岗位 — mock HTTP 返回合法 JSON"""
    mock_json = {
        "article_type": "social_recruitment",
        "jobs": [
            {
                "company_name": "德邦证券",
                "job_name": "数据分析师",
                "location": "上海",
                "apply_channel": "hr@example.com",
                "email": "hr@example.com",
                "email_chars": ["h","r","@","e","x","a","m","p","l","e",".","c","o","m"],
                "deadline": {"date": "2026-07-31", "inferred": False},
                "source_evidence": {"company_name": "德邦证券"},
                "confidence": 85,
            }
        ],
        "warnings": [],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(mock_json)}}]
    }
    mock_resp.raise_for_status = MagicMock()

    provider = DeepSeekProvider(api_key="sk-test", model="deepseek-chat")
    with patch.object(provider._client, "post", return_value=mock_resp):
        response = provider.extract_jobs(
            text="德邦证券招聘数据分析师，邮箱 hr@example.com",
            title="招聘公告",
            publish_time="2026-06-28",
        )
    assert response.success is True
    assert response.article_type == "social_recruitment"
    assert len(response.jobs) == 1
    assert response.jobs[0].company_name == "德邦证券"
    assert response.jobs[0].confidence == 85


def test_deepseek_extract_jobs_invalid_json():
    """LLM 返回非法 JSON — 标记失败"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "这不是JSON"}}]
    }
    mock_resp.raise_for_status = MagicMock()

    provider = DeepSeekProvider(api_key="sk-test", model="deepseek-chat")
    with patch.object(provider._client, "post", return_value=mock_resp):
        response = provider.extract_jobs(text="正文", title="标题", publish_time="2026-06-28")
    assert response.success is False
    assert "JSON" in response.error or "json" in response.error.lower()


def test_deepseek_extract_jobs_api_error():
    """API 错误(401)— 标记失败"""
    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401 Unauthorized", request=MagicMock(), response=mock_resp
    )

    provider = DeepSeekProvider(api_key="sk-bad", model="deepseek-chat")
    with patch.object(provider._client, "post", return_value=mock_resp):
        response = provider.extract_jobs(text="正文", title="标题", publish_time="2026-06-28")
    assert response.success is False


def test_deepseek_retry_on_invalid_json():
    """JSON 解析失败重试 1 次"""
    call_count = [0]
    def mock_post(*args, **kwargs):
        call_count[0] += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if call_count[0] == 1:
            mock_resp.json.return_value = {"choices": [{"message": {"content": "bad"}}]}
        else:
            mock_resp.json.return_value = {"choices": [{"message": {"content": '{"article_type":"unknown","jobs":[],"warnings":[]}'}}]}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    provider = DeepSeekProvider(api_key="sk-test", model="deepseek-chat")
    with patch.object(provider._client, "post", side_effect=mock_post):
        response = provider.extract_jobs(text="正文", title="标题", publish_time="2026-06-28")
    assert call_count[0] == 2  # 第一次失败 + 重试一次
    assert response.success is True
