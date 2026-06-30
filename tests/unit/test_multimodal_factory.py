"""多模态 Provider 工厂测试"""
import pytest
from unittest.mock import patch, MagicMock

from wehire_monitor.providers.factory import create_multimodal_provider
from wehire_monitor.providers.multimodal.mimo import MiMoProvider
from wehire_monitor.providers.multimodal.qwen_vl import QwenVLProvider


def test_create_multimodal_provider_mimo():
    """mimo provider 创建成功"""
    with patch.dict("os.environ", {
        "MULTIMODAL_PROVIDER": "mimo",
        "MULTIMODAL_API_KEY": "sk-test",
        "MULTIMODAL_MODEL": "mimo-v2.5",
    }):
        provider = create_multimodal_provider()
        assert isinstance(provider, MiMoProvider)
        assert provider.name == "mimo"
        assert provider.model == "mimo-v2.5"
        provider.close()


def test_create_multimodal_provider_qwen_vl():
    """qwen_vl provider 创建成功"""
    with patch.dict("os.environ", {
        "MULTIMODAL_PROVIDER": "qwen_vl",
        "MULTIMODAL_API_KEY": "sk-test",
        "MULTIMODAL_MODEL": "qwen-vl-max",
    }):
        provider = create_multimodal_provider()
        assert isinstance(provider, QwenVLProvider)
        assert provider.name == "qwen_vl"
        assert provider.model == "qwen-vl-max"
        provider.close()


def test_create_multimodal_provider_missing_api_key():
    """缺少 API Key 抛异常"""
    with patch.dict("os.environ", {
        "MULTIMODAL_PROVIDER": "mimo",
        "MULTIMODAL_API_KEY": "",
    }, clear=True):
        with pytest.raises(ValueError, match="MULTIMODAL_API_KEY"):
            create_multimodal_provider()


def test_create_multimodal_provider_unknown_raises():
    """不支持的 provider 名抛 ValueError"""
    with patch.dict("os.environ", {
        "MULTIMODAL_PROVIDER": "unknown_provider",
        "MULTIMODAL_API_KEY": "sk-test",
    }):
        with pytest.raises(ValueError, match="不支持的多模态 Provider"):
            create_multimodal_provider()
