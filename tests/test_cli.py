from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from douban_cookie.cli import main
from douban_cookie.movie import MovieDetail, MovieRating, MovieSearchResult
from douban_cookie.qr_login import QrLoginResult, QrLoginStatus


class CliTests(unittest.TestCase):
    def test_check_missing_state_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-storage-state.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["check", "--state", str(missing)])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Cookie state file does not exist", stderr.getvalue())

    def test_check_rejects_browser_flags(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["check", "--headless"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --headless", stderr.getvalue())

    def test_login_rejects_missing_browser_path_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("DOUBAN_USER=user\nDOUBAN_PASS=pass\n", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                code = main(
                    [
                        "login",
                        "--env",
                        str(env_path),
                        "--browser-executable",
                        "/missing/chrome",
                        "--headless",
                    ]
                )

        self.assertEqual(code, 1)
        self.assertIn("--browser-executable browser executable does not exist", stderr.getvalue())

    def test_login_qr_prints_qr_and_saved_paths(self) -> None:
        def fake_login_with_qr(**kwargs: object) -> QrLoginResult:
            qr_callback = kwargs["qr_callback"]
            status_callback = kwargs["status_callback"]
            qr_callback(Path("out/qr.png"))
            status_callback(QrLoginStatus(login_status="pending", message="success", description="ok"))
            return QrLoginResult(
                state_path=Path("out/storage_state.json"),
                cookies_path=Path("out/cookies.json"),
                header_path=Path("out/cookie-header.txt"),
                netscape_path=Path("out/cookies.netscape.txt"),
                qr_path=Path("out/qr.png"),
                cookie_names=["bid", "dbcl2"],
            )

        stdout = io.StringIO()

        with patch("douban_cookie.cli.login_with_qr", fake_login_with_qr), redirect_stdout(stdout):
            code = main(["login-qr", "--timeout", "5"])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("qr_image: out/qr.png", output)
        self.assertIn("qr_status: pending (ok)", output)
        self.assertIn("storage_state: out/storage_state.json", output)
        self.assertIn("cookie_names:   bid, dbcl2", output)

    def test_movie_detail_prints_json(self) -> None:
        detail = MovieDetail(
            subject_id="1292052",
            url="https://movie.douban.com/subject/1292052/",
            title="肖申克的救赎",
            image="",
            directors=["弗兰克·德拉邦特"],
            writers=[],
            actors=[],
            genres=["剧情"],
            date_published="1994-09-10",
            duration="PT2H22M",
            summary="summary",
            rating=MovieRating(value=9.7, count=3296709),
            info={"IMDb": "tt0111161"},
        )
        stdout = io.StringIO()

        with patch("douban_cookie.cli.movie_detail", return_value=detail), redirect_stdout(stdout):
            code = main(["movie", "detail", "1292052", "--json"])

        self.assertEqual(code, 0)
        self.assertIn('"subject_id": "1292052"', stdout.getvalue())
        self.assertIn('"title": "肖申克的救赎"', stdout.getvalue())

    def test_movie_search_prints_text_results(self) -> None:
        result = MovieSearchResult(
            subject_id="1292052",
            title="肖申克的救赎",
            url="https://movie.douban.com/subject/1292052/",
            abstract="美国 / 犯罪 / 剧情",
            abstract_2="弗兰克·德拉邦特 / 蒂姆·罗宾斯",
            cover_url="",
            rating=MovieRating(value=9.7, count=3296709),
        )
        stdout = io.StringIO()

        with patch("douban_cookie.cli.movie_search", return_value=[result]), redirect_stdout(stdout):
            code = main(["movie", "search", "肖申克", "--limit", "1"])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("1. 1292052 肖申克的救赎 rating=9.7(3296709)", output)
        self.assertIn("https://movie.douban.com/subject/1292052/", output)


if __name__ == "__main__":
    unittest.main()
