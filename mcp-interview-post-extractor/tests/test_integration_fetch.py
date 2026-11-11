from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import textwrap
import time
import unittest
from contextlib import closing
from datetime import timedelta
from typing import ClassVar

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from interview_crawl_mcp.fetch_xiaohongshu import DEFAULT_BROWSER_DATA_DIR


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


NOWCODER_URL = "https://www.nowcoder.com/share/jump/1221189251783869391765"
XIAOHONGSHU_URL = "https://xhslink.cn/o/6uumtlHDOAv"
# XIAOHONGSHU_URL = "https://www.xiaohongshu.com/discovery/item/6a54ea7a000000000f02ade5?app_platform=android&ignoreEngage=true&app_version=9.54.0&share_from_user_hidden=true&xsec_source=app_share&type=normal&xsec_token=CBgjE71f2wiBgInwiOFZpyrWXOu923Hg8gexQ_G2bM8T8%3D&author_share=1&xhsshare=CopyLink&shareRedId=OD5IQTQ8OUo2NzUyOTgwNjk1OTk7PjZP&apptime=1794381592&share_id=ceb3277a079a4a7baba1af94a0f27ff8&share_channel=copy_link&track_code=6uumtlHDOAv"


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_tcp_port(host: str, port: int, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None

    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)

    raise TimeoutError(f"MCP HTTP server did not listen on {host}:{port}: {last_error}")


def print_mcp_result(name: str, result) -> None:
    """Print the MCP tool result preview for real integration-test runs."""

    text_items = [item for item in result if getattr(item, "type", None) == "text"]
    text = text_items[0].text if text_items else ""
    preview = textwrap.shorten(text.replace("\n", " "), width=1000, placeholder="...")

    print(f"\n===== {name} =====")
    print(f"text_length: {len(text)}")
    print(f"tool_items: {len(result)}")
    print("text_preview:")
    print(preview)


class FetchIntegrationTest(unittest.TestCase):
    server_process: ClassVar[subprocess.Popen[str] | None] = None
    mcp_url: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        host = "127.0.0.1"
        port = find_free_port()
        cls.mcp_url = f"http://{host}:{port}/mcp"

        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        cls.server_process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "interview_crawl_mcp.server",
                "--host",
                host,
                "--port",
                str(port),
                "--transport",
                "streamable-http",
                "--path",
                "/mcp",
                "--log-level",
                "DEBUG",
            ],
            # 继承终端输出，实时显示服务端日志，避免未读取的 PIPE 堵塞。
            env=env,
        )

        try:
            wait_for_tcp_port(host, port)
        except Exception:
            cls._stop_server()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stop_server()

    @classmethod
    def _stop_server(cls) -> None:
        process = cls.server_process
        cls.server_process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    async def call_fetch_tool(self, share_text: str):
        """调用真实 MCP 服务，并打印初始化和工具调用阶段的耗时。"""

        started_at = time.monotonic()
        print("\n[MCP 测试] 开始连接服务并初始化会话", flush=True)
        # 工具执行期间 SSE 可能没有新数据，不能使用 httpx 默认的 5 秒读取超时。
        async with httpx.AsyncClient(
            trust_env=False, timeout=httpx.Timeout(30.0, read=300.0)
        ) as http_client:
            async with streamable_http_client(
                self.mcp_url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                # 同时限制 MCP 请求等待时间，防止传输中断后一直等不到响应。
                async with ClientSession(
                    read_stream, write_stream, read_timeout_seconds=timedelta(seconds=300)
                ) as session:
                    await session.initialize()
                    print(
                        f"[MCP 测试] 会话初始化完成，耗时 {time.monotonic() - started_at:.1f} 秒",
                        flush=True,
                    )
                    tool_started_at = time.monotonic()
                    print("[MCP 测试] 开始调用 fetch_interview_post，等待服务端返回", flush=True)
                    try:
                        result = await session.call_tool(
                            "fetch_interview_post",
                            {"share_text": share_text},
                        )
                    finally:
                        # 失败或手动中断时也打印等待时长，便于区分卡住的阶段。
                        print(
                            f"[MCP 测试] 工具调用结束，耗时 {time.monotonic() - tool_started_at:.1f} 秒",
                            flush=True,
                        )
                    return result.content

    def test_fetch_nowcoder_post_via_http_mcp(self) -> None:
        """Integration test: fetch a NowCoder post through HTTP MCP transport."""

        result = asyncio.run(self.call_fetch_tool(NOWCODER_URL))
        print_mcp_result("nowcoder http mcp", result)

        text = result[0].text
        self.assertIn("正文：", text)
        self.assertIn("图片ocr结果:", text)
        self.assertNotIn("image_paths:", text)
        self.assertNotIn("Fetch failed:", text)

    def test_fetch_xiaohongshu_post_via_http_mcp(self) -> None:
        """Integration test: fetch a Xiaohongshu note through HTTP MCP transport."""

        if not DEFAULT_BROWSER_DATA_DIR.exists() or not any(DEFAULT_BROWSER_DATA_DIR.iterdir()):
            self.skipTest("小红书登录态不存在，请先运行 `uv run maintain-xhs-login`。")

        result = asyncio.run(self.call_fetch_tool(XIAOHONGSHU_URL))
        print_mcp_result("xiaohongshu http mcp", result)

        text = result[0].text
        self.assertIn("正文：", text)
        self.assertIn("图片ocr结果:", text)
        self.assertNotIn("image_paths:", text)
        self.assertNotIn("Fetch failed:", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
