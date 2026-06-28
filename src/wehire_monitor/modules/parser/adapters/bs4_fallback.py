"""BeautifulSoup 兜底解析器

解析规则(承接 spec §4.2):
- 正文容器优先 #js_content
- 图片地址优先级:data-src > src > data-backsrc
"""
from bs4 import BeautifulSoup
from urllib.parse import urljoin


def extract_content(html: str) -> tuple[str, list[dict]]:
    """从 HTML 中提取正文文本和图片信息

    Returns:
        (plain_text, images_info)
        images_info: list of {url, index}, index 从 0 连续递增
    """
    soup = BeautifulSoup(html, "lxml")

    # 正文容器
    content_div = soup.find(id="js_content")
    if content_div is None:
        # 兜底:用整个 body,记录警告
        content_div = soup.find("body") or soup

    plain_text = content_div.get_text(separator="\n", strip=True)

    # 图片提取(使用独立计数器保证 index 连续)
    images_info: list[dict] = []
    img_index = 0
    for img in content_div.find_all("img"):
        url = (
            img.get("data-src")
            or img.get("src")
            or img.get("data-backsrc")
            or ""
        ).strip()
        if not url:
            continue
        # 处理协议相对 URL
        if url.startswith("//"):
            url = "https:" + url
        images_info.append({"url": url, "index": img_index})
        img_index += 1

    return plain_text, images_info
