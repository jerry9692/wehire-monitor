"""统一多模态模型 Provider 包

从原来的三路提取(OCR + LLM + VLM)架构演进为统一多模态模型架构:
同一个模型同时接收文本和图片,完成结构化岗位信息提取。

导出:
- :class:`MultimodalProvider`   统一多模态供应商接口(Protocol)
- :class:`MultimodalResponse`   提取响应数据结构
- :class:`OpenAICompatibleProvider` OpenAI 兼容基类(供自定义 provider 继承)
- :class:`MiMoProvider`         MiMo-V2.5 实现(默认 provider)
- :class:`QwenVLProvider`       Qwen-VL-Max 实现(逐切片调用)
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
from wehire_monitor.providers.multimodal.qwen_vl import QwenVLProvider

__all__ = [
    "MultimodalProvider",
    "MultimodalResponse",
    "OpenAICompatibleProvider",
    "MiMoProvider",
    "QwenVLProvider",
]
