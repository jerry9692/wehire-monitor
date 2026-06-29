"""短图拼接 stitcher 测试"""
from pathlib import Path

from PIL import Image

from wehire_monitor.domain.models import ImageAsset
from wehire_monitor.modules.extractor.stitcher import (
    ImageStitcher,
    StitchedImages,
    should_stitch,
)


def _create_test_image(width, height, path, color="white"):
    """创建测试用纯色 JPEG 图片。"""
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, "JPEG")
    return path


def _make_asset(index, path, width, height, sha256=None):
    """构造 ImageAsset。"""
    return ImageAsset(
        index=index,
        url="",
        local_path=str(path),
        width=width,
        height=height,
        sha256=sha256 or f"sha_{index}",
    )


# ----------------------------------------------------------------------
# 1. 两张短图应拼接(宽度相近、高度 < 3000)
# ----------------------------------------------------------------------
def test_should_stitch_two_short():
    a = ImageAsset(index=0, url="", local_path=None, width=800, height=1000, sha256="a")
    b = ImageAsset(index=1, url="", local_path=None, width=800, height=1200, sha256="b")
    groups = should_stitch([a, b])
    assert groups == [[0, 1]]


def test_stitch_two_short_images(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)
    p1 = _create_test_image(800, 1000, tmp_path / "a.jpg", color="white")
    p2 = _create_test_image(800, 1200, tmp_path / "b.jpg", color="red")
    a1 = _make_asset(0, p1, 800, 1000, "a")
    a2 = _make_asset(1, p2, 800, 1200, "b")
    result = stitcher.stitch_images([a1, a2], [0, 1])

    assert result.is_stitched is True
    assert result.source_indices == [0, 1]
    assert result.width == 800
    assert result.height == 2200  # 1000 + 1200
    assert result.overlap_trimmed == 0
    assert Path(result.local_path).exists()


# ----------------------------------------------------------------------
# 2. 不同宽度的图不拼接(宽度差异 >= 10%)
# ----------------------------------------------------------------------
def test_should_stitch_different_widths():
    a = ImageAsset(index=0, url="", local_path=None, width=800, height=1000, sha256="a")
    b = ImageAsset(index=1, url="", local_path=None, width=400, height=1000, sha256="b")
    groups = should_stitch([a, b])
    assert groups == [[0], [1]]


def test_process_different_widths_not_stitched(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)
    p1 = _create_test_image(800, 1000, tmp_path / "a.jpg", color="white")
    p2 = _create_test_image(400, 1000, tmp_path / "b.jpg", color="red")
    a1 = _make_asset(0, p1, 800, 1000, "a")
    a2 = _make_asset(1, p2, 400, 1000, "b")
    results = stitcher.process_article_images([a1, a2])

    assert len(results) == 2
    assert all(r.is_stitched is False for r in results)


# ----------------------------------------------------------------------
# 3. 单张图不需要拼接
# ----------------------------------------------------------------------
def test_should_stitch_single():
    a = ImageAsset(index=0, url="", local_path=None, width=800, height=1000, sha256="a")
    groups = should_stitch([a])
    assert groups == [[0]]


def test_stitch_single_image(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)
    p = _create_test_image(800, 1000, tmp_path / "a.jpg", color="white")
    a = _make_asset(0, p, 800, 1000, "a")
    result = stitcher.stitch_images([a], [0])

    assert result.is_stitched is False
    assert result.width == 800
    assert result.height == 1000
    assert result.source_indices == [0]
    assert result.overlap_trimmed == 0
    assert Path(result.local_path).exists()


# ----------------------------------------------------------------------
# 4. SVG 图片被跳过
# ----------------------------------------------------------------------
def test_svg_skipped(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)

    svg_path = tmp_path / "logo.svg"
    svg_path.write_text("<svg></svg>")
    svg_asset = _make_asset(0, svg_path, 800, 1000, "svg")

    p1 = _create_test_image(800, 1000, tmp_path / "a.jpg", color="white")
    p2 = _create_test_image(800, 1000, tmp_path / "b.jpg", color="red")
    a1 = _make_asset(1, p1, 800, 1000, "a")
    a2 = _make_asset(2, p2, 800, 1000, "b")

    results = stitcher.process_article_images([svg_asset, a1, a2])

    # SVG 被跳过,剩下两张短图拼接为一组
    assert len(results) == 1
    assert results[0].is_stitched is True
    assert results[0].source_indices == [1, 2]  # 保留原始索引


# ----------------------------------------------------------------------
# 5. 重复图片被去重
# ----------------------------------------------------------------------
def test_duplicate_dedup(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=True)

    # 两张完全相同的图片(相同颜色、相同尺寸)
    p1 = _create_test_image(800, 1000, tmp_path / "a.jpg", color="red")
    p2 = _create_test_image(800, 1000, tmp_path / "b.jpg", color="red")
    a1 = _make_asset(0, p1, 800, 1000, "a")
    a2 = _make_asset(1, p2, 800, 1000, "b")

    results = stitcher.process_article_images([a1, a2])

    # 去重后只剩一张,单图不拼接
    assert len(results) == 1
    assert results[0].is_stitched is False
    assert results[0].source_indices == [0]  # 保留首次出现的


