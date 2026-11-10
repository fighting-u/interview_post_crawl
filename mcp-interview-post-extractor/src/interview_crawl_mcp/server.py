from __future__ import annotations

import asyncio
import argparse
import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from .fetch_nowcoder import FetchError as NowcoderFetchError
from .fetch_nowcoder import FetchedPost as NowcoderPost
from .fetch_nowcoder import fetch_nowcoder_post
from .fetch_xiaohongshu import FetchError as XhsFetchError
from .fetch_xiaohongshu import FetchedPost as XhsPost
from .fetch_xiaohongshu import fetch_xiaohongshu_post
from .fetch_xiaohongshu import fetch_xiaohongshu_post_async
from .ocr import ocr_image_base64


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 7148

mcp = FastMCP("interview-crawl", host=DEFAULT_HOST, port=DEFAULT_PORT)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERVIEW_DIR = PROJECT_ROOT / "面经"
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
XHS_SHORTLINK_HOSTS = {
    "xhslink.cn",
    "www.xhslink.cn",
    "xhslink.com",
    "www.xhslink.com",
}


class FetchError(RuntimeError):
    """MCP 编排层统一使用的抓取异常。"""


@dataclass(frozen=True)
class ImageRef:
    """server 统一处理的图片引用。"""

    url: str
    alt: str | None = None


@dataclass(frozen=True)
class FetchedPost:
    """server 统一处理的平台抓取结果。"""

    source: str
    url: str
    title: str | None
    posted_at: str | None
    body: str | None
    images: list[ImageRef]


def user_agent() -> str:
    """返回下载图片时使用的桌面浏览器 UA。"""

    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )


def clean_url(url: str) -> str:
    """去掉分享文本里常见的中文标点和括号尾巴。"""

    return url.rstrip("，。；;、)]）\"'")


