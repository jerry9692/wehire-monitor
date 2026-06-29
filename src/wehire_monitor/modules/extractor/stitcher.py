"""短图拼接为长图(PRD §8.3)

微信公众号文章中,发布者常将一张很长的招聘海报裁剪为多张短图片依次发布。
这些图片需要按顺序拼接才能看到完整内容,部分文字甚至会在裁剪边界处被截断。

本模块负责:
  1. 检测哪些连续短图应该拼接(should_stitch)
  2. 竖直拼接多张短图为一张长图(ImageStitcher)
  3. 感知哈希去重(imagehash, 汉明距离<5 视为重复)
  4. SVG 图片跳过(检测 .svg 后缀,跳过并记录警告)
  5. 拼接边界重叠检测与裁剪(底部/顶部条带像素匹配)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PIL import Image

from wehire_monitor.domain.models import ImageAsset

try:
    import imagehash
    _HAS_IMAGEHASH = True
except ImportError:  # pragma: no cover
    _HAS_IMAGEHASH = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False


# 短图高度阈值(>= 此值视为长图,不参与拼接)
_SHORT_IMAGE_MAX_HEIGHT = 3000
# 宽度差异比例阈值(< 10% 视为宽度相近)
_WIDTH_DIFF_THRESHOLD = 0.10
# 去重汉明距离阈值(< 5 视为重复)
_DEDUP_HAMMING_THRESHOLD = 5
# 重叠检测参数
_OVERLAP_MAX = 300          # 最大检测重叠像素
_OVERLAP_MIN = 20           # 最小有效重叠像素
_OVERLAP_MAD_THRESHOLD = 15.0   # 平均绝对差阈值(0-255),低于此值视为重叠
_OVERLAP_STD_GUARD = 10.0       # 方差守卫:低于此值视为纯色区域,跳过重叠检测


def should_stitch(images: list[ImageAsset]) -> list[list[int]]:
    """检测哪些图片应该拼接在一起。

    判断条件(连续两张同时满足才归入同一组):
      - 宽度差异 < 10%
      - 高度均 < 3000px(短图特征)

    Args:
        images: 图片资产列表(按文章出现顺序)。

    Returns:
        分组列表,每组是该组图片在 ``images`` 中的索引列表。
        例如 ``[[0, 1], [2], [3, 4]]`` 表示图 0/1 拼接,图 2 单独,图 3/4 拼接。
    """
    if not images:
        return []

    groups: list[list[int]] = []
    current: list[int] = [0]

    for i in range(1, len(images)):
        prev = images[i - 1]
        curr = images[i]
        max_w = max(prev.width, curr.width, 1)
        width_diff_ratio = abs(prev.width - curr.width) / max_w
        width_close = width_diff_ratio < _WIDTH_DIFF_THRESHOLD
        both_short = (
            prev.height < _SHORT_IMAGE_MAX_HEIGHT
            and curr.height < _SHORT_IMAGE_MAX_HEIGHT
        )
        if width_close and both_short:
            current.append(i)
        else:
            groups.append(current)
            current = [i]

    groups.append(current)
    return groups


def _is_svg(path: str | None) -> bool:
    """判断是否为 SVG 图片(按后缀)。"""
    if not path:
        return False
    return path.lower().endswith(".svg")


@dataclass
class StitchedImages:
    """拼接后的图片组"""
    local_path: str                    # 拼接后图片的本地路径
    source_indices: list[int]          # 原始图片索引列表
    width: int                         # 拼接后宽度
    height: int                        # 拼接后总高度
    is_stitched: bool                  # 是否为多图拼接(单图时为 False)
    overlap_trimmed: int               # 拼接时裁剪的重叠像素数(0=无重叠)


class ImageStitcher:
    """短图拼接器

    Args:
        data_dir: 数据目录,拼接结果保存到 ``data_dir/stitched/``。
        overlap_detect: 是否启用拼接边界重叠检测与裁剪。
        dedup: 是否启用感知哈希去重(汉明距离 < 5 视为重复)。
    """

    def __init__(
        self,
        data_dir: str,
        overlap_detect: bool = True,
        dedup: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.overlap_detect = overlap_detect
        self.dedup = dedup
        self.dedup_threshold = _DEDUP_HAMMING_THRESHOLD
        self._stitched_dir = self.data_dir / "stitched"
        self._stitched_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def stitch_images(
        self,
        images: list[ImageAsset],
        indices: list[int],
    ) -> StitchedImages:
        """拼接指定索引的图片。

        Args:
            images: 图片池。
            indices: 要拼接的图片在 ``images`` 中的索引列表。

        Returns:
            StitchedImages 拼接结果。
        """
        selected = [images[i] for i in indices]
        return self._stitch_selected(selected, list(indices))

    def process_article_images(
        self,
        images: list[ImageAsset],
    ) -> list[StitchedImages]:
        """处理文章全部图片,返回拼接后的图片组。

        流程: SVG 跳过 → 去重 → 分组(should_stitch) → 逐组拼接。
        每组可能是一张原图(单图)或多张拼接。
        """
        # 1. 过滤 SVG
        filtered: list[tuple[int, ImageAsset]] = []
        for idx, img in enumerate(images):
            if _is_svg(img.local_path):
                logger.warning(
                    f"图片 {idx} 为 SVG 格式,跳过拼接: {img.local_path}"
                )
                continue
            filtered.append((idx, img))

        if not filtered:
            return []

        # 2. 去重
        if self.dedup:
            dup_set = self._find_duplicates(filtered)
            if dup_set:
                logger.info(f"去重移除 {len(dup_set)} 张重复图片")
            filtered = [
                (idx, img) for idx, img in filtered if idx not in dup_set
            ]

        if not filtered:
            return []

        # 3. 分组
        clean_assets = [a for _, a in filtered]
        groups = should_stitch(clean_assets)

        # 4. 逐组拼接
        results: list[StitchedImages] = []
        for group in groups:
            selected = [clean_assets[p] for p in group]
            orig_indices = [filtered[p][0] for p in group]
            results.append(self._stitch_selected(selected, orig_indices))
        return results

    # ------------------------------------------------------------------
    # 拼接核心
    # ------------------------------------------------------------------
    def _stitch_selected(
        self,
        selected: list[ImageAsset],
        source_indices: list[int],
    ) -> StitchedImages:
        """拼接给定的图片(已选定),并打上原始索引标签。"""
        pil_images = [self._load_image(asset) for asset in selected]
        pil_images = [img for img in pil_images if img is not None]

        if not pil_images:
            logger.warning("无可拼接图片,返回空结果")
            return StitchedImages(
                local_path="",
                source_indices=source_indices,
                width=0,
                height=0,
                is_stitched=False,
                overlap_trimmed=0,
            )

        # 单图: 直接保存,不拼接
        if len(pil_images) == 1:
            img = pil_images[0]
            out_path = self._save(img, source_indices)
            return StitchedImages(
                local_path=out_path,
                source_indices=source_indices,
                width=img.width,
                height=img.height,
                is_stitched=False,
                overlap_trimmed=0,
            )

        # 多图: 以最窄图为基准,其他图等比缩放
        min_width = min(img.width for img in pil_images)
        scaled = [self._scale_to_width(img, min_width) for img in pil_images]

        # 竖直拼接 + 重叠检测
        canvas = scaled[0]
        overlap_trimmed = 0
        for i in range(1, len(scaled)):
            next_img = scaled[i]
            if self.overlap_detect:
                ov = self._detect_overlap(canvas, next_img)
            else:
                ov = 0
            # 防止裁剪超过图片高度
            ov = min(ov, max(next_img.height - 1, 0))
            overlap_trimmed += ov
            if ov > 0:
                next_img = next_img.crop(
                    (0, ov, next_img.width, next_img.height)
                )
                logger.info(f"第 {i} 张图裁剪顶部重叠 {ov}px")
            new_h = canvas.height + next_img.height
            new_canvas = Image.new("RGB", (min_width, new_h), "white")
            new_canvas.paste(canvas, (0, 0))
            new_canvas.paste(next_img, (0, canvas.height))
            canvas = new_canvas

        out_path = self._save(canvas, source_indices)
        logger.info(
            f"拼接 {len(scaled)} 张图 → {canvas.width}x{canvas.height}, "
            f"裁剪重叠 {overlap_trimmed}px"
        )
        return StitchedImages(
            local_path=out_path,
            source_indices=source_indices,
            width=canvas.width,
            height=canvas.height,
            is_stitched=True,
            overlap_trimmed=overlap_trimmed,
        )

    # ------------------------------------------------------------------
    # 图片加载与缩放
    # ------------------------------------------------------------------
    @staticmethod
    def _load_image(asset: ImageAsset) -> Image.Image | None:
        """加载图片为 PIL.Image(RGB)。失败时用 ImageAsset 尺寸创建空白图。"""
        if asset.local_path and Path(asset.local_path).exists():
            try:
                return Image.open(asset.local_path).convert("RGB")
            except Exception as exc:
                logger.warning(f"图片加载失败({asset.local_path}): {exc}")
        # 兜底: 用 ImageAsset 的尺寸创建空白图
        w = max(asset.width, 1)
        h = max(asset.height, 1)
        return Image.new("RGB", (w, h), "white")

    @staticmethod
    def _scale_to_width(img: Image.Image, target_width: int) -> Image.Image:
        """等比缩放到指定宽度(保持纵横比)。"""
        if img.width == target_width:
            return img
        new_height = int(round(img.height * target_width / img.width))
        new_height = max(new_height, 1)
        return img.resize((target_width, new_height), Image.Resampling.LANCZOS)

    # ------------------------------------------------------------------
    # 重叠检测
    # ------------------------------------------------------------------
    def _detect_overlap(
        self,
        img_a: Image.Image,
        img_b: Image.Image,
    ) -> int:
        """检测 img_a 底部与 img_b 顶部的重叠像素数。

        通过比较底部/顶部条带的像素相似度(平均绝对差 MAD)判断重叠。
        若区域为纯色(方差过低)则不检测,避免对纯色图误判。
        """
        if not self.overlap_detect or not _HAS_NUMPY:
            return 0

        h_a, h_b = img_a.height, img_b.height
        max_ov = min(_OVERLAP_MAX, h_a, h_b)
        if max_ov < _OVERLAP_MIN:
            return 0

        w = min(img_a.width, img_b.width)
        strip_a = np.asarray(
            img_a.crop((0, h_a - max_ov, w, h_a)).convert("RGB"),
            dtype=np.float32,
        )
        strip_b = np.asarray(
            img_b.crop((0, 0, w, max_ov)).convert("RGB"),
            dtype=np.float32,
        )

        # 方差守卫: 纯色区域不检测重叠(避免同色图被误判为完全重叠)
        if strip_a.std() < _OVERLAP_STD_GUARD or strip_b.std() < _OVERLAP_STD_GUARD:
            return 0

        best_overlap = 0
        best_mad = _OVERLAP_MAD_THRESHOLD
        # 从大到小搜索最大重叠
        for h in range(max_ov, _OVERLAP_MIN - 1, -1):
            bottom = strip_a[max_ov - h:]
            top = strip_b[:h]
            if bottom.shape != top.shape:
                continue
            mad = float(np.mean(np.abs(bottom - top)))
            if mad < best_mad:
                best_mad = mad
                best_overlap = h
        return best_overlap

    # ------------------------------------------------------------------
    # 感知哈希去重
    # ------------------------------------------------------------------
    def _find_duplicates(
        self,
        items: list[tuple[int, ImageAsset]],
    ) -> set[int]:
        """返回重复图片的原始索引集合(保留首次出现的)。

        Args:
            items: (原始索引, ImageAsset) 列表。
        """
        dup_set: set[int] = set()
        seen: list[tuple[int, tuple[str, object]]] = []  # (orig_idx, hash_repr)
        for orig_idx, asset in items:
            h = self._compute_hash(asset)
            is_dup = False
            for seen_idx, seen_h in seen:
                dist = self._hash_distance(h, seen_h)
                if dist < self.dedup_threshold:
                    logger.warning(
                        f"图片 {orig_idx} 与图片 {seen_idx} 重复"
                        f"(距离 {dist} < {self.dedup_threshold}),去重"
                    )
                    is_dup = True
                    break
            if is_dup:
                dup_set.add(orig_idx)
            else:
                seen.append((orig_idx, h))
        return dup_set

    def _compute_hash(self, asset: ImageAsset) -> tuple[str, object]:
        """计算图片哈希。

        优先使用 imagehash.phash(感知哈希);
        若 imagehash 未安装或计算失败,兜底使用 (宽, 高, 文件大小) 元组。
        """
        path = asset.local_path
        if path and Path(path).exists():
            if _HAS_IMAGEHASH:
                try:
                    img = Image.open(path)
                    return ("phash", imagehash.phash(img))
                except Exception as exc:
                    logger.debug(f"phash 计算失败({path}): {exc}")
            # 兜底: 文件大小 + 尺寸
            try:
                size = Path(path).stat().st_size
            except OSError:
                size = 0
            return ("fallback", (asset.width, asset.height, size))
        # 无本地文件: 用 sha256 + 尺寸
        return ("fallback", (asset.width, asset.height, asset.sha256))

    @staticmethod
    def _hash_distance(
        h1: tuple[str, object],
        h2: tuple[str, object],
    ) -> int:
        """计算两个哈希表示的距离。

        - phash: 汉明距离(ImageHash 相减)。
        - fallback: 完全相同 → 0,否则 → 999(视为不重复)。
        """
        if h1[0] == "phash" and h2[0] == "phash":
            return int(h1[1] - h2[1])
        return 0 if h1[1] == h2[1] else 999

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def _save(self, img: Image.Image, source_indices: list[int]) -> str:
        """保存拼接结果为 JPEG 到 data_dir/stitched/ 目录。"""
        name = "stitched_" + "_".join(str(i) for i in source_indices) + ".jpg"
        out_path = self._stitched_dir / name
        img.save(out_path, "JPEG", quality=95)
        return str(out_path)
