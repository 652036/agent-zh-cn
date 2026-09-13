import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_zh as agent


class MultiAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.patches = [mock.patch.object(agent, "config_base", return_value=self.base / "config"),
                        mock.patch.object(agent.Path, "home", return_value=self.base / "home"),
                        mock.patch.object(agent, "app_running", return_value=False)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def fixture(self, app_id, layouts=None):
        root = self.base / app_id / "resources/app"
        root.mkdir(parents=True)
        if layouts is None:
            layouts = agent.ENTRYPOINTS[1:3] if app_id == "devin" else agent.ENTRYPOINTS[:1]
        checksums = {}
        for i, rel in enumerate(layouts):
            file = root / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            startup = "sessions" if file.name == "sessions.html" else "workbench"
            text = f'<html>\r\n<body data-version="{i}"></body>\r\n<script type="module" src="./{startup}.js"></script>\r\n</html>'
            file.write_bytes(text.encode())
            checksums[rel[4:]] = agent.vscode_checksum(file.read_bytes())
        (root / "product.json").write_text(json.dumps({"applicationName": agent.PROFILES[app_id]["applicationNames"][0],
            "checksums": checksums, "unrelated": "keep"}, indent=2), encoding="utf-8")
        (root / "package.json").write_text('{"version":"1.2.3"}', encoding="utf-8")
        return agent.Installation(app_id, root)

    def fake_pack(self, app):
        folder = app.extensions / "ms-ceintl.vscode-language-pack-zh-hans-1.2.3-universal"
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"version": "1.2.3", "contributes": {"localizations": [{
            "languageId": "zh-cn", "translations": [{"id": "vscode", "path": "./main.json"}]}]}}), encoding="utf-8")
        (folder / "main.json").write_text('{"contents":{"known":{"key":"已有翻译"}}}', encoding="utf-8")
        (app.root / "out/nls.metadata.json").write_text(json.dumps({"keys": {"known": ["key"], "custom": ["new"]},
            "messages": {"known": ["Known text"], "custom": ["New Space"]}}), encoding="utf-8")
        return folder / "main.json"

    def test_matrix_apply_reapply_revert_preserves_other_files(self):
        for app_id in agent.PROFILES:
            with self.subTest(app=app_id):
                app = self.fixture(app_id)
                originals = {p: p.read_bytes() for p in [*app.entrypoints, app.root / "product.json"]}
                app.argv.parent.mkdir(parents=True, exist_ok=True)
                app.argv.write_text('// keep\n{"disable-hardware-acceleration":true,"nested":{"locale":"en"},}', encoding="utf-8")
                other = app.root / "out/unrelated.js"
                other.write_bytes(b'const filename="New Space";')
                agent.apply(app, install_pack=False)
                self.assertTrue(agent.status(app)["installed"])
                self.assertTrue(all(e["checksum"] == "ok" for e in agent.status(app)["entries"]))
                first = {p: p.read_bytes() for p in originals}
                agent.apply(app, install_pack=False)
                self.assertEqual(first, {p: p.read_bytes() for p in originals})
                self.assertIn('// keep', app.argv.read_text("utf-8"))
                self.assertTrue(agent.parse_jsonc(app.argv.read_text("utf-8"))["disable-hardware-acceleration"])
                self.assertEqual(agent.parse_jsonc(app.argv.read_text("utf-8"))["nested"]["locale"], "en")
                agent.revert(app)
                self.assertEqual(originals, {p: p.read_bytes() for p in originals})
                self.assertEqual(other.read_bytes(), b'const filename="New Space";')
                self.assertEqual(agent.parse_jsonc(app.argv.read_text("utf-8"))["locale"], "zh-cn")

    def test_migrate_cursor_old_tag_without_two_observers(self):
        app = self.fixture("cursor")
        file = app.entrypoints[0]
        original = file.read_bytes()
        file.write_bytes(original.replace(b'</html>', b'\n\t' + agent.LEGACY_TAG + b'</html>'))
        (file.parent / "cursor-zh.js").write_text("legacy", encoding="utf-8")
        agent.apply(app, install_pack=False)
        self.assertNotIn(agent.LEGACY_TAG, file.read_bytes())
        self.assertEqual(file.read_bytes().count(agent.SCRIPT_TAG), 1)
        self.assertFalse((file.parent / "cursor-zh.js").exists())

    def test_language_pack_copy_and_index_restore(self):
        app = self.fixture("devin")
        official = self.fake_pack(app)
        original = official.read_bytes()
        agent.apply(app, install_pack=False)
        index = agent.read_json(app.user_data / "languagepacks.json")
        merged = Path(index["zh-cn"]["translations"]["vscode"])
        self.assertEqual(agent.read_json(merged)["contents"]["custom"]["new"], "新建空间")
        self.assertEqual(official.read_bytes(), original)
        agent.revert(app)
        self.assertTrue(Path(agent.read_json(app.user_data / "languagepacks.json")["zh-cn"]["translations"]["vscode"]).samefile(official))
        self.assertFalse(merged.exists())

    def test_backups_distinguish_missing_empty_and_different_paths(self):
        app = self.fixture("devin")
        first = app.root / "first.js"
        second = app.root / "second.js"
        snapshots = [agent.backup(app, files) for files in (
            {first: None}, {first: b""}, {second: b""},
            {first: b"ab", second: b"c"}, {first: b"a", second: b"bc"})]
        self.assertEqual(len(set(snapshots)), 5)
        self.assertEqual(agent.backup(app, {first: None}), snapshots[0])
        self.assertFalse(agent.read_json(snapshots[0] / "manifest.json")["files"][0]["existed"])
        empty = agent.read_json(snapshots[1] / "manifest.json")["files"][0]
        self.assertTrue(empty["existed"])
        self.assertEqual((snapshots[1] / empty["backup"]).read_bytes(), b"")

    def test_language_index_failure_restores_app_and_user_configuration(self):
        app = self.fixture("devin")
        self.fake_pack(app)
        index = app.user_data / "languagepacks.json"
        locale = app.user_data / "User/locale.json"
        for file, data in ((app.argv, b'{/* keep */"locale":"en","other":true}'),
                           (locale, b'{"locale":"en"}'),
                           (index, b'{"fr":{"label":"Francais"}}')):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        originals = {p: p.read_bytes() for p in [*app.entrypoints, app.root / "product.json", app.argv, locale, index]}
        real_write = agent.atomic_write_bytes

        def fail_index(path, data):
            if path == index:
                raise PermissionError("simulated language index failure")
            real_write(path, data)

        with mock.patch.object(agent, "atomic_write_bytes", side_effect=fail_index):
            with self.assertRaises(PermissionError):
                agent.apply(app, install_pack=False)
        self.assertEqual(originals, {p: p.read_bytes() for p in originals})
        self.assertFalse((app.user_data / "AgentZh/main.i18n.json").exists())
        self.assertTrue(all(not (p.parent / "agent-zh.js").exists() for p in app.entrypoints))

    def test_second_entrypoint_failure_rolls_back_all_writes(self):
        app = self.fixture("devin")
        originals = {p: p.read_bytes() for p in [*app.entrypoints, app.root / "product.json"]}
        real_write = agent.atomic_write_bytes
        failure = app.entrypoints[1]

        def fail_once(path, data):
            if path == failure:
                raise PermissionError("simulated write failure")
            real_write(path, data)

        with mock.patch.object(agent, "atomic_write_bytes", side_effect=fail_once):
            with self.assertRaises(PermissionError):
                agent.apply(app, install_pack=False)
        self.assertEqual(originals, {p: p.read_bytes() for p in originals})
        self.assertFalse((app.entrypoints[0].parent / "agent-zh.js").exists())
        self.assertFalse(app.argv.exists())

    def test_unknown_layout_does_not_write_or_download(self):
        app = self.fixture("devin")
        app.entrypoints[1].write_text('<html><script src="unknown.js"></script></html>', encoding="utf-8")
        original = app.entrypoints[0].read_bytes()
        with mock.patch.object(agent, "install_ms_pack") as download:
            with self.assertRaises(agent.AgentZhError):
                agent.apply(app)
            download.assert_not_called()
        self.assertEqual(app.entrypoints[0].read_bytes(), original)

    def test_wrong_profile_and_unsupported_app_are_rejected(self):
        app = self.fixture("devin")
        with self.assertRaises(agent.AgentZhError):
            agent.discover("cursor", str(app.root))
        product = agent.read_json(app.root / "product.json")
        product["applicationName"] = "some-other-electron-app"
        (app.root / "product.json").write_text(json.dumps(product), encoding="utf-8")
        self.assertIsNone(agent.identify(app.root))

    def test_deferred_apply_does_not_operate_running_app_or_download(self):
        app = self.fixture("devin")
        with mock.patch.object(agent, "app_running", return_value=True), \
             mock.patch.object(agent, "stop_app") as stop, \
             mock.patch.object(agent, "start_app") as start, \
             mock.patch.object(agent, "install_ms_pack") as download:
            agent.apply(app, defer_restart=True)
            stop.assert_not_called()
            start.assert_not_called()
            download.assert_not_called()
        self.assertTrue(agent.status(app)["installed"])
        with self.assertRaises(agent.AgentZhError):
            agent.apply(app, defer_restart=True, do_restart=True)

    def test_revert_keeps_new_app_version_instead_of_old_backup(self):
        app = self.fixture("devin")
        agent.apply(app, install_pack=False)
        file = app.entrypoints[0]
        file.write_bytes(file.read_bytes().replace(b'data-version="0"', b'data-version="new"'))
        agent.revert(app)
        self.assertIn(b'data-version="new"', file.read_bytes())
        self.assertTrue(all(e["checksum"] == "ok" for e in agent.status(app)["entries"]))

    def test_windows_active_version_uses_launcher_not_old_directory(self):
        app = self.fixture("vscode")
        install = self.base / "vscode"
        version_root = install / "aabbccdd11"
        version_root.mkdir()
        shutil.move(str(install / "resources"), str(version_root / "resources"))
        (install / "Code.exe").write_bytes(b"fixture")
        (install / "Code.VisualElementsManifest.xml").write_text('<VisualElements Square150x150Logo="aabbccdd11\\resources\\app\\icon.png" />', encoding="utf-8")
        with mock.patch.object(agent.sys, "platform", "win32"):
            found = agent.discover("vscode", str(install / "Code.exe"))[0]
            self.assertTrue(found.root.samefile(version_root / "resources/app"))
            self.assertTrue(found.install_dir.samefile(install))
            self.assertTrue(found.executable.samefile(install / "Code.exe"))

    def test_portable_data_is_not_written_to_global_profile(self):
        app = self.fixture("cursor")
        (app.install_dir / "data").mkdir()
        with mock.patch.object(agent.sys, "platform", "win32"):
            self.assertEqual(app.user_data, app.install_dir / "data/user-data")
            self.assertEqual(app.argv, app.user_data / "argv.json")
            self.assertEqual(app.extensions, app.install_dir / "data/extensions")

    def test_windows_process_filter_does_not_target_standalone_devin_cli(self):
        app = self.fixture("devin")
        rows = [{"Id": 11, "Path": str(app.install_dir / "Devin.exe")},
                {"Id": 22, "Path": str(self.base / "standalone-cli/devin.exe")}]
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(rows), stderr="")
        with mock.patch.object(agent.sys, "platform", "win32"), mock.patch.object(agent.subprocess, "run", return_value=result):
            self.assertEqual(agent.windows_process_ids(app), [11])

    def test_revert_failure_is_transactional(self):
        app = self.fixture("devin")
        agent.apply(app, install_pack=False)
        product = app.root / "product.json"
        original = {p: p.read_bytes() for p in [*app.entrypoints, product]}
        real_write = agent.atomic_write_bytes

        def fail_product(path, data):
            if path == product:
                raise PermissionError("fixture")
            real_write(path, data)

        with mock.patch.object(agent, "atomic_write_bytes", side_effect=fail_product):
            with self.assertRaises(PermissionError):
                agent.revert(app)
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        self.assertTrue(all((p.parent / "agent-zh.js").is_file() for p in app.entrypoints))


