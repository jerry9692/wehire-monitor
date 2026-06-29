"""长图切片器 slicer 测试"""
from pathlib import Path

from PIL import Image

from wehire_monitor.modules.extractor.slicer import (
    SLICE_HEIGHT,
    SLICE_OVERLAP,
    MAX_WIDTH,
    MAX_SLICES,
    LONG_IMAGE_THRESHOLD,
    LOW_OCR_TEXT_THRESHOLD,
    should_slice_image,
    LongImageSlicer,
)


def _create_test_image(width, height, path, color="white"):
    """创建测试用纯色 JPEG 图片"""
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, "JPEG")
    return str(path)


# ----------------------------------------------------------------------
# 1. should_slice_image: 高度 > 3000 触发
# ----------------------------------------------------------------------
def test_should_slice_tall_image(tmp_path):
    """高度>3000 且 OCR 文本<300 → 触发切片"""
    p = _create_test_image(800, 3500, tmp_path / "tall.jpg")
    assert should_slice_image(p, ocr_text_len=100) is True


# ----------------------------------------------------------------------
# 2. should_slice_image: 高度 <= 3000 不触发
# ----------------------------------------------------------------------
def test_should_not_slice_short_image(tmp_path):
    """高度<=3000 → 不触发切片"""
    p = _create_test_image(800, 2000, tmp_path / "short.jpg")
    assert should_slice_image(p, ocr_text_len=100) is False


# ----------------------------------------------------------------------
# 3. should_slice_image: OCR 文本 < 300 且图片较高才触发
# ----------------------------------------------------------------------
def test_should_slice_low_ocr_text(tmp_path):
    """OCR<300字 且图片较高触发; OCR>=300字 不触发"""
    p = _create_test_image(800, 3500, tmp_path / "tall.jpg")
    # OCR 文本 < 300 → 触发
    assert should_slice_image(p, ocr_text_len=200) is True
    # OCR 文本 >= 300 → 不触发(即使图片较高)
    assert should_slice_image(p, ocr_text_len=300) is False
    assert should_slice_image(p, ocr_text_len=500) is False


# ----------------------------------------------------------------------
# 4. slice_image: 5000px 高图 → 4 切片,检查 y_start/y_end/overlap
# ----------------------------------------------------------------------
def test_slice_count_and_overlap(tmp_path):
    """5000px 高图 → 4 切片,验证 y 坐标和重叠"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    slicer = LongImageSlicer(str(data_dir))

    p = _create_test_image(800, 5000, tmp_path / "long.jpg")
    slices = slicer.slice_image(p, image_index=0)

    # step = 1800 - 220 = 1580, ceil(5000/1580) = 4
    assert len(slices) == 4

    # 验证 y_start 升序
    y_starts = [s.meta.y_start for s in slices]
    assert y_starts == [0, 1580, 3160, 4740]

    # 验证 y_end
    y_ends = [s.meta.y_end for s in slices]
    assert y_ends == [1800, 3380, 4960, 5000]

    # 验证重叠 = slice_height - step = 220
    for i in range(1, len(slices)):
        overlap = slices[i - 1].meta.y_end - slices[i].meta.y_start
        assert overlap == SLICE_OVERLAP

    # 验证最后一片 is_bottom=True, 其余 False
    assert slices[-1].meta.is_bottom is True
    assert all(s.meta.is_bottom is False for s in slices[:-1])

    # 验证切片已保存
    for s in slices:
        assert Path(s.local_path).exists()


# ----------------------------------------------------------------------
# 5. slice_image: 宽 > 1440 等比缩放
# ----------------------------------------------------------------------
def test_slice_width_scaling(tmp_path):
    """原宽>1440px 等比缩放到 1440px"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    slicer = LongImageSlicer(str(data_dir))

    # 2000x4000 → 缩放到 1440 x 2880
    p = _create_test_image(2000, 4000, tmp_path / "wide.jpg")
    slices = slicer.slice_image(p, image_index=0)

    # 缩放后高度 2880, step=1580, ceil(2880/1580)=2
    assert len(slices) == 2

    # 验证保存的切片宽度 = 1440
    for s in slices:
        saved = Image.open(s.local_path)
        assert saved.width == MAX_WIDTH

    # 验证 y_end 为缩放后的高度
    assert slices[-1].meta.y_end == 2880


