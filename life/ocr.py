"""OCR（图片识别记账）后端。

设计目标
--------
- 可插拔 OCR 引擎：本地 Tesseract（默认）或外部云服务，通过 ``settings.OCR_PROVIDER`` 切换。
- 无外部服务也能跑通「上传图片 → 识别 → 提取 → 确认记账」整条链路：
  测试用 MockOCRProvider（可注入预设文本），本地用 TesseractOCRProvider。
- 识别文本经过 ``extract_receipt_fields`` 提取金额 / 日期 / 商户，供前端预填
  「确认记账」表单，复用既有 Expense 创建逻辑（source="ocr"）。

Provider 接口只有一个方法：``recognize(image_bytes: bytes) -> str``（纯文本）。
"""
import io
import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings

logger = logging.getLogger(__name__)


class OCRException(Exception):
    """OCR 识别失败（引擎缺失 / 图片损坏 / 服务异常）。

    视图层捕获后转为用户友好的提示，不会 500。
    """


class OCRProvider:
    """Base class. Subclasses implement ``recognize``."""

    name = "base"

    def recognize(self, image_bytes: bytes) -> str:  # pragma: no cover
        raise NotImplementedError


class TesseractOCRProvider(OCRProvider):
    """本地 Tesseract OCR（默认引擎）。

    依赖：系统安装 Tesseract 二进制 + Python 包 ``pytesseract`` / ``Pillow``。
    pytesseract 与 Pillow 均惰性导入，因此 ``import ocr`` 本身不需要这些依赖。
    """

    name = "tesseract"

    def __init__(self, lang=None):
        self.lang = lang or getattr(settings, "OCR_TESSERACT_LANG", "chi_sim+eng")

    def recognize(self, image_bytes: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as e:
            raise OCRException(
                "未安装 OCR 依赖（pytesseract / Pillow）。请先执行 "
                "`pip install pytesseract pillow`。"
            ) from e
        try:
            img = Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            raise OCRException(f"无法读取图片：{e}") from e
        try:
            return pytesseract.image_to_string(img, lang=self.lang)
        except Exception as e:
            msg = str(e)
            if "tesseract" in msg.lower() or "not installed" in msg.lower():
                raise OCRException(
                    "未检测到 Tesseract 引擎。请安装 Tesseract OCR 并确保在 PATH 中可访问"
                    "（Windows: https://github.com/tesseract-ocr/tesseract ；"
                    "macOS: brew install tesseract；Debian: apt install tesseract-ocr）。"
                ) from e
            raise OCRException(f"OCR 识别失败：{msg[:200]}") from e


class CloudOCRProvider(OCRProvider):
    """外部 OCR 云服务（可选）。

    通过环境变量配置：``OCR_CLOUD_ENDPOINT`` / ``OCR_CLOUD_API_KEY`` / ``OCR_CLOUD_TYPE``。
    支持通用 JSON 接口与 ocr.space 风格返回。不同服务字段不同，可覆盖 ``_parse_response``。
    """

    name = "cloud"

    def __init__(self, endpoint="", api_key="", ocr_type="generic"):
        self.endpoint = endpoint or getattr(settings, "OCR_CLOUD_ENDPOINT", "")
        self.api_key = api_key or getattr(settings, "OCR_CLOUD_API_KEY", "")
        self.ocr_type = ocr_type or getattr(settings, "OCR_CLOUD_TYPE", "generic")

    def recognize(self, image_bytes: bytes) -> str:
        if not self.endpoint:
            raise OCRException("未配置 OCR 云服务地址（OCR_CLOUD_ENDPOINT）。")
        try:
            import base64
            import urllib.error
            import urllib.request
        except ImportError as e:  # pragma: no cover
            raise OCRException("运行环境缺少 urllib 支持。") from e
        try:
            payload = json.dumps({
                "apikey": self.api_key,
                "base64image": "data:image/png;base64,"
                + base64.b64encode(image_bytes).decode("ascii"),
            }).encode("utf-8")
            req = urllib.request.Request(
                self.endpoint,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
            return self._parse_response(body)
        except urllib.error.URLError as e:
            raise OCRException(f"OCR 云服务请求失败：{e}") from e
        except Exception as e:
            raise OCRException(f"OCR 云服务异常：{e}") from e

    def _parse_response(self, body: str) -> str:
        """把云服务的 JSON 响应收敛为纯文本。可被子类/配置覆盖。"""
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body
        if isinstance(data, dict):
            # ocr.space 风格
            if "ParsedResults" in data:
                return "\n".join(
                    p.get("ParsedText", "") for p in data["ParsedResults"]
                )
            if "text" in data:
                return data["text"]
            if "result" in data and isinstance(data["result"], str):
                return data["result"]
        return body


class MockOCRProvider(OCRProvider):
    """测试用：返回预设文本，不接触任何 OCR 引擎。"""

    name = "mock"

    def __init__(self, text: str = ""):
        self.text = text

    def recognize(self, image_bytes: bytes) -> str:
        return self.text


def get_ocr_provider() -> OCRProvider:
    """按 ``settings.OCR_PROVIDER`` 返回对应引擎实例。

    - "tesseract"（默认）：本地 Tesseract。
    - "cloud"：外部云服务（需配置 endpoint/key）。
    - "mock"：返回 settings.OCR_MOCK_TEXT（测试）。
    """
    name = getattr(settings, "OCR_PROVIDER", "tesseract")
    if name == "cloud":
        return CloudOCRProvider()
    if name == "mock":
        return MockOCRProvider(getattr(settings, "OCR_MOCK_TEXT", ""))
    return TesseractOCRProvider()


# ── 小票字段提取 ────────────────────────────────────────────────────────────
# 小票/POS 单的排版千差万别，这里做「够用」的启发式：
#   金额：优先「合计/总计/金额/TOTAL」等关键词后的数字；否则取所有金额中的最大值
#         （小票合计通常最大）。要求带货币符号 / 小数 / 千分位，避免把年份当金额。
#   日期：优先 YYYY年MM月DD日 / YYYY-MM-DD / YYYY/MM/DD；其次 MM/DD/YYYY。
#   商户：第一行有意义的文本（跳过纯数字、金额、常见票据词）。
_AMOUNT_KW = re.compile(
    r"(合计|总计|总额|总金额|应付|实付|应收|金额|TOTAL|Total|AMOUNT|Amount)\D{0,12}?"
    r"(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)
# 金额 token：货币符号可选；数字必须带逗号分组或小数（排除裸整数 / 年份）
_MONEY = re.compile(r"[¥￥$€]?\s*(\d{1,3}(?:,\d{3})+|\d+\.\d{1,2})")
_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
_DATE_ISO = re.compile(r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})")
_DATE_SHORT = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)")
# 商户候选时跳过的行（纯数字 / 票据术语 / 支付方式）
_MERCHANT_SKIP = re.compile(
    r"^\s*([\d\s\-/.:]+|合计|总计|金额|收据|小票|发票|tax|total|subtotal|qty|item|"
    r"品名|单价|数量|discount|找零|现金|card|微信|支付宝|cash)\b",
    re.I,
)


def _clean_amount(token: str):
    token = (token or "").replace(",", "").strip()
    try:
        return Decimal(token)
    except (InvalidOperation, ValueError):
        return None


def extract_receipt_fields(text: str) -> dict:
    """从 OCR 原文提取金额 / 日期 / 商户。返回 ``{amount, date, merchant, raw_text}``。"""
    raw = text or ""
    if not raw.strip():
        return {"amount": None, "date": None, "merchant": "", "raw_text": raw}

    # 金额
    amount = None
    kw = _AMOUNT_KW.search(raw)
    if kw:
        amount = _clean_amount(kw.group(2))
    if amount is None:
        candidates = [_clean_amount(x) for x in _MONEY.findall(raw)]
        candidates = [c for c in candidates if c is not None and c > 0]
        if candidates:
            amount = max(candidates)

    # 日期
    parsed_date = None
    for pat in (_DATE_CN, _DATE_ISO):
        m = pat.search(raw)
        if m:
            try:
                parsed_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                break
            except ValueError:
                parsed_date = None
    if parsed_date is None:
        m = _DATE_SHORT.search(raw)
        if m:
            try:
                parsed_date = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError:
                parsed_date = None

    # 商户：第一行有意义的文本
    merchant = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if _MERCHANT_SKIP.match(line):
            continue
        cleaned = _MONEY.sub("", line)
        cleaned = re.sub(r"\d{4}\s*年.*|\d{4}[/\-.]", "", cleaned)
        cleaned = cleaned.strip(" ·:-")
        if len(cleaned) >= 2:
            merchant = cleaned[:60]
            break

    return {
        "amount": amount,
        "date": parsed_date,
        "merchant": merchant,
        "raw_text": raw,
    }
