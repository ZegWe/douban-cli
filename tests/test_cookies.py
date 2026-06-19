from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from douban_cookie.cookies import (
    cookie_header,
    cookie_names,
    douban_cookies,
    has_auth_cookie,
    netscape_cookie_file,
    save_cookie_exports,
)


class CookieExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cookies = [
            {
                "name": "dbcl2",
                "value": "auth-value",
                "domain": ".douban.com",
                "path": "/",
                "secure": True,
                "expires": 4_102_444_800,
            },
            {
                "name": "ck",
                "value": "csrf-value",
                "domain": "www.douban.com",
                "path": "/",
                "secure": False,
                "expires": -1,
            },
            {
                "name": "expired",
                "value": "old",
                "domain": ".douban.com",
                "path": "/",
                "secure": False,
                "expires": 1,
            },
            {
                "name": "session",
                "value": "other-site",
                "domain": "example.com",
                "path": "/",
                "secure": False,
                "expires": -1,
            },
        ]

    def test_cookie_helpers_filter_douban_auth_cookies(self) -> None:
        self.assertTrue(has_auth_cookie(self.cookies))
        self.assertEqual(cookie_names(self.cookies), ["ck", "dbcl2", "expired", "session"])
        self.assertEqual(
            [cookie["name"] for cookie in douban_cookies(self.cookies)],
            ["dbcl2", "ck", "expired"],
        )

    def test_cookie_header_skips_non_douban_and_expired_cookies(self) -> None:
        header = cookie_header(self.cookies)

        self.assertIn("dbcl2=auth-value", header)
        self.assertIn("ck=csrf-value", header)
        self.assertNotIn("expired=old", header)
        self.assertNotIn("session=other-site", header)

    def test_netscape_cookie_file_contains_douban_cookies(self) -> None:
        content = netscape_cookie_file(self.cookies)

        self.assertIn("# Netscape HTTP Cookie File", content)
        self.assertIn(".douban.com\tTRUE\t/\tTRUE\t4102444800\tdbcl2\tauth-value", content)
        self.assertIn("www.douban.com\tFALSE\t/\tFALSE\t0\tck\tcsrf-value", content)
        self.assertNotIn("example.com", content)

    def test_save_cookie_exports_writes_private_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "out"
            cookies_path = root / "cookies.json"
            header_path = root / "cookie-header.txt"
            netscape_path = root / "cookies.netscape.txt"

            save_cookie_exports(self.cookies, cookies_path, header_path, netscape_path)

            self.assertTrue(cookies_path.exists())
            self.assertTrue(header_path.exists())
            self.assertTrue(netscape_path.exists())
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(cookies_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
