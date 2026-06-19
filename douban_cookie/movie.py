from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .browser import DESKTOP_CHROME_UA
from .check import (
    CheckError,
    _cookie_jar,
    _is_login_url,
    _load_storage_cookies,
    _response_title,
    _unexpired_cookies,
)
from .cookies import AUTH_COOKIE_NAME, has_auth_cookie


SUBJECT_URL = "https://movie.douban.com/subject/{subject_id}/"
SEARCH_URL = "https://search.douban.com/movie/subject_search"


class MovieError(RuntimeError):
    pass


@dataclass(frozen=True)
class MovieRating:
    value: float | None
    count: int | None
    info: str = ""
    star_count: float | None = None


@dataclass(frozen=True)
class MovieDetail:
    subject_id: str
    url: str
    title: str
    image: str
    directors: list[str]
    writers: list[str]
    actors: list[str]
    genres: list[str]
    date_published: str
    duration: str
    summary: str
    rating: MovieRating
    info: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MovieSearchResult:
    subject_id: str
    title: str
    url: str
    abstract: str
    abstract_2: str
    cover_url: str
    rating: MovieRating

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _HttpResult:
    url: str
    status: int
    title: str
    body: str


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ld_json: list[str] = []
        self._capturing_ld = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if attr_map.get("type", "").lower() == "application/ld+json":
            self._capturing_ld = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capturing_ld:
            self.ld_json.append("".join(self._parts).strip())
            self._capturing_ld = False
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capturing_ld:
            self._parts.append(data)


class _TextElementParser(HTMLParser):
    def __init__(self, tag_name: str, attr_name: str, attr_value: str) -> None:
        super().__init__()
        self._tag_name = tag_name
        self._attr_name = attr_name
        self._attr_value = attr_value
        self._capturing = False
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if self._capturing:
            if lowered == "br":
                self._parts.append("\n")
            else:
                self._depth += 1
            return

        if lowered != self._tag_name:
            return
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if attr_map.get(self._attr_name) == self._attr_value:
            self._capturing = True
            self._depth = 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._capturing and tag.lower() == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._capturing:
            return
        self._depth -= 1
        if self._depth <= 0:
            self._capturing = False
            self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def _storage_cookies(state_path: Path) -> list[dict[str, Any]]:
    if not state_path.exists():
        raise MovieError(f"Cookie state file does not exist: {state_path}")

    try:
        cookies = _unexpired_cookies(_load_storage_cookies(state_path))
    except CheckError as exc:
        raise MovieError(str(exc)) from exc

    if not has_auth_cookie(cookies):
        raise MovieError(f"Missing {AUTH_COOKIE_NAME!r} auth cookie.")
    return cookies


def _decode_body(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value
            break
    return body.decode(charset, errors="replace")


def _fetch_html(*, cookies: list[dict[str, Any]], url: str, timeout_s: int) -> _HttpResult:
    opener = build_opener(HTTPCookieProcessor(_cookie_jar(cookies)))
    request = Request(
        url,
        headers={
            "User-Agent": DESKTOP_CHROME_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://movie.douban.com/",
        },
    )
    try:
        with opener.open(request, timeout=timeout_s) as response:
            body = response.read(2 * 1024 * 1024)
            content_type = response.headers.get("content-type", "")
            return _HttpResult(
                url=response.geturl(),
                status=response.status,
                title=_response_title(body, content_type),
                body=_decode_body(body, content_type),
            )
    except HTTPError as exc:
        body = exc.read(2 * 1024 * 1024)
        content_type = exc.headers.get("content-type", "")
        return _HttpResult(
            url=exc.geturl(),
            status=exc.code,
            title=_response_title(body, content_type),
            body=_decode_body(body, content_type),
        )
    except URLError as exc:
        raise MovieError(f"Douban movie request failed: {exc.reason}") from exc


def _ensure_success(result: _HttpResult) -> None:
    parsed = urlparse(result.url)
    if _is_login_url(result.url):
        raise MovieError("Douban redirected the movie request to login.")
    if parsed.hostname == "sec.douban.com":
        raise MovieError("Douban redirected the movie request to a security check.")
    if result.status >= 400:
        title = f" ({result.title})" if result.title else ""
        raise MovieError(f"Douban returned HTTP status {result.status}{title}.")


def _subject_id(value: str) -> str:
    raw = value.strip()
    if raw.isdigit():
        return raw
    match = re.search(r"/subject/(\d+)/?", raw)
    if match:
        return match.group(1)
    raise MovieError(f"Could not parse Douban movie subject id from: {value}")


def _first_ld_json(html: str) -> dict[str, Any]:
    parser = _ScriptParser()
    parser.feed(html)
    for raw in parser.ld_json:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _people_names(value: Any) -> list[str]:
    if isinstance(value, dict):
        name = value.get("name")
        return [str(name)] if name else []
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            names.extend(_people_names(item))
        return names
    return []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if value:
        return [str(value)]
    return []


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rating(value: Any) -> MovieRating:
    if not isinstance(value, dict):
        return MovieRating(value=None, count=None)
    return MovieRating(
        value=_float_or_none(value.get("ratingValue") or value.get("value")),
        count=_int_or_none(value.get("ratingCount") or value.get("count")),
        info=str(value.get("rating_info") or ""),
        star_count=_float_or_none(value.get("star_count")),
    )


def _element_text(html: str, tag_name: str, attr_name: str, attr_value: str) -> str:
    parser = _TextElementParser(tag_name, attr_name, attr_value)
    parser.feed(html)
    return parser.text


def _normalized_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _info_fields(html: str) -> dict[str, str]:
    info_text = _element_text(html, "div", "id", "info")
    fields: dict[str, str] = {}
    for line in _normalized_lines(info_text):
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip()] = value.strip()
    return fields


