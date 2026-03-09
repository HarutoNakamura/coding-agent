"""
Coding Agent - エントリポイント
Usage:
    python main.py                        # サーバー起動
    python main.py --scan /path/to/proj   # CLIでスキャン確認
    python main.py --query "質問" --scan /path/to/proj  # CLIクエリ
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# venv の自動セットアップ: 必要なパッケージがなければ venv を作って再起動する
def _ensure_venv() -> None:
    venv_dir = (Path(__file__).parent / ".venv").resolve()
    venv_python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    sentinel = venv_dir / ".installed"

    # すでにこの venv 内で動いていれば何もしない（sys.prefix で判定）
    if Path(sys.prefix).resolve() == venv_dir:
        return

    # venv がなければ作成
    if not venv_python.exists():
        print("[setup] Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    # sentinel がなければ依存パッケージをインストール（初回のみ）
    if not sentinel.exists():
        req = Path(__file__).parent / "requirements.txt"
        if req.exists():
            print("[setup] Installing dependencies (first run)...")
            result = subprocess.run(
                [str(venv_python), "-m", "pip", "install", "-q", "-r", str(req)]
            )
            if result.returncode == 0:
                sentinel.touch()

    # venv の python で自分自身を再起動
    raise SystemExit(subprocess.run([str(venv_python)] + sys.argv).returncode)

_ensure_venv()

import argparse
import asyncio
import logging
import os
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("coding-agent")


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        logger.warning(f"config.yaml not found at {path}, using defaults")
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


async def ensure_ollama(base_url: str = "http://localhost:11434") -> bool:
    """
    Ollamaが起動していなければ `ollama serve` をバックグラウンドで起動する。
    Returns: 最終的にOllamaが利用可能かどうか
    """
    import httpx

    async def is_up() -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    if await is_up():
        logger.info("Ollama is already running.")
        return True

    logger.info("Ollama not detected. Attempting to start `ollama serve`...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("ollama command not found. Install Ollama: https://ollama.com")
        return False
    except Exception as e:
        logger.warning(f"Failed to start Ollama: {e}")
        return False

    # 起動完了を最大10秒待つ
    for i in range(10):
        await asyncio.sleep(1)
        if await is_up():
            logger.info(f"Ollama started successfully. (waited {i + 1}s)")
            return True

    logger.warning("Ollama did not respond within 10 seconds.")
    return False


async def ensure_llama_server(base_url: str, model_path: str) -> bool:
    """
    llama-serverが起動していなければバックグラウンドで起動する。
    Returns: 最終的にllama-serverが利用可能かどうか
    """
    import httpx

    async def is_up() -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{base_url}/health")
                return r.status_code == 200
        except Exception:
            return False

    if await is_up():
        logger.info("llama-server (PII) is already running.")
        return True

    if not model_path or not Path(model_path).exists():
        logger.warning(f"LFM2 model not found: {model_path}. PII extraction disabled.")
        return False

    logger.info(f"Starting llama-server with {Path(model_path).name} ...")
    try:
        port = base_url.rsplit(":", 1)[-1]
        subprocess.Popen(
            [
                "llama-server",
                "--model", model_path,
                "--host", "127.0.0.1",
                "--port", port,
                "--temp", "0.0",
                "--jinja",
                "--ctx-size", "4096",
                "-ngl", "99",
                "--log-disable",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("llama-server not found. Install with: brew install llama.cpp")
        return False
    except Exception as e:
        logger.warning(f"Failed to start llama-server: {e}")
        return False

    for i in range(15):
        await asyncio.sleep(1)
        if await is_up():
            logger.info(f"llama-server started. (waited {i + 1}s)")
            return True

    logger.warning("llama-server did not respond within 15 seconds.")
    return False


async def run_server(config: dict) -> None:
    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import HTMLResponse

    from src.api.routes import router, state
    from src.llm.local import OllamaClient
    from src.llm.cloud import CloudLLMClient
    from src.llm.pii_extractor import PIIExtractorClient

    app = FastAPI(
        title="Coding Agent",
        description="ローカルLLMプロキシ付きコーディングエージェント",
        version="0.1.0",
    )

    # 状態に設定を注入
    state.config = config

    local_cfg = config.get("local_llm", {})
    base_url = local_cfg.get("base_url", "http://localhost:11434")

    await ensure_ollama(base_url)

    pii_cfg = config.get("pii_llm", {})
    if pii_cfg.get("enable", True):
        pii_base_url = pii_cfg.get("base_url", "http://127.0.0.1:8766")
        pii_model = pii_cfg.get("model_path", "")
        await ensure_llama_server(pii_base_url, pii_model)

    state.ollama = OllamaClient(
        base_url=base_url,
        model=local_cfg.get("model", "llama3.2"),
        timeout=local_cfg.get("timeout", 60),
    )

    # モデル自動選択
    auto_model = await state.ollama.auto_select_model()
    if auto_model:
        state.ollama.model = auto_model
        logger.info(f"Ollama model auto-selected: {auto_model}")
    elif await state.ollama.is_available():
        logger.warning("Ollama is running but no models are installed. Run: ollama pull llama3.2")
    else:
        logger.warning("Ollama not available. LLM masking disabled.")

    pii_cfg = config.get("pii_llm", {})
    if pii_cfg.get("enable", True):
        state.pii = PIIExtractorClient(
            base_url=pii_cfg.get("base_url", "http://127.0.0.1:8766"),
            timeout=pii_cfg.get("timeout", 30),
        )
        if await state.pii.is_available():
            logger.info("LFM2 PII extractor: available at port 8766")
        else:
            logger.info(
                "LFM2 PII extractor: not running "
                "(start with: scripts/start_pii_server.sh)"
            )

    cloud_cfg = config.get("cloud_llm", {})
    state.cloud = CloudLLMClient(
        provider=cloud_cfg.get("provider", "openai"),
        model=cloud_cfg.get("model", "gpt-4o"),
        api_key=cloud_cfg.get("api_key"),
    )
    if state.cloud.is_configured():
        logger.info(f"Cloud LLM: {state.cloud.provider}/{state.cloud.model}")
    else:
        logger.warning(
            "Cloud LLM API key not set. "
            "Set OPENAI_API_KEY or ANTHROPIC_API_KEY environment variable."
        )

    # ルーターをマウント
    app.include_router(router)

    # Web UI 静的ファイル
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/web", StaticFiles(directory=str(web_dir)), name="web")

    @app.get("/", response_class=HTMLResponse)
    async def root():
        index_html = web_dir / "index.html"
        if index_html.exists():
            return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Coding Agent</h1><p>Web UI not found.</p>")

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "127.0.0.1")
    port = server_cfg.get("port", 8765)

    logger.info(f"Starting server at http://{host}:{port}")
    cfg = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    await server.serve()


async def run_cli(config: dict, scan_path: str, query: str | None) -> None:
    """CLIモード: スキャン→マスキング→プレビュー（クラウドには送らない）"""
    from src.scanner.project import scan_project
    from src.masking.mapper import MaskMapper
    from src.llm.local import OllamaClient
    from src.prompt.generator import PromptGenerator

    print(f"\n[Coding Agent CLI]")
    print(f"Scanning: {scan_path}")

    project_cfg = config.get("project", {})
    index = scan_project(
        root=scan_path,
        exclude_patterns=project_cfg.get("exclude", []),
        max_file_size_kb=project_cfg.get("max_file_size_kb", 100),
        max_total_files=project_cfg.get("max_total_files", 200),
    )
    print(f"  Files scanned: {index.summary['total_files']}")
    print(f"  Skipped: {index.summary['skipped_files']}")
    print(f"  Size: {index.summary['total_size_kb']}KB")
    print(f"  Extensions: {index.summary['extensions']}")

    mapper = MaskMapper()

    # ローカルLLM
    ollama = None
    masking_cfg = config.get("masking", {})
    local_cfg = config.get("local_llm", {})
    if masking_cfg.get("enable_local_llm", True):
        ollama = OllamaClient(
            base_url=local_cfg.get("base_url", "http://localhost:11434"),
            model=local_cfg.get("model", "llama3.2"),
        )
        if await ollama.is_available():
            auto = await ollama.auto_select_model()
            if auto:
                ollama.model = auto
            print(f"\n[Ollama available] Model: {ollama.model}")
        else:
            print("\n[Ollama not available] Using regex masking only.")
            ollama = None

    summarized: dict[str, str] = {}
    if ollama and masking_cfg.get("mask_code", False):
        print("Summarizing code files with local LLM...")
        for f in index.files:
            summary = await ollama.summarize_code(f.content)
            summarized[f.path] = summary

    user_query = query or "このプロジェクトの概要と主要なモジュールを説明してください。"
    provider = config.get("cloud_llm", {}).get("provider", "openai")

    gen = PromptGenerator(mapper=mapper, provider=provider)
    result = gen.generate(index, user_query, summarized)

    print(f"\n{'='*60}")
    print(f"Query: {user_query}")
    print(f"{'='*60}")
    print(f"Estimated tokens: {result.estimated_tokens}")
    print(f"Files included: {result.files_included} / {index.summary['total_files']}")
    if result.files_truncated:
        print(f"Files truncated (budget exceeded): {result.files_truncated}")

    if mapper.entries:
        print(f"\n[Masked {len(mapper.entries)} items]")
        for e in mapper.entries:
            preview = e.original[:4] + "****" if len(e.original) > 4 else "****"
            print(f"  {e.token} <- [{e.pattern_name}] {preview}")
    else:
        print("\n[No sensitive data detected]")

    print(f"\n[Prompt Preview (first 1000 chars)]")
    print(result.context[:1000])
    if len(result.context) > 1000:
        print("... (truncated for preview)")

    print(f"\n{'='*60}")
    print("To send to cloud LLM, run: python main.py (server mode)")
    print(f"Then POST http://localhost:8765/api/query")


def main() -> None:
    parser = argparse.ArgumentParser(description="Coding Agent")
    parser.add_argument("--scan", metavar="PATH", help="Project directory to scan (CLI mode)")
    parser.add_argument("--query", metavar="QUERY", help="Query to ask (CLI mode)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.scan:
        asyncio.run(run_cli(config, args.scan, args.query))
    else:
        asyncio.run(run_server(config))


if __name__ == "__main__":
    main()
