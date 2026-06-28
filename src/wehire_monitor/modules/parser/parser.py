"""文章解析器

v0.1 使用 BS4 兜底解析器。
后续版本优先封装 wechat-article-for-ai,失败回退 BS4。
"""
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from wehire_monitor.domain.models import ArticleMeta, ImageAsset, ParsedArticle
from wehire_monitor.modules.parser.adapters.bs4_fallback import extract_content


class Parser:
    """文章解析器:HTML → 正文 + 图片本地化"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.images_dir = self.data_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    def _fetch_html(self, url: str) -> str:
        """下载文章 HTML"""
        resp = self._client.get(url, headers={"Referer": "https://mp.weixin.qq.com/"})
        resp.raise_for_status()
        return resp.text

    def _download_image(
        self, url: str, article_id: str, index: int
    ) -> tuple[str | None, int, int, str]:
        """下载图片到本地,返回 (local_path, width, height, sha256)"""
        try:
            resp = self._client.get(url, headers={"Referer": "https://mp.weixin.qq.com/"})
            resp.raise_for_status()
            content = resp.content

            # 计算 sha256
            sha = hashlib.sha256(content).hexdigest()

            # 保存
            ext = ".jpg"
            local_path = str(self.images_dir / f"{article_id}_{index}{ext}")
            with open(local_path, "wb") as f:
                f.write(content)

            # 获取尺寸
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(content))
            width, height = img.size

            return local_path, width, height, sha
        except Exception as e:
            logger.warning(f"图片下载失败: {url} — {e}")
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
