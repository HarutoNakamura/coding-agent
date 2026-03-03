"""
Masking mapper: replaces sensitive values with tokens and maintains
a reversible mapping table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .patterns import PATTERNS, MaskPattern


@dataclass
class MaskEntry:
    token: str       # [SECRET_001] のようなトークン
    original: str    # 元の値
    pattern_name: str


class MaskMapper:
    """
    テキストを受け取ってマスキングし、逆変換マップを保持する。

    使い方:
        mapper = MaskMapper()
        masked_text, log = mapper.mask(text)
        original = mapper.unmask(masked_text)  # 必要なら元に戻す
    """

    def __init__(self) -> None:
        self._map: dict[str, MaskEntry] = {}   # token -> entry
        self._seen: dict[str, str] = {}         # original_value -> token (重複防止)
        self._counters: dict[str, int] = {}

    def _next_token(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"[{prefix}_{n:03d}]"

    def _register(self, original: str, prefix: str, pattern_name: str) -> str:
        if original in self._seen:
            return self._seen[original]
        token = self._next_token(prefix)
        entry = MaskEntry(token=token, original=original, pattern_name=pattern_name)
        self._map[token] = entry
        self._seen[original] = token
        return token

    def mask(self, text: str) -> tuple[str, list[MaskEntry]]:
        """
        テキストをマスキングする。
        Returns: (masked_text, list of new MaskEntry created this call)
        """
        applied: list[MaskEntry] = []
        result = text

        for mp in PATTERNS:
            def replacer(m: re.Match, _mp: MaskPattern = mp) -> str:
                # グループがあれば最後のグループ、なければマッチ全体を秘匿対象とする
                if m.lastindex and m.lastindex >= 1:
                    sensitive = m.group(m.lastindex)
                    token = self._register(sensitive, _mp.label_prefix, _mp.name)
                    # グループ部分のみ置換（前後の固定部分は保持）
                    return m.group(0).replace(sensitive, token)
                else:
                    sensitive = m.group(0)
                    token = self._register(sensitive, _mp.label_prefix, _mp.name)
                    return token

            result = mp.pattern.sub(replacer, result)

        # 新たに追加されたエントリを収集
        for token, entry in self._map.items():
            if entry not in applied:
                applied.append(entry)

        return result, list(self._map.values())

    def mask_detections(self, text: str, detections: list[dict]) -> str:
        """
        LLMが検出した機密情報リストを元にテキストをマスキングする。
        detections: [{"value": "機密値", "type": "種別"}, ...]
        """
        result = text
        for det in detections:
            value = str(det.get("value", "")).strip()
            dtype = (
                str(det.get("type", "secret"))
                .upper()
                .replace(" ", "_")
                .replace("-", "_")
            )
            if value and len(value) >= 3 and value in result:
                token = self._register(value, dtype, f"llm_{det.get('type', 'secret')}")
                result = result.replace(value, token)
        return result

    def unmask(self, text: str) -> str:
        """マスクされたトークンを元の値に戻す。"""
        result = text
        for token, entry in self._map.items():
            result = result.replace(token, entry.original)
        return result

    @property
    def entries(self) -> list[MaskEntry]:
        return list(self._map.values())

    def reset(self) -> None:
        self._map.clear()
        self._seen.clear()
        self._counters.clear()
