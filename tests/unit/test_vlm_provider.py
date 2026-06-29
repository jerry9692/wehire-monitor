"""VLM Provider 测试"""
import json
import httpx
import pytest
from unittest.mock import patch, MagicMock

from wehire_monitor.domain.models import ImageSlice, SliceMeta
from wehire_monitor.providers.vlm.base import VLMProvider, VLMResponse
from wehire_monitor.providers.vlm.qwen_vl import QwenVLProvider


# ---------- VLMResponse 构造 ----------

def test_vlm_response_success():
    """VLMResponse 成功构造"""
    resp = VLMResponse(
        success=True,
        article_type="social_recruitment",
        jobs=["job1"],
        warnings=["w1"],
        cost_estimate=0.06,
    )
    assert resp.success is True
    assert resp.article_type == "social_recruitment"
    assert resp.jobs == ["job1"]
    assert resp.warnings == ["w1"]
    assert resp.cost_estimate == 0.06
    assert resp.error == ""


def test_vlm_response_failure():
    """VLMResponse 失败构造"""
    resp = VLMResponse(success=False, error="boom")
    assert resp.success is False
    assert resp.error == "boom"
    assert resp.article_type == "unknown"
    assert resp.jobs == []
    assert resp.warnings == []
    assert resp.cost_estimate == 0.0


# ---------- Protocol ----------

def test_vlm_provider_protocol_exists():
    """VLMProvider Protocol 存在,且 QwenVLProvider 满足接口"""
    provider = QwenVLProvider(api_key="sk-test", model="qwen-vl-max")
    assert isinstance(provider, VLMProvider)
    assert provider.name == "qwen_vl"
    assert hasattr(provider, "extract_jobs_from_slices")
    assert hasattr(provider, "close")


# ---------- 辅助:构造带真实临时文件的切片 ----------

def _make_slice(tmp_path, image_index=0, slice_index=0, y_start=0, y_end=800):
    """构造一个 ImageSlice,local_path 指向真实临时文件"""
    img = tmp_path / f"slice_{image_index}_{slice_index}.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    meta = SliceMeta(
        image_index=image_index,
        slice_index=slice_index,
        y_start=y_start,
        y_end=y_end,
    )
    return ImageSlice(pil_image=None, local_path=str(img), meta=meta)


def _mock_ok_response(payload: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ---------- QwenVLProvider 提取 ----------

def test_qwen_vl_extract_success(tmp_path):
    """QwenVLProvider 成功提取 — mock httpx 返回合法 JSON"""
    payload = {
        "article_type": "social_recruitment",
        "jobs": [
            {
                "company_name": "德邦证券",
                "job_name": "数据分析师",
                "location": "上海",
                "apply_channel": "hr@example.com",
                "email": "hr@example.com",
                "email_chars": list("hr@example.com"),
                "deadline": {"date": "2026-07-31", "inferred": False},
                "source_evidence": {"company_name": "德邦证券"},
                "confidence": 85,
            }
        ],
        "warnings": [],
    }
    mock_resp = _mock_ok_response(payload)

    provider = QwenVLProvider(api_key="sk-test", model="qwen-vl-max")
    sl = _make_slice(tmp_path)
    with patch.object(provider._client, "post", return_value=mock_resp):
        response = provider.extract_jobs_from_slices(
            slices=[sl], title="招聘公告", publish_time="2026-06-28"
        )
    assert response.success is True
    assert response.article_type == "social_recruitment"
    assert len(response.jobs) == 1
    assert response.jobs[0].company_name == "德邦证券"
    assert response.jobs[0].confidence == 85
    assert response.jobs[0].email_chars == list("hr@example.com")
    # 成本估算: 1 切片 * 0.03
    assert response.cost_estimate == pytest.approx(0.03)


def test_qwen_vl_api_error(tmp_path):
    """QwenVLProvider API 错误(401)— 标记失败"""
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401 Unauthorized", request=MagicMock(), response=mock_resp
    )

    provider = QwenVLProvider(api_key="sk-bad", model="qwen-vl-max")
    sl = _make_slice(tmp_path)
    # patch time.sleep 避免重试退避拖慢测试
    with patch.object(provider._client, "post", return_value=mock_resp), \
         patch("wehire_monitor.providers.vlm.qwen_vl.time.sleep"):
        response = provider.extract_jobs_from_slices(
            slices=[sl], title="招聘公告", publish_time="2026-06-28"
        )
    assert response.success is False
    assert "401" in response.error or "API error" in response.error


def test_qwen_vl_invalid_json(tmp_path):
    """QwenVLProvider 非法 JSON — 标记失败"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "这不是合法JSON"}}]
    }
    mock_resp.raise_for_status = MagicMock()

    provider = QwenVLProvider(api_key="sk-test", model="qwen-vl-max")
    sl = _make_slice(tmp_path)
    with patch.object(provider._client, "post", return_value=mock_resp), \
         patch("wehire_monitor.providers.vlm.qwen_vl.time.sleep"):
        response = provider.extract_jobs_from_slices(
            slices=[sl], title="招聘公告", publish_time="2026-06-28"
        )
    assert response.success is False
    assert "JSON" in response.error or "json" in response.error.lower()


def test_qwen_vl_markdown_wrapped_json(tmp_path):
    """VLM 返回 ```json``` 包裹的 JSON 也能正常解析"""
    payload = {
        "article_type": "campus_recruitment",
        "jobs": [],
        "warnings": ["ok"],
    }
    wrapped = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": wrapped}}]}
    mock_resp.raise_for_status = MagicMock()

    provider = QwenVLProvider(api_key="sk-test", model="qwen-vl-max")
    sl = _make_slice(tmp_path)
    with patch.object(provider._client, "post", return_value=mock_resp):
        response = provider.extract_jobs_from_slices(
            slices=[sl], title="校招", publish_time="2026-06-28"
        )
    assert response.success is True
    assert response.article_type == "campus_recruitment"


# ---------- prompt 渲染 ----------

def test_qwen_vl_prompt_render():
    """QwenVLProvider prompt 渲染 — 占位符全部替换"""
    provider = QwenVLProvider(api_key="sk-test", model="qwen-vl-max")
    rendered = provider._render_prompt(
        title="测试标题", image_index=1, slice_index=2, y_start=100, y_end=500,
        is_bottom=False, publish_time="2026-06-28",
    )
    assert "测试标题" in rendered
    assert "{{title}}" not in rendered
    assert "{{image_index}}" not in rendered
    assert "{{slice_index}}" not in rendered
    assert "{{y_start}}" not in rendered
    assert "{{y_end}}" not in rendered
    assert "{{is_bottom}}" not in rendered
    assert "{{publish_time}}" not in rendered
    # 验证具体值已注入
    assert "image_index=1" in rendered
    assert "slice_index=2" in rendered
    assert "y_range=100-500" in rendered
