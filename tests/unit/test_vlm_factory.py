"""VLM Provider 工厂测试"""
import pytest
from unittest.mock import patch

from wehire_monitor.providers.factory import create_vlm_provider
from wehire_monitor.providers.vlm.qwen_vl import QwenVLProvider


def test_create_vlm_provider_qwen_vl():
    """qwen_vl provider 创建成功"""
    with patch.dict("os.environ", {
        "VLM_PROVIDER": "qwen_vl",
        "VLM_API_KEY": "sk-test",
        "VLM_MODEL": "qwen-vl-max",
    }):
        provider = create_vlm_provider()
        assert isinstance(provider, QwenVLProvider)
        assert provider.name == "qwen_vl"
        assert provider.model == "qwen-vl-max"
        provider.close()


def test_create_vlm_provider_unknown_raises():
    """未知 provider 抛异常"""
    with patch.dict("os.environ", {
        "VLM_PROVIDER": "unknown_vlm",
        "VLM_API_KEY": "sk-test",
    }):
        with pytest.raises(ValueError, match="不支持的 VLM Provider"):
            create_vlm_provider()


def test_create_vlm_provider_missing_api_key():
    """缺少 API Key 抛异常"""
    with patch.dict("os.environ", {
        "VLM_PROVIDER": "qwen_vl",
        "VLM_API_KEY": "",
    }, clear=True):
        with pytest.raises(ValueError, match="VLM_API_KEY"):
            create_vlm_provider()


def test_create_vlm_provider_not_set_returns_none():
    """VLM_PROVIDER 未设置返回 None"""
    with patch.dict("os.environ", {}, clear=True):
        result = create_vlm_provider()
        assert result is None
