from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douban_cookie.envfile import ConfigError, load_env_file, read_credentials


class EnvFileTests(unittest.TestCase):
    def test_load_env_file_supports_export_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "export DOUBAN_USER='alice@example.com'",
                        'DOUBAN_PASS="secret value"',
                        "IGNORED_LINE",
                    ]
                ),
                encoding="utf-8",
            )

            values = load_env_file(path)

        self.assertEqual(values["DOUBAN_USER"], "alice@example.com")
        self.assertEqual(values["DOUBAN_PASS"], "secret value")
        self.assertNotIn("IGNORED_LINE", values)

    def test_read_credentials_prefers_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("DOUBAN_USER=file-user\nDOUBAN_PASS=file-pass\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"DOUBAN_USER": "env-user", "DOUBAN_PASS": "env-pass"},
                clear=False,
            ):
                credentials = read_credentials(path)

        self.assertEqual(credentials.username, "env-user")
        self.assertEqual(credentials.password, "env-pass")

    def test_read_credentials_requires_user_and_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("DOUBAN_USER=alice@example.com\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigError):
                    read_credentials(path)


if __name__ == "__main__":
    unittest.main()
