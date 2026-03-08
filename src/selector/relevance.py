"""
File relevance selector: scores project files against a user query and
returns the most relevant subset.

- English: word tokenization via regex
- Japanese: bi-gram tokenization (no external dependencies)
- Path match weighted higher than content match
- Falls back to code-extension priority when no tokens match
"""
from __future__ import annotations

import re

from ..scanner.project import ScannedFile


CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rs", ".c", ".cpp",
    ".cs", ".rb", ".php", ".swift", ".kt",
}

_MAX_CONTENT_CHARS = 2000
_PATH_WEIGHT = 3.0
_CONTENT_WEIGHT = 1.0

# カタカナ → ローマ字（パスとの照合用）
_KT = {
    'ア':'a','イ':'i','ウ':'u','エ':'e','オ':'o',
    'カ':'ka','キ':'ki','ク':'ku','ケ':'ke','コ':'ko',
    'サ':'sa','シ':'shi','ス':'su','セ':'se','ソ':'so',
    'タ':'ta','チ':'chi','ツ':'tsu','テ':'te','ト':'to',
    'ナ':'na','ニ':'ni','ヌ':'nu','ネ':'ne','ノ':'no',
    'ハ':'ha','ヒ':'hi','フ':'fu','ヘ':'he','ホ':'ho',
    'マ':'ma','ミ':'mi','ム':'mu','メ':'me','モ':'mo',
    'ヤ':'ya','ユ':'yu','ヨ':'yo',
    'ラ':'ra','リ':'ri','ル':'ru','レ':'re','ロ':'ro',
    'ワ':'wa','ン':'n',
    'ガ':'ga','ギ':'gi','グ':'gu','ゲ':'ge','ゴ':'go',
    'ザ':'za','ジ':'ji','ズ':'zu','ゼ':'ze','ゾ':'zo',
    'ダ':'da','デ':'de','ド':'do',
    'バ':'ba','ビ':'bi','ブ':'bu','ベ':'be','ボ':'bo',
    'パ':'pa','ピ':'pi','プ':'pu','ペ':'pe','ポ':'po',
    'ッ':'','ー':'',
}


def _to_romaji(text: str) -> str:
    return "".join(_KT.get(c, "") for c in text)


def _has_common_substring(a: str, b: str, min_len: int = 3) -> bool:
    """a の部分文字列が b に含まれるか（カタカナ→ローマ字 vs 英語パス照合用）。"""
    for i in range(len(a) - min_len + 1):
        if a[i : i + min_len] in b:
            return True
    return False


def _tokenize(text: str) -> tuple[list[str], list[str]]:
    """
    英語 + 日本語のトークン化。
    Returns: (通常トークン, カタカナローマ字トークン)
    """
    text_lower = text.lower()

    # 英語: アルファベット・数字・アンダースコア
    en_tokens = re.findall(r"[a-z0-9_]+", text_lower)

    # 日本語 bi-gram（ひらがな・カタカナ・漢字）
    jp_tokens: list[str] = []
    for segment in re.findall(r"[\u3040-\u9fff]+", text):
        for i in range(len(segment) - 1):
            jp_tokens.append(segment[i : i + 2])
        jp_tokens.extend(list(segment))

    # カタカナ → ローマ字（英語パスとのマッチに使う）
    romaji_tokens: list[str] = []
    for segment in re.findall(r"[\u30A0-\u30FF]+", text):
        romaji = _to_romaji(segment)
        if len(romaji) >= 3:
            romaji_tokens.append(romaji)

    normal = [t for t in en_tokens + jp_tokens if len(t) >= 2]
    return normal, romaji_tokens


def _score(file: ScannedFile, tokens: list[str], romaji_tokens: list[str]) -> float:
    if not tokens and not romaji_tokens:
        return 0.0

    path_lower = file.path.lower().replace("\\", "/")
    filename = path_lower.split("/")[-1]
    path_words = re.findall(r"[a-z0-9]+", path_lower)
    snippet = file.content[:_MAX_CONTENT_CHARS].lower()

    path_hits = 0.0
    content_hits = 0

    for token in tokens:
        if token in path_lower:
            path_hits += 1.0
        if token in filename:
            path_hits += 0.5
        if token in snippet:
            content_hits += 1

    # カタカナ由来のローマ字と英語パス単語の部分文字列マッチ
    for romaji in romaji_tokens:
        for pw in path_words:
            if _has_common_substring(romaji, pw, min_len=3):
                path_hits += 0.8
                break

    denom = max(len(tokens) + len(romaji_tokens), 1)
    ps = min(path_hits / denom, 1.0)
    cs = min(content_hits / denom, 1.0)
    return ps * _PATH_WEIGHT + cs * _CONTENT_WEIGHT


class FileSelector:
    """
    クエリとの関連性でファイルをスコアリングして上位 max_files 件を返す。

    スコアがすべて min_score 未満の場合はコードファイル優先でフォールバック。
    """

    def __init__(self, max_files: int = 10, min_score: float = 0.1) -> None:
        self.max_files = max_files
        self.min_score = min_score

    def select(self, files: list[ScannedFile], query: str) -> list[ScannedFile]:
        if not files:
            return []

        tokens, romaji_tokens = _tokenize(query)
        if not tokens and not romaji_tokens:
            return self._fallback(files)

        scored = sorted(
            ((f, _score(f, tokens, romaji_tokens)) for f in files),
            key=lambda x: x[1],
            reverse=True,
        )

        selected = [f for f, s in scored if s >= self.min_score][: self.max_files]
        return selected if selected else self._fallback(files)

    def _fallback(self, files: list[ScannedFile]) -> list[ScannedFile]:
        """マッチなし時: コードファイル優先で max_files 件返す。"""
        return sorted(
            files,
            key=lambda f: (0 if f.extension in CODE_EXTENSIONS else 1, f.path),
        )[: self.max_files]
