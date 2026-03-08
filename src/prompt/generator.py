"""
Prompt generator: combines project context + user query into
a detailed, API-optimized prompt for cloud LLMs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..scanner.project import ProjectIndex, ScannedFile
from ..masking.mapper import MaskMapper


# トークン概算（1文字≒0.4トークン、英語は1単語≒1.3トークン）
CHARS_PER_TOKEN = 2.5
DEFAULT_MAX_CONTEXT_TOKENS = 30_000


SYSTEM_PROMPT_TEMPLATE = """\
You are a senior software engineer and coding assistant.
You have been given context about a software project.
Answer the user's question accurately and concisely, referencing the project context.
If the user asks to write or modify code, always follow the project's existing conventions and style.

Project root: {root}
"""

PROJECT_CONTEXT_TEMPLATE = """\
## Project Structure
{file_tree}

## Project Summary
- Total files: {total_files}
- Languages: {languages}
- Total size: {total_size_kb}KB

## File Contents
{file_sections}
"""

FILE_SECTION_TEMPLATE = """\
### {path}
```{ext}
{content}
```
"""


@dataclass
class GeneratedPrompt:
    system: str
    context: str          # マスク済みコンテキスト（プレビュー用）
    user_message: str
    messages: list[dict]  # クラウドLLMに渡す形式
    estimated_tokens: int
    files_included: int
    files_truncated: int


class PromptGenerator:
    def __init__(
        self,
        mapper: MaskMapper,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        provider: str = "openai",
    ) -> None:
        self.mapper = mapper
        self.max_context_tokens = max_context_tokens
        self.provider = provider

    def _estimate_tokens(self, text: str) -> int:
        return int(len(text) / CHARS_PER_TOKEN)

    def _ext_to_lang(self, ext: str) -> str:
        mapping = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".jsx": "jsx", ".tsx": "tsx", ".java": "java", ".go": "go",
            ".rs": "rust", ".c": "c", ".cpp": "cpp", ".cs": "csharp",
            ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
            ".sh": "bash", ".sql": "sql", ".html": "html", ".css": "css",
            ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
            ".md": "markdown",
        }
        return mapping.get(ext, "")

    def _build_file_section(self, f: ScannedFile, masked_content: str) -> str:
        lang = self._ext_to_lang(f.extension)
        return FILE_SECTION_TEMPLATE.format(
            path=f.path,
            ext=lang,
            content=masked_content,
        )

    def generate(
        self,
        index: ProjectIndex,
        user_query: str,
        summarized_contents: dict[str, str] | None = None,
        llm_masked_contents: dict[str, str] | None = None,
        files: list[ScannedFile] | None = None,
    ) -> GeneratedPrompt:
        """
        プロジェクトインデックスとユーザークエリからプロンプトを生成する。

        Args:
            index: ProjectIndex (スキャン済み)
            user_query: ユーザーの質問
            summarized_contents: {rel_path: summarized_text} ローカルLLMによる要約
            llm_masked_contents: {rel_path: masked_text} OllamaによるLLMマスキング済みコンテンツ
        """
        summarized = summarized_contents or {}
        llm_masked = llm_masked_contents or {}

        # ファイル内容をマスキング
        budget = self.max_context_tokens
        file_sections: list[str] = []
        files_included = 0
        files_truncated = 0

        # files が指定されていれば選択済みリストを使う（FileSelectorから渡される）
        # None の場合は従来通り全ファイルを拡張子優先でソート
        if files is not None:
            sorted_files = files
        else:
            code_exts = {".py", ".js", ".ts", ".go", ".java", ".rs", ".swift", ".kt"}
            sorted_files = sorted(
                index.files,
                key=lambda f: (0 if f.extension in code_exts else 1, f.path)
            )

        for f in sorted_files:
            # 要約があればそれを使う、なければ元のコンテキストを使う
            content = summarized.get(f.path, f.content)
            if f.path in llm_masked:
                # OllamaによるLLMマスキング済みコンテンツを使用（正規表現マスキングをスキップ）
                masked_content = llm_masked[f.path]
            else:
                masked_content, _ = self.mapper.mask(content)

            section = self._build_file_section(f, masked_content)
            section_tokens = self._estimate_tokens(section)

            if section_tokens > budget:
                # バジェット超過: このファイルはスキップ
                files_truncated += 1
                continue

            file_sections.append(section)
            budget -= section_tokens
            files_included += 1

        # 言語一覧
        langs = sorted({self._ext_to_lang(f.extension) for f in index.files if f.extension})

        context = PROJECT_CONTEXT_TEMPLATE.format(
            file_tree=index.file_tree,
            total_files=index.summary["total_files"],
            languages=", ".join(langs) or "unknown",
            total_size_kb=index.summary["total_size_kb"],
            file_sections="\n".join(file_sections),
        )

        system = SYSTEM_PROMPT_TEMPLATE.format(root=os.path.basename(index.root))

        # クラウドAPI向けメッセージ構築
        if self.provider == "anthropic":
            # Anthropic形式: systemは別フィールド
            messages = [
                {"role": "user", "content": f"{context}\n\n---\n\n{user_query}"}
            ]
        else:
            # OpenAI形式
            messages = [
                {"role": "system", "content": system + "\n\n" + context},
                {"role": "user", "content": user_query},
            ]

        total_text = system + context + user_query
        estimated_tokens = self._estimate_tokens(total_text)

        return GeneratedPrompt(
            system=system,
            context=context,
            user_message=user_query,
            messages=messages,
            estimated_tokens=estimated_tokens,
            files_included=files_included,
            files_truncated=files_truncated,
        )
