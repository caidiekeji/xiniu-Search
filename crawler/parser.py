"""
xiniubot HTML 解析器
====================
从 HTML 中提取:
  - 标题 (title)
  - 元描述 (meta description)
  - 正文文本
  - 链接 (带锚文本)
  - 编码处理
"""

import re
import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment

import config

logger = logging.getLogger("xiniubot.parser")


class PageData:
    """解析后的页面数据."""
    __slots__ = ("url", "title", "description", "body_text", "links", "text_length")

    def __init__(self):
        self.url: str = ""
        self.title: str = ""
        self.description: str = ""
        self.body_text: str = ""
        self.links: list[tuple[str, str]] = []   # [(url, anchor_text), ...]
        self.text_length: int = 0


# 需要移除的标签 (boilerplate)
REMOVE_TAGS = {
    "script", "style", "nav", "footer", "header", "aside",
    "noscript", "iframe", "svg", "canvas", "form", "input",
    "button", "select", "textarea",
}


def parse_html(url: str, html_bytes: bytes, encoding: str = "utf-8") -> PageData:
    """
    解析 HTML, 提取文本和链接.

    Args:
        url: 页面 URL
        html_bytes: 原始 HTML 字节
        encoding: 字符编码

    Returns:
        PageData 对象
    """
    page = PageData()
    page.url = url

    # 解码
    try:
        html_str = html_bytes.decode(encoding, errors="replace")
    except (UnicodeDecodeError, LookupError):
        html_str = html_bytes.decode("utf-8", errors="replace")

    # 解析
    try:
        soup = BeautifulSoup(html_str, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html_str, "html.parser")
        except Exception:
            page.body_text = html_str[:5000]
            return page

    # ── 移除不需要的标签 ──
    for tag_name in REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # 移除注释
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # ── 提取标题 ──
    title_tag = soup.find("title")
    if title_tag:
        page.title = title_tag.get_text(strip=True)[:200]

    # ── 提取 meta description ──
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta_desc and meta_desc.get("content"):
        page.description = meta_desc["content"].strip()[:500]

    # ── 提取正文文本 (主内容去噪) ──
    page.body_text = _extract_main_text(soup)

    # 限制正文长度
    page.text_length = len(page.body_text)
    if page.text_length > 50000:
        page.body_text = page.body_text[:50000]

    # ── 提取链接 ──
    links = []
    seen_urls = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        # 绝对化 URL
        abs_url = urljoin(url, href)

        # 过滤
        if len(abs_url) > config.CRAWLER["max_url_length"]:
            continue

        # 去重
        if abs_url not in seen_urls:
            seen_urls.add(abs_url)
            anchor = a_tag.get_text(strip=True)[:100]
            links.append((abs_url, anchor))

    page.links = links
    return page


def extract_title_from_html(html_bytes: bytes, encoding: str = "utf-8") -> str:
    """快速提取标题 (不完整解析)."""
    try:
        html_str = html_bytes.decode(encoding, errors="replace")
    except Exception:
        return ""

    m = re.search(r"<title[^>]*>(.*?)</title>", html_str, re.I | re.S)
    if m:
        return m.group(1).strip()[:200]
    return ""


def _collect_text(container) -> list[str]:
    """收集容器内的所有文本片段, 过滤单字符噪声."""
    parts = []
    for string in container.stripped_strings:
        text = string.strip()
        if text and len(text) > 1:
            parts.append(text)
    return parts


def _extract_main_text(soup) -> str:
    """
    主内容提取: 优先 article/main 容器; 否则按文本密度评分过滤导航/页脚/菜单噪音.
    """
    # 1) 优先语义容器
    container = soup.find("article") or soup.find("main")
    if container is not None:
        parts = _collect_text(container)
        if sum(len(x) for x in parts) >= 100:
            return "\n".join(parts)

    # 2) 基于文本密度: 保留较长且非链接为主的块
    blocks = []
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "li"]):
        if el.find_parent(["nav", "footer", "header", "aside"]):
            continue
        text = el.get_text(" ", strip=True)
        if len(text) < 20:
            continue
        # 过滤链接为主的块 (导航/菜单/标签云)
        link_len = sum(len(a.get_text(" ", strip=True)) for a in el.find_all("a"))
        if link_len > len(text) * 0.6:
            continue
        blocks.append(text)
    if len(blocks) >= 2:
        return "\n".join(blocks)

    # 3) 兜底: 整页文本
    return "\n".join(_collect_text(soup.body or soup))
