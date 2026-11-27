"""OpenAI 兼容的最小客户端（零第三方依赖）。

为什么不用官方 SDK：
  1) 本包 pyproject 的 dependencies 是空的，`pip install -e .` 不该要求联网；
  2) 只用到 /chat/completions 一个端点，SDK 带来的版本冲突比省下的代码多。

设计取舍：
  - 只做「发消息、取文本」，不做函数调用、流式、批量；
  - 图片一律内联成 data URI（base64），不依赖公网可访问的 URL；
  - 429 / 5xx / 网络超时按可重试处理，退避 2s、4s；4xx（除 429）直接失败，
    因为那通常是 key、模型名或参数写错，重试只是浪费时间。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 240
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """调用失败。status 为 None 表示网络层错误（未拿到响应）。"""

    def __init__(self, message: str, status: Optional[int] = None,
                 body: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.retryable = retryable


def normalize_endpoint(base_url: str) -> str:
    """把用户填的 base_url 拼成 chat/completions 的完整地址。

    接受的写法：
        https://api.example.com/v1                    -> .../v1/chat/completions
        https://api.example.com/v1/                   -> .../v1/chat/completions
        https://api.example.com/v1/chat/completions    -> 原样
        https://api.example.com                        -> .../v1/chat/completions
    """
    base = (base_url or "").strip()
    if not base:
        raise ApiError("未配置 base_url（config.api.json 或 ANSWER_API_BASE）")
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def encode_image(path: Path) -> str:
    """把一个本地图片读成 data URI。"""
    p = Path(path)
    if not p.exists():
        raise ApiError("图片不存在：%s" % p)
    raw = p.read_bytes()
    if not raw:
        raise ApiError("图片为空：%s" % p)
    mime = mimetypes.guess_type(str(p))[0]
    if not mime or not mime.startswith("image/"):
        # 扩展名不可靠时按 PNG 兜底；模型侧只认 mime，与真实字节不符会报错，
        # 所以这里只处理「后缀缺失」的情况，不猜 jpg/webp 的互换。
        mime = "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def image_part(path: Path) -> Dict[str, Any]:
    """OpenAI 兼容的图片消息片段。"""
    return {"type": "image_url", "image_url": {"url": encode_image(path)}}


def text_part(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def _extract_text(data: Dict[str, Any]) -> str:
    """从响应里取正文。兼容 content 为字符串或片段数组两种写法。"""
    choices = data.get("choices") or []
    if not choices:
        raise ApiError("响应里没有 choices：%s" % json.dumps(data, ensure_ascii=False)[:300])
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in (None, "text"):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    if not content and msg.get("reasoning_content"):
        return str(msg["reasoning_content"])
    raise ApiError("响应 content 为空：%s" % json.dumps(data, ensure_ascii=False)[:300])


def chat(
    messages: List[Dict[str, Any]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
    retries: int = 1,
    extra_body: Optional[Dict[str, Any]] = None,
) -> str:
    """发一轮对话，返回正文。失败抛 ApiError。"""
    if not api_key:
        raise ApiError("未配置 API key（config.api.json 或 ANSWER_API_KEY）")
    if not model:
        raise ApiError("未配置 model（config.api.json 或 ANSWER_API_MODEL）")

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    if extra_body:
        payload.update(extra_body)

    url = normalize_endpoint(base_url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Authorization": "Bearer %s" % api_key,
    }

    attempt = 0
    while True:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            return _extract_text(data)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - 读不到 body 不该掩盖原始错误
                pass
            msg = _http_message(exc.code, detail)
            retryable = exc.code in RETRYABLE_STATUS
            if retryable and attempt < retries:
                time.sleep(2 * (attempt + 1))
                attempt += 1
                continue
            raise ApiError(msg, status=exc.code, body=detail, retryable=retryable) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                attempt += 1
                continue
            raise ApiError("网络错误：%s" % exc, retryable=True) from exc
        except json.JSONDecodeError as exc:
            raise ApiError("响应不是合法 JSON：%s" % exc) from exc


def _http_message(code: int, detail: str) -> str:
    """把服务端错误体压成一行好读的信息。"""
    hint = ""
    try:
        obj = json.loads(detail)
        err = obj.get("error")
        if isinstance(err, dict):
            hint = str(err.get("message") or err.get("code") or "")
        elif isinstance(err, str):
            hint = err
        if not hint:
            hint = str(obj.get("message") or obj.get("msg") or "")
    except Exception:  # noqa: BLE001 - 非 JSON 错误体按原样截断
        hint = detail.strip()
    hint = " ".join(hint.split())[:400]
    table = {
        401: "鉴权失败（key 无效或已过期）",
        403: "无权限（模型未开通或额度不足）",
        404: "端点或模型不存在（检查 base_url 与 model）",
        413: "请求体过大（图片太多或太大）",
        429: "触发限流或额度用尽",
    }
    head = table.get(code) or "HTTP %d" % code
    return ("%s：%s" % (head, hint)) if hint else head
