from __future__ import annotations

import os
import unittest
from pathlib import Path

from interview_crawl_mcp.ocr import ocr_image_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# OCR_TEST_IMAGE = PROJECT_ROOT / "面经图片" / "小红书" / "腾讯 ai 应用开发 一面" / "2.webp"
OCR_TEST_IMAGE = PROJECT_ROOT / "面经图片" / "牛客" / "网易互娱-AI技术面经" / "7.png"


class OcrIntegrationTest(unittest.TestCase):
    def test_ocr_image_file_with_tencent_cloud(self) -> None:
        """集成测试：使用腾讯云真实高精度 OCR 识别本地图片。"""

        if not os.environ.get("TENCENTCLOUD_SECRET_ID") or not os.environ.get(
            "TENCENTCLOUD_SECRET_KEY"
        ):
            self.skipTest("请先设置 TENCENTCLOUD_SECRET_ID 和 TENCENTCLOUD_SECRET_KEY。")
        if not OCR_TEST_IMAGE.exists():
            self.skipTest(f"测试图片不存在：{OCR_TEST_IMAGE}")

        text = ocr_image_file(OCR_TEST_IMAGE)
        print(f"\n===== tencent ocr =====\n{text}\n========================\n")

        self.assertTrue(text.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
