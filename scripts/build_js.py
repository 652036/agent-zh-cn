#!/usr/bin/env python3
"""从已审核词典生成 AgentZh 运行时脚本。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALE = ROOT / "locales" / "zh-CN.json"
RUNTIME = ROOT / "src" / "runtime.js"
OUT = ROOT / "agent-zh.js"


def js_object(d: dict[str, str]) -> str:
    items = []
    for k, v in d.items():
        items.append(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}")
    return "{\n" + ",\n".join(items) + "\n  }"


def js_patterns(rows: list[dict[str, str]]) -> str:
    lines = []
    for row in rows:
        match = row["match"]
        repl = row["replace"]
        lines.append(
            "    [new RegExp("
            + json.dumps(match, ensure_ascii=False)
            + "), "
            + json.dumps(repl, ensure_ascii=False)
            + "]"
        )
    return "[\n" + ",\n".join(lines) + "\n  ]"


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_locale() -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    data = json.loads(LOCALE.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    phrase = data["phrase"]
    short = data.get("short") or {}
    patterns = data.get("patterns") or []
    if not isinstance(phrase, dict) or not isinstance(short, dict) or not isinstance(patterns, list):
        raise ValueError("phrase/short/patterns types are invalid")
    for section, rows in (("phrase", phrase), ("short", short)):
        for key, value in rows.items():
            if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
                raise ValueError(f"{section} entries must be non-empty strings")
    for index, row in enumerate(patterns):
        if not isinstance(row, dict) or set(row) != {"match", "replace"}:
            raise ValueError(f"patterns[{index}] must contain only match and replace")
        if not isinstance(row["match"], str) or not isinstance(row["replace"], str):
            raise ValueError(f"patterns[{index}] values must be strings")
    overlap = sorted(set(phrase) & set(short))
    if overlap:
        raise ValueError(f"keys in both phrase and short: {overlap[:5]}")
    return phrase, short, patterns


def load_merged_locale() -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    phrase, short, patterns = load_locale()
    # Test callers can provide a standalone fixture without importing production data.
    if LOCALE.resolve() != (ROOT / "locales/zh-CN.json").resolve():
        return phrase, short, patterns
    extra = ROOT / "locales/common.json"
    data = json.loads(extra.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    for section, destination, other in (("phrase", phrase, short), ("short", short, phrase)):
        for key, value in data.get(section, {}).items():
            if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
                raise ValueError(f"common.{section} entries must be non-empty strings")
            other.pop(key, None)
            destination[key] = value
    for row in data.get("patterns", []):
        if set(row) != {"match", "replace"} or not all(isinstance(v, str) for v in row.values()):
            raise ValueError("common patterns must contain match/replace strings")
        patterns.append(row)
    return phrase, short, patterns


def render() -> tuple[str, int, int, int]:
    phrase, short, patterns = load_merged_locale()
    tpl = RUNTIME.read_text(encoding="utf-8")
    for token, value in (
        ("__PHRASE__", js_object(phrase)),
        ("__SHORT__", js_object(short)),
        ("__PATTERNS__", js_patterns(patterns)),
    ):
        if tpl.count(token) != 1:
            raise ValueError(f"expected exactly one {token} in runtime.js")
        tpl = tpl.replace(token, value, 1)
    if re.search(r"__[A-Z]+__", tpl):
        raise ValueError("unreplaced placeholder left in output")
    return tpl, len(phrase), len(short), len(patterns)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="检查生成脚本是否与词典和运行时模板一致")
    args = parser.parse_args()
    try:
        tpl, phrase_count, short_count, pattern_count = render()
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"构建失败：{error}", file=sys.stderr)
        return 1
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != tpl:
            print("生成脚本已过期；请运行 python scripts/build_js.py", file=sys.stderr)
            return 1
        print("agent-zh.js 已是最新版本")
        return 0
    OUT.write_bytes(tpl.encode("utf-8"))
    print(
        f"已生成 {OUT}（{OUT.stat().st_size} 字节，"
        f"长文本={phrase_count}，短文本={short_count}，模式={pattern_count}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
