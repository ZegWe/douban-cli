from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douban_cookie.check import CheckError, check_login


class _FakeResponse:
    status = 200
    headers = {"content-type": "text/html; charset=utf-8"}

    def __init__(self, url: str) -> None:
        self._url = url

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        return b"<html><head><title>Mine</title></head><body>ok</body></html>"


def _write_state(path: Path, cookies: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")


class CheckTests(unittest.TestCase):
    def test_missing_state_file_fails_before_http_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-storage-state.json"

            with self.assertRaises(CheckError) as raised:
                check_login(
                    state_path=missing,
                    check_url="https://www.douban.com/mine/",
                    timeout_s=1,
                )

        self.assertIn("Cookie state file does not exist", str(raised.exception))

    def test_check_uses_direct_http_with_storage_state_cookies(self) -> None:
        seen: dict[str, object] = {}

        def fake_build_opener(processor: object) -> object:
            class FakeOpener:
                def open(self, request: object, timeout: int) -> _FakeResponse:
                    processor.http_request(request)
                    seen["cookie"] = request.get_header("Cookie", "")
                    seen["timeout"] = timeout
                    return _FakeResponse(request.full_url)

            return FakeOpener()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "douban_cookie.check.build_opener", fake_build_opener
        ):
            state_path = Path(tmp) / "storage-state.json"
            _write_state(
                state_path,
                [
                    {
                        "name": "dbcl2",
                        "value": "auth-token",
                        "domain": "127.0.0.1",
                        "path": "/",
                    },
                    {
                        "name": "ck",
                        "value": "csrf-token",
                        "domain": "127.0.0.1",
                        "path": "/",
                    },
                ],
            )

            result = check_login(
                state_path=state_path,
                check_url="http://127.0.0.1/mine/",
                timeout_s=2,
            )

        self.assertTrue(result.logged_in)
        self.assertEqual(result.title, "Mine")
        self.assertIn("dbcl2", result.cookie_names)
        self.assertIn("dbcl2=auth-token", seen["cookie"])
        self.assertIn("ck=csrf-token", seen["cookie"])
        self.assertEqual(seen["timeout"], 2)

    def test_missing_auth_cookie_fails_without_http_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "storage-state.json"
            _write_state(
                state_path,
                [{"name": "ck", "value": "csrf-token", "domain": "127.0.0.1", "path": "/"}],
            )

            result = check_login(
                state_path=state_path,
                check_url="http://127.0.0.1:1/mine/",
                timeout_s=1,
            )

        self.assertFalse(result.logged_in)
        self.assertIn("Missing 'dbcl2' auth cookie", result.reason)


if __name__ == "__main__":
    unittest.main()
