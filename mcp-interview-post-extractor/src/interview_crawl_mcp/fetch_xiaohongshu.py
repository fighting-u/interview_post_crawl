from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BROWSER_DATA_DIR = PROJECT_ROOT / ".browser_data" / "xhs"

XHS_HOST = "https://edith.xiaohongshu.com"
XHS_INDEX_URL = "https://www.xiaohongshu.com"
XHS_NOTE_DETAIL_URI = "/api/sns/web/v1/feed"
XHS_IMAGE_URL_KEYS = ("url_default", "url_pre", "url_size_large", "url")


class FetchError(RuntimeError):
    """小红书抓取或登录态维护失败时抛出的业务异常。"""


@dataclass(frozen=True)
class ImageRef:
    """笔记里解析到的一张图片。"""

    url: str
    alt: str | None = None


@dataclass(frozen=True)
class FetchedPost:
    """小红书笔记解析结果。"""

    source: str
    url: str
    title: str | None
    posted_at: str | None
    body: str | None
    images: list[ImageRef]


@dataclass(frozen=True)
class NoteUrlInfo:
    """小红书详情接口需要的链接参数。"""

    url: str
    note_id: str
    xsec_token: str
    xsec_source: str


def user_agent(chrome_version: str = "137.0.0.0") -> str:
    """返回小红书请求和浏览器读取 Cookie 使用的 UA。"""

    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_version} Safari/537.36"
    )


def has_required_cookie(cookie: str) -> bool:
    """判断 Cookie 是否包含小红书接口需要的关键字段。"""

    return "a1=" in cookie and "web_session=" in cookie


def cookies_to_string(cookies: list[dict[str, Any]]) -> str:
    """把 Playwright Cookie 列表拼成 requests 可用的 Cookie header。"""

    return "; ".join(
        f"{cookie['name']}={cookie['value']}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value")
    )


def open_browser_context(user_data_dir: Path, *, headless: bool):
    """用当前 MCP 项目的 profile 打开持久浏览器上下文。"""

    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        channel="msedge",
        headless=headless,
        accept_downloads=False,
        user_agent=user_agent(),
    )
    return playwright, context


async def open_browser_context_async(user_data_dir: Path, *, headless: bool):
    """用 async Playwright 打开当前 MCP 项目的持久浏览器上下文。"""

    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        channel="msedge",
        headless=headless,
        accept_downloads=False,
        user_agent=user_agent(),
    )
    return playwright, context


def read_cookie_from_browser_state(user_data_dir: Path = DEFAULT_BROWSER_DATA_DIR) -> str:
    """MCP 请求前从项目 profile 中读取最新 Cookie。"""

    if not user_data_dir.exists() or not any(user_data_dir.iterdir()):
        raise FetchError("小红书登录态不存在，请先运行 `uv run maintain-xhs-login`。")

    try:
        playwright, context = open_browser_context(user_data_dir, headless=True)
    except ImportError as exc:
        raise FetchError("缺少依赖 playwright，请先在 my_mcp 中运行 `uv sync`。") from exc

    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(XHS_INDEX_URL, wait_until="domcontentloaded", timeout=60_000)
        cookie = cookies_to_string(context.cookies([XHS_INDEX_URL]))
        if not has_required_cookie(cookie):
            raise FetchError("小红书登录态已失效，请运行 `uv run maintain-xhs-login` 重新登录。")
        return cookie
    finally:
        context.close()
        playwright.stop()


async def read_cookie_from_browser_state_async(
    user_data_dir: Path = DEFAULT_BROWSER_DATA_DIR,
) -> str:
    """在 MCP async server 中读取最新 Cookie，避免 sync Playwright 嵌套事件循环。"""

    if not user_data_dir.exists() or not any(user_data_dir.iterdir()):
        raise FetchError("小红书登录态不存在，请先运行 `uv run maintain-xhs-login`。")

    try:
        playwright, context = await open_browser_context_async(user_data_dir, headless=True)
    except ImportError as exc:
        raise FetchError("缺少依赖 playwright，请先在 my_mcp 中运行 `uv sync`。") from exc

    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(XHS_INDEX_URL, wait_until="domcontentloaded", timeout=60_000)
        cookie = cookies_to_string(await context.cookies([XHS_INDEX_URL]))
        if not has_required_cookie(cookie):
            raise FetchError("小红书登录态已失效，请运行 `uv run maintain-xhs-login` 重新登录。")
        return cookie
    finally:
        await context.close()
        await playwright.stop()


