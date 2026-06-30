"""多模态 Provider 工厂测试"""
import pytest
from unittest.mock import patch

from wehire_monitor.providers.factory import create_multimodal_provider
from wehire_monitor.providers.multimodal.mimo import MiMoProvider
from wehire_monitor.providers.multimodal.openai_compatible import (
    OpenAICompatibleProvider,
)


def test_create_multimodal_provider_mimo():
    """mimo provider 创建成功(默认)"""
    with patch.dict("os.environ", {
        "MULTIMODAL_PROVIDER": "mimo",
        "MULTIMODAL_API_KEY": "sk-test",
        "MULTIMODAL_MODEL": "mimo-v2.5",
    }):
        provider = create_multimodal_provider()
        assert isinstance(provider, MiMoProvider)
        assert provider.name == "mimo"
        assert provider.model == "mimo-v2.5"
        assert "xiaomimimo.com" in provider.base_url
        provider.close()


def test_create_multimodal_provider_openai_generic():
    """openai 通用 provider 创建成功(可接入任意兼容模型)"""
    with patch.dict("os.environ", {
        "MULTIMODAL_PROVIDER": "openai",
        "MULTIMODAL_API_KEY": "sk-test",
        "MULTIMODAL_MODEL": "qwen-vl-max",
        "MULTIMODAL_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "MULTIMODAL_INPUT_PRICE": "3.0",
        "MULTIMODAL_OUTPUT_PRICE": "9.0",
    }):
        provider = create_multimodal_provider()
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "openai"
        assert provider.model == "qwen-vl-max"
        assert provider.input_price == 3.0
        assert provider.output_price == 9.0
        assert provider.base_url == (
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        )
        provider.close()


def test_create_multimodal_provider_openai_missing_base_url():
    """openai provider 缺少 MULTIMODAL_BASE_URL 抛异常"""
    with patch.dict("os.environ", {
        "MULTIMODAL_PROVIDER": "openai",
        "MULTIMODAL_API_KEY": "sk-test",
        "MULTIMODAL_MODEL": "gpt-4o",
    }, clear=True):
        with pytest.raises(ValueError, match="MULTIMODAL_BASE_URL"):
            create_multimodal_provider()


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