def _summary(html: str, fallback: Any) -> str:
    text = _element_text(html, "span", "property", "v:summary")
    normalized = " ".join(text.split())
    if normalized:
        return normalized
    return str(fallback or "")


def parse_movie_detail_html(subject_id: str, url: str, html: str) -> MovieDetail:
    data = _first_ld_json(html)
    info = _info_fields(html)
    return MovieDetail(
        subject_id=subject_id,
        url=url,
        title=str(data.get("name") or ""),
        image=str(data.get("image") or ""),
        directors=_people_names(data.get("director")),
        writers=_people_names(data.get("author")),
        actors=_people_names(data.get("actor")),
        genres=_string_list(data.get("genre")),
        date_published=str(data.get("datePublished") or ""),
        duration=str(data.get("duration") or ""),
        summary=_summary(html, data.get("description")),
        rating=_rating(data.get("aggregateRating")),
        info=info,
    )


def movie_detail(*, subject: str, state_path: Path, timeout_s: int) -> MovieDetail:
    subject_id = _subject_id(subject)
    cookies = _storage_cookies(state_path)
    result = _fetch_html(
        cookies=cookies,
        url=SUBJECT_URL.format(subject_id=subject_id),
        timeout_s=timeout_s,
    )
    _ensure_success(result)
    detail = parse_movie_detail_html(subject_id, result.url, result.body)
    if not detail.title:
        raise MovieError("Could not parse movie detail from Douban response.")
    return detail


def _search_data(html: str) -> dict[str, Any]:
    match = re.search(r"window\.__DATA__\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
    if not match:
        raise MovieError("Could not find search result data in Douban response.")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise MovieError("Could not parse search result data from Douban response.") from exc
    if not isinstance(data, dict):
        raise MovieError("Douban search result data has an unexpected shape.")
    return data


def parse_movie_search_html(html: str, limit: int) -> list[MovieSearchResult]:
    data = _search_data(html)
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return []

    results: list[MovieSearchResult] = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("tpl_name") != "search_subject":
            continue
        subject_id = str(item.get("id") or "")
        if not subject_id:
            continue
        results.append(
            MovieSearchResult(
                subject_id=subject_id,
                title=str(item.get("title") or ""),
                url=str(item.get("url") or SUBJECT_URL.format(subject_id=subject_id)),
                abstract=str(item.get("abstract") or ""),
                abstract_2=str(item.get("abstract_2") or ""),
                cover_url=str(item.get("cover_url") or ""),
                rating=_rating(item.get("rating")),
            )
        )
        if len(results) >= limit:
            break
    return results


def movie_search(
    *,
    query: str,
    state_path: Path,
    timeout_s: int,
    limit: int,
) -> list[MovieSearchResult]:
    if limit < 1:
        raise MovieError("--limit must be at least 1")
    cookies = _storage_cookies(state_path)
    url = SEARCH_URL + "?" + urlencode({"search_text": query, "cat": "1002"})
    result = _fetch_html(cookies=cookies, url=url, timeout_s=timeout_s)
    _ensure_success(result)
    return parse_movie_search_html(result.body, limit=limit)
