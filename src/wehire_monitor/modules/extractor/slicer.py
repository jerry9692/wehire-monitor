"""长图识别与切片(PRD §8.3.2)

微信公众号文章中的长招聘海报(高度 >3000px)在 OCR 后文本稀少(<300字)时,
需要按固定高度切片后分别送 VLM 提取,避免单次请求超出模型输入限制或漏检。

切片策略:
  - 单图高度 >3000px 且 OCR 文本 <300字 → 触发切片
  - 最大切片高度 1800px
  - 重叠 220px(避免文字被切断)
  - 原宽 >1440px 等比缩放到 1440px
  - 每篇最多 8 切片
  - 底部切片标记 is_bottom=True
"""
from __future__ import annotations

import math
from pathlib import Path

from loguru import logger
from PIL import Image

from wehire_monitor.domain.models import ImageSlice, SliceMeta

# 切片参数(PRD §8.3.2)
SLICE_HEIGHT = 1800          # 最大切片高度
SLICE_OVERLAP = 220          # 切片重叠像素
MAX_WIDTH = 1440             # 最大宽度(超过则等比缩放)
MAX_SLICES = 8               # 每张图最多切片数
LONG_IMAGE_THRESHOLD = 3000  # 长图高度阈值
LOW_OCR_TEXT_THRESHOLD = 300  # 低 OCR 文本阈值


def should_slice_image(image_path: str, ocr_text_len: int) -> bool:
    """触发条件: 单图高度 >3000px 且 OCR 文本 <300字

    Args:
        image_path: 图片本地路径
        ocr_text_len: 该图片的 OCR 文本字符数

    Returns:
        是否需要切片
    """
    try:
        with Image.open(image_path) as img:
            height = img.height
    except Exception as exc:
        logger.warning(f"无法读取图片尺寸({image_path}): {exc}")
        return False
    return height > LONG_IMAGE_THRESHOLD and ocr_text_len < LOW_OCR_TEXT_THRESHOLD


class LongImageSlicer:
    """长图切片器

    Args:
        data_dir: 数据目录,切片结果保存到 ``data_dir/slices/``。
        slice_height: 最大切片高度(默认 1800)。
        overlap: 切片重叠像素(默认 220)。
        max_width: 最大宽度,超过则等比缩放(默认 1440)。
        max_slices: 每张图最多切片数(默认 8)。
    """

    def __init__(
        self,
        data_dir: str,
        slice_height: int = SLICE_HEIGHT,
        overlap: int = SLICE_OVERLAP,
        max_width: int = MAX_WIDTH,
        max_slices: int = MAX_SLICES,
    ):
        self.data_dir = Path(data_dir)
        self.slice_height = slice_height
        self.overlap = overlap
        self.max_width = max_width
        self.max_slices = max_slices
        self._slices_dir = self.data_dir / "slices"
        self._slices_dir.mkdir(parents=True, exist_ok=True)

    def slice_image(
        self,
        image_path: str,
        image_index: int = 0,
    ) -> list[ImageSlice]:
        """对单张图片进行切片,返回按 y_start 升序的切片列表

        Args:
            image_path: 图片本地路径
            image_index: 原图索引(第几张图),用于命名和 meta

        Returns:
            ImageSlice 列表,按 y_start 升序排列
        """
        # 1. 打开图片,获取尺寸
        img = Image.open(image_path).convert("RGB")
        width, height = img.size
        logger.info(f"切片原图: {width}x{height} (index={image_index})")

        # 2. 宽度缩放(>max_width 等比缩放)
        if width > self.max_width:
            new_height = int(round(height * self.max_width / width))
            new_height = max(new_height, 1)
            img = img.resize(
                (self.max_width, new_height), Image.Resampling.LANCZOS
            )
            width, height = self.max_width, new_height
            logger.info(f"宽度缩放至 {self.max_width}px: {width}x{height}")

        # 3. 计算切片数: step = slice_height - overlap
        step = self.slice_height - self.overlap
        if step <= 0:
            step = self.slice_height
        num = math.ceil(height / step) if step > 0 else 1
        # 截断到 max_slices
        num = max(1, min(num, self.max_slices))
        logger.info(f"切片数: {num} (step={step}, height={height})")

        # 4. 逐切片裁剪、保存
        slices: list[ImageSlice] = []
        for i in range(num):
            y_start = i * step
            y_end = min(y_start + self.slice_height, height)
            # 5. 最后一片 y_end=height, is_bottom=True
            if i == num - 1:
                y_end = height
            is_bottom = (i == num - 1)

            crop = img.crop((0, y_start, width, y_end))

            out_name = f"slice_{image_index}_{i}.jpg"
            out_path = self._slices_dir / out_name
            crop.save(out_path, "JPEG", quality=95)

            meta = SliceMeta(
                image_index=image_index,
                slice_index=i,
                y_start=y_start,
                y_end=y_end,
                is_bottom=is_bottom,
            )
            slices.append(ImageSlice(
                pil_image=crop,
                local_path=str(out_path),
                meta=meta,
            ))
            logger.debug(
                f"切片 {i}: y={y_start}-{y_end}, "
                f"is_bottom={is_bottom}, path={out_path}"
            )

        # 6. 返回 ImageSlice 列表(已按 y_start 升序)
        return slices
