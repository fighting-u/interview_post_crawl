from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


NOWCODER_CREATE_TIME_RE = re.compile(r'"createTime"\s*:\s*(\d{10,13})')
NOWCODER_SUFFIX = "_牛客网_牛客在手,offer不愁"
NOWCODER_IMAGE_SRC_ATTRS = ("src", "data-src", "data-original", "data-url", "data-actualsrc")
NOWCODER_INITIAL_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")
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
    """从页面脚本里解析发帖时间，按北京时间返回。"""

    match = NOWCODER_CREATE_TIME_RE.search(html)
    if not match:
        return None

    timestamp = int(match.group(1))
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    return datetime.fromtimestamp(
        timestamp, tz=timezone(timedelta(hours=8))
    ).isoformat(timespec="seconds")


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
        if img.has_attr("data-card-emoji"):
            continue
        image_url = image_url_from_tag(img, page_url)
        if not image_url or image_url in seen or is_builtin_emoji_url(image_url):
            continue
        seen.add(image_url)
        images.append(ImageRef(url=image_url, alt=(img.get("alt") or "").strip() or None))
    return images


def images_from_script(soup: BeautifulSoup, page_url: str) -> list[ImageRef]:
    """只读取初始状态中当前帖子的 imgMoment，不扫描推荐、评论和头像。"""

    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        match = NOWCODER_INITIAL_STATE_RE.search(text)
        if not match:
            continue
        try:
            state, _ = json.JSONDecoder().raw_decode(text[match.end():])
        except json.JSONDecodeError:
            continue
        if not isinstance(state, dict):
            continue
        # 分享链接可能不是详情 URL，优先使用页面状态中实际渲染的详情路径。
        detail_url = state.get("path") or page_url
        post_id = urlparse(detail_url).path.rstrip("/").split("/")[-1]
        prefetch = state.get("prefetchData")
        if not isinstance(prefetch, dict):
            continue
        for entry in prefetch.values():
            if not isinstance(entry, dict) or str(entry.get("contentId")) != post_id:
                continue
            common = entry.get("ssrCommonData")
            if not isinstance(common, dict):
                continue
            post = common.get("contentData")
            if not isinstance(post, dict):
                continue
            attachments = post.get("imgMoment")
            if not isinstance(attachments, list):
                return []
            images: list[ImageRef] = []
            seen: set[str] = set()
            for item in attachments:
                if not isinstance(item, dict):
                    continue
                src = item.get("src")
                if not isinstance(src, str) or not src.strip():
                    continue
                image_url = urljoin(page_url, src.strip())
                if image_url in seen or is_builtin_emoji_url(image_url):
                    continue
                seen.add(image_url)
                images.append(ImageRef(url=image_url, alt=item.get("alt") or None))
            return images
    return []


def collect_images(soup: BeautifulSoup, content_el: Any, page_url: str) -> list[ImageRef]:
    """只合并正文内嵌图片与当前帖子的附图，保留表情过滤。"""

    images: list[ImageRef] = []
    seen: set[str] = set()
    inline_images = images_from_container(content_el, page_url) if content_el else []
    for image in [*inline_images, *images_from_script(soup, page_url)]:
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
    images = collect_images(soup, content_el, url)

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
