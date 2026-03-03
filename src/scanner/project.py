"""
Project scanner: reads project files and builds an in-memory index.
Respects .gitignore patterns and skips binary/large files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pathspec


TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".r", ".lua", ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".html", ".css", ".scss", ".sass", ".less",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".csv",
    ".md", ".txt", ".rst", ".env", ".env.example",
    ".dockerfile", ".tf", ".hcl", ".proto",
}


@dataclass
class ScannedFile:
    path: str          # 相対パス
    abs_path: str      # 絶対パス
    content: str       # ファイル内容
    size_bytes: int
    extension: str


@dataclass
class ProjectIndex:
    root: str
    files: list[ScannedFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def file_tree(self) -> str:
        """ファイルツリーの文字列表現（フォルダ付き）"""
        lines = [f"{os.path.basename(self.root)}/"]
        paths = sorted(f.path for f in self.files)

        seen_dirs: set[str] = set()
        for p in paths:
            parts = p.replace("\\", "/").split("/")
            for i in range(len(parts) - 1):
                dir_key = "/".join(parts[: i + 1])
                if dir_key not in seen_dirs:
                    seen_dirs.add(dir_key)
                    indent = "  " * i
                    lines.append(f"{indent}  {parts[i]}/")
            depth = len(parts) - 1
            indent = "  " * depth
            lines.append(f"{indent}  {parts[-1]}")
        return "\n".join(lines)

    @property
    def summary(self) -> dict:
        exts: dict[str, int] = {}
        for f in self.files:
            exts[f.extension] = exts.get(f.extension, 0) + 1
        return {
            "total_files": len(self.files),
            "skipped_files": len(self.skipped),
            "extensions": exts,
            "total_size_kb": sum(f.size_bytes for f in self.files) // 1024,
        }


def _load_gitignore(root: Path) -> Optional[pathspec.PathSpec]:
    gitignore = root / ".gitignore"
    if gitignore.exists():
        patterns = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return None


def _is_binary(data: bytes) -> bool:
    """Check for null bytes as a heuristic for binary files."""
    return b"\x00" in data[:8192]


def scan_project(
    root: str | Path,
    exclude_patterns: list[str] | None = None,
    max_file_size_kb: int = 100,
    max_total_files: int = 200,
) -> ProjectIndex:
    """
    指定ディレクトリをスキャンしてProjectIndexを返す。

    Args:
        root: スキャン対象のルートディレクトリ
        exclude_patterns: 追加の除外パターン（config.yamlのexclude）
        max_file_size_kb: 1ファイルの最大サイズ
        max_total_files: 最大ファイル数
    """
    root = Path(root).resolve()
    index = ProjectIndex(root=str(root))

    # gitignore パターン
    git_spec = _load_gitignore(root)

    # 設定ファイルの除外パターン
    extra_spec: Optional[pathspec.PathSpec] = None
    if exclude_patterns:
        extra_spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns)

    max_bytes = max_file_size_kb * 1024

    for abs_path in sorted(root.rglob("*")):
        if not abs_path.is_file():
            continue

        rel_str = str(abs_path.relative_to(root))

        # .gitignore チェック
        if git_spec and git_spec.match_file(rel_str):
            index.skipped.append(rel_str)
            continue

        # 追加除外パターン
        if extra_spec and extra_spec.match_file(rel_str):
            index.skipped.append(rel_str)
            continue

        # ファイルサイズチェック
        size = abs_path.stat().st_size
        if size > max_bytes:
            index.skipped.append(f"{rel_str} (too large: {size // 1024}KB)")
            continue

        # 拡張子チェック (明示的にテキストと判定できるもののみ)
        ext = abs_path.suffix.lower()

        # バイナリチェック
        try:
            raw = abs_path.read_bytes()
        except (OSError, PermissionError):
            index.skipped.append(f"{rel_str} (permission denied)")
            continue

        if _is_binary(raw):
            index.skipped.append(f"{rel_str} (binary)")
            continue

        # .envなど拡張子なしでも読む、ただし拡張子ありの場合はホワイトリスト確認
        if ext and ext not in TEXT_EXTENSIONS:
            index.skipped.append(f"{rel_str} (unknown extension)")
            continue

        try:
            content = raw.decode("utf-8", errors="replace")
        except Exception:
            index.skipped.append(f"{rel_str} (decode error)")
            continue

        index.files.append(ScannedFile(
            path=rel_str,
            abs_path=str(abs_path),
            content=content,
            size_bytes=size,
            extension=ext,
        ))

        if len(index.files) >= max_total_files:
            break

    return index
