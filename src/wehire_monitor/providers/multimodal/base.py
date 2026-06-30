"""统一多模态模型 Provider 抽象接口

从原来的三路提取(OCR + LLM + VLM)架构演进为统一多模态模型架构:
同一个模型同时接收文本和图片,完成结构化岗位信息提取。

本模块定义统一接口 ``MultimodalProvider`` 与响应数据结构 ``MultimodalResponse``,
供 ``openai_compatible``、``mimo`` 等具体实现遵循。
所有 OpenAI 兼容的多模态模型(GPT-4o/GPT-5、Qwen-VL、豆包、智谱等)
通过 ``openai_compatible.OpenAICompatibleProvider`` 基类即可接入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from wehire_monitor.domain.models import Job


@dataclass
class MultimodalResponse:
    """多模态模型提取响应

    Attributes:
        success: 整体提取是否成功(至少有一次成功调用即为成功)
        article_type: 文章类型(social_recruitment / campus_recruitment /
            internship / non_recruitment / unknown)
        jobs: 提取出的结构化岗位列表
        warnings: 提取过程中的告警信息(如某切片失败、字符不确定等)
        cost_estimate: 成本估算(元),按 input/output token 单价计算
        model_calls: 实际发起的 API 调用次数
        error: 失败时的错误信息(成功时为空字符串)
    """
    success: bool
    article_type: str = "unknown"
    jobs: list[Job] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cost_estimate: float = 0.0
    model_calls: int = 0  # API 调用次数
    error: str = ""


@runtime_checkable
class MultimodalProvider(Protocol):
    """统一多模态模型供应商接口(文本 + 图片同一模型)

    实现方需提供 ``name``、``model`` 属性,并实现 ``extract_jobs`` 与 ``close``。
    具体实现可参考 ``OpenAICompatibleProvider`` 基类。
    """
    name: str
    model: str

    def extract_jobs(
        self,
        text: str | None,
        images: list,  # list[ImageSlice], ImageSlice 来自 domain.models
        title: str,
        publish_time: str,
    ) -> MultimodalResponse:
        """从文本和/或图片中提取结构化岗位信息

        Args:
            text: 文章正文文本(可为 None,表示纯图片文章)
            images: 图片切片列表(可为空列表,表示纯文本文章)
            title: 文章标题
            publish_time: 文章发布时间(ISO8601)

        Returns:
            :class:`MultimodalResponse` 提取结果
        """
        ...

    def close(self) -> None:
        """释放资源(如关闭 httpx.Client)"""
        ...