def resolve_xhs_shortlink(url: str) -> str:
    """Follow an xhslink.com share URL and return the final Xiaohongshu URL."""

    response: requests.Response | None = None
    try:
        response = requests.get(
            url,
            allow_redirects=True,
            headers={
                "User-Agent": user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            stream=True,
            timeout=15,
        )
        response.raise_for_status()
        return response.url
    except requests.RequestException as exc:
        raise FetchError(f"Failed to resolve Xiaohongshu short link {url}: {exc}") from exc
    finally:
        if response is not None:
            response.close()


def detect_platform_url(share_text: str) -> tuple[str, str]:
    """从混合分享文本中找出牛客或小红书链接。"""

    for match in URL_RE.finditer(share_text):
        url = clean_url(match.group(0))
        host = urlparse(url).netloc.lower()
        if host.endswith("nowcoder.com"):
            return "nowcoder", url
        if host in XHS_SHORTLINK_HOSTS:
            url = resolve_xhs_shortlink(url)
            host = urlparse(url).netloc.lower()
        if host.endswith("xiaohongshu.com"):
            return "xiaohongshu", url
    raise FetchError("未找到支持的链接，请提供牛客或小红书分享链接。")


def normalize_post(post: NowcoderPost | XhsPost) -> FetchedPost:
    """把平台模块返回结果转换成 server 自己的简单结构。"""

    return FetchedPost(
        source=post.source,
        url=post.url,
        title=post.title,
        posted_at=post.posted_at,
        body=post.body,
        images=[ImageRef(url=image.url, alt=image.alt) for image in post.images],
    )


def parse_data_image(data_url: str) -> tuple[str, str] | None:
    """解析 data:image/...;base64,... 形式的图片。"""

    match = re.match(r"data:(image/[A-Za-z0-9.+-]+);base64,(.*)", data_url, re.DOTALL)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def image_to_base64(image: ImageRef, referer: str) -> str:
    """Download one image and return base64 without saving it locally."""

    if image.url.startswith("data:image/"):
        parsed = parse_data_image(image.url)
        if not parsed:
            raise FetchError("图片 data URL 不是有效的 base64 内容。")
        return parsed[1]

    response = requests.get(
        image.url,
        timeout=30,
        headers={"User-Agent": user_agent(), "Referer": referer},
    )
    response.raise_for_status()
    return base64.b64encode(response.content).decode("utf-8")


def format_post_text(post: FetchedPost, image_ocr_texts: list[str]) -> str:
    """把抓取结果格式化成 MCP 返回的文本内容。"""

    return "\n".join(
        [
            "正文：",
            post.body or "",
            "",
            "图片ocr结果:",
            "\n".join(text for text in image_ocr_texts if text.strip()),
        ]
    )


def safe_markdown_filename(filename: str) -> str:
    """把用户给的文件名清洗成当前目录下可用的 md 文件名。"""

    name = Path(filename).name.strip()
    name = INVALID_FILENAME_CHARS_RE.sub("_", name).strip(" .")
    if not name:
        name = "未命名"
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    return name


def next_markdown_path(filename: str) -> Path:
    """生成不覆盖已有文件的面经保存路径。"""

    INTERVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = INTERVIEW_DIR / safe_markdown_filename(filename)
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = INTERVIEW_DIR / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def fetch_post(share_text: str) -> FetchedPost:
    """按链接域名分发到对应平台抓取模块。"""

    platform, url = detect_platform_url(share_text)
    if platform == "nowcoder":
        return normalize_post(fetch_nowcoder_post(url))
    if platform == "xiaohongshu":
        return normalize_post(fetch_xiaohongshu_post(url))
    raise FetchError(f"暂不支持的平台：{platform}")


async def fetch_post_async(share_text: str) -> FetchedPost:
    """在 MCP async tool 中按链接域名分发到对应平台抓取模块。"""

    platform, url = await asyncio.to_thread(detect_platform_url, share_text)
    if platform == "nowcoder":
        return normalize_post(await asyncio.to_thread(fetch_nowcoder_post, url))
    if platform == "xiaohongshu":
        return normalize_post(await fetch_xiaohongshu_post_async(url))
    raise FetchError(f"暂不支持的平台：{platform}")


@mcp.tool()
async def fetch_interview_post(share_text: str) -> list[TextContent]:
    """MCP 工具：抓取牛客或小红书面经分享内容。"""

    try:
        post = await fetch_post_async(share_text)
        image_ocr_texts: list[str] = []
        image_ocr_errors: list[str] = []

        for index, image in enumerate(post.images, start=1):
            if post.source == "xiaohongshu" and index > 1:
                await asyncio.sleep(1)
            try:
                image_base64 = await asyncio.to_thread(image_to_base64, image, post.url)
                image_ocr_texts.append(await asyncio.to_thread(ocr_image_base64, image_base64))
            except Exception as exc:
                image_ocr_errors.append(f"image {index} failed: {exc}")

        text = format_post_text(post, image_ocr_texts)
        if image_ocr_errors:
            text = f"{text}\n\nimage_ocr_errors:\n" + "\n".join(image_ocr_errors)

        return [TextContent(type="text", text=text)]
    except (
        requests.RequestException,
        json.JSONDecodeError,
        FetchError,
        NowcoderFetchError,
        XhsFetchError,
    ) as exc:
        return [TextContent(type="text", text=f"Fetch failed: {exc}")]


@mcp.tool()
def write_interview_markdown(filename: str, content: str) -> list[TextContent]:
    """MCP 工具：把 Markdown 内容写入项目固定的面经目录。"""

    path = next_markdown_path(filename)
    path.write_text(content, encoding="utf-8")
    return [TextContent(type="text", text=f"Markdown saved: {path}")]


def main() -> None:
    """启动常驻 HTTP MCP server。"""

    parser = argparse.ArgumentParser(description="Run interview-crawl as an HTTP MCP server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"HTTP bind host. Default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP bind port. Default: {DEFAULT_PORT}")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "sse"],
        default="streamable-http",
        help="HTTP transport. Default: streamable-http",
    )
    parser.add_argument("--path", default="/mcp", help="Streamable HTTP path. Default: /mcp")
    parser.add_argument("--sse-path", default="/sse", help="SSE endpoint path. Default: /sse")
    parser.add_argument("--message-path", default="/messages/", help="SSE message path. Default: /messages/")
    parser.add_argument(
        "--stateless-http",
        action="store_true",
        help="Disable Streamable HTTP session tracking for clients that reuse closed sessions.",
    )
    args = parser.parse_args()

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.streamable_http_path = args.path
    mcp.settings.sse_path = args.sse_path
    mcp.settings.message_path = args.message_path
    mcp.settings.stateless_http = args.stateless_http
    endpoint_path = args.path if args.transport == "streamable-http" else args.sse_path
    session_mode = "stateless" if args.stateless_http else "stateful"
    print(f"MCP endpoint: http://{args.host}:{args.port}{endpoint_path} ({session_mode})", flush=True)
    mcp.run(args.transport)


if __name__ == "__main__":
    main()
