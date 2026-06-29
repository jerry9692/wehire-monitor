"""Extractor — AI 混合提取(SRS §4.4)

三路切换逻辑(v0.3):
  正文>=500字 且含岗位/投递信息 → 文本 LLM (路径1)
  有图片 → OCR → 质量评分:
    score>=0.72 → OCR文本+原文 → 文本 LLM (路径2a)
    0.45<=score<0.72 → VLM 复核 (路径2b,低成本,传整图给VLM)
    score<0.45 或长图 → VLM 切片提取 (路径2c)
  预算耗尽 → need_review
  正文短且无图 → 兜底文本 LLM
"""
from __future__ import annotations

from loguru import logger

from wehire_monitor.domain.models import (
    ImageSlice,
    OCRResult,
    ParsedArticle,
    PrefilterResult,
    ExtractionResult,
    SliceMeta,
)
from wehire_monitor.modules.extractor.ocr_quality import calculate_ocr_quality
from wehire_monitor.modules.extractor.postprocess import postprocess_jobs
from wehire_monitor.modules.extractor.slicer import (
    should_slice_image,
    LongImageSlicer,
    LONG_IMAGE_THRESHOLD,
)
from wehire_monitor.modules.extractor.vlm_merge import merge_slice_jobs

# 触发文本 LLM 的关键词
_JOB_TRIGGER_WORDS = {"招聘", "岗位", "职位", "投递", "简历", "报名", "应聘", "录用"}
_TEXT_LLM_THRESHOLD = 500  # 正文字数阈值

# OCR 质量评分阈值
_OCR_QUALITY_HIGH = 0.72   # >= 走 OCR文本+原文
_OCR_QUALITY_LOW = 0.45    # < 走 VLM 切片提取


