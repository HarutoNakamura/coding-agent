# Coding Agent

ローカルLLMをプロキシとして使う**プロンプト特化型コーディングエージェント**。

プロジェクトのコードを読んで、APIキーや個人情報を自動マスキングしてからクラウドLLMに質問できる。

---

## 何ができるか

```
あなたの質問
    ↓
プロジェクトをスキャン（全ファイル読み込み）
    ↓
クエリと関連するファイルだけを自動選択
    ↓
regex でAPIキー・シークレットを [OPENAI_KEY_001] に置換
    ↓
LFM2-350M で日本語PII（人名・住所・電話番号・法人名）を検出・置換
    ↓
詳細なコンテキスト付きプロンプトを生成
    ↓
クラウドLLM（OpenAI / Claude）に送信
```

**既存ツールとの違い:**
- Aider / Cline のようにコードを直接編集しない
- VS Code 拡張ではなく、どのエディタからでも使える
- 「クラウドに何を送るか」をブラウザで確認できる透明性

---

## セットアップ

### 1. 依存パッケージをインストール

```bash
cd coding-agent
pip3 install -r requirements.txt
```

### 2. LFM2モデルをダウンロード（日本語PII抽出用）

```bash
brew install git-lfs llama.cpp
git lfs clone https://huggingface.co/LiquidAI/LFM2-350M-PII-Extract-JP-GGUF
```

`config.yaml` の `pii_llm.model_path` にダウンロード先のパスを設定する。

### 3. APIキーを環境変数に設定

**Claude (Anthropic) を使う場合:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**ChatGPT (OpenAI) を使う場合:**
```bash
export OPENAI_API_KEY=sk-...
```

永続化したい場合は `~/.zshrc` に追加:
```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.zshrc
source ~/.zshrc
```

> **注意:** APIキーをチャットやコードに直接貼り付けないでください。

### 4. `config.yaml` でプロバイダーを選択

```yaml
cloud_llm:
  provider: anthropic  # openai または anthropic
  model: claude-sonnet-4-6
```

---

## 使い方

### Web UI モード（推奨）

```bash
python3 main.py
```

起動時に Ollama・llama-server が自動起動する。ブラウザで `http://localhost:8765` を開く。

1. 左サイドバーの **Project** にスキャンしたいディレクトリのパスを入力
2. **Scan** ボタンを押す → ファイルツリーが表示される
3. 下の **Chat** タブで質問を入力して **Send**
4. 送信前に確認したい場合は **Preview** ボタン → マスク済みプロンプトが見える

---

### CLI モード（クラウドに送らずプレビューのみ）

```bash
# スキャンしてマスキング結果を確認
python3 main.py --scan /path/to/your/project

# 質問も指定する
python3 main.py --scan /path/to/your/project --query "認証まわりの実装を説明して"
```

出力例:
```
[Coding Agent CLI]
Scanning: /path/to/your/project
  Files scanned: 42
  Skipped: 8
  Size: 120KB

[Masked 3 items]
  [SECRET_001]   <- [generic_secret] mysecr****
  [EMAIL_001]    <- [email]          john.****
  [AWS_KEY_001]  <- [aws_access_key] AKIA****

[Prompt Preview (first 1000 chars)]
...
```

---

## REST API

サーバー起動中は以下のエンドポイントを使える。他のツールや自動化スクリプトから呼び出すことも可能。

| Method | Path | 説明 |
|--------|------|------|
| `GET`  | `/api/status` | サーバー状態・LLM接続確認 |
| `POST` | `/api/scan` | プロジェクトをスキャン |
| `GET`  | `/api/project` | スキャン済みプロジェクト情報 |
| `POST` | `/api/query` | 質問をクラウドLLMに送信 |
| `GET`  | `/api/preview` | マスク済みプロンプトをプレビュー |
| `GET`  | `/api/masking/log` | マスキングされた項目の一覧 |
| `POST` | `/api/masking/reset` | マスキングテーブルをリセット |

**例: curlで質問する**
```bash
# スキャン
curl -X POST http://localhost:8765/api/scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/your/project"}'

# 質問（クラウドに送信）
curl -X POST http://localhost:8765/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "このプロジェクトのDB設計を説明して", "send_to_cloud": true}'

# プロンプトのプレビューだけ見る（送信しない）
curl "http://localhost:8765/api/preview?query=概要を教えて"
```

Swagger UI: `http://localhost:8765/docs`

---

## マスキングの仕組み

クラウドに送る前に**3層のマスキング**を適用する。

### Layer 1: regex（常時有効）

パターンが既知の情報を高速検出・置換。

