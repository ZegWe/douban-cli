from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douban_cookie.movie import movie_detail, movie_search


DETAIL_HTML = """
<html>
<head><title>Movie</title></head>
<body>
<script type="application/ld+json">
{
  "name": "肖申克的救赎 The Shawshank Redemption",
  "url": "/subject/1292052/",
  "image": "https://img.example/poster.jpg",
  "director": [{"name": "弗兰克·德拉邦特 Frank Darabont"}],
  "author": [{"name": "斯蒂芬·金 Stephen King"}],
  "actor": [{"name": "蒂姆·罗宾斯 Tim Robbins"}, {"name": "摩根·弗里曼 Morgan Freeman"}],
  "datePublished": "1994-09-10",
  "genre": ["剧情", "犯罪"],
  "duration": "PT2H22M",
  "description": "short summary",
  "aggregateRating": {"ratingCount": "3296709", "ratingValue": "9.7"}
}
</script>
<div id="info">
<span><span class="pl">导演</span>: <span>弗兰克·德拉邦特</span></span><br/>
<span class="pl">制片国家/地区:</span> 美国<br/>
<span class="pl">语言:</span> 英语<br/>
<span class="pl">IMDb:</span> tt0111161<br/>
</div>
<span property="v:summary">
  full
  summary
</span>
</body>
</html>
"""


SEARCH_HTML = """
<html><head><title>Search</title></head><body>
<script>
window.__DATA__ = {
  "items": [
    {"tpl_name": "search_more"},
    {
      "tpl_name": "search_subject",
      "id": 1292052,
      "title": "肖申克的救赎 The Shawshank Redemption‎ (1994)",
      "url": "https://movie.douban.com/subject/1292052/",
      "abstract": "美国 / 犯罪 / 剧情 / 142分钟",
      "abstract_2": "弗兰克·德拉邦特 / 蒂姆·罗宾斯",
      "cover_url": "https://img.example/poster.jpg",
      "rating": {"count": 3296709, "value": 9.7, "star_count": 5.0}
    }
  ]
};
</script>
</body></html>
"""


class _FakeResponse:
    status = 200
    headers = {"content-type": "text/html; charset=utf-8"}

    def __init__(self, url: str, body: str) -> None:
        self._url = url
        self._body = body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        return self._body


def _write_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "dbcl2",
                        "value": "auth-token",
                        "domain": ".douban.com",
                        "path": "/",
                    },
                    {
                        "name": "ck",
                        "value": "csrf-token",
                        "domain": ".douban.com",
                        "path": "/",
                    },
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )


class MovieTests(unittest.TestCase):
    def test_movie_detail_uses_saved_cookies_and_parses_page(self) -> None:
        seen: dict[str, object] = {}

        def fake_build_opener(processor: object) -> object:
            class FakeOpener:
                def open(self, request: object, timeout: int) -> _FakeResponse:
                    processor.http_request(request)
                    seen["cookie"] = request.get_header("Cookie", "")
                    seen["url"] = request.full_url
                    seen["timeout"] = timeout
                    return _FakeResponse(request.full_url, DETAIL_HTML)

            return FakeOpener()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "douban_cookie.movie.build_opener", fake_build_opener
        ):
            state_path = Path(tmp) / "storage-state.json"
            _write_state(state_path)

            result = movie_detail(subject="1292052", state_path=state_path, timeout_s=2)

        self.assertEqual(result.subject_id, "1292052")
        self.assertEqual(result.title, "肖申克的救赎 The Shawshank Redemption")
        self.assertEqual(result.rating.value, 9.7)
        self.assertEqual(result.rating.count, 3296709)
        self.assertEqual(result.info["IMDb"], "tt0111161")
        self.assertEqual(result.summary, "full summary")
        self.assertIn("dbcl2=auth-token", seen["cookie"])
        self.assertEqual(seen["url"], "https://movie.douban.com/subject/1292052/")
        self.assertEqual(seen["timeout"], 2)

    def test_movie_search_parses_window_data_results(self) -> None:
        def fake_build_opener(processor: object) -> object:
            class FakeOpener:
                def open(self, request: object, timeout: int) -> _FakeResponse:
                    processor.http_request(request)
                    return _FakeResponse(request.full_url, SEARCH_HTML)

            return FakeOpener()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "douban_cookie.movie.build_opener", fake_build_opener
        ):
            state_path = Path(tmp) / "storage-state.json"
            _write_state(state_path)

            results = movie_search(
                query="肖申克",
                state_path=state_path,
                timeout_s=2,
                limit=5,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].subject_id, "1292052")
        self.assertEqual(results[0].rating.value, 9.7)
        self.assertIn("142分钟", results[0].abstract)


if __name__ == "__main__":
    unittest.main()
