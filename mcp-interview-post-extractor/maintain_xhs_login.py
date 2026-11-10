from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path.cwd()
DEFAULT_BROWSER_DATA_DIR = PROJECT_ROOT / ".browser_data" / "xhs"
XHS_INDEX_URL = "https://www.xiaohongshu.com"
XHS_PROFILE_SELECTOR = "xpath=//a[contains(@href, '/user/profile/')]//span[text()='我']"


def ensure_src_on_path() -> None:
    """源码运行时补充 src 路径，打包安装后通常不需要。"""

    src_dir = PROJECT_ROOT / "src"
    if not src_dir.exists():
        return
    src_path = str(src_dir)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)


def user_agent() -> str:
    """返回登录态维护时使用的桌面浏览器 UA。"""

    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )


def open_browser_context(user_data_dir: Path, *, headless: bool):
    """用当前项目的 profile 打开 Playwright 持久浏览器上下文。"""

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


def login_state_is_valid(user_data_dir: Path) -> bool:
    """检查当前项目中的小红书登录态是否可用。"""

    ensure_src_on_path()
    from interview_crawl_mcp.fetch_xiaohongshu import cookies_to_string, has_required_cookie

    if not user_data_dir.exists() or not any(user_data_dir.iterdir()):
        return False

    playwright = None
    context = None
    try:
        playwright, context = open_browser_context(user_data_dir, headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(XHS_INDEX_URL, wait_until="domcontentloaded", timeout=60_000)

        logged_in = page.locator(XHS_PROFILE_SELECTOR).first.is_visible(timeout=5_000)
        cookie = cookies_to_string(context.cookies([XHS_INDEX_URL]))
        return logged_in and has_required_cookie(cookie)
    except Exception:
        return False
    finally:
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()


def refresh_login_state(user_data_dir: Path, login_timeout: int) -> None:
    """打开有头浏览器，让用户扫码或验证后保存登录态。"""

    from interview_crawl_mcp.fetch_xiaohongshu import FetchError
    from interview_crawl_mcp.fetch_xiaohongshu import cookies_to_string, has_required_cookie

    user_data_dir.mkdir(parents=True, exist_ok=True)
    playwright, context = open_browser_context(user_data_dir, headless=False)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(XHS_INDEX_URL, wait_until="domcontentloaded", timeout=60_000)
        print("请在打开的小红书浏览器窗口中登录；检测到左侧“我”入口后会自动保存。")

        deadline = time.monotonic() + login_timeout
        while time.monotonic() < deadline:
            if page.locator(XHS_PROFILE_SELECTOR).first.is_visible(timeout=500):
                cookie = cookies_to_string(context.cookies([XHS_INDEX_URL]))
                if has_required_cookie(cookie):
                    print(f"小红书登录态已保存到：{user_data_dir}")
                    return
            time.sleep(1)

        raise FetchError(f"等待小红书登录超时：{login_timeout} 秒。")
    finally:
        context.close()
        playwright.stop()


def maintain_login_state(user_data_dir: Path, login_timeout: int, *, force: bool = False) -> None:
    """有效就跳过，无效就打开浏览器刷新登录态。"""

    if not force and login_state_is_valid(user_data_dir):
        print(f"小红书登录态有效：{user_data_dir}")
        return
    refresh_login_state(user_data_dir, login_timeout)


def main() -> None:
    """命令行入口：维护 my_mcp/.browser_data/xhs 登录态。"""

    ensure_src_on_path()
    from interview_crawl_mcp.fetch_xiaohongshu import FetchError

    parser = argparse.ArgumentParser(description="维护小红书登录态。")
    parser.add_argument(
        "--user-data-dir",
        default=str(DEFAULT_BROWSER_DATA_DIR),
        help=f"浏览器登录态目录，默认 {DEFAULT_BROWSER_DATA_DIR}",
    )
    parser.add_argument("--login-timeout", type=int, default=180, help="等待登录秒数。")
    parser.add_argument("--force", action="store_true", help="跳过有效性检查，直接打开浏览器重新登录。")
    args = parser.parse_args()

    try:
        maintain_login_state(Path(args.user_data_dir), args.login_timeout, force=args.force)
    except FetchError as exc:
        raise SystemExit(f"维护小红书登录态失败：{exc}") from exc


if __name__ == "__main__":
    main()
