"""RapidOCR 实现(本地 CPU OCR)"""
from __future__ import annotations

from loguru import logger

from wehire_monitor.domain.models import OCRResult, OCRLine


class RapidOCRProvider:
    """RapidOCR-onnxruntime 实现"""

    name = "rapid"

    def __init__(self):
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        except ImportError:
            logger.error("rapidocr-onnxruntime 未安装,请运行 uv add rapidocr-onnxruntime")
            raise

    def ocr(self, image_path: str) -> OCRResult:
        """对图片执行 OCR"""
        try:
            result, _ = self._engine(image_path)
            if not result:
                return OCRResult(lines=[], full_text="")

            lines: list[OCRLine] = []
            texts: list[str] = []
            for item in result:
                box, text, conf = item
                # box 是 4 个点的坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                x_coords = [p[0] for p in box]
                y_coords = [p[1] for p in box]
                x = int(min(x_coords))
                y = int(min(y_coords))
                w = int(max(x_coords) - x)
                h = int(max(y_coords) - y)
                lines.append(OCRLine(
                    text=text,
                    confidence=float(conf),
                    box=[x, y, w, h],
                ))
                texts.append(text)

            return OCRResult(
                lines=lines,
                full_text="\n".join(texts),
            )
        except Exception as e:
            logger.error(f"OCR 失败 ({image_path}): {e}")
            return OCRResult(lines=[], full_text="")
