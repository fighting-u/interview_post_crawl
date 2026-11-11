from __future__ import annotations

import base64
import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)


def ocr_image_base64(image_base64: str) -> str:
    """调用腾讯云高精度 OCR，返回图片中识别出的纯文本。"""

    from tencentcloud.common import credential
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
    from tencentcloud.ocr.v20181119 import ocr_client, models

    secret_id = os.environ["TENCENTCLOUD_SECRET_ID"]
    secret_key = os.environ["TENCENTCLOUD_SECRET_KEY"]
    region = os.environ.get("TENCENTCLOUD_REGION", "ap-guangzhou")

    # 兼容 data URL 形式，腾讯云接口只需要纯 base64 内容。
    if "," in image_base64 and image_base64.lstrip().startswith("data:"):
        image_base64 = image_base64.split(",", 1)[1]

    req = models.GeneralAccurateOCRRequest()
    req.ImageBase64 = image_base64

    client = ocr_client.OcrClient(credential.Credential(secret_id, secret_key), region)
    try:
        resp = client.GeneralAccurateOCR(req)
    except TencentCloudSDKException as exc:
        if exc.get_code() == "FailedOperation.ImageNoText":
            logger.debug("[OCR] 图片未检测到文字，按空文本处理")
            return ""
        raise

    # 高精度 OCR 返回多段文本，这里按接口顺序合并成模型更易消费的纯文本。
    lines = [
        item.DetectedText
        for item in resp.TextDetections or []
        if getattr(item, "DetectedText", "")
    ]
    return "\n".join(lines)


def ocr_image_file(image_path: str | Path) -> str:
    """读取本地图片并复用 base64 OCR 函数，返回识别出的纯文本。"""

    image_base64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
    return ocr_image_base64(image_base64)
