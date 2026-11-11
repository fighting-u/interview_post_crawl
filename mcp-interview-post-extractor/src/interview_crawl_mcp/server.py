from __future__ import annotations

import asyncio
import argparse
import base64
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

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
from .runtime_logs import install_output_log


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

# 服务端阶段日志，不记录分享文本、Cookie、签名或图片内容。
logger = logging.getLogger(__name__)

mcp = FastMCP("interview-crawl", host=DEFAULT_HOST, port=DEFAULT_PORT)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = PROJECT_ROOT.parent
INTERVIEW_DIR = OUTPUT_ROOT / "面经"
IMAGE_DIR = OUTPUT_ROOT / "面经图片"
BEIJING_TIME = timezone(timedelta(hours=8))
_uuid_lock = threading.Lock()
_uuid_timestamp = -1
_uuid_random = 0
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
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
    """跟随小红书短链接跳转，从跳转链中取出带 token 的笔记地址。"""

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
        # 未携带 Cookie 时，笔记页可能继续跳到登录页；不能只取最终地址。
        for hop in [*response.history, response]:
            parsed = urlparse(hop.url)
            host = (parsed.hostname or "").lower()
            if host != "xiaohongshu.com" and not host.endswith(".xiaohongshu.com"):
                continue
            if not re.fullmatch(r"/(?:discovery/item|explore)/[^/]+/?", parsed.path):
                continue
            token = (parse_qs(parsed.query).get("xsec_token") or [""])[0]
            if token:
                return hop.url
        response.raise_for_status()
        raise FetchError("小红书短链接跳转中未找到带 xsec_token 的笔记地址，请重新复制分享链接。")
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


def new_uuid7() -> str:
    """生成 UUIDv7；同一进程内同毫秒或时钟回拨时保持递增。"""

    global _uuid_timestamp, _uuid_random
    with _uuid_lock:
        timestamp = time.time_ns() // 1_000_000
        if timestamp > _uuid_timestamp:
            _uuid_timestamp = timestamp
            _uuid_random = secrets.randbits(74)
        else:
            _uuid_random += 1
            if _uuid_random >= 1 << 74:
                _uuid_timestamp += 1
                _uuid_random = 0
        value = (_uuid_timestamp << 80) | (7 << 76)
        value |= (_uuid_random >> 62) << 64
        value |= (2 << 62) | (_uuid_random & ((1 << 62) - 1))
        return str(UUID(int=value))


def image_to_base64(image: ImageRef, referer: str, save_stem: Path) -> str:
    """下载图片并保存原始字节，返回供 OCR 使用的 base64。"""

    if image.url.startswith("data:image/"):
        parsed = parse_data_image(image.url)
        if not parsed:
            raise FetchError("图片 data URL 不是有效的 base64 内容。")
        mime_type, encoded = parsed
        content = base64.b64decode(encoded)
    else:
        with requests.get(
            image.url,
            timeout=30,
            headers={"User-Agent": user_agent(), "Referer": referer},
        ) as response:
            response.raise_for_status()
            content = response.content
            mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    extensions = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "image/avif": ".avif", "image/bmp": ".bmp",
        "image/svg+xml": ".svg", "image/tiff": ".tiff",
    }
    extension = extensions.get(mime_type)
    if extension is None:
        suffix = Path(urlparse(image.url).path).suffix.lower()
        extension = suffix if suffix in extensions.values() else ".img"
    save_stem.with_suffix(extension).write_bytes(content)
    return base64.b64encode(content).decode("utf-8")


def format_post_text(
    post: FetchedPost, image_ocr_texts: list[str], fetched_at: str, save_dir: Path
) -> str:
    """把抓取结果格式化成 MCP 返回的文本内容。"""

    return "\n".join(
        [
            f"帖子发布时间：{post.posted_at or '未知'}",
            f"抓取时间：{fetched_at}",
            f"你应保存文件的地址为：{save_dir}",
            "",
            "下面是正文的文字和图片内容，",
            "正文文字：",
            post.body or "",
            "",
            "正文中的图片ocr结果:",
            "\n".join(text for text in image_ocr_texts if text.strip()),
        ]
    )


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

    started_at = time.monotonic()
    logger.debug("[抓取] 开始解析分享链接")
    platform, url = await asyncio.to_thread(detect_platform_url, share_text)
    logger.debug("[抓取] 链接解析完成，平台=%s，耗时=%.1f秒", platform, time.monotonic() - started_at)
    logger.debug("[抓取] 开始获取帖子，平台=%s", platform)
    if platform == "nowcoder":
        return normalize_post(await asyncio.to_thread(fetch_nowcoder_post, url))
    if platform == "xiaohongshu":
        return normalize_post(await fetch_xiaohongshu_post_async(url))
    raise FetchError(f"暂不支持的平台：{platform}")


