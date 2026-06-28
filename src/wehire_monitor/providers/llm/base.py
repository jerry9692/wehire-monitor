"""LLM Provider 抽象接口"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

from wehire_monitor.domain.models import Job


@dataclass
class LLMResponse:
    """LLM 调用结果"""
    success: bool
    article_type: str = "unknown"
    jobs: list[Job] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    raw_content: str = ""


class LLMProvider(Protocol):
    """LLM 供应商接口"""
    name: str
    model: str

    def extract_jobs(
        self, text: str, title: str, publish_time: str
    ) -> LLMResponse:
        """从文本中提取结构化岗位信息"""
        ...
