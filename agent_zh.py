"""AgentZh：为受支持的 VS Code 系桌面应用提供可逆的简体中文本地化。"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

VERSION = "2.1.0"
HERE = Path(__file__).resolve().parent
PROFILES = json.loads((HERE / "src/apps.json").read_text(encoding="utf-8"))
DICT_SRC = HERE / "agent-zh.js"
SCRIPT_TAG = b'<script src="./agent-zh.js"></script>'
OLD_CURSOR_TAG = b'<script src="./cursor-zh.js"></script>'
ENTRYPOINTS = (
    "out/vs/code/electron-sandbox/workbench/workbench.html",
    "out/vs/code/electron-browser/workbench/workbench.html",
    "out/vs/sessions/electron-browser/sessions.html",
    "out/vs/sessions/electron-sandbox/sessions.html",
)


class AgentZhError(RuntimeError):
    """可恢复、可直接展示给用户的安装器错误。"""


class ChineseArgumentParser(argparse.ArgumentParser):
    """使用中文标题和错误前缀的命令行解析器。"""

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：", 1)

    def format_help(self) -> str:
        return super().format_help().replace("usage:", "用法：", 1)

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"参数错误：{message}\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def vscode_checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def config_base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def backup_root() -> Path:
    return config_base() / "AgentZh/backup"


@dataclass(frozen=True)
class Installation:
    app_id: str
    root: Path

    @property
    def profile(self) -> dict:
        return PROFILES[self.app_id]

    @property
    def name(self) -> str:
        return self.profile["name"]

    @property
    def product(self) -> dict:
        return read_json(self.root / "product.json")

    @property
    def version(self) -> str:
        return str(read_json(self.root / "package.json").get("version", "unknown"))

    @property
    def entrypoints(self) -> list[Path]:
        return [self.root / rel for rel in ENTRYPOINTS if (self.root / rel).is_file()]

    @property
    def install_dir(self) -> Path:
        directory = self.root.parent.parent
        # Recent Windows VS Code builds keep resources under an active version folder.
        if sys.platform == "win32" and re.fullmatch(r"[a-fA-F0-9]{8,40}", directory.name):
            if any((directory.parent / name).is_file() for name in self.profile["executables"]):
                return directory.parent
        return directory

    @property
    def portable_data(self) -> Path | None:
        folder = self.install_dir / "data"
        return folder if sys.platform != "darwin" and folder.is_dir() else None

    @property
    def user_data(self) -> Path:
        if self.portable_data:
            return self.portable_data / "user-data"
        return config_base() / self.profile["userData"]

    @property
    def extensions(self) -> Path:
        if self.portable_data:
            return self.portable_data / "extensions"
        return Path.home() / self.profile["dataFolder"] / "extensions"

    @property
    def argv(self) -> Path:
        if self.portable_data:
            return self.user_data / "argv.json"
        return Path.home() / self.profile["dataFolder"] / "argv.json"

    @property
    def executable(self) -> Path | None:
        parents = [self.install_dir]
        if sys.platform == "darwin":
            parents.insert(0, self.root.parent.parent / "MacOS")
        for parent in parents:
            for name in self.profile["executables"]:
                candidate = parent / name
                if candidate.is_file():
                    return candidate
        return None

    @property
    def cli(self) -> Path | None:
        # Upstream and forks have used both install/bin and resources/app/bin.
        for base in (self.install_dir / "bin", self.root / "bin"):
            for name in self.profile["cliNames"]:
                candidate = base / (name + (".cmd" if sys.platform == "win32" else ""))
                if candidate.is_file():
                    return candidate
        return None


def normalize_roots(value: Path) -> list[Path]:
    value = value.expanduser()
    if value.suffix.lower() == ".app":
        return [value / "Contents/Resources/app"]
    if value.is_file():
        value = value.parent
    roots = [value, value / "resources/app", value / "Contents/Resources/app"]
    if sys.platform == "win32" and value.is_dir():
        # Use the launcher's active resource version; never patch arbitrary old folders.
        for manifest in value.glob("*.VisualElementsManifest.xml"):
            if manifest.name.startswith("old_"):
                continue
            text = manifest.read_text(encoding="utf-8-sig")
            for version in re.findall(r'"([a-fA-F0-9]{8,40})[\\/]resources[\\/]app[\\/]', text):
                roots.insert(0, value / version / "resources/app")
    return roots


def identify(root: Path, app_id: str | None = None) -> Installation | None:
    try:
        product = read_json(root / "product.json")
        read_json(root / "package.json")
    except (OSError, ValueError):
        return None
    for key, profile in PROFILES.items():
        if app_id and key != app_id:
            continue
        if product.get("applicationName") not in profile["applicationNames"]:
            continue
        result = Installation(key, root.resolve())
        if result.entrypoints:
            return result
    return None


def registry_roots(app_id: str) -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg

    found = []
    for hive, key_path in (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                            if PROFILES[app_id]["name"].casefold() not in name.casefold():
                                continue
                            for field in ("InstallLocation", "DisplayIcon"):
                                try:
                                    raw = str(winreg.QueryValueEx(sub, field)[0])
                                    found.append(Path(raw.rsplit(",", 1)[0].strip(' "')))
                                    break
                                except OSError:
                                    continue
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def candidates(app_id: str) -> list[Path]:
    profile = PROFILES[app_id]
    values = []
    for key in (f"{app_id.upper()}_PATH", f"{app_id.upper()}_APP"):
        if os.environ.get(key):
            values.append(Path(os.environ[key]))
    if sys.platform == "win32":
        bases = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"]
        bases += [Path(os.environ.get(k, fallback)) for k, fallback in
                  (("ProgramFiles", "C:/Program Files"), ("ProgramFiles(x86)", "C:/Program Files (x86)"))]
        values += [base / name for base in bases for name in profile["windowsDirs"]]
        values += registry_roots(app_id)
    elif sys.platform == "darwin":
        values += [base / (profile["macBundle"] + ".app") for base in
                   (Path("/Applications"), Path.home() / "Applications")]
    else:
        values += [base / name for base in (Path("/usr/share"), Path("/usr/lib"), Path("/opt"),
                   Path.home() / ".local/share") for name in profile["linuxDirs"]]
        for cli in profile["cliNames"]:
            command = shutil.which(cli)
            if command:
                values.append(Path(command).resolve())
    return [root for value in values for root in normalize_roots(value)]


def discover(app_id: str | None = None, app_path: str | None = None) -> list[Installation]:
    if app_path:
        for root in normalize_roots(Path(app_path)):
            installation = identify(root, app_id)
            if installation:
                return [installation]
        raise AgentZhError("指定目录与所选软件不匹配，或没有受支持的 workbench 入口。")
    found, seen = [], set()
    for key in ([app_id] if app_id else PROFILES):
        for root in candidates(key):
            installation = identify(root, key)
            if installation and str(installation.root) not in seen:
                found.append(installation)
                seen.add(str(installation.root))
    return found


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    tmp = path.with_name(f".{path.name}.agent-zh-{os.getpid()}-{time.time_ns()}.tmp")
    try:
        tmp.write_bytes(data)
        if mode is not None:
            tmp.chmod(mode | stat.S_IWUSR)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def commit_writes(writes: dict[Path, bytes | None]) -> None:
    """Rollback every file in the transaction, including user configuration."""
    original = {p: p.read_bytes() if p.exists() else None for p in writes}
    done = []
    try:
        for path, data in writes.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, data)
            done.append(path)
    except OSError as error:
        failures = []
        for path in reversed(done):
            try:
                if original[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, original[path])
            except OSError as rollback_error:
                failures.append(f"{path}: {rollback_error}")
        if failures:
            raise AgentZhError(f"写入失败且回滚不完整：{error}\n" + "\n".join(failures)) from error
        raise


def strip_html(raw: bytes) -> bytes:
    for tag in (SCRIPT_TAG, OLD_CURSOR_TAG):
        for prefix in (b"\r\n\t", b"\n\t", b"\r\n", b"\n", b""):
            raw = raw.replace(prefix + tag, b"")
    return raw


def inject_html(raw: bytes) -> bytes:
    clean = strip_html(raw)
    startup = re.search(rb'<script\b[^>]*\bsrc=["\']\./(?:workbench|sessions)\.js["\'][^>]*>\s*</script>', clean)
    if not startup:
        raise AgentZhError("找不到 workbench.js 或 sessions.js 启动标签；该版本尚未适配。")
    newline = b"\r\n" if b"\r\n" in clean else b"\n"
    return clean[:startup.end()] + newline + b"\t" + SCRIPT_TAG + clean[startup.end():]


def product_with_checksums(raw: bytes, htmls: dict[str, bytes]) -> bytes:
    # Preserve formatting, BOM and all unrelated product fields.
    text = raw.decode("utf-8")
    product = json.loads(text.lstrip("\ufeff"))
    checksums = product.get("checksums", {})
    for rel, html in htmls.items():
        key = rel.removeprefix("out/")
        if key not in checksums:
            continue
        pattern = re.compile(r'(' + re.escape(json.dumps(key)) + r'\s*:\s*")[^"]*(")')
        text, count = pattern.subn(lambda m: m[1] + vscode_checksum(html) + m[2], text, count=1)
        if count != 1:
            raise AgentZhError(f"无法更新入口校验值：{key}")
    return text.encode("utf-8")


def process_names(app: Installation) -> list[str]:
    if sys.platform == "win32":
        return [n for n in app.profile["executables"] if n.endswith(".exe")]
    if sys.platform == "darwin":
        return [app.profile["executables"][-1]]
    return [n for n in app.profile["executables"] if not n.endswith(".exe")]


def windows_process_ids(app: Installation) -> list[int]:
    names = ",".join("'" + Path(n).stem.replace("'", "''") + "'" for n in process_names(app))
    script = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
              f"Get-Process -Name {names} -ErrorAction SilentlyContinue | "
              "Select-Object Id,Path | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    # Get-Process may exit nonzero when there are no matches; empty output is normal.
    if not result.stdout.strip():
        if result.stderr.strip():
            raise AgentZhError("无法读取目标软件进程状态，请先手动退出该软件。")
        return []
    rows = json.loads(result.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    expected = {str((app.install_dir / name).resolve()).casefold() for name in process_names(app)}
    found = []
    for row in rows:
        if not row.get("Path"):
            raise AgentZhError("无法核对同名进程的程序路径，请手动退出该软件后重试。")
        if str(Path(row["Path"]).resolve()).casefold() in expected:
            found.append(int(row["Id"]))
    return found


def app_running(app: Installation) -> bool:
    if sys.platform == "win32":
        return bool(windows_process_ids(app))
    for name in process_names(app):
        if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
            return True
    return False


def stop_app(app: Installation) -> None:
    if sys.platform == "win32":
        for pid in windows_process_ids(app):
            subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"], capture_output=True)
    else:
        for name in process_names(app):
            subprocess.run(["pkill", "-x", name], capture_output=True)
    for _ in range(40):
        if not app_running(app):
            return
        time.sleep(0.25)
    raise AgentZhError(f"{app.name} 仍在运行，请手动退出。")


def start_app(app: Installation) -> None:
    if not app.executable:
        print(f"未找到 {app.name} 可执行文件，请手动启动。")
        return
    subprocess.Popen([str(app.executable)], close_fds=True)


def ensure_stopped(app: Installation, do_kill: bool) -> None:
    if app_running(app):
        if not do_kill:
            raise AgentZhError(f"请先退出 {app.name}，或加上 --kill 允许关闭该软件。")
        stop_app(app)


def backup(app: Installation, originals: dict[Path, bytes | None]) -> Path:
    app_hash = sha256(str(app.root).encode())[:12]
    # Paths, missing files and empty files are distinct snapshot states.
    identity = [(str(p), None if data is None else sha256(data)) for p, data in originals.items()]
    baseline_hash = sha256(json.dumps(identity, ensure_ascii=False).encode())[:16]
    version = re.sub(r"[^\w.-]", "_", app.version)
    folder = backup_root() / app.app_id / app_hash / version / baseline_hash
    manifest = folder / "manifest.json"
    if manifest.is_file():
        return folder
    entries = []
    for i, (p, data) in enumerate(originals.items()):
        item = {"path": str(p), "existed": data is not None}
        if data is not None:
            target = folder / f"{i}-{p.name}.orig"
            atomic_write_bytes(target, data)
            item.update(backup=target.name, sha256=sha256(data))
        entries.append(item)
    atomic_write_bytes(manifest, (json.dumps({"installerVersion": VERSION, "app": app.app_id,
        "appVersion": app.version, "appPath": str(app.root), "files": entries}, ensure_ascii=False, indent=2) + "\n").encode())
    return folder


JSONC_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/|[{}\[\]:,]|\s+|[^\s{}\[\]:,/]+|.')


def jsonc_tokens(text: str) -> list[re.Match]:
    return [m for m in JSONC_TOKEN.finditer(text) if not m[0].isspace()
            and not m[0].startswith(("//", "/*")) and m[0] != "\ufeff"]


def parse_jsonc(text: str) -> dict:
    tokens = jsonc_tokens(text)
    cleaned = "".join(m[0] for i, m in enumerate(tokens)
                      if not (m[0] == "," and i + 1 < len(tokens) and tokens[i + 1][0] in ("}", "]")))
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise AgentZhError("配置文件必须是 JSON 对象。")
    return result


def locale_bytes(raw: bytes | None) -> bytes:
    if raw is None:
        return b'{\n  "locale": "zh-cn"\n}\n'
    text = raw.decode("utf-8")
    parse_jsonc(text)
    tokens = jsonc_tokens(text)
    depth, matches = 0, []
    for i, token in enumerate(tokens):
        if token[0] in ("{", "["):
            depth += 1
        elif token[0] in ("}", "]"):
            depth -= 1
        elif depth == 1 and token[0] == '"locale"' and i + 2 < len(tokens) and tokens[i + 1][0] == ":":
            matches.append(tokens[i + 2])
    if len(matches) > 1:
        raise AgentZhError("配置中有重复 locale 字段，请先修正。")
    if matches:
        value = matches[0]
        if not value[0].startswith('"'):
            raise AgentZhError("现有 locale 字段不是字符串。")
        text = text[:value.start()] + '"zh-cn"' + text[value.end():]
    else:
        start = tokens[0].end()
        newline = "\r\n" if "\r\n" in text else "\n"
        comma = "," if len(tokens) > 2 else ""
        text = text[:start] + newline + '  "locale": "zh-cn"' + comma + text[start:]
    parse_jsonc(text)
    return text.encode("utf-8")


def translation_map(app_id: str) -> dict[str, str]:
    dictionary = read_json(HERE / "locales/zh-CN.json")
    result = {**dictionary.get("phrase", {}), **dictionary.get("short", {})}
    for name in ("common", app_id):
        extra = HERE / f"locales/{name}.json"
        if extra.is_file():
            data = read_json(extra)
            result.update(data.get("phrase", {}))
            result.update(data.get("short", {}))
    supplemental = HERE / f"locales/{app_id}-nls.json"
    if supplemental.is_file():
        result.update(read_json(supplemental))
    return result


def install_ms_pack(app: Installation) -> bool:
    cli = app.cli
    if not cli:
        print(f"未找到 {app.name} CLI，跳过语言包下载。")
        return False
    print(f"正在为 {app.name} 安装微软简体中文语言包…")
    try:
        result = subprocess.run([str(cli), "--install-extension", "MS-CEINTL.vscode-language-pack-zh-hans"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"语言包安装未完成：{error}")
        return False
    if result.returncode:
        print("语言包安装失败；补充界面翻译仍可使用。" + (result.stderr or result.stdout)[-500:])
        return False
    return True


def find_language_pack(app: Installation) -> tuple[dict, dict] | None:
    packs = []
    if not app.extensions.is_dir():
        return None
    for folder in app.extensions.glob("ms-ceintl.vscode-language-pack-zh-hans-*"):
        try:
            package = read_json(folder / "package.json")
            for localization in package.get("contributes", {}).get("localizations", []):
                if localization.get("languageId") != "zh-cn":
                    continue
                paths = {}
                for item in localization.get("translations", []):
                    target = (folder / item["path"]).resolve()
                    # Extensions are external input; translation paths stay inside the extension.
                    if folder.resolve() not in target.parents:
                        raise AgentZhError("语言包包含越界路径。")
                    if target.is_file():
                        paths[item["id"]] = str(target)
                if "vscode" not in paths:
                    continue
                entry = {"extensions": [{"version": package["version"], "extensionIdentifier":
                         {"id": "ms-ceintl.vscode-language-pack-zh-hans"}}],
                         "translations": paths, "label": "中文(简体)"}
                version = tuple(int(n) for n in re.findall(r"\d+", package["version"]))
                packs.append((version, entry, read_json(Path(paths["vscode"]))))
        except (OSError, ValueError, KeyError):
            continue
    if not packs:
        return None
    _, entry, translation = max(packs, key=lambda item: item[0])
    return entry, translation


def language_writes(app: Installation) -> dict[Path, bytes]:
    writes = {}
    for file in (app.argv, app.user_data / "User/locale.json"):
        writes[file] = locale_bytes(file.read_bytes() if file.exists() else None)
    pack = find_language_pack(app)
    if pack is None:
        print("未发现已安装的简体中文语言包，基础菜单可能仍为英文。")
        return writes
    entry, translation = pack
    metadata_path = app.root / "out/nls.metadata.json"
    count = 0
    if metadata_path.is_file():
        metadata = read_json(metadata_path)
        mapping = translation_map(app.app_id)
        existing = translation.setdefault("contents", {})
        # Reuse unambiguous upstream translations for keys introduced by a fork.
        by_text: dict[str, set[str]] = {}
        for module, keys in metadata["keys"].items():
            for index, key in enumerate(keys):
                key = key if isinstance(key, str) else key["key"]
                value = existing.get(module, {}).get(key)
                if value:
                    by_text.setdefault(metadata["messages"][module][index], set()).add(value)
        for text, values in by_text.items():
            if len(values) == 1:
                mapping.setdefault(text, next(iter(values)))
        for module, keys in metadata["keys"].items():
            for index, key in enumerate(keys):
                key = key if isinstance(key, str) else key["key"]
                english = metadata["messages"][module][index]
                if key not in existing.get(module, {}) and english in mapping:
                    existing.setdefault(module, {})[key] = mapping[english]
                    count += 1
    # Keep the official extension unchanged. A private merged copy serves this installation.
    merged_path = app.user_data / "AgentZh/main.i18n.json"
    writes[merged_path] = (json.dumps(translation, ensure_ascii=False) + "\n").encode()
    entry["translations"]["vscode"] = str(merged_path)
    entry["hash"] = hashlib.md5(writes[merged_path]).hexdigest()
    index_path = app.user_data / "languagepacks.json"
    index = read_json(index_path) if index_path.exists() else {}
    index["zh-cn"] = entry
    writes[index_path] = (json.dumps(index, ensure_ascii=False) + "\n").encode()
    print(f"基础语言包已索引，补充 {count} 条专有语言资源。")
    return writes


def runtime_bytes(app: Installation) -> bytes:
    return DICT_SRC.read_bytes()


def apply(app: Installation, *, do_kill: bool = False, do_restart: bool = False,
          install_pack: bool = True, defer_restart: bool = False) -> None:
    if not DICT_SRC.is_file():
        raise AgentZhError("缺少 agent-zh.js；请运行 python scripts/build_js.py。")
    if defer_restart:
        if do_kill or do_restart:
            raise AgentZhError("延后重启不能与关闭或重启选项同时使用。")
        # Only startup resources change. Do not launch the CLI or operate the running UI.
        install_pack = False
    else:
        ensure_stopped(app, do_kill)
    htmls = {str(p.relative_to(app.root)).replace("\\", "/"): inject_html(p.read_bytes()) for p in app.entrypoints}
    product_path = app.root / "product.json"
    writes = {}
    for relative in htmls:
        entry = app.root / relative
        writes[entry.parent / "agent-zh.js"] = runtime_bytes(app)
    for relative, html in htmls.items():
        entry = app.root / relative
        writes[entry] = html
        # Remove only the old project's exact known artifact after migration.
        old_cursor_runtime = entry.parent / "cursor-zh.js"
        if OLD_CURSOR_TAG in entry.read_bytes() and old_cursor_runtime.is_file():
            writes[old_cursor_runtime] = None
    writes[product_path] = product_with_checksums(product_path.read_bytes(), htmls)
    if install_pack:
        install_ms_pack(app)
    writes.update(language_writes(app))
    originals = {p: p.read_bytes() if p.exists() else None for p in writes}
    folder = backup(app, originals)
    commit_writes(writes)
    print(f"已为 {app.name} {app.version} 安装 Agent 汉化，覆盖 {len(htmls)} 个窗口入口。")
    print(f"备份：{folder}")
    if do_restart:
        start_app(app)
    else:
        print(f"请完全退出并重新打开 {app.name}，新打开的窗口才会加载补充汉化。")


def revert(app: Installation, *, do_kill: bool = False, do_restart: bool = False) -> None:
    ensure_stopped(app, do_kill)
    htmls, writes = {}, {}
    for entry in app.entrypoints:
        raw = entry.read_bytes()
        clean = strip_html(raw)
        if raw != clean:
            writes[entry] = clean
            htmls[entry.relative_to(app.root).as_posix()] = clean
            for name, tag in (("agent-zh.js", SCRIPT_TAG), ("cursor-zh.js", OLD_CURSOR_TAG)):
                if tag in raw:
                    writes[entry.parent / name] = None
    if htmls:
        product = app.root / "product.json"
        writes[product] = product_with_checksums(product.read_bytes(), htmls)
    index_path = app.user_data / "languagepacks.json"
    if index_path.is_file():
        index = read_json(index_path)
        current = index.get("zh-cn", {}).get("translations", {}).get("vscode")
        merged = app.user_data / "AgentZh/main.i18n.json"
        if current and Path(current) == merged:
            pack = find_language_pack(app)
            if pack:
                entry, _ = pack
                entry["hash"] = hashlib.md5(json.dumps(entry, sort_keys=True).encode()).hexdigest()
                index["zh-cn"] = entry
            else:
                index.pop("zh-cn", None)
            writes[index_path] = (json.dumps(index, ensure_ascii=False) + "\n").encode()
            writes[merged] = None
    commit_writes(writes)
    print(f"已移除 {app.name} 的补充汉化。保留微软语言包和中文语言设置。")
    if do_restart:
        start_app(app)


def status(app: Installation) -> dict:
    checksums = app.product.get("checksums", {})
    entries = []
    for entry in app.entrypoints:
        relative = entry.relative_to(app.root).as_posix()
        html = entry.read_bytes()
        expected = checksums.get(relative.removeprefix("out/"))
        dictionary = entry.parent / "agent-zh.js"
        installed = SCRIPT_TAG in html and dictionary.is_file()
        entries.append({"path": relative, "installed": installed,
                        "runtimeCurrent": installed and dictionary.read_bytes() == DICT_SRC.read_bytes(),
                        "oldCursorResidue": OLD_CURSOR_TAG in html,
                        "checksum": "untracked" if expected is None else "ok" if expected == vscode_checksum(html) else "mismatch"})
    return {"app": app.app_id, "name": app.name, "version": app.version,
            "path": str(app.root), "entries": entries, "backup": str(backup_root()),
            "installed": all(e["installed"] for e in entries)}


def select_installations(args) -> list[Installation]:
    found = discover(args.app, args.path)
    if not found:
        raise AgentZhError("未找到受支持的软件。可用 --app 指定软件、--path 指定安装目录。")
    if args.all or args.cmd in ("list", "status") or len(found) == 1:
        return found
    if not sys.stdin.isatty():
        raise AgentZhError("找到多个软件，请用 --app 指定，或 --all 明确选择全部。")
    print("Agent 汉化：选择目标软件")
    for i, app in enumerate(found, 1):
        print(f"  {i}. {app.name} {app.version}  ({app.root})")
    answer = input("输入编号（0 退出）：").strip()
    if answer == "0":
        return []
    if not answer.isdigit() or not 1 <= int(answer) <= len(found):
        raise AgentZhError("无效的选择。")
    return [found[int(answer) - 1]]


def main(argv: list[str] | None = None) -> int:
    parser = ChineseArgumentParser(
        description="AgentZh：Devin / Cursor / Windsurf / Visual Studio Code 简体中文补充翻译",
        add_help=False,
    )
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示此帮助信息并退出")
    parser.add_argument("cmd", nargs="?", default="apply", choices=["apply", "revert", "status", "list"], help="操作：apply 应用、revert 移除、status 状态、list 列表")
    parser.add_argument("--app", choices=list(PROFILES), help="选择目标软件")
    parser.add_argument("--path", help="可执行文件、安装目录或 resources/app")
    parser.add_argument("--all", action="store_true", help="明确对全部检测到的软件操作")
    parser.add_argument("--kill", action="store_true", help="允许关闭所选软件（请先保存工作）")
    parser.add_argument("--restart", action="store_true", help="操作完成后重新启动所选软件")
    parser.add_argument("--no-langpack", action="store_true", help="不下载语言包，仍使用已安装的语言包")
    parser.add_argument("--defer-restart", action="store_true", help="只更新启动资源，保留运行中软件；不下载语言包，稍后手动重启")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出检测和状态结果")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if args.all and (args.app or args.path):
        parser.error("--all 不能与 --app 或 --path 同时使用")
    if args.defer_restart and (args.cmd != "apply" or args.kill or args.restart):
        parser.error("--defer-restart 只用于 apply，且不能与 --kill/--restart 同时使用")
    try:
        apps = select_installations(args)
        if args.cmd in ("list", "status"):
            rows = [status(app) for app in apps]
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                print(f"Agent 汉化 {VERSION}")
                for row in rows:
                    print(f"{row['app']:9} {row['name']} {row['version']} | "
                          f"{'已安装' if row['installed'] else '未安装'} | {row['path']}")
                    if args.cmd == "status":
                        for entry in row["entries"]:
                            checksum_label = {"ok": "正常", "mismatch": "不匹配", "untracked": "未跟踪"}[entry["checksum"]]
                            old_cursor = " 有旧 CursorZh 残留" if entry["oldCursorResidue"] else ""
                            print(
                                f"  {entry['path']}: "
                                f"注入={'是' if entry['installed'] else '否'} "
                                f"词典最新={'是' if entry['runtimeCurrent'] else '否'} "
                                f"校验={checksum_label}{old_cursor}"
                            )
            return 0
        for app in apps:
            if args.cmd == "apply":
                apply(app, do_kill=args.kill, do_restart=args.restart, install_pack=not args.no_langpack,
                      defer_restart=args.defer_restart)
            else:
                revert(app, do_kill=args.kill, do_restart=args.restart)
        return 0
    except (AgentZhError, OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