@mcp.tool()
async def fetch_interview_post(share_text: str) -> list[TextContent]:
    """MCP 工具：抓取牛客或小红书面经分享内容。"""

    started_at = time.monotonic()
    fetched_datetime = datetime.now(BEIJING_TIME)
    fetched_at = fetched_datetime.isoformat(timespec="seconds")
    stage = "抓取帖子"
    logger.debug("[MCP抓取] 工具开始执行，事件循环=%s", type(asyncio.get_running_loop()).__name__)
    try:
        post = await fetch_post_async(share_text)
        stage = "保存抓取记录"
        date_path = Path(fetched_datetime.strftime("%Y/%m/%d"))
        image_dir = IMAGE_DIR / date_path / new_uuid7()
        save_dir = INTERVIEW_DIR / date_path
        image_dir.mkdir(parents=True, exist_ok=False)
        save_dir.mkdir(parents=True, exist_ok=True)
        (image_dir / "metadata.json").write_text(
            json.dumps({"url": post.url, "fetched_at": fetched_at}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.debug(
            "[MCP抓取] 帖子抓取完成，正文=%d字符，图片=%d张，累计耗时=%.1f秒",
            len(post.body or ""), len(post.images), time.monotonic() - started_at,
        )
        image_ocr_texts: list[str] = []
        image_ocr_errors: list[str] = []

        for index, image in enumerate(post.images, start=1):
            if post.source == "xiaohongshu" and index > 1:
                await asyncio.sleep(0.3)
            try:
                stage = f"图片{index}/{len(post.images)}下载或转码"
                image_started_at = time.monotonic()
                logger.debug("[MCP抓取] 开始%s", stage)
                image_base64 = await asyncio.to_thread(
                    image_to_base64, image, post.url, image_dir / f"{index:03d}"
                )
                logger.debug("[MCP抓取] %s完成，耗时=%.1f秒", stage, time.monotonic() - image_started_at)
                stage = f"图片{index}/{len(post.images)} OCR"
                ocr_started_at = time.monotonic()
                logger.debug("[MCP抓取] 开始%s", stage)
                ocr_text = await asyncio.to_thread(ocr_image_base64, image_base64)
                image_ocr_texts.append(ocr_text)
                if not ocr_text.strip():
                    logger.debug("[MCP抓取] %s文字为空，非失败结果", stage)
                logger.debug(
                    "[MCP抓取] %s完成，文字=%d字符，耗时=%.1f秒",
                    stage, len(ocr_text), time.monotonic() - ocr_started_at,
                )
            except Exception as exc:
                logger.warning(
                    "[MCP抓取] %s失败，异常类型=%s，详细错误=%s，继续处理后续图片",
                    stage, type(exc).__name__, exc,
                )
                image_ocr_errors.append(f"image {index} failed: {exc}")

        stage = "格式化返回结果"
        text = format_post_text(post, image_ocr_texts, fetched_at, save_dir)
        if image_ocr_errors:
            text = f"{text}\n\nimage_ocr_errors:\n" + "\n".join(image_ocr_errors)

        logger.debug(
            "[MCP抓取] 准备向MCP框架返回结果，文本=%d字符，图片失败=%d张，总耗时=%.1f秒",
            len(text), len(image_ocr_errors), time.monotonic() - started_at,
        )
        return [TextContent(type="text", text=text)]
    except (
        requests.RequestException,
        json.JSONDecodeError,
        FetchError,
        NowcoderFetchError,
        XhsFetchError,
    ) as exc:
        logger.error(
            "[MCP抓取] %s失败，异常类型=%s，准备返回错误结果，耗时=%.1f秒",
            stage, type(exc).__name__, time.monotonic() - started_at,
        )
        return [TextContent(type="text", text=f"Fetch failed: {exc}")]
    except asyncio.CancelledError:
        logger.warning("[MCP抓取] %s期间被取消，耗时=%.1f秒", stage, time.monotonic() - started_at)
        raise
    except Exception as exc:
        logger.error("[MCP抓取] %s出现未处理异常，异常类型=%s", stage, type(exc).__name__)
        raise


def main() -> None:
    """启动常驻 HTTP MCP server。"""

    parser = argparse.ArgumentParser(description="Run interview-crawl as an HTTP MCP server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"HTTP bind host. Default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP bind port. Default: {DEFAULT_PORT}")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="抓取业务日志级别，默认 INFO；排查阶段耗时时使用 DEBUG。",
    )
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
    log_path = install_output_log(PROJECT_ROOT / "logs")
    print(f"运行日志: {log_path}", flush=True)

    # 仅调整业务日志，避免第三方库的 DEBUG 输出淹没阶段日志或打印请求细节。
    # -m 启动时当前模块名为 __main__，因此同时设置当前 logger 和包级 logger。
    logger.setLevel(args.log_level)
    logging.getLogger("interview_crawl_mcp").setLevel(args.log_level)
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
