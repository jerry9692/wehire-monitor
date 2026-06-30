"""Provider 工厂 — 按 .env 配置返回对应实现"""
from __future__ import annotations
import os

from loguru import logger


def create_multimodal_provider(provider_name: str | None = None):
    """创建多模态模型 Provider 实例(统一文本+图片)

    通过 MULTIMODAL_PROVIDER 环境变量切换:
    - mimo: MiMo-V2.5(默认,0.7 元/百万输入 token)
    - qwen_vl: Qwen-VL-Max(3 元/百万输入 token)
    - openai: OpenAI GPT-4o 等
    """
    name = provider_name or os.environ.get("MULTIMODAL_PROVIDER", "mimo").strip()
    api_key = os.environ.get("MULTIMODAL_API_KEY", "").strip()
    model = os.environ.get("MULTIMODAL_MODEL", "").strip()
    base_url = os.environ.get("MULTIMODAL_BASE_URL", "").strip()

    if not api_key:
        raise ValueError(
            "MULTIMODAL_API_KEY 未配置,请在 config/.env 中设置"
        )

    if name == "mimo":
        from wehire_monitor.providers.multimodal.mimo import MiMoProvider
        return MiMoProvider(
            api_key=api_key,
            model=model or None,
            base_url=base_url or None,
        )

    if name == "qwen_vl":
        from wehire_monitor.providers.multimodal.qwen_vl import QwenVLProvider
        return QwenVLProvider(
            api_key=api_key,
            model=model or None,
            base_url=base_url or None,
        )

    if name == "openai":
        from wehire_monitor.providers.multimodal.openai_compatible import (
            OpenAICompatibleProvider,
        )

        class _OpenAIProvider(OpenAICompatibleProvider):
            name = "openai"
            input_price = 17.5
            output_price = 70.0

        openai_base = base_url or "https://api.openai.com/v1"
        openai_model = model or "gpt-4o"
        return _OpenAIProvider(
            api_key=api_key,
            model=openai_model,
            base_url=openai_base,
        )

    raise ValueError(f"不支持的多模态 Provider: {name}")


# ========== 向后兼容(检测旧配置并警告) ==========

def create_llm_provider(provider_name: str | None = None):
    """[已废弃] 请使用 create_multimodal_provider()"""
    logger.warning(
        "create_llm_provider() 已废弃,请使用 create_multimodal_provider()。"
        "请在 config/.env 中将 LLM_* 配置改为 MULTIMODAL_*"
    )
    raise DeprecationWarning(
        "LLM/VLM/OCR 三路架构已废弃,请使用 create_multimodal_provider()"
    )


def create_vlm_provider(provider_name: str | None = None):
    """[已废弃] 请使用 create_multimodal_provider()"""
    logger.warning(
        "create_vlm_provider() 已废弃,请使用 create_multimodal_provider()。"
        "请在 config/.env 中将 VLM_* 配置改为 MULTIMODAL_*"
    )
    return None


def create_ocr_provider(provider_name: str | None = None):
    """[已废弃] 统一多模态架构不再需要 OCR"""
    logger.warning(
        "create_ocr_provider() 已废弃,统一多模态架构不再需要 OCR"
    )
    return None
