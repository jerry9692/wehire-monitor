"""Qwen-VL-Max 多模态 Provider(DashScope OpenAI 兼容接口)

从 ``providers/vlm/qwen_vl.py`` 迁移,适配统一多模态接口。
保留原有逐切片调用逻辑:每张图片单独调用一次 API,然后用 merge_slice_jobs 去重合并。

与 MiMo 的单次多图调用不同,Qwen-VL-Max 对招聘长图采用逐切片识别,
更适合控制单次请求大小与成本。文本部分则单独发起一次调用。
所有公共逻辑(重试、JSON 解析、图片编码、成本估算)复用
:class:`OpenAICompatibleProvider` 基类。
"""
from __future__ import annotations

from typing import Any

from wehire_monitor.providers.multimodal.base import MultimodalResponse
from wehire_monitor.providers.multimodal.openai_compatible import (
    OpenAICompatibleProvider,
)
from wehire_monitor.modules.extractor.vlm_merge import merge_slice_jobs


class QwenVLProvider(OpenAICompatibleProvider):
    """Qwen-VL-Max 多模态实现(逐切片调用)

    - base_url:     https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
    - model:        qwen-vl-max
    - 输入价格:      3 元/百万 token
    - 输出价格:      9 元/百万 token

    调用策略:
    - 纯文本(text 有值、images 为空): 单次文本调用
    - 纯图片(text 为空、images 非空): 每张图片单独调用一次
    - 文本 + 图片: 一次文本调用 + 每张图片单独调用
    只要任意一次调用成功即视为整体成功,合并全部 jobs / warnings / 成本。
    """

    name = "qwen_vl"
    base_url = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    model = "qwen-vl-max"
    input_price = 3.0   # 元/百万 token
    output_price = 9.0  # 元/百万 token

    # ------------------------------------------------------------------
    # 统一提取接口(逐切片调用)
    # ------------------------------------------------------------------
    def extract_jobs(
        self,
        text: str | None,
        images: list,
        title: str,
        publish_time: str,
    ) -> MultimodalResponse:
        """逐切片调用 Qwen-VL 提取岗位信息并合并结果

        Args:
            text: 文章正文文本(可为 None / 空字符串,表示纯图片文章)
            images: 图片切片列表(可为空列表,表示纯文本文章)
            title: 文章标题
            publish_time: 文章发布时间(ISO8601)

        Returns:
            :class:`MultimodalResponse` 合并后的提取结果
        """
        if not text and not images:
            return MultimodalResponse(
                success=False, error="no text and no images provided"
            )

        all_jobs: list = []
        slice_job_lists: list[list] = []  # per-slice job lists for dedup merge
        all_warnings: list[str] = []
        article_type = "unknown"
        total_cost = 0.0
        model_calls = 0
        success_count = 0
        last_error = ""

        # 1) 文本提取(如有正文)
        body_text = (text or "").strip()
        if body_text:
            resp = self._extract_from_text(body_text, title, publish_time)
            model_calls += resp.model_calls
            total_cost += resp.cost_estimate
            if resp.success:
                success_count += 1
                slice_job_lists.append(resp.jobs)
                all_warnings.extend(resp.warnings)
                if resp.article_type and resp.article_type != "unknown":
                    article_type = resp.article_type
            else:
                last_error = resp.error or "unknown error"
                all_warnings.append(f"文本提取失败: {last_error}")

        # 2) 逐图片提取(每张图片单独调用一次 API)
        for idx, img in enumerate(images):
            resp = self._extract_from_image(img, title, publish_time, idx)
            model_calls += resp.model_calls
            total_cost += resp.cost_estimate
            if resp.success:
                success_count += 1
                slice_job_lists.append(resp.jobs)
                all_warnings.extend(resp.warnings)
                if resp.article_type and resp.article_type != "unknown":
                    article_type = resp.article_type
            else:
                last_error = resp.error or "unknown error"
                all_warnings.append(f"图片 {idx} 提取失败: {last_error}")

        # 3) 跨切片去重合并
        all_jobs = merge_slice_jobs(slice_job_lists) if slice_job_lists else []

        return MultimodalResponse(
            success=success_count > 0,
            article_type=article_type,
            jobs=all_jobs,
            warnings=all_warnings,
            cost_estimate=total_cost,
            model_calls=model_calls,
            error="" if success_count > 0 else last_error,
        )

    # ------------------------------------------------------------------
    # 单路提取
    # ------------------------------------------------------------------
    def _extract_from_text(
        self,
        text: str,
        title: str,
        publish_time: str,
    ) -> MultimodalResponse:
        """对纯文本发起单次调用(复用基类消息构建)"""
        messages = self._build_messages(text, [], title, publish_time)
        return self._invoke(messages)

    def _extract_from_image(
        self,
        image: Any,
        title: str,
        publish_time: str,
        idx: int,
    ) -> MultimodalResponse:
        """对单张图片发起调用(带切片元信息)

        Args:
            image: 图片切片对象(ImageSlice / 含 local_path 与 meta 的对象)
            title: 文章标题
            publish_time: 文章发布时间
            idx: 图片在列表中的序号(用于告警定位)
        """
        data_uri = self._encode_image(image)
        if not data_uri:
            local_path = getattr(image, "local_path", "")
            return MultimodalResponse(
                success=False,
                error=f"cannot read image: {local_path}",
            )

        # 渲染提取指令 prompt(含标题与发布时间)
        prompt_text = self._render_prompt(title, publish_time)

        # 追加切片元信息(便于模型理解图片在长图中的位置)
        meta = self._slice_meta(image)
        slice_info = (
            "\n图片元信息:\n"
            f"image_index={meta['image_index']}\n"
            f"slice_index={meta['slice_index']}\n"
            f"y_range={meta['y_start']}-{meta['y_end']}\n"
            f"is_bottom={'true' if meta['is_bottom'] else 'false'}\n"
        )

        content = [
            {"type": "text", "text": prompt_text + slice_info},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
        messages = self._wrap_messages(content)
        return self._invoke(messages)

    # ------------------------------------------------------------------
    # 切片元信息提取(兼容 ImageSlice / dict / 普通对象)
    # ------------------------------------------------------------------
    @staticmethod
    def _slice_meta(slice_obj: Any) -> dict:
        """从切片对象提取元信息

        兼容三种形态:
        - ``ImageSlice``(含 ``meta: SliceMeta`` 属性)
        - ``dict``(直接含 image_index / slice_index 等键)
        - 普通对象(直接含 image_index / slice_index 等属性)
        """
        meta_obj = getattr(slice_obj, "meta", None)
        if meta_obj is not None:
            return {
                "image_index": getattr(meta_obj, "image_index", 0),
                "slice_index": getattr(meta_obj, "slice_index", 0),
                "y_start": getattr(meta_obj, "y_start", 0),
                "y_end": getattr(meta_obj, "y_end", 0),
                "is_bottom": getattr(meta_obj, "is_bottom", False),
            }
        if isinstance(slice_obj, dict):
            return {
                "image_index": slice_obj.get("image_index", 0),
                "slice_index": slice_obj.get("slice_index", 0),
                "y_start": slice_obj.get("y_start", 0),
                "y_end": slice_obj.get("y_end", 0),
                "is_bottom": slice_obj.get("is_bottom", False),
            }
        return {
            "image_index": getattr(slice_obj, "image_index", 0),
            "slice_index": getattr(slice_obj, "slice_index", 0),
            "y_start": getattr(slice_obj, "y_start", 0),
            "y_end": getattr(slice_obj, "y_end", 0),
            "is_bottom": getattr(slice_obj, "is_bottom", False),
        }