def test_dedup_disabled_keeps_both(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)

    p1 = _create_test_image(800, 1000, tmp_path / "a.jpg", color="red")
    p2 = _create_test_image(800, 1000, tmp_path / "b.jpg", color="red")
    a1 = _make_asset(0, p1, 800, 1000, "a")
    a2 = _make_asset(1, p2, 800, 1000, "b")

    results = stitcher.process_article_images([a1, a2])

    # 不去重 → 两张短图被拼接
    assert len(results) == 1
    assert results[0].is_stitched is True
    assert results[0].source_indices == [0, 1]


# ----------------------------------------------------------------------
# 6. 拼接后高度正确
# ----------------------------------------------------------------------
def test_stitched_height_correct(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)
    p1 = _create_test_image(800, 1000, tmp_path / "a.jpg", color="white")
    p2 = _create_test_image(800, 1500, tmp_path / "b.jpg", color="red")
    a1 = _make_asset(0, p1, 800, 1000, "a")
    a2 = _make_asset(1, p2, 800, 1500, "b")
    result = stitcher.stitch_images([a1, a2], [0, 1])

    assert result.height == 2500  # 1000 + 1500
    assert result.overlap_trimmed == 0
    assert Path(result.local_path).exists()
    # 保存的图片确实是对应尺寸
    saved = Image.open(result.local_path)
    assert saved.size == (800, 2500)


# ----------------------------------------------------------------------
# 7. 三张图连续拼接
# ----------------------------------------------------------------------
def test_stitch_three_images(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)
    colors = ["white", "red", "blue"]
    heights = [1000, 1100, 1200]
    assets = []
    for i, (h, c) in enumerate(zip(heights, colors)):
        p = _create_test_image(800, h, tmp_path / f"{i}.jpg", color=c)
        assets.append(_make_asset(i, p, 800, h, str(i)))

    result = stitcher.stitch_images(assets, [0, 1, 2])

    assert result.is_stitched is True
    assert result.source_indices == [0, 1, 2]
    assert result.height == 3300  # 1000 + 1100 + 1200
    assert result.width == 800
    assert result.overlap_trimmed == 0
    assert Path(result.local_path).exists()


def test_should_stitch_three_short():
    assets = [
        ImageAsset(index=i, url="", local_path=None, width=800, height=1000 + i * 100, sha256=str(i))
        for i in range(3)
    ]
    assert should_stitch(assets) == [[0, 1, 2]]


# ----------------------------------------------------------------------
# 8. 混合场景: 短图 + 长图 + 短图,只有连续短图被拼接
# ----------------------------------------------------------------------
def test_mixed_scenario(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)
    # 短, 短, 长, 短, 短
    heights = [1000, 1100, 4000, 900, 1000]
    colors = ["white", "red", "blue", "green", "yellow"]
    assets = []
    for i, (h, c) in enumerate(zip(heights, colors)):
        p = _create_test_image(800, h, tmp_path / f"{i}.jpg", color=c)
        assets.append(_make_asset(i, p, 800, h, str(i)))

    results = stitcher.process_article_images(assets)

    assert len(results) == 3
    # 第一组: 两张短图拼接
    assert results[0].is_stitched is True
    assert results[0].source_indices == [0, 1]
    assert results[0].height == 2100  # 1000 + 1100
    # 第二组: 长图单独
    assert results[1].is_stitched is False
    assert results[1].source_indices == [2]
    assert results[1].height == 4000
    # 第三组: 两张短图拼接
    assert results[2].is_stitched is True
    assert results[2].source_indices == [3, 4]
    assert results[2].height == 1900  # 900 + 1000


# ----------------------------------------------------------------------
# 附加: 拼接时以最窄图为基准等比缩放
# ----------------------------------------------------------------------
def test_stitch_scale_to_min_width(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stitcher = ImageStitcher(str(data_dir), dedup=False)
    # 800x1000 → 缩放到 400 宽 → 400x500
    p1 = _create_test_image(800, 1000, tmp_path / "a.jpg", color="white")
    # 400x500 → 已是最窄,不变
    p2 = _create_test_image(400, 500, tmp_path / "b.jpg", color="red")
    a1 = _make_asset(0, p1, 800, 1000, "a")
    a2 = _make_asset(1, p2, 400, 500, "b")
    result = stitcher.stitch_images([a1, a2], [0, 1])

    assert result.is_stitched is True
    assert result.width == 400          # 最窄宽度
    assert result.height == 1000        # 500 + 500
    assert Path(result.local_path).exists()
