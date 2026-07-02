"""Gemini / OpenAI / Anthropic / xAI 統一ラッパー。"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from config.design_system import MODEL_NAMES

logger = logging.getLogger(__name__)

PROVIDER_ALIASES = {
    "gemini": "gemini",
    "claude": "claude",
    "gpt4o": "gpt4o",
    "grok": "grok",
    "openai": "gpt4o",
}


@dataclass
class UsageRecord:
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    image_units: int = 0
    audio_video_seconds: float = 0.0


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    usage: UsageRecord = field(default_factory=lambda: UsageRecord(provider=""))


class LLMClientManager:
    """利用可能なプロバイダを .env から判定し、統一インターフェースを提供する。"""

    def __init__(self, env_path: Optional[Path] = None) -> None:
        load_dotenv(env_path or Path(".env"))
        self._setup_proxy()
        self._gemini_api_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ).strip()
        self.available: Dict[str, bool] = {
            "gemini": bool(self._gemini_api_key),
            "claude": bool(os.getenv("ANTHROPIC_API_KEY")),
            "gpt4o": bool(os.getenv("OPENAI_API_KEY")),
            "grok": bool(os.getenv("XAI_API_KEY")),
        }
        self._gemini_client = None
        self._openai_client = None
        self._anthropic_client = None
        self._xai_client = None
        self.total_usage: List[UsageRecord] = []

    def _setup_proxy(self) -> None:
        http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if http_proxy:
            os.environ["HTTP_PROXY"] = http_proxy
        if https_proxy:
            os.environ["HTTPS_PROXY"] = https_proxy

    def has_any_key(self) -> bool:
        return any(self.available.values())

    def _get_gemini(self):
        if self._gemini_client is None:
            from google import genai

            self._gemini_client = genai.Client(api_key=self._gemini_api_key)
        return self._gemini_client

    def _get_openai(self):
        if self._openai_client is None:
            from openai import OpenAI

            self._openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return self._openai_client

    def _get_anthropic(self):
        if self._anthropic_client is None:
            from anthropic import Anthropic

            self._anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        return self._anthropic_client

    def _get_xai(self):
        if self._xai_client is None:
            from openai import OpenAI

            self._xai_client = OpenAI(
                api_key=os.getenv("XAI_API_KEY"),
                base_url="https://api.x.ai/v1",
            )
        return self._xai_client

    def generate(
        self,
        provider: str,
        prompt: str,
        *,
        system: Optional[str] = None,
        images: Optional[List[Path]] = None,
        json_mode: bool = False,
        timeout_sec: float = 120,
        cancel_event=None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        provider = PROVIDER_ALIASES.get(provider, provider)
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("ユーザーによりキャンセルされました")

        def _invoke() -> LLMResponse:
            if provider == "gemini":
                return self._generate_gemini(
                    prompt,
                    system=system,
                    images=images,
                    json_mode=json_mode,
                    timeout_sec=timeout_sec,
                    model=model,
                )
            if provider == "claude":
                return self._generate_claude(
                    prompt,
                    system=system,
                    images=images,
                    json_mode=json_mode,
                    timeout_sec=timeout_sec,
                    model=model,
                )
            if provider == "gpt4o":
                return self._generate_openai(
                    prompt,
                    system=system,
                    images=images,
                    json_mode=json_mode,
                    timeout_sec=timeout_sec,
                    model=model,
                )
            if provider == "grok":
                return self._generate_xai(
                    prompt,
                    system=system,
                    images=images,
                    json_mode=json_mode,
                    timeout_sec=timeout_sec,
                    model=model,
                )
            raise ValueError(f"未知のプロバイダ: {provider}")

        start = time.time()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_invoke)
            try:
                resp = future.result(timeout=timeout_sec)
            except FuturesTimeout:
                future.cancel()
                raise TimeoutError(f"{provider} の応答がタイムアウト ({timeout_sec}s)") from None

        elapsed = time.time() - start
        logger.debug("%s 応答 %.2fs (limit %.2fs)", provider, elapsed, timeout_sec)
        self.total_usage.append(resp.usage)
        return resp

    def generate_with_file_uri(
        self,
        file_uri: str,
        prompt: str,
        *,
        timeout_sec: float = 900,
        cancel_event=None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """YouTube URL 等を Gemini file_uri として渡す (プレビュー機能・料金変更の可能性あり)。"""
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("ユーザーによりキャンセルされました")
        if not self.available.get("gemini"):
            raise RuntimeError("Gemini APIキーが未設定です")

        client = self._get_gemini()
        from google.genai import types

        model = model or MODEL_NAMES["gemini"]
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_uri(file_uri=file_uri, mime_type="video/*"),
                types.Part.from_text(text=prompt),
            ],
        )
        text = response.text or ""
        usage = UsageRecord(provider="gemini", input_tokens=len(prompt) // 2, output_tokens=len(text) // 2)
        return LLMResponse(text=text, provider="gemini", model=model, usage=usage)

    def generate_with_media_file(
        self,
        file_path: Path,
        prompt: str,
        mime_type: str,
        *,
        timeout_sec: float = 600,
        cancel_event=None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("ユーザーによりキャンセルされました")
        if not self.available.get("gemini"):
            raise RuntimeError("Gemini APIキーが未設定です")

        client = self._get_gemini()
        from google.genai import types

        uploaded = client.files.upload(file=str(file_path))
        model = model or MODEL_NAMES["gemini"]
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type),
                types.Part.from_text(text=prompt),
            ],
        )
        text = response.text or ""
        usage = UsageRecord(provider="gemini", input_tokens=1000, output_tokens=len(text) // 2)
        return LLMResponse(text=text, provider="gemini", model=model, usage=usage)

    def _generate_gemini(
        self,
        prompt,
        system=None,
        images=None,
        json_mode=False,
        timeout_sec: float = 120,
        model: Optional[str] = None,
    ) -> LLMResponse:
        client = self._get_gemini()
        from google.genai import types

        model = model or MODEL_NAMES["gemini"]
        parts: List[Any] = []
        if images:
            for img in images:
                data = base64.b64encode(img.read_bytes()).decode()
                parts.append(types.Part.from_bytes(data=base64.b64decode(data), mime_type="image/jpeg"))
        parts.append(types.Part.from_text(text=prompt))
        timeout_ms = max(1000, int(timeout_sec * 1000))
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else None,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        response = client.models.generate_content(model=model, contents=parts, config=config)
        text = response.text or ""
        usage = UsageRecord(provider="gemini", input_tokens=len(prompt) // 2, output_tokens=len(text) // 2)
        return LLMResponse(text=text, provider="gemini", model=model, usage=usage)

    def _generate_claude(
        self,
        prompt,
        system=None,
        images=None,
        json_mode=False,
        timeout_sec: float = 120,
        model: Optional[str] = None,
    ) -> LLMResponse:
        client = self._get_anthropic()
        model = model or MODEL_NAMES["claude"]
        content: List[Dict] = []
        if images:
            for img in images:
                data = base64.b64encode(img.read_bytes()).decode()
                content.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
                    }
                )
        content.append({"type": "text", "text": prompt})
        msg = client.messages.create(
            model=model,
            max_tokens=8192,
            system=system or "",
            messages=[{"role": "user", "content": content}],
            timeout=timeout_sec,
        )
        text = msg.content[0].text if msg.content else ""
        usage = UsageRecord(
            provider="claude",
            input_tokens=getattr(msg.usage, "input_tokens", 0),
            output_tokens=getattr(msg.usage, "output_tokens", 0),
            image_units=len(images or []),
        )
        return LLMResponse(text=text, provider="claude", model=model, usage=usage)

    def _generate_openai(
        self,
        prompt,
        system=None,
        images=None,
        json_mode=False,
        timeout_sec: float = 120,
        model: Optional[str] = None,
    ) -> LLMResponse:
        client = self._get_openai()
        model = model or MODEL_NAMES["gpt4o"]
        user_content: List[Dict] = [{"type": "text", "text": prompt}]
        if images:
            for img in images:
                data = base64.b64encode(img.read_bytes()).decode()
                user_content.append(
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}
                )
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system or "You are a helpful assistant."},
                {"role": "user", "content": user_content},
            ],
            "timeout": timeout_sec,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content or ""
        u = response.usage
        usage = UsageRecord(
            provider="gpt4o",
            input_tokens=u.prompt_tokens if u else 0,
            output_tokens=u.completion_tokens if u else 0,
            image_units=len(images or []),
        )
        return LLMResponse(text=text, provider="gpt4o", model=model, usage=usage)

    def _generate_xai(
        self,
        prompt,
        system=None,
        images=None,
        json_mode=False,
        timeout_sec: float = 120,
        model: Optional[str] = None,
    ) -> LLMResponse:
        client = self._get_xai()
        model = model or MODEL_NAMES["grok"]
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system or ""},
                {"role": "user", "content": prompt},
            ],
            timeout=timeout_sec,
        )
        text = response.choices[0].message.content or ""
        u = response.usage
        usage = UsageRecord(
            provider="grok",
            input_tokens=u.prompt_tokens if u else 0,
            output_tokens=u.completion_tokens if u else 0,
        )
        return LLMResponse(text=text, provider="grok", model=model, usage=usage)

    def reset_usage(self) -> None:
        self.total_usage = []

    def usage_summary(self) -> Dict[str, int]:
        summary = {"input_tokens": 0, "output_tokens": 0, "image_units": 0}
        for u in self.total_usage:
            summary["input_tokens"] += u.input_tokens
            summary["output_tokens"] += u.output_tokens
            summary["image_units"] += u.image_units
        return summary
