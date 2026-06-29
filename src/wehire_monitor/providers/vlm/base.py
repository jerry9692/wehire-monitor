"""VLMProvider 抽象接口(SRS §7.1)

VLM(视觉语言模型)负责从图片切片中提取结构化岗位信息。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class VLMResponse:
    """VLM 提取响应"""
    success: bool
    article_type: str = "unknown"
    jobs: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cost_estimate: float = 0.0
    error: str = ""


@runtime_checkable
class VLMProvider(Protocol):
    """VLM 供应商抽象接口"""
    name: str

    def extract_jobs_from_slices(
        self,
        slices: list,
        title: str,
        publish_time: str,
    ) -> VLMResponse:
        """从图片切片中提取岗位信息"""
        ...

    def close(self) -> None:
        """释放资源"""
        ...
