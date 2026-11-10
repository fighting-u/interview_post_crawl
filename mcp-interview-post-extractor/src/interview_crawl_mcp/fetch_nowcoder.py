from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


NOWCODER_CREATE_TIME_RE = re.compile(r'"createTime"\s*:\s*(\d{10,13})')
NOWCODER_SUFFIX = "_牛客网_牛客在手,offer不愁"
NOWCODER_IMAGE_SRC_ATTRS = ("src", "data-src", "data-original", "data-url", "data-actualsrc")
NOWCODER_IMG_MOMENT_BLOCK_RE = re.compile(r'"imgMoment"\s*:\s*\[(.*?)\]', re.DOTALL)
NOWCODER_UPLOAD_IMAGE_RE = re.compile(
    r"https?://uploadfiles\.nowcoder\.com/images/[A-Za-z0-9_./-]+"
)
NOWCODER_BUILTIN_EMOJI_MARKERS = ("20220815/318889480",)
NOWCODER_FETCH_ATTEMPTS = 3
NOWCODER_DETAIL_BODY_MIN_LENGTH = 500


class FetchError(RuntimeError):
    """牛客抓取失败时抛出的业务异常。"""


@dataclass(frozen=True)
class ImageRef:
    """页面里解析到的一张图片。"""

    url: str
    alt: str | None = None


@dataclass(frozen=True)
class FetchedPost:
    """牛客帖子解析结果。"""

    source: str
    url: str
    title: str | None
    posted_at: str | None
    body: str | None
    images: list[ImageRef]


def user_agent() -> str:
    """返回牛客页面请求使用的桌面浏览器 UA。"""

    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )


def fetch_html(url: str) -> str:
    """请求牛客帖子 HTML。"""

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": user_agent(),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    response.raise_for_status()
    return response.text


def parse_create_time(html: str) -> str | None:
    """从页面脚本里解析发帖日期。"""

    match = NOWCODER_CREATE_TIME_RE.search(html)
    if not match:
        return None

    timestamp = int(match.group(1))
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def normalize_js_text(text: str) -> str:
    """还原脚本中被转义的 URL 字符。"""

    return text.replace("\\u002F", "/").replace("\\/", "/").replace('\\"', '"')


def is_builtin_emoji_url(image_url: str) -> bool:
    """判断是否为牛客自带表情包图片。"""

    return any(marker in image_url for marker in NOWCODER_BUILTIN_EMOJI_MARKERS)


def image_url_from_tag(img: Any, page_url: str) -> str | None:
    """从 img 标签常见属性里取图片地址。"""

    for attr in NOWCODER_IMAGE_SRC_ATTRS:
        value = (img.get(attr) or "").strip()
        if value:
            return value if value.startswith("data:image/") else urljoin(page_url, value)
    return None


def images_from_container(container: Any, page_url: str) -> list[ImageRef]:
    """从一个 HTML 容器中提取图片。"""

    images: list[ImageRef] = []
    seen: set[str] = set()
    for img in container.select("img"):
        image_url = image_url_from_tag(img, page_url)
        if not image_url or image_url in seen or is_builtin_emoji_url(image_url):
            continue
        seen.add(image_url)
        images.append(ImageRef(url=image_url, alt=(img.get("alt") or "").strip() or None))
    return images


def images_from_script(html: str) -> list[ImageRef]:
    """从页面脚本里的 imgMoment 数据兜底提取图片。"""

    normalized_html = normalize_js_text(html)
    search_areas = NOWCODER_IMG_MOMENT_BLOCK_RE.findall(normalized_html) or [normalized_html]

    for search_area in search_areas:
        images: list[ImageRef] = []
        seen: set[str] = set()
        for match in NOWCODER_UPLOAD_IMAGE_RE.finditer(search_area):
            image_url = match.group(0)
            if image_url in seen or is_builtin_emoji_url(image_url):
                continue
            seen.add(image_url)
            images.append(ImageRef(url=image_url))
        if images:
            return images
    return []


def collect_images(html: str, soup: BeautifulSoup, content_el: Any, page_url: str) -> list[ImageRef]:
    """汇总正文附近和脚本里的所有图片。"""

    containers: list[Any] = []
    if content_el:
        containers.append(content_el)
        if content_el.parent:
            containers.extend(content_el.parent.select("div.feed-img, div[class*='feed-img']"))
        containers.extend(content_el.find_next_siblings())
    containers.extend(soup.select("div.feed-img, div[class*='feed-img']"))

    images: list[ImageRef] = []
    seen: set[str] = set()
    for container in containers:
        if not hasattr(container, "select"):
            continue
        for image in images_from_container(container, page_url):
            if image.url in seen:
                continue
            seen.add(image.url)
            images.append(image)

    for image in images_from_script(html):
        if image.url in seen:
            continue
        seen.add(image.url)
        images.append(image)

    return images


def post_score(post: FetchedPost) -> int:
    """给解析结果打分，用于规避 share/jump 偶发返回摘要页。"""

    return len(post.body or "") + len(post.images) * 500 + (200 if post.title else 0)


def is_probably_detail_post(post: FetchedPost) -> bool:
    """判断是否已经拿到详情页，而不是只有 meta description 的分享摘要。"""

    return bool(post.title or post.images or len(post.body or "") >= NOWCODER_DETAIL_BODY_MIN_LENGTH)


def parse_nowcoder_post(html: str, url: str) -> FetchedPost:
    """从牛客 HTML 中解析帖子正文、标题、发布时间和图片。"""

    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("div.content-post-title h1") or soup.select_one("h1")
    content_el = soup.select_one("div.nc-slate-editor-content") or soup.select_one(
        "div.feed-content-text"
    )

    title = title_el.get_text(strip=True) if title_el else ""
    body = content_el.get_text("\n", strip=True) if content_el else ""
    images = collect_images(html, soup, content_el, url)

    if not body:
        desc_el = soup.select_one('meta[name="description"]')
        body = (desc_el.get("content") or "").strip() if desc_el else ""
        if body.endswith(NOWCODER_SUFFIX):
            body = body[: -len(NOWCODER_SUFFIX)].rstrip()

    if not body and not images:
        raise FetchError("牛客正文和图片都未解析到，页面可能需要登录或触发了验证。")

    return FetchedPost(
        source="nowcoder",
        url=url,
        title=title or None,
        posted_at=parse_create_time(html),
        body=body or None,
        images=images,
    )


def fetch_nowcoder_post(url: str) -> FetchedPost:
    """抓取牛客帖子正文、标题、发布时间和图片。"""

    best_post: FetchedPost | None = None

    for attempt in range(NOWCODER_FETCH_ATTEMPTS):
        html = fetch_html(url)
        post = parse_nowcoder_post(html, url)

        if best_post is None or post_score(post) > post_score(best_post):
            best_post = post

        if is_probably_detail_post(post):
            return post

        if attempt < NOWCODER_FETCH_ATTEMPTS - 1:
            time.sleep(0.5)

    if best_post is None:
        raise FetchError("牛客正文和图片都未解析到，页面可能需要登录或触发了验证。")

    return best_post