def parse_note_url(url: str) -> NoteUrlInfo:
    """从小红书分享链接中解析 note_id 和 xsec 参数。"""

    parsed = urlparse(url)
    note_id = parsed.path.rstrip("/").split("/")[-1]
    params = parse_qs(parsed.query)
    xsec_token = (params.get("xsec_token") or [""])[0]
    xsec_source = (params.get("xsec_source") or ["pc_share"])[0]

    if not note_id:
        raise FetchError(f"无法从小红书链接解析 note_id：{url}")
    if not xsec_token:
        raise FetchError("小红书链接缺少 xsec_token，请使用 App 复制出的完整分享链接。")

    return NoteUrlInfo(url=url, note_id=note_id, xsec_token=xsec_token, xsec_source=xsec_source)


def sign_headers(uri: str, payload: dict[str, Any], cookie: str) -> dict[str, str]:
    """调用 xhshow 生成小红书详情接口签名头。"""

    try:
        from xhshow import Xhshow
    except ImportError as exc:
        raise FetchError("缺少依赖 xhshow，请先在 my_mcp 中运行 `uv sync`。") from exc

    signs = Xhshow().sign_headers_post(uri=uri, cookies=cookie, payload=payload)
    return {
        "X-S": signs.get("x-s", ""),
        "X-T": signs.get("x-t", ""),
        "x-S-Common": signs.get("x-s-common", ""),
        "X-B3-Traceid": signs.get("x-b3-traceid", ""),
    }


def build_headers(cookie: str, payload: dict[str, Any]) -> dict[str, str]:
    """构造小红书详情接口请求头。"""

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://www.xiaohongshu.com",
        "pragma": "no-cache",
        "referer": "https://www.xiaohongshu.com/",
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": user_agent(),
        "Cookie": cookie,
    }
    headers.update(sign_headers(XHS_NOTE_DETAIL_URI, payload, cookie))
    return headers


def fetch_note_card(note: NoteUrlInfo, cookie: str) -> dict[str, Any]:
    """用最新 Cookie 请求小红书笔记详情接口。"""

    payload: dict[str, Any] = {
        "source_note_id": note.note_id,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": 1},
        "xsec_source": note.xsec_source or "pc_share",
        "xsec_token": note.xsec_token,
    }
    response = requests.post(
        f"{XHS_HOST}{XHS_NOTE_DETAIL_URI}",
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        headers=build_headers(cookie, payload),
        timeout=30,
    )
    if response.status_code in (461, 471):
        raise FetchError("小红书登录态失效或需要验证，请先运行 `uv run maintain-xhs-login`。")
    response.raise_for_status()

    result = response.json()
    if not result.get("success"):
        raise FetchError(f"小红书接口返回失败：{result}")

    items = result.get("data", {}).get("items") or []
    if not items:
        raise FetchError(f"小红书接口未返回笔记详情：{result}")

    return items[0].get("note_card") or {}


def image_url_from_item(image: dict[str, Any]) -> str | None:
    """从小红书图片对象中取可访问的图片地址。"""

    for key in XHS_IMAGE_URL_KEYS:
        value = image.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    info_list = image.get("info_list") or []
    if isinstance(info_list, list):
        for info in reversed(info_list):
            if not isinstance(info, dict):
                continue
            for key in XHS_IMAGE_URL_KEYS:
                value = info.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def extract_images(note_card: dict[str, Any]) -> list[ImageRef]:
    """从笔记详情中提取图片列表。"""

    images: list[ImageRef] = []
    seen: set[str] = set()
    for image in note_card.get("image_list") or []:
        if not isinstance(image, dict):
            continue
        image_url = image_url_from_item(image)
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        images.append(ImageRef(url=image_url, alt=image.get("trace_id") or image.get("file_id")))
    return images


def fetch_xiaohongshu_post(url: str) -> FetchedPost:
    """抓取小红书笔记正文、标题和图片。"""

    note = parse_note_url(url)
    cookie = read_cookie_from_browser_state()
    note_card = fetch_note_card(note, cookie)

    return FetchedPost(
        source="xiaohongshu",
        url=note.url,
        title=note_card.get("title") or None,
        posted_at=None,
        body=note_card.get("desc") or None,
        images=extract_images(note_card),
    )


async def fetch_xiaohongshu_post_async(url: str) -> FetchedPost:
    """在 async MCP server 中抓取小红书笔记正文、标题和图片。"""

    note = parse_note_url(url)
    cookie = await read_cookie_from_browser_state_async()
    note_card = await asyncio.to_thread(fetch_note_card, note, cookie)

    return FetchedPost(
        source="xiaohongshu",
        url=note.url,
        title=note_card.get("title") or None,
        posted_at=None,
        body=note_card.get("desc") or None,
        images=extract_images(note_card),
    )
