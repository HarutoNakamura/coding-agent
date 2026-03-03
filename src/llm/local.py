"""
Local LLM client for Ollama.
Falls back gracefully when Ollama is not available.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"

DETECT_SECRETS_PROMPT = """\
以下のテキストに含まれる機密情報を全て抽出してください。

検出対象:
- APIキー（OpenAI, Anthropic, AWS, GitHub等）
- パスワード・シークレット
- 認証トークン
- メールアドレス
- プライベートIPアドレス（192.168.x.x, 10.x.x.x 等）

JSON配列のみ出力してください。見つからない場合は [] を返してください。
[{{"value": "実際の文字列", "type": "種別"}}]

種別の例: api_key, password, token, email, private_ip, secret

テキスト:
{text}

JSON:"""

SUMMARIZE_PROMPT = """\
以下のコードを読んで、**実装の詳細（変数名・具体的な値）を含まない**、
機能の概要説明のみを日本語で1〜3文で書いてください。
コードブロックや構文は出力しないでください。

コード:
{code}

機能説明:"""


class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "llama3.2",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._available: Optional[bool] = None

    async def _check_availability(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def is_available(self) -> bool:
        if self._available is None:
            self._available = await self._check_availability()
        return self._available

    async def list_models(self) -> list[str]:
        """利用可能なモデル一覧を返す。"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.warning(f"Failed to list Ollama models: {e}")
            return []

    async def auto_select_model(self) -> Optional[str]:
        """
        利用可能なモデルから自動選択。
        好みの順: llama3.2 > llama3 > mistral > codellama > 最初に見つかったもの
        """
        models = await self.list_models()
        if not models:
            return None
        preferred = ["llama3.2", "llama3", "mistral", "codellama", "qwen"]
        for pref in preferred:
            for m in models:
                if pref in m.lower():
                    return m
        return models[0]

    async def generate(self, prompt: str) -> str:
        """
        テキスト生成。Ollamaが使えない場合は空文字を返す。
        """
        if not await self.is_available():
            return ""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 256},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json().get("response", "").strip()
        except Exception as e:
            logger.warning(f"Ollama generate error: {e}")
            return ""

    async def detect_secrets(self, text: str) -> list[dict]:
        """
        テキスト内の機密情報をLLMで検出する。
        Returns: [{"value": "実際の値", "type": "種別"}, ...]
        """
        if not await self.is_available():
            return []
        chunk = text[:5000] if len(text) > 5000 else text
        prompt = DETECT_SECRETS_PROMPT.format(text=chunk)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 512},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "").strip()
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                detections = json.loads(match.group(0))
                return [d for d in detections if isinstance(d, dict) and d.get("value")]
        except Exception as e:
            logger.warning(f"Ollama detect_secrets error: {e}")
        return []

    async def summarize_code(self, code: str) -> str:
        """
        コードをセマンティックな説明に変換する。
        Ollamaが使えない場合はコードをそのまま返す（マスキングのみ適用される）。
        """
        if not await self.is_available():
            return code
        if len(code) > 8000:
            code = code[:8000] + "\n... (truncated)"
        prompt = SUMMARIZE_PROMPT.format(code=code)
        result = await self.generate(prompt)
        return result if result else code
