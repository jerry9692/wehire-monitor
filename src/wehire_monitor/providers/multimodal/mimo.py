"""MiMo-V2.5 多模态 Provider(默认 provider)

小米 MiMo-V2.5 通过 OpenAI 兼容接口调用,支持文本与图片在同一请求中输入,
适合作为统一多模态架构的默认实现。本类仅声明模型端点与计价参数,
其余 HTTP 调用、重试、JSON 解析、成本估算等逻辑全部复用
:class:`OpenAICompatibleProvider` 基类。
"""
from __future__ import annotations

from wehire_monitor.providers.multimodal.openai_compatible import (
    OpenAICompatibleProvider,
)


class MiMoProvider(OpenAICompatibleProvider):
    """MiMo-V2.5 多模态实现(单次调用,文本 + 图片同发)

    - base_url:     https://api.xiaomimimo.com/v1/chat/completions
    - model:        mimo-v2.5
    - 输入价格:      0.7 元/百万 token
    - 输出价格:      2.1 元/百万 token
    """

    name = "mimo"
    base_url = "https://api.xiaomimimo.com/v1/chat/completions"
    model = "mimo-v2.5"
    input_price = 0.7   # 元/百万 token
    output_price = 2.1  # 元/百万 token

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """初始化 MiMo Provider

        Args:
            api_key: 小米 MiMo API Key
            model:   可选,覆盖默认模型名 ``mimo-v2.5``
            base_url: 可选,覆盖默认 API 端点(支持传入 base URL 或完整 endpoint)
        """
        super().__init__(api_key, model, base_url)