class Extractor:
    """AI 混合提取器"""

    def __init__(
        self,
        llm_provider,
        ocr_provider=None,
        vlm_provider=None,
        slicer=None,
        budget_manager=None,
    ):
        self.llm = llm_provider
        self.ocr = ocr_provider
        self.vlm = vlm_provider
        self.slicer = slicer
        self.budget_manager = budget_manager

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

        # 使用 article.publish_time(ParsedArticle 新增字段)
        publish_time = article.publish_time or ""

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

        # 后处理校验(publish_time 为空时用当前日期兜底)
        fallback_date = publish_time[:10] if publish_time else ""
        jobs = postprocess_jobs(response.jobs, fallback_date, article_text=combined_text)

        return ExtractionResult(
            article_type=response.article_type,
            jobs=jobs,
            warnings=list(response.warnings),
            llm_calls=1,
            ocr_calls=ocr_calls,
        )

    def _extract_with_ocr(self, article: ParsedArticle) -> ExtractionResult:
        """OCR → 质量评分 → LLM(多图 y 坐标加偏移避免顺序评分错误)"""
        all_lines = []
        ocr_texts: list[str] = []
        ocr_calls = 0
        y_offset = 0  # 多图累加 y 偏移,保证全局 y 单调递增

        for img in article.images:
            if not img.local_path:
                continue
            result = self.ocr.ocr(img.local_path)
            ocr_calls += 1
            if result.lines:
                # 给当前图片的每行 y 坐标加上偏移
                for line in result.lines:
                    adjusted_box = [
                        line.box[0],
                        line.box[1] + y_offset,
                        line.box[2],
                        line.box[3],
                    ]
                    # 重建 OCRLine(y 偏移后)
                    from wehire_monitor.domain.models import OCRLine
                    all_lines.append(OCRLine(
                        text=line.text,
                        confidence=line.confidence,
                        box=adjusted_box,
                    ))
                # 更新偏移:取当前图所有行的最大 y + box 高度
                max_y = max(
                    (line.box[1] + line.box[3] for line in result.lines),
                    default=y_offset,
                )
                y_offset = max_y + 10  # 留 10px 间距
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

        # 检测长图(任一图片高度 > 3000px)
        has_long_image = any(
            img.height > LONG_IMAGE_THRESHOLD
            for img in article.images
            if img.local_path
        )

        # 路径2c: score<0.45 或长图 → VLM 切片提取(force_slice=True)
        if quality < _OCR_QUALITY_LOW or has_long_image:
            if has_long_image:
                logger.info(
                    f"检测到长图(高度>{LONG_IMAGE_THRESHOLD}px),走 VLM 切片提取"
                )
            else:
                logger.warning(
                    f"OCR 质量过低({quality:.2f}),走 VLM 切片提取"
                )
            return self._extract_with_vlm(
                article, ocr_calls=ocr_calls, ocr_text=combined_ocr,
                force_slice=True,
            )

        # 路径2b: 0.45<=score<0.72 → VLM 复核(force_slice=False)
        if quality < _OCR_QUALITY_HIGH:
            logger.info(f"OCR 质量中等({quality:.2f}),走 VLM 复核")
            return self._extract_with_vlm(
                article, ocr_calls=ocr_calls, ocr_text=combined_ocr,
                force_slice=False,
            )

        # 路径2a: score>=0.72 → OCR文本+原文 → 文本 LLM
        return self._extract_with_llm(
            article, ocr_text=combined_ocr, ocr_calls=ocr_calls
        )

    def _extract_with_vlm(
        self,
        article: ParsedArticle,
        ocr_calls: int,
        ocr_text: str,
        force_slice: bool = False,
    ) -> ExtractionResult:
        """VLM 提取(切片或复核)

        Args:
            article: 解析后的文章
            ocr_calls: 本次提取已发生的 OCR 调用次数(透传到结果)
            ocr_text: OCR 拼接文本(供后处理使用)
            force_slice: True=切片提取(路径2c), False=复核模式(路径2b)
        """
        # 检查 VLM provider 是否存在
        if self.vlm is None:
            if force_slice:
                # 路径2c: 低质量且无 VLM → need_review
                logger.warning("无 VLM provider 且 OCR 质量低,标记 need_review")
                return ExtractionResult(
                    article_type="unknown",
                    jobs=[],
                    warnings=[
                        "need_review: no VLM provider for low quality OCR"
                    ],
                    ocr_calls=ocr_calls,
                )
            # 路径2b: 中等质量且无 VLM → 回退文本 LLM
            logger.warning("无 VLM provider,回退文本 LLM")
            return self._extract_with_llm(
                article, ocr_text=ocr_text, ocr_calls=ocr_calls
            )

        # 检查预算是否耗尽
        if self.budget_manager is not None and self.budget_manager.is_exhausted():
            logger.warning("VLM 预算耗尽,标记 need_review")
            return ExtractionResult(
                article_type="unknown",
                jobs=[],
                warnings=["need_review: VLM budget exhausted"],
                ocr_calls=ocr_calls,
            )

        all_slice_jobs: list[list] = []
        vlm_calls = 0
        article_type = "unknown"
        warnings: list[str] = []
        publish_time = article.publish_time or ""

        for img in article.images:
            if not img.local_path:
                continue

            # 确定切片列表
            slices: list[ImageSlice] = []
            if force_slice and self.slicer is not None:
                ocr_text_len = len(ocr_text)
                if should_slice_image(img.local_path, ocr_text_len):
                    logger.info(f"图片 {img.index} 触发切片(长图)")
                    slices = self.slicer.slice_image(
                        img.local_path, image_index=img.index
                    )
                else:
                    slices = [self._make_single_slice(img)]
            else:
                # 复核模式或无 slicer: 直接用原图
                slices = [self._make_single_slice(img)]

            if not slices:
                continue

            # 逐切片调用 VLM
            response = self.vlm.extract_jobs_from_slices(
                slices=slices,
                title=article.title,
                publish_time=publish_time,
            )
            vlm_calls += 1

            # 消费预算
            if self.budget_manager is not None:
                self.budget_manager.consume(
                    response.cost_estimate, slices=len(slices)
                )

            if response.success:
                if article_type == "unknown":
                    article_type = response.article_type
                all_slice_jobs.append(response.jobs)
                warnings.extend(response.warnings)
            else:
                logger.warning(f"VLM 提取失败: {response.error}")
                all_slice_jobs.append([])
                warnings.append(f"VLM error: {response.error}")

        # 用 merge_slice_jobs 合并结果
        merged_jobs = merge_slice_jobs(all_slice_jobs)

        # 调用 postprocess_jobs 后处理
        fallback_date = publish_time[:10] if publish_time else ""
        combined_text = article.plain_text
        if ocr_text:
            combined_text = f"{article.plain_text}\n\n[图片OCR文本]\n{ocr_text}"
        jobs = postprocess_jobs(
            merged_jobs, fallback_date, article_text=combined_text
        )

        return ExtractionResult(
            article_type=article_type,
            jobs=jobs,
            warnings=warnings,
            vlm_calls=vlm_calls,
            ocr_calls=ocr_calls,
        )

    @staticmethod
    def _make_single_slice(img) -> ImageSlice:
        """将原图封装为单个 ImageSlice(不切片)"""
        return ImageSlice(
            pil_image=None,
            local_path=img.local_path,
            meta=SliceMeta(
                image_index=img.index,
                slice_index=0,
                y_start=0,
                y_end=img.height,
                is_bottom=True,
            ),
        )
