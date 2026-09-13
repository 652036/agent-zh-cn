"""Find likely user-visible application strings missing from the zh-CN dictionary."""
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_zh  # noqa: E402
from scripts import build_js

LOCALE = ROOT / "locales" / "zh-CN.json"
FIELDS = (
    "title",
    "message",
    "label",
    "detail",
    "placeholder",
    "heading",
    "accessibleLabel",
    "aria-label",
    "confirmLabel",
    "cancelLabel",
    "description",
)
FIELD_RE = re.compile(
    rf"(?P<field>{'|'.join(map(re.escape, FIELDS))})"
    r'\s*:\s*"(?P<text>(?:\\.|[^"\\]){2,300})"'
)
# 通用字符串字面量：双引号串，以及仅在结尾带 ${...} 插值的模板字符串。
STRING_RE = re.compile(r'"(?P<text>(?:\\.|[^"\\]){8,300})"')
TEMPLATE_RE = re.compile(r"`(?P<text>(?:\\.|[^`\\$]){8,300}?)(?P<tail>(?:\$\{[^}`]{1,60}\})*)`")
SENTENCE_RE = re.compile(
    r"^[A-Z][A-Za-z0-9 ,.'\u2018\u2019\u201c\u201d\-\u2014\u2013:;()/&%!?+\u2026@#]+$"
)
DEV_MARKERS = re.compile(
    r"\b(?:dev(?:eloper)?|debug|demo|dummy|test|internal|metric|profiler|"
    r"monaco|workbench lifecycle|fake)\b",
    re.IGNORECASE,
)


def decode_js_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace(r"\n", " ").replace(r"\t", " ").replace(r"\"", '"')


def likely_ui(text: str) -> bool:
    if not 2 <= len(text) <= 240 or not re.search(r"[A-Za-z]", text):
        return False
    if re.search(r"[\u3400-\u9fff]|https?://|[/\\][A-Za-z0-9_.-]+[/\\]", text):
        return False
    if re.search(r"[{}=]|;(?! )|=>|async function|\.tsx?\b|^[a-z0-9_.:/-]+$", text):
        return False
    if text.startswith(("$(", "\\", "fsd-", "cursor.", "workbench.")):
        return False
    return True


def likely_sentence(text: str) -> bool:
    """更严格的启发式：像完整英文界面句子的通用字面量。"""
    if not SENTENCE_RE.match(text):
        return False
    words = text.split()
    if len(words) < 3:
        return False
    # 排除代码痕迹：驼峰、下划线、连续大写标识符
    if re.search(r"[a-z][A-Z]|_|\b[A-Z]{4,}\b", text):
        return False
    return True


def scan_file(path: Path, known: set[str]) -> list[dict[str, object]]:
    source = path.read_text(encoding="utf-8", errors="replace")
    newlines = [match.start() for match in re.finditer("\n", source)]
    rows = []
    seen_spans = set()

    def add(start: int, end: int, text: str, field: str) -> None:
        if text in known or not likely_ui(text):
            return
        context = source[max(0, start - 180) : end + 180]
        priority = "low" if DEV_MARKERS.search(context) else "review"
        rows.append(
            {
                "text": text,
                "field": field,
                "priority": priority,
                "source": path.name,
                "line": bisect.bisect_right(newlines, start) + 1,
            }
        )

    for match in FIELD_RE.finditer(source):
        text = re.sub(r"\s+", " ", decode_js_string(match.group("text"))).strip()
        seen_spans.add(match.span("text"))
        add(match.start(), match.end(), text, match.group("field"))

    for match in STRING_RE.finditer(source):
        if match.span("text") in seen_spans:
            continue
        text = re.sub(r"\s+", " ", decode_js_string(match.group("text"))).strip()
        if likely_sentence(text):
            add(match.start(), match.end(), text, "string")

    for match in TEMPLATE_RE.finditer(source):
        text = re.sub(r"\s+", " ", decode_js_string(match.group("text"))).strip()
        if likely_sentence(text):
            field = "template-prefix" if match.group("tail") else "template"
            add(match.start(), match.end(), text, field)

    return rows


def resolve_app(value: str | None, app_id: str | None = None) -> Path:
    found = agent_zh.discover(app_id, value)
    if len(found) != 1:
        raise agent_zh.AgentZhError("请用 --app 或 --path 指定一个待扫描软件。")
    return found[0].root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=list(agent_zh.PROFILES))
    parser.add_argument("--path", "--cursor-app", dest="app_path", help="Executable or resources/app directory")
    parser.add_argument("--output", type=Path, help="write JSON report to this file")
    parser.add_argument("--include-low-priority", action="store_true")
    args = parser.parse_args()

    app = resolve_app(args.app_path, args.app)
    phrase, short, _ = build_js.load_merged_locale()
    known = set(phrase) | set(short)
    sources = [
        app / "out" / "vs" / "workbench" / "workbench.glass.main.js",
        app / "out" / "vs" / "workbench" / "workbench.desktop.main.js",
        app / "out" / "vs" / "workbench" / "workbench.anysphere-ui-automations.js",
        app / "out/vs/sessions/sessions.desktop.main.js",
        app / "out/vs/workbench/windsurf-chat-client/index.js",
        app / "extensions/windsurf/dist/extension.js",
    ]
    rows = []
    for source in sources:
        if source.is_file():
            rows.extend(scan_file(source, known))

    unique = {}
    for row in rows:
        old = unique.get(row["text"])
        if old is None or (old["priority"] == "low" and row["priority"] == "review"):
            unique[row["text"]] = row
    report = sorted(unique.values(), key=lambda row: (row["priority"], str(row["text"]).lower()))
    if not args.include_low_priority:
        report = [row for row in report if row["priority"] != "low"]
    payload = {
        "appPath": str(app),
        "dictionaryEntries": len(known),
        "candidateCount": len(report),
        "candidates": report,
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"wrote {args.output} ({len(report)} candidates)")
    else:
        try:
            print(output, end="")
        except UnicodeEncodeError:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
