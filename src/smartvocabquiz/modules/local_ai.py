"""连接用户已启动的 OpenAI 兼容视觉模型服务。"""

from __future__ import annotations

import base64
import json
from typing import Any

import requests


SYSTEM_PROMPT = """你是英语教材词汇表的忠实抄录员。识别图片中印刷的英文词或短语与紧随其后的中文解释。
只依据图片，不补充、翻译或猜测。忽略标题、字母分组标题、页码、手写涂改和被白框遮盖的区域。
保留原有英文大小写、撇号与短语；中文解释原样抄录。无法辨认的一项直接省略。
仅返回 JSON 对象，格式为 {"entries":[{"english":"...","chinese":"..."}]}。不要输出其他文字。"""


class LocalAIClient:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: int, max_tokens: int):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def check_available(self) -> None:
        """只做只读健康检查；从不启动或关闭服务。"""
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        response = self.session.get(f"{root}/health", timeout=10)
        response.raise_for_status()
        response = self.session.get(f"{self.base_url}/models", timeout=10)
        response.raise_for_status()
        models = [item.get("id") for item in response.json().get("data", [])]
        if self.model not in models:
            raise RuntimeError(f"配置的模型不可用：{self.model}；可用模型：{models}")

    def recognize(self, image: bytes) -> list[dict[str, str]]:
        encoded = base64.b64encode(image).decode("ascii")
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "逐项识别这张词汇表图块中的英文及其中文解释。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                ]},
            ],
        }
        response = self.session.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("模型输出超过 max_tokens，无法保证词汇完整")
        content = choice["message"].get("content")
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        if not isinstance(content, str):
            raise ValueError("模型未返回文本结果")
        result = _parse_json(content)
        entries = result.get("entries")
        if not isinstance(entries, list):
            raise ValueError("模型结果缺少 entries 数组")
        clean = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            english, chinese = item.get("english"), item.get("chinese")
            if isinstance(english, str) and isinstance(chinese, str) and english.strip() and chinese.strip():
                clean.append({"english": english.strip(), "chinese": chinese.strip()})
        return clean

    def generate_task(self, system_prompt: str, task_type: str,
                      vocab_list: list[dict[str, str]], count: int,
                      excluded_words: list[str], feedback: str = "") -> dict[str, Any]:
        """按一个题型请求结构化试题；调用方负责校验和打乱。"""
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.35,
            "max_tokens": self.max_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps({
                    "task_type": task_type,
                    "item_count": count,
                    "vocab_list": vocab_list,
                    "avoid_answer_words": excluded_words,
                    "previous_validation_error": feedback,
                    "request": "只生成指定题型和指定数量。所有答案及选项必须来自 vocab_list，答案不要重复。仅返回 JSON。",
                }, ensure_ascii=False)},
            ],
        }
        response = self.session.post(f"{self.base_url}/chat/completions", json=body,
                                     timeout=self.timeout)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("出题结果超过 max_tokens")
        content = choice["message"].get("content")
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        if not isinstance(content, str):
            raise ValueError("模型未返回出题文本")
        return _parse_json(content)


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回有效 JSON") from None
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型 JSON 顶层不是对象")
    return data
