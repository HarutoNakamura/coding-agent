"""
FastAPI routes for the coding agent server.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from ..scanner.project import scan_project, ProjectIndex
from ..masking.mapper import MaskMapper
from ..llm.local import OllamaClient
from ..llm.cloud import CloudLLMClient
from ..llm.pii_extractor import PIIExtractorClient
from ..prompt.generator import PromptGenerator
from ..selector.relevance import FileSelector
from .models import (
    ScanRequest, QueryRequest,
    PreviewResponse, QueryResponse,
    ProjectInfo, MaskingLogResponse, StatusResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---- アプリケーション状態 (シングルトン的に使う) ----

class AgentState:
    MAX_HISTORY = 20

    def __init__(self) -> None:
        self.index: Optional[ProjectIndex] = None
        self.mapper = MaskMapper()
        self.ollama: Optional[OllamaClient] = None
        self.pii: Optional[PIIExtractorClient] = None
        self.cloud: Optional[CloudLLMClient] = None
        self.config: dict = {}
        self.summarized: dict[str, str] = {}  # path -> ollama summary
        self._selector: Optional[FileSelector] = None
        self.prompt_history: list[dict] = []  # 直近のプロンプトスナップショット

    @property
    def selector(self) -> FileSelector:
        if self._selector is None:
            sel_cfg = self.config.get("selector", {})
            self._selector = FileSelector(
                max_files=sel_cfg.get("max_files", 10),
                min_score=sel_cfg.get("min_score", 0.1),
            )
        return self._selector

    def save_prompt_snapshot(self, snapshot: dict) -> None:
        self.prompt_history.insert(0, snapshot)
        if len(self.prompt_history) > self.MAX_HISTORY:
            self.prompt_history.pop()

    def reset_masking(self) -> None:
        self.mapper.reset()
        self.summarized.clear()
        self.prompt_history.clear()


state = AgentState()


def get_state() -> AgentState:
    return state


# ---- ヘルスチェック / ステータス ----

@router.get("/api/status", response_model=StatusResponse)
async def get_status():
    ollama_ok = False
    if state.ollama:
        ollama_ok = await state.ollama.is_available()

    pii_ok = False
    if state.pii:
        pii_ok = await state.pii.is_available()

    cloud_ok = state.cloud.is_configured() if state.cloud else False
    provider = state.cloud.provider if state.cloud else "not configured"
    model = state.cloud.model if state.cloud else "not configured"

    return StatusResponse(
        status="ok",
        project_loaded=state.index is not None,
        local_llm_available=ollama_ok,
        pii_llm_available=pii_ok,
        cloud_llm_configured=cloud_ok,
        provider=provider,
        model=model,
    )


# ---- プロジェクトスキャン ----

@router.post("/api/scan")
async def scan(req: ScanRequest):
    """プロジェクトをスキャンしてインデックスを構築する。"""
    path = os.path.expanduser(req.path)
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail=f"Directory not found: {path}")

    cfg = state.config
    project_cfg = cfg.get("project", {})

    state.index = scan_project(
        root=path,
        exclude_patterns=project_cfg.get("exclude", []),
        max_file_size_kb=project_cfg.get("max_file_size_kb", 100),
        max_total_files=project_cfg.get("max_total_files", 200),
    )
    state.reset_masking()

    # ローカルLLMが有効で mask_code=True の場合、バックグラウンドで要約
    masking_cfg = cfg.get("masking", {})
    if masking_cfg.get("enable_local_llm") and masking_cfg.get("mask_code"):
        if state.ollama and await state.ollama.is_available():
            for f in state.index.files:
                summary = await state.ollama.summarize_code(f.content)
                state.summarized[f.path] = summary

    return {
        "message": "Scan complete",
        "summary": state.index.summary,
    }


# ---- プロジェクト情報 ----

@router.get("/api/project", response_model=ProjectInfo)
async def get_project():
    if not state.index:
        raise HTTPException(status_code=404, detail="No project loaded. Call POST /api/scan first.")
    s = state.index.summary
    return ProjectInfo(
        root=state.index.root,
        total_files=s["total_files"],
        skipped_files=s["skipped_files"],
        extensions=s["extensions"],
        total_size_kb=s["total_size_kb"],
        file_tree=state.index.file_tree,
    )


# ---- プロンプトプレビュー ----

@router.get("/api/prompt-history")
async def get_prompt_history():
    """Sendで送信したプロンプトの履歴一覧（最新順）。"""
    return {
        "entries": [
            {"index": i, "query": e["query"]}
            for i, e in enumerate(state.prompt_history)
        ]
    }


@router.get("/api/preview")
async def preview_prompt(index: int = 0):
    """直近のSendで生成されたマスク済みプロンプトを返す。"""
    if not state.prompt_history:
        raise HTTPException(status_code=404, detail="まだSendが押されていません。")
    if index >= len(state.prompt_history):
        raise HTTPException(status_code=404, detail=f"index {index} は範囲外です。")

    entry = state.prompt_history[index]
    return PreviewResponse(
        masked_prompt=entry["masked_prompt"],
        estimated_tokens=entry["estimated_tokens"],
        files_included=entry["files_included"],
        files_truncated=0,
        masking_log=entry["masking_log"],
        selected_files=entry["selected_files"],
    )


# ---- メインクエリ ----

@router.post("/api/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """
    ユーザーの質問をプロジェクトコンテキストとともにクラウドLLMに送る。
    """
    if not state.index:
        raise HTTPException(status_code=404, detail="No project loaded. Call POST /api/scan first.")

    if req.send_to_cloud and (not state.cloud or not state.cloud.is_configured()):
        raise HTTPException(
            status_code=400,
            detail="Cloud LLM not configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY."
        )

    cfg = state.config
    provider = cfg.get("cloud_llm", {}).get("provider", "openai")

    # クエリに関連するファイルだけを選択
    selected_files = state.selector.select(state.index.files, req.query)

    # 選択されたファイルだけをマスク
    llm_masked: dict[str, str] = {}
    masking_cfg = cfg.get("masking", {})
    pii_cfg = cfg.get("pii_llm", {})
    pii_enabled = pii_cfg.get("enable", True)

    for f in selected_files:
        content = f.content

        # Ollama: APIキー・シークレット系
        if masking_cfg.get("enable_local_llm", True) and state.ollama and await state.ollama.is_available():
            detections = await state.ollama.detect_secrets(content)
            if detections:
                content = state.mapper.mask_detections(content, detections)

        # LFM2: 日本語PII（人名・住所・電話番号・法人名）
        if pii_enabled and state.pii and await state.pii.is_available():
            pii_detections = await state.pii.extract_pii(content)
            if pii_detections:
                content = state.mapper.mask_detections(content, pii_detections)

        llm_masked[f.path] = content

    gen = PromptGenerator(
        mapper=state.mapper,
        max_context_tokens=cfg.get("max_context_tokens", 30_000),
        provider=provider,
    )
    prompt_result = gen.generate(state.index, req.query, state.summarized, llm_masked, files=selected_files)

    response_text = ""
    cost = None
    local_llm_used = bool(state.summarized)

    if req.send_to_cloud:
        try:
            response_text = await state.cloud.chat(prompt_result.messages)

            # コスト概算（文字数ベースの推定: 1トークン≒4文字）
            prompt_tokens = prompt_result.estimated_tokens
            completion_tokens = len(response_text) // 4
            cost = state.cloud.estimate_cost(prompt_tokens, completion_tokens)

        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Cloud LLM error: {e}")

        if req.unmask_response:
            response_text = state.mapper.unmask(response_text)
    else:
        # クラウドに送らない場合はプロンプトのプレビューを返す
        response_text = f"[Preview only - not sent to cloud]\n\n{prompt_result.context[:3000]}"

    # プロンプトスナップショットを履歴に保存
    state.save_prompt_snapshot({
        "query": req.query,
        "masked_prompt": prompt_result.context,
        "estimated_tokens": prompt_result.estimated_tokens,
        "files_included": prompt_result.files_included,
        "selected_files": [f.path for f in selected_files],
        "masking_log": [
            {
                "token": e.token,
                "pattern": e.pattern_name,
                "original": e.original[:40] + ("..." if len(e.original) > 40 else ""),
            }
            for e in state.mapper.entries
        ],
    })

    return QueryResponse(
        query=req.query,
        response=response_text,
        estimated_tokens=prompt_result.estimated_tokens,
        files_included=prompt_result.files_included,
        masking_count=len(state.mapper.entries),
        cost_estimate=cost,
        local_llm_used=local_llm_used,
        selected_files=[f.path for f in selected_files],
    )


# ---- マスキングログ ----

@router.get("/api/masking/log", response_model=MaskingLogResponse)
async def masking_log():
    entries = [
        {
            "token": e.token,
            "pattern_name": e.pattern_name,
            "original_length": len(e.original),
            # セキュリティのため元の値は返さない（確認したい場合はローカルのみ）
            "preview": e.original[:4] + "****" if len(e.original) > 4 else "****",
        }
        for e in state.mapper.entries
    ]
    return MaskingLogResponse(entries=entries, total=len(entries))


# ---- マスキングリセット ----

@router.post("/api/masking/reset")
async def reset_masking():
    state.reset_masking()
    return {"message": "Masking table reset"}
