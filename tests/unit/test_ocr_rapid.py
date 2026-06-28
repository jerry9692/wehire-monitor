"""RapidOCR Provider 测试"""
import pytest
from unittest.mock import patch, MagicMock

from wehire_monitor.providers.ocr.base import OCRProvider, OCRResult
from wehire_monitor.providers.ocr.rapid import RapidOCRProvider


def test_rapid_ocr_initialization():
    """RapidOCR 可初始化(不实际加载模型)"""
    with patch("rapidocr_onnxruntime.RapidOCR") as mock_ocr:
        mock_ocr.return_value = MagicMock()
        provider = RapidOCRProvider()
        assert provider.name == "rapid"


def test_rapid_ocr_returns_ocr_result():
    """OCR 返回 OCRResult 结构"""
    with patch("rapidocr_onnxruntime.RapidOCR") as mock_ocr_cls:
        mock_engine = MagicMock()
        mock_engine.return_value = (
            [([[0, 0], [100, 0], [100, 30], [0, 30]], "招聘公告", 0.95)],
            None,
        )
        mock_ocr_cls.return_value = mock_engine
        provider = RapidOCRProvider()
        result = provider.ocr("/tmp/test.png")
        assert isinstance(result, OCRResult)
        assert len(result.lines) == 1
        assert result.lines[0].text == "招聘公告"
        assert result.lines[0].confidence == 0.95
        assert "招聘公告" in result.full_text


def test_rapid_ocr_empty_result():
    """无文字图片返回空结果"""
    with patch("rapidocr_onnxruntime.RapidOCR") as mock_ocr_cls:
        mock_engine = MagicMock()
        mock_engine.return_value = (None, None)
        mock_ocr_cls.return_value = mock_engine
        provider = RapidOCRProvider()
        result = provider.ocr("/tmp/blank.png")
        assert isinstance(result, OCRResult)
        assert len(result.lines) == 0
        assert result.full_text == ""


def test_rapid_ocr_handles_exception():
    """OCR 异常不崩溃,返回空结果"""
    with patch("rapidocr_onnxruntime.RapidOCR") as mock_ocr_cls:
        mock_engine = MagicMock()
        mock_engine.side_effect = Exception("模型加载失败")
        mock_ocr_cls.return_value = mock_engine
        provider = RapidOCRProvider()
        result = provider.ocr("/tmp/test.png")
        assert len(result.lines) == 0


def test_rapid_ocr_box_conversion():
    """box 格式 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] 转换为 [x,y,w,h]"""
    with patch("rapidocr_onnxruntime.RapidOCR") as mock_ocr_cls:
        mock_engine = MagicMock()
        # 四点框,顺序为左上/右上/右下/左下
        mock_engine.return_value = (
            [([[10, 20], [110, 20], [110, 50], [10, 50]], "岗位", 0.88)],
            None,
        )
        mock_ocr_cls.return_value = mock_engine
        provider = RapidOCRProvider()
        result = provider.ocr("/tmp/test.png")
        box = result.lines[0].box
        # x=min(x), y=min(y), w=max(x)-min(x), h=max(y)-min(y)
        assert box == [10, 20, 100, 30]