| 対象 | 検出例 | 置換後 |
|------|--------|--------|
| OpenAI APIキー | `sk-proj-abc...` | `[OPENAI_KEY_001]` |
| Anthropic APIキー | `sk-ant-abc...` | `[ANTHROPIC_KEY_001]` |
| AWS アクセスキー | `AKIA...` | `[AWS_KEY_001]` |
| GitHub token | `ghp_...` | `[GITHUB_TOKEN_001]` |
| パスワード・シークレット | `password="xxx"` | `[SECRET_001]` |
| Bearer token | `Authorization: Bearer ...` | `[BEARER_TOKEN_001]` |
| メールアドレス | `user@example.com` | `[EMAIL_001]` |
| プライベートIP | `192.168.x.x` | `[PRIVATE_IP_001]` |
| .env の値 | `API_KEY=xxxxxx` | `[ENV_VAR_001]` |

### Layer 2: LFM2-350M-PII-Extract-JP（日本語PII）

[LiquidAI/LFM2-350M-PII-Extract-JP](https://huggingface.co/LiquidAI/LFM2-350M-PII-Extract-JP) を llama-server でローカル実行。
regexでは検出できない**日本語の意味的なPII**を抽出する。

| 対象 | 例 | 置換後 |
|------|-----|--------|
| 人名 | 田中太郎 | `[HUMAN_NAME_001]` |
| 住所 | 東京都渋谷区1-2-3 | `[ADDRESS_001]` |
| 電話番号 | 090-1234-5678 | `[PHONE_001]` |
| 法人名・機関名 | 株式会社ABC | `[COMPANY_001]` |

モデルは `main.py` 起動時に llama-server(:8766) として自動起動する。
手動で起動する場合: `bash scripts/start_pii_server.sh [Q4_K_M|Q8_0|...]`

### Layer 3: Ollama（オプション）

`config.yaml` で `enable_local_llm: true` かつ Ollama が起動している場合、追加の機密情報検出を行う。
`mask_code: true` にするとコードを意味説明に変換してから送信（生コードを送りたくない場合）。

マッピングテーブルは内部で保持されるため、レスポンスを元に戻すことも可能（`unmask_response: true`）。

---

## クエリベースのファイル選択

質問内容に関連するファイルだけをマスク・送信する。無関係なファイルはコンテキストに含まれない。

| クエリ | 選択されるファイル |
|--------|------------------|
| `マスキングの実装を教えて` | `src/masking/mapper.py`, `patterns.py` |
| `Ollamaのクライアントを見せて` | `src/llm/local.py` |
| `pii extractor の仕組み` | `src/llm/pii_extractor.py` |
| `APIルーターの設定` | `src/api/routes.py` |

英語・日本語（カタカナ含む）の両方に対応。`config.yaml` の `selector.max_files` で選択数を調整できる。

---

## 設定リファレンス (`config.yaml`)

```yaml
local_llm:
  provider: ollama
  base_url: http://localhost:11434
  model: llama3.2        # 起動時に自動検出・選択
  timeout: 60

pii_llm:
  base_url: http://127.0.0.1:8766  # llama-server
  timeout: 30
  enable: true
  model_path: /path/to/LFM2-350M-PII-Extract-JP-Q4_K_M.gguf  # 自動起動するモデル

cloud_llm:
  provider: anthropic    # openai または anthropic
  model: claude-sonnet-4-6
  # api_key: ここに書かず環境変数を使うこと

project:
  exclude:               # スキャンから除外するパターン
    - .git
    - node_modules
    - __pycache__
    - "*.lock"
  max_file_size_kb: 100
  max_total_files: 200

masking:
  enable_regex: true
  enable_local_llm: true
  mask_code: false       # trueにするとコードを説明文に変換（Ollama必要）

selector:
  max_files: 10          # クエリ毎に選択する最大ファイル数
  min_score: 0.1         # このスコア未満はフォールバック対象

server:
  host: 127.0.0.1
  port: 8765
```

---

## ファイル構成

```
coding-agent/
├── main.py               # エントリポイント（Ollama・llama-server自動起動）
├── config.yaml           # 設定ファイル
├── requirements.txt
├── scripts/
│   └── start_pii_server.sh  # llama-server 手動起動スクリプト
├── src/
│   ├── scanner/
│   │   └── project.py    # プロジェクトスキャナ（.gitignore対応）
│   ├── selector/
│   │   └── relevance.py  # クエリベースファイル選択（JP/EN対応）
│   ├── masking/
│   │   ├── patterns.py   # regexマスキングパターン定義
│   │   └── mapper.py     # マッピングテーブル管理
│   ├── llm/
│   │   ├── local.py      # Ollama クライアント
│   │   ├── pii_extractor.py  # LFM2 PIIクライアント（llama-server）
│   │   └── cloud.py      # OpenAI / Anthropic クライアント
│   ├── prompt/
│   │   └── generator.py  # プロンプト生成
│   └── api/
│       ├── routes.py     # FastAPI ルーター
│       └── models.py     # API スキーマ
└── web/
    └── index.html        # Web ダッシュボード
```
