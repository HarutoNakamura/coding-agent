# Coding Agent

ローカルLLMをプロキシとして使う**プロンプト特化型コーディングエージェント**。

プロジェクトのコードを読んで、APIキーや秘密情報を自動マスキングしてからクラウドLLMに質問できる。

---

## 何ができるか

```
あなたの質問
    ↓
プロジェクトをスキャン（全ファイル読み込み）
    ↓
Ollama（任意）でコードを意味説明に変換
    ↓
APIキー・シークレットを [SECRET_001] に置換
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

### 2. APIキーを環境変数に設定

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

### 3. `config.yaml` でプロバイダーを選択

```yaml
cloud_llm:
  provider: anthropic  # openai または anthropic
  model: claude-sonnet-4-6  # 使いたいモデル
```

---

## 使い方

### Web UI モード（推奨）

```bash
python3 main.py
```

ブラウザで `http://localhost:8765` を開く。

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
  [SECRET_001] <- [generic_secret] mysecr****
  [EMAIL_001]  <- [email]          john.****
  [AWS_KEY_001]<- [aws_access_key] AKIA****

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

クラウドに送る前に以下のパターンを自動検出して `[TOKEN_001]` 形式に置換する。

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

マッピングテーブルは内部で保持されるため、レスポンスを元に戻すことも可能（`unmask_response: true`）。

### Ollama によるコードサマライズ（オプション）

`config.yaml` で `mask_code: true` にすると、Ollama がコードを意味説明に変換してからクラウドに送る（生のコードを送りたくない場合に使う）。

```yaml
masking:
  mask_code: true  # Ollama が必要
```

Ollama のインストール: https://ollama.com
モデルは自動検出（llama3.2 / mistral / codellama など）。

---

## 設定リファレンス (`config.yaml`)

```yaml
local_llm:
  provider: ollama
  base_url: http://localhost:11434
  model: llama3.2        # 起動時に自動検出・選択
  timeout: 60

cloud_llm:
  provider: anthropic    # openai または anthropic
  model: claude-sonnet-4-6
  # api_key: ここに書かず環境変数を使うこと

project:
  scan_path: ./
  exclude:               # スキャンから除外するパターン
    - .git
    - node_modules
    - __pycache__
    - "*.lock"
  max_file_size_kb: 100  # 1ファイルの上限
  max_total_files: 200   # 最大ファイル数

masking:
  enable_regex: true     # regex マスキング（常時推奨）
  enable_local_llm: true # Ollama によるマスキング
  mask_code: false       # コードを説明文に変換（遅い）

server:
  host: 127.0.0.1
  port: 8765
```

---

## ファイル構成

```
coding-agent/
├── main.py               # エントリポイント
├── config.yaml           # 設定ファイル
├── requirements.txt
├── src/
│   ├── scanner/
│   │   └── project.py    # プロジェクトスキャナ
│   ├── masking/
│   │   ├── patterns.py   # マスキングパターン定義
│   │   └── mapper.py     # マッピングテーブル管理
│   ├── llm/
│   │   ├── local.py      # Ollama クライアント
│   │   └── cloud.py      # OpenAI / Anthropic クライアント
│   ├── prompt/
│   │   └── generator.py  # プロンプト生成
│   └── api/
│       ├── routes.py     # FastAPI ルーター
│       └── models.py     # API スキーマ
└── web/
    └── index.html        # Web ダッシュボード
```
