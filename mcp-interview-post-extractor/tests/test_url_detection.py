from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from interview_crawl_mcp.server import FetchError, detect_platform_url


XHS_LONG_URL = (
    "https://www.xiaohongshu.com/discovery/item/6a4fa1b90000000015027e25"
    "?xsec_source=app_share&xsec_token=token"
)
XHS_SHORT_URL = "http://xhslink.com/o/2tlreA4Ltrj"
XHS_CN_SHORT_URL = "https://xhslink.cn/o/7dwBtcIfSwE"


class UrlDetectionTest(unittest.TestCase):
    def test_detects_xiaohongshu_long_url_without_network(self) -> None:
        with patch("interview_crawl_mcp.server.requests.get") as mock_get:
            platform, url = detect_platform_url(f"copy this {XHS_LONG_URL}")

        self.assertEqual(platform, "xiaohongshu")
        self.assertEqual(url, XHS_LONG_URL)
        mock_get.assert_not_called()

    def test_resolves_xiaohongshu_shortlink(self) -> None:
        response = Mock()
        response.history = []
        response.url = XHS_LONG_URL
        response.raise_for_status.return_value = None

        with patch("interview_crawl_mcp.server.requests.get", return_value=response) as mock_get:
            platform, url = detect_platform_url(f"QQ share text {XHS_SHORT_URL}")

        self.assertEqual(platform, "xiaohongshu")
        self.assertEqual(url, XHS_LONG_URL)
        response.close.assert_called_once_with()
        mock_get.assert_called_once()

    def test_resolves_xiaohongshu_cn_shortlink(self) -> None:
        response = Mock()
        response.history = []
        response.url = XHS_LONG_URL
        response.raise_for_status.return_value = None

        with patch("interview_crawl_mcp.server.requests.get", return_value=response) as mock_get:
            platform, url = detect_platform_url(f"copy this {XHS_CN_SHORT_URL}")

        self.assertEqual(platform, "xiaohongshu")
        self.assertEqual(url, XHS_LONG_URL)
        response.close.assert_called_once_with()
        mock_get.assert_called_once()

    def test_keeps_note_url_before_login_redirect(self) -> None:
        """笔记地址之后即使跳到登录页，也应保留中间地址中的 token。"""

        response = Mock()
        response.url = "https://www.xiaohongshu.com/login?redirectPath=example"
        response.history = [Mock(url=XHS_CN_SHORT_URL), Mock(url=XHS_LONG_URL)]

        with patch("interview_crawl_mcp.server.requests.get", return_value=response) as mock_get:
            platform, url = detect_platform_url(f"分享文本 {XHS_CN_SHORT_URL} 来围观")

        self.assertEqual(platform, "xiaohongshu")
        self.assertEqual(url, XHS_LONG_URL)
        mock_get.assert_called_once()
        response.close.assert_called_once_with()

    def test_rejects_redirect_without_note_token(self) -> None:
        """跳转链没有有效笔记地址时，直接报告短链接解析失败。"""

        response = Mock()
        response.url = "https://www.xiaohongshu.com/login?redirectPath=example"
        response.history = [Mock(url=XHS_CN_SHORT_URL)]
        response.raise_for_status.return_value = None

        with patch("interview_crawl_mcp.server.requests.get", return_value=response) as mock_get:
            with self.assertRaisesRegex(FetchError, "未找到带 xsec_token 的笔记地址"):
                detect_platform_url(XHS_CN_SHORT_URL)

        mock_get.assert_called_once()
        response.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
