"""Install or remove Cursor-specific Simplified Chinese UI strings."""
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
from pathlib import Path

VERSION = "1.0.0"


class CursorZhError(RuntimeError):
    """A recoverable installer error with a user-facing message."""


HERE = Path(__file__).resolve().parent
DICT_SRC = HERE / "cursor-zh.js"
SCRIPT_TAG = b'<script src="./cursor-zh.js"></script>'
WORKBENCH_TAG = b'<script src="./workbench.js" type="module"></script>'


def backup_root() -> Path:
    if sys.platform == "win32":
        return Path(os.path.expandvars(r"%APPDATA%\CursorZh\backup"))
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/CursorZh/backup"
    return Path.home() / ".config/CursorZh/backup"


def user_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.path.expandvars(r"%APPDATA%\Cursor"))
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Cursor"
    return Path.home() / ".config/Cursor"


def relaunch_as_admin() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    if ctypes.windll.shell32.IsUserAnAdmin():
        return
    exe = sys.executable
    params = subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    raise SystemExit(0)


def vscode_checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def _registry_cursor_dirs() -> list[Path]:
    if sys.platform != "win32":
        return []
    found: list[Path] = []
    try:
        import winreg

        roots = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, path in roots:
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                    name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                    if "cursor" not in name.lower():
                        continue
                    loc = ""
                    for val in ("InstallLocation", "DisplayIcon"):
                        try:
                            loc = str(winreg.QueryValueEx(sub, val)[0])
                            break
                        except OSError:
                            pass
                    if loc:
                        found.append(Path(loc.split(",")[0].strip().strip('"')))
                except OSError:
                    continue
    except Exception:
        pass
    return found


def find_app() -> Path:
    env = os.environ.get("CURSOR_APP") or os.environ.get("CURSOR_PATH")
    candidates: list[Path] = []
    if env:
        p = Path(env)
        if p.name.lower() in {"cursor.exe", "cursor", "cursor.app"}:
            if p.suffix.lower() == ".app":
                candidates.append(p / "Contents" / "Resources" / "app")
            else:
                candidates.append(p.parent / "resources" / "app")
        candidates.append(p if p.name == "app" else p / "resources" / "app")

    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor"
        candidates.extend(
            [
                local / "resources" / "app",
                Path(r"C:\Program Files\Cursor\resources\app"),
                Path(r"C:\Program Files (x86)\Cursor\resources\app"),
            ]
        )
        for loc in _registry_cursor_dirs():
            if loc.is_file():
                candidates.append(loc.parent / "resources" / "app")
            else:
                candidates.append(loc / "resources" / "app")
                candidates.append(loc)
    elif sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Cursor.app/Contents/Resources/app"),
                Path.home() / "Applications/Cursor.app/Contents/Resources/app",
            ]
        )
    else:
        candidates.extend(
            [
                Path("/usr/share/cursor/resources/app"),
                Path("/opt/Cursor/resources/app"),
                Path("/usr/lib/cursor/resources/app"),
                Path.home() / ".local/share/cursor/resources/app",
            ]
        )

    seen: set[str] = set()
    for c in candidates:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if (c / "out/vs/code/electron-sandbox/workbench/workbench.html").is_file():
            return c
    raise CursorZhError(
        "找不到 Cursor 安装目录。请设置环境变量 CURSOR_PATH 指向 Cursor 可执行文件或 resources/app"
    )


def cursor_exe(app: Path) -> Path | None:
    if sys.platform == "darwin":
        # .../Cursor.app/Contents/Resources/app
        bundle = app.parents[2] if len(app.parents) >= 3 else None
        mac = bundle / "MacOS" / "Cursor" if bundle else None
        if mac and mac.is_file():
            return mac
        return None
    names = ("Cursor.exe", "cursor", "Cursor")
    for parent in (app.parents[1], app.parent.parent):
        for name in names:
            p = parent / name
            if p.is_file():
                return p
    return None


def html_path(app: Path) -> Path:
    return app / "out/vs/code/electron-sandbox/workbench/workbench.html"


def product_path(app: Path) -> Path:
    return app / "product.json"


def dict_dst(app: Path) -> Path:
    return app / "out/vs/code/electron-sandbox/workbench/cursor-zh.js"


