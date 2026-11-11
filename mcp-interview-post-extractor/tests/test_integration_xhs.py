from __future__ import annotations

import asyncio
import os
import time
import unittest
from contextlib import ExitStack
from functools import wraps
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import BrowserContext, Page, Playwright

from interview_crawl_mcp import fetch_xiaohongshu as xhs
from interview_crawl_mcp.server import detect_platform_url, parse_data_image, user_agent


# 默认使用完整链接；XHS_TEST_URL 环境变量支持长链接、短链接或完整分享文本。
XIAOHONGSHU_URL = "https://xhslink.cn/o/6uumtlHDOAv"
# XIAOHONGSHU_URL = "https://www.xiaohongshu.com/discovery/item/6a54ea7a000000000f02ade5?app_platform=android&ignoreEngage=true&app_version=9.54.0&share_from_user_hidden=true&xsec_source=app_share&type=normal&xsec_token=CBgjE71f2wiBgInwiOFZpyrWXOu923Hg8gexQ_G2bM8T8%3D&author_share=1&xhsshare=CopyLink&shareRedId=OD5IQTQ8OUo2NzUyOTgwNjk1OTk7PjZP&apptime=1794381592&share_id=ceb3277a079a4a7baba1af94a0f27ff8&share_channel=copy_link&track_code=6uumtlHDOAv"


def trace_async_stage(label, original):
    """包装异步阶段，仅打印状态和耗时，保留原函数行为和异常。"""

    @wraps(original)
    async def traced(*args, **kwargs):
        started_at = time.monotonic()
        print(f"[XHS 测试] 开始：{label}", flush=True)
        try:
            result = await original(*args, **kwargs)
        except BaseException as exc:
            print(
                f"[XHS 测试] 失败或中断：{label}，{type(exc).__name__}，"
                f"耗时 {time.monotonic() - started_at:.1f} 秒",
                flush=True,
            )
            raise
        print(
            f"[XHS 测试] 完成：{label}，耗时 {time.monotonic() - started_at:.1f} 秒",
            flush=True,
        )
        return result

    return traced


