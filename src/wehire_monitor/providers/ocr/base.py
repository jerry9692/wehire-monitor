"""OCR Provider 抽象接口"""
from __future__ import annotations
from typing import Protocol

from wehire_monitor.domain.models import OCRResult


class OCRProvider(Protocol):
    """OCR 供应商接口"""
    name: str

    def ocr(self, image_path: str) -> OCRResult:
        """对单张图片执行 OCR,返回结构化结果"""
        ...
