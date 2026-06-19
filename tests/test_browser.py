from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douban_cookie.browser import BrowserConfigError, default_headless, find_browser_executable


class BrowserTests(unittest.TestCase):
    def test_default_headless_honors_env_override(self) -> None:
        with patch.dict(os.environ, {"DOUBAN_HEADLESS": "true", "DISPLAY": ":0"}, clear=False):
            self.assertTrue(default_headless())

        with patch.dict(os.environ, {"DOUBAN_HEADLESS": "0"}, clear=False):
            self.assertFalse(default_headless())

    def test_default_headless_uses_display_presence(self) -> None:
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertFalse(default_headless())

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(default_headless())

    def test_find_browser_executable_accepts_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "chrome"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")

            self.assertEqual(find_browser_executable(str(executable)), str(executable))

    def test_find_browser_executable_rejects_missing_path(self) -> None:
        with self.assertRaises(BrowserConfigError):
            find_browser_executable("/missing/google-chrome-stable")

    def test_find_browser_executable_rejects_missing_command(self) -> None:
        with patch("shutil.which", return_value=None):
            with self.assertRaises(BrowserConfigError):
                find_browser_executable("missing-browser-command")


if __name__ == "__main__":
    unittest.main()
