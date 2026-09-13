import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_js


class BuildTests(unittest.TestCase):
    def test_generated_file_matches_sources(self):
        rendered, _, _, _ = build_js.render()
        self.assertEqual(
            rendered, build_js.OUT.read_text(encoding="utf-8")
        )

    def test_regex_source_is_serialized_as_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locale = root / "locale.json"
            runtime = root / "runtime.js"
            locale.write_text(
                json.dumps(
                    {
                        "phrase": {},
                        "short": {},
                        "patterns": [{"match": "^a/b$", "replace": "a/b"}],
                    }
                ),
                encoding="utf-8",
            )
            runtime.write_text(
                "const a=__PHRASE__;const b=__SHORT__;const c=__PATTERNS__;",
                encoding="utf-8",
            )
            with mock.patch.object(build_js, "LOCALE", locale), mock.patch.object(
                build_js, "RUNTIME", runtime
            ):
                rendered, _, _, _ = build_js.render()
            self.assertIn('new RegExp("^a/b$")', rendered)
            self.assertNotIn("[/^a/b$/", rendered)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            locale = Path(directory) / "locale.json"
            locale.write_text(
                '{"phrase":{"Same":"甲","Same":"乙"},"short":{},"patterns":[]}',
                encoding="utf-8",
            )
            with mock.patch.object(build_js, "LOCALE", locale):
                with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                    build_js.load_locale()


if __name__ == "__main__":
    unittest.main()
