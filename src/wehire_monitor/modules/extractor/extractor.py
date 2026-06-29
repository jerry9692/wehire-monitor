"""Extractor — AI 混合提取(SRS §4.4)

三路切换逻辑(v0.3):
  正文>=500字 且含岗位/投递信息 → 文本 LLM (路径1)
  有图片 → 先短图拼接(stitcher) → OCR → 质量评分:
    score>=0.72 → OCR文本+原文 → 文本 LLM (路径2a)
    0.45<=score<0.72 → VLM 复核 (路径2b,低成本,传整图给VLM)
    score<0.45 或长图 → VLM 切片提取 (路径2c)
  预算耗尽 → need_review
  正文短且无图 → 兜底文本 LLM
  VLM 不可用时降级到 v0.2 行为(OCR+LLM)
"""
from __future__ import annotations

from loguru import logger

from wehire_monitor.domain.models import (
    ImageSlice,
    OCRLine,
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
from wehire_monitor.modules.extractor.stitcher import ImageStitcher, StitchedImages
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
        stitcher=None,
        budget_manager=None,
    ):
        self.llm = llm_provider
        self.ocr = ocr_provider
        self.vlm = vlm_provider
        self.slicer = slicer
        self.stitcher = stitcher
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

        # 路径2: 有图片 → 拼接 → OCR → 质量评分
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

        fallback_date = publish_time[:10] if publish_time else ""
        jobs = postprocess_jobs(response.jobs, fallback_date, article_text=combined_text)

        return ExtractionResult(
            article_type=response.article_type,
            jobs=jobs,
            warnings=list(response.warnings),
            llm_calls=1,
            ocr_calls=ocr_calls,
        )

    def _stitch_article_images(self, article: ParsedArticle) -> list[StitchedImages]:
        """对文章图片执行短图拼接,返回拼接后的图片列表。
        无 stitcher 时将原图包装为单图 StitchedImages(兼容下游)。
        """
        if self.stitcher is None:
            # 无 stitcher:逐图包装为单图
            results = []
            for img in article.images:
                if img.local_path:
                    results.append(StitchedImages(
                        local_path=img.local_path,
                        source_indices=[img.index],
                        width=img.width,
                        height=img.height,
                        is_stitched=False,
                        overlap_trimmed=0,
                    ))
            return results
        return self.stitcher.process_article_images(article.images)

    def _extract_with_ocr(self, article: ParsedArticle) -> ExtractionResult:
        """OCR → 拼接(stitcher)→ 质量评分 → LLM/VLM(多图 y 坐标加偏移避免顺序评分错误)

        v0.3 改进:
        - 先执行短图拼接(stitcher),再对拼接后的图片做 OCR 和 VLM
        - 逐图记录 per-image OCR 文本长度,正确判断长图切片
        - y_offset 正确累加(修复 v0.3 audit bug)
        """
        # 1. 先执行短图拼接
        stitched_images = self._stitch_article_images(article)
        if not stitched_images:
            logger.warning("图片拼接后无有效图片,回退文本 LLM")
            return self._extract_with_llm(article)

        # 2. 对拼接后的图片逐张 OCR
        all_lines = []
        ocr_texts: list[str] = []
        per_image_ocr_len: list[int] = []  # 每张(拼接后)图片的 OCR 文本长度
        ocr_calls = 0
        y_offset = 0

        for st in stitched_images:
            if not st.local_path:
                per_image_ocr_len.append(0)
                continue
            result = self.ocr.ocr(st.local_path)
            ocr_calls += 1
            img_text_len = len(result.full_text or "")
            per_image_ocr_len.append(img_text_len)
            if result.lines:
                for line in result.lines:
                    adjusted_box = [
                        line.box[0],
                        line.box[1] + y_offset,
                        line.box[2],
                        line.box[3],
                    ]
                    all_lines.append(OCRLine(
                        text=line.text,
                        confidence=line.confidence,
                        box=adjusted_box,
                    ))
                # 修复:y_offset 应在全局坐标系上累加(取已加偏移的行)
                max_y = max(
                    (line.box[1] + y_offset + line.box[3] for line in result.lines),
                    default=y_offset,
                )
                y_offset = max_y + 10
            if result.full_text:
                ocr_texts.append(result.full_text)

        combined_ocr = "\n".join(ocr_texts)

        if not combined_ocr:
            logger.warning("OCR 无文本,回退文本 LLM")
            return self._extract_with_llm(article, ocr_calls=ocr_calls)

        ocr_result = OCRResult(lines=all_lines, full_text=combined_ocr)
        quality = calculate_ocr_quality(ocr_result)
        logger.info(f"OCR 质量评分: {quality:.2f}")

        # 检测长图(拼接后任一图片高度 > 3000px)
        has_long_image = any(
            st.height > LONG_IMAGE_THRESHOLD for st in stitched_images if st.local_path
        )

        # 路径2c: score<0.45 或长图 → VLM 切片提取(force_slice=True)
        if quality < _OCR_QUALITY_LOW or has_long_image:
            if self.vlm is None:
                # 无 VLM 兜底: OCR 质量过低且无 VLM,标记 need_review
                logger.warning(
                    f"OCR 质量过低({quality:.2f})且无 VLM provider,标记 need_review"
                )
                return ExtractionResult(
                    article_type="unknown",
                    jobs=[],
                    warnings=["need_review: OCR quality too low and no VLM available"],
                    ocr_calls=ocr_calls,
                )
            if has_long_image:
                logger.info(
                    f"检测到长图(高度>{LONG_IMAGE_THRESHOLD}px),走 VLM 切片提取"
                )
            else:
                logger.warning(
                    f"OCR 质量过低({quality:.2f}),走 VLM 切片提取"
                )
            return self._extract_with_vlm(
                article, stitched_images=stitched_images,
                ocr_calls=ocr_calls, ocr_text=combined_ocr,
                per_image_ocr_len=per_image_ocr_len,
                force_slice=True,
            )

        # 路径2b: 0.45<=score<0.72 → VLM 复核(force_slice=False)
        if quality < _OCR_QUALITY_HIGH:
            if self.vlm is None:
                # 无 VLM: 中等质量 OCR 降级到 OCR+LLM 路径(质量尚可接受)
                logger.info(
                    f"OCR 质量中等({quality:.2f})无 VLM,降级到 OCR+LLM 路径"
                )
                return self._extract_with_llm(
                    article, ocr_text=combined_ocr, ocr_calls=ocr_calls
                )
            logger.info(f"OCR 质量中等({quality:.2f}),走 VLM 复核")
            return self._extract_with_vlm(
                article, stitched_images=stitched_images,
                ocr_calls=ocr_calls, ocr_text=combined_ocr,
                per_image_ocr_len=per_image_ocr_len,
                force_slice=False,
            )

        # 路径2a: score>=0.72 → OCR文本+原文 → 文本 LLM
        return self._extract_with_llm(
            article, ocr_text=combined_ocr, ocr_calls=ocr_calls
        )

    def _extract_with_vlm(
        self,
        article: ParsedArticle,
        stitched_images: list[StitchedImages],
        ocr_calls: int,
        ocr_text: str,
        per_image_ocr_len: list[int],
        force_slice: bool = False,
    ) -> ExtractionResult:
        """VLM 提取(切片或复核)

        Args:
            article: 解析后的文章
            stitched_images: 拼接后的图片列表(v0.3 新增,替代 article.images)
            ocr_calls: 本次提取已发生的 OCR 调用次数
            ocr_text: OCR 拼接文本
            per_image_ocr_len: 每张拼接后图片的 OCR 文本长度
            force_slice: True=切片提取(路径2c), False=复核模式(路径2b)
        """
        # 检查 VLM provider 是否存在
        if self.vlm is None:
            # v0.3 修复:无 VLM 时统一回退到 OCR+LLM(v0.2 行为),不直接 need_review
            logger.warning("无 VLM provider,回退到 OCR+LLM 路径")
            return self._extract_with_llm(
                article, ocr_text=ocr_text, ocr_calls=ocr_calls
            )

        # 检查预算是否耗尽(前置检查)
        if self.budget_manager is not None and self.budget_manager.is_exhausted():
            logger.warning("VLM 预算耗尽,标记 need_review")
            return ExtractionResult(
                article_type="unknown",
                jobs=[],
                warnings=["need_review: VLM budget exhausted"],
                ocr_calls=ocr_calls,
            )

        all_slice_jobs: list[list] = []
        vlm_api_calls = 0  # 实际 API 调用次数(=切片总数)
        article_type = "unknown"
        warnings: list[str] = []
        publish_time = article.publish_time or ""
        total_cost = 0.0

        for idx, st in enumerate(stitched_images):
            if not st.local_path:
                continue

            # 循环内预算检查(防止超支)
            if self.budget_manager is not None and self.budget_manager.is_exhausted():
                logger.warning(f"VLM 预算耗尽(处理到第 {idx} 张图),剩余文章标记 need_review")
                warnings.append("need_review: VLM budget exhausted mid-article")
                break

            # 确定切片列表
            slices: list[ImageSlice] = []
            if force_slice and self.slicer is not None:
                # 使用该拼接图自身的 OCR 长度(而非总长度)
                img_ocr_len = per_image_ocr_len[idx] if idx < len(per_image_ocr_len) else 0
                if should_slice_image(st.local_path, img_ocr_len):
                    logger.info(
                        f"拼接图 {idx} 触发切片(长图{st.height}px,OCR {img_ocr_len}字)"
                    )
                    slices = self.slicer.slice_image(
                        st.local_path, image_index=idx
                    )
                else:
                    slices = [self._make_single_slice_from_stitched(st, idx)]
            else:
                slices = [self._make_single_slice_from_stitched(st, idx)]

            if not slices:
                continue

            # 单张图片切片前检查是否付得起
            est_cost = len(slices) * 0.03
            if self.budget_manager is not None and not self.budget_manager.can_afford(est_cost):
                logger.warning(
                    f"VLM 预算不足(需{est_cost:.2f}元,剩{self.budget_manager.remaining:.2f}元),"
                    f"标记 need_review"
                )
                warnings.append("need_review: VLM budget insufficient for remaining slices")
                break

            # 逐切片调用 VLM(一次调用处理一张图的所有切片)
            response = self.vlm.extract_jobs_from_slices(
                slices=slices,
                title=article.title,
                publish_time=publish_time,
            )
            vlm_api_calls += len(slices)  # 每个切片一次 API 调用
            total_cost += response.cost_estimate

            # 消费预算(当前 qwen_vl 是逐切片调用 API,所以 api_calls=len(slices))
            if self.budget_manager is not None:
                self.budget_manager.consume(
                    response.cost_estimate, slices=len(slices),
                    api_calls=len(slices),
                )

            if response.success:
                if article_type == "unknown" and response.article_type != "unknown":
                    article_type = response.article_type
                all_slice_jobs.append(response.jobs)
                warnings.extend(response.warnings)
            else:
                logger.warning(f"VLM 提取失败(图{idx}): {response.error}")
                all_slice_jobs.append([])
                warnings.append(f"VLM error (image {idx}): {response.error}")

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
            vlm_calls=vlm_api_calls,
            ocr_calls=ocr_calls,
            cost_estimate=total_cost,
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

    @staticmethod
    def _make_single_slice_from_stitched(st: StitchedImages, image_index: int) -> ImageSlice:
        """将拼接后的图片封装为单个 ImageSlice(不切片)"""
        return ImageSlice(
            pil_image=None,
            local_path=st.local_path,
            meta=SliceMeta(
                image_index=image_index,
                slice_index=0,
                y_start=0,
                y_end=st.height,
                is_bottom=True,
            ),
        )
