"""Provider 工厂测试"""
import pytest
from unittest.mock import patch, MagicMock

from wehire_monitor.providers.factory import create_ocr_provider, create_llm_provider
from wehire_monitor.providers.ocr.rapid import RapidOCRProvider
from wehire_monitor.providers.llm.deepseek import DeepSeekProvider


def test_create_ocr_provider_rapid():
    with patch("rapidocr_onnxruntime.RapidOCR"):
        provider = create_ocr_provider("rapid")
        assert isinstance(provider, RapidOCRProvider)


def test_create_ocr_provider_unknown_raises():
    with pytest.raises(ValueError, match="不支持的 OCR"):
        create_ocr_provider("unknown")


def test_create_llm_provider_deepseek():
    with patch.dict("os.environ", {
        "LLM_PROVIDER": "deepseek",
        "LLM_API_KEY": "sk-test",
        "LLM_MODEL": "deepseek-chat",
    }):
        provider = create_llm_provider()
        assert isinstance(provider, DeepSeekProvider)


def test_create_llm_provider_missing_api_key():
    with patch.dict("os.environ", {"LLM_PROVIDER": "deepseek", "LLM_API_KEY": ""}, clear=True):
        with pytest.raises(ValueError, match="LLM_API_KEY"):
            create_llm_provider()
