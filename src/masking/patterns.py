"""
Regex-based sensitive data detection and masking patterns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class MaskPattern:
    name: str
    pattern: re.Pattern
    label_prefix: str  # マスク後のラベル例: SECRET, KEY, EMAIL


PATTERNS: list[MaskPattern] = [
    # OpenAI API key (旧形式: sk-xxx48文字, 新形式: sk-proj-xxx)
    MaskPattern("openai_key", re.compile(r"sk-(?:proj-)?[a-zA-Z0-9_-]{20,}"), "OPENAI_KEY"),
    # Anthropic API key
    MaskPattern("anthropic_key", re.compile(r"sk-ant-[a-zA-Z0-9_-]{40,}"), "ANTHROPIC_KEY"),
    # AWS Access Key
    MaskPattern("aws_access_key", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"), "AWS_KEY"),
    # AWS Secret Key (= 40文字base64-like, よくある変数名の後)
    MaskPattern("aws_secret", re.compile(
        r"(?i)aws_secret(?:_access)?_key\s*[=:]\s*['\"]?([a-zA-Z0-9+/]{40})['\"]?"
    ), "AWS_SECRET"),
    # GitHub token
    MaskPattern("github_token", re.compile(r"ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82}"), "GITHUB_TOKEN"),
    # Generic secret/password/token assignment
    MaskPattern("generic_secret", re.compile(
        r'(?i)(?:secret|password|passwd|token|api_key|apikey|auth_key|private_key)'
        r'\s*[=:]\s*[\'"]([^\'"]{8,})[\'"]'
    ), "SECRET"),
    # Bearer token in headers
    MaskPattern("bearer_token", re.compile(
        r'(?i)(?:Authorization|Bearer)\s*[:\s]+[\'"]?([a-zA-Z0-9._-]{20,})[\'"]?'
    ), "BEARER_TOKEN"),
    # Email address
    MaskPattern("email", re.compile(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    ), "EMAIL"),
    # IPv4 address (プライベートIPのみマスク: 192.168.x.x, 10.x.x.x, 172.16-31.x.x)
    MaskPattern("private_ip", re.compile(
        r'(?:192\.168\.|10\.|172\.(?:1[6-9]|2[0-9]|3[01])\.)\d{1,3}\.\d{1,3}'
    ), "PRIVATE_IP"),
    # .envファイルのVAR=value形式（8文字以上の値）
    MaskPattern("env_value", re.compile(
        r'^([A-Z_][A-Z0-9_]*)\s*=\s*([^\s#\n]{8,})$', re.MULTILINE
    ), "ENV_VAR"),
]
