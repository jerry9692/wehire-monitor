"""BeautifulSoup 兜底解析器

解析规则(承接 spec §4.2):
- 正文容器优先 #js_content
- 图片地址优先级:data-src > src > data-backsrc
"""
from bs4 import BeautifulSoup

from wehire_monitor.domain.models import ImageAsset


def extract_content(html: str) -> tuple[str, list[dict]]:
    """从 HTML 中提取正文文本和图片信息

    Returns:
        (plain_text, images_info)
        images_info: list of {url, index}
    """
    soup = BeautifulSoup(html, "lxml")

    # 正文容器
    content_div = soup.find(id="js_content")
    if content_div is None:
        # 兜底:用整个 body
        content_div = soup.find("body") or soup

    plain_text = content_div.get_text(separator="\n", strip=True)

    # 图片提取
    images_info: list[dict] = []
    for idx, img in enumerate(content_div.find_all("img")):
        url = (
            img.get("data-src")
            or img.get("src")
            or img.get("data-backsrc")
            or ""
        )
        if url:
            images_info.append({"url": url, "index": idx})

    return plain_text, images_info
