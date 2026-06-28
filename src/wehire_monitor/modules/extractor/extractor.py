"""Extractor — AI 混合提取(SRS §4.4)

双路切换逻辑:
  正文>=500字 且含岗位/投递信息 → 文本 LLM
  否则有图片 → OCR → 质量评分(调用 calculate_ocr_quality)
    score>=0.72 → OCR文本+原文 → 文本 LLM
    0.45<=score<0.72 → 文本 LLM(v0.2 暂走文本,v0.3 走 VLM)
    score<0.45 → need_review
  正文短且无图 → 兜底文本 LLM
"""
from __future__ import annotations

from loguru import logger

from wehire_monitor.domain.models import (
    OCRResult,
    ParsedArticle,
    PrefilterResult,
    ExtractionResult,
)
from wehire_monitor.modules.extractor.ocr_quality import calculate_ocr_quality
from wehire_monitor.modules.extractor.postprocess import postprocess_jobs

# 触发文本 LLM 的关键词
_JOB_TRIGGER_WORDS = {"招聘", "岗位", "职位", "投递", "简历", "报名", "应聘", "录用"}
_TEXT_LLM_THRESHOLD = 500  # 正文字数阈值

# OCR 质量评分阈值
_OCR_QUALITY_HIGH = 0.72   # >= 走 OCR文本+原文
_OCR_QUALITY_LOW = 0.45    # < 标记 need_review


class Extractor:
    """AI 混合提取器"""

    def __init__(self, llm_provider, ocr_provider=None):
        self.llm = llm_provider
        self.ocr = ocr_provider

    def extract(
        self, article: ParsedArticle, prefilter: PrefilterResult
    ) -> ExtractionResult:
        """执行提取,返回 ExtractionResult"""
        text = article.plain_text
        has_images = bool(article.images)
        has_job_keywords = any(kw in text for kw in _JOB_TRIGGER_WORDS)
        text_long_enough = len(text) >= _TEXT_LLM_THRESHOLD

        # 路径1: 正文足够长且含岗位/投递信息 → 直接文本 LLM
        if text_long_enough and has_job_keywords:
            logger.info(f"文章 {article.article_id[:8]}: 文本 LLM 路径(正文{len(text)}字)")
            return self._extract_with_llm(article)

        # 路径2: 有图片 → OCR → 质量评分
        if has_images and self.ocr is not None:
            logger.info(f"文章 {article.article_id[:8]}: OCR 路径({len(article.images)}张图)")
            return self._extract_with_ocr(article)

        # 路径3: 正文短且无图 → 仍尝试文本 LLM(可能信息密度高)
        logger.info(f"文章 {article.article_id[:8]}: 文本 LLM 路径(兜底,{len(text)}字)")
        return self._extract_with_llm(article)

    def _extract_with_llm(
        self,
        article: ParsedArticle,
        ocr_text: str = "",
        ocr_calls: int = 0,
    ) -> ExtractionResult:
        """调用文本 LLM 提取

        Args:
            article: 解析后的文章
            ocr_text: 由 OCR 路径拼接得到的图片文本(可选)
            ocr_calls: 本次提取已发生的 OCR 调用次数(透传到结果)
        """
        combined_text = article.plain_text
        if ocr_text:
            combined_text = f"{article.plain_text}\n\n[图片OCR文本]\n{ocr_text}"

        # 截取发布时间的日期部分
        publish_time = ""
        if hasattr(article, "publish_time") and article.publish_time:
            publish_time = str(article.publish_time)[:10]

        response = self.llm.extract_jobs(
            text=combined_text,
            title=article.title,
            publish_time=publish_time,
        )

        if not response.success:
            logger.error(f"LLM 提取失败: {response.error}")
            return ExtractionResult(
                article_type="unknown",
                jobs=[],
                warnings=[f"LLM error: {response.error}"],
                llm_calls=1,
                ocr_calls=ocr_calls,
            )

        # 后处理校验
        jobs = postprocess_jobs(response.jobs, publish_time or "2026-01-01")

        return ExtractionResult(
            article_type=response.article_type,
            jobs=jobs,
            warnings=list(response.warnings),
            llm_calls=1,
            ocr_calls=ocr_calls,
        )

    def _extract_with_ocr(self, article: ParsedArticle) -> ExtractionResult:
        """OCR → 质量评分 → LLM"""
        all_lines = []
        ocr_texts: list[str] = []
        ocr_calls = 0

        for img in article.images:
            if not img.local_path:
                continue
            result = self.ocr.ocr(img.local_path)
            ocr_calls += 1
            if result.lines:
                all_lines.extend(result.lines)
            if result.full_text:
                ocr_texts.append(result.full_text)

        combined_ocr = "\n".join(ocr_texts)

        if not combined_ocr:
            # OCR 无结果,仍尝试文本 LLM
            logger.warning("OCR 无文本,回退文本 LLM")
            return self._extract_with_llm(article, ocr_calls=ocr_calls)

        # 构造 OCRResult 对象供质量评分
        ocr_result = OCRResult(lines=all_lines, full_text=combined_ocr)
        quality = calculate_ocr_quality(ocr_result)
        logger.info(f"OCR 质量评分: {quality:.2f}")

        if quality < _OCR_QUALITY_LOW:
            logger.warning(f"OCR 质量过低({quality:.2f}),标记 need_review")
            return ExtractionResult(
                article_type="unknown",
                jobs=[],
                warnings=[f"need_review: OCR quality {quality:.2f} < {_OCR_QUALITY_LOW}"],
                ocr_calls=ocr_calls,
            )

        # 质量达标(>=0.45): OCR文本 + 原文 → 文本 LLM
        # v0.2 中 0.45<=score<0.72 与 score>=0.72 均暂走文本 LLM
        return self._extract_with_llm(
            article, ocr_text=combined_ocr, ocr_calls=ocr_calls
        )
