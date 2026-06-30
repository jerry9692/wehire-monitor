"""统一多模态模型 Provider 包

从原来的三路提取(OCR + LLM + VLM)架构演进为统一多模态模型架构:
同一个模型同时接收文本和图片,完成结构化岗位信息提取。

支持所有 OpenAI 兼容的多模态模型:
- MiMo-V2.5(默认,小米)
- GPT-4o / GPT-5(OpenAI)
- Qwen-VL-Max / Qwen3.7 Plus(阿里千问,通过 DashScope 兼容端点)
- 豆包、智谱 GLM-4V、Claude(通过兼容代理)等
- 任何支持 /chat/completions + vision(image_url) 的端点

导出:
- :class:`MultimodalProvider`   统一多模态供应商接口(Protocol)
- :class:`MultimodalResponse`   提取响应数据结构
- :class:`OpenAICompatibleProvider` OpenAI 兼容基类(自定义 provider 可继承)
- :class:`MiMoProvider`         MiMo-V2.5 实现(默认 provider)
"""
from __future__ import annotations

from wehire_monitor.providers.multimodal.base import (
    MultimodalProvider,
    MultimodalResponse,
)
from wehire_monitor.providers.multimodal.mimo import MiMoProvider
from wehire_monitor.providers.multimodal.openai_compatible import (
    OpenAICompatibleProvider,
)

__all__ = [
    "MultimodalProvider",
    "MultimodalResponse",
    "OpenAICompatibleProvider",
    "MiMoProvider",
]
