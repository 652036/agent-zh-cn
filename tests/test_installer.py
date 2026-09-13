import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cursor_zh


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "Cursor" / "resources" / "app"
        self.html = cursor_zh.html_path(self.app)
        self.product = cursor_zh.product_path(self.app)
        self.html.parent.mkdir(parents=True)
        (self.app / "package.json").write_text(
            json.dumps({"version": "3.17.21"}), encoding="utf-8"
        )
        self.original_html = (
            b"<html>\n<body>\n"
            + cursor_zh.WORKBENCH_TAG
            + b"\n</body>\n</html>\n"
        )
        checksum = cursor_zh.vscode_checksum(self.original_html)
        self.original_product = json.dumps(
            {
                "checksums": {
                    "vs/code/electron-sandbox/workbench/workbench.html": checksum
                }
            },
            indent=2,
        ).encode()
        self.html.write_bytes(self.original_html)
        self.product.write_bytes(self.original_product)
        self.user_data = self.root / "user-data"
        self.backups = self.root / "backups"

    def tearDown(self):
        self.temp.cleanup()

    def installer_context(self):
        return mock.patch.multiple(
            cursor_zh,
            backup_root=lambda: self.backups,
            user_data_dir=lambda: self.user_data,
            cursor_running=lambda: False,
        )

    def test_inject_and_strip_round_trip_for_lf_and_crlf(self):
        for newline in (b"\n", b"\r\n"):
            raw = newline.join((b"<html>", cursor_zh.WORKBENCH_TAG, b"</html>"))
            injected = cursor_zh.inject_html(raw)
            self.assertIn(cursor_zh.SCRIPT_TAG, injected)
            self.assertEqual(cursor_zh.inject_html(injected), injected)
            self.assertEqual(cursor_zh.strip_html(injected), raw)

    def test_apply_and_revert_are_surgical(self):
        argv = self.user_data / "argv.json"
        argv.parent.mkdir(parents=True)
        argv_content = '// keep this comment\n{"disable-hardware-acceleration": true}\n'
        argv.write_text(argv_content, encoding="utf-8")

        with self.installer_context():
            cursor_zh.apply(
                self.app, do_kill=False, do_restart=False, install_pack=False
            )
            self.assertIn(cursor_zh.SCRIPT_TAG, self.html.read_bytes())
            self.assertTrue(cursor_zh.dict_dst(self.app).is_file())
            self.assertEqual(argv.read_text(encoding="utf-8"), argv_content)
            self.assertEqual(
                json.loads(
                    (self.user_data / "User" / "locale.json").read_text(
                        encoding="utf-8"
                    )
                )["locale"],
                "zh-cn",
            )
            current_product = self.product.read_bytes()
            self.assertEqual(
                cursor_zh.product_with_html_checksum(
                    current_product, self.html.read_bytes()
                ),
                current_product,
            )

            cursor_zh.revert(self.app, do_kill=False, do_restart=False)
            self.assertEqual(self.html.read_bytes(), self.original_html)
            self.assertEqual(self.product.read_bytes(), self.original_product)
            self.assertFalse(cursor_zh.dict_dst(self.app).exists())

    def test_backups_are_separated_by_cursor_version(self):
        with self.installer_context():
            cursor_zh.apply(
                self.app, do_kill=False, do_restart=False, install_pack=False
            )
            updated_html = (
                b"<html>\n<body data-version='new'>\n"
                + cursor_zh.WORKBENCH_TAG
                + b"\n</body>\n</html>\n"
            )
            (self.app / "package.json").write_text(
                json.dumps({"version": "3.18.0"}), encoding="utf-8"
            )
            self.html.write_bytes(updated_html)
            updated_product = cursor_zh.product_with_html_checksum(
                self.product.read_bytes(), updated_html
            )
            self.assertIsNotNone(updated_product)
            self.product.write_bytes(updated_product)

            cursor_zh.apply(
                self.app, do_kill=False, do_restart=False, install_pack=False
            )

        manifests = list(self.backups.rglob("manifest.json"))
        self.assertEqual(len(manifests), 2)
        versions = {
            json.loads(path.read_text(encoding="utf-8"))["cursorVersion"]
            for path in manifests
        }
        self.assertEqual(versions, {"3.17.21", "3.18.0"})

    def test_unix_process_matching_never_uses_full_command_line(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, stdout=b"")

        with mock.patch.object(cursor_zh.sys, "platform", "linux"), mock.patch.object(
            cursor_zh.subprocess, "run", side_effect=fake_run
        ):
            self.assertFalse(cursor_zh.cursor_running())
            cursor_zh.kill_cursor()

        self.assertTrue(calls)
        self.assertTrue(all("-f" not in command for command in calls))
        self.assertIn(["pkill", "-x", "cursor"], calls)


if __name__ == "__main__":
    unittest.main()
