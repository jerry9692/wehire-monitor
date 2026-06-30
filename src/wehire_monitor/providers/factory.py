"""Provider 工厂 — 按 .env 配置返回对应多模态模型实现

支持的 provider 名称:
- mimo:      MiMo-V2.5(默认,0.7 元/百万输入 token)
- openai:    通用 OpenAI 兼容端点(可接入 GPT-4o/GPT-5、Qwen-VL、
             豆包、智谱 GLM-4V、Claude 等任何兼容 /chat/completions + vision 的模型)

使用 openai provider 时,通过以下环境变量配置:
- MULTIMODAL_BASE_URL:   API 端点 base URL(自动补全 /chat/completions)
- MULTIMODAL_MODEL:      模型名(如 gpt-4o、qwen-vl-max、qwen3.7-plus 等)
- MULTIMODAL_INPUT_PRICE:  输入单价(元/百万 token,可选,默认 0)
- MULTIMODAL_OUTPUT_PRICE: 输出单价(元/百万 token,可选,默认 0)
"""
from __future__ import annotations
import os

from loguru import logger


def create_multimodal_provider(provider_name: str | None = None):
    """创建多模态模型 Provider 实例(统一文本+图片)

    通过 MULTIMODAL_PROVIDER 环境变量切换:
    - mimo:   MiMo-V2.5(默认)
    - openai: 通用 OpenAI 兼容端点(支持任意多模态模型)
    """
    name = provider_name or os.environ.get("MULTIMODAL_PROVIDER", "mimo").strip()
    api_key = os.environ.get("MULTIMODAL_API_KEY", "").strip()
    model = os.environ.get("MULTIMODAL_MODEL", "").strip()
    base_url = os.environ.get("MULTIMODAL_BASE_URL", "").strip()

    if not api_key:
        raise ValueError(
            "MULTIMODAL_API_KEY 未配置,请在 config/.env 中设置"
        )

    # 读取可选的价格配置(元/百万 token),用于成本估算
    def _parse_price(key: str, default: float = 0.0) -> float:
        raw = os.environ.get(key, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            logger.warning(f"{key} 格式无效({raw}),使用默认值 {default}")
            return default

    if name == "mimo":
        from wehire_monitor.providers.multimodal.mimo import MiMoProvider
        return MiMoProvider(
            api_key=api_key,
            model=model or None,
            base_url=base_url or None,
        )

    if name == "openai":
        from wehire_monitor.providers.multimodal.openai_compatible import (
            OpenAICompatibleProvider,
        )

        _in_price = _parse_price("MULTIMODAL_INPUT_PRICE", 0.0)
        _out_price = _parse_price("MULTIMODAL_OUTPUT_PRICE", 0.0)

        class _GenericProvider(OpenAICompatibleProvider):
            name = "openai"
            input_price = _in_price
            output_price = _out_price

        if not base_url:
            raise ValueError(
                "使用 openai provider 时必须配置 MULTIMODAL_BASE_URL"
            )

        return _GenericProvider(
            api_key=api_key,
            model=model or None,
            base_url=base_url,
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
