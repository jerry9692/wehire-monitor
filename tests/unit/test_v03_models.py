"""v0.3 领域模型测试"""
from wehire_monitor.domain.models import ImageSlice, SliceMeta


def test_slice_meta_defaults():
    meta = SliceMeta(image_index=0, slice_index=0, y_start=0, y_end=1800)
    assert meta.image_index == 0
    assert meta.slice_index == 0
    assert meta.y_start == 0
    assert meta.y_end == 1800
    assert meta.is_bottom is False


def test_slice_meta_bottom_flag():
    meta = SliceMeta(image_index=0, slice_index=3, y_start=5400, y_end=7200, is_bottom=True)
    assert meta.is_bottom is True


def test_image_slice_creation():
    meta = SliceMeta(image_index=0, slice_index=1, y_start=1580, y_end=3380)
    sl = ImageSlice(
        pil_image=None,
        local_path="data/images/slice_0_1.jpg",
        meta=meta,
    )
    assert sl.local_path == "data/images/slice_0_1.jpg"
    assert sl.meta.slice_index == 1
    assert sl.meta.y_start == 1580