def print_url_summary(url: str) -> None:
    """打印地址结构和参数名称，隐藏 token 等参数值。"""

    parsed = urlsplit(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    print(
        f"[XHS 测试] 地址：{parsed.scheme}://{parsed.netloc}{parsed.path}；"
        f"参数名：{', '.join(params) or '（无）'}；"
        f"含非空 xsec_token：{bool((params.get('xsec_token') or [''])[0])}",
        flush=True,
    )


def trace_sync_stage(label, original):
    """包装同步阶段，不打印 Cookie、签名或完整分享链接，不增加请求或重试。"""

    @wraps(original)
    def traced(*args, **kwargs):
        started_at = time.monotonic()
        print(f"[XHS 测试] 开始：{label}", flush=True)
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            print(
                f"[XHS 测试] 失败或中断：{label}，{type(exc).__name__}，"
                f"耗时 {time.monotonic() - started_at:.1f} 秒",
                flush=True,
            )
            raise
        status = f"，HTTP {result.status_code}" if isinstance(result, xhs.requests.Response) else ""
        if isinstance(result, xhs.requests.Response):
            # 只观察已经发生的请求，不额外访问；包含中间跳转和最终响应地址。
            for response in [*result.history, result]:
                print(f"[XHS 测试] 响应状态：HTTP {response.status_code}", flush=True)
                print_url_summary(response.url)
        print(
            f"[XHS 测试] 完成：{label}{status}，耗时 {time.monotonic() - started_at:.1f} 秒",
            flush=True,
        )
        return result

    return traced


class XiaohongshuFetchIntegrationTest(unittest.TestCase):
    """验证小红书抓取及图片地址访问，不启动 MCP，不保存图片或调用 OCR。"""

    def check_image_access(self, image: xhs.ImageRef, referer: str) -> None:
        """使用与正式下载相同的请求头检查图片响应，仅接收响应头后关闭连接。"""

        if image.url.startswith("data:image/"):
            parsed = parse_data_image(image.url)
            self.assertIsNotNone(parsed, "图片 data URL 格式无效")
            self.assertTrue(parsed[1], "图片 data URL 内容为空")
            print("[XHS 测试] 内嵌图片格式有效，无需网络请求", flush=True)
            return

        # GET 与正式图片下载一致；stream=True 不读取响应体，也不创建临时文件。
        # 每张图片只请求一次，不用 HEAD 失败后再 GET 的方式重复探测。
        with xhs.requests.get(
            image.url,
            headers={"User-Agent": user_agent(), "Referer": referer},
            stream=True,
            timeout=(10, 15),
        ) as response:
            content_type = response.headers.get("Content-Type", "")
            print(
                f"[XHS 测试] 图片 HTTP {response.status_code}；"
                f"Content-Type：{content_type or '（未提供）'}；"
                f"Content-Length：{response.headers.get('Content-Length', '（未提供）')}",
                flush=True,
            )
            print_url_summary(response.url)
            response.raise_for_status()
            self.assertIn(response.status_code, (200, 206), "图片没有返回可用的成功响应")
            media_type = content_type.split(";", 1)[0].strip().lower()
            self.assertTrue(
                media_type.startswith("image/") or media_type == "application/octet-stream",
                "图片地址返回的不是图片或二进制内容，可能是登录页或错误页",
            )

    def test_fetch_xiaohongshu_post(self) -> None:
        """单次真实抓取；失败直接抛出，通过测试内包装定位等待阶段。"""

        profile = xhs.DEFAULT_BROWSER_DATA_DIR
        if not profile.exists() or not any(profile.iterdir()):
            self.skipTest("小红书登录态不存在，请先运行 `uv run maintain-xhs-login`。")

        # 所有包装都调用原函数，退出测试后恢复，不修改正式流程。
        async_stages = [
            (xhs, "read_cookie_from_browser_state_async", "读取登录态完整流程"),
            (xhs, "open_browser_context_async", "启动 Playwright 和浏览器"),
            (Page, "goto", "打开小红书首页"),
            (BrowserContext, "cookies", "读取 Cookie"),
            (BrowserContext, "close", "关闭浏览器上下文"),
            (Playwright, "stop", "停止 Playwright"),
        ]
        sync_stages = [
            (xhs.requests, "get", "请求短链接并跟随跳转"),
            (xhs, "fetch_note_card", "获取笔记详情完整流程"),
            (xhs, "sign_headers", "生成请求签名"),
            (xhs.requests, "post", "请求笔记详情接口"),
            (xhs, "extract_images", "提取图片地址"),
        ]
        with ExitStack() as stack:
            for target, name, label in async_stages:
                stack.enter_context(patch.object(target, name, trace_async_stage(label, getattr(target, name))))
            for target, name, label in sync_stages:
                stack.enter_context(patch.object(target, name, trace_sync_stage(label, getattr(target, name))))
            # 复用正式 MCP 入口的链接解析逻辑，不启动 MCP 服务。
            share_text = os.environ.get("XHS_TEST_URL", XIAOHONGSHU_URL)
            platform, resolved_url = trace_sync_stage(
                "提取分享链接并解析短链接跳转", detect_platform_url
            )(share_text)
            self.assertEqual(platform, "xiaohongshu", "请提供小红书分享链接")
            print_url_summary(resolved_url)
            post = asyncio.run(xhs.fetch_xiaohongshu_post_async(resolved_url))

        print(f"\n标题：{post.title or '（无标题）'}", flush=True)
        print(f"正文长度：{len(post.body or '')}；图片数量：{len(post.images)}", flush=True)
        self.assertEqual(post.source, "xiaohongshu")
        self.assertEqual(post.url, resolved_url)
        # 小红书允许纯图片笔记，因此不要求正文一定非空。
        self.assertTrue((post.body or "").strip() or post.images, "未获取到正文或图片信息")

        # 顺序访问所有图片地址，沿用正式流程中图片之间一秒的间隔。
        for index, image in enumerate(post.images, start=1):
            if index > 1:
                time.sleep(1)
            trace_sync_stage(
                f"检查图片地址 {index}/{len(post.images)}", self.check_image_access
            )(image, post.url)
        print(
            f"[XHS 测试] 全部完成：笔记抓取成功，已检查 {len(post.images)} 张图片地址；"
            "未保存图片，未调用 OCR。图片响应体的下载完整性不在本次检查范围内。",
            flush=True,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
