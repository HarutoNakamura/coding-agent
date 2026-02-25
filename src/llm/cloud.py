"""
Cloud LLM client supporting OpenAI and Anthropic APIs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)


class CloudLLMClient:
    """
    OpenAI / Anthropic の両方に対応した薄いHTTPクライアント。
    SDK依存なし、httpxのみ使用。
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
    ) -> None:
        self.provider = provider.lower()
        self.model = model
        self.api_key = api_key or self._load_api_key()

    def _load_api_key(self) -> str:
        if self.provider == "openai":
            key = os.environ.get("OPENAI_API_KEY", "")
        elif self.provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "")
        else:
            key = ""
        return key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def chat(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """
        チャット形式でLLMに問い合わせる。
        messages: [{"role": "user"|"assistant"|"system", "content": "..."}]
        """
        if not self.is_configured():
            raise ValueError(
                f"API key not set. Set the environment variable: "
                f"{'OPENAI_API_KEY' if self.provider == 'openai' else 'ANTHROPIC_API_KEY'}"
            )

        if self.provider == "openai":
            return await self._openai_chat(messages, max_tokens, temperature)
        elif self.provider == "anthropic":
            return await self._anthropic_chat(messages, max_tokens, temperature)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def _openai_chat(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _anthropic_chat(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> str:
        # systemメッセージをAnthropicの形式に分離
        system_content = ""
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system_content = m["content"]
            else:
                chat_messages.append(m)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat_messages,
        }
        if system_content:
            payload["system"] = system_content

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> dict:
        """概算コストを返す（USD）。"""
        # 2025年時点の代表的な価格
        pricing = {
            ("openai", "gpt-4o"):         (0.0025, 0.010),   # per 1K tokens
            ("openai", "gpt-4o-mini"):    (0.00015, 0.0006),
            ("openai", "gpt-3.5-turbo"):  (0.0005, 0.0015),
            ("anthropic", "claude-opus-4-6"):   (0.015, 0.075),
            ("anthropic", "claude-sonnet-4-6"): (0.003, 0.015),
            ("anthropic", "claude-haiku-4-5-20251001"):  (0.00025, 0.00125),
        }
        key = (self.provider, self.model)
        input_price, output_price = pricing.get(key, (0.001, 0.002))
        cost = (prompt_tokens / 1000 * input_price) + (completion_tokens / 1000 * output_price)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_usd": round(cost, 6),
        }
