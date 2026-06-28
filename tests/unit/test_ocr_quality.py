"""OCR 质量评分测试"""
from wehire_monitor.modules.extractor.ocr_quality import calculate_ocr_quality
from wehire_monitor.domain.models import OCRResult, OCRLine


def test_high_quality_ocr():
    """高质量 OCR:高置信度+有效字符+邮箱命中"""
    lines = [
        OCRLine(text="德邦证券2026年社会招聘", confidence=0.95, box=[0, 0, 300, 30]),
        OCRLine(text="投递邮箱: hr@example.com", confidence=0.92, box=[0, 40, 300, 30]),
        OCRLine(text="截止日期: 2026-07-31", confidence=0.90, box=[0, 80, 300, 30]),
    ]
    result = OCRResult(
        lines=lines,
        full_text="德邦证券2026年社会招聘\n投递邮箱: hr@example.com\n截止日期: 2026-07-31",
    )
    score = calculate_ocr_quality(result)
    assert score >= 0.72  # 高质量


def test_low_quality_ocr():
    """低质量 OCR:低置信度+乱码"""
    lines = [
        OCRLine(text="???招聘??", confidence=0.3, box=[0, 0, 100, 30]),
        OCRLine(text="??邮箱??", confidence=0.25, box=[0, 40, 100, 30]),
    ]
    result = OCRResult(lines=lines, full_text="???招聘??\n??邮箱??")
    score = calculate_ocr_quality(result)
    assert score < 0.45  # 低质量


def test_medium_quality_ocr():
    """中等质量:0.45-0.72"""
    lines = [
        OCRLine(text="某公司招聘公告", confidence=0.6, box=[0, 0, 200, 30]),
        OCRLine(text="岗位 数据分析", confidence=0.55, box=[0, 40, 200, 30]),
    ]
    result = OCRResult(lines=lines, full_text="某公司招聘公告\n岗位 数据分析")
    score = calculate_ocr_quality(result)
    assert 0.45 <= score < 0.72


def test_empty_ocr():
    """空 OCR 结果评分为 0"""
    result = OCRResult(lines=[], full_text="")
    score = calculate_ocr_quality(result)
    assert score == 0.0
