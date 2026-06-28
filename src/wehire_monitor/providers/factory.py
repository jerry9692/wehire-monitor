"""Provider 工厂 — 按 .env 配置返回对应实现"""
from __future__ import annotations
import os

from loguru import logger


def create_ocr_provider(provider_name: str | None = None):
    """创建 OCR Provider 实例"""
    name = provider_name or os.environ.get("OCR_PROVIDER", "rapid")
    if name == "rapid":
        from wehire_monitor.providers.ocr.rapid import RapidOCRProvider
        return RapidOCRProvider()
    raise ValueError(f"不支持的 OCR Provider: {name}")


def create_llm_provider(provider_name: str | None = None):
    """创建 LLM Provider 实例"""
    name = provider_name or os.environ.get("LLM_PROVIDER", "deepseek")
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()

    if not api_key:
        raise ValueError("LLM_API_KEY 未配置,请在 config/.env 中设置")

    if name == "deepseek":
        from wehire_monitor.providers.llm.deepseek import DeepSeekProvider
        return DeepSeekProvider(api_key=api_key, model=model or "deepseek-chat")

    raise ValueError(f"不支持的 LLM Provider: {name}")
