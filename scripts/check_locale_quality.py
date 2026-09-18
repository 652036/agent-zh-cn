#!/usr/bin/env python3
"""检查 AgentZh 中文词典质量与旧项目残留。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"

ALLOWED_ENGLISH_VALUES = {
    "MCP",
    "DeepWiki",
    "Deep&&Wiki",
    "&&Lifeguard",
    "Slack",
    "Cascade",
    "Devin Desktop",
}
CANONICAL = {
    "New session": "新建会话",
    "Open PR": "打开拉取请求",
    "View PR": "查看拉取请求",
    "Create PR": "创建拉取请求",
    "Open pull request": "打开拉取请求",
    "Send to Chat": "发送到聊天",
    "Ask": "提问",
    "Off": "关闭",
    "Effort": "推理强度",
    "Running": "正在运行",
}
FORBIDDEN_REPO_ARTIFACTS = {
    "CursorZh.ps1",
    "cursor_zh.py",
    "cursor-zh.js",
    "scripts/scan_cursor_strings.py",
    "tests/test_installer.py",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def translation_items(path: Path):
    data = load(path)
    if path.name in {"zh-CN.json", "common.json"}:
        for section in ("phrase", "short"):
            for key, value in data.get(section, {}).items():
                yield key, value
    else:
        for key, value in data.items():
            if isinstance(value, str):
                yield key, value


def is_untranslated(value: str) -> bool:
    if value in ALLOWED_ENGLISH_VALUES:
        return False
    return bool(re.search(r"[A-Za-z]{2}", value)) and not bool(re.search(r"[\u3400-\u9fff]", value))


def main() -> int:
    errors = []
    tables = {}
    for name in ("zh-CN.json", "common.json", "devin-nls.json"):
        path = LOCALES / name
        rows = dict(translation_items(path))
        tables[name] = rows
        for key, value in rows.items():
            if is_untranslated(value):
                errors.append(f"{name}: 疑似未翻译：{key!r} -> {value!r}")

    for key, expected in CANONICAL.items():
        seen = []
        for name, rows in tables.items():
            if key in rows:
                seen.append((name, rows[key]))
        wrong = [(name, value) for name, value in seen if value != expected]
        if wrong:
            errors.append(f"术语不一致：{key!r} 应为 {expected!r}，实际 {wrong!r}")

    for rel in sorted(FORBIDDEN_REPO_ARTIFACTS):
        if (ROOT / rel).exists():
            errors.append(f"发现旧 CursorZh 仓库残留：{rel}")

    if errors:
        print("本地化质量检查失败：", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("本地化质量检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
