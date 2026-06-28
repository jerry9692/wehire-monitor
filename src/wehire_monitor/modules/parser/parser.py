"""文章解析器

v0.1 使用 BS4 兜底解析器。
后续版本优先封装 wechat-article-for-ai,失败回退 BS4。
"""
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from wehire_monitor.domain.models import ArticleMeta, ImageAsset, ParsedArticle
from wehire_monitor.modules.parser.adapters.bs4_fallback import extract_content

# Content-Type → 文件扩展名映射
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


class Parser:
    """文章解析器:HTML → 正文 + 图片本地化(支持上下文管理器)"""

    def __init__(self, data_dir: str = "data", user_agent: str | None = None):
        self.data_dir = Path(data_dir).resolve()
        self.images_dir = self.data_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        self._client = httpx.Client(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    def __enter__(self) -> "Parser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _fetch_html(self, url: str) -> str:
        """下载文章 HTML"""
        resp = self._client.get(
            url,
            headers={
                "Referer": "https://mp.weixin.qq.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        resp.raise_for_status()
        return resp.text

    def _guess_ext(self, content_type: str | None, url: str) -> str:
        """根据 Content-Type 和 URL 推断图片扩展名"""
        if content_type:
            ct = content_type.split(";")[0].strip().lower()
            if ct in _CONTENT_TYPE_EXT:
                return _CONTENT_TYPE_EXT[ct]
        # 从 URL 推断
        lower_url = url.lower().split("?")[0]
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            if lower_url.endswith(ext):
                return ".jpg" if ext == ".jpeg" else ext
        return ".jpg"

    def _download_image(
        self, url: str, article_id: str, index: int
    ) -> tuple[str | None, int, int, str]:
        """下载图片到本地,返回 (local_path, width, height, sha256)"""
        try:
            resp = self._client.get(url, headers={"Referer": "https://mp.weixin.qq.com/"})
            resp.raise_for_status()
            content = resp.content
            if not content:
                logger.warning(f"图片内容为空: {url}")
                return None, 0, 0, ""

            # 先验证图片并获取尺寸
            from PIL import Image, UnidentifiedImageError
            try:
                with Image.open(io.BytesIO(content)) as img:
                    img.load()  # 强制加载检测损坏
                    width, height = img.size
                    fmt = (img.format or "JPEG").lower()
            except (UnidentifiedImageError, OSError) as e:
                logger.warning(f"图片损坏无法识别: {url} — {e}")
                return None, 0, 0, ""

            # 计算 sha256
            sha = hashlib.sha256(content).hexdigest()

            # 根据格式保存
            ext = self._guess_ext(resp.headers.get("content-type"), url)
            local_path = str(self.images_dir / f"{article_id}_{index}{ext}")
            with open(local_path, "wb") as f:
                f.write(content)

            return local_path, width, height, sha
        except httpx.HTTPError as e:
            logger.warning(f"图片下载 HTTP 错误: {url} — {e}")
            return None, 0, 0, ""
        except OSError as e:
            logger.warning(f"图片保存失败: {url} — {e}")
            return None, 0, 0, ""

    def parse(self, meta: ArticleMeta) -> ParsedArticle:
        """解析文章"""
        article_id = hashlib.sha256(meta.url.encode()).hexdigest()
        logger.info(f"解析文章: {meta.title} (id={article_id[:8]})")

        html = self._fetch_html(meta.url)
        plain_text, images_info = extract_content(html)

        # 下载图片
        images: list[ImageAsset] = []
        for info in images_info:
            local_path, width, height, sha = self._download_image(
                info["url"], article_id, info["index"]
            )
            images.append(
                ImageAsset(
                    index=info["index"],
                    url=info["url"],
                    local_path=local_path,
                    width=width,
                    height=height,
                    sha256=sha,
                    status="ok" if local_path else "image_download_failed",
                )
            )

        content_hash = hashlib.sha256(plain_text.encode()).hexdigest()

        return ParsedArticle(
            article_id=article_id,
            title=meta.title,
            plain_text=plain_text,
            images=images,
            content_hash=content_hash,
        )

    def close(self) -> None:
        self._client.close()
