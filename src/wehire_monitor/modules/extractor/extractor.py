"""Extractor — 统一多模态模型提取(SRS §4.4)

单路提取逻辑(v0.3 多模态统一):
  短图拼接(stitcher) → 长图切片(slicer) → 多模态模型提取
  预算耗尽 → need_review
  一个模型同时处理文本和图片,不再需要 OCR 中间步骤
"""
from __future__ import annotations

from loguru import logger

from wehire_monitor.domain.models import (
    ImageSlice,
    ParsedArticle,
    PrefilterResult,
    ExtractionResult,
    SliceMeta,
)
from wehire_monitor.modules.extractor.postprocess import postprocess_jobs
from wehire_monitor.modules.extractor.slicer import (
    should_slice_image,
    LongImageSlicer,
    LONG_IMAGE_THRESHOLD,
)
from wehire_monitor.modules.extractor.stitcher import ImageStitcher, StitchedImages
from wehire_monitor.modules.extractor.vlm_merge import merge_slice_jobs


class Extractor:
    """统一多模态模型提取器"""

    def __init__(
        self,
        multimodal_provider,
        slicer: LongImageSlicer | None = None,
        stitcher: ImageStitcher | None = None,
        budget_manager=None,
    ):
        self.provider = multimodal_provider
        self.slicer = slicer
        self.stitcher = stitcher
        self.budget_manager = budget_manager

    def extract(
        self, article: ParsedArticle, prefilter: PrefilterResult
    ) -> ExtractionResult:
        """执行统一多模态提取,返回 ExtractionResult"""
        # 1. 预算前置检查
        if self.budget_manager is not None and self.budget_manager.is_exhausted():
            logger.warning("模型预算耗尽,标记 need_review")
            return ExtractionResult(
                article_type="unknown",
                jobs=[],
                warnings=["need_review: model budget exhausted"],
                model_calls=0,
            )

        # 2. 短图拼接(微信长图被拆成多张短图时拼回)
        stitched_images = self._stitch_article_images(article)
        has_images = bool(stitched_images)
        logger.info(
            f"文章 {article.article_id[:8]}: "
            f"正文{len(article.plain_text)}字, "
            f"拼接后{len(stitched_images)}张图"
        )

        # 3. 长图切片(超长图超过模型分辨率限制时切片)
        all_slices: list[ImageSlice] = []
        for idx, st in enumerate(stitched_images):
            if not st.local_path:
                continue
            if self.slicer is not None and should_slice_image(st.local_path, 0):
                logger.info(
                    f"拼接图 {idx} 触发切片(长图{st.height}px)"
                )
                slices = self.slicer.slice_image(st.local_path, image_index=idx)
            else:
                slices = [self._make_single_slice_from_stitched(st, idx)]
            all_slices.extend(slices)

        # 4. 预算中途检查(切片后、调用前)
        if self.budget_manager is not None and self.budget_manager.is_exhausted():
            logger.warning("模型预算耗尽(切片后),标记 need_review")
            return ExtractionResult(
                article_type="unknown",
                jobs=[],
                warnings=["need_review: model budget exhausted after slicing"],
                model_calls=0,
            )

        # 5. 调用统一多模态模型
        text = article.plain_text if article.plain_text else None
        publish_time = article.publish_time or ""

        response = self.provider.extract_jobs(
            text=text,
            images=all_slices,
            title=article.title,
            publish_time=publish_time,
        )

        # 6. 消费预算
        if self.budget_manager is not None and response.success:
            self.budget_manager.consume(
                response.cost_estimate,
                slices=len(all_slices),
                api_calls=response.model_calls,
            )

        if not response.success:
            logger.error(f"多模态模型提取失败: {response.error}")
            return ExtractionResult(
                article_type="unknown",
                jobs=[],
                warnings=[f"model error: {response.error}"],
                model_calls=response.model_calls,
                cost_estimate=response.cost_estimate,
            )

        # 7. 合并切片结果(多切片时去重)
        if len(all_slices) > 1:
            merged_jobs = merge_slice_jobs([response.jobs])
        else:
            merged_jobs = response.jobs

        # 8. 后处理校验
        fallback_date = publish_time[:10] if publish_time else ""
        jobs = postprocess_jobs(
            merged_jobs, fallback_date, article_text=article.plain_text
        )

        return ExtractionResult(
            article_type=response.article_type,
            jobs=jobs,
            warnings=list(response.warnings),
            model_calls=response.model_calls,
            cost_estimate=response.cost_estimate,
        )

    def _stitch_article_images(self, article: ParsedArticle) -> list[StitchedImages]:
        """对文章图片执行短图拼接,返回拼接后的图片列表。
        无 stitcher 时将原图包装为单图 StitchedImages(兼容下游)。
        """
        if self.stitcher is None:
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

    @staticmethod
    def _make_single_slice_from_stitched(
        st: StitchedImages, image_index: int
    ) -> ImageSlice:
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
