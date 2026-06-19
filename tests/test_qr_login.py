from __future__ import annotations

import json
import tempfile
import unittest
from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import patch

from douban_cookie.qr_login import QrLoginStatus, login_with_qr


class _FakeResponse:
    def __init__(self, url: str, status: int, body: bytes) -> None:
        self._url = url
        self.status = status
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def geturl(self) -> str:
        return self._url

    def read(self) -> bytes:
        return self._body


def _cookie(name: str, value: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=".douban.com",
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": True, "SameSite": "Lax"},
        rfc2109=False,
    )


class QrLoginTests(unittest.TestCase):
    def test_login_with_qr_saves_state_and_exports_after_scan(self) -> None:
        statuses: list[QrLoginStatus] = []
        qr_paths: list[Path] = []

        def fake_build_opener(processor: object) -> object:
            jar = processor.cookiejar
            polls = {"count": 0}
            jar.set_cookie(_cookie("bid", "bid-token"))

            class FakeOpener:
                def open(self, request: object, timeout: int) -> _FakeResponse:
                    url = request.full_url
                    if "qrlogin_code" in url:
                        return _FakeResponse(
                            url,
                            200,
                            json.dumps(
                                {
                                    "status": "success",
                                    "message": "success",
                                    "description": "处理成功",
                                    "payload": {
                                        "code": "douban-qrlogin|test",
                                        "img": "https://img.example/qr.png",
                                    },
                                }
                            ).encode("utf-8"),
                        )
                    if url == "https://img.example/qr.png":
                        return _FakeResponse(url, 200, b"png-bytes")
                    if "qrlogin_status" in url:
                        polls["count"] += 1
                        login_status = "login" if polls["count"] > 1 else "pending"
                        if login_status == "login":
                            jar.set_cookie(_cookie("dbcl2", "auth-token"))
                        return _FakeResponse(
                            url,
                            200,
                            json.dumps(
                                {
                                    "status": "success",
                                    "message": "success",
                                    "description": "处理成功",
                                    "payload": {"login_status": login_status},
                                }
                            ).encode("utf-8"),
                        )
                    raise AssertionError(f"unexpected URL: {url}")

            return FakeOpener()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "douban_cookie.qr_login.build_opener", fake_build_opener
        ):
            root = Path(tmp)
            result = login_with_qr(
                state_path=root / "storage_state.json",
                cookies_path=root / "cookies.json",
                header_path=root / "cookie-header.txt",
                netscape_path=root / "cookies.netscape.txt",
                qr_path=root / "qr-login.png",
                timeout_s=5,
                request_timeout_s=1,
                qr_callback=qr_paths.append,
                status_callback=statuses.append,
            )

            state = json.loads(result.state_path.read_text(encoding="utf-8"))
            header = result.header_path.read_text(encoding="utf-8")
            qr_bytes = result.qr_path.read_bytes()

        self.assertEqual(qr_paths, [result.qr_path])
        self.assertEqual(qr_bytes, b"png-bytes")
        self.assertIn("dbcl2", result.cookie_names)
        self.assertIn("dbcl2=auth-token", header)
        self.assertEqual([status.login_status for status in statuses], ["pending", "login"])
        self.assertIn("dbcl2", [cookie["name"] for cookie in state["cookies"]])


if __name__ == "__main__":
    unittest.main()