def writable(p: Path) -> None:
    try:
        p.chmod(p.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace a file without exposing a partially written destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    if path.exists():
        writable(path)
    tmp = path.with_name(f".{path.name}.cursor-zh-{os.getpid()}-{time.time_ns()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
        if old_mode is not None:
            try:
                path.chmod(old_mode)
            except OSError:
                pass
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _process_exists(name: str) -> bool:
    r = subprocess.run(["pgrep", "-x", name], capture_output=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def cursor_running() -> bool:
    if sys.platform == "win32":
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Cursor.exe"], capture_output=True)
        return b"Cursor.exe" in (r.stdout or b"")
    names = ("Cursor",) if sys.platform == "darwin" else ("cursor", "Cursor")
    return any(_process_exists(name) for name in names)


def kill_cursor() -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", "Cursor.exe", "/T"], capture_output=True)
    elif sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'tell application "Cursor" to quit'], capture_output=True)
        time.sleep(0.8)
        subprocess.run(["pkill", "-x", "Cursor"], capture_output=True)
    else:
        for name in ("cursor", "Cursor"):
            subprocess.run(["pkill", "-x", name], capture_output=True)
    for _ in range(40):
        if not cursor_running():
            return
        time.sleep(0.25)
    raise CursorZhError("Cursor 仍在运行，请先手动退出。")


def start_cursor(app: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Cursor"], close_fds=True)
        return
    exe = cursor_exe(app)
    if exe:
        subprocess.Popen([str(exe)], close_fds=True)


def product_with_html_checksum(product: bytes, html: bytes) -> bytes | None:
    bom = b"\xef\xbb\xbf" if product.startswith(b"\xef\xbb\xbf") else b""
    text = product[len(bom) :].decode("utf-8")
    got = vscode_checksum(html)
    pat = re.compile(r'("vs/code/electron-sandbox/workbench/workbench.html":\s*")([^"]+)(")')
    updated, count = pat.subn(rf"\g<1>{got}\g<3>", text, count=1)
    if not count:
        return None
    return bom + updated.encode("utf-8")


def update_html_checksum(app: Path) -> bool:
    prod = product_path(app)
    updated = product_with_html_checksum(prod.read_bytes(), html_path(app).read_bytes())
    if updated is None:
        print("警告: product.json 里没有 workbench.html checksum，已跳过")
        return False
    atomic_write_bytes(prod, updated)
    return True


def inject_html(raw: bytes) -> bytes:
    if SCRIPT_TAG in raw:
        return raw
    if WORKBENCH_TAG not in raw:
        raise CursorZhError("workbench.html 里找不到 workbench.js 标签，Cursor 版本可能变了")
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    return raw.replace(WORKBENCH_TAG, WORKBENCH_TAG + nl + b"\t" + SCRIPT_TAG, 1)


def strip_html(raw: bytes) -> bytes:
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    raw = raw.replace(nl + b"\t" + SCRIPT_TAG, b"")
    raw = raw.replace(nl + SCRIPT_TAG, b"")
    raw = raw.replace(SCRIPT_TAG, b"")
    return raw


def app_version(app: Path) -> str:
    try:
        data = json.loads((app / "package.json").read_text(encoding="utf-8"))
        value = str(data.get("version") or "unknown")
    except (OSError, json.JSONDecodeError):
        value = "unknown"
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value)


def backup_once(app: Path, original_html: bytes, original_product: bytes) -> Path:
    """Store a versioned baseline for manual recovery, never for blind restore."""
    baseline_html = strip_html(original_html)
    baseline_product = product_with_html_checksum(original_product, baseline_html) or original_product
    app_id = hashlib.sha256(str(app.resolve()).casefold().encode("utf-8")).hexdigest()[:12]
    html_id = hashlib.sha256(baseline_html).hexdigest()[:16]
    root = backup_root() / app_id / app_version(app) / html_id
    manifest = root / "manifest.json"
    html_bak = root / "workbench.html.orig"
    prod_bak = root / "product.json.orig"
    if manifest.is_file() and html_bak.is_file() and prod_bak.is_file():
        return root
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(html_bak, baseline_html)
    atomic_write_bytes(prod_bak, baseline_product)
    metadata = {
        "installerVersion": VERSION,
        "cursorVersion": app_version(app),
        "appPath": str(app.resolve()),
        "workbenchSha256": hashlib.sha256(baseline_html).hexdigest(),
        "productSha256": hashlib.sha256(baseline_product).hexdigest(),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    atomic_write_bytes(
        manifest,
        (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return root


def write_locale() -> None:
    user = user_data_dir()
    (user / "User").mkdir(parents=True, exist_ok=True)
    locale = user / "User" / "locale.json"
    payload = '{\n\t"locale": "zh-cn"\n}\n'
    atomic_write_bytes(locale, payload.encode("utf-8"))


def install_ms_pack(app: Path) -> None:
    if sys.platform == "win32":
        cli = app / "bin" / "cursor.cmd"
        cmd = [str(cli)] if cli.is_file() else None
    else:
        cli = app / "bin" / "cursor"
        cmd = [str(cli)] if cli.is_file() else shutil.which("cursor") and ["cursor"]
    if not cmd:
        print("未找到 cursor CLI，跳过官方语言包安装")
        return
    print("正在安装微软简体中文语言包（已装则会跳过/更新）…")
    r = subprocess.run(
        [*cmd, "--install-extension", "MS-CEINTL.vscode-language-pack-zh-hans", "--force"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    msg = (r.stdout or "") + (r.stderr or "")
    print(msg.strip()[:800] or f"exit {r.returncode}")


def status(app: Path) -> str:
    html = html_path(app).read_bytes()
    injected = SCRIPT_TAG in html
    dict_ok = dict_dst(app).is_file()
    product = product_path(app).read_bytes()
    expected_product = product_with_html_checksum(product, html)
    checksum_ok = expected_product is None or expected_product == product
    locale = user_data_dir() / "User" / "locale.json"
    loc = ""
    if locale.exists():
        try:
            loc = json.loads(locale.read_text(encoding="utf-8")).get("locale", "")
        except json.JSONDecodeError:
            loc = "?"
    return "\n".join(
        [
            f"cursor-zh-cn {VERSION}",
            f"Cursor app: {app}",
            f"词典脚本: {'已复制' if dict_ok else '缺失'} ({dict_dst(app)})",
            f"workbench.html 注入: {'已注入' if injected else '未注入'}",
            f"product.json 校验: {'正常' if checksum_ok else '不匹配'}",
            f"locale.json: {loc or '未设置'}",
            f"备份目录: {backup_root()}",
            f"Cursor 进程: {'运行中' if cursor_running() else '未运行'}",
        ]
    )


def apply(app: Path, *, do_kill: bool, do_restart: bool, install_pack: bool) -> None:
    if not DICT_SRC.is_file():
        raise CursorZhError(f"缺少词典文件: {DICT_SRC}")
    if cursor_running():
        if do_kill:
            print("正在关闭 Cursor…")
            kill_cursor()
            time.sleep(0.6)
        else:
            raise CursorZhError("请先退出 Cursor，或加上 --kill")
    dst = dict_dst(app)
    html = html_path(app)
    prod = product_path(app)
    original_html = html.read_bytes()
    original_product = prod.read_bytes()
    original_dict = dst.read_bytes() if dst.exists() else None
    new_html = inject_html(original_html)
    new_product = product_with_html_checksum(original_product, new_html)
    backup = backup_once(app, original_html, original_product)
    print("已备份原文件", backup)
    try:
        atomic_write_bytes(dst, DICT_SRC.read_bytes())
        print("已复制词典", dst)
        atomic_write_bytes(html, new_html)
        print("已注入 workbench.html")
        if new_product is None:
            print("警告: product.json 里没有 workbench.html checksum，已跳过")
        else:
            atomic_write_bytes(prod, new_product)
            print("已更新 product.json checksum")
        write_locale()
        print("已设置 locale = zh-cn")
    except OSError as error:
        rollback_errors = []
        for path, data in (
            (prod, original_product),
            (html, original_html),
            (dst, original_dict),
        ):
            try:
                if data is None:
                    if path.exists():
                        writable(path)
                        path.unlink()
                else:
                    atomic_write_bytes(path, data)
            except OSError as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        if rollback_errors:
            raise CursorZhError(
                f"写入失败且自动回滚不完整: {error}\n" + "\n".join(rollback_errors)
            ) from error
        if isinstance(error, PermissionError):
            relaunch_as_admin()
        raise
    if install_pack:
        install_ms_pack(app)
    print("\n汉化完成。请完全重启 Cursor。")
    print(status(app))
    if do_restart:
        start_cursor(app)
        print("已尝试重新打开 Cursor")


def revert(app: Path, *, do_kill: bool, do_restart: bool) -> None:
    if cursor_running():
        if do_kill:
            print("正在关闭 Cursor…")
            kill_cursor()
            time.sleep(0.6)
        else:
            raise CursorZhError("请先退出 Cursor，或加上 --kill")
    html = html_path(app)
    original_html = html.read_bytes()
    clean_html = strip_html(original_html)
    if clean_html != original_html:
        atomic_write_bytes(html, clean_html)
        print("已移除 workbench.html 注入")
        update_html_checksum(app)
        print("已更新 product.json checksum")
    else:
        print("workbench.html 未发现汉化注入")
    dst = dict_dst(app)
    if dst.exists():
        try:
            dst.unlink()
            print("已删除 cursor-zh.js")
        except OSError as e:
            print("删除词典失败:", e)
    print("\n已取消 Cursor 专用界面汉化（微软语言包和 locale.json 未动）。")
    print(status(app))
    if do_restart:
        start_cursor(app)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cursor 专有界面一键汉化")
    parser.add_argument("cmd", nargs="?", default="apply", choices=["apply", "revert", "status"])
    parser.add_argument("--kill", action="store_true", help="自动结束 Cursor 进程")
    parser.add_argument("--restart", action="store_true", help="完成后重新打开 Cursor")
    parser.add_argument("--no-langpack", action="store_true", help="不安装微软语言包")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    try:
        app = find_app()
        if args.cmd == "status":
            print(status(app))
        elif args.cmd == "apply":
            apply(app, do_kill=args.kill, do_restart=args.restart, install_pack=not args.no_langpack)
        elif args.cmd == "revert":
            revert(app, do_kill=args.kill, do_restart=args.restart)
    except CursorZhError as e:
        parser.exit(1, f"错误: {e}\n")


if __name__ == "__main__":
    # Preserve the published Cursor CLI while the primary entry supports all adapters.
    import agent_zh
    raise SystemExit(agent_zh.main([*sys.argv[1:], "--app", "cursor"]))