class ConfigTests(unittest.TestCase):
    def test_jsonc_preserves_comments_urls_bom_and_other_values(self):
        raw = '\ufeff{\r\n // "locale":"fake"\r\n "url":"https://example.test/a,}", /* preserve */ "locale":"en", "nested":{"locale":"fr"},\r\n}'.encode()
        result = agent.locale_bytes(raw)
        self.assertEqual(result, raw.replace(b'"locale":"en"', b'"locale":"zh-cn"'))
        self.assertEqual(agent.parse_jsonc(result.decode())["url"], "https://example.test/a,}")

    def test_invalid_jsonc_not_replaced(self):
        for raw in (b'{broken', b'[]', b'{"locale":"en","locale":"fr"}', b'{"locale":{}}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, agent.AgentZhError)):
                agent.locale_bytes(raw)

    def test_empty_jsonc_with_comment(self):
        value = agent.locale_bytes(b'{/* preserve me */}')
        self.assertIn(b'/* preserve me */', value)
        self.assertEqual(agent.parse_jsonc(value.decode())["locale"], "zh-cn")


@unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "Windows PowerShell fixture")
class NativePowerShellTests(unittest.TestCase):
    def test_native_devin_two_windows_apply_and_revert(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            tool = base / "tool"
            (tool / "src").mkdir(parents=True)
            (tool / "locales").mkdir()
            for name in ("AgentZh.ps1", "agent-zh.js"):
                shutil.copy2(agent.HERE / name, tool / name)
            for name in ("zh-CN.json", "common.json", "devin-nls.json"):
                shutil.copy2(agent.HERE / "locales" / name, tool / "locales" / name)
            profiles = json.loads(json.dumps(agent.PROFILES))
            profiles["devin"]["executables"] = ["AgentZhTestFixture.exe"]
            (tool / "src/apps.json").write_text(json.dumps(profiles), encoding="utf-8")
            root = base / "Devin/resources/app"
            root.mkdir(parents=True)
            original = {}
            sums = {}
            for rel in agent.ENTRYPOINTS[1:3]:
                file = root / rel
                file.parent.mkdir(parents=True, exist_ok=True)
                name = "sessions" if "sessions.html" in rel else "workbench"
                data = f'<html>\r\n<script src="./{name}.js" type="module"></script>\r\n</html>'.encode()
                file.write_bytes(data)
                original[file] = data
                sums[rel[4:]] = agent.vscode_checksum(data)
            (root / "product.json").write_text(json.dumps({"applicationName": "devin-desktop", "checksums": sums}), encoding="utf-8")
            (root / "package.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
            original[root / "product.json"] = (root / "product.json").read_bytes()
            env = dict(os.environ, APPDATA=str(base / "config"), USERPROFILE=str(base / "home"))
            pack = base / "home/.devin/extensions/ms-ceintl.vscode-language-pack-zh-hans-1.2.3-universal"
            pack.mkdir(parents=True)
            (pack / "package.json").write_text(json.dumps({"version": "1.2.3", "contributes": {"localizations": [{
                "languageId": "zh-cn", "translations": [{"id": "vscode", "path": "main.json"}]}]}}), encoding="utf-8")
            # Real language packs contain both empty and case-distinct keys.
            original_pack = {"contents": {"": {"": "保留"}, "known": {"Tab": "大写", "tab": "小写"}}}
            (pack / "main.json").write_text(json.dumps(original_pack, ensure_ascii=False), encoding="utf-8")
            (root / "out/nls.metadata.json").write_text(json.dumps({"keys": {"custom": ["new"]},
                "messages": {"custom": ["New Space"]}}), encoding="utf-8")
            common = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tool / "AgentZh.ps1"), "-Native", "-App", "devin", "-Path", str(root), "-NoLangpack"]
            for cmd in ("apply", "apply", "revert"):
                result = subprocess.run([*common, "-Cmd", cmd], env=env, capture_output=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
                if cmd == "apply":
                    merged = json.loads((base / "config/Devin/AgentZh/main.i18n.json").read_text("utf-8"))
                    self.assertEqual(merged["contents"]["custom"]["new"], "新建空间")
                    self.assertEqual(merged["contents"][""], original_pack["contents"][""])
                    self.assertEqual(merged["contents"]["known"], original_pack["contents"]["known"])
            self.assertEqual(original, {p: p.read_bytes() for p in original})


if __name__ == "__main__":
    unittest.main()