# ----------------------------------------------------------------------
# 6. slice_image: 超过 8 切片截断
# ----------------------------------------------------------------------
def test_slice_max_8(tmp_path):
    """超过 8 切片时截断到 MAX_SLICES"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    slicer = LongImageSlicer(str(data_dir))

    # 100x14000 → step=1580, ceil(14000/1580)=9, 截断到 8
    p = _create_test_image(100, 14000, tmp_path / "very_long.jpg")
    slices = slicer.slice_image(p, image_index=0)

    assert len(slices) == MAX_SLICES

    # 最后一片 is_bottom=True, y_end=height(原始高度,因为宽度未缩放)
    assert slices[-1].meta.is_bottom is True
    assert slices[-1].meta.y_end == 14000

    # y_start 升序
    y_starts = [s.meta.y_start for s in slices]
    assert y_starts == sorted(y_starts)


# ----------------------------------------------------------------------
# 7. slice_image: 元信息完整
# ----------------------------------------------------------------------
def test_slice_metadata(tmp_path):
    """切片元信息(SliceMeta)字段完整"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    slicer = LongImageSlicer(str(data_dir))

    p = _create_test_image(800, 5000, tmp_path / "meta.jpg")
    slices = slicer.slice_image(p, image_index=3)

    assert len(slices) == 4

    for i, s in enumerate(slices):
        # image_index 透传
        assert s.meta.image_index == 3
        # slice_index 从 0 递增
        assert s.meta.slice_index == i
        # y_start < y_end
        assert s.meta.y_start < s.meta.y_end
        # y_start 升序
        if i > 0:
            assert s.meta.y_start > slices[i - 1].meta.y_start

    # 第一片 y_start=0
    assert slices[0].meta.y_start == 0
    # 最后一片 y_end=height, is_bottom=True
    assert slices[-1].meta.y_end == 5000
    assert slices[-1].meta.is_bottom is True

    # local_path 非空且文件存在
    for s in slices:
        assert s.local_path != ""
        assert Path(s.local_path).exists()

    # pil_image 不为 None
    for s in slices:
        assert s.pil_image is not None


# ----------------------------------------------------------------------
# 附加: 短图不切片(高度 <= slice_height)
# ----------------------------------------------------------------------
def test_slice_short_image_single_slice(tmp_path):
    """高度 <= slice_height 时仍返回 1 个切片"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    slicer = LongImageSlicer(str(data_dir))

    p = _create_test_image(800, 1000, tmp_path / "short.jpg")
    slices = slicer.slice_image(p, image_index=0)

    assert len(slices) == 1
    assert slices[0].meta.y_start == 0
    assert slices[0].meta.y_end == 1000
    assert slices[0].meta.is_bottom is True


# ----------------------------------------------------------------------
# 附加: slices/ 目录自动创建
# ----------------------------------------------------------------------
def test_slices_dir_created(tmp_path):
    """data_dir/slices/ 目录自动创建"""
    data_dir = tmp_path / "data"
    # 不预先创建 data_dir
    slicer = LongImageSlicer(str(data_dir))

    assert (data_dir / "slices").exists()
    assert (data_dir / "slices").is_dir()


# ----------------------------------------------------------------------
# 附加: should_slice_image 文件不存在返回 False
# ----------------------------------------------------------------------
def test_should_slice_image_nonexistent_file(tmp_path):
    """文件不存在时 should_slice_image 返回 False"""
    assert should_slice_image(str(tmp_path / "nonexistent.jpg"), ocr_text_len=0) is False
