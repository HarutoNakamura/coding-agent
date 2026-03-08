"""
PII Extractor client using LFM2-350M-PII-Extract-JP via llama-server.

Extracts Japanese PII (human names, addresses, phone numbers, emails,
company names) and converts to mapper.mask_detections() compatible format.

Server must be running before use:
    scripts/start_pii_server.sh
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8766"

# LFM2のJSONキー → マスキングラベル（実測値に基づく）
PII_TYPE_MAP = {
    "human_name":    "HUMAN_NAME",
    "address":       "ADDRESS",
    "phone_number":  "PHONE",
    "email":         "EMAIL",
    "email_address": "EMAIL",   # LFM2が実際に出力するキー
    "company_name":  "COMPANY",
}


class PIIExtractorClient:
    """
    llama-serverで動くLFM2-350M-PII-Extract-JPを使って日本語PIIを抽出する。

    出力例 (LFM2のJSON):
        {"human_name": ["田中太郎"], "address": ["東京都渋谷区..."], ...}

    変換後 (mask_detections形式):
        [{"value": "田中太郎", "type": "HUMAN_NAME"}, ...]

    サーバーが起動していない場合はフォールバックして [] を返す。
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._available: Optional[bool] = None

    async def _check_availability(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def is_available(self) -> bool:
        if self._available is None:
            self._available = await self._check_availability()
        return self._available

    def reset_cache(self) -> None:
        """availability キャッシュをクリア（再チェックさせる）。"""
        self._available = None

    async def extract_pii(self, text: str) -> list[dict]:
        """
        テキストから日本語PIIを抽出する。

        Args:
            text: 解析対象テキスト（長すぎる場合は先頭3000文字で切る）

        Returns:
            [{"value": "田中太郎", "type": "HUMAN_NAME"}, ...]
            サーバー未起動・エラー時は []
        """
        if not await self.is_available():
            return []

        chunk = text[:3000] if len(text) > 3000 else text

        payload = {
            "messages": [
                {"role": "user", "content": chunk}
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()

            raw = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(raw)
            detections = self._normalize(parsed)
            if detections:
                logger.debug(f"PIIExtractor: {len(detections)} items found")
            return detections

        except json.JSONDecodeError as e:
            logger.warning(f"PIIExtractor JSON parse error: {e}")
        except Exception as e:
            logger.warning(f"PIIExtractor error: {e}")
            self._available = None  # 次回再チェックさせる

        return []

    # マスキングトークンのパターン（例: EMAIL_001, OPENAI_KEY_002）
    import re as _re
    _MASK_TOKEN_RE = _re.compile(r"^[A-Z][A-Z0-9_]+_\d{3}$")

    def _normalize(self, parsed: dict) -> list[dict]:
        """
        LFM2出力 {"human_name": [...], "address": [...], ...} を
        [{"value": "...", "type": "..."}] に変換する。
        regexが先に置換したマスクトークン（例: EMAIL_001）は除外する。
        """
        result = []
        for key, label in PII_TYPE_MAP.items():
            values = parsed.get(key, [])
            if isinstance(values, str):
                values = [values]
            for v in values:
                if not isinstance(v, str):
                    continue
                v = v.strip()
                if not v:
                    continue
                if self._MASK_TOKEN_RE.match(v):
                    continue  # 既存マスクトークンをスキップ
                result.append({"value": v, "type": label})
        return result
